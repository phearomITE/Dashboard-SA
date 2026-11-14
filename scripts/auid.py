from __future__ import annotations

from collections import defaultdict
import unicodedata

from app.data.sa_members import SA_MEMBERS, normalize_username


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(ch for ch in text if ch.isalnum())


def main() -> None:
    print(f"Official users: {len(SA_MEMBERS)}")
    print(f"Official groups: {len({m['group_name'] for m in SA_MEMBERS})}")
    problems = 0

    for label, key_fn in {
        "username": lambda m: normalize_username(m.get("username")) or "",
        "name": lambda m: normalize_name(m.get("name")),
    }.items():
        owners: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for member in SA_MEMBERS:
            key = key_fn(member)
            if key:
                owners[(member["group_name"], key)].append(member)
        duplicates = [(key, rows) for key, rows in owners.items() if len(rows) > 1]
        print(f"Duplicate {label}s inside same group: {len(duplicates)}")
        for (group_name, key), rows in duplicates:
            problems += 1
            print(f"  {group_name} | {key} | {[r.get('name') for r in rows]}")

    no_username = [m for m in SA_MEMBERS if not normalize_username(m.get("username"))]
    print(f"Users without Telegram username: {len(no_username)}")
    if problems:
        raise SystemExit("ROSTER IDENTITY AUDIT FAILED")
    print("ROSTER IDENTITY AUDIT OK")


if __name__ == "__main__":
    main()
