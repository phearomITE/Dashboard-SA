"""Regression for the R6-SA-BTY8 false mention at 13:43-14:06."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:offline-validation")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.database.models import PinHistory, SalePhotoReport, SAList, SAUser
from app.services.user_service import (
    get_missing_sale_photo_users_for_interval,
    seed_sa_list,
    sync_group_members_from_list,
)
from app.services.roster_migration import migrate_legacy_roster_positions


def main() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    group_id = -100600000008
    group_name = "R6-SA-BTY8"
    sync_group_members_from_list(db, group_id, group_name)
    primg = db.query(SAUser).filter_by(group_id=group_id).first()
    start = datetime(2026, 8, 3, 13, 43)
    end = datetime(2026, 8, 3, 14, 6)

    # Reproduce a stale canonical pin identity while retaining the same stable
    # Telegram sender. The linked report at 14:02 must still prevent mention.
    real_sender_id = 600019525
    primg.telegram_user_id = real_sender_id
    db.add(SalePhotoReport(
        sa_user_id=primg.id,
        telegram_user_id=real_sender_id,
        group_id=group_id,
        group_name=group_name,
        name=primg.name,
        username=None,
        caption="ID:90019525 Dealer:BTY8 Outlet Name:test",
        reply_lat=12.5,
        reply_lon=104.5,
        reply_map_link="https://maps.google.com/?q=12.5,104.5",
        telegram_message_id=1401,
        replied_message_id=1402,
        submitted_at=datetime(2026, 8, 3, 14, 2),
        window_start=start,
        window_end=end,
    ))
    db.add(PinHistory(
        sa_user_id=None,
        # Simulate a historic stale identity on the separate pin row. The
        # atomic linked report remains authoritative.
        telegram_user_id=-999999,
        group_id=group_id,
        group_name=group_name,
        name=primg.name,
        username=None,
        lat=12.5,
        lon=104.5,
        map_link="https://maps.google.com/?q=12.5,104.5",
        pinned_at=datetime(2026, 8, 3, 14, 2),
        window_start=start,
        window_end=end,
    ))
    db.commit()

    missing = get_missing_sale_photo_users_for_interval(
        db, group_id, start, end, flow_key=primg.flow_key
    )
    assert primg.id not in {user.id for user in missing}, (
        "Primg Sokim was falsely mentioned despite same-sender photo + replied GPS at 14:02"
    )

    # Reproduce the 06/08/2026 R4-SA-BTB1 production case. PostgreSQL still
    # contained legacy SA Water rows even though the generated roster had been
    # changed. Startup seeding must migrate both SA_List and SAUser, then a GPS
    # reply to Keo's own text form at 13:58 must be accepted atomically.
    r4_group_id = -100400000001
    r4_group_name = "R4-SA-BTB1"
    sync_group_members_from_list(db, r4_group_id, r4_group_name)
    keo = db.query(SAUser).filter_by(group_id=r4_group_id).first()
    keo.position = "SA Water"
    keo.working_time = "6:30am-4:30pm"
    keo.flow_key = "sa_water"
    list_row = db.query(SAList).filter_by(
        group_name=r4_group_name,
        telegram_user_id=keo.telegram_user_id,
    ).one()
    list_row.position = "SA Water"
    list_row.working_time = "6:30am-4:30pm"
    db.commit()

    seed_sa_list(db)
    migrate_legacy_roster_positions(db)
    sync_group_members_from_list(db, r4_group_id, r4_group_name)
    keo = db.query(SAUser).filter_by(group_id=r4_group_id).first()
    assert keo.position == "SA", keo.position
    assert keo.working_time == "7:00am-5:00pm", keo.working_time
    assert keo.flow_key == "sa_7", keo.flow_key

    r4_start = datetime(2026, 8, 6, 13, 40)
    r4_end = datetime(2026, 8, 6, 14, 0)
    keo.telegram_user_id = 90026781
    db.add(SalePhotoReport(
        sa_user_id=keo.id,
        telegram_user_id=90026781,
        group_id=r4_group_id,
        group_name=r4_group_name,
        name=keo.name,
        username="anusey_keo",
        caption="ID:90026781 Dealer:BTB1 Outlet Name:test",
        reply_lat=13.1,
        reply_lon=103.2,
        telegram_message_id=1357,
        replied_message_id=1358,
        submitted_at=datetime(2026, 8, 6, 13, 58),
        window_start=r4_start,
        window_end=r4_end,
    ))
    db.commit()
    r4_missing = get_missing_sale_photo_users_for_interval(
        db, r4_group_id, r4_start, r4_end, flow_key="sa_7"
    )
    assert keo.id not in {user.id for user in r4_missing}, (
        "Keo Anusey was falsely mentioned despite replying to his own form with GPS at 13:58"
    )
    db.close()
    print(
        "ATOMIC PHOTO+GPS REGRESSION OK: Primg and Keo are excluded after valid "
        "same-sender form/photo + GPS submissions; legacy SA Water rows migrate to SA."
    )


if __name__ == "__main__":
    main()
