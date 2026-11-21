from collections import Counter
from datetime import date, datetime, time, timedelta

from sqlalchemy import String, cast, func, literal, select, union_all

from app.core.config import get_settings
from app.data.controllers import CONTROLLERS_BY_REGION, controllers_for_group, controllers_for_region, group_code_from_name, region_from_group
from app.data.sa_members import normalize_group
from app.database.models import (
    ControllerGroupAssignment, MissingSalePhotoAlert, MonitoringPreferences,
    PinHistory, Product, SAList, SAUser, SalePhotoReport, TelegramGroup,
)
from app.services.shift_config import ALL_FLOWS, break_alert_for_shift
from app.services.telethon_recovery import telethon_is_configured
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
                           employee_id=row.employee_id or "",
                           group=row.group_name, group_id=str(gid) if group else "", region=row.region,
                           dealer=row.dealer, controller=controller, position=row.position, shift=row.working_time,
                           break_alert=break_alert_for_shift(row.working_time),
                           status=status, bound=bool(user), enabled=bool(row.is_active),
                           leave_start=user.alert_pause_start if user else None,
                           leave_end=user.alert_paused_until if user else None,
                           leave_reason=user.alert_pause_reason if user else None))
    counts = Counter(r["group_id"] for r in roster if r["enabled"])
    prefs = db.get(MonitoringPreferences, 1)
    bot_paused_until = prefs.bot_paused_until if (prefs and prefs.bot_paused_until and prefs.bot_paused_until > now) else None
    bot_pause_reason = prefs.bot_pause_reason if bot_paused_until else None
    controllers = [dict(name=c.name, telegram_id=str(c.user_id), username=c.username, scope=region, regional=True)
                   for region, items in CONTROLLERS_BY_REGION.items() for c in items]
    controllers.extend(dict(name=c.name, telegram_id=str(c.user_id), username=c.username, scope=c.group_code, regional=False)
                       for c in db.query(ControllerGroupAssignment).order_by(ControllerGroupAssignment.group_code))
    settings_obj = get_settings()
    from app.services.khmer_holidays import cambodia_holiday_name, upcoming_cambodia_holidays
    today_holiday = cambodia_holiday_name(now, extra_holidays_csv=settings_obj.EXTRA_HOLIDAY_DATES)
    return dict(now=now, master_date=None if preview else "2026-11-14", timezone=settings_obj.APP_TIMEZONE, online=online, preview=preview,
                alerts_enabled=prefs is None or prefs.alerts_enabled,
                bot_paused_until=bot_paused_until,
                bot_pause_reason=bot_pause_reason,
                controller_alerts_enabled=prefs is None or prefs.controller_alerts_enabled,
                controller_alert_group_id=str(prefs.controller_alert_group_id if prefs and prefs.controller_alert_group_id else -1004464440125),
                telethon_configured=telethon_is_configured(),
                bot_rules=dict(
                    tracking_mode="24/7 (Mon–Sun, including weekends & public holidays)",
                    miss_alert_days="Monday – Friday (excludes Cambodia public holidays)",
                    alert_interval_minutes=settings_obj.ALERT_INTERVAL_MINUTES,
                    skip_cambodia_holidays=settings_obj.SKIP_CAMBODIA_HOLIDAYS,
                    today_holiday=today_holiday,
                    working_shifts=[
                        "6:30am-4:30pm",
                        "7:00am-5:00pm (also 7:00am 5:00pm / 7:00am-5pm)",
                        "7:30am-5:00pm",
                        "9:00am-8:00pm",
                    ],
                    upcoming_holidays=upcoming_cambodia_holidays(
                        now.date(), limit=8, extra_holidays_csv=settings_obj.EXTRA_HOLIDAY_DATES
                    ),
                ),
                roster=roster, controllers=controllers,
                groups=[dict(id=str(g.group_id), name=g.group_name or str(g.group_id), region=region_from_group(g.group_name),
                             enabled=bool(g.is_active), members=counts[str(g.group_id)]) for g in groups],
                products=[dict(id=p.id, sku=p.sku, name=p.name, category=p.category, enabled=p.enabled)
                          for p in db.query(Product).order_by(Product.name, Product.id)],
                workdays=settings_obj.REMINDER_DAYS,
                schedules=[dict(key=f.key, label=f.label, windows=[
                    dict(start=f"{w.start_hour:02}:{w.start_minute:02}", end=f"{w.end_hour:02}:{w.end_minute:02}")
                    for w in f.windows]) for f in ALL_FLOWS])


EVENTS = {
    "reports": (SalePhotoReport, SalePhotoReport.submitted_at),
    "pins": (PinHistory, func.coalesce(PinHistory.event_at, PinHistory.pinned_at)),
    "alerts": (MissingSalePhotoAlert, MissingSalePhotoAlert.missed_at),
}


def analytics(db, days: int = 7, *, from_date: date | None = None, to_date: date | None = None):
    today = local_now().date()
    if from_date or to_date:
        start_day = from_date or to_date
        end_day = to_date or from_date
        if end_day < start_day:
            start_day, end_day = end_day, start_day
        span_days = max(1, (end_day - start_day).days + 1)
        dates = [start_day + timedelta(days=i) for i in range(span_days)]
    else:
        span_days = days
        dates = [today - timedelta(days=i) for i in reversed(range(days))]
    start = datetime.combine(dates[0], time.min)
    end = datetime.combine(dates[-1] + timedelta(days=1), time.min)
    series = {str(day): dict(date=str(day), reports=0, pins=0, alerts=0, al=0) for day in dates}
    previous = {}
    regional = {}

    roster_by_group = {}
    roster_by_uid = {}
    for gname, reg, dlr, tid in (
        db.query(SAList.group_name, SAList.region, SAList.dealer, SAList.telegram_user_id)
        .filter(SAList.is_deleted == 0)
        .all()
    ):
        reg_u = (reg or "").strip().upper()
        dlr_u = (dlr or "").strip().upper()
        gn = normalize_group(gname)
        if gn and reg_u:
            roster_by_group.setdefault(gn, (reg_u, dlr_u))
        if tid and reg_u:
            roster_by_uid.setdefault(str(tid), (reg_u, dlr_u))

    def _resolve_reg_dealer(group_name, raw_region=None, raw_dealer=None, uid=None):
        code = group_code_from_name(group_name)
        fb = roster_by_group.get(normalize_group(group_name)) or roster_by_uid.get(str(uid or "")) or ("", "")
        reg = ((raw_region or "").strip().upper()
               or (code.split("-")[0] if code else "")
               or region_from_group(group_name)
               or fb[0]
               or "Unknown")
        dlr = ((raw_dealer or "").strip().upper()
               or (code.split("-", 1)[1] if code and "-" in code else "")
               or fb[1]
               or "Unassigned")
        return reg, dlr

    daily_scope: dict[tuple[str, str, str], dict] = {}

    def _scope_row(d_str: str, reg: str, dlr: str) -> dict:
        k = (d_str, reg, dlr)
        row = daily_scope.get(k)
        if row is None:
            row = dict(date=d_str, region=reg, dealer=dlr, checkin=0, miss=0, al=0, reports=0)
            daily_scope[k] = row
        return row

    for key, (model, stamp) in EVENTS.items():
        day = func.date(stamp)
        for d_val, n in db.query(day, func.count(model.id)).filter(stamp >= start, stamp < end).group_by(day):
            d_str = str(d_val)[:10]
            if d_str in series:
                series[d_str][key] = n
        previous[key] = db.query(model).filter(stamp >= start - timedelta(days=span_days), stamp < start).count()
        for group, count in db.query(model.group_name, func.count(model.id)).filter(
                stamp >= start, stamp < end).group_by(model.group_name):
            region, _ = _resolve_reg_dealer(group)
            regional.setdefault(
                region,
                dict(region=region, reports=0, pins=0, alerts=0, al=0, reasons=0, written_reasons=0, unwritten_reasons=0),
            )[key] += count

    pin_stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    for d_val, gname, raw_reg, raw_dlr, n in (
        db.query(func.date(pin_stamp), PinHistory.group_name, PinHistory.region, PinHistory.dealer, func.count(PinHistory.id))
        .filter(PinHistory.group_id < 0, pin_stamp >= start, pin_stamp < end)
        .group_by(func.date(pin_stamp), PinHistory.group_name, PinHistory.region, PinHistory.dealer)
    ):
        reg, dlr = _resolve_reg_dealer(gname, raw_reg, raw_dlr)
        _scope_row(str(d_val)[:10], reg, dlr)["checkin"] += n

    for d_val, gname, n in (
        db.query(func.date(SalePhotoReport.submitted_at), SalePhotoReport.group_name, func.count(SalePhotoReport.id))
        .filter(SalePhotoReport.group_id < 0, SalePhotoReport.submitted_at >= start, SalePhotoReport.submitted_at < end)
        .group_by(func.date(SalePhotoReport.submitted_at), SalePhotoReport.group_name)
    ):
        reg, dlr = _resolve_reg_dealer(gname)
        _scope_row(str(d_val)[:10], reg, dlr)["reports"] += n

    for d_val, gname, raw_reg, raw_dlr, n in (
        db.query(func.date(MissingSalePhotoAlert.missed_at), MissingSalePhotoAlert.group_name,
                 MissingSalePhotoAlert.region, MissingSalePhotoAlert.dealer, func.count(MissingSalePhotoAlert.id))
        .filter(MissingSalePhotoAlert.group_id < 0,
                MissingSalePhotoAlert.missed_at >= start, MissingSalePhotoAlert.missed_at < end)
        .group_by(func.date(MissingSalePhotoAlert.missed_at), MissingSalePhotoAlert.group_name,
                  MissingSalePhotoAlert.region, MissingSalePhotoAlert.dealer)
    ):
        reg, dlr = _resolve_reg_dealer(gname, raw_reg, raw_dlr)
        _scope_row(str(d_val)[:10], reg, dlr)["miss"] += n

    for u in db.query(SAUser).filter(
        SAUser.is_from_list == 1,
        SAUser.alert_paused_until.isnot(None),
        SAUser.alert_paused_until >= start,
    ).all():
        reg, dlr = _resolve_reg_dealer(u.group_name, u.region, u.dealer, u.telegram_user_id)
        for d in dates:
            d_start = datetime.combine(d, time.min)
            d_end = d_start + timedelta(days=1)
            if u.alert_paused_until >= d_start and (u.alert_pause_start is None or u.alert_pause_start < d_end):
                d_str = str(d)
                if d_str in series:
                    series[d_str]["al"] += 1
                regional.setdefault(
                    reg,
                    dict(region=reg, reports=0, pins=0, alerts=0, al=0, reasons=0, written_reasons=0, unwritten_reasons=0),
                )["al"] += 1
                _scope_row(d_str, reg, dlr)["al"] += 1

    written_alerts = 0
    for group, count in db.query(MissingSalePhotoAlert.group_name, func.count(MissingSalePhotoAlert.id)).filter(
            MissingSalePhotoAlert.missed_at >= start, MissingSalePhotoAlert.missed_at < end,
            MissingSalePhotoAlert.reason_text.isnot(None), MissingSalePhotoAlert.reason_text != ""
    ).group_by(MissingSalePhotoAlert.group_name):
        region, _ = _resolve_reg_dealer(group)
        reg_row = regional.setdefault(
            region,
            dict(region=region, reports=0, pins=0, alerts=0, al=0, reasons=0, written_reasons=0, unwritten_reasons=0),
        )
        reg_row["reasons"] += count
        reg_row["written_reasons"] += count
        written_alerts += count
    for reg_row in regional.values():
        reg_row["unwritten_reasons"] = max(0, reg_row["alerts"] - reg_row["written_reasons"])
    total_alerts = sum(r["alerts"] for r in regional.values())
    unwritten_alerts = max(0, total_alerts - written_alerts)
    visitors = db.query(func.count(func.distinct(PinHistory.telegram_user_id))).filter(
        func.coalesce(PinHistory.event_at, PinHistory.pinned_at) >= start,
        func.coalesce(PinHistory.event_at, PinHistory.pinned_at) < end).scalar() or 0
    return dict(series=list(series.values()), previous=previous,
                regional=sorted(regional.values(), key=lambda r: r["region"]),
                daily_scope=sorted(daily_scope.values(), key=lambda x: (x["date"], x["region"], x["dealer"])),
                visitors=visitors, written_alerts=written_alerts, unwritten_alerts=unwritten_alerts,
                from_date=str(dates[0]), to_date=str(dates[-1]))


def activity(db, days: int = 7, kind: str = "all", page: int = 1, size: int = 10,
             *, from_date: date | None = None, to_date: date | None = None):
    if from_date or to_date:
        start_day = from_date or to_date
        end_day = to_date or from_date
        if end_day < start_day:
            start_day, end_day = end_day, start_day
        since = datetime.combine(start_day, time.min)
        until = datetime.combine(end_day + timedelta(days=1), time.min)
    else:
        since = datetime.combine(local_now().date() - timedelta(days=days - 1), time.min)
        until = None
    streams = []
    for key, (model, stamp) in EVENTS.items():
        if kind != "all" and key != kind:
            continue
        reason = model.reason_text if key == "alerts" else model.caption if key == "reports" else cast(literal(None), String)
        stmt = select(model.id, literal(key).label("kind"), stamp.label("at"),
                      model.telegram_user_id.label("telegram_id"),
                      model.name.label("name"), model.group_name, model.group_id,
                      reason.label("reason")).where(stamp >= since)
        if until is not None:
            stmt = stmt.where(stamp < until)
        streams.append(stmt)
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
        db.query(SAList.telegram_user_id, SAList.group_name, SAList.controller, SAList.dealer,
                 SAList.region, SAList.name, SAList.source_type, SAList.is_active, SAList.employee_id)
        .filter(SAList.is_deleted == 0)
        .all()
    )
    roster = {
        (str(r[0]), normalize_group(r[1])): (r[2], r[3], r[4], r[5], r[8] or "")
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
            info = ("", u.dealer or "", u.region or region_from_group(u.group_name) or "", u.official_name or u.name or "", "")
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
                member = ("", "", "", "", "")
        key = (str(uid), gnorm or str(group_id))
        if key not in rows:
            code = group_code_from_name(group_name)
            resolved_region = region or member[2] or (code.split('-')[0] if code else region_from_group(group_name)) or ""
            resolved_dealer = dealer or member[1] or (code.split('-')[1] if code else "")
            assigned = controllers_for_group(group_name) or controllers_for_region(resolved_region)
            rows[key] = dict(telegram_id=str(uid), employee_id=member[4] or "",
                             name=name or member[3] or "Unknown", group=group_name or str(group_id),
                             region=resolved_region, dealer=resolved_dealer,
                             controller=", ".join(c.name for c in assigned) or member[0] or "Unassigned",
                             total_visit=0, total_miss=0, written_count=0, unwritten_count=0,
                             reasons=[], alert_windows=[])
        return rows[key]

    stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    for pin in db.query(PinHistory).filter(PinHistory.group_id < 0, stamp >= start, stamp < end):
        if has_official_roster and (str(pin.telegram_user_id), normalize_group(pin.group_name)) not in active_pairs:
            continue
        item(pin.telegram_user_id, pin.group_id, pin.group_name, pin.name)["total_visit"] += 1
    for alert in db.query(MissingSalePhotoAlert).filter(MissingSalePhotoAlert.group_id < 0,
                                                        MissingSalePhotoAlert.window_end >= start,
                                                        MissingSalePhotoAlert.window_end < end).order_by(MissingSalePhotoAlert.window_start.desc()):
        row = item(alert.telegram_user_id, alert.group_id, alert.group_name,
                   alert.name, alert.region, alert.dealer)
        row["total_miss"] += 1
        reason = (alert.reason_text or "").strip()
        if reason:
            row["written_count"] += 1
            if reason not in row["reasons"]:
                row["reasons"].append(reason)
        else:
            row["unwritten_count"] += 1
        if len(row["alert_windows"]) < 50:
            w_start = alert.window_start.strftime("%Y-%m-%d %H:%M") if alert.window_start else ""
            w_end = alert.window_end.strftime("%H:%M") if alert.window_end else ""
            row["alert_windows"].append(dict(
                window=f"{w_start}-{w_end}" if w_start and w_end else (w_start or ""),
                reason=reason or "Awaiting reason",
                written=bool(reason),
            ))
    return dict(rows=sorted(rows.values(), key=lambda r: (-r["total_miss"], r["name"].casefold())),
                from_date=from_date, to_date=to_date)
