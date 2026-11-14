"""Exhaustive code-level regression for false missing mentions.

Validates every official user in every configured group against every alert
window in that user's assigned flow.  The suite is intentionally database-only
and never connects to Telegram or production PostgreSQL.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:offline-validation")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.sa_members import SA_MEMBERS
from app.database.database import Base
from app.database.models import PinHistory, SalePhotoReport, SAUser
from app.services.shift_config import ALL_FLOWS
from app.services.user_service import get_missing_sale_photo_users_for_interval, sync_group_members_from_list


DAY = datetime(2026, 8, 3)


def activity_time(start: datetime, end: datetime) -> datetime:
    return start + (end - start) / 2


def report(
    db, user: SAUser, at: datetime, start: datetime, end: datetime, *,
    linked_gps: bool, canonical: bool = True,
) -> None:
    db.add(SalePhotoReport(
        sa_user_id=user.id if canonical else None,
        telegram_user_id=user.telegram_user_id,
        group_id=user.group_id,
        group_name=user.group_name,
        name=user.name,
        username=user.username,
        caption="ID:90000001 Dealer:TEST Outlet Name:Regression",
        reply_lat=12.5 if linked_gps else None,
        reply_lon=104.5 if linked_gps else None,
        reply_map_link="https://maps.google.com/?q=12.5,104.5" if linked_gps else None,
        submitted_at=at,
        window_start=start,
        window_end=end,
    ))


def pin(
    db, user: SAUser, at: datetime, start: datetime, end: datetime, *,
    canonical: bool = True,
) -> None:
    db.add(PinHistory(
        sa_user_id=user.id if canonical else None,
        telegram_user_id=user.telegram_user_id,
        group_id=user.group_id,
        group_name=user.group_name,
        name=user.name,
        username=user.username,
        lat=12.5,
        lon=104.5,
        map_link="https://maps.google.com/?q=12.5,104.5",
        pinned_at=at,
        window_start=start,
        window_end=end,
    ))


def new_database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_all_groups(db) -> dict[str, int]:
    group_names = sorted({row["group_name"] for row in SA_MEMBERS})
    ids = {name: -(900_000_000_000 + index) for index, name in enumerate(group_names, 1)}
    for name, group_id in ids.items():
        sync_group_members_from_list(db, group_id, name)
    return ids


def validate_separate_form_and_pin() -> tuple[int, int, int]:
    db = new_database()
    group_ids = seed_all_groups(db)
    checked_windows = 0
    checked_users = 0

    for group_name, group_id in group_ids.items():
        for flow in ALL_FLOWS:
            users = db.query(SAUser).filter_by(group_id=group_id, is_from_list=1, flow_key=flow.key).all()
            if not users:
                continue
            checked_users += len(users)
            for window in flow.windows:
                start, end = window.interval(DAY)
                for user in users:
                    # Exercise both inclusive boundaries. The SQL contract
                    # accepts the exact start and the final second of the
                    # displayed end minute; every instant between them follows
                    # from the same closed-range predicate.
                    report(db, user, start, start, end, linked_gps=False)
                    pin(db, user, end + timedelta(seconds=59), start, end)
                db.commit()
                missing = get_missing_sale_photo_users_for_interval(db, group_id, start, end, flow_key=flow.key)
                assert not missing, (
                    f"False mention with separate form+GPS: group={group_name} flow={flow.key} "
                    f"window={start:%H:%M}-{end:%H:%M} users={[u.name for u in missing]}"
                )
                checked_windows += 1
    db.close()
    return len(group_ids), checked_users, checked_windows


def validate_profile_change_between_form_and_pin() -> int:
    """Stable Telegram ID must survive name/username changes mid-window."""
    db = new_database()
    group_ids = seed_all_groups(db)
    checked = 0
    for group_name, group_id in group_ids.items():
        users = db.query(SAUser).filter_by(group_id=group_id, is_from_list=1).all()
        for user in users:
            user.telegram_user_id = 8_000_000_000 + int(user.id)
            db.commit()
            flow = next(item for item in ALL_FLOWS if item.key == user.flow_key)
            start, end = flow.windows[len(flow.windows) // 2].interval(DAY)
            at = activity_time(start, end)

            # Simulate legacy activity rows with no canonical SA row ID.
            report(db, user, at, start, end, linked_gps=False, canonical=False)
            user.name = f"Renamed Telegram Profile {user.id}"
            user.username = f"renamed_profile_{user.id}"
            db.commit()
            pin(db, user, at, start, end, canonical=False)
            db.commit()

            missing = get_missing_sale_photo_users_for_interval(
                db, group_id, start, end, flow_key=flow.key
            )
            assert user.id not in {item.id for item in missing}, (
                f"False mention after Telegram profile change: "
                f"group={group_name} user_id={user.telegram_user_id}"
            )
            checked += 1
    db.close()
    return checked


def validate_atomic_linked_reply() -> int:
    db = new_database()
    group_ids = seed_all_groups(db)
    checked = 0
    for group_name, group_id in group_ids.items():
        users = db.query(SAUser).filter_by(group_id=group_id, is_from_list=1).all()
        for user in users:
            # Exercise stable Telegram-ID recovery rather than relying on the
            # canonical row ID. This represents a legacy/stale canonical link.
            user.telegram_user_id = 7_000_000_000 + int(user.id)
            db.commit()
            flow = next(item for item in ALL_FLOWS if item.key == user.flow_key)
            window = flow.windows[len(flow.windows) // 2]
            start, end = window.interval(DAY)
            report(
                db, user, activity_time(start, end), start, end,
                linked_gps=True, canonical=False,
            )
            db.commit()
            missing = get_missing_sale_photo_users_for_interval(db, group_id, start, end, flow_key=flow.key)
            assert user.id not in {item.id for item in missing}, (
                f"False mention with linked photo+GPS: group={group_name} user={user.name}"
            )
            checked += 1
    db.close()
    return checked


def validate_cross_user_never_combines() -> int:
    db = new_database()
    group_ids = seed_all_groups(db)
    checked = 0
    for group_name, group_id in group_ids.items():
        for flow in ALL_FLOWS:
            users = db.query(SAUser).filter_by(group_id=group_id, is_from_list=1, flow_key=flow.key).all()
            if len(users) < 2:
                continue
            owner, different_user = users[:2]
            start, end = flow.windows[len(flow.windows) // 2].interval(DAY)
            at = activity_time(start, end)
            report(db, owner, at, start, end, linked_gps=False)
            pin(db, different_user, at, start, end)
            db.commit()
            missing = get_missing_sale_photo_users_for_interval(db, group_id, start, end, flow_key=flow.key)
            missing_ids = {item.id for item in missing}
            assert owner.id in missing_ids and different_user.id in missing_ids, (
                f"Cross-user activity was combined: group={group_name} flow={flow.key}"
            )
            checked += 1
    db.close()
    return checked


def main() -> None:
    groups, user_flow_checks, windows = validate_separate_form_and_pin()
    linked_users = validate_atomic_linked_reply()
    renamed_profile_users = validate_profile_change_between_form_and_pin()
    cross_user_checks = validate_cross_user_never_combines()
    official_users = len(SA_MEMBERS)
    assert groups == 65, groups
    assert official_users == 231, official_users
    assert linked_users == official_users, (linked_users, official_users)
    assert renamed_profile_users == official_users, (renamed_profile_users, official_users)
    print(
        "ALL-GROUP WRONG-MENTION REGRESSION OK: "
        f"groups={groups}, official_users={official_users}, "
        f"user_flow_checks={user_flow_checks}, windows={windows}, "
        f"linked_reply_users={linked_users}, renamed_profile_users={renamed_profile_users}, "
        f"cross_user_checks={cross_user_checks}."
    )


if __name__ == "__main__":
    main()
