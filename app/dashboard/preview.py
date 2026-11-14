"""A loopback-only workspace with fictional records, no Telegram or production DB connection."""
import argparse
import os
import random
from datetime import timedelta
from tempfile import TemporaryDirectory
from pathlib import Path

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:offline-preview"

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.dashboard.access import DashboardAccess
from app.dashboard.api import create_api
from app.data.controllers import CONTROLLERS_BY_REGION, Controller
from app.database.database import Base
from app.database.models import DealerLocation, MissingSalePhotoAlert, PinHistory, Product, SAList, SAUser, SalePhotoReport, TelegramGroup
from app.services.time_service import local_now


def seed(db):
    rng = random.Random(3917)
    now = local_now()
    CONTROLLERS_BY_REGION.clear()
    people = ["Dara", "Sophea", "Vannak", "Bopha", "Sokha", "Kanha", "Rithy", "Sreymom", "Vichea",
              "Sophal", "Chenda", "Pisey", "Raksmey", "Sovann", "Nita", "Theara"]
    families = ["Chan", "Sok", "Kim", "Lim", "Chea", "Suon", "Heng", "Keo", "Meas", "Long", "Chhay", "Ros"]
    teams = []
    bases = [(11.551,104.919),(11.482,104.948),(10.611,104.178),(13.101,103.199),
             (13.358,103.855),(11.994,105.464),(12.491,106.022),(11.580,104.884)]
    origins = {}
    for i, size in enumerate([32, 27, 23, 19, 15, 12, 9, 7]):
        region = f"R{i + 1}"
        CONTROLLERS_BY_REGION[region] = (Controller(f"{families[i]} {people[i]}", "", 99000+i),)
        for j in range(size):
            dealer = f"CA{j % 3 + 1}"
            gid = -9000 - i * 10 - j % 3
            group = f"{region}-SA-{dealer}"
            if j < 3:
                db.add(TelegramGroup(group_id=gid, group_name=group))
                lat, lon = bases[i]
                origins[gid] = lat+j*.009, lon-j*.007
                db.add(DealerLocation(region=region, dealer=dealer, lat=origins[gid][0], lon=origins[gid][1]))
            uid = 800000 + len(teams)
            name = f"{families[(i + j // len(people)) % len(families)]} {people[(j + i) % len(people)]}"
            member = dict(group_name=group, telegram_user_id=uid, name=name, region=region,
                          dealer=dealer, position="SA", working_time="7:00am-5:00pm", source_type="manual")
            db.add(SAList(**member))
            db.add(SAUser(**member, group_id=gid, is_from_list=1, flow_key="sa_7"))
            teams.append((uid, name, gid, group, region))
    for idx, name in enumerate(["Mineral water", "Sparkling lime", "Iced jasmine tea", "Mango nectar"]):
        db.add(Product(name=name, sku=f"BEV-{101+idx}", category="Beverages", enabled=idx != 3))
    for offset in range(30):
        day = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        limit = max(1, int((now - day).total_seconds()) if offset == 0 else 20 * 3600)
        lower = min(7 * 3600, limit - 1)
        counts = [83, 121, 96, 140, 113, 152, 127]
        reports = round(counts[offset % 7] * (1 - .06 * (offset // 7)))
        for kind, count in enumerate([reports, round(reports * .71) + offset % 8, 3 + offset % 6]):
            for j in range(count):
                # Give several check-ins to each sample route so it can be explored.
                team = teams[(j % 14)*9] if kind == 1 else rng.choice(teams)
                uid, name, gid, group, region = team
                at = day + timedelta(seconds=rng.randrange(lower, limit), microseconds=j)
                event = dict(name=name, group_name=group, group_id=gid, telegram_user_id=uid,
                             window_start=at-timedelta(minutes=20), window_end=at)
                if kind == 0:
                    db.add(SalePhotoReport(**event, submitted_at=at))
                elif kind == 1:
                    lat, lon = origins[gid]
                    db.add(PinHistory(**event, pinned_at=at, lat=lat+rng.uniform(-.021,.021),
                                      lon=lon+rng.uniform(-.021,.021), map_link=""))
                else:
                    db.add(MissingSalePhotoAlert(**event, missed_at=at, region=region,
                        reason_text="Customer visit" if j % 3 == 0 else None))
    db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="fieldline-preview-") as folder:
        engine = create_engine(f"sqlite:///{Path(folder) / 'preview.db'}", connect_args={"check_same_thread": False})
        sessions = sessionmaker(engine, info={"official_roster": []})
        Base.metadata.create_all(engine)
        with sessions() as db:
            seed(db)
        try:
            uvicorn.run(create_api(DashboardAccess(), sessions, preview=True), host="127.0.0.1", port=args.port)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
