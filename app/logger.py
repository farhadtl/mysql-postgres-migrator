"""
logger.py
---------
راه‌اندازی متمرکز لاگینگ برای کل پروژه.
تمام خطاها و رویدادها هم در کنسول (stdout) و هم در فایل logs/migration.log ذخیره می‌شوند.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


_LOGGER_NAME = "migration"
_configured = False


def setup_logger(log_file: str, log_level: str = "INFO") -> logging.Logger:
    """
    لاگر اصلی پروژه را راه‌اندازی می‌کند.
    اگر قبلاً راه‌اندازی شده باشد، همان instance را برمی‌گرداند (idempotent).
    """
    global _configured

    logger = logging.getLogger(_LOGGER_NAME)

    if _configured:
        return logger

    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # اطمینان از وجود پوشه لاگ
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # هندلر فایل - چرخشی (Rotating) برای جلوگیری از رشد بی‌رویه فایل لاگ
    file_handler = RotatingFileHandler(
        log_file, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # هندلر کنسول
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # فایل جداگانه فقط برای خطاها، برای بررسی سریع‌تر در پایان کار
    errors_log_path = os.path.join(log_dir or ".", "errors.log")
    error_handler = RotatingFileHandler(
        errors_log_path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    logger.addHandler(error_handler)

    _configured = True
    return logger


def get_logger() -> logging.Logger:
    """لاگر اصلی پروژه را برمی‌گرداند. در صورت عدم راه‌اندازی، با تنظیمات پیش‌فرض راه‌اندازی می‌کند."""
    if not _configured:
        return setup_logger("/app/logs/migration.log", "INFO")
    return logging.getLogger(_LOGGER_NAME)
