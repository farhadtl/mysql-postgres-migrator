"""
data_migrator.py
------------------
هسته اصلی فرآیند migration. این ماژول تمام مراحل را به ترتیب صحیح
هماهنگ می‌کند:

  1. اتصال به هر دو دیتابیس
  2. خواندن لیست جداول از MySQL (با اعمال include/exclude)
  3. برای هر جدول:
     a. خواندن schema کامل از MySQL
     b. ساخت جدول در PostgreSQL (اگر قبلاً ساخته نشده - پشتیبانی از Resume)
     c. انتقال داده‌ها به صورت batch (با Resume از آخرین offset)
  4. بعد از اتمام تمام جداول:
     a. اعمال Index/Unique Constraint ها
     b. اعمال Foreign Key ها (بعد از همه چون به PK/Unique جداول دیگر نیاز دارند)
     c. تبدیل Auto Increment به Identity و sync سازی sequence ها
  5. تولید گزارش نهایی

طراحی به گونه‌ای است که در صورت قطع شدن برنامه در هر مرحله، اجرای بعدی
از طریق state file می‌تواند دقیقاً از همان نقطه ادامه یابد.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from tqdm import tqdm

from config import Settings
from fk_converter import FKConverter
from index_converter import IndexConverter
from mysql_reader import MySQLReader, TableSchema
from postgres_writer import PostgresWriter
from schema_converter import SchemaConverter
from sequence_converter import SequenceConverter
from utils import (
    MigrationState,
    StateManager,
    Timer,
    format_duration,
    format_number,
)


class MigrationReport:
    """جمع‌آوری آمار نهایی برای گزارش پایانی."""

    def __init__(self) -> None:
        self.tables_migrated: int = 0
        self.total_rows: int = 0
        self.total_errors: int = 0
        self.per_table_rows: dict = {}
        self.per_table_errors: dict = {}
        self.skipped_tables: List[str] = []

    def record_table(self, table_name: str, rows: int, errors: int) -> None:
        self.tables_migrated += 1
        self.total_rows += rows
        self.total_errors += errors
        self.per_table_rows[table_name] = rows
        self.per_table_errors[table_name] = errors

    def record_skip(self, table_name: str) -> None:
        self.skipped_tables.append(table_name)


class DataMigrator:
    """
    کلاس اصلی که کل فرآیند migration را orchestrate می‌کند.
    """

    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger

        self.mysql_reader = MySQLReader(settings.mysql, logger)
        self.pg_writer = PostgresWriter(settings.postgres, logger)
        self.schema_converter = SchemaConverter(logger)
        self.index_converter = IndexConverter(logger)
        self.fk_converter = FKConverter(logger)
        self.sequence_converter = SequenceConverter(logger)

        self.state_manager = StateManager(settings.migration.state_file)
        self.state: MigrationState = self.state_manager.load()

        self.report = MigrationReport()
        self.timer = Timer()

    # ------------------------------------------------------------------
    # نقطه ورود اصلی
    # ------------------------------------------------------------------

    def run(self) -> MigrationReport:
        self.timer.start()
        self.logger.info("=" * 70)
        self.logger.info("شروع فرآیند Migration از MySQL به PostgreSQL")
        self.logger.info("=" * 70)

        self._test_connections()

        table_names = self._resolve_table_list()
        if not table_names:
            self.logger.warning("هیچ جدولی برای migrate کردن یافت نشد.")
            return self.report

        self.logger.info(f"تعداد {len(table_names)} جدول برای migration شناسایی شد.")

        # مرحله 1: خواندن schema کامل تمام جداول (لازم برای مرتب‌سازی وابستگی FK)
        table_schemas = self._read_all_schemas(table_names)

        # مرحله 2: ساخت جداول (Schema) به ترتیب وابستگی
        if not self.settings.migration.data_only:
            ordered_schemas = self.fk_converter.sort_tables_by_dependency(table_schemas)
            self._create_all_tables(ordered_schemas)
        else:
            ordered_schemas = table_schemas

        # مرحله 3: انتقال داده‌ها (به همان ترتیب وابستگی، برای رعایت منطقی FK والد قبل از فرزند)
        if not self.settings.migration.schema_only:
            self._migrate_all_data(ordered_schemas)

        # مرحله 4: اعمال Index/Unique Constraint
        if not self.settings.migration.data_only:
            self._apply_all_indexes(ordered_schemas)

            # مرحله 5: اعمال Foreign Key ها (بعد از Index چون به Unique/PK نیاز دارند)
            self._apply_all_foreign_keys(ordered_schemas)

            # مرحله 6: تبدیل Auto Increment به Identity + sync سازی
            self._apply_all_sequences(ordered_schemas)

        self.timer.stop()
        self._print_final_report()

        return self.report

    # ------------------------------------------------------------------
    # اتصال اولیه
    # ------------------------------------------------------------------

    def _test_connections(self) -> None:
        try:
            self.mysql_reader.test_connection()
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"اتصال به MySQL ناموفق بود: {exc}")
            raise

        try:
            self.pg_writer.test_connection()
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"اتصال به PostgreSQL ناموفق بود: {exc}")
            raise

    # ------------------------------------------------------------------
    # تعیین لیست نهایی جداول (با اعمال include/exclude)
    # ------------------------------------------------------------------

    def _resolve_table_list(self) -> List[str]:
        all_tables = self.mysql_reader.get_table_names()

        include = set(self.settings.migration.include_tables)
        exclude = set(self.settings.migration.exclude_tables)

        if include:
            tables = [t for t in all_tables if t in include]
            missing = include - set(tables)
            if missing:
                self.logger.warning(
                    f"جداول زیر در INCLUDE_TABLES مشخص شده‌اند اما در MySQL "
                    f"یافت نشدند: {', '.join(sorted(missing))}"
                )
        else:
            tables = all_tables

        if exclude:
            tables = [t for t in tables if t not in exclude]

        return tables

    # ------------------------------------------------------------------
    # خواندن Schema تمام جداول
    # ------------------------------------------------------------------

    def _read_all_schemas(self, table_names: List[str]) -> List[TableSchema]:
        self.logger.info("در حال خواندن ساختار (schema) تمام جداول از MySQL...")
        schemas = []
        for name in tqdm(table_names, desc="خواندن Schema", unit="جدول"):
            try:
                schema = self.mysql_reader.get_full_table_schema(name)
                schemas.append(schema)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"خطا در خواندن schema جدول '{name}': {exc}")
                self.state.total_errors += 1
                if self.settings.migration.stop_on_error:
                    raise
        return schemas

    # ------------------------------------------------------------------
    # ساخت جداول
    # ------------------------------------------------------------------

    def _create_all_tables(self, table_schemas: List[TableSchema]) -> None:
        self.logger.info("در حال ساخت جداول در PostgreSQL...")

        for schema in tqdm(table_schemas, desc="ساخت جداول", unit="جدول"):
            if schema.name in self.state.schema_created_tables:
                self.logger.debug(
                    f"جدول '{schema.name}' قبلاً ساخته شده (طبق state)؛ رد شد."
                )
                continue

            try:
                self._create_single_table(schema)
                self.state.schema_created_tables.append(schema.name)
                self.state_manager.save(self.state)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"خطا در ساخت جدول '{schema.name}': {exc}")
                self.state.total_errors += 1
                self.state_manager.save(self.state)
                if self.settings.migration.stop_on_error:
                    raise

    def _create_single_table(self, schema: TableSchema) -> None:
        table_already_exists = self.pg_writer.table_exists(schema.name)

        if table_already_exists:
            if self.settings.migration.drop_existing_tables:
                self.logger.info(f"جدول '{schema.name}' از قبل وجود دارد؛ DROP می‌شود.")
                self.pg_writer.execute_ddl(
                    self.schema_converter.build_drop_table_ddl(schema.name)
                )
            else:
                self.logger.info(
                    f"جدول '{schema.name}' از قبل در PostgreSQL وجود دارد؛ "
                    f"از ساخت مجدد صرف‌نظر شد (DROP_EXISTING_TABLES=false)."
                )
                return

        create_ddl, enum_checks = self.schema_converter.build_create_table_ddl(schema)
        self.pg_writer.execute_ddl(create_ddl)
        self.logger.info(f"جدول '{schema.name}' با موفقیت ساخته شد.")

        # اعمال CHECK constraint های enum
        for check_ddl in enum_checks:
            self.pg_writer.execute_ddl(check_ddl)

        # اعمال کامنت جدول و ستون‌ها (best-effort - خطا در این مرحله بحرانی نیست)
        try:
            table_comment_ddl = self.schema_converter.build_table_comment_ddl(schema)
            if table_comment_ddl:
                self.pg_writer.execute_ddl(table_comment_ddl)
            for comment_ddl in self.schema_converter.build_column_comment_ddls(schema):
                self.pg_writer.execute_ddl(comment_ddl)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"اعمال کامنت‌ها برای جدول '{schema.name}' ناموفق بود: {exc}")

    # ------------------------------------------------------------------
    # انتقال داده‌ها
    # ------------------------------------------------------------------

    def _migrate_all_data(self, table_schemas: List[TableSchema]) -> None:
        self.logger.info("در حال انتقال داده‌ها...")

        for schema in table_schemas:
            if schema.name in self.state.completed_tables:
                self.logger.info(
                    f"جدول '{schema.name}' قبلاً به طور کامل منتقل شده (طبق state)؛ رد شد."
                )
                self.report.record_table(
                    schema.name,
                    rows=self.pg_writer.get_row_count(schema.name)
                    if self.pg_writer.table_exists(schema.name)
                    else 0,
                    errors=0,
                )
                continue

            self._migrate_single_table_data(schema)

    def _migrate_single_table_data(self, schema: TableSchema) -> None:
        table_name = schema.name
        column_names = [c.name for c in schema.columns]
        column_type_map = self.schema_converter.build_column_type_map(schema)

        # تعیین offset شروع: اگر همین جدول، جدول جاری در state باشد (یعنی
        # migration قبلی وسط این جدول قطع شده)، از همان offset ادامه می‌دهیم.
        start_offset = 0
        if self.state.current_table == table_name:
            start_offset = self.state.current_table_offset
            self.logger.info(
                f"ادامه انتقال جدول '{table_name}' از ردیف {format_number(start_offset)}"
            )
        else:
            self.state.current_table = table_name
            self.state.current_table_offset = 0
            self.state_manager.save(self.state)

        total_rows = schema.row_count
        migrated_in_this_run = 0
        table_errors = 0

        progress = tqdm(
            total=total_rows,
            initial=start_offset,
            desc=f"{table_name}",
            unit="ردیف",
        )

        try:
            batch_iterator = self.mysql_reader.iter_rows_batched(
                table_name,
                batch_size=self.settings.migration.batch_size,
                start_offset=start_offset,
            )

            current_offset = start_offset

            for batch in batch_iterator:
                try:
                    coerced_batch = self.schema_converter.coerce_row_values(
                        batch, column_type_map
                    )
                    inserted = self.pg_writer.insert_batch(
                        table_name, column_names, coerced_batch
                    )
                    migrated_in_this_run += inserted
                    current_offset += len(batch)

                    # به‌روزرسانی state بعد از هر batch موفق - این کلید قابلیت Resume است
                    self.state.current_table_offset = current_offset
                    self.state.total_rows_migrated += inserted
                    self.state_manager.save(self.state)

                    progress.update(len(batch))
                except Exception as exc:  # noqa: BLE001
                    table_errors += 1
                    self.state.total_errors += 1
                    self.logger.error(
                        f"خطا در درج batch برای جدول '{table_name}' در offset "
                        f"{current_offset}: {exc}"
                    )
                    self.state_manager.save(self.state)
                    if self.settings.migration.stop_on_error:
                        raise
                    # اگر ادامه بدهیم، از این batch عبور می‌کنیم تا گیر نکنیم
                    current_offset += len(batch)
                    self.state.current_table_offset = current_offset
                    self.state_manager.save(self.state)
                    progress.update(len(batch))

        finally:
            progress.close()

        # جدول به پایان رسید (یا با خطا از آن عبور کردیم)
        self.state.completed_tables.append(table_name)
        self.state.current_table = None
        self.state.current_table_offset = 0
        self.state_manager.save(self.state)

        self.report.record_table(table_name, migrated_in_this_run, table_errors)

        self.logger.info(
            f"جدول '{table_name}' تکمیل شد: "
            f"{format_number(migrated_in_this_run)} ردیف منتقل شد، "
            f"{table_errors} خطا."
        )

    # ------------------------------------------------------------------
    # اعمال Index ها
    # ------------------------------------------------------------------

    def _apply_all_indexes(self, table_schemas: List[TableSchema]) -> None:
        self.logger.info("در حال اعمال Index ها و Unique Constraint ها...")

        for schema in tqdm(table_schemas, desc="اعمال Index", unit="جدول"):
            if schema.name in self.state.index_applied_tables:
                continue

            if not schema.indexes:
                self.state.index_applied_tables.append(schema.name)
                continue

            ddls = self.index_converter.build_index_ddls(schema)
            errors = self.pg_writer.execute_ddl_many(ddls, continue_on_error=True)

            if errors:
                self.state.total_errors += len(errors)

            self.state.index_applied_tables.append(schema.name)
            self.state_manager.save(self.state)

    # ------------------------------------------------------------------
    # اعمال Foreign Key ها
    # ------------------------------------------------------------------

    def _apply_all_foreign_keys(self, table_schemas: List[TableSchema]) -> None:
        self.logger.info("در حال اعمال Foreign Key ها...")

        for schema in tqdm(table_schemas, desc="اعمال Foreign Key", unit="جدول"):
            if schema.name in self.state.fk_applied_tables:
                continue

            if not schema.foreign_keys:
                self.state.fk_applied_tables.append(schema.name)
                continue

            ddls = self.fk_converter.build_fk_ddls(schema)
            errors = self.pg_writer.execute_ddl_many(ddls, continue_on_error=True)

            if errors:
                self.state.total_errors += len(errors)

            self.state.fk_applied_tables.append(schema.name)
            self.state_manager.save(self.state)

    # ------------------------------------------------------------------
    # اعمال Sequence / Auto Increment
    # ------------------------------------------------------------------

    def _apply_all_sequences(self, table_schemas: List[TableSchema]) -> None:
        self.logger.info("در حال تبدیل Auto Increment به Identity و همگام‌سازی Sequence ها...")

        for schema in tqdm(table_schemas, desc="اعمال Sequence", unit="جدول"):
            if schema.name in self.state.sequence_synced_tables:
                continue

            auto_inc_col = self.sequence_converter.get_auto_increment_column(schema)
            if auto_inc_col is None:
                self.state.sequence_synced_tables.append(schema.name)
                continue

            try:
                pg_type = self.schema_converter.convert_column_type(auto_inc_col, schema.name)
                identity_ddl = self.sequence_converter.build_identity_ddl(schema, pg_type)
                if identity_ddl:
                    self.pg_writer.execute_ddl(identity_ddl)

                self.sequence_converter.sync_sequence(self.pg_writer, schema)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(
                    f"خطا در اعمال Sequence/Identity برای جدول '{schema.name}': {exc}"
                )
                self.state.total_errors += 1
                if self.settings.migration.stop_on_error:
                    raise

            self.state.sequence_synced_tables.append(schema.name)
            self.state_manager.save(self.state)

    # ------------------------------------------------------------------
    # گزارش نهایی
    # ------------------------------------------------------------------

    def _print_final_report(self) -> None:
        duration = format_duration(self.timer.elapsed_seconds)

        self.logger.info("=" * 70)
        self.logger.info("گزارش نهایی Migration")
        self.logger.info("=" * 70)
        self.logger.info(f"Tables   : {self.report.tables_migrated}")
        self.logger.info(f"Rows     : {format_number(self.report.total_rows)}")
        self.logger.info(f"Duration : {duration}")
        self.logger.info(f"Errors   : {self.report.total_errors}")

        if self.report.skipped_tables:
            self.logger.info(
                f"جداول نادیده گرفته شده: {', '.join(self.report.skipped_tables)}"
            )

        self.logger.info("=" * 70)

        # چاپ خلاصه هم در stdout به فرمت ساده و خوانا برای کاربر
        print("\n" + "=" * 40)
        print("خلاصه Migration")
        print("=" * 40)
        print(f"Tables   : {self.report.tables_migrated}")
        print(f"Rows     : {format_number(self.report.total_rows)}")
        print(f"Duration : {duration}")
        print(f"Errors   : {self.report.total_errors}")
        print("=" * 40)

    def close(self) -> None:
        """بستن تمام اتصالات باز به دیتابیس‌ها."""
        self.mysql_reader.close()
        self.pg_writer.close()
