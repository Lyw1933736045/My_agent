"""Conservative date normalization for source and event cutoff checks."""

from __future__ import annotations

from datetime import datetime, time
import re
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")


def parse_datetime(value: object, *, end_of_day: bool = False) -> datetime | None:
    """Parse common ISO/Chinese report dates; return None instead of guessing."""
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace("Z", "+00:00")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    candidates = [normalized]
    match = re.search(r"(20\d{2})[-.](\d{1,2})[-.](\d{1,2})", normalized)
    if match:
        year, month, day = (int(part) for part in match.groups())
        date_only = f"{year:04d}-{month:02d}-{day:02d}"
        candidates.append(date_only)
    match = re.fullmatch(r"(\d{1,2})[-.](\d{1,2})", normalized)
    if match:
        # A yearless date is intentionally not inferred here.
        return None
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CHINA_TZ)
        if end_of_day and parsed.time() == time.min:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed
    return None
