from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import asyncio
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from telegram.error import TelegramError

from app.data.controllers import CONTROLLERS_BY_REGION, GLOBAL_MANAGEMENT_USER_IDS, normalize_group_code, region_from_group
from app.data.sa_members import normalize_group
from app.database.models import ControllerGroupAssignment, MissingSalePhotoAlert, MonitoringPreferences, PinHistory, Product, RegionalController, SAAlertExclusion, SAList, SAUser, TelegramGroup
from app.dashboard.daily_report import build_daily_report_workbook, daily_report_data
from app.dashboard.queries import activity, analytics, missing_report, snapshot
from app.dashboard.dealer_import import import_dealers
from app.dashboard.roster_import import import_master_sa
from app.services.live_map import map_day
from app.services.map_geography import boundary_map
from app.services import user_service as users
from app.services.export_service import build_missing_export_rows, create_missing_export_workbook, create_sa_list_workbook
from app.services.shift_config import ALL_FLOWS, previous_window_for_now
from app.services.time_service import local_now
from app.core.telemetry import instrument_dashboard

DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
Shift = Literal["6:30am-4:30pm", "7:00am-5:00pm", "7:30am-5:00pm", "9:00am-8:00pm"]
Position = Literal["SA", "SA Support WS", "Commando", "SA Team Leader"]


class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class RoadRouteInput(Input):
    points: list[tuple[float, float]] = Field(min_length=2, max_length=120)


class Login(Input):
    code: str = Field(default="", max_length=128)


class Toggle(Input):
    enabled: bool


class ControllerAlerts(Input):
    enabled: bool
    group_id: int = Field(lt=0, ge=-(2**52))


class BotBreak(Input):
    minutes: int = Field(ge=0, le=10080)
    reason: str = Field(default="Dashboard Settings", max_length=100)


class ProductInput(Input):
    name: str = Field(min_length=1, max_length=160)
    sku: str = Field(min_length=1, max_length=64)
    category: str = Field(default="", max_length=80)


class MemberEdit(Input):
    position: Position
    shift: Shift


class MemberAdd(MemberEdit):
    group_id: int = Field(lt=0, ge=-(2**52))
    telegram_id: int = Field(gt=0, lt=2**52)
    employee_id: str = Field(default="", max_length=64)
    dealer: str = Field(min_length=1, max_length=50)
    controller_id: int = Field(gt=0, lt=2**52)


class MemberFullEdit(MemberEdit):
    telegram_id: int = Field(gt=0, lt=2**52)
    employee_id: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=255)
    username: str = Field(default="", max_length=255)
    region: str = Field(min_length=2, max_length=50)
    dealer: str = Field(min_length=1, max_length=50)
    group_id: int = Field(lt=0, ge=-(2**52))


class Leave(Input):
    period: Literal["morning", "afternoon", "day", "range", "off"]
    start_date: date | None = None
    end_date: date | None = None


class Assignment(Input):
    scope: str = Field(min_length=2, max_length=50)
    telegram_id: int = Field(gt=0, lt=2**52)
    name: str = Field(min_length=1, max_length=255)
    username: str = Field(default="", max_length=254)


class DealerControllerInput(Input):
    telegram_id: int = Field(gt=0, lt=2**52)
    name: str = Field(min_length=1, max_length=255)
    username: str = Field(default="", max_length=254)


class MissReportEdit(Input):
    from_date: date
    to_date: date
    name: str = Field(min_length=1, max_length=255)
    region: str = Field(min_length=2, max_length=50)
    dealer: str = Field(min_length=1, max_length=50)
    reason: str = Field(default="", max_length=2000)


class RecoveryRun(Input):
    day: date
    from_time: str = Field(default="00:00", pattern=r"^\d{2}:\d{2}$")
    to_time: str = Field(default="23:59", pattern=r"^\d{2}:\d{2}$")
    group: str = Field(default="", max_length=120)


def required(db, model, identity):
    item = db.get(model, identity, with_for_update=True)
    if not item:
        raise HTTPException(404, "This record no longer exists. Refresh the workspace.")
    return item


def group_for(db, group_id):
    group = db.query(TelegramGroup).filter_by(group_id=group_id).with_for_update().one_or_none()
    if not group or group_id >= 0:
        raise HTTPException(404, "Registered group not found.")
    return group


def roster_link(db, row):
    groups = [g for g in db.query(TelegramGroup).filter(TelegramGroup.group_id < 0)
              if normalize_group(g.group_name) == normalize_group(row.group_name)]
    if len(groups) > 1:
        raise HTTPException(409, "Multiple Telegram groups have this name. Resolve the group names before editing.")
    if not groups:
        return None, None
    query = db.query(SAUser).filter_by(group_id=groups[0].group_id, is_from_list=1)
    if row.telegram_user_id:
        return groups[0], query.filter_by(telegram_user_id=row.telegram_user_id).with_for_update().one_or_none()
    matches = [u for u in query if users.normalize_name(u.official_name or u.name) == users.normalize_name(row.name)]
    if len(matches) > 1:
        raise HTTPException(409, "Ambiguous roster identity.")
    return groups[0], next(iter(matches), None)


def create_api(access, sessions, bot=None, *, preview=False, auth_required=True, bot_loop=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=500)
    instrument_dashboard(app)
    cookie = "fieldline_preview" if preview else "fieldline_session"
    photo_cache = {}
    photo_locks = {}
    photo_sem = asyncio.Semaphore(4)
    known_ids = set()
    api_cache = {}

    async def run_on_bot_loop(coro):
        if bot_loop is not None and bot_loop.is_running():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, bot_loop))
        return await coro

    def cached_endpoint(key, ttl_seconds, compute):
        if not preview:
            now = datetime.now(timezone.utc)
            entry = api_cache.get(key)
            if entry and entry[0] > now:
                return entry[1]
        with sessions() as db:
            value = compute(db)
            if not preview and db.get_bind().dialect.name == "postgresql":
                api_cache[key] = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds), value)
                if len(api_cache) > 256:
                    api_cache.clear()
            return value

    def cached_query(db, key, ttl_seconds, compute):
        if preview or db.get_bind().dialect.name != "postgresql":
            return compute()
        now = datetime.now(timezone.utc)
        entry = api_cache.get(key)
        if entry and entry[0] > now:
            return entry[1]
        value = compute()
        api_cache[key] = (now + timedelta(seconds=ttl_seconds), value)
        if len(api_cache) > 256:
            api_cache.clear()
        return value

    @app.middleware("http")
    async def private_api(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if request.method != "GET" and request.headers.get("x-fieldline-request") != "1":
                return JSONResponse({"detail": "Invalid request."}, status_code=403)
            request.state.admin = (access.identify(request.cookies.get(cookie, "")) if auth_required
                                   else next(iter(GLOBAL_MANAGEMENT_USER_IDS)))
            if auth_required and path not in {"/api/session", "/api/mode"} and not request.state.admin:
                return JSONResponse({"detail": "Sign in to the workspace."}, status_code=401)
            if request.method in {"POST", "PUT", "DELETE"}:
                api_cache.clear()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api/") and not path.startswith("/api/profile-photo/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(IntegrityError)
    async def duplicate(request, error):
        return JSONResponse({"detail": "This identifier already exists. Refresh and choose a unique value."}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    def read():
        with sessions() as db:
            yield db

    def write():
        # Existing Telegram services commit internally. Savepoints keep multi-step
        # dashboard commands atomic, including updates to both roster tables.
        with sessions() as base, base.begin():
            connection = base.connection()
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")
            with Session(bind=connection, join_transaction_mode="create_savepoint", info=base.info) as db:
                yield db
                db.commit()

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz():
        return {"status": "ok"}

    @app.get("/api/mode")
    def mode():
        return {"preview": preview, "auth_required": auth_required}

    @app.post("/api/session")
    async def login(body: Login, request: Request):
        code = access.issue(next(iter(GLOBAL_MANAGEMENT_USER_IDS))) if preview else body.code
        token = access.exchange(code)
        if not token:
            raise HTTPException(401, "That code is invalid or expired. Request a new one with /dashboard.")
        result = JSONResponse({"ok": True})
        result.set_cookie(cookie, token, httponly=True, secure=request.url.scheme == "https", samesite="strict",
                          max_age=8 * 3600, path="/api")
        return result

    @app.delete("/api/session")
    async def logout(request: Request):
        access.revoke(request.cookies.get(cookie, ""))
        result = Response(status_code=204)
        result.delete_cookie(cookie, path="/api")
        return result

    @app.get("/api/workspace")
    def workspace():
        online = bool(bot and bot.running and bot.updater and bot.updater.running)
        res = cached_endpoint(("workspace", online), 30, lambda db: snapshot(db, online, preview))
        for item in res.get("roster", ()):
            if item.get("telegram_id", "").isdigit():
                known_ids.add(int(item["telegram_id"]))
        for item in res.get("controllers", ()):
            if item.get("telegram_id", "").isdigit():
                known_ids.add(int(item["telegram_id"]))
        return res

    @app.get("/api/profile-photo/{telegram_id}")
    async def profile_photo(telegram_id: int):
        if telegram_id <= 0:
            raise HTTPException(404, "SA member not found.")
        now = datetime.now(timezone.utc)
        cached = photo_cache.get(telegram_id)
        if cached and cached[0] > now:
            if cached[1] is None:
                raise HTTPException(404, "No Telegram profile photo available.")
            return Response(cached[1], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
        if telegram_id not in known_ids:
            with sessions() as db:
                exists = (db.query(SAList.id).filter(SAList.telegram_user_id == telegram_id,
                                                     SAList.is_deleted == 0).first() or
                          db.query(SAUser.id).filter(SAUser.telegram_user_id == telegram_id).first() or
                          db.query(ControllerGroupAssignment.id).filter(ControllerGroupAssignment.user_id == telegram_id).first() or
                          db.query(RegionalController.region).filter(RegionalController.user_id == telegram_id).first())
                if not exists:
                    raise HTTPException(404, "SA member not found.")
                known_ids.add(telegram_id)
        lock = photo_locks.setdefault(telegram_id, asyncio.Lock())
        async with lock:
            cached = photo_cache.get(telegram_id)
            if cached and cached[0] > datetime.now(timezone.utc):
                if cached[1] is None:
                    raise HTTPException(404, "No Telegram profile photo available.")
                return Response(cached[1], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
            payload = None
            if bot and bot.running:
                async with photo_sem:
                    try:
                        async def _fetch_photo():
                            photos = await bot.bot.get_user_profile_photos(telegram_id, limit=1)
                            if photos.photos:
                                file = await bot.bot.get_file(photos.photos[0][-1].file_id)
                                return bytes(await file.download_as_bytearray())
                            return None
                        payload = await run_on_bot_loop(_fetch_photo())
                    except TelegramError:
                        pass
            photo_cache[telegram_id] = (datetime.now(timezone.utc) + timedelta(hours=6 if payload else 1), payload)
            if len(photo_cache) > 5000:
                photo_cache.clear()
            if payload is None:
                raise HTTPException(404, "No Telegram profile photo available.")
            return Response(payload, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/live-map")
    def live_map(day: date | None = None, from_date: date | None = None, to_date: date | None = None):
        if from_date or to_date:
            f = from_date or to_date or day or local_now().date()
            t = to_date or from_date or day or local_now().date()
            if t < f or (t - f).days > 366:
                raise HTTPException(422, "Choose a valid date range of up to 367 days.")
            return cached_endpoint(("live-map", str(f), str(t)), 15, lambda db: map_day(db, t, from_date=f, to_date=t))
        target = day or local_now().date()
        return cached_endpoint(("live-map", str(target)), 15, lambda db: map_day(db, target))

    @app.get("/api/live-map/areas")
    def areas(west: float = 102, south: float = 9, east: float = 108, north: float = 15, detail: bool = False):
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise HTTPException(422, "Invalid map bounds.")
        return boundary_map(west, south, east, north, detail and east-west < 1.5 and north-south < 1.5)

    @app.post("/api/live-map/dealers")
    async def upload_dealers(request: Request, format: Literal["csv", "xlsx"], db=Depends(read)):
        from starlette.concurrency import run_in_threadpool
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > 4_000_000:
                raise HTTPException(413, "Choose a file smaller than 4 MB.")
            content.extend(chunk)
        result = await run_in_threadpool(import_dealers, db, bytes(content), format)
        api_cache.clear()
        return result

    road_route_cache: dict[tuple, dict] = {}

    @app.post("/api/live-map/road-route")
    async def live_map_road_route(body: RoadRouteInput):
        pts = [(round(float(lat), 5), round(float(lon), 5)) for lat, lon in body.points]
        for lat, lon in pts:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise HTTPException(422, "Invalid coordinates.")
        cache_key = tuple(pts)
        cached = road_route_cache.get(cache_key)
        if cached is not None:
            return cached

        all_coords: list[list[float]] = []
        all_legs_km: list[float] = []
        chunk_size = 25
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                idx = 0
                while idx < len(pts) - 1:
                    chunk = pts[idx: idx + chunk_size]
                    coord_str = ";".join(f"{lon},{lat}" for lat, lon in chunk)
                    url = f"https://router.project-osrm.org/route/v1/driving/{coord_str}"
                    resp = await client.get(url, params={"overview": "full", "geometries": "geojson", "steps": "false"})
                    if resp.status_code != 200:
                        return {"available": False, "legs_km": [], "total_km": None, "coordinates": []}
                    payload = resp.json()
                    routes_list = payload.get("routes") or []
                    if payload.get("code") != "Ok" or not routes_list:
                        return {"available": False, "legs_km": [], "total_km": None, "coordinates": []}
                    route_obj = routes_list[0]
                    geom = (route_obj.get("geometry") or {}).get("coordinates") or []
                    seg_coords = [[float(c[1]), float(c[0])] for c in geom]
                    if all_coords and seg_coords:
                        all_coords.extend(seg_coords[1:])
                    else:
                        all_coords.extend(seg_coords)
                    for leg in route_obj.get("legs") or []:
                        all_legs_km.append(round(float(leg.get("distance", 0.0)) / 1000.0, 2))
                    idx += chunk_size - 1
        except Exception:
            return {"available": False, "legs_km": [], "total_km": None, "coordinates": []}

        result = {
            "available": True,
            "legs_km": all_legs_km,
            "total_km": round(sum(all_legs_km), 2),
            "coordinates": all_coords,
        }
        if len(road_route_cache) >= 512:
            road_route_cache.clear()
        road_route_cache[cache_key] = result
        return result

    @app.get("/api/report-daily")
    def report_daily(from_date: date | None = None, to_date: date | None = None):
        today = local_now().date()
        f = from_date or to_date or today
        t = to_date or from_date or today
        if t < f or (t - f).days > 366:
            raise HTTPException(422, "Choose a valid date range of up to 367 days.")
        return cached_endpoint(("report-daily", str(f), str(t)), 10, lambda db: daily_report_data(db, f, t))

    @app.get("/api/report-daily/export")
    def report_daily_export(from_date: date | None = None, to_date: date | None = None):
        today = local_now().date()
        f = from_date or to_date or today
        t = to_date or from_date or today
        if t < f or (t - f).days > 366:
            raise HTTPException(422, "Choose a valid date range of up to 367 days.")
        data = cached_endpoint(("report-daily", str(f), str(t)), 10, lambda db: daily_report_data(db, f, t))
        content = build_daily_report_workbook(data)
        filename = f"daily-sales-report-{f.isoformat()}-to-{t.isoformat()}.xlsx"
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/analytics")
    def metrics(days: int = Query(7, ge=1, le=366), from_date: date | None = None, to_date: date | None = None):
        if from_date and to_date and (to_date < from_date or (to_date - from_date).days > 366):
            raise HTTPException(422, "Choose a valid date range of up to 367 days.")
        key = ("analytics", str(from_date or ""), str(to_date or ""), days)
        return cached_endpoint(key, 30, lambda db: analytics(db, days, from_date=from_date, to_date=to_date))

    @app.get("/api/activity")
    def events(kind: Literal["all", "reports", "pins", "alerts"] = "all",
               days: int = Query(7, ge=1, le=366), page: int = Query(1, ge=1, le=100000),
               size: int = Query(10, ge=1, le=100),
               from_date: date | None = None, to_date: date | None = None):
        if from_date and to_date and (to_date < from_date or (to_date - from_date).days > 366):
            raise HTTPException(422, "Choose a valid date range of up to 367 days.")
        key = ("activity", str(from_date or ""), str(to_date or ""), days, kind, page, size)
        return cached_endpoint(key, 15, lambda db: activity(db, days, kind, page, size, from_date=from_date, to_date=to_date))

    @app.get("/api/missing-report")
    def missing(from_date: date, to_date: date):
        if to_date < from_date or (to_date - from_date).days > 366:
            raise HTTPException(422, "Choose a valid date range of up to 367 days.")
        return cached_endpoint(("missing-report", str(from_date), str(to_date)), 15, lambda db: missing_report(db, from_date, to_date))

    @app.put("/api/missing-report/{telegram_id}")
    def edit_missing_report(telegram_id: int, body: MissReportEdit, db=Depends(write)):
        if body.to_date < body.from_date or (body.to_date - body.from_date).days > 366:
            raise HTTPException(422, "Choose a valid date range of up to 367 days.")
        region = body.region.upper()
        dealer = body.dealer.upper()
        if region not in REGIONS:
            raise HTTPException(422, "Choose a region from R1 to R8.")
        start = datetime.combine(body.from_date, time.min)
        end = datetime.combine(body.to_date + timedelta(days=1), time.min)
        now = local_now()
        for row in db.query(SAList).filter(SAList.telegram_user_id == telegram_id, SAList.is_deleted == 0):
            row.name, row.region, row.dealer = body.name, region, dealer
            row.dashboard_override = 1
            row.updated_at = now
        for user in db.query(SAUser).filter(SAUser.telegram_user_id == telegram_id):
            user.name, user.official_name = body.name, body.name
            user.region, user.dealer = region, dealer
            user.updated_at = now
        stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
        for pin in db.query(PinHistory).filter(PinHistory.telegram_user_id == telegram_id, stamp >= start, stamp < end):
            pin.name, pin.region, pin.dealer = body.name, region, dealer
        for alert in db.query(MissingSalePhotoAlert).filter(
            MissingSalePhotoAlert.telegram_user_id == telegram_id,
            MissingSalePhotoAlert.window_end >= start,
            MissingSalePhotoAlert.window_end < end,
        ):
            alert.name, alert.region, alert.dealer = body.name, region, dealer
            alert.reason_text = body.reason or None
            alert.reason_submitted_at = now if body.reason else None
        db.flush()
        return {"message": "Miss report updated."}

    @app.delete("/api/missing-report/{telegram_id}")
    def delete_missing_report(telegram_id: int, from_date: date, to_date: date,
                              mode: Literal["all", "alerts"] = "all", db=Depends(write)):
        if to_date < from_date or (to_date - from_date).days > 366:
            raise HTTPException(422, "Choose a valid date range of up to 367 days.")
        start = datetime.combine(from_date, time.min)
        end = datetime.combine(to_date + timedelta(days=1), time.min)
        db.query(MissingSalePhotoAlert).filter(
            MissingSalePhotoAlert.telegram_user_id == telegram_id,
            MissingSalePhotoAlert.window_end >= start,
            MissingSalePhotoAlert.window_end < end,
        ).delete(synchronize_session=False)
        if mode == "all":
            stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
            db.query(PinHistory).filter(
                PinHistory.telegram_user_id == telegram_id,
                stamp >= start,
                stamp < end,
            ).delete(synchronize_session=False)
        db.flush()
        return {"message": "Miss report record deleted." if mode == "all" else "Missing alerts cleared."}

    @app.post("/api/products", status_code=201)
    def add_product(body: ProductInput, db=Depends(write)):
        item = Product(name=body.name, sku=body.sku.upper(), category=body.category)
        db.add(item)
        db.flush()
        return {"id": item.id}

    @app.put("/api/products/{identity}")
    def edit_product(identity: int, body: ProductInput, db=Depends(write)):
        item = required(db, Product, identity)
        item.name, item.sku, item.category = body.name, body.sku.upper(), body.category
        db.flush()
        return {"id": item.id}

    @app.put("/api/products/{identity}/status")
    def toggle_product(identity: int, body: Toggle, db=Depends(write)):
        required(db, Product, identity).enabled = body.enabled
        return {"enabled": body.enabled}

    @app.delete("/api/products/{identity}")
    def delete_product(identity: int, db=Depends(write)):
        item = required(db, Product, identity)
        db.delete(item)
        db.flush()
        return {"message": "Product deleted."}

    @app.post("/api/members/import")
    async def upload_master_sa(request: Request, format: Literal["csv", "xlsx"] = "xlsx", db=Depends(read)):
        from starlette.concurrency import run_in_threadpool
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > 8_000_000:
                raise HTTPException(413, "Choose a file smaller than 8 MB.")
            content.extend(chunk)
        result = await run_in_threadpool(import_master_sa, db, bytes(content), format)
        api_cache.clear()
        return result

    @app.post("/api/members", status_code=201)
    def add_member(body: MemberAdd, request: Request, db=Depends(write)):
        group = group_for(db, body.group_id)
        region = region_from_group(group.group_name)
        if not region:
            raise HTTPException(422, "The Telegram group needs a region code.")
        scope = normalize_group_code(f"{region}-{body.dealer}")
        known = {c.user_id: c for items in CONTROLLERS_BY_REGION.values() for c in items}
        known.update({c.user_id: c for c in db.query(ControllerGroupAssignment).all()})
        selected = known.get(body.controller_id)
        if selected is None:
            raise HTTPException(422, "Choose a registered controller.")
        if any(r.telegram_user_id == body.telegram_id and normalize_group(r.group_name) == normalize_group(group.group_name)
               for r in db.query(SAList).filter_by(telegram_user_id=body.telegram_id)):
            raise HTTPException(409, "That member already belongs to this group.")
        if db.query(SAAlertExclusion).filter_by(group_id=group.group_id, telegram_user_id=body.telegram_id).first():
            raise HTTPException(409, "Restore this member before adding them.")
        users.add_manual_user(db, group_id=group.group_id, group_name=group.group_name,
                              telegram_user_id=body.telegram_id, name=f"Telegram User {body.telegram_id}",
                              position=body.position, working_time=body.shift, dealer=body.dealer, username=None,
                              employee_id=body.employee_id.strip() or None)
        db.query(ControllerGroupAssignment).filter_by(group_code=scope).delete(synchronize_session=False)
        db.add(ControllerGroupAssignment(group_code=scope, user_id=body.controller_id, name=selected.name,
                                         username=selected.username or "",
                                         changed_by_user_id=request.state.admin))
        db.commit()
        users.load_controller_overrides(db)
        return {"ok": True}

    @app.put("/api/members/{identity}")
    def edit_member(identity: int, body: MemberFullEdit, db=Depends(write)):
        row = required(db, SAList, identity)
        group = group_for(db, body.group_id)
        _, user = roster_link(db, row)
        region = body.region.upper()
        dealer = body.dealer.upper()
        if region not in REGIONS:
            raise HTTPException(422, "Choose a region from R1 to R8.")
        if db.query(SAList).filter(SAList.id != row.id, SAList.group_name == group.group_name,
                                   SAList.name == body.name, SAList.is_deleted == 0).first():
            raise HTTPException(409, "A member with this name already exists in that group.")
        if db.query(SAList).filter(SAList.id != row.id, SAList.group_name == group.group_name,
                                   SAList.telegram_user_id == body.telegram_id, SAList.is_deleted == 0).first():
            raise HTTPException(409, "This Telegram ID already belongs to another member in that group.")
        existing = db.query(SAUser).filter_by(group_id=body.group_id, telegram_user_id=body.telegram_id).one_or_none()
        if existing and (not user or existing.id != user.id):
            raise HTTPException(409, "This Telegram account is already bound in that group.")
        old_group_id = user.group_id if user else None
        old_telegram_id = user.telegram_user_id if user else None
        username = users.normalize_username(body.username)
        row.telegram_user_id, row.name, row.username = body.telegram_id, body.name, username
        row.employee_id = body.employee_id.strip() or None
        row.region, row.dealer, row.group_name = region, dealer, group.group_name
        row.position, row.working_time = body.position, body.shift
        row.dashboard_override = 1
        if user is None and row.is_active:
            user = SAUser(group_id=body.group_id, telegram_user_id=body.telegram_id,
                          group_name=group.group_name, name=body.name, username=username,
                          is_from_list=1, source_type=row.source_type)
            db.add(user)
        if user:
            user.group_id, user.telegram_user_id, user.group_name = body.group_id, body.telegram_id, group.group_name
            user.name, user.username = body.name, username
            user.official_name, user.official_username = body.name, username
            user.region, user.dealer, user.position = region, dealer, body.position
            user.working_time, user.working_time_override = body.shift, None
            user.flow_key = users.flow_key_for_position(body.position, body.shift)
            if old_group_id != body.group_id or old_telegram_id != body.telegram_id:
                user.lat = user.lon = user.map_link = user.last_pin_at = None
        db.flush()
        return {"ok": True}

    @app.delete("/api/members/{identity}")
    def delete_member(identity: int, db=Depends(write)):
        row = required(db, SAList, identity)
        if row.is_deleted:
            raise HTTPException(404, "This member has already been deleted.")
        _, user = roster_link(db, row)
        if user:
            db.delete(user)
        row.is_deleted = 1
        row.is_active = 0
        row.dashboard_override = 1
        db.flush()
        return {"message": "Member deleted from the active roster. Past reports and pins are retained."}

    @app.put("/api/members/{identity}/controller")
    def replace_member_controller(identity: int, body: DealerControllerInput, request: Request, db=Depends(read)):
        row = db.get(SAList, identity)
        if not row or row.is_deleted:
            raise HTTPException(404, "This member no longer exists.")
        scope = normalize_group_code(f"{row.region}-{row.dealer}")
        db.query(ControllerGroupAssignment).filter_by(group_code=scope).delete(synchronize_session=False)
        db.add(ControllerGroupAssignment(group_code=scope, user_id=body.telegram_id, name=body.name,
                                         username=users.normalize_username(body.username) or "",
                                         changed_by_user_id=request.state.admin))
        db.commit()
        users.load_controller_overrides(db)
        return {"message": f"Controller updated for {scope}."}

    @app.put("/api/members/{identity}/leave")
    def set_leave(identity: int, body: Leave, db=Depends(write)):
        row = required(db, SAList, identity)
        _, user = roster_link(db, row)
        if not user or not row.is_active:
            raise HTTPException(409, "Leave requires a synchronized, enabled member.")
        if body.period == "off":
            users.resume_alerts(db, user)
        else:
            if body.period == "range" and (body.start_date is None or body.end_date is None):
                raise HTTPException(422, "Choose both the start and end date for a range.")
            users.pause_alerts_for_dates(db, user, body.period, body.start_date or local_now().date(), body.end_date)
        return {"ok": True}

    @app.put("/api/members/{identity}/status")
    def toggle_member(identity: int, body: Toggle, request: Request, db=Depends(write)):
        row = required(db, SAList, identity)
        group, user = roster_link(db, row)
        if body.enabled and group:
            exclusions = db.query(SAAlertExclusion).filter_by(group_id=group.group_id)
            for ex in exclusions:
                same = (row.telegram_user_id == ex.telegram_user_id if row.telegram_user_id and ex.telegram_user_id
                        else ex.normalized_name == users.normalize_name(row.name))
                if same:
                    db.delete(ex)
            row.is_active = 1
            db.flush()
            users.sync_group_members_from_list(db, group.group_id, group.group_name)
        elif not body.enabled and user:
            users.remove_user_from_alerts(db, user, request.state.admin, "Dashboard admin")
        row.is_active = int(body.enabled)
        return {"ok": True}

    @app.put("/api/groups/{gid}/status")
    def toggle_group(gid: int, body: Toggle, db=Depends(write)):
        group_for(db, gid).is_active = int(body.enabled)
        return {"ok": True}

    @app.post("/api/groups/{gid}/sync")
    def sync(gid: int, db=Depends(write)):
        group = group_for(db, gid)
        users.sync_group_members_from_list(db, gid, group.group_name)
        return {"message": "Group roster synchronized."}

    @app.post("/api/groups/{gid}/reset")
    def reset(gid: int, db=Depends(write)):
        group_for(db, gid)
        count = users.reset_locations(db, gid)
        return {"message": f"Cleared current locations for {count} members."}

    @app.put("/api/monitoring")
    def monitoring(body: Toggle, db=Depends(write)):
        prefs = db.get(MonitoringPreferences, 1) or MonitoringPreferences(id=1)
        prefs.alerts_enabled = body.enabled
        db.add(prefs)
        return {"ok": True}

    @app.put("/api/monitoring/break")
    def monitoring_break(body: BotBreak, db=Depends(write)):
        prefs = db.get(MonitoringPreferences, 1) or MonitoringPreferences(id=1)
        if body.minutes <= 0:
            prefs.bot_paused_until = None
            prefs.bot_pause_reason = None
            db.add(prefs)
            return {"bot_paused_until": None, "message": "Automatic bot alerts resumed."}
        until = local_now() + timedelta(minutes=body.minutes)
        prefs.bot_paused_until = until
        prefs.bot_pause_reason = body.reason or "Dashboard Settings"
        db.add(prefs)
        hrs, mins = divmod(body.minutes, 60)
        if hrs and mins:
            label = f"{hrs}h {mins}m"
        elif hrs:
            label = "1 hour" if hrs == 1 else f"{hrs} hours"
        else:
            label = "1 minute" if mins == 1 else f"{mins} minutes"
        return {
            "bot_paused_until": until,
            "message": f"Bot automatic alerts paused for {label} (until {until:%H:%M}).",
        }

    @app.put("/api/monitoring/controller-alerts")
    def controller_alerts(body: ControllerAlerts, db=Depends(write)):
        prefs = db.get(MonitoringPreferences, 1) or MonitoringPreferences(id=1)
        prefs.controller_alerts_enabled = body.enabled
        prefs.controller_alert_group_id = body.group_id
        db.add(prefs)
        return {"ok": True}

    @app.post("/api/recovery/run")
    async def run_recovery(body: RecoveryRun, db=Depends(write)):
        from app.core.config import get_settings
        from app.services.telethon_recovery import sync_history_with_telethon, telethon_is_configured

        if preview:
            return {"message": "History recovery is disabled in preview mode."}
        cfg = get_settings()
        if not telethon_is_configured():
            raise HTTPException(
                409,
                "Set TELETHON_API_ID, TELETHON_API_HASH, and TELETHON_STRING_SESSION on the server first.",
            )
        start_t = datetime.strptime(body.from_time, "%H:%M").time()
        end_t = datetime.strptime(body.to_time, "%H:%M").time().replace(second=59)
        if end_t < start_t:
            raise HTTPException(422, "End time must not be earlier than start time.")
        try:
            summary = await sync_history_with_telethon(
                api_id=int(cfg.TELETHON_API_ID),
                api_hash=cfg.TELETHON_API_HASH.strip(),
                target_date=body.day,
                start_time=start_t,
                end_time=end_t,
                session_path=cfg.TELETHON_SESSION_FILE,
                string_session=cfg.TELETHON_STRING_SESSION.strip() or None,
                group_filter=body.group.strip() or None,
                dry_run=False,
                reconcile_missing=True,
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {
            "ok": True,
            "groups_synced": summary.groups_synced,
            "groups_total": summary.groups_total,
            "recovered_pins": summary.recovered_pins,
            "recovered_reports": summary.recovered_reports,
            "repaired_timestamps": summary.repaired_timestamps,
            "cleared_alerts": summary.reconciled_missing_alerts,
            "message": (
                f"Recovered +{summary.recovered_pins} pins, +{summary.recovered_reports} reports, "
                f"repaired {summary.repaired_timestamps} timestamps, cleared {summary.reconciled_missing_alerts} "
                f"false alerts across {summary.groups_synced}/{summary.groups_total} groups."
            ),
        }

    @app.post("/api/groups/register/{gid}")
    async def register(gid: int, db=Depends(write)):
        if gid >= 0:
            raise HTTPException(422, "Enter a negative Telegram group ID.")
        if not bot:
            raise HTTPException(409, "Group registration needs a live Telegram connection.")
        try:
            chat = await run_on_bot_loop(bot.bot.get_chat(gid))
        except TelegramError:
            raise HTTPException(422, "The bot cannot access this Telegram group.")
        if chat.type not in {"group", "supergroup"}:
            raise HTTPException(422, "Choose a Telegram group or supergroup.")
        users.register_group(db, gid, chat.title)
        return {"message": "Group registered and roster synchronized."}

    @app.post("/api/groups/{gid}/check")
    async def check(gid: int, db=Depends(read)):
        from app.scheduler.jobs import _send_missing_alert_for_group_window
        group = group_for(db, gid)
        if not bot or not bot.running or not bot.updater.running or not group.is_active:
            raise HTTPException(409, "This action requires an active group and live Telegram polling.")
        users.sync_group_members_from_list(db, gid, group.group_name)
        now = local_now()
        sent = 0
        for flow in ALL_FLOWS:
            window = previous_window_for_now(now, flow.label)
            if window:
                start, end = window.interval(now)
                count = await run_on_bot_loop(_send_missing_alert_for_group_window(
                    bot, db, group, start, end, window.title, window.include_submitted, flow.key))
                if count < 0:
                    raise HTTPException(502, "Telegram delivery failed. Check the group before retrying.")
                sent += count
        return {"message": f"Check completed. Members mentioned: {sent}."}

    @app.put("/api/controllers")
    async def assign(body: Assignment, request: Request, db=Depends(read)):
        args = dict(user_id=body.telegram_id, name=body.name, username=body.username,
                    changed_by_user_id=request.state.admin)
        if body.scope.upper() in REGIONS:
            users.change_region_controller(db, region=body.scope, **args)
        else:
            users.add_controller_groups(db, group_codes=[normalize_group_code(body.scope)], **args)
        return {"ok": True}

    @app.delete("/api/controllers/{scope}/{uid}")
    async def unassign(scope: str, uid: int, request: Request, db=Depends(read)):
        from app.database.models import ControllerGroupAssignment
        if scope in REGIONS:
            users.remove_region_controller(db, region=scope, changed_by_user_id=request.state.admin)
        else:
            row = db.query(ControllerGroupAssignment).filter_by(group_code=normalize_group_code(scope), user_id=uid).first()
            if row:
                db.delete(row)
                db.commit()
                users.load_controller_overrides(db)
        return {"ok": True}

    @app.get("/api/exports/{kind}")
    def export(kind: Literal["roster", "missing"], day: date | None = None,
               region: str = "all", db=Depends(read)):
        if region != "all" and region not in REGIONS:
            raise HTTPException(422, "Unknown region.")
        if kind == "roster":
            path = create_sa_list_workbook(db)
        else:
            regions = REGIONS if region == "all" else (region,)
            day = day or local_now().date()
            path = create_missing_export_workbook(build_missing_export_rows(db, regions, day), regions, day)
        try:
            return Response(path.read_bytes(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="{path.name}"'})
        finally:
            path.unlink(missing_ok=True)
            path.parent.rmdir()

    @app.api_route("/", methods=["GET", "HEAD"])
    def index():
        if not (DIST / "index.html").exists():
            raise HTTPException(503, "Dashboard assets are missing. Build the frontend in the dashboard directory.")
        return FileResponse(DIST / "index.html")

    app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="assets")
    return app


REGIONS = tuple(f"R{i}" for i in range(1, 9))
