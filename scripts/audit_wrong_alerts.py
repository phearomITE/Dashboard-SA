"""Audit false missing-alert causes for one group and time window.

Usage:
  python -m scripts.audit_wrong_alerts "R5-SA-BMC2" "2026-07-29 14:06" "2026-07-29 14:29"
"""
from __future__ import annotations

import sys
from datetime import datetime

from sqlalchemy import func

from app.database.database import SessionLocal
from app.database.models import PinHistory, SAUser, SalePhotoReport, TelegramGroup
from app.services.user_service import get_missing_sale_photo_users_for_interval


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit('Usage: python -m scripts.audit_wrong_alerts "GROUP" "YYYY-MM-DD HH:MM" "YYYY-MM-DD HH:MM"')
    group_name, start_raw, end_raw = sys.argv[1:]
    start = datetime.strptime(start_raw, "%Y-%m-%d %H:%M")
    end = datetime.strptime(end_raw, "%Y-%m-%d %H:%M")

    db = SessionLocal()
    try:
        group = db.query(TelegramGroup).filter(func.lower(TelegramGroup.group_name) == group_name.lower()).first()
        if group is None:
            raise SystemExit(f"Group not found: {group_name}")

        users = db.query(SAUser).filter(SAUser.group_id == group.group_id, SAUser.is_from_list == 1).all()
        missing_ids = {u.id for u in get_missing_sale_photo_users_for_interval(db, group.group_id, start, end)}
        print(f"GROUP: {group.group_name} ({group.group_id})")
        print(f"WINDOW: {start} -> {end}")
        for user in users:
            forms = db.query(SalePhotoReport).filter(
                SalePhotoReport.group_id == group.group_id,
                SalePhotoReport.submitted_at >= start,
                SalePhotoReport.submitted_at <= end.replace(second=59),
            ).filter(
                (SalePhotoReport.telegram_user_id == user.telegram_user_id)
                | (func.lower(func.replace(func.coalesce(SalePhotoReport.username, ''), '@', '')) == (user.username or '').lstrip('@').lower())
                | (func.lower(func.trim(SalePhotoReport.name)) == (user.name or '').strip().lower())
            ).all()
            pins = db.query(PinHistory).filter(
                PinHistory.group_id == group.group_id,
                PinHistory.pinned_at >= start,
                PinHistory.pinned_at <= end.replace(second=59),
            ).filter(
                (PinHistory.telegram_user_id == user.telegram_user_id)
                | (func.lower(func.replace(func.coalesce(PinHistory.username, ''), '@', '')) == (user.username or '').lstrip('@').lower())
                | (func.lower(func.trim(PinHistory.name)) == (user.name or '').strip().lower())
            ).all()
            status = "MISSING" if user.id in missing_ids else "OK"
            print(f"{status:7} | {user.name} | @{user.username or '-'} | id={user.telegram_user_id} | forms={len(forms)} pins={len(pins)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
