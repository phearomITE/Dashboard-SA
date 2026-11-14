# app/scheduler/jobs.py
import asyncio
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram.ext import Application

from app.core.config import get_settings
from app.core.logger import get_logger
from app.data.controllers import EXPORT_AUDIT_RECIPIENTS, controller_label_for_group
from app.database.database import SessionLocal
from app.database.models import MonitoringPreferences
from app.services.formatter import sale_photo_missing_alert_message, special_window_alert_message
from app.services.shift_config import (
    ALL_FLOWS,
    all_unique_run_times,
    previous_window_for_now,
    windows_for_run_time,
)
from app.services.user_service import (
    count_submitted_users_for_interval,
    get_active_groups,
    get_missing_sale_photo_users_for_interval,
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
        sent_message = await application.bot.send_message(
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
            try:
                await application.bot.send_message(chat_id=controller_group_id, text=summary,
                                                   parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                logger.exception("Controller alert copy failed group=%s destination=%s", group.group_id, controller_group_id)
        return len(users)
    except Exception as exc:
        logger.exception("Failed to send alert to group %s: %s", group.group_id, exc)
        return -1


async def send_alert_job(application: Application, run_hour: int | None = None, run_minute: int | None = None) -> None:
    scheduled_run = run_hour is not None and run_minute is not None
    updater = getattr(application, "updater", None)
    if scheduled_run and (
        not bool(getattr(application, "running", False))
        or updater is None
        or not bool(getattr(updater, "running", False))
    ):
        # Never let a scheduler-only/zombie instance send missing alerts. If
        # polling is not active, this process cannot have received the users'
        # forms and GPS updates and therefore has no safe basis for mentioning.
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

    # Cron passes the exact run time. /alert_now can call without args and uses each group's latest completed window.
    run_hour = now.hour if run_hour is None else run_hour
    run_minute = now.minute if run_minute is None else run_minute

    # Production race-condition guard: the displayed end minute is valid through
    # :59.  Cron fires at :59, then this short yield lets polling handlers commit
    # any form/pin that arrived in the final second before we calculate missing.
    if scheduled_run and settings.ALERT_SETTLE_SECONDS > 0:
        await asyncio.sleep(float(settings.ALERT_SETTLE_SECONDS))
        now = local_now()

    db = SessionLocal()
    try:
        preferences = db.get(MonitoringPreferences, 1)
        if preferences and not preferences.alerts_enabled:
            return
        groups = get_active_groups(db)
        logger.info("Checking %s active groups for scheduled time %02d:%02d", len(groups), run_hour, run_minute)

        for group in groups:
            # Keep DB users aligned with uploaded Excel list.
            sync_group_members_from_list(db, group.group_id, group.group_name)

            # One Telegram group can contain several working schedules, so
            # check every schedule flow separately at the current run time.
            for flow in ALL_FLOWS:
                windows = windows_for_run_time(flow, run_hour, run_minute)
                if not windows:
                    continue

                for window in windows:
                    start, end = window.interval(now)
                    await _send_missing_alert_for_group_window(
                        application,
                        db,
                        group,
                        start,
                        end,
                        window.title,
                        window.include_submitted,
                        flow_key=flow.key,
                    )
    finally:
        db.close()


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
        if preferences and not preferences.alerts_enabled:
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
