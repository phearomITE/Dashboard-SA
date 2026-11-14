"""Exercise the real Telegram handlers for the 07/08/2026 CA2 evidence."""
from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:offline-validation")

# Handler import normally loads optional GPU/audio packages. They are unrelated
# to this database-only validation and need not be installed on a test machine.
voice_module = types.ModuleType("app.services.local_voice_transcriber")
voice_module.get_local_voice_transcriber = lambda: None
sys.modules[voice_module.__name__] = voice_module

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.bot.handlers as handlers
import app.services.user_service as user_service
from app.database.database import Base
from app.database.models import PinHistory, SalePhotoReport, SAUser


GROUP_ID = -1_001_000_000_002
GROUP_NAME = "R1-SA-CA2"
WINDOW_START = datetime(2026, 8, 7, 7, 50)
WINDOW_END = datetime(2026, 8, 7, 8, 10)


def update_for(user_id: int, name: str, username: str | None, message):
    first, *rest = name.split(" ", 1)
    user = SimpleNamespace(
        id=user_id,
        first_name=first,
        last_name=rest[0] if rest else None,
        username=username,
    )
    message.from_user = user
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(
            id=GROUP_ID,
            title=GROUP_NAME,
            type="supergroup",
        ),
        effective_message=message,
    )


async def submit_form_and_reply_gps(
    clock: list[datetime], *, user_id: int, name: str, username: str | None,
    form_time: datetime, gps_time: datetime, message_id: int,
) -> None:
    text = "ID:90025288 Dealer:CA2 Outlet Name:test\nWURKZ CAN 250ML: 1"
    form = SimpleNamespace(
        text=text,
        caption=None,
        message_id=message_id,
        reply_to_message=None,
        photo=[],
        location=None,
    )
    clock[0] = form_time
    await handlers.receive_sale_text(
        update_for(user_id, name, username, form), None
    )
    location = SimpleNamespace(latitude=11.55, longitude=104.91)
    gps = SimpleNamespace(
        text=None,
        caption=None,
        message_id=message_id + 1,
        reply_to_message=form,
        photo=[],
        location=location,
    )
    clock[0] = gps_time
    await handlers.receive_location(
        update_for(user_id, name, username, gps), None
    )


def main() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    handlers.SessionLocal = session
    clock = [WINDOW_START]
    user_service.local_now = lambda: clock[0]

    db = session()
    user_service.seed_sa_list(db)
    user_service.sync_group_members_from_list(db, GROUP_ID, GROUP_NAME)
    official_ids = [
        row.telegram_user_id
        for row in db.query(SAUser).filter(SAUser.group_id == GROUP_ID).order_by(SAUser.id).limit(2)
    ]
    db.close()

    asyncio.run(submit_form_and_reply_gps(
        clock,
        user_id=official_ids[0],
        name="Chorn Chiva SA CA2",
        username=None,
        form_time=datetime(2026, 8, 7, 8, 2),
        gps_time=datetime(2026, 8, 7, 8, 3),
        message_id=1020,
    ))
    asyncio.run(submit_form_and_reply_gps(
        clock,
        user_id=official_ids[1],
        name="SOT TARA ✨sales commando R1 CA2",
        username="raahakk",
        form_time=datetime(2026, 8, 7, 8, 10, 30),
        gps_time=datetime(2026, 8, 7, 8, 10, 59),
        message_id=1030,
    ))

    db = session()
    # Reproduce the scheduler's final pre-alert roster synchronization.
    user_service.sync_group_members_from_list(db, GROUP_ID, GROUP_NAME)
    missing = user_service.get_missing_sale_photo_users_for_interval(
        db, GROUP_ID, WINDOW_START, WINDOW_END, flow_key="sa_7"
    )
    missing_names = {row.official_name or row.name for row in missing}
    assert "Chorn Chiva SA CA2" not in missing_names, missing_names
    assert "SOT TARA ✨sales commando R1 CA2" not in missing_names, missing_names
    reports = db.query(SalePhotoReport).all()
    assert len(reports) == 2 and all(r.reply_lat is not None and r.reply_lon is not None for r in reports)
    assert db.query(PinHistory).count() == 2
    assert db.query(SAUser).filter(
        SAUser.group_id == GROUP_ID,
        SAUser.telegram_user_id.in_(official_ids),
        SAUser.is_from_list == 1,
    ).count() == 2
    db.close()
    print(
        "LIVE HANDLER PIPELINE OK: Chorn 08:02+08:03 and SOT "
        "08:10:30+08:10:59 are not falsely mentioned after scheduler sync."
    )


if __name__ == "__main__":
    main()
