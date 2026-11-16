from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import func

from app.data.controllers import group_code_from_name, region_from_group
from app.database.models import DealerLocation, MissingSalePhotoAlert, PinHistory, SAList, SAUser, SalePhotoReport
from app.services.map_geography import administrative_area, distance_km
from app.services.time_service import local_now


def day_bounds(day):
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def pin_scope(pin, member=None):
    code = group_code_from_name(pin.group_name)
    region = pin.region or (member.region if member else None) or region_from_group(pin.group_name)
    dealer = pin.dealer or (member.dealer if member else None) or (code.split("-", 1)[1] if code else None)
    return (region or "").strip().upper(), (dealer or "").strip().upper()


def enrich_pin(pin, member=None):
    pin.region, pin.dealer = pin_scope(pin, member)
    pin.province, pin.district, pin.commune = administrative_area(pin.lat, pin.lon)
    pin.geo_checked = True


def route_distances(pins, origin):
    previous = (origin.lat, origin.lon) if origin else None
    for pin in pins:
        point = pin.lat, pin.lon
        leg = round(distance_km(previous, point), 4) if previous else None
        if pin.leg_km != leg:
            pin.leg_km = leg
        previous = point


def update_pin_route(db, pin, member):
    enrich_pin(pin, member)
    db.flush()
    start, end = day_bounds((pin.event_at or pin.pinned_at).date())
    stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    day_pins = db.query(PinHistory).filter(
        PinHistory.telegram_user_id == pin.telegram_user_id, stamp >= start, stamp < end,
    ).order_by(stamp, PinHistory.id)
    scope = pin.region, pin.dealer
    route = [p for p in day_pins if pin_scope(p, member if p.group_id == pin.group_id else None) == scope]
    route_distances(route, db.get(DealerLocation, scope))


def map_day(db, day):
    start, end = day_bounds(day)
    stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    pins = db.query(PinHistory).filter(PinHistory.group_id < 0, stamp >= start, stamp < end).order_by(stamp, PinHistory.id).all()
    origins = {(d.region, d.dealer): d for d in db.query(DealerLocation)}
    legacy_ids = {p.telegram_user_id for p in pins if not p.geo_checked}
    members = {(u.telegram_user_id, u.group_id): u for u in db.query(SAUser).filter(SAUser.telegram_user_id.in_(legacy_ids))} if legacy_ids else {}
    routes = defaultdict(list)
    for pin in pins:
        if not pin.geo_checked:
            enrich_pin(pin, members.get((pin.telegram_user_id, pin.group_id)))
        routes[pin.telegram_user_id, pin.region, pin.dealer].append(pin)
    events = []
    for (uid, region, dealer), route in routes.items():
        route_distances(route, origins.get((region, dealer)))
        for sequence, p in enumerate(route, 1):
            events.append(dict(id=p.id, employee_id=str(uid), name=p.name, username=p.username or "",
                               outlet_name=p.outlet_name, reported_employee_id=p.reported_employee_id,
                               region=region, dealer=dealer, group=p.group_name or str(p.group_id),
                               province=p.province, district=p.district, commune=p.commune,
                               lat=p.lat, lon=p.lon, at=p.event_at or p.pinned_at, outlet=sequence, leg_km=p.leg_km))
    # Persist historical enrichment only; refreshed rows can then use the same path as live pins.
    if db.dirty:
        db.commit()
    from app.services.export_service import _clean_reason
    reasons = defaultdict(list)
    roster_scope = {
        str(r.telegram_user_id): ((r.region or "").strip().upper(), (r.dealer or "").strip().upper(), r.name, r.username or "")
        for r in db.query(SAList).filter(SAList.is_deleted == 0, SAList.telegram_user_id.isnot(None)).all()
    }
    report_buckets = {}
    for rep in db.query(SalePhotoReport).filter(SalePhotoReport.group_id < 0,
                                                SalePhotoReport.submitted_at >= start,
                                                SalePhotoReport.submitted_at < end).all():
        uid = str(rep.telegram_user_id)
        code = group_code_from_name(rep.group_name)
        fallback = roster_scope.get(uid, ("", "", "", ""))
        reg = (region_from_group(rep.group_name) or (code.split("-")[0] if code else "") or fallback[0]).strip().upper()
        dlr = ((code.split("-", 1)[1] if code and "-" in code else "") or fallback[1]).strip().upper()
        key = (uid, reg, dlr)
        if key not in report_buckets:
            report_buckets[key] = dict(employee_id=uid, name=rep.name or fallback[2] or "",
                                       username=rep.username or fallback[3] or "", region=reg, dealer=dlr, count=0)
        report_buckets[key]["count"] += 1

    miss_buckets = {}
    all_alerts = db.query(MissingSalePhotoAlert).filter(
        MissingSalePhotoAlert.group_id < 0,
        MissingSalePhotoAlert.window_end >= start,
        MissingSalePhotoAlert.window_end < end,
    ).order_by(MissingSalePhotoAlert.window_end).all()
    for alert in all_alerts:
        uid = str(alert.telegram_user_id)
        code = group_code_from_name(alert.group_name)
        fallback = roster_scope.get(uid, ("", "", "", ""))
        reg = (alert.region or region_from_group(alert.group_name) or (code.split("-")[0] if code else "") or fallback[0]).strip().upper()
        dlr = (alert.dealer or (code.split("-", 1)[1] if code and "-" in code else "") or fallback[1]).strip().upper()
        key = (uid, reg, dlr)
        if key not in miss_buckets:
            miss_buckets[key] = dict(employee_id=uid, name=alert.name or fallback[2] or "",
                                     username=alert.username or fallback[3] or "", region=reg, dealer=dlr, count=0)
        miss_buckets[key]["count"] += 1
        if alert.reason_text:
            reason = _clean_reason(alert.reason_text)
            if reason and reason != (alert.voice_to_text or "").strip() and reason not in reasons[uid]:
                reasons[uid].append(reason)
    return dict(day=day, now=local_now(), reasons=reasons,
                reports=list(report_buckets.values()), misses=list(miss_buckets.values()),
                events=sorted(events, key=lambda e: (e["at"], e["id"])),
                dealers=[dict(region=d.region, dealer=d.dealer, lat=d.lat, lon=d.lon) for d in origins.values()])
