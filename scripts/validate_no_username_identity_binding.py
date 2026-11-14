"""Regression coverage for roster/display-name variants without usernames."""
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

voice_module = types.ModuleType("app.services.local_voice_transcriber")
voice_module.get_local_voice_transcriber = lambda: None
sys.modules[voice_module.__name__] = voice_module

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.bot.handlers as handlers
import app.services.user_service as service
from app.data.sa_members import SA_MEMBERS
from app.database.database import Base
from app.database.models import SAUser


def _update(user_id, name, message):
    first, *rest = name.split(" ", 1)
    user = SimpleNamespace(
        id=user_id, first_name=first, last_name=rest[0] if rest else None,
        username=None, is_bot=False,
    )
    message.from_user = user
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=-1002005005, title="R2-SA-KSV5", type="supergroup"),
        effective_message=message,
    )


async def _submit(clock, telegram_id):
    form = SimpleNamespace(
        text="ID:90020302 Dealer:KSV5 Outlet Name:test\nWATER PET 500ML: 2",
        caption=None, message_id=1316, reply_to_message=None, photo=[], location=None,
    )
    clock[0] = datetime(2026, 8, 7, 13, 16, 0)
    await handlers.receive_sale_text(_update(telegram_id, "Rin Saroth SA KSV5", form), None)
    gps = SimpleNamespace(
        text=None, caption=None, message_id=1317, reply_to_message=form, photo=[],
        location=SimpleNamespace(latitude=12.0, longitude=105.0),
    )
    clock[0] = datetime(2026, 8, 7, 13, 16, 30)
    await handlers.receive_location(_update(telegram_id, "Rin Saroth SA KSV5", gps), None)


def main():
    # Every fuzzy core used for automatic binding must be unique in its group.
    by_group = {}
    for member in SA_MEMBERS:
        core = service.identity_name_core(
            member.get("name"), region=member.get("region"), dealer=member.get("dealer")
        )
        by_group.setdefault(member.get("group_name"), {}).setdefault(core, []).append(member)
    ambiguous = [
        (group, core, [m.get("name") for m in members])
        for group, cores in by_group.items() for core, members in cores.items()
        if core and len(members) > 1
    ]
    assert not ambiguous, ambiguous

    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    handlers.SessionLocal = session
    clock = [datetime(2026, 8, 7, 13, 0)]
    service.local_now = lambda: clock[0]

    db = session()
    service.seed_sa_list(db)
    service.sync_group_members_from_list(db, -1002005005, "R2-SA-KSV5")
    telegram_id = db.query(SAUser).filter(SAUser.group_id == -1002005005).first().telegram_user_id
    db.close()

    asyncio.run(_submit(clock, telegram_id))

    db = session()
    service.sync_group_members_from_list(db, -1002005005, "R2-SA-KSV5")
    official = db.query(SAUser).filter(
        SAUser.group_id == -1002005005,
        SAUser.telegram_user_id == telegram_id,
        SAUser.is_from_list == 1,
    ).one()
    assert official.name == "Rin Saroth SA KSV5"
    missing = service.get_missing_sale_photo_users_for_interval(
        db, -1002005005, datetime(2026, 8, 7, 13, 0), datetime(2026, 8, 7, 13, 20),
        flow_key="sa_7",
    )
    assert official.id not in {row.id for row in missing}, [row.name for row in missing]
    db.close()
    print("NO-USERNAME IDENTITY BINDING OK: Rin Saroth is not falsely mentioned.")


if __name__ == "__main__":
    main()
