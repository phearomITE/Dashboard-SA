"""Exact regression for the approved 2026-08-05 working-time schedules."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:offline-validation")

from app.core.config import get_settings
from app.services.shift_config import flow_by_key


def intervals(key: str) -> list[tuple[str, str]]:
    return [
        (f"{w.start_hour:02d}:{w.start_minute:02d}", f"{w.end_hour:02d}:{w.end_minute:02d}")
        for w in flow_by_key(key).windows
    ]


def chain(start_hour: int, start_minute: int, count: int) -> list[tuple[str, str]]:
    total = start_hour * 60 + start_minute
    result = []
    for _ in range(count):
        end = total + 20
        result.append((f"{total // 60:02d}:{total % 60:02d}", f"{end // 60:02d}:{end % 60:02d}"))
        total = end
    return result


def main() -> None:
    assert get_settings().ALERT_INTERVAL_MINUTES == 20

    expected_sa_7 = (
        [("07:00", "07:50")]
        + chain(7, 50, 12)
        + [("13:00", "13:20")]
        + chain(13, 20, 10)
    )
    expected_water = (
        [("06:30", "07:20")]
        + chain(7, 20, 13)
        + [("13:00", "13:20")]
        + chain(13, 20, 8)
    )
    expected_sa_730 = (
        [("07:30", "08:10")]
        + chain(8, 10, 11)
        + [("13:00", "13:20")]
        + chain(13, 20, 10)
    )
    expected_commando = (
        [("09:00", "09:50")]
        + chain(9, 50, 6)
        + [("13:00", "13:20")]
        + chain(13, 20, 12)
        + chain(17, 50, 5)
    )

    assert intervals("sa_7") == expected_sa_7
    assert intervals("sa_630") == expected_water
    assert intervals("sa") == expected_sa_730
    assert intervals("commando") == expected_commando
    print(
        "20-MINUTE SCHEDULE VALIDATION OK: "
        "07:00, 06:30, 07:30 exception, and 09:00 Commando flows match exactly."
    )


if __name__ == "__main__":
    main()
