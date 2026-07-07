"""
main.py
-------
نقطه ورود اصلی برنامه Migration.
این اسکریپت:
  1. تنظیمات را از .env بارگذاری می‌کند
  2. لاگر را راه‌اندازی می‌کند
  3. DataMigrator را اجرا می‌کند
  4. با کد خروجی مناسب (0 = موفق، 1 = خطا) پایان می‌یابد تا در صورت اجرا
     داخل Docker یا CI، وضعیت موفقیت/شکست به درستی قابل تشخیص باشد.
"""

from __future__ import annotations

import sys

from config import load_settings
from data_migrator import DataMigrator
from logger import setup_logger


def main() -> int:
    # مرحله 1: بارگذاری تنظیمات (در صورت نامعتبر بودن، load_settings خودش
    # پیام خطا چاپ کرده و sys.exit(1) می‌کند)
    settings = load_settings()

    # مرحله 2: راه‌اندازی لاگر
    logger = setup_logger(
        log_file=settings.migration.log_file,
        log_level=settings.migration.log_level,
    )

    migrator = DataMigrator(settings, logger)

    try:
        report = migrator.run()
        if report.total_errors > 0:
            logger.warning(
                f"Migration با {report.total_errors} خطا به پایان رسید. "
                f"جزئیات را در فایل لاگ بررسی کنید."
            )
            return 1
        logger.info("Migration با موفقیت و بدون خطا به پایان رسید.")
        return 0

    except KeyboardInterrupt:
        logger.warning(
            "Migration توسط کاربر متوقف شد (KeyboardInterrupt). "
            "پیشرفت در فایل state ذخیره شده و می‌توانید بعداً ادامه دهید."
        )
        return 130

    except Exception as exc:  # noqa: BLE001
        logger.error(f"Migration با خطای غیرمنتظره متوقف شد: {exc}", exc_info=True)
        logger.error(
            "پیشرفت تا این لحظه در فایل state ذخیره شده است. "
            "با اجرای مجدد برنامه، migration از همان نقطه ادامه می‌یابد."
        )
        return 1

    finally:
        migrator.close()


if __name__ == "__main__":
    sys.exit(main())
