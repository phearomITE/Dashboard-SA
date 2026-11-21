from __future__ import annotations

from datetime import date, datetime

# Official fixed public holidays in Cambodia (Royal Government Sub-Decree / Khmer Calendar).
FIXED_CAMBODIA_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "International New Year's Day",
    (1, 7): "Victory Day over Genocide",
    (3, 8): "International Women's Day",
    (4, 14): "Khmer New Year (Day 1)",
    (4, 15): "Khmer New Year (Day 2)",
    (4, 16): "Khmer New Year (Day 3)",
    (5, 1): "International Labour Day",
    (5, 14): "King Norodom Sihamoni's Birthday",
    (6, 18): "Queen Mother's Birthday",
    (9, 24): "Constitution Day",
    (10, 15): "Commemoration Day of King Father",
    (10, 29): "King's Coronation Day",
    (11, 9): "National Independence Day",
    (12, 29): "Peace Day in Cambodia",
}

# Official Khmer lunar public holidays in Cambodia (Visak Bochea, Royal Ploughing,
# Pchum Ben, and Water Festival / Bon Om Touk) following khmer-lunar-calendar.com
# and Royal Government Sub-Decrees.
LUNAR_CAMBODIA_HOLIDAYS: dict[date, str] = {
    # 2025
    date(2025, 5, 11): "Visak Bochea Day",
    date(2025, 5, 15): "Royal Ploughing Ceremony",
    date(2025, 9, 21): "Pchum Ben Festival (Day 1)",
    date(2025, 9, 22): "Pchum Ben Festival (Day 2)",
    date(2025, 9, 23): "Pchum Ben Festival (Day 3)",
    date(2025, 11, 4): "Water Festival / Bon Om Touk (Day 1)",
    date(2025, 11, 5): "Water Festival / Bon Om Touk (Day 2)",
    date(2025, 11, 6): "Water Festival / Bon Om Touk (Day 3)",
    # 2026
    date(2026, 5, 1): "Visak Bochea Day / International Labour Day",
    date(2026, 5, 5): "Royal Ploughing Ceremony",
    date(2026, 10, 10): "Pchum Ben Festival (Day 1)",
    date(2026, 10, 11): "Pchum Ben Festival (Day 2)",
    date(2026, 10, 12): "Pchum Ben Festival (Day 3)",
    date(2026, 11, 23): "Water Festival / Bon Om Touk (Day 1)",
    date(2026, 11, 24): "Water Festival / Bon Om Touk (Day 2)",
    date(2026, 11, 25): "Water Festival / Bon Om Touk (Day 3)",
    # 2027
    date(2027, 5, 20): "Visak Bochea Day",
    date(2027, 5, 24): "Royal Ploughing Ceremony",
    date(2027, 9, 29): "Pchum Ben Festival (Day 1)",
    date(2027, 9, 30): "Pchum Ben Festival (Day 2)",
    date(2027, 10, 1): "Pchum Ben Festival (Day 3)",
    date(2027, 11, 12): "Water Festival / Bon Om Touk (Day 1)",
    date(2027, 11, 13): "Water Festival / Bon Om Touk (Day 2)",
    date(2027, 11, 14): "Water Festival / Bon Om Touk (Day 3)",
    # 2028
    date(2028, 5, 8): "Visak Bochea Day",
    date(2028, 5, 12): "Royal Ploughing Ceremony",
    date(2028, 9, 17): "Pchum Ben Festival (Day 1)",
    date(2028, 9, 18): "Pchum Ben Festival (Day 2)",
    date(2028, 9, 19): "Pchum Ben Festival (Day 3)",
    date(2028, 10, 31): "Water Festival / Bon Om Touk (Day 1)",
    date(2028, 11, 1): "Water Festival / Bon Om Touk (Day 2)",
    date(2028, 11, 2): "Water Festival / Bon Om Touk (Day 3)",
}


def _parse_extra_holidays(raw: str | None) -> set[date]:
    dates: set[date] = set()
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            dates.add(date.fromisoformat(token))
        except ValueError:
            continue
    return dates


def cambodia_holiday_name(target: date | datetime, *, extra_holidays_csv: str = "") -> str | None:
    """Return the Cambodia public holiday name if target falls on a holiday, else None."""
    day = target.date() if isinstance(target, datetime) else target
    if day in LUNAR_CAMBODIA_HOLIDAYS:
        return LUNAR_CAMBODIA_HOLIDAYS[day]
    fixed = FIXED_CAMBODIA_HOLIDAYS.get((day.month, day.day))
    if fixed:
        return fixed
    if extra_holidays_csv and day in _parse_extra_holidays(extra_holidays_csv):
        return "Custom Public Holiday"
    return None


def is_cambodia_holiday(target: date | datetime, *, extra_holidays_csv: str = "") -> bool:
    """Return True if target falls on an official Cambodia public holiday."""
    return cambodia_holiday_name(target, extra_holidays_csv=extra_holidays_csv) is not None


def upcoming_cambodia_holidays(
    from_day: date | datetime,
    limit: int = 8,
    *,
    extra_holidays_csv: str = "",
) -> list[dict]:
    """Return the next upcoming Cambodia public holidays from from_day onward."""
    start = from_day.date() if isinstance(from_day, datetime) else from_day
    extra = _parse_extra_holidays(extra_holidays_csv)
    items: list[tuple[date, str]] = []
    for year in (start.year, start.year + 1):
        for (month, day_num), name in FIXED_CAMBODIA_HOLIDAYS.items():
            d = date(year, month, day_num)
            if d >= start and d not in LUNAR_CAMBODIA_HOLIDAYS:
                items.append((d, name))
    for d, name in LUNAR_CAMBODIA_HOLIDAYS.items():
        if d >= start:
            items.append((d, name))
    for d in extra:
        if d >= start:
            items.append((d, "Custom Public Holiday"))
    seen: dict[date, str] = {}
    for d, name in sorted(items, key=lambda pair: pair[0]):
        seen.setdefault(d, name)
    return [
        {"date": d.isoformat(), "weekday": d.strftime("%A"), "name": name}
        for d, name in list(seen.items())[:limit]
    ]
