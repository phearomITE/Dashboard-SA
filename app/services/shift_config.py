# app/services/shift_config.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.services.time_service import at_time

settings = get_settings()

# Alert titles requested by business flow.
REGULAR_TITLE = "➡️🔴 បងៗមិនទាន់បានទម្លាក់រូបភាពលក់"
FIRST_PHOTO_TITLE = "➡️🔴បងៗម៉េចបានអត់ទាន់ឃើញទម្លាក់រូបភាពលក់ដំបូងចឹងបងៗ🙏"
LAST_PHOTO_TITLE = "➡️🔴 បងៗមិនទាន់បានទម្លាក់រូបភាពលក់ចុងក្រោយ"


@dataclass(frozen=True)
class CheckWindow:
    code: str
    run_hour: int
    run_minute: int
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    title: str
    include_submitted: bool = False
    is_last: bool = False

    def run_at(self, day: datetime) -> datetime:
        return at_time(day, self.run_hour, self.run_minute)

    def interval(self, day: datetime) -> tuple[datetime, datetime]:
        return (
            at_time(day, self.start_hour, self.start_minute),
            at_time(day, self.end_hour, self.end_minute),
        )


@dataclass(frozen=True)
class ShiftFlow:
    key: str
    label: str
    windows: tuple[CheckWindow, ...]


def _window(code: str, start: str, end: str, title: str = REGULAR_TITLE, *, is_last: bool = False) -> CheckWindow:
    """Create one check window. The scheduler runs at the window end time."""
    sh, sm = [int(x) for x in start.split(":")]
    eh, em = [int(x) for x in end.split(":")]
    return CheckWindow(
        code=code,
        run_hour=eh,
        run_minute=em,
        start_hour=sh,
        start_minute=sm,
        end_hour=eh,
        end_minute=em,
        title=title,
        is_last=is_last,
    )


def _chain_windows(prefix: str, start: str, final_end: str, interval_minutes: int = 20) -> list[CheckWindow]:
    """Build exact repeated 20-minute windows from start until final_end."""
    sh, sm = [int(x) for x in start.split(":")]
    eh, em = [int(x) for x in final_end.split(":")]
    cur = datetime(2000, 1, 1, sh, sm)
    final = datetime(2000, 1, 1, eh, em)
    result: list[CheckWindow] = []
    idx = 1
    while cur + timedelta(minutes=interval_minutes) <= final:
        nxt = cur + timedelta(minutes=interval_minutes)
        result.append(_window(f"{prefix}_{idx:02d}", cur.strftime("%H:%M"), nxt.strftime("%H:%M")))
        cur = nxt
        idx += 1
    return result


def _sa_7_windows() -> tuple[CheckWindow, ...]:
    """07:00-17:00: first 07:50, lunch 11:50-13:00, last 16:20-16:40."""
    windows: list[CheckWindow] = [
        _window("sa_7_first_photo", "07:00", "07:50", FIRST_PHOTO_TITLE),
    ]
    windows.extend(_chain_windows("sa_7_morning", "07:50", "11:50"))
    windows.append(_window("sa_7_afternoon_start", "13:00", "13:20"))
    afternoon = _chain_windows("sa_7_afternoon", "13:20", "16:40")
    if afternoon:
        afternoon[-1] = _window("sa_7_last", "16:20", "16:40", LAST_PHOTO_TITLE, is_last=True)
    windows.extend(afternoon)
    return tuple(windows)


def _sa_730_windows() -> tuple[CheckWindow, ...]:
    """07:30-17:00 exception: first 08:20, lunch 11:40-13:00, last 16:20-16:40."""
    windows: list[CheckWindow] = [
        _window("sa_730_first_photo", "07:30", "08:20", FIRST_PHOTO_TITLE),
    ]
    windows.extend(_chain_windows("sa_730_morning", "08:20", "11:40"))
    windows.append(_window("sa_730_afternoon_start", "13:00", "13:20"))
    afternoon = _chain_windows("sa_730_afternoon", "13:20", "16:40")
    if afternoon:
        afternoon[-1] = _window("sa_730_last", "16:20", "16:40", LAST_PHOTO_TITLE, is_last=True)
    windows.extend(afternoon)
    return tuple(windows)


def _sa_630_windows() -> tuple[CheckWindow, ...]:
    """06:30-16:30: first 07:20, lunch 11:40-13:00, last 16:00-16:20."""
    windows: list[CheckWindow] = [
        _window("sa_630_first_photo", "06:30", "07:20", FIRST_PHOTO_TITLE),
    ]
    windows.extend(_chain_windows("sa_630_morning", "07:20", "11:40"))
    windows.append(_window("sa_630_afternoon_start", "13:00", "13:20"))
    afternoon = _chain_windows("sa_630_afternoon", "13:20", "16:20")
    if afternoon:
        afternoon[-1] = _window("sa_630_last", "16:00", "16:20", LAST_PHOTO_TITLE, is_last=True)
    windows.extend(afternoon)
    return tuple(windows)


def _commando_windows() -> tuple[CheckWindow, ...]:
    """09:00-20:00 exception with 20-minute checks and two breaks."""
    windows: list[CheckWindow] = [
        _window("commando_first_photo", "09:00", "09:30", FIRST_PHOTO_TITLE),
    ]
    windows.extend(_chain_windows("commando_morning", "09:30", "11:50"))
    windows.append(_window("commando_afternoon_start", "13:00", "13:20"))
    windows.extend(_chain_windows("commando_afternoon", "13:20", "17:20"))
    evening = _chain_windows("commando_evening", "17:50", "19:30")
    if evening:
        evening[-1] = _window("commando_last", "19:10", "19:30", LAST_PHOTO_TITLE, is_last=True)
    windows.extend(evening)

    return tuple(windows)


SA_7_FLOW = ShiftFlow("sa_7", "07:00AM-05:00PM", _sa_7_windows())
SA_FLOW = ShiftFlow("sa", "07:30AM-05:00PM", _sa_730_windows())
SA_630_FLOW = ShiftFlow("sa_630", "06:30AM-04:30PM", _sa_630_windows())
COMMANDO_FLOW = ShiftFlow("commando", "Commando", _commando_windows())
ALL_FLOWS = (SA_7_FLOW, SA_FLOW, SA_630_FLOW, COMMANDO_FLOW)


def normalize_group_name(group_name: str | None) -> str:
    return (group_name or "").lower().replace("_", " ").replace("-", " ")


def flow_for_group(group_name: str | None) -> ShiftFlow:
    name = normalize_group_name(group_name)
    for flow in ALL_FLOWS:
        if name in {normalize_group_name(flow.key), normalize_group_name(flow.label)}:
            return flow
    if "commando" in name or "cammondo" in name or "cammondo" in name or "cmd" in name:
        return COMMANDO_FLOW
    if "water" in name or "sa water" in name:
        return SA_630_FLOW
    return SA_7_FLOW


def all_unique_run_times(day: datetime) -> list[datetime]:
    seen: set[tuple[int, int]] = set()
    result: list[datetime] = []
    for flow in ALL_FLOWS:
        for window in flow.windows:
            key = (window.run_hour, window.run_minute)
            if key in seen:
                continue
            seen.add(key)
            result.append(window.run_at(day))
    return sorted(result)


def windows_for_run_time(flow: ShiftFlow, hour: int, minute: int) -> list[CheckWindow]:
    return [w for w in flow.windows if w.run_hour == hour and w.run_minute == minute]


def previous_window_for_now(day: datetime, group_name: str | None) -> CheckWindow | None:
    flow = flow_for_group(group_name)
    candidates = [w for w in flow.windows if w.run_at(day) <= day]
    if not candidates:
        return None
    candidates.sort(key=lambda w: w.run_at(day))
    return candidates[-1]


def _compact_time(value: str | None) -> str:
    text = (value or "").strip().lower()
    return (
        text.replace(" ", "")
        .replace(".", ":")
        .replace("–", "-")
        .replace("—", "-")
    )


def flow_key_for_position(position: str | None, working_time: str | None = None, alert_time: str | None = None) -> str:
    """Choose the schedule from each Excel row, not from the position label.

    The uploaded list intentionally contains people whose historical display
    name includes a former role, but schedules are selected independently from
    the three approved positions. Explicit Alert time is the source
    of truth, followed by working time, then position as the last fallback.
    """
    value = (position or "").strip().lower()
    wt = _compact_time(working_time)
    at = _compact_time(alert_time)

    # Working time is the operational source of truth. This ensures controller
    # changes and SA_List edits are not overridden by an old Alert time value.
    if "9:00" in wt or "20:00" in wt or "8:00pm" in wt:
        return COMMANDO_FLOW.key
    if "6:30" in wt or "16:30" in wt or "4:30pm" in wt:
        return SA_630_FLOW.key
    if "7:30" in wt:
        return SA_FLOW.key
    if "7:00" in wt or "17:00" in wt or "5:00pm" in wt:
        return SA_7_FLOW.key

    if at in {"9:30am", "09:30am", "9:30", "09:30"}:
        return COMMANDO_FLOW.key
    if at in {"7:20am", "07:20am", "7:20", "07:20"}:
        return SA_630_FLOW.key
    if at in {"8:20am", "08:20am", "8:20", "08:20"}:
        return SA_FLOW.key

    if "commando" in value or "cammondo" in value:
        return COMMANDO_FLOW.key
    if "water" in value:
        return SA_630_FLOW.key
    return SA_7_FLOW.key


def flow_by_key(flow_key: str | None) -> ShiftFlow:
    key = (flow_key or "").strip().lower()
    # Read old database rows safely during startup; roster migration rewrites
    # this legacy key to sa_630 before scheduled alerts begin.
    if key == "sa_water":
        return SA_630_FLOW
    for flow in ALL_FLOWS:
        if flow.key == key:
            return flow
    return SA_7_FLOW
