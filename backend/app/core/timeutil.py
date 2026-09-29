"""Timezone helpers. The DB stores naive UTC; display logic uses the patient's timezone."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def patient_zone(tz_name):
    try:
        return ZoneInfo(tz_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def to_local(dt_utc, zone):
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(zone)


def local_day_bounds_utc(day: date, zone):
    """[start, end) of a local calendar day, as naive UTC datetimes."""
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def to_ms(dt):
    """Truncate a datetime to millisecond precision (the precision used in every layer)."""
    return dt.replace(microsecond=dt.microsecond // 1000 * 1000) if dt is not None else None


def iso_utc(dt):
    """Naive UTC datetime -> '2026-09-23T01:30:00.123Z' (api-design.md §1.1; same as JS toISOString)."""
    if dt is None:
        return None
    return dt.isoformat(timespec="milliseconds") + "Z"


def iso_date(d):
    return d.isoformat() if d else None


MAX_BACKDATE = timedelta(days=7)
MAX_CLOCK_SKEW = timedelta(minutes=5)


def parse_observed_at(raw, now, max_backdate=None):
    """Client-supplied observation time (ISO 8601 with timezone) → (naive UTC, error message|None).

    Missing means "now". Rejects future times (beyond small clock skew) and anything older
    than ``max_backdate`` (default 7 days).
    """
    max_backdate = max_backdate or MAX_BACKDATE
    if raw is None:
        return now, None
    if not isinstance(raw, str):
        return now, "must be an ISO 8601 datetime string"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return now, "must be an ISO 8601 datetime string"
    if parsed.tzinfo is None:
        return now, "must include a timezone (e.g. 'Z')"
    value = to_ms(parsed.astimezone(timezone.utc).replace(tzinfo=None))
    if value > now + MAX_CLOCK_SKEW:
        return value, "cannot be in the future"
    if value < now - max_backdate:
        return value, f"cannot be more than {max_backdate.days} days ago"
    return value, None
