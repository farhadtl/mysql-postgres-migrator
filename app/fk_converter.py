"""
fk_converter.py
---------------
مسئول تولید DDL برای Foreign Key ها.

نکته مهم: Foreign Key ها باید پس از این‌ها اجرا شوند:
  1. تمام جداول ساخته شده باشند (schema کامل شود)
  2. تمام داده‌ها منتقل شده باشند
  3. Primary Key و Unique Constraint های جداول مرجع (parent) اعمال شده باشند
    (چون FK به یک ستون UNIQUE/PRIMARY KEY در جدول مقصد نیاز دارد)

به همین دلیل، اعمال FK ها در انتهای کل فرآیند migration (بعد از تمام
جداول، نه بعد از هر جدول) در main.py/data_migrator.py انجام می‌شود.
"""

from __future__ import annotations

import hashlib
import logging
from typing import List

from mysql_reader import ForeignKeyInfo, TableSchema
from utils import safe_identifier


# نگاشت مقادیر ON UPDATE / ON DELETE از MySQL به PostgreSQL.
# هر دو دیتابیس از همین مجموعه مقادیر پشتیبانی می‌کنند، اما نام‌گذاری را
# نرمال‌سازی می‌کنیم تا از مقادیر غیرمنتظره (مثل NO ACTION با فرمت متفاوت)
# جلوگیری شود.
_VALID_FK_ACTIONS = {"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"}


class FKConverter:
    """تولید DDL برای ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY بر اساس اطلاعات MySQL."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def build_fk_ddls(self, table_schema: TableSchema) -> List[str]:
        """
        برای هر Foreign Key موجود در جدول، دستور ALTER TABLE ADD CONSTRAINT
        مناسب را تولید می‌کند، با حفظ رفتار ON UPDATE / ON DELETE اصلی.
        """
        ddls: List[str] = []
        table_ident = safe_identifier(table_schema.name)

        for fk in table_schema.foreign_keys:
            constraint_name = self._safe_fk_name(table_schema.name, fk.name)
            local_cols = ", ".join(safe_identifier(c) for c in fk.columns)
            ref_cols = ", ".join(safe_identifier(c) for c in fk.ref_columns)
            ref_table = safe_identifier(fk.ref_table)

            on_update = self._normalize_action(fk.on_update)
            on_delete = self._normalize_action(fk.on_delete)

            ddl = (
                f"ALTER TABLE {table_ident} "
                f"ADD CONSTRAINT {safe_identifier(constraint_name)} "
                f"FOREIGN KEY ({local_cols}) "
                f"REFERENCES {ref_table} ({ref_cols}) "
                f"ON UPDATE {on_update} ON DELETE {on_delete}"
            )
            ddls.append(ddl)

        return ddls

    @staticmethod
    def _normalize_action(action: str) -> str:
        action_upper = (action or "").strip().upper()
        if action_upper in _VALID_FK_ACTIONS:
            return action_upper
        return "RESTRICT"

    @staticmethod
    def _safe_fk_name(table_name: str, fk_name: str) -> str:
        """
        نام constraint را با پیشوند نام جدول یکتا می‌کند و در صورت لزوم
        با هش کوتاه می‌کند تا از محدودیت 63 کاراکتری PostgreSQL عبور نکند.
        """
        combined = f"fk_{table_name}_{fk_name}"
        max_len = 63
        if len(combined) <= max_len:
            return combined
        suffix = hashlib.md5(combined.encode("utf-8")).hexdigest()[:8]
        truncated = combined[: max_len - len(suffix) - 1]
        return f"{truncated}_{suffix}"

    def sort_tables_by_dependency(
        self, table_schemas: List[TableSchema]
    ) -> List[TableSchema]:
        """
        جداول را بر اساس وابستگی FK مرتب می‌کند طوری‌که جداول parent
        (بدون وابستگی یا با وابستگی‌های قبلاً حل‌شده) زودتر بیایند.
        این ترتیب برای CREATE TABLE و درج داده مفید است (هرچند چون FK ها
        در این پروژه بعد از انتقال کامل داده‌ها اعمال می‌شوند، ترتیب اینجا
        صرفاً بهینه‌سازی است، نه الزام سخت).

        از الگوریتم topological sort ساده استفاده می‌شود. در صورت وجود
        وابستگی چرخه‌ای (self-referencing یا حلقه بین چند جدول)، آن جداول
        به ترتیب اصلی (نام الفبایی) در انتها اضافه می‌شوند تا کل فرآیند
        متوقف نشود.
        """
        name_to_schema = {t.name: t for t in table_schemas}
        visited = set()
        result: List[TableSchema] = []
        in_progress = set()

        def visit(table_name: str) -> None:
            if table_name in visited or table_name not in name_to_schema:
                return
            if table_name in in_progress:
                # وابستگی چرخه‌ای شناسایی شد؛ از رفتن عمیق‌تر صرف‌نظر می‌شود
                self.logger.warning(
                    f"وابستگی چرخه‌ای (circular dependency) شامل جدول "
                    f"'{table_name}' شناسایی شد. ترتیب پیش‌فرض حفظ می‌شود."
                )
                return
            in_progress.add(table_name)

            schema = name_to_schema[table_name]
            for fk in schema.foreign_keys:
                if fk.ref_table != table_name:  # از self-reference رد می‌شویم
                    visit(fk.ref_table)

            in_progress.discard(table_name)
            visited.add(table_name)
            result.append(schema)

        for schema in sorted(table_schemas, key=lambda t: t.name):
            visit(schema.name)

        return result
