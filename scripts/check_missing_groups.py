# scripts/check_missing_groups.py
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

# Make the project root importable when this file is run as:
# python scripts\check_missing_groups.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.data.sa_members import SA_MEMBERS, normalize_group
from app.database.database import SessionLocal
from app.database.models import TelegramGroup


def main() -> None:
    official_by_key: dict[str, str] = {}
    official_user_counts: Counter[str] = Counter()

    for member in SA_MEMBERS:
        group_name = str(member.get("group_name") or "").strip()
        key = normalize_group(group_name)
        if not key:
            continue
        official_by_key.setdefault(key, group_name)
        official_user_counts[key] += 1

    with SessionLocal() as db:
        rows = db.execute(
            select(
                TelegramGroup.group_id,
                TelegramGroup.group_name,
                TelegramGroup.is_active,
            )
        ).all()

    registered_by_key: dict[str, list[tuple[int, str, int]]] = defaultdict(list)

    for group_id, group_name, is_active in rows:
        display_name = str(group_name or "").strip()
        key = normalize_group(display_name)
        if not key:
            continue

        registered_by_key[key].append(
            (int(group_id), display_name, int(is_active or 0))
        )

    official_keys = set(official_by_key)

    active_registered_keys = {
        key
        for key, entries in registered_by_key.items()
        if any(is_active == 1 for _, _, is_active in entries)
    }

    missing_keys = sorted(
        official_keys - active_registered_keys,
        key=lambda key: official_by_key[key],
    )

    extra_keys = sorted(
        active_registered_keys - official_keys,
        key=lambda key: registered_by_key[key][0][1],
    )

    inactive_official_keys = sorted(
        key
        for key in official_keys & set(registered_by_key)
        if not any(is_active == 1 for _, _, is_active in registered_by_key[key])
    )

    duplicate_keys = sorted(
        key for key, entries in registered_by_key.items() if len(entries) > 1
    )

    print("=" * 72)
    print("TELEGRAM GROUP REGISTRATION CHECK")
    print("=" * 72)
    print(f"Official groups in SA list : {len(official_keys)}")
    print(f"Rows in telegram_groups    : {len(rows)}")
    print(f"Active matched groups      : {len(official_keys & active_registered_keys)}")
    print(f"Missing active groups      : {len(missing_keys)}")
    print(f"Extra/unmatched DB groups  : {len(extra_keys)}")
    print(f"Inactive official groups   : {len(inactive_official_keys)}")
    print(f"Duplicate normalized groups: {len(duplicate_keys)}")

    print("\nMISSING GROUPS — run /setup_group then /sync_sa_list:")
    if not missing_keys:
        print("  None. All official groups are registered and active.")
    else:
        for index, key in enumerate(missing_keys, start=1):
            print(
                f"  {index:>2}. {official_by_key[key]}"
                f" | expected users: {official_user_counts[key]}"
            )

    print("\nEXTRA OR NAME-MISMATCHED GROUPS IN DATABASE:")
    if not extra_keys:
        print("  None.")
    else:
        for index, key in enumerate(extra_keys, start=1):
            details = "; ".join(
                f"{name} (ID={group_id}, active={is_active})"
                for group_id, name, is_active in registered_by_key[key]
            )
            print(f"  {index:>2}. {details}")

    print("\nINACTIVE OFFICIAL GROUPS:")
    if not inactive_official_keys:
        print("  None.")
    else:
        for index, key in enumerate(inactive_official_keys, start=1):
            details = "; ".join(
                f"{name} (ID={group_id}, active={is_active})"
                for group_id, name, is_active in registered_by_key[key]
            )
            print(f"  {index:>2}. {details}")

    print("\nDUPLICATE GROUP RECORDS AFTER NAME NORMALIZATION:")
    if not duplicate_keys:
        print("  None.")
    else:
        for index, key in enumerate(duplicate_keys, start=1):
            details = "; ".join(
                f"{name} (ID={group_id}, active={is_active})"
                for group_id, name, is_active in registered_by_key[key]
            )
            print(f"  {index:>2}. {details}")

    print("\nDone.")


if __name__ == "__main__":
    main()