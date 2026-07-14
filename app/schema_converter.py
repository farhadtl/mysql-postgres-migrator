"""
schema_converter.py
--------------------
مسئول تبدیل Schema از MySQL به PostgreSQL:
- تبدیل انواع داده (Data Type Mapping)
- تولید عبارت CHECK برای ENUM (طبق تصمیم پروژه: varchar + CHECK)
- تولید DDL کامل CREATE TABLE
- تولید DDL برای Primary Key
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from mysql_reader import ColumnInfo, TableSchema
from utils import safe_identifier


class SchemaConverter:
    """
    این کلاس منطق تبدیل نوع داده و تولید DDL برای PostgreSQL را نگه می‌دارد.
    هیچ اتصال دیتابیسی در این کلاس نیست؛ فقط رشته‌های SQL تولید می‌کند تا
    توسط PostgresWriter اجرا شوند. این جداسازی، تست‌پذیری را بالا می‌برد.
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    # ------------------------------------------------------------------
    # تبدیل نوع داده هر ستون
    # ------------------------------------------------------------------

    def convert_column_type(self, column: ColumnInfo, table_name: str) -> str:
        """
        نوع داده MySQL ستون را به معادل PostgreSQL آن تبدیل می‌کند.
        طبق تصمیم پروژه:
          - tinyint(1) -> boolean
          - tinyint دیگر -> smallint
          - enum -> varchar (با طول مناسب؛ CHECK به صورت جداگانه اضافه می‌شود)
        """
        dt = column.data_type.lower()

        # ---------------- اعداد صحیح ----------------
        if dt == "tinyint":
            # tinyint(1) در MySQL قرارداداً معادل boolean است
            if column.column_type.lower().startswith("tinyint(1)"):
                return "boolean"
            return "smallint"

        if dt == "smallint":
            return "smallint"

        if dt == "mediumint":
            # mediumint معادل مستقیمی در PostgreSQL ندارد؛ نزدیک‌ترین integer است
            return "integer"

        if dt == "int" or dt == "integer":
            return "integer"

        if dt == "bigint":
            return "bigint"

        # ---------------- اعداد اعشاری ----------------
        if dt == "decimal" or dt == "numeric":
            precision = column.numeric_precision
            scale = column.numeric_scale
            if precision is not None and scale is not None:
                return f"numeric({precision},{scale})"
            if precision is not None:
                return f"numeric({precision})"
            return "numeric"

        if dt == "double":
            return "double precision"

        if dt == "float":
            return "real"

        # ---------------- رشته‌ای ----------------
        if dt == "char":
            length = column.character_maxlen or 1
            return f"char({length})"

        if dt == "varchar":
            length = column.character_maxlen or 255
            return f"varchar({length})"

        if dt == "text":
            return "text"

        if dt == "tinytext":
            return "text"

        if dt == "mediumtext":
            return "text"

        if dt == "longtext":
            return "text"

        # ---------------- تاریخ/زمان ----------------
        if dt == "datetime":
            return "timestamp"

        if dt == "timestamp":
            return "timestamptz"

        if dt == "date":
            return "date"

        if dt == "time":
            return "time"

        if dt == "year":
            return "smallint"

        # ---------------- JSON ----------------
        if dt == "json":
            return "jsonb"

        # ---------------- ENUM / SET ----------------
        if dt == "enum":
            max_len = max((len(v) for v in column.enum_values), default=1)
            # کمی فضای اضافه برای مقادیر آینده در نظر گرفته می‌شود
            length = max(max_len, 1) + 20
            return f"varchar({length})"

        if dt == "set":
            # SET در MySQL می‌تواند چند مقدار همزمان داشته باشد؛
            # به عنوان متن ساده (comma separated) منتقل می‌شود
            return "text"

        # ---------------- باینری ----------------
        if dt == "blob":
            return "bytea"

        if dt == "tinyblob":
            return "bytea"

        if dt == "mediumblob":
            return "bytea"

        if dt == "longblob":
            return "bytea"

        if dt == "binary":
            return "bytea"

        if dt == "varbinary":
            return "bytea"

        # ---------------- بولین ----------------
        if dt == "bool" or dt == "boolean":
            return "boolean"

        # ---------------- مکانی (Spatial) ----------------
        if dt in ("geometry", "point", "linestring", "polygon", "multipoint",
                   "multilinestring", "multipolygon", "geometrycollection"):
            self.logger.warning(
                f"جدول '{table_name}', ستون '{column.name}': نوع مکانی '{dt}' "
                f"به صورت مستقیم پشتیبانی نمی‌شود (نیازمند PostGIS)؛ به عنوان "
                f"text منتقل می‌شود."
            )
            return "text"

        # ---------------- بیت ----------------
        if dt == "bit":
            if column.character_maxlen == 1:
                return "boolean"
            return "bytea"

        # ---------------- fallback ----------------
        self.logger.warning(
            f"جدول '{table_name}', ستون '{column.name}': نوع داده ناشناخته "
            f"'{dt}' یافت شد؛ به عنوان text منتقل می‌شود."
        )
        return "text"

    # ------------------------------------------------------------------
    # تبدیل مقدار DEFAULT
    # ------------------------------------------------------------------

    def convert_default_value(self, column: ColumnInfo, pg_type: str) -> Optional[str]:
        """
        مقدار DEFAULT ستون MySQL را به معادل قابل‌فهم برای PostgreSQL تبدیل می‌کند.
        اگر قابل تبدیل امن نباشد، None برمی‌گرداند (یعنی DEFAULT اعمال نمی‌شود).
        """
        default = column.default

        if default is None:
            return None
        
        # MySQL zero date is invalid in PostgreSQL
        if str(default).strip("'") in (
            "0000-00-00",
            "0000-00-00 00:00:00",
            "0000-00-00 00:00:00.000000",
        ):
            return None

        default_upper = str(default).strip().upper()

        # مقادیر خاص زمان
        if default_upper in ("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()"):
            return "CURRENT_TIMESTAMP"

        if default_upper == "NULL":
            return None

        # boolean
        if pg_type == "boolean":
            if str(default) in ("1", "b'1'"):
                return "true"
            if str(default) in ("0", "b'0'"):
                return "false"
            return None

        # عددی: بدون کوتیشن
        numeric_types = (
            "smallint", "integer", "bigint", "real", "double precision",
        )
        if pg_type == numeric_types or pg_type in numeric_types or pg_type.startswith("numeric"):
            # اطمینان از این‌که واقعاً یک عدد است
            if re.match(r"^-?\d+(\.\d+)?$", str(default)):
                return str(default)
            return None

        # JSON/JSONB: باید به صورت رشته کوتیشن‌دار با تبدیل ضمنی باشد
        if pg_type == "jsonb":
            escaped = str(default).replace("'", "''")
            return f"'{escaped}'::jsonb"

        # پیش‌فرض: رشته‌ای - در کوتیشن تک قرار می‌گیرد
        escaped = str(default).replace("'", "''")
        return f"'{escaped}'"

    # ------------------------------------------------------------------
    # تولید CHECK Constraint برای ENUM
    # ------------------------------------------------------------------

    def build_enum_check_constraint(
        self, table_name: str, column: ColumnInfo
    ) -> Optional[str]:
        """
        برای ستون‌های ENUM، یک عبارت CHECK تولید می‌کند که مقدار ستون را
        محدود به لیست مقادیر مجاز enum اصلی می‌کند.
        طبق تصمیم پروژه: به جای PostgreSQL native ENUM type از
        varchar + CHECK constraint استفاده می‌شود (برای سهولت تغییر مقادیر
        در آینده بدون نیاز به ALTER TYPE).
        """
        if column.data_type.lower() != "enum" or not column.enum_values:
            return None

        col_ident = safe_identifier(column.name)
        values_list = ", ".join(
            "'" + v.replace("'", "''") + "'" for v in column.enum_values
        )
        constraint_name = self._truncate_identifier(
            f"chk_{table_name}_{column.name}_enum"
        )
        return (
            f"ALTER TABLE {safe_identifier(table_name)} "
            f"ADD CONSTRAINT {safe_identifier(constraint_name)} "
            f"CHECK ({col_ident} IN ({values_list}))"
        )

    @staticmethod
    def _truncate_identifier(name: str) -> str:
        """
        PostgreSQL شناسه‌ها را حداکثر تا 63 بایت پشتیبانی می‌کند.
        اگر نام طولانی‌تر باشد، کوتاه می‌شود.
        """
        max_len = 63
        if len(name) <= max_len:
            return name
        return name[:max_len]

    # ------------------------------------------------------------------
    # تولید کامل DDL برای یک جدول (بدون FK - آن‌ها جداگانه اضافه می‌شوند)
    # ------------------------------------------------------------------

    def build_create_table_ddl(self, table_schema: TableSchema) -> Tuple[str, List[str]]:
        """
        DDL کامل CREATE TABLE را برای یک جدول تولید می‌کند.
        خروجی یک tuple است: (دستور CREATE TABLE, لیست دستورات CHECK جداگانه برای ENUM)

        Primary Key در همان CREATE TABLE گنجانده می‌شود.
        Index، Unique Constraint و Foreign Key در این متد قرار نمی‌گیرند
        (این‌ها توسط index_converter.py و fk_converter.py پس از انتقال
        داده‌ها اعمال می‌شوند تا سرعت INSERT بالاتر برود).
        """
        column_defs: List[str] = []
        enum_checks: List[str] = []

        for column in table_schema.columns:
            pg_type = self.convert_column_type(column, table_schema.name)
            col_ident = safe_identifier(column.name)

            parts = [col_ident, pg_type]

            if not column.is_nullable:
                parts.append("NOT NULL")

            default_value = self.convert_default_value(column, pg_type)
            if default_value is not None:
                parts.append(f"DEFAULT {default_value}")

            column_defs.append(" ".join(parts))

            # آماده‌سازی CHECK برای enum (اما اعمال آن بعد از CREATE TABLE انجام می‌شود
            # چون در PostgreSQL می‌توان CHECK را هم داخل CREATE TABLE و هم جدا اضافه کرد؛
            # اینجا جدا نگه می‌داریم تا با فرآیند index/fk هماهنگ بماند)
            check_ddl = self.build_enum_check_constraint(table_schema.name, column)
            if check_ddl:
                enum_checks.append(check_ddl)

        # Primary Key
        if table_schema.primary_key_columns:
            pk_cols = ", ".join(
                safe_identifier(c) for c in table_schema.primary_key_columns
            )
            pk_name = self._truncate_identifier(f"pk_{table_schema.name}")
            column_defs.append(
                f"CONSTRAINT {safe_identifier(pk_name)} PRIMARY KEY ({pk_cols})"
            )

        table_ident = safe_identifier(table_schema.name)
        columns_sql = ",\n    ".join(column_defs)
        create_stmt = f"CREATE TABLE {table_ident} (\n    {columns_sql}\n)"

        return create_stmt, enum_checks

    # ------------------------------------------------------------------
    # تبدیل مقادیر سلولی هر ردیف پیش از درج (Row-level value coercion)
    # ------------------------------------------------------------------

    def build_column_type_map(self, table_schema: "TableSchema") -> Dict[str, str]:
        """
        نگاشت column_name -> pg_type را برای کل جدول می‌سازد. این نگاشت توسط
        coerce_row_values استفاده می‌شود تا مشخص شود هر ستون باید چگونه از
        مقدار خام MySQL به مقدار سازگار با psycopg2/PostgreSQL تبدیل شود.
        """
        return {
            column.name: self.convert_column_type(column, table_schema.name)
            for column in table_schema.columns
        }

    def coerce_row_values(
        self, rows: List[Dict[str, Any]], column_type_map: Dict[str, str]
        ) -> List[Dict[str, Any]]:

        boolean_columns = [
            col for col, pg_type in column_type_map.items()
            if pg_type == "boolean"
        ]

        zero_dates = {
            "0000-00-00",
            "0000-00-00 00:00:00",
            "0000-00-00 00:00:00.000000",
        }

        for row in rows:

            for col, value in row.items():

                # MySQL zero date -> PostgreSQL NULL
                if isinstance(value, str) and value in zero_dates:
                    row[col] = "1970-01-01 00:00:00"
                    continue

                # MySQL boolean conversion
                if col in boolean_columns:

                    if value is None:
                        continue

                    if isinstance(value, bool):
                        continue

                    if isinstance(value, (bytes, bytearray)):
                        row[col] = value != b"\x00"

                    else:
                        row[col] = bool(int(value))

        return rows

    def build_drop_table_ddl(self, table_name: str) -> str:
        """DDL برای حذف جدول (در صورت فعال بودن DROP_EXISTING_TABLES)."""
        return f"DROP TABLE IF EXISTS {safe_identifier(table_name)} CASCADE"

    def build_table_comment_ddl(self, table_schema: TableSchema) -> Optional[str]:
        """DDL برای انتقال کامنت جدول، در صورت وجود."""
        if not table_schema.table_comment:
            return None
        escaped = table_schema.table_comment.replace("'", "''")
        return f"COMMENT ON TABLE {safe_identifier(table_schema.name)} IS '{escaped}'"

    def build_column_comment_ddls(self, table_schema: TableSchema) -> List[str]:
        """DDL هایی برای انتقال کامنت هر ستون، در صورت وجود."""
        ddls = []
        for column in table_schema.columns:
            if column.column_comment:
                escaped = column.column_comment.replace("'", "''")
                ddls.append(
                    f"COMMENT ON COLUMN {safe_identifier(table_schema.name)}."
                    f"{safe_identifier(column.name)} IS '{escaped}'"
                )
        return ddls
