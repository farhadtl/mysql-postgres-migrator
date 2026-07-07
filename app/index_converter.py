"""
index_converter.py
-------------------
مسئول تولید DDL برای Index ها و Unique Constraint ها.
این DDL ها پس از درج کامل داده‌ها اجرا می‌شوند (نه قبل از آن)، چون ساخت
Index بعد از پر شدن جدول در PostgreSQL معمولاً به‌مراتب سریع‌تر از نگه‌داشتن
Index در حین INSERT های پیاپی است.
"""

from __future__ import annotations

import logging
from typing import List

from mysql_reader import IndexInfo, TableSchema
from utils import safe_identifier


class IndexConverter:
    """تولید DDL برای CREATE INDEX و UNIQUE CONSTRAINT بر اساس اطلاعات استخراج‌شده از MySQL."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def build_index_ddls(self, table_schema: TableSchema) -> List[str]:
        """
        برای هر Index موجود در جدول (به جز PRIMARY که در schema_converter مدیریت
        می‌شود)، دستور مناسب تولید می‌کند:
        - اگر unique باشد: ALTER TABLE ... ADD CONSTRAINT ... UNIQUE (...)
        - در غیر این صورت: CREATE INDEX ... ON ... (...)
        """
        ddls: List[str] = []
        table_ident = safe_identifier(table_schema.name)

        for index in table_schema.indexes:
            index_name = self._safe_index_name(table_schema.name, index.name)
            columns_sql = ", ".join(safe_identifier(c) for c in index.columns)

            if index.is_unique:
                ddls.append(
                    f"ALTER TABLE {table_ident} "
                    f"ADD CONSTRAINT {safe_identifier(index_name)} "
                    f"UNIQUE ({columns_sql})"
                )
            else:
                ddls.append(
                    f"CREATE INDEX {safe_identifier(index_name)} "
                    f"ON {table_ident} ({columns_sql})"
                )

        return ddls

    @staticmethod
    def _safe_index_name(table_name: str, index_name: str) -> str:
        """
        نام index را برای جلوگیری از تداخل بین جداول مختلف (که ممکن است
        نام index یکسانی در MySQL داشته باشند) با پیشوند نام جدول یکتا می‌کند،
        و طول آن را به حداکثر مجاز PostgreSQL (63 کاراکتر) محدود می‌کند.
        """
        combined = f"{table_name}_{index_name}"
        max_len = 63
        if len(combined) <= max_len:
            return combined
        # کوتاه کردن با حفظ خوانایی: بخشی از نام جدول + بخشی از نام ایندکس
        # به همراه هش کوتاه برای یکتایی
        import hashlib

        suffix = hashlib.md5(combined.encode("utf-8")).hexdigest()[:8]
        truncated = combined[: max_len - len(suffix) - 1]
        return f"{truncated}_{suffix}"
