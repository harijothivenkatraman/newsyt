"""
scheduler/peak_times.py
=======================
Calculates the next YouTube peak upload time in UTC.

Global peak slots are derived from when YouTube Shorts get the highest
engagement across the major viewing regions (IN, US, EU, AU). Videos
uploaded with publishAt set to these slots maximise initial velocity.

Peak windows (UTC):
  00:30 — India morning (7 AM IST)
  06:00 — Europe morning (7 AM CET / 8 AM IST midday)
  12:00 — US East morning (7 AM EST) / India evening (5:30 PM IST)
  14:00 — Global afternoon sweet spot
  17:00 — US East lunch (12 PM EST) / Europe evening (6 PM CET)
  19:00 — India prime time (12:30 AM IST next day) / Europe night
  22:00 — US West prime time (3 PM PST) / best global overlap slot
  23:30 — US East prime time (6:30 PM EST)

Usage
-----
    from scheduler.peak_times import next_peak_slot, schedule_mode

    if schedule_mode() == "peak":
        publish_at = next_peak_slot()   # next UTC datetime
    else:
        publish_at = None               # upload immediately
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from loguru import logger


# ── Peak slot definitions (UTC hours + minutes) ────────────────────────────────

PEAK_SLOTS_UTC: list[tuple[int, int]] = [
    (0,  30),   # India morning
    (3,  30),   # India midday / Australia morning
    (6,  0),    # Europe morning
    (9,  0),    # Europe midday / India evening
    (12, 0),    # US East morning
    (14, 0),    # Global afternoon
    (17, 0),    # US lunch / Europe evening
    (19, 0),    # US afternoon / Asia night
    (22, 0),    # US West prime (BEST global slot)
    (23, 30),   # US East prime
]

# Minimum lead time: YouTube needs at least 15 min to process a scheduled video
_MIN_LEAD_MINUTES = 20


def _env_slots() -> list[tuple[int, int]]:
    """
    Override peak slots via PEAK_SLOTS_UTC env var.
    Format: "00:30,06:00,14:00,22:00"
    """
    raw = os.getenv("PEAK_SLOTS_UTC", "").strip()
    if not raw:
        return []
    try:
        slots = []
        for part in raw.split(","):
            h, m = part.strip().split(":")
            slots.append((int(h), int(m)))
        return slots
    except Exception:
        return []


def schedule_mode() -> str:
    """Return 'peak' or 'immediate' based on UPLOAD_SCHEDULE env var."""
    return os.getenv("UPLOAD_SCHEDULE", "immediate").strip().lower()


def next_peak_slot(after: Optional[datetime] = None) -> datetime:
    """
    Return the next upcoming peak publish time (UTC, timezone-aware).

    Parameters
    ----------
    after : datetime, optional
        Find the next slot after this time. Defaults to now + MIN_LEAD_MINUTES.

    Returns
    -------
    datetime (UTC, timezone-aware)
    """
    slots = _env_slots() or PEAK_SLOTS_UTC

    if after is None:
        after = datetime.now(timezone.utc) + timedelta(minutes=_MIN_LEAD_MINUTES)
    elif after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)

    # Try today's slots first, then tomorrow's
    for day_offset in range(3):  # look up to 3 days ahead
        base = after.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
        for h, m in sorted(slots):
            candidate = base.replace(hour=h, minute=m)
            if candidate > after:
                logger.debug(f"[PeakTimes] Next peak slot: {candidate.isoformat()}")
                return candidate

    # Fallback: 1 day from now at 22:00 UTC
    fallback = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )
    logger.warning(f"[PeakTimes] No slot found in 3 days, using fallback: {fallback}")
    return fallback


def slots_today_utc() -> list[datetime]:
    """Return all remaining peak slots for today in UTC."""
    slots = _env_slots() or PEAK_SLOTS_UTC
    now   = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    result = []
    for h, m in slots:
        candidate = today.replace(hour=h, minute=m)
        if candidate > now + timedelta(minutes=_MIN_LEAD_MINUTES):
            result.append(candidate)
    return result


def format_slot(dt: datetime) -> str:
    """Human-readable slot string, e.g. 'Today 22:00 UTC (Thu 3:30 AM IST)'."""
    ist = dt + timedelta(hours=5, minutes=30)
    now = datetime.now(timezone.utc)
    day = "Today" if dt.date() == now.date() else "Tomorrow"
    return (
        f"{day} {dt.strftime('%H:%M')} UTC "
        f"({ist.strftime('%a %I:%M %p')} IST)"
    )


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Schedule mode: {schedule_mode()}")
    print(f"Current UTC:   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    slot = next_peak_slot()
    print(f"Next peak slot: {format_slot(slot)}")
    print()

    remaining = slots_today_utc()
    print(f"Remaining slots today ({len(remaining)}):")
    for s in remaining:
        print(f"  {format_slot(s)}")
