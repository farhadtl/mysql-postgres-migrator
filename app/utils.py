"""
utils.py
--------
توابع کمکی مشترک که در سایر ماژول‌ها استفاده می‌شوند:
- مدیریت فایل state برای قابلیت Resume
- فرمت‌بندی زمان و اعداد
- کمک‌تابع‌های عمومی
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


# ======================================================================
# مدیریت State برای قابلیت Resume
# ======================================================================

@dataclass
class MigrationState:
    """
    ساختار داده‌ای که وضعیت فعلی migration را نگه می‌دارد.
    این شیء به صورت دوره‌ای در یک فایل JSON ذخیره می‌شود تا در صورت قطع شدن
    برنامه، بتوان migration را از همان نقطه ادامه داد.
    """

    # نام آخرین جدولی که migrate آن شروع شده (ممکن است کامل نشده باشد)
    current_table: Optional[str] = None

    # تعداد ردیف‌هایی که از current_table با موفقیت منتقل شده‌اند
    current_table_offset: int = 0

    # لیست جداولی که migrate آن‌ها کاملاً به پایان رسیده (schema + data)
    completed_tables: list = field(default_factory=list)

    # لیست جداولی که schema آن‌ها ساخته شده (برای جلوگیری از CREATE TABLE تکراری)
    schema_created_tables: list = field(default_factory=list)

    # لیست جداولی که Foreign Key های آن‌ها اعمال شده
    fk_applied_tables: list = field(default_factory=list)

    # لیست جداولی که Index/Constraint های آن‌ها اعمال شده
    index_applied_tables: list = field(default_factory=list)

    # لیست جداولی که Sequence/Auto Increment آن‌ها sync شده
    sequence_synced_tables: list = field(default_factory=list)

    # زمان شروع کل عملیات migration (ISO format) - برای محاسبه Duration کل
    started_at: Optional[str] = None

    # مجموع خطاهای رخ داده تا این لحظه
    total_errors: int = 0

    # مجموع ردیف‌های منتقل شده تا این لحظه (در کل migration، نه فقط جدول جاری)
    total_rows_migrated: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_table": self.current_table,
            "current_table_offset": self.current_table_offset,
            "completed_tables": self.completed_tables,
            "schema_created_tables": self.schema_created_tables,
            "fk_applied_tables": self.fk_applied_tables,
            "index_applied_tables": self.index_applied_tables,
            "sequence_synced_tables": self.sequence_synced_tables,
            "started_at": self.started_at,
            "total_errors": self.total_errors,
            "total_rows_migrated": self.total_rows_migrated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MigrationState":
        return cls(
            current_table=data.get("current_table"),
            current_table_offset=data.get("current_table_offset", 0),
            completed_tables=data.get("completed_tables", []),
            schema_created_tables=data.get("schema_created_tables", []),
            fk_applied_tables=data.get("fk_applied_tables", []),
            index_applied_tables=data.get("index_applied_tables", []),
            sequence_synced_tables=data.get("sequence_synced_tables", []),
            started_at=data.get("started_at"),
            total_errors=data.get("total_errors", 0),
            total_rows_migrated=data.get("total_rows_migrated", 0),
        )


class StateManager:
    """
    مسئول خواندن/نوشتن فایل state روی دیسک.
    این کلاس تضمین می‌کند نوشتن فایل به صورت atomic انجام شود
    (نوشتن در فایل موقت و سپس rename) تا در صورت crash در حین نوشتن،
    فایل state خراب (corrupt) نشود.
    """

    def __init__(self, state_file_path: str):
        self.state_file_path = state_file_path
        state_dir = os.path.dirname(state_file_path)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)

    def load(self) -> MigrationState:
        """
        فایل state موجود را می‌خواند. اگر فایل وجود نداشته باشد یا خراب باشد،
        یک state خالی جدید برمی‌گرداند.
        """
        if not os.path.exists(self.state_file_path):
            return MigrationState(started_at=datetime.utcnow().isoformat())

        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            state = MigrationState.from_dict(data)
            if state.started_at is None:
                state.started_at = datetime.utcnow().isoformat()
            return state
        except (json.JSONDecodeError, OSError):
            # فایل state خراب است؛ به صورت امن، یک state جدید شروع می‌شود
            return MigrationState(started_at=datetime.utcnow().isoformat())

    def save(self, state: MigrationState) -> None:
        """state را به صورت atomic روی دیسک ذخیره می‌کند."""
        tmp_path = f"{self.state_file_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.state_file_path)

    def reset(self) -> MigrationState:
        """state را پاک کرده و یک state خالی جدید برمی‌گرداند (برای اجرای تازه)."""
        if os.path.exists(self.state_file_path):
            os.remove(self.state_file_path)
        return MigrationState(started_at=datetime.utcnow().isoformat())


# ======================================================================
# کمک‌تابع‌های فرمت‌بندی
# ======================================================================

def format_duration(seconds: float) -> str:
    """ثانیه را به فرمت HH:MM:SS تبدیل می‌کند."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_number(n: int) -> str:
    """عدد را با جداکننده هزارگان فرمت می‌کند. مثال: 5432192 -> 5,432,192"""
    return f"{n:,}"


class Timer:
    """یک تایمر ساده برای اندازه‌گیری مدت زمان اجرای عملیات."""

    def __init__(self) -> None:
        self._start: Optional[float] = None
        self._end: Optional[float] = None

    def start(self) -> None:
        self._start = time.monotonic()
        self._end = None

    def stop(self) -> None:
        self._end = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        if self._start is None:
            return 0.0
        end = self._end if self._end is not None else time.monotonic()
        return end - self._start


def chunked_range(total: int, batch_size: int):
    """
    یک generator که offset ها را به صورت batch تولید می‌کند.
    مثال: chunked_range(2500, 1000) -> (0, 1000), (1000, 1000), (2000, 500)
    """
    offset = 0
    while offset < total:
        current_batch = min(batch_size, total - offset)
        yield offset, current_batch
        offset += current_batch


def safe_identifier(name: str) -> str:
    """
    یک شناسه (نام جدول/ستون) را برای استفاده امن در PostgreSQL آماده می‌کند.
    PostgreSQL شناسه‌ها را lower-case می‌کند مگر داخل کوتیشن باشند؛
    برای سازگاری کامل و جلوگیری از تداخل با کلمات کلیدی، همیشه داخل
    دابل‌کوتیشن قرار می‌گیرند.
    """
    escaped = name.replace('"', '""')
    return f'"{escaped}"'
