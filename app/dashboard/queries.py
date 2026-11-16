from collections import Counter
from datetime import datetime, time, timedelta

from sqlalchemy import String, cast, func, literal, select, union_all

from app.core.config import get_settings
from app.data.controllers import CONTROLLERS_BY_REGION, controllers_for_group, controllers_for_region, group_code_from_name, region_from_group
from app.data.sa_members import normalize_group
from app.database.models import (
    ControllerGroupAssignment, MissingSalePhotoAlert, MonitoringPreferences,
    PinHistory, Product, SAList, SAUser, SalePhotoReport, TelegramGroup,
)
from app.services.shift_config import ALL_FLOWS
from app.services.time_service import local_now
from app.services.user_service import normalize_name
from app.services.outlet_form import report_details, report_quantities


def snapshot(db, online, preview):
    now = local_now()
    groups = db.query(TelegramGroup).filter(TelegramGroup.group_id < 0).order_by(TelegramGroup.group_name).all()
    registered = {normalize_group(g.group_name): g for g in groups}
    users = db.query(SAUser).filter(SAUser.is_from_list == 1).all()
    by_id = {(u.group_id, u.telegram_user_id): u for u in users}
    by_name = {(u.group_id, normalize_name(u.official_name or u.name)): u for u in users}
    roster = []
    for row in db.query(SAList).filter(SAList.is_deleted == 0).order_by(SAList.name, SAList.id):
        group = registered.get(normalize_group(row.group_name))
        gid = group.group_id if group else None
        user = (by_id.get((gid, row.telegram_user_id)) if row.telegram_user_id
                else by_name.get((gid, normalize_name(row.name))))
        status = "Active"
        if not row.is_active:
            status = "Disabled"
        elif user and user.alert_paused_until and user.alert_paused_until >= now:
            status = "Leave scheduled" if user.alert_pause_start and user.alert_pause_start > now else "On leave"
        elif user and user.alert_active_from and user.alert_active_from > now:
            status = "Starts tomorrow"
        elif not user:
            status = "Awaiting sync" if group else "Unregistered group"
        assigned = controllers_for_group(row.group_name) or controllers_for_region(row.region)
        controller = ", ".join(c.name for c in assigned) or row.controller or ""
        roster.append(dict(id=row.id, name=row.name, username=row.username or (user.username if user else "") or "",
                           telegram_id=str(user.telegram_user_id if user else row.telegram_user_id or ""),
                           group=row.group_name, group_id=str(gid) if group else "", region=row.region,
                           dealer=row.dealer, controller=controller, position=row.position, shift=row.working_time,
                           status=status, bound=bool(user), enabled=bool(row.is_active),
                           leave_start=user.alert_pause_start if user else None,
                           leave_end=user.alert_paused_until if user else None,
                           leave_reason=user.alert_pause_reason if user else None))
    counts = Counter(r["group_id"] for r in roster if r["enabled"])
    prefs = db.get(MonitoringPreferences, 1)
    controllers = [dict(name=c.name, telegram_id=str(c.user_id), username=c.username, scope=region, regional=True)
                   for region, items in CONTROLLERS_BY_REGION.items() for c in items]
    controllers.extend(dict(name=c.name, telegram_id=str(c.user_id), username=c.username, scope=c.group_code, regional=False)
                       for c in db.query(ControllerGroupAssignment).order_by(ControllerGroupAssignment.group_code))
    return dict(now=now, master_date=None if preview else "2026-11-14", timezone=get_settings().APP_TIMEZONE, online=online, preview=preview,
                alerts_enabled=prefs is None or prefs.alerts_enabled,
                controller_alerts_enabled=prefs is None or prefs.controller_alerts_enabled,
                controller_alert_group_id=str(prefs.controller_alert_group_id if prefs and prefs.controller_alert_group_id else -1004464440125),
                roster=roster, controllers=controllers,
                groups=[dict(id=str(g.group_id), name=g.group_name or str(g.group_id), region=region_from_group(g.group_name),
                             enabled=bool(g.is_active), members=counts[str(g.group_id)]) for g in groups],
                products=[dict(id=p.id, sku=p.sku, name=p.name, category=p.category, enabled=p.enabled)
                          for p in db.query(Product).order_by(Product.name, Product.id)],
                workdays=get_settings().REMINDER_DAYS,
                schedules=[dict(key=f.key, label=f.label, windows=[
                    dict(start=f"{w.start_hour:02}:{w.start_minute:02}", end=f"{w.end_hour:02}:{w.end_minute:02}")
                    for w in f.windows]) for f in ALL_FLOWS])


EVENTS = {
    "reports": (SalePhotoReport, SalePhotoReport.submitted_at),
    "pins": (PinHistory, func.coalesce(PinHistory.event_at, PinHistory.pinned_at)),
    "alerts": (MissingSalePhotoAlert, MissingSalePhotoAlert.missed_at),
}


def analytics(db, days):
    today = local_now().date()
    dates = [today - timedelta(days=i) for i in reversed(range(days))]
    start = datetime.combine(dates[0], time.min)
    end = datetime.combine(today + timedelta(days=1), time.min)
    series = {str(day): dict(date=str(day), reports=0, pins=0, alerts=0) for day in dates}
    previous = {}
    regional = {}
    for key, (model, stamp) in EVENTS.items():
        day = func.date(stamp)
        for date, n in db.query(day, func.count(model.id)).filter(stamp >= start, stamp < end).group_by(day):
            series[str(date)][key] = n
        previous[key] = db.query(model).filter(stamp >= start - timedelta(days=days), stamp < start).count()
        for group, count in db.query(model.group_name, func.count(model.id)).filter(
                stamp >= start, stamp < end).group_by(model.group_name):
            region = region_from_group(group) or "Unknown"
            regional.setdefault(region, dict(region=region, reports=0, pins=0, alerts=0, reasons=0))[key] += count
    for group, count in db.query(MissingSalePhotoAlert.group_name, func.count(MissingSalePhotoAlert.id)).filter(
            MissingSalePhotoAlert.missed_at >= start, MissingSalePhotoAlert.missed_at < end,
            MissingSalePhotoAlert.reason_text.isnot(None), MissingSalePhotoAlert.reason_text != ""
    ).group_by(MissingSalePhotoAlert.group_name):
        region = region_from_group(group) or "Unknown"
        regional.setdefault(region, dict(region=region, reports=0, pins=0, alerts=0, reasons=0))["reasons"] += count
    visitors = db.query(func.count(func.distinct(PinHistory.telegram_user_id))).filter(
        func.coalesce(PinHistory.event_at, PinHistory.pinned_at) >= start,
        func.coalesce(PinHistory.event_at, PinHistory.pinned_at) < end).scalar() or 0
    return dict(series=list(series.values()), previous=previous, regional=sorted(regional.values(), key=lambda r:r["region"]), visitors=visitors)


def activity(db, days, kind, page, size):
    since = datetime.combine(local_now().date() - timedelta(days=days - 1), time.min)
    streams = []
    for key, (model, stamp) in EVENTS.items():
        if kind != "all" and key != kind:
            continue
        reason = model.reason_text if key == "alerts" else model.caption if key == "reports" else cast(literal(None), String)
        streams.append(select(model.id, literal(key).label("kind"), stamp.label("at"),
                              model.telegram_user_id.label("telegram_id"),
                              model.name.label("name"), model.group_name, model.group_id,
                              reason.label("reason")).where(stamp >= since))
    stream = union_all(*streams).subquery()
    total = db.scalar(select(func.count()).select_from(stream))
    ordered = select(stream).order_by(stream.c.at.desc(), stream.c.kind, stream.c.id.desc())
    events = []
    for row in db.execute(ordered.offset((page - 1) * size).limit(size)):
        detail = {"alerts": row.reason or "Awaiting reason", "pins": "Location received", "reports": "Report received"}
        if row.kind == "reports":
            fields = report_details(row.reason)
            detail["reports"] = " · ".join(fields[k] for k in ("outlet name", "outlet type", "dealer", "id") if fields.get(k)) or "Report received"
            quantities = report_quantities(row.reason)
            if quantities:
                detail["reports"] += " · " + ", ".join(quantities)
        events.append(dict(id=f"{row.kind}-{row.id}", kind=row.kind, at=row.at, name=row.name,
                           telegram_id=str(row.telegram_id or ""),
                           group=row.group_name or str(row.group_id), region=region_from_group(row.group_name),
                           detail=detail[row.kind], reason=row.kind=="alerts" and bool(row.reason)))
    return dict(rows=events, total=total)


def missing_report(db, from_date, to_date):
    """Aggregate recorded visits and missing alerts by Telegram user and group."""
    start = datetime.combine(from_date, time.min)
    end = datetime.combine(to_date + timedelta(days=1), time.min)
    roster_rows = (
        db.query(SAList.telegram_user_id, SAList.group_name, SAList.controller, SAList.dealer, SAList.region, SAList.name, SAList.source_type, SAList.is_active)
        .filter(SAList.is_deleted == 0)
        .all()
    )
    roster = {
        (str(r[0]), normalize_group(r[1])): (r[2], r[3], r[4], r[5])
        for r in roster_rows if r[0] is not None
    }
    by_uid = {}
    for (uid, gnorm), member in roster.items():
        by_uid.setdefault(uid, []).append((gnorm, member))
    sa_users = {
        (str(u.telegram_user_id), normalize_group(u.group_name)): u
        for u in db.query(SAUser).filter(SAUser.is_from_list == 1).all()
        if u.telegram_user_id is not None
    }
    for (uid, gnorm), u in sa_users.items():
        if (uid, gnorm) not in roster:
            info = ("", u.dealer or "", u.region or region_from_group(u.group_name) or "", u.official_name or u.name or "")
            roster[(uid, gnorm)] = info
            by_uid.setdefault(uid, []).append((gnorm, info))
    has_official_roster = any(r[6] == "excel" for r in roster_rows)
    active_pairs = {
        (str(r[0]), normalize_group(r[1]))
        for r in roster_rows if r[0] is not None and r[7] == 1
    } | set(sa_users.keys())
    rows = {}

    def item(uid, group_id, group_name, name, region="", dealer=""):
        gnorm = normalize_group(group_name)
        member = roster.get((str(uid), gnorm))
        if member is None:
            matches = by_uid.get(str(uid), [])
            if len(matches) == 1:
                gnorm, member = matches[0]
            else:
                member = ("", "", "", "")
        key = (str(uid), gnorm or str(group_id))
        if key not in rows:
            code = group_code_from_name(group_name)
            resolved_region = region or member[2] or (code.split('-')[0] if code else region_from_group(group_name)) or ""
            resolved_dealer = dealer or member[1] or (code.split('-')[1] if code else "")
            assigned = controllers_for_group(group_name) or controllers_for_region(resolved_region)
            rows[key] = dict(telegram_id=str(uid), name=name or member[3] or "Unknown", group=group_name or str(group_id),
                             region=resolved_region, dealer=resolved_dealer,
                             controller=", ".join(c.name for c in assigned) or member[0] or "Unassigned",
                             total_visit=0, total_miss=0, reasons=[])
        return rows[key]

    stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    for pin in db.query(PinHistory).filter(PinHistory.group_id < 0, stamp >= start, stamp < end):
        if has_official_roster and (str(pin.telegram_user_id), normalize_group(pin.group_name)) not in active_pairs:
            continue
        item(pin.telegram_user_id, pin.group_id, pin.group_name, pin.name)["total_visit"] += 1
    for alert in db.query(MissingSalePhotoAlert).filter(MissingSalePhotoAlert.group_id < 0,
                                                        MissingSalePhotoAlert.window_end >= start,
                                                        MissingSalePhotoAlert.window_end < end):
        row = item(alert.telegram_user_id, alert.group_id, alert.group_name,
                   alert.name, alert.region, alert.dealer)
        row["total_miss"] += 1
        reason = (alert.reason_text or "").strip()
        if reason and reason not in row["reasons"]:
            row["reasons"].append(reason)
    return dict(rows=sorted(rows.values(), key=lambda r: (-r["total_miss"], r["name"].casefold())),
                from_date=from_date, to_date=to_date)
