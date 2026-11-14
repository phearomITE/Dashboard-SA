# app/services/time_service.py

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def local_now() -> datetime:
    settings = get_settings()
    return datetime.now(ZoneInfo(settings.APP_TIMEZONE)).replace(tzinfo=None)


def at_time(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def get_current_interval_start(now: datetime, interval_minutes: int) -> datetime:
    minute = (now.minute // interval_minutes) * interval_minutes
    return now.replace(minute=minute, second=0, microsecond=0)


def get_current_interval_end(now: datetime, interval_minutes: int) -> datetime:
    return get_current_interval_start(now, interval_minutes) + timedelta(minutes=interval_minutes)


def previous_session_window(now: datetime, session_start: datetime, interval_minutes: int):
    """
    Anchor window from session_start.

    Example:
    session_start 08:20, interval 20
    08:40 -> 08:20-08:40
    09:00 -> 08:40-09:00
    """
    if now <= session_start:
        return None

    elapsed_minutes = int((now - session_start).total_seconds() // 60)
    completed_intervals = elapsed_minutes // interval_minutes

    if completed_intervals <= 0:
        return None

    end = session_start + timedelta(minutes=completed_intervals * interval_minutes)
    start = end - timedelta(minutes=interval_minutes)

    return start, end


def iter_run_times(session_start: datetime, session_end: datetime, interval_minutes: int):
    """
    Yield daily run times where each run checks the previous completed window.
    """
    run_time = session_start + timedelta(minutes=interval_minutes)

    while run_time <= session_end:
        yield run_time
        run_time += timedelta(minutes=interval_minutes)
