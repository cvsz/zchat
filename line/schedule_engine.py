"""Pure scheduling rules for recurring LINE status changes."""
from __future__ import annotations

from datetime import datetime, timedelta


def due_schedules(schedule, now=None, grace_seconds=90):
    """Return newly due item copies and their occurrence keys.

    Weekly entries fire once per local date/minute. Interval entries use their
    last-fired time; the first interval starts when its entry is created.
    Callers must persist each returned occurrence key before doing UI work.
    """
    now = now or datetime.now()
    if schedule.get("paused"):
        return []
    fired = schedule.get("last_fired")
    if not isinstance(fired, dict):
        fired = {}
        schedule["last_fired"] = fired
    due = []
    for item in schedule.get("items", []):
        if not isinstance(item, dict):
            continue
        if not item.get("enabled", True):
            continue
        item_id = item.get("id")
        if not item_id or not isinstance(item_id, str):
            continue
        mode = item.get("mode", "weekly")
        if mode == "interval":
            try:
                minutes = int(item.get("interval_minutes"))
            except (TypeError, ValueError):
                continue
            if not 1 <= minutes <= 10080:
                continue
            previous_raw = fired.get(item_id)
            try:
                previous = datetime.fromisoformat(previous_raw) if previous_raw else None
            except (TypeError, ValueError):
                previous = None
            # ISO-8601 markers may be naive (older configs) or timezone-aware
            # (hand-edited/imported configs). Compare both forms on one timeline.
            if previous is None or now.timestamp() - previous.timestamp() >= minutes * 60:
                occurrence = now.isoformat(timespec="seconds")
                due.append((dict(item), occurrence))
            continue

        days = item.get("days")
        if not isinstance(days, (list, tuple)) or not days:
            continue
        try:
            if now.weekday() not in days:
                continue
            hour, minute = map(int, str(item.get("time", "")).split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
        except (ValueError, AttributeError):
            continue
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        age = (now - scheduled).total_seconds()
        if 0 <= age <= grace_seconds:
            occurrence = scheduled.isoformat(timespec="minutes")
            if fired.get(item_id) != occurrence:
                due.append((dict(item), occurrence))
    return due


def mark_schedule_fired(schedule, item_id, occurrence, now=None):
    """Persist a run marker and prune entries older than two weeks."""
    fired = schedule.setdefault("last_fired", {})
    fired[item_id] = occurrence
    try:
        ref_time = datetime.fromisoformat(occurrence)
    except Exception:
        ref_time = now or datetime.now()
    if ref_time.tzinfo is not None:
        cutoff = ref_time.timestamp() - timedelta(days=14).total_seconds()
    else:
        cutoff = ref_time.timestamp() - timedelta(days=14).total_seconds()
    for key, value in list(fired.items()):
        try:
            when = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            del fired[key]
            continue
        try:
            expired = when.timestamp() < cutoff
        except (OverflowError, OSError, ValueError):
            expired = True
        if expired:
            del fired[key]
