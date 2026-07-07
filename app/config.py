"""
config.py
---------
مدیریت متمرکز تمام تنظیمات پروژه.
تمام مقادیر از متغیرهای محیطی (Environment Variables) خوانده می‌شوند
که معمولاً از طریق فایل .env و python-dotenv بارگذاری می‌شوند.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

# بارگذاری فایل .env در صورت وجود. اگر برنامه داخل Docker اجرا شود،
# متغیرها معمولاً مستقیماً از docker-compose تزریق می‌شوند، اما این خط
# اجرای local (خارج از Docker) را هم پشتیبانی می‌کند.
load_dotenv()


def _get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    """
    یک متغیر محیطی را می‌خواند.
    اگر required=True باشد و متغیر تنظیم نشده باشد، برنامه با پیام خطا متوقف می‌شود.
    """
    value = os.getenv(name, default)
    if required and (value is None or value.strip() == ""):
        print(f"[CONFIG ERROR] متغیر محیطی الزامی '{name}' تنظیم نشده است.", file=sys.stderr)
        sys.exit(1)
    return value if value is not None else ""


def _get_env_int(name: str, default: int) -> int:
    """یک متغیر محیطی عددی را می‌خواند و در صورت نامعتبر بودن مقدار پیش‌فرض را برمی‌گرداند."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(
            f"[CONFIG WARNING] مقدار '{raw}' برای متغیر '{name}' عدد معتبر نیست. "
            f"از مقدار پیش‌فرض {default} استفاده می‌شود.",
            file=sys.stderr,
        )
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    """یک متغیر محیطی بولین را می‌خواند (true/false/1/0/yes/no)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_env_list(name: str) -> List[str]:
    """یک متغیر محیطی comma-separated را به لیست تبدیل می‌کند."""
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout: int

    @property
    def sqlalchemy_url(self) -> str:
        # از pymysql به عنوان درایور استفاده می‌شود.
        from urllib.parse import quote_plus

        pwd = quote_plus(self.password)
        user = quote_plus(self.user)
        return (
            f"mysql+pymysql://{user}:{pwd}@{self.host}:{self.port}/"
            f"{self.database}?charset=utf8mb4"
        )


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    connect_timeout: int

    @property
    def sqlalchemy_url(self) -> str:
        from urllib.parse import quote_plus

        pwd = quote_plus(self.password)
        user = quote_plus(self.user)
        return (
            f"postgresql+psycopg2://{user}:{pwd}@{self.host}:{self.port}/"
            f"{self.database}"
        )


@dataclass(frozen=True)
class MigrationConfig:
    batch_size: int
    drop_existing_tables: bool
    schema_only: bool
    data_only: bool
    exclude_tables: List[str]
    include_tables: List[str]
    state_file: str
    log_file: str
    log_level: str
    stop_on_error: bool


@dataclass(frozen=True)
class Settings:
    mysql: MySQLConfig
    postgres: PostgresConfig
    migration: MigrationConfig

    def validate(self) -> None:
        """اعتبارسنجی نهایی تنظیمات پیش از شروع migration."""
        errors = []

        if self.migration.batch_size <= 0:
            errors.append("BATCH_SIZE باید عددی مثبت باشد.")

        if self.migration.schema_only and self.migration.data_only:
            errors.append(
                "SCHEMA_ONLY و DATA_ONLY هر دو نمی‌توانند true باشند؛ "
                "این دو گزینه متضاد یکدیگرند."
            )

        overlap = set(self.migration.exclude_tables) & set(self.migration.include_tables)
        if overlap:
            errors.append(
                f"جداول زیر هم در EXCLUDE_TABLES و هم در INCLUDE_TABLES قرار دارند: "
                f"{', '.join(sorted(overlap))}"
            )

        if errors:
            print("[CONFIG ERROR] خطاهای زیر در تنظیمات یافت شد:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)


def load_settings() -> Settings:
    """تمام تنظیمات پروژه را از متغیرهای محیطی بارگذاری کرده و برمی‌گرداند."""

    mysql = MySQLConfig(
        host=_get_env("MYSQL_HOST", required=True),
        port=_get_env_int("MYSQL_PORT", 3306),
        database=_get_env("MYSQL_DATABASE", required=True),
        user=_get_env("MYSQL_USER", required=True),
        password=_get_env("MYSQL_PASSWORD", default=""),
        connect_timeout=_get_env_int("CONNECT_TIMEOUT", 30),
    )

    postgres = PostgresConfig(
        host=_get_env("POSTGRES_HOST", required=True),
        port=_get_env_int("POSTGRES_PORT", 5432),
        database=_get_env("POSTGRES_DATABASE", required=True),
        user=_get_env("POSTGRES_USER", required=True),
        password=_get_env("POSTGRES_PASSWORD", default=""),
        connect_timeout=_get_env_int("CONNECT_TIMEOUT", 30),
    )

    migration = MigrationConfig(
        batch_size=_get_env_int("BATCH_SIZE", 1000),
        drop_existing_tables=_get_env_bool("DROP_EXISTING_TABLES", False),
        schema_only=_get_env_bool("SCHEMA_ONLY", False),
        data_only=_get_env_bool("DATA_ONLY", False),
        exclude_tables=_get_env_list("EXCLUDE_TABLES"),
        include_tables=_get_env_list("INCLUDE_TABLES"),
        state_file=_get_env("STATE_FILE", default="/app/logs/migration_state.json"),
        log_file=_get_env("LOG_FILE", default="/app/logs/migration.log"),
        log_level=_get_env("LOG_LEVEL", default="INFO").upper(),
        stop_on_error=_get_env_bool("STOP_ON_ERROR", False),
    )

    settings = Settings(mysql=mysql, postgres=postgres, migration=migration)
    settings.validate()
    return settings
