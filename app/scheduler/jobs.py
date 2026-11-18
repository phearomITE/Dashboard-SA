# app/scheduler/jobs.py
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
import time
from zoneinfo import ZoneInfo

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from telegram.error import NetworkError, RetryAfter
from telegram.ext import Application

from app.core.config import get_settings
from app.core.logger import get_logger
from app.data.controllers import EXPORT_AUDIT_RECIPIENTS, controller_label_for_group, controller_topic_for_group
from app.data.sa_members import normalize_group
from app.database.database import SessionLocal
from app.database.models import MissingSalePhotoAlert, MonitoringPreferences, PinHistory, SAList, SAUser, SalePhotoReport
from app.services.formatter import sale_photo_missing_alert_message, special_window_alert_message
from app.services.shift_config import (
    ALL_FLOWS,
    all_unique_run_times,
    previous_window_for_now,
    windows_for_run_time,
)
from app.services.user_service import (
    _resolve_submitted_user_ids,
    count_submitted_users_for_interval,
    get_active_groups,
    get_missing_sale_photo_users_for_interval,
    interval_end_with_grace,
    interval_start_with_grace,
    record_missing_users_and_attach_today_count,
    attach_alert_message_id,
    reset_locations,
    sync_group_members_from_list,
)

settings = get_settings()
logger = get_logger(__name__)


def local_now():
    return datetime.now(ZoneInfo(settings.APP_TIMEZONE)).replace(tzinfo=None)


def is_work_day(day: datetime) -> bool:
    return day.weekday() in settings.work_days_list


def regular_run_times_for_today(now: datetime) -> list[datetime]:
    """All unique check times used by the configured working schedules."""
    return all_unique_run_times(now)


def get_previous_regular_window(now: datetime, group_name: str | None = None):
    window = previous_window_for_now(now, group_name)
    if not window:
        return None
    return window.interval(now)


async def _send_with_retry(application: Application, **send_kwargs):
    for attempt in range(2):
        try:
            return await application.bot.send_message(**send_kwargs)
        except RetryAfter as retry_exc:
            if attempt == 1:
                raise
            await asyncio.sleep(float(retry_exc.retry_after) + 0.5)
        except NetworkError:
            if attempt == 1:
                raise
            await asyncio.sleep(1.0)


async def _send_missing_alert_for_group_window(
    application: Application,
    db,
    group,
    start: datetime,
    end: datetime,
    title: str,
    include_submitted: bool,
    flow_key: str | None = None,
) -> int:
    # Always read the latest committed form/pin rows immediately before the
    # decision. This is the last-chance reconciliation before a real mention.
    db.expire_all()
    users = get_missing_sale_photo_users_for_interval(db, group.group_id, start, end, flow_key=flow_key)
    if not users:
        logger.info("Group %s all submitted for %s - %s. Silent.", group.group_id, start, end)
        return 0

    users = record_missing_users_and_attach_today_count(db, users, group.group_id, start, end)
    # Send one escalation when the seventh daily miss is recorded. Later
    # windows do not spam the audit recipient for the same user/day.
    escalated = [user for user in users if int(getattr(user, "miss_count_today", 0) or 0) == 7]
    if escalated:
        lines = ["⚠️ <b>More than 6 missing submissions</b>"]
        for user in escalated:
            lines.append(
                "\n"
                f"Name: <b>{escape(user.name)}</b>\n"
                f"Telegram ID: <code>{user.telegram_user_id}</code>\n"
                f"Region: {escape(user.region or '')}\n"
                f"Dealer: {escape(user.dealer or '')}\n"
                f"Group: {escape(group.group_name)}\n"
                f"Missing Count: <b>{user.miss_count_today}</b>\n"
                f"{escape(controller_label_for_group(group.group_name))}"
            )
        audit_text = "\n".join(lines)
        for recipient in EXPORT_AUDIT_RECIPIENTS:
            try:
                await application.bot.send_message(recipient.user_id, audit_text, parse_mode="HTML")
            except Exception:
                logger.exception("Failed >6-miss escalation recipient=%s", recipient.user_id)
    submitted_count = count_submitted_users_for_interval(db, group.group_id, start, end, flow_key=flow_key)
    total_users = submitted_count + len(users)

    if include_submitted:
        text = special_window_alert_message(
            users,
            settings.APP_TIMEZONE,
            start,
            end,
            group.group_name,
            title,
            submitted_count,
            total_users,
        )
    else:
        text = sale_photo_missing_alert_message(
            users,
            settings.APP_TIMEZONE,
            start,
            end,
            group.group_name,
            title=title,
            submitted_count=submitted_count,
            total_users=total_users,
        )

    if not text:
        return 0

    try:
        sent_message = await _send_with_retry(
            application,
            chat_id=group.group_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        attach_alert_message_id(
            db,
            group.group_id,
            start,
            end,
            [user.telegram_user_id for user in users],
            sent_message.message_id,
        )
        logger.info(
            "Alert sent to group %s missing=%s window=%s-%s message_id=%s",
            group.group_id,
            len(users),
            start,
            end,
            sent_message.message_id,
        )
        preferences = db.get(MonitoringPreferences, 1)
        controller_group_id = (preferences.controller_alert_group_id if preferences else -1004464440125)
        if (preferences is None or preferences.controller_alerts_enabled) and controller_group_id:
            controller_names = controller_label_for_group(group.group_name)
            summary = (f"⚠️ <b>Missing report · {escape(group.group_name or str(group.group_id))}</b>\n"
                       f"Controller: {escape(controller_names)}\n"
                       f"Window: {start:%d/%m/%Y %H:%M}–{end:%H:%M}\n"
                       f"Missing: <b>{len(users)}</b>\n")
            for user in users:
                line = f"\n• {escape(user.name)} · <code>{user.telegram_user_id}</code>"
                if len(summary) + len(line) > 3900:
                    summary += "\nMore members omitted from this message."
                    break
                summary += line
            topic_id = controller_topic_for_group(group.group_name)
            send_kwargs = dict(
                chat_id=controller_group_id,
                text=summary,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if topic_id is not None:
                send_kwargs["message_thread_id"] = topic_id
            try:
                await _send_with_retry(application, **send_kwargs)
            except NetworkError as net_exc:
                logger.warning(
                    "Controller alert copy skipped after retry group=%s destination=%s: %s",
                    group.group_id,
                    controller_group_id,
                    net_exc,
                )
            except Exception:
                logger.exception("Controller alert copy failed group=%s destination=%s", group.group_id, controller_group_id)
        return len(users)
    except Exception as exc:
        logger.exception("Failed to send alert to group %s: %s", group.group_id, exc)
        return -1


def _bot_break_active(db, preferences: MonitoringPreferences | None, now: datetime) -> bool:
    if not preferences:
        return False
    if not preferences.alerts_enabled:
        return True
    paused_until = getattr(preferences, "bot_paused_until", None)
    if paused_until is None:
        return False
    if paused_until > now:
        logger.info("Bot break active until %s. Skipping scheduled alerts.", paused_until)
        return True
    preferences.bot_paused_until = None
    preferences.bot_pause_reason = None
    db.commit()
    return False


def _prepare_scheduled_alerts_sync(run_hour: int, run_minute: int, now: datetime) -> list[dict]:
    db = SessionLocal()
    try:
        preferences = db.get(MonitoringPreferences, 1)
        if _bot_break_active(db, preferences, now):
            return []

        groups = get_active_groups(db)
        logger.info("Checking %s active groups for scheduled time %02d:%02d", len(groups), run_hour, run_minute)
        if not groups:
            return []

        flow_windows: list[tuple[str, object, datetime, datetime]] = []
        for flow in ALL_FLOWS:
            for window in windows_for_run_time(flow, run_hour, run_minute):
                start, end = window.interval(now)
                flow_windows.append((flow.key, window, start, end))
        if not flow_windows:
            return []

        min_grace_start = min(interval_start_with_grace(s) for _, _, s, _ in flow_windows)
        max_grace_end = max(interval_end_with_grace(e) for _, _, _, e in flow_windows)
        active_group_ids = [g.group_id for g in groups]

        all_users = (
            db.query(SAUser)
            .filter(
                SAUser.group_id.in_(active_group_ids),
                SAUser.is_from_list == 1,
                SAUser.region.isnot(None),
                SAUser.dealer.isnot(None),
                SAUser.position.isnot(None),
            )
            .order_by(SAUser.position.asc().nulls_last(), SAUser.name.asc())
            .all()
        )
        users_by_group: dict[int, list[SAUser]] = defaultdict(list)
        for u in all_users:
            users_by_group[u.group_id].append(u)

        # Only sync groups on-the-fly if they actually have active SAList members missing from sa_users.
        missing_groups = [g for g in groups if not users_by_group.get(g.group_id)]
        if missing_groups:
            active_list_groups = {
                normalize_group(r[0])
                for r in db.query(SAList.group_name).filter(SAList.is_deleted == 0, SAList.is_active == 1).all()
                if r[0]
            }
            for g in missing_groups:
                if normalize_group(g.group_name) not in active_list_groups:
                    continue
                sync_group_members_from_list(db, g.group_id, g.group_name)
                users_by_group[g.group_id] = (
                    db.query(SAUser)
                    .filter(
                        SAUser.group_id == g.group_id,
                        SAUser.is_from_list == 1,
                        SAUser.region.isnot(None),
                        SAUser.dealer.isnot(None),
                        SAUser.position.isnot(None),
                    )
                    .order_by(SAUser.position.asc().nulls_last(), SAUser.name.asc())
                    .all()
                )

        all_reports = (
            db.query(SalePhotoReport)
            .filter(
                SalePhotoReport.group_id.in_(active_group_ids),
                SalePhotoReport.submitted_at >= min_grace_start,
                SalePhotoReport.submitted_at <= max_grace_end,
            )
            .all()
        )
        all_pins = (
            db.query(PinHistory)
            .filter(
                PinHistory.group_id.in_(active_group_ids),
                PinHistory.pinned_at >= min_grace_start,
                PinHistory.pinned_at <= max_grace_end,
            )
            .all()
        )
        reports_by_group: dict[int, list[SalePhotoReport]] = defaultdict(list)
        for rep in all_reports:
            reports_by_group[rep.group_id].append(rep)
        pins_by_group: dict[int, list[PinHistory]] = defaultdict(list)
        for pin in all_pins:
            pins_by_group[pin.group_id].append(pin)

        candidates: list[tuple] = []
        insert_rows: list[dict] = []
        missing_pairs: set[tuple[int, int]] = set()
        recorded_now = local_now()

        for group in groups:
            g_users = users_by_group.get(group.group_id, [])
            g_reports = reports_by_group.get(group.group_id, [])
            g_pins = pins_by_group.get(group.group_id, [])

            for flow_key, window, start, end in flow_windows:
                eligible = [
                    u for u in g_users
                    if u.flow_key == flow_key
                    and (u.alert_active_from is None or u.alert_active_from <= start)
                    and (
                        u.alert_paused_until is None
                        or u.alert_paused_until < start
                        or (u.alert_pause_start is not None and u.alert_pause_start > end)
                    )
                ]
                if not eligible:
                    continue

                g_start = interval_start_with_grace(start)
                g_end = interval_end_with_grace(end)
                w_reports = [r for r in g_reports if g_start <= r.submitted_at <= g_end]
                w_pins = [p for p in g_pins if g_start <= p.pinned_at <= g_end]
                submitted_ids = _resolve_submitted_user_ids(eligible, w_reports, w_pins)
                missing = [u for u in eligible if u.id not in submitted_ids]
                if not missing:
                    logger.info("Group %s all submitted for %s - %s. Silent.", group.group_id, start, end)
                    continue

                for u in missing:
                    insert_rows.append(
                        dict(
                            telegram_user_id=u.telegram_user_id,
                            group_id=group.group_id,
                            group_name=u.group_name,
                            name=u.name,
                            username=u.username,
                            region=u.region,
                            dealer=u.dealer,
                            position=u.position,
                            missed_at=recorded_now,
                            window_start=start,
                            window_end=end,
                        )
                    )
                    missing_pairs.add((group.group_id, u.telegram_user_id))

                candidates.append((group, window, start, end, missing, len(submitted_ids)))

        if not candidates:
            return []

        if insert_rows:
            stmt = (
                insert(MissingSalePhotoAlert)
                .values(insert_rows)
                .on_conflict_do_nothing(constraint="uq_missing_sale_photo_user_window")
            )
            db.execute(stmt)
            db.commit()

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        count_rows = (
            db.query(
                MissingSalePhotoAlert.group_id,
                MissingSalePhotoAlert.telegram_user_id,
                func.count(MissingSalePhotoAlert.id),
            )
            .filter(
                MissingSalePhotoAlert.group_id.in_([gid for gid, _ in missing_pairs]),
                MissingSalePhotoAlert.missed_at >= today_start,
                MissingSalePhotoAlert.missed_at < tomorrow_start,
            )
            .group_by(MissingSalePhotoAlert.group_id, MissingSalePhotoAlert.telegram_user_id)
            .all()
        )
        today_counts = {(gid, tid): int(cnt or 0) for gid, tid, cnt in count_rows}

        controller_group_id = (
            preferences.controller_alert_group_id if preferences else -1004464440125
        )
        send_controller_copies = bool(
            (preferences is None or preferences.controller_alerts_enabled) and controller_group_id
        )

        tasks: list[dict] = []
        for group, window, start, end, missing, submitted_count in candidates:
            for u in missing:
                u.miss_count_today = today_counts.get((group.group_id, u.telegram_user_id), 0)
            total_users = submitted_count + len(missing)

            if window.include_submitted:
                text = special_window_alert_message(
                    missing,
                    settings.APP_TIMEZONE,
                    start,
                    end,
                    group.group_name,
                    window.title,
                    submitted_count,
                    total_users,
                )
            else:
                text = sale_photo_missing_alert_message(
                    missing,
                    settings.APP_TIMEZONE,
                    start,
                    end,
                    group.group_name,
                    title=window.title,
                    submitted_count=submitted_count,
                    total_users=total_users,
                )
            if not text:
                continue

            audit_text = None
            escalated = [u for u in missing if int(getattr(u, "miss_count_today", 0) or 0) == 7]
            if escalated and EXPORT_AUDIT_RECIPIENTS:
                lines = ["⚠️ <b>More than 6 missing submissions</b>"]
                for u in escalated:
                    lines.append(
                        "\n"
                        f"Name: <b>{escape(u.name)}</b>\n"
                        f"Telegram ID: <code>{u.telegram_user_id}</code>\n"
                        f"Region: {escape(u.region or '')}\n"
                        f"Dealer: {escape(u.dealer or '')}\n"
                        f"Group: {escape(group.group_name)}\n"
                        f"Missing Count: <b>{u.miss_count_today}</b>\n"
                        f"{escape(controller_label_for_group(group.group_name))}"
                    )
                audit_text = "\n".join(lines)

            ctrl_kwargs = None
            if send_controller_copies:
                controller_names = controller_label_for_group(group.group_name)
                summary = (
                    f"⚠️ <b>Missing report · {escape(group.group_name or str(group.group_id))}</b>\n"
                    f"Controller: {escape(controller_names)}\n"
                    f"Window: {start:%d/%m/%Y %H:%M}–{end:%H:%M}\n"
                    f"Missing: <b>{len(missing)}</b>\n"
                )
                for u in missing:
                    line = f"\n• {escape(u.name)} · <code>{u.telegram_user_id}</code>"
                    if len(summary) + len(line) > 3900:
                        summary += "\nMore members omitted from this message."
                        break
                    summary += line
                topic_id = controller_topic_for_group(group.group_name)
                ctrl_kwargs = dict(
                    chat_id=controller_group_id,
                    text=summary,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                if topic_id is not None:
                    ctrl_kwargs["message_thread_id"] = topic_id

            tasks.append(
                dict(
                    group_id=group.group_id,
                    start=start,
                    end=end,
                    text=text,
                    user_ids=[u.telegram_user_id for u in missing],
                    audit_text=audit_text,
                    ctrl_kwargs=ctrl_kwargs,
                )
            )
        return tasks
    finally:
        db.close()


def _attach_alert_message_ids_batch(attachments: list[tuple[int, datetime, datetime, list[int], int]]) -> None:
    if not attachments:
        return
    db = SessionLocal()
    try:
        for group_id, start, end, user_ids, message_id in attachments:
            if not user_ids:
                continue
            db.execute(
                update(MissingSalePhotoAlert)
                .where(
                    MissingSalePhotoAlert.group_id == group_id,
                    MissingSalePhotoAlert.window_start == start,
                    MissingSalePhotoAlert.window_end == end,
                    MissingSalePhotoAlert.telegram_user_id.in_(user_ids),
                )
                .values(alert_message_id=message_id)
            )
        db.commit()
    finally:
        db.close()


def _batch_controller_copies(items: list[dict]) -> list[dict]:
    """Merge controller copies per (chat_id, topic_id) to stay well under Telegram's 20 msg/min group limit."""
    grouped: dict[tuple[int, int | None], list[str]] = defaultdict(list)
    for item in items:
        ck = item.get("ctrl_kwargs")
        if not ck:
            continue
        grouped[(ck["chat_id"], ck.get("message_thread_id"))].append(ck["text"])

    batches: list[dict] = []
    for (chat_id, thread_id), texts in grouped.items():
        current = ""
        for entry in texts:
            sep = "\n\n" if current else ""
            if current and len(current) + len(sep) + len(entry) > 3800:
                kwargs = dict(chat_id=chat_id, text=current, parse_mode="HTML", disable_web_page_preview=True)
                if thread_id is not None:
                    kwargs["message_thread_id"] = thread_id
                batches.append(kwargs)
                current = entry
            else:
                current = f"{current}{sep}{entry}"
        if current:
            kwargs = dict(chat_id=chat_id, text=current, parse_mode="HTML", disable_web_page_preview=True)
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id
            batches.append(kwargs)
    return batches


async def _deliver_controller_copies(application: Application, ctrl_batches: list[dict], audit_texts: list[str]) -> None:
    for audit_text in audit_texts:
        for recipient in EXPORT_AUDIT_RECIPIENTS:
            try:
                await _send_with_retry(
                    application,
                    chat_id=recipient.user_id,
                    text=audit_text,
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed >6-miss escalation recipient=%s", recipient.user_id)

    for idx, send_kwargs in enumerate(ctrl_batches):
        if idx > 0:
            await asyncio.sleep(0.5)
        try:
            await _send_with_retry(application, **send_kwargs)
        except NetworkError as net_exc:
            logger.warning(
                "Controller alert copy skipped after retry destination=%s topic=%s: %s",
                send_kwargs["chat_id"],
                send_kwargs.get("message_thread_id"),
                net_exc,
            )
        except Exception:
            logger.exception(
                "Controller alert copy failed destination=%s topic=%s",
                send_kwargs["chat_id"],
                send_kwargs.get("message_thread_id"),
            )


async def send_alert_job(application: Application, run_hour: int | None = None, run_minute: int | None = None) -> None:
    scheduled_run = run_hour is not None and run_minute is not None
    updater = getattr(application, "updater", None)
    if scheduled_run and (
        not bool(getattr(application, "running", False))
        or updater is None
        or not bool(getattr(updater, "running", False))
    ):
        logger.error(
            "Scheduled alert skipped: Telegram polling is not healthy "
            "(application_running=%s updater_running=%s)",
            bool(getattr(application, "running", False)),
            bool(getattr(updater, "running", False)) if updater is not None else False,
        )
        return
    now = local_now()
    if not is_work_day(now):
        logger.info("Non-work day. Skip alert.")
        return

    run_hour = now.hour if run_hour is None else run_hour
    run_minute = now.minute if run_minute is None else run_minute

    if scheduled_run and settings.ALERT_SETTLE_SECONDS > 0:
        await asyncio.sleep(float(settings.ALERT_SETTLE_SECONDS))
        now = local_now()

    t0 = time.monotonic()
    tasks = await asyncio.to_thread(_prepare_scheduled_alerts_sync, run_hour, run_minute, now)
    if not tasks:
        return
    t_prep = time.monotonic() - t0

    # Deliver ALL SA group alerts first without blocking on SA_Controll rate limits.
    sem = asyncio.Semaphore(12)
    attachments: list[tuple[int, datetime, datetime, list[int], int]] = []

    async def _deliver_group(item: dict) -> None:
        async with sem:
            try:
                sent_message = await _send_with_retry(
                    application,
                    chat_id=item["group_id"],
                    text=item["text"],
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                attachments.append(
                    (
                        item["group_id"],
                        item["start"],
                        item["end"],
                        item["user_ids"],
                        sent_message.message_id,
                    )
                )
                logger.info(
                    "Alert sent to group %s missing=%s window=%s-%s message_id=%s",
                    item["group_id"],
                    len(item["user_ids"]),
                    item["start"],
                    item["end"],
                    sent_message.message_id,
                )
            except Exception as exc:
                logger.exception("Failed to send alert to group %s: %s", item["group_id"], exc)

    t_send_start = time.monotonic()
    await asyncio.gather(*(_deliver_group(item) for item in tasks))
    t_send = time.monotonic() - t_send_start
    logger.info(
        "Scheduled alert cycle %02d:%02d prepared=%s groups in %.2fs, delivered=%s in %.2fs",
        run_hour,
        run_minute,
        len(tasks),
        t_prep,
        len(attachments),
        t_send,
    )

    if attachments:
        await asyncio.to_thread(_attach_alert_message_ids_batch, attachments)

    ctrl_batches = _batch_controller_copies(tasks)
    audit_texts = [item["audit_text"] for item in tasks if item.get("audit_text")]
    if ctrl_batches or audit_texts:
        asyncio.create_task(_deliver_controller_copies(application, ctrl_batches, audit_texts))


async def send_special_window_check_job(
    application: Application,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    message: str,
    min_required: int = 1,
) -> None:
    """
    Backward-compatible function kept for old imports/custom jobs.
    New code uses send_alert_job with flow-based windows.
    """
    now = local_now()
    if not is_work_day(now):
        logger.info("Non-work day. Skip special window check.")
        return

    db = SessionLocal()
    try:
        preferences = db.get(MonitoringPreferences, 1)
        if _bot_break_active(db, preferences, now):
            return
        groups = get_active_groups(db)
        for group in groups:
            start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
            end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
            await _send_missing_alert_for_group_window(
                application,
                db,
                group,
                start,
                end,
                message,
                include_submitted=True,
            )
    finally:
        db.close()


async def daily_reset_job() -> None:
    db = SessionLocal()
    try:
        count = reset_locations(db)
        logger.info("Daily reset completed for %s user-group rows.", count)
    finally:
        db.close()
