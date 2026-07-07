"""
postgres_writer.py
-------------------
مسئول تمام تعاملات نوشتنی با PostgreSQL:
- اتصال و تست اتصال
- اجرای DDL (CREATE TABLE، CHECK، Index، FK)
- درج داده به صورت Batch (bulk insert)
- کمک به تشخیص وجود جدول برای پشتیبانی از Resume
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import PostgresConfig
from utils import safe_identifier


class PostgresWriter:
    """
    این کلاس تمام عملیات نوشتن روی PostgreSQL را انجام می‌دهد.
    از psycopg2 خام برای bulk insert با execute_values استفاده می‌شود
    (سریع‌تر از insert تک‌به‌تک با SQLAlchemy ORM)، و از SQLAlchemy Engine
    برای DDL و query های متادیتا استفاده می‌شود.
    """

    def __init__(self, config: PostgresConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self._engine: Optional[Engine] = None
        self._raw_conn: Optional[psycopg2.extensions.connection] = None

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
        """اتصال به PostgreSQL را تست می‌کند. در صورت شکست، Exception پرتاب می‌شود."""
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        self.logger.info(
            f"اتصال به PostgreSQL برقرار شد: "
            f"{self.config.host}:{self.config.port}/{self.config.database}"
        )

    def _get_raw_connection(self) -> psycopg2.extensions.connection:
        """
        یک اتصال خام psycopg2 برای bulk insert سریع باز می‌کند/برمی‌گرداند.
        این اتصال به صورت مجزا از SQLAlchemy Engine نگه‌داری می‌شود تا
        بتوان autocommit و transaction را دقیق‌تر کنترل کرد.
        """
        if self._raw_conn is None or self._raw_conn.closed:
            self._raw_conn = psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.database,
                user=self.config.user,
                password=self.config.password,
                connect_timeout=self.config.connect_timeout,
            )
        return self._raw_conn

    def close(self) -> None:
        """تمام اتصالات باز را می‌بندد."""
        if self._raw_conn is not None and not self._raw_conn.closed:
            self._raw_conn.close()
            self._raw_conn = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    # ------------------------------------------------------------------
    # اجرای DDL
    # ------------------------------------------------------------------

    def execute_ddl(self, ddl: str) -> None:
        """یک دستور DDL را در یک تراکنش مستقل اجرا می‌کند."""
        with self.engine.begin() as conn:
            conn.execute(text(ddl))

    def execute_ddl_many(self, ddls: List[str], continue_on_error: bool = False) -> List[str]:
        """
        چندین دستور DDL را اجرا می‌کند.
        اگر continue_on_error=True باشد، خطای هر دستور جمع‌آوری شده و اجرای
        بقیه ادامه می‌یابد (مفید برای Index/FK که شکست یکی نباید بقیه را متوقف کند).
        خروجی: لیست پیام خطاهایی که رخ داده (خالی یعنی بدون خطا).
        """
        errors: List[str] = []
        for ddl in ddls:
            try:
                self.execute_ddl(ddl)
            except Exception as exc:  # noqa: BLE001
                msg = f"خطا در اجرای DDL: {ddl[:200]}... -> {exc}"
                self.logger.error(msg)
                errors.append(msg)
                if not continue_on_error:
                    raise
        return errors

    # ------------------------------------------------------------------
    # بررسی وجود جدول (برای Resume و DROP_EXISTING_TABLES)
    # ------------------------------------------------------------------

    def table_exists(self, table_name: str) -> bool:
        query = text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :table
            )
            """
        )
        with self.engine.connect() as conn:
            result = conn.execute(query, {"table": table_name})
            return bool(result.scalar())

    def get_row_count(self, table_name: str) -> int:
        """تعداد ردیف‌های فعلی جدول مقصد را برمی‌گرداند (برای اعتبارسنجی و resume)."""
        query = text(f"SELECT COUNT(*) FROM {safe_identifier(table_name)}")
        with self.engine.connect() as conn:
            result = conn.execute(query)
            return int(result.scalar() or 0)

    def truncate_table(self, table_name: str) -> None:
        """داده‌های جدول را خالی می‌کند (بدون حذف ساختار)."""
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {safe_identifier(table_name)} CASCADE"))

    # ------------------------------------------------------------------
    # درج داده به صورت Batch
    # ------------------------------------------------------------------

    def insert_batch(
        self,
        table_name: str,
        column_names: Sequence[str],
        rows: List[Dict[str, Any]],
    ) -> int:
        """
        یک batch از رکوردها را با psycopg2.extras.execute_values درج می‌کند
        که بسیار سریع‌تر از INSERT های تکی است.
        خروجی: تعداد ردیف‌های درج‌شده.

        از "ON CONFLICT DO NOTHING" استفاده می‌شود تا عملیات درج idempotent
        باشد: اگر برنامه دقیقاً بعد از موفقیت یک INSERT ولی قبل از ذخیره
        state متوقف شود (crash)، اجرای مجدد همان batch در Resume باعث خطای
        duplicate key نمی‌شود و بدون توقف غیرضروری، به سادگی از رکوردهای
        تکراری صرف‌نظر می‌کند. این کار به‌خصوص برای جداولی با Primary Key
        حیاتی است.
        """
        if not rows:
            return 0

        conn = self._get_raw_connection()
        columns_sql = ", ".join(safe_identifier(c) for c in column_names)
        table_ident = safe_identifier(table_name)

        insert_sql = (
            f"INSERT INTO {table_ident} ({columns_sql}) VALUES %s "
            f"ON CONFLICT DO NOTHING"
        )

        values = [
            tuple(row.get(col) for col in column_names) for row in rows
        ]

        try:
            with conn.cursor() as cursor:
                psycopg2.extras.execute_values(
                    cursor, insert_sql, values, page_size=len(values)
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return len(rows)

    # ------------------------------------------------------------------
    # مدیریت Sequence برای Auto Increment
    # ------------------------------------------------------------------

    def set_sequence_value(self, table_name: str, column_name: str, value: int) -> None:
        """
        مقدار sequence مرتبط با یک ستون serial/identity را تنظیم می‌کند.
        از pg_get_serial_sequence استفاده می‌شود که نام sequence واقعی
        (که PostgreSQL خودش هنگام ساخت ستون IDENTITY تولید می‌کند) را پیدا می‌کند.
        """
        query = text(
            """
            SELECT setval(
                pg_get_serial_sequence(:table_name, :column_name),
                :value,
                true
            )
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                query,
                {"table_name": table_name, "column_name": column_name, "value": value},
            )

    def get_max_column_value(self, table_name: str, column_name: str) -> Optional[int]:
        """بیشترین مقدار فعلی یک ستون عددی را برمی‌گرداند (برای sync کردن sequence)."""
        query = text(
            f"SELECT MAX({safe_identifier(column_name)}) FROM {safe_identifier(table_name)}"
        )
        with self.engine.connect() as conn:
            result = conn.execute(query)
            value = result.scalar()
            return int(value) if value is not None else None
