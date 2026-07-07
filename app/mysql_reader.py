"""
mysql_reader.py
---------------
مسئول اتصال به MySQL و استخراج اطلاعات ساختاری (schema) و داده‌ها.
شامل:
- خواندن لیست جداول
- خواندن ستون‌ها و انواع داده
- خواندن Primary Key، Unique Constraint، Index، Foreign Key
- خواندن داده‌ها به صورت batch با Server-Side Cursor
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

import pymysql
from pymysql.cursors import SSDictCursor
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import MySQLConfig


# ======================================================================
# ساختارهای داده‌ای برای نمایش Schema
# ======================================================================

@dataclass
class ColumnInfo:
    name: str
    data_type: str          # نوع داده خام MySQL، مثل varchar, int, enum
    column_type: str        # نوع کامل با طول/دقت، مثل varchar(255), decimal(10,2)
    is_nullable: bool
    default: Optional[str]
    extra: str               # مثل auto_increment
    character_maxlen: Optional[int]
    numeric_precision: Optional[int]
    numeric_scale: Optional[int]
    column_comment: str
    enum_values: List[str] = field(default_factory=list)
    ordinal_position: int = 0

    @property
    def is_auto_increment(self) -> bool:
        return "auto_increment" in (self.extra or "").lower()


@dataclass
class IndexInfo:
    name: str
    columns: List[str]
    is_unique: bool
    is_primary: bool


@dataclass
class ForeignKeyInfo:
    name: str
    columns: List[str]
    ref_table: str
    ref_columns: List[str]
    on_update: str
    on_delete: str


@dataclass
class TableSchema:
    name: str
    columns: List[ColumnInfo]
    primary_key_columns: List[str]
    indexes: List[IndexInfo]
    foreign_keys: List[ForeignKeyInfo]
    row_count: int
    table_comment: str = ""


class MySQLReader:
    """
    این کلاس تمام تعاملات خواندنی با MySQL را مدیریت می‌کند.
    از دو نوع اتصال استفاده می‌شود:
    - SQLAlchemy Engine برای query های information_schema (metadata)
    - اتصال خام pymysql با SSDictCursor برای خواندن داده‌های حجیم بدون
      بارگذاری کامل نتیجه در RAM (Server-Side Cursor)
    """

    def __init__(self, config: MySQLConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self._engine: Optional[Engine] = None

    # ------------------------------------------------------------------
    # اتصال
    # ------------------------------------------------------------------

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                self.config.sqlalchemy_url,
                pool_pre_ping=True,
                connect_args={"connect_timeout": self.config.connect_timeout},
            )
        return self._engine

    def test_connection(self) -> None:
        """اتصال به MySQL را تست می‌کند. در صورت شکست، Exception پرتاب می‌شود."""
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        self.logger.info(
            f"اتصال به MySQL برقرار شد: {self.config.host}:{self.config.port}/{self.config.database}"
        )

    def _raw_connection(self) -> pymysql.connections.Connection:
        """یک اتصال خام pymysql برای استفاده با Server-Side Cursor باز می‌کند."""
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset="utf8mb4",
            connect_timeout=self.config.connect_timeout,
            cursorclass=SSDictCursor,
        )

    # ------------------------------------------------------------------
    # خواندن لیست جداول
    # ------------------------------------------------------------------

    def get_table_names(self) -> List[str]:
        """تمام جداول (نه View ها) موجود در دیتابیس را برمی‌گرداند، به ترتیب حروف الفبا."""
        query = text(
            """
            SELECT TABLE_NAME
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """
        )
        with self.engine.connect() as conn:
            result = conn.execute(query, {"db": self.config.database})
            return [row[0] for row in result]

    def get_row_count(self, table_name: str) -> int:
        """تعداد ردیف‌های یک جدول را برمی‌گرداند (با COUNT(*) دقیق، نه تخمینی)."""
        query = text(f"SELECT COUNT(*) FROM `{table_name}`")
        with self.engine.connect() as conn:
            result = conn.execute(query)
            return int(result.scalar() or 0)

    def get_table_comment(self, table_name: str) -> str:
        query = text(
            """
            SELECT TABLE_COMMENT
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :table
            """
        )
        with self.engine.connect() as conn:
            result = conn.execute(query, {"db": self.config.database, "table": table_name})
            row = result.fetchone()
            return row[0] if row and row[0] else ""

    # ------------------------------------------------------------------
    # خواندن ستون‌ها
    # ------------------------------------------------------------------

    def get_columns(self, table_name: str) -> List[ColumnInfo]:
        """اطلاعات کامل تمام ستون‌های یک جدول را برمی‌گرداند."""
        query = text(
            """
            SELECT
                COLUMN_NAME,
                DATA_TYPE,
                COLUMN_TYPE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                EXTRA,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                COLUMN_COMMENT,
                ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :table
            ORDER BY ORDINAL_POSITION
            """
        )
        columns: List[ColumnInfo] = []
        with self.engine.connect() as conn:
            result = conn.execute(query, {"db": self.config.database, "table": table_name})
            for row in result:
                (
                    col_name,
                    data_type,
                    column_type,
                    is_nullable,
                    default,
                    extra,
                    char_maxlen,
                    num_precision,
                    num_scale,
                    col_comment,
                    ordinal_position,
                ) = row

                enum_values: List[str] = []
                if data_type.lower() in ("enum", "set"):
                    enum_values = self._parse_enum_values(column_type)

                columns.append(
                    ColumnInfo(
                        name=col_name,
                        data_type=data_type.lower(),
                        column_type=column_type,
                        is_nullable=(is_nullable.upper() == "YES"),
                        default=default,
                        extra=extra or "",
                        character_maxlen=char_maxlen,
                        numeric_precision=num_precision,
                        numeric_scale=num_scale,
                        column_comment=col_comment or "",
                        enum_values=enum_values,
                        ordinal_position=ordinal_position,
                    )
                )
        return columns

    @staticmethod
    def _parse_enum_values(column_type: str) -> List[str]:
        """
        از رشته column_type مثل: enum('a','b','c') لیست مقادیر را استخراج می‌کند.
        """
        start = column_type.find("(")
        end = column_type.rfind(")")
        if start == -1 or end == -1:
            return []
        inner = column_type[start + 1 : end]
        values = []
        current = ""
        in_quotes = False
        i = 0
        while i < len(inner):
            char = inner[i]
            if char == "'" and (i == 0 or inner[i - 1] != "\\"):
                if in_quotes and i + 1 < len(inner) and inner[i + 1] == "'":
                    # کوتیشن دوبل شده به معنای escape یک کوتیشن داخل مقدار
                    current += "'"
                    i += 2
                    continue
                in_quotes = not in_quotes
                if not in_quotes:
                    values.append(current)
                    current = ""
                i += 1
                continue
            if in_quotes:
                current += char
            i += 1
        return values

    # ------------------------------------------------------------------
    # خواندن Primary Key
    # ------------------------------------------------------------------

    def get_primary_key_columns(self, table_name: str) -> List[str]:
        query = text(
            """
            SELECT COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = :db
              AND TABLE_NAME = :table
              AND CONSTRAINT_NAME = 'PRIMARY'
            ORDER BY ORDINAL_POSITION
            """
        )
        with self.engine.connect() as conn:
            result = conn.execute(query, {"db": self.config.database, "table": table_name})
            return [row[0] for row in result]

    # ------------------------------------------------------------------
    # خواندن Index ها (شامل Unique Constraint ها)
    # ------------------------------------------------------------------

    def get_indexes(self, table_name: str) -> List[IndexInfo]:
        """
        تمام index های جدول (غیر از PRIMARY) را برمی‌گرداند.
        از SHOW INDEX استفاده می‌شود چون اطلاعات ترتیب ستون‌ها را دقیق‌تر می‌دهد.
        """
        query = text(f"SHOW INDEX FROM `{table_name}`")
        indexes_map: Dict[str, IndexInfo] = {}
        # از این dict برای نگه‌داشتن ترتیب ستون‌ها بر اساس Seq_in_index استفاده می‌شود
        columns_by_index: Dict[str, List[tuple]] = {}

        with self.engine.connect() as conn:
            result = conn.execute(query)
            rows = result.mappings().all()

        for row in rows:
            key_name = row["Key_name"]
            if key_name == "PRIMARY":
                continue
            seq = row["Seq_in_index"]
            col_name = row["Column_name"]
            non_unique = row["Non_unique"]

            if key_name not in indexes_map:
                indexes_map[key_name] = IndexInfo(
                    name=key_name,
                    columns=[],
                    is_unique=(int(non_unique) == 0),
                    is_primary=False,
                )
                columns_by_index[key_name] = []

            columns_by_index[key_name].append((seq, col_name))

        for key_name, cols in columns_by_index.items():
            cols_sorted = [c[1] for c in sorted(cols, key=lambda x: x[0])]
            indexes_map[key_name].columns = cols_sorted

        return list(indexes_map.values())

    # ------------------------------------------------------------------
    # خواندن Foreign Key ها
    # ------------------------------------------------------------------

    def get_foreign_keys(self, table_name: str) -> List[ForeignKeyInfo]:
        query = text(
            """
            SELECT
                kcu.CONSTRAINT_NAME,
                kcu.COLUMN_NAME,
                kcu.REFERENCED_TABLE_NAME,
                kcu.REFERENCED_COLUMN_NAME,
                kcu.ORDINAL_POSITION,
                rc.UPDATE_RULE,
                rc.DELETE_RULE
            FROM information_schema.KEY_COLUMN_USAGE kcu
            JOIN information_schema.REFERENTIAL_CONSTRAINTS rc
                ON kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
                AND kcu.TABLE_SCHEMA = rc.CONSTRAINT_SCHEMA
            WHERE kcu.TABLE_SCHEMA = :db
              AND kcu.TABLE_NAME = :table
              AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
            """
        )

        fks_map: Dict[str, ForeignKeyInfo] = {}
        with self.engine.connect() as conn:
            result = conn.execute(query, {"db": self.config.database, "table": table_name})
            rows = result.mappings().all()

        for row in rows:
            constraint_name = row["CONSTRAINT_NAME"]
            if constraint_name not in fks_map:
                fks_map[constraint_name] = ForeignKeyInfo(
                    name=constraint_name,
                    columns=[],
                    ref_table=row["REFERENCED_TABLE_NAME"],
                    ref_columns=[],
                    on_update=row["UPDATE_RULE"] or "RESTRICT",
                    on_delete=row["DELETE_RULE"] or "RESTRICT",
                )
            fks_map[constraint_name].columns.append(row["COLUMN_NAME"])
            fks_map[constraint_name].ref_columns.append(row["REFERENCED_COLUMN_NAME"])

        return list(fks_map.values())

    # ------------------------------------------------------------------
    # ترکیب همه اطلاعات یک جدول در یک TableSchema
    # ------------------------------------------------------------------

    def get_full_table_schema(self, table_name: str) -> TableSchema:
        """تمام اطلاعات ساختاری یک جدول را جمع‌آوری و در یک TableSchema برمی‌گرداند."""
        columns = self.get_columns(table_name)
        primary_key_columns = self.get_primary_key_columns(table_name)
        indexes = self.get_indexes(table_name)
        foreign_keys = self.get_foreign_keys(table_name)
        row_count = self.get_row_count(table_name)
        table_comment = self.get_table_comment(table_name)

        return TableSchema(
            name=table_name,
            columns=columns,
            primary_key_columns=primary_key_columns,
            indexes=indexes,
            foreign_keys=foreign_keys,
            row_count=row_count,
            table_comment=table_comment,
        )

    # ------------------------------------------------------------------
    # خواندن داده‌ها به صورت Batch با Server-Side Cursor
    # ------------------------------------------------------------------

    def iter_rows_batched(
        self, table_name: str, batch_size: int, start_offset: int = 0
    ) -> Generator[List[Dict[str, Any]], None, None]:
        """
        داده‌های یک جدول را به صورت batch-به-batch با استفاده از
        Server-Side Cursor (SSDictCursor) می‌خواند تا کل نتیجه در RAM
        بارگذاری نشود. مناسب برای جداول بسیار بزرگ.

        از start_offset برای پشتیبانی از Resume استفاده می‌شود: به جای
        دوباره خواندن از ابتدا، مستقیماً از offset مشخص‌شده ادامه می‌دهد.

        نکته: از ORDER BY روی Primary Key (یا اگر وجود نداشت، بدون ORDER BY)
        استفاده می‌شود تا ترتیب رکوردها بین اجراهای مختلف پایدار بماند و
        Resume به درستی کار کند.
        """
        pk_columns = self.get_primary_key_columns(table_name)
        order_clause = ""
        if pk_columns:
            cols_quoted = ", ".join(f"`{c}`" for c in pk_columns)
            order_clause = f"ORDER BY {cols_quoted}"

        conn = self._raw_connection()
        try:
            with conn.cursor() as cursor:
                query = (
                    f"SELECT * FROM `{table_name}` {order_clause} "
                    f"LIMIT %s OFFSET %s"
                )
                offset = start_offset
                while True:
                    cursor.execute(query, (batch_size, offset))
                    rows = cursor.fetchall()
                    if not rows:
                        break
                    yield rows
                    offset += len(rows)
                    if len(rows) < batch_size:
                        break
        finally:
            conn.close()

    def close(self) -> None:
        """اتصال Engine را می‌بندد."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
