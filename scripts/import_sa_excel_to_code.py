"""Generate app/data/sa_members.py from the project Excel SA list.

Usage:
    python scripts/import_sa_excel_to_code.py "app/data/SA List R5-R8-R1-R2_R4-R6-R7-R3.xlsx"

The generated module contains robust group-title matching and supports an
optional ``User ID`` column for members who do not have Telegram usernames.
"""
from __future__ import annotations

import pprint
import sys
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "data" / "sa_members.py"
DEFAULT_EXCEL = ROOT / "app" / "data" / "SA List R5-R8-R1-R2_R4-R6-R7-R3.xlsx"

REQUIRED = {
    "region": "Region",
    "dealer": "Dealer",
    "name": "SA Name",
    "position": "Position",
    "working_time": "working time",
    "username": "Username",
    "group_name": "Group",
}

OPTIONAL = {
    "alert_time": ("Alert time",),
    "dinner": ("dinner break", "dinner"),
    "finish_time": ("finish time",),
    "controller": ("Controller", "Controler"),
    "telegram_user_id": ("Telegram ID", "User ID"),
}


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def header_key(value) -> str:
    return clean(value).casefold()


def read_members(excel_path: Path) -> list[dict]:
    wb = load_workbook(excel_path, data_only=True, read_only=True)
    ws = wb.active
    headers = [header_key(cell.value) for cell in ws[1]]
    idx = {header: i for i, header in enumerate(headers)}

    missing = [column for column in REQUIRED.values() if header_key(column) not in idx]
    if missing:
        raise SystemExit(f"Missing required columns: {missing}")

    members: list[dict] = []
    seen_ids: dict[int, int] = {}
    for row_number, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(value not in (None, "") for value in row):
            continue

        item = {
            key: clean(row[idx[header_key(column)]])
            for key, column in REQUIRED.items()
        }
        for key, columns in OPTIONAL.items():
            column_index = next(
                (idx[header_key(column)] for column in columns if header_key(column) in idx),
                None,
            )
            item[key] = clean(row[column_index]) if column_index is not None else ""

        raw_user_id = item.get("telegram_user_id")
        if raw_user_id:
            try:
                item["telegram_user_id"] = int(float(raw_user_id))
            except (TypeError, ValueError):
                raise SystemExit(
                    f"Invalid User ID for {item.get('name')}: {raw_user_id!r}"
                )
        else:
            item["telegram_user_id"] = None

        user_id = item["telegram_user_id"]
        if not user_id or user_id <= 0:
            raise SystemExit(f"Row {row_number}: positive Telegram ID is required")
        if user_id in seen_ids:
            raise SystemExit(
                f"Row {row_number}: duplicate Telegram ID {user_id}; "
                f"first used at row {seen_ids[user_id]}"
            )
        seen_ids[user_id] = row_number
        if not item["group_name"]:
            raise SystemExit(f"Row {row_number}: Group is required")
        if not item["name"]:
            # Safe temporary display only. Telegram activity or
            # /export_SA_list replaces it using this exact numeric ID.
            item["name"] = f"Telegram User {user_id}"
        members.append(item)

    return members


MODULE_TEMPLATE = '''# app/data/sa_members.py
# Generated from the uploaded Excel list. Do not edit rows manually.
from __future__ import annotations

import re
import unicodedata

SA_MEMBERS = {members}


def normalize_username(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    return value.lstrip("@").lower()


def normalize_group(value: str | None) -> str:
    """Normalize harmless punctuation/spacing differences in Telegram group titles."""
    text = unicodedata.normalize("NFKC", value or "").upper()
    text = text.replace("\\u200b", "").replace("\\ufeff", "")
    return "".join(ch for ch in text if ch.isalnum())


def _group_signature(value: str | None) -> tuple[str, str, str] | None:
    """Return a safe fallback key: (region, category, dealer)."""
    text = unicodedata.normalize("NFKC", value or "").upper()
    region_match = re.search(r"(?<![A-Z0-9])R\\s*([1-9])(?![A-Z0-9])", text)
    if not region_match:
        region_match = re.search(r"R([1-9])", text)
    dealer_match = re.search(r"(?<![A-Z0-9])([A-Z]{{2,4}}\\d+)(?![A-Z0-9])", text)
    if not region_match or not dealer_match:
        return None

    compact = normalize_group(text)
    if "COMMANDO" in compact or "CAMMONDO" in compact:
        category = "COMMANDO"
    elif "SPECIALIST" in compact:
        category = "SPECIALIST"
    elif "SA" in compact:
        category = "SA"
    else:
        category = "OTHER"
    return (f"R{{region_match.group(1)}}", category, dealer_match.group(1))


_EXACT_GROUP_INDEX: dict[str, list[dict]] = {{}}
_SIGNATURE_GROUP_NAMES: dict[tuple[str, str, str], set[str]] = {{}}
for _member in SA_MEMBERS:
    _exact = normalize_group(_member.get("group_name"))
    _EXACT_GROUP_INDEX.setdefault(_exact, []).append(_member)
    _sig = _group_signature(_member.get("group_name"))
    if _sig:
        _SIGNATURE_GROUP_NAMES.setdefault(_sig, set()).add(_exact)


def members_for_group(group_name: str | None) -> list[dict]:
    exact = normalize_group(group_name)
    direct = _EXACT_GROUP_INDEX.get(exact)
    if direct:
        return list(direct)

    signature = _group_signature(group_name)
    if not signature:
        return []

    group_names = _SIGNATURE_GROUP_NAMES.get(signature, set())
    if len(group_names) != 1:
        return []
    only_group = next(iter(group_names))
    return list(_EXACT_GROUP_INDEX.get(only_group, []))
'''


def main() -> None:
    excel_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXCEL
    if not excel_path.exists():
        raise SystemExit(f"Excel file not found: {excel_path}")

    members = read_members(excel_path)
    rendered = MODULE_TEMPLATE.format(
        members=pprint.pformat(members, width=140, sort_dicts=False)
    )
    OUT.write_text(rendered, encoding="utf-8")
    print(f"Generated {OUT} with {len(members)} users.")


if __name__ == "__main__":
    main()
