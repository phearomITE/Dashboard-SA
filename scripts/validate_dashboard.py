"""Offline dashboard integration checks with real ORM services and HTTP requests."""
import os
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:offline-validation"

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.dashboard.access import DashboardAccess
from app.dashboard.api import create_api
from app.data.controllers import GLOBAL_MANAGEMENT_USER_IDS
from app.database.database import Base
from app.database.models import PinHistory, SAList, SAUser
from app.services.user_service import _eligible_users_query, register_group, save_location, seed_sa_list
from app.services.time_service import local_now


def check_live_map(client, sessions):
    headers = {"X-Fieldline-Request": "1"}
    def upload(content, fmt="csv"):
        return client.post("/api/live-map/dealers?format=" + fmt, content=content, headers=headers)

    csv_header = b"Region,Dealer,Latitude,Longitude\n"
    invalid = upload(csv_header + b"R1,CA1,11.55,104.92\nR1,CA2,91,104\n")
    assert invalid.status_code == 422 and "Row 3" in invalid.json()["detail"]
    assert not client.get("/api/live-map").json()["dealers"]
    with sessions() as db:
        for update, hour, lat in [(101, 18, 11.56), (102, 17, 11.55), (102, 17, 11.55)]:
            save_location(db, 123456789, -151, "R1-SA-CA1", "Test member", None, lat, 104.92,
                          event_at=datetime(2026, 11, 13, hour, tzinfo=timezone.utc), telegram_update_id=update)
        pins = db.query(PinHistory).order_by(PinHistory.event_at).all()
        assert len(pins) == 2 and pins[0].leg_km is None and 1.10 < pins[1].leg_km < 1.13
        assert pins[1].province == "Phnom Penh" and pins[1].district and pins[1].commune
        pin = pins[0]
        # Old records have only receipt time, sender identity and group metadata.
        db.add(PinHistory(telegram_user_id=987654, group_id=-151, group_name="R1-SA-CA1",
                          name="Historical", lat=0, lon=0, map_link="", pinned_at=datetime(2026,11,13,23,59),
                          window_start=pin.window_start, window_end=pin.window_end))
        db.commit()
    day = client.get("/api/live-map?day=2026-11-14").json()
    assert [p["outlet"] for p in day["events"]] == [1, 2]
    missing = client.get("/api/missing-report?from_date=2026-11-14&to_date=2026-11-14")
    assert missing.status_code == 200 and missing.json()["rows"][0]["total_visit"] == 2
    assert client.get("/api/missing-report?from_date=2026-11-15&to_date=2026-11-14").status_code == 422
    assert day["events"][0]["at"] == "2026-11-14T00:00:00"
    old = client.get("/api/live-map?day=2026-11-13").json()["events"]
    assert len(old) == 1 and old[0]["province"] is None and old[0]["dealer"] == "CA1"
    assert upload(csv_header + b"R1,CA1,11.54,104.92\n").json() == {"count": 1}
    day = client.get("/api/live-map?day=2026-11-14").json()
    assert 1.10 < day["events"][0]["leg_km"] < 1.13
    book = Workbook()
    book.active.append(["Dealer code", "Region", "Lat", "Lng"])
    book.active.append(["CA1", "R1", 11.55, 104.92])
    content = BytesIO()
    book.save(content)
    assert upload(content.getvalue(), "xlsx").json() == {"count": 1}
    assert client.get("/api/live-map?day=2026-11-14").json()["events"][0]["leg_km"] == 0
    assert len(client.get("/api/live-map/areas").json()["features"]) == 25


def check():
    with TemporaryDirectory() as folder:
        engine = create_engine(f"sqlite:///{Path(folder) / 'checks.db'}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, info={"official_roster": []})
        with sessions() as db:
            register_group(db, -151, "R1-SA-CA1", sync_members=False)
        access = DashboardAccess()
        admin = next(iter(GLOBAL_MANAGEMENT_USER_IDS))
        with TestClient(create_api(access, sessions)) as client:
            def call(method, path, body=None, status=200):
                result = client.request(method, "/api/" + path, json=body, headers={"X-Fieldline-Request": "1"})
                assert result.status_code == status, (method, path, result.status_code, result.text[:500])
                return result

            def state():
                return call("GET", "workspace").json()

            call("GET", "workspace", status=401)
            call("GET", "live-map", status=401)
            call("POST", "live-map/dealers?format=csv", status=401)
            assert client.post("/api/session", json={}).status_code == 403
            code = access.issue(admin)
            call("POST", "session", {"code": code})
            call("POST", "session", {"code": code}, status=401)
            product = {"name": "ទឹកសុទ្ធ", "sku": "w-9", "category": "Beverages"}
            pid = call("POST", "products", product, 201).json()["id"]
            call("POST", "products", product, 409)
            path = f"products/{pid}"
            call("PUT", path, {**product, "category": "Water"})
            call("PUT", path + "/status", {"enabled": False})
            assert state()["products"] == [dict(id=pid, sku="W-9", name=product["name"], category="Water", enabled=False)]
            call("PUT", path + "/status", {"enabled": True})
            call("POST", "members", {"telegram_id": "123456789", "group_id": "-151", "dealer": "CA1",
                                    "position": "SA", "shift": "7:00am-5:00pm", "controller_id": 827319746}, 201)
            member = state()["roster"][0]
            assert "Sovannry On" in member["controller"]
            path = f"members/{member['id']}"
            edit = {"telegram_id": 123456780, "name": "Edited SA", "username": "edited_sa",
                    "region": "R1", "dealer": "CA1", "group_id": -151,
                    "position": "Commando", "shift": "9:00am-8:00pm"}
            call("PUT", path, {**edit, "region": "R9"}, 422)
            with sessions() as db:
                assert db.query(SAUser).one().position == "SA"
                assert db.query(SAList).one().position == "SA"
            call("PUT", path, edit)
            assert state()["roster"][0]["username"] == "edited_sa"
            assert state()["roster"][0]["telegram_id"] == "123456780"
            call("PUT", path + "/leave", {"period": "day"})
            assert state()["roster"][0]["status"] == "On leave"
            start = local_now().date()
            end = start + timedelta(days=3)
            call("PUT", path + "/leave", {"period": "range", "start_date": str(start), "end_date": str(end)})
            leave = state()["roster"][0]
            assert leave["leave_reason"] == "AL_RANGE"
            assert leave["leave_start"].startswith(str(start)) and leave["leave_end"].startswith(str(end))
            with sessions() as db:
                during = datetime.combine(start + timedelta(days=2), datetime.min.time()).replace(hour=10)
                after = datetime.combine(end + timedelta(days=1), datetime.min.time()).replace(hour=10)
                assert _eligible_users_query(db, -151, during, during + timedelta(minutes=20)).count() == 0
                assert _eligible_users_query(db, -151, after, after + timedelta(minutes=20)).count() == 1
            call("PUT", path + "/leave", {"period": "range", "start_date": str(end), "end_date": str(start)}, 422)
            call("PUT", path + "/leave", {"period": "off"})
            call("PUT", path + "/status", {"enabled": False})
            call("POST", "groups/-151/sync")
            assert not state()["roster"][0]["bound"]
            call("PUT", path + "/status", {"enabled": True})
            assert state()["roster"][0]["bound"]
            call("PUT", "groups/-151/status", {"enabled": False})
            with sessions() as db:
                register_group(db, -151, "R1-SA-CA1", sync_members=False)
            assert not state()["groups"][0]["enabled"]
            call("PUT", "monitoring", {"enabled": False})
            assert not state()["alerts_enabled"]
            call("PUT", "controllers", dict(scope="R1-CA1", telegram_id="999001", name="Test controller"))
            assert any(c["telegram_id"] == "999001" for c in state()["controllers"])
            call("PUT", path + "/controller", dict(telegram_id="999002", name="Replacement controller"))
            assert any(c["telegram_id"] == "999002" for c in state()["controllers"])
            assert all(c["telegram_id"] != "999001" for c in state()["controllers"])
            call("DELETE", "controllers/R1-CA1/999002")
            assert all(c["telegram_id"] != "999002" for c in state()["controllers"])
            for kind in ["roster", "missing"]:
                wb = load_workbook(BytesIO(call("GET", "exports/" + kind).content))
                assert wb.active.max_row >= 1
                wb.close()
            assert len(call("GET", "analytics?days=14").json()["series"]) == 14
            assert call("GET", "activity").json()["total"] == 0
            check_live_map(client, sessions)
            call("DELETE", path)
            assert not state()["roster"]
            assert call("GET", "live-map?day=2026-11-14").json()["events"]
            call("DELETE", "session", status=204)
            call("GET", "workspace", status=401)
        master = {"telegram_user_id": 876543210, "group_name": "R1-SA-CA1", "name": "Master SA",
                  "username": "master_sa", "region": "R1", "dealer": "CA1", "position": "SA",
                  "working_time": "7:00am-5:00pm", "is_active": 1}
        master_sessions = sessionmaker(engine, info={"official_roster": [master]})
        with master_sessions() as db:
            seed_sa_list(db)
        with TestClient(create_api(DashboardAccess(), master_sessions, auth_required=False)) as client:
            assert client.get("/api/mode").json() == {"preview": False, "auth_required": False}
            assert client.get("/api/workspace").status_code == 200
            assert client.get("/api/live-map").status_code == 200
            result = client.post("/api/products", json={"name": "Local access", "sku": "LOCAL-1"},
                                 headers={"X-Fieldline-Request": "1"})
            assert result.status_code == 201, result.text
            assert any(p["sku"] == "LOCAL-1" for p in client.get("/api/workspace").json()["products"])
            roster = client.get("/api/workspace").json()["roster"]
            master_id = next(r["id"] for r in roster if r["name"] == "Master SA")
            headers = {"X-Fieldline-Request": "1"}
            edited = {"telegram_id": 876543210, "name": "Dashboard SA", "username": "dashboard_sa",
                      "region": "R1", "dealer": "CA1", "group_id": -151,
                      "position": "SA Team Leader", "shift": "6:30am-4:30pm"}
            assert client.put(f"/api/members/{master_id}", json=edited, headers=headers).status_code == 200
            with master_sessions() as db:
                seed_sa_list(db)
            assert any(r["name"] == "Dashboard SA" for r in client.get("/api/workspace").json()["roster"])
            assert client.delete(f"/api/members/{master_id}", headers=headers).status_code == 200
            with master_sessions() as db:
                seed_sa_list(db)
            assert not any(r["id"] == master_id for r in client.get("/api/workspace").json()["roster"])
        asyncio.run(check_listener(sessions))
        engine.dispose()
    print("Dashboard API, persistence, permissions, and workbook checks passed.")


async def check_listener(sessions):
    import httpx
    from app.dashboard import runtime
    application = SimpleNamespace(bot_data={}, running=False, updater=None)
    settings = SimpleNamespace(DASHBOARD_ENABLED=True, DASHBOARD_HOST="127.0.0.1", DASHBOARD_PORT=0,
                               DASHBOARD_AUTH_REQUIRED=True)
    with patch.object(runtime, "get_settings", return_value=settings), patch.object(runtime, "SessionLocal", sessions):
        await runtime.start_dashboard(application)
        server, task, sock = application.bot_data["dashboard_server"]
        try:
            for _ in range(200):
                if server.started:
                    break
                if task.done():
                    await task
                    raise AssertionError("Dashboard listener exited")
                await asyncio.sleep(.01)
            assert server.started
            port = sock.getsockname()[1]
            async with httpx.AsyncClient() as client:
                result = await client.get(f"http://127.0.0.1:{port}/api/mode")
                assert result.json() == {"preview": False, "auth_required": True}
        finally:
            await runtime.stop_dashboard(application)
        assert not application.bot_data


if __name__ == "__main__":
    check()
