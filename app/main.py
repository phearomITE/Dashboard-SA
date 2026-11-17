# app/main.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import httpx
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from app.bot.handlers import (
    alert_now,
    add_sa,
    annual_leave,
    bind_group_member_activity,
    bind_sa,
    break_bot_1h,
    break_bot_2h,
    chatid,
    change_management,
    delete_pin,
    export_missing,
    export_sa_list,
    myid,
    pin,
    remove_sa,
    restore_sa,
    resume_bot,
    receive_location,
    receive_sale_photo,
    receive_sale_text,
    receive_missing_reason_reply,
    register_topic,
    reset_today,
    setup_group,
    start,
    status,
    sync_sa_list,
    test_afternoon,
    test_alert,
    test_alert_all_time,
)
from app.core.config import get_settings
from app.core.logger import get_logger, setup_logger
from app.data.sa_members import SA_MEMBERS
from app.dashboard.runtime import dashboard_command, start_dashboard, stop_dashboard
from app.database.database import (
    SessionLocal,
    acquire_runtime_lock,
    init_db,
    release_runtime_lock,
)
from app.database.migrations import add_missing_columns
from app.scheduler.jobs import (
    daily_reset_job,
    local_now,
    regular_run_times_for_today,
    send_alert_job,
)
from app.services.user_service import load_controller_overrides, seed_sa_list
from app.services.roster_migration import migrate_legacy_roster_positions

settings = get_settings()
logger = get_logger(__name__)
BUILD_ID = "20261105-controller-sa-start-menu-v23"




async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log handler failures without sending unwanted error replies into chats."""
    if update is None and isinstance(context.error, NetworkError):
        logger.info("Transient Telegram polling network reconnect: %s", context.error)
        return
    if update is None and isinstance(context.error, Conflict):
        logger.warning("Telegram polling conflict (rolling deploy overlap): %s", context.error)
        return
    logger.exception("Telegram update failed", exc_info=context.error)
    if context.error is not None:
        with trace.get_tracer("sa-alert-bot").start_as_current_span("telegram.update.error") as span:
            span.record_exception(context.error)
            span.set_status(Status(StatusCode.ERROR))


# Telegram normally supplies a bot-command entity, but some Desktop/mobile
# clients, copied commands, migrated supergroups and forum-topic messages have
# been observed arriving as ordinary text.  Route every management command from
# its text as the FIRST handler group so /AL (including uppercase spelling) and
# the other controller commands cannot silently fall through to sale-form text
# handling.  The regular CommandHandlers below remain as a second safety net;
# claim_command() makes the callbacks idempotent for the same update.
ALL_COMMANDS = {
    "dashboard": dashboard_command,
    "start": start,
    "myid": myid,
    "chatid": chatid,
    "bind_sa": bind_sa,
    "export": export_missing,
    "export_sa_list": export_sa_list,
    "remove": remove_sa,
    "restore": restore_sa,
    "al": annual_leave,
    "add": add_sa,
    "change": change_management,
    "pin": pin,
    "delete_pin": delete_pin,
    "status": status,
    "alert_now": alert_now,
    "reset_today": reset_today,
    "register_topic": register_topic,
    "setup_group": setup_group,
    "sync_sa_list": sync_sa_list,
    "test_afternoon": test_afternoon,
    "test_alert": test_alert,
    "test_alert_all_time": test_alert_all_time,
    "break_bot_1h": break_bot_1h,
    "break_bot_2h": break_bot_2h,
    "resume_bot": resume_bot,
}


async def route_management_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route every known slash command even when Telegram omits its command entity."""
    message = update.effective_message
    text = (getattr(message, "text", None) or "").strip() if message else ""
    if not text.startswith("/"):
        return

    token = text.split(maxsplit=1)[0][1:].split("@", 1)[0].casefold()
    callback = ALL_COMMANDS.get(token)
    if callback is not None:
        sender = update.effective_user
        logger.info(
            "Command received command=%s user_id=%s username=%s chat_id=%s chat_type=%s",
            token,
            getattr(sender, "id", None),
            getattr(sender, "username", None),
            getattr(update.effective_chat, "id", None),
            getattr(update.effective_chat, "type", None),
        )
        # The early router stops later handler groups. Refresh/bind the sender
        # here as well so a profile change is learned even when the user's next
        # group activity is a command rather than a form, photo, GPS, or text.
        await bind_group_member_activity(update, context)
        await callback(update, context)
        # The fallback owns this update. Do not execute the regular
        # CommandHandler again in a later handler group.
        raise ApplicationHandlerStop


def create_bot() -> Application:
    bot_request = HTTPXRequest(
        connection_pool_size=32,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        httpx_kwargs={"limits": httpx.Limits(max_connections=32, max_keepalive_connections=0)},
    )
    polling_request = HTTPXRequest(
        connection_pool_size=4,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
        httpx_kwargs={"limits": httpx.Limits(max_connections=4, max_keepalive_connections=0)},
    )
    app = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .request(bot_request)
        .get_updates_request(polling_request)
        .post_init(start_dashboard)
        .post_shutdown(stop_dashboard)
        .build()
    )

    # Use one early text router independent of Telegram's bot-command entity.
    # It covers every registered command, private chats, groups, edited
    # commands, /AL capitalization and commands addressed with @BotUsername.
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(r"^\s*/[A-Za-z0-9_]+(?:@\w+)?(?:\s|$)"),
            route_management_command,
        ),
        group=-10,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("bind_sa", bind_sa))
    app.add_handler(CommandHandler("add", add_sa))
    app.add_handler(CommandHandler("change", change_management))
    app.add_handler(CommandHandler("al", annual_leave))
    app.add_handler(CommandHandler("remove", remove_sa))
    app.add_handler(CommandHandler("restore", restore_sa))
    app.add_handler(CommandHandler("export", export_missing))
    app.add_handler(CommandHandler("export_sa_list", export_sa_list))
    app.add_handler(CommandHandler("pin", pin))
    app.add_handler(CommandHandler("delete_pin", delete_pin))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("alert_now", alert_now))
    app.add_handler(CommandHandler("reset_today", reset_today))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("register_topic", register_topic))
    app.add_handler(CommandHandler("setup_group", setup_group))
    app.add_handler(CommandHandler("sync_sa_list", sync_sa_list))
    app.add_handler(CommandHandler("test_afternoon", test_afternoon))
    app.add_handler(CommandHandler("test_alert", test_alert))
    app.add_handler(CommandHandler("test_alert_all_time", test_alert_all_time))
    app.add_handler(CommandHandler("break_bot_1h", break_bot_1h))
    app.add_handler(CommandHandler("break_bot_2h", break_bot_2h))
    app.add_handler(CommandHandler("resume_bot", resume_bot))
    # Group -1 records typed text and photo-caption reasons. Voice/audio is not
    # processed, downloaded, transcribed, or stored by this build.
    # A photo is included so its caption can be stored as the reason when the
    # official user replies directly to the exact missing-alert message.
    app.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.PHOTO,
            receive_missing_reason_reply,
        ),
        group=-1,
    )

    app.add_handler(MessageHandler(filters.LOCATION, receive_location))
    app.add_handler(MessageHandler(filters.PHOTO, receive_sale_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sale_text))

    # Run after normal handlers for every group message. This learns each official
    # SA's real Telegram ID so missing alerts can create a true blue mention.
    app.add_handler(
        MessageHandler(filters.ALL, bind_group_member_activity),
        group=1,
    )
    app.add_error_handler(error_handler)

    return app


def add_regular_tracking_jobs(scheduler: AsyncIOScheduler, app: Application) -> None:
    """
    Use Cron jobs for each regular check time.
    This keeps morning and afternoon windows aligned exactly with .env.
    """
    now = local_now()
    run_times = regular_run_times_for_today(now)

    for run_time in run_times:
        job_id = f"regular_check_{run_time.hour:02d}_{run_time.minute:02d}"

        scheduler.add_job(
            send_alert_job,
            trigger=CronTrigger(
                day_of_week=settings.REMINDER_DAYS,
                hour=run_time.hour,
                minute=run_time.minute,
                # Use the final second of the displayed minute. send_alert_job
                # then waits ALERT_SETTLE_SECONDS before reading PostgreSQL.
                second=59,
                timezone=settings.APP_TIMEZONE,
            ),
            args=[app, run_time.hour, run_time.minute],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    logger.info(
        "Regular tracking jobs scheduled: %s",
        ", ".join(t.strftime("%H:%M") for t in run_times),
    )




def start_scheduler(app: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.APP_TIMEZONE)

    add_regular_tracking_jobs(scheduler, app)

    scheduler.add_job(
        daily_reset_job,
        trigger=CronTrigger(
            hour=0,
            minute=1,
            timezone=settings.APP_TIMEZONE,
        ),
        id="daily_reset_location_all_groups",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    scheduler.start()
    return scheduler


def main() -> None:
    setup_logger()
    from app.core.telemetry import configure_telemetry
    configure_telemetry()
    if not acquire_runtime_lock():
        raise RuntimeError(
            "Another Track_SA_alert instance is already using this PostgreSQL "
            "database. Stop the old python/app.main process before starting a new one."
        )
    scheduler = None
    try:
        init_db()
        add_missing_columns()

        db = SessionLocal()
        try:
            from app.database.models import AppliedMaster, SAList
            if not db.get(AppliedMaster, "schema-2026-11-16"):
                from app.dashboard.dealer_import import seed_dealer_stations
                seed_dealer_stations(db)
                from app.services.master_update import apply_master
                apply_master(db)
                seed_sa_list(db)
                migrate_legacy_roster_positions(db)
                db.merge(AppliedMaster(version="schema-2026-11-16"))
                db.commit()
            elif SA_MEMBERS:
                seed_sa_list(db)
            load_controller_overrides(db)
            active_rows = db.query(SAList.group_name).filter(SAList.is_deleted == 0, SAList.is_active == 1).all()
            db_group_count = len({r[0] for r in active_rows if r[0]})
            db_user_count = len(active_rows)
        finally:
            db.close()

        app = create_bot()
        scheduler = start_scheduler(app)

        logger.info(
            "Bot started. build=%s timezone=%s database_lock=acquired",
            BUILD_ID,
            settings.APP_TIMEZONE,
        )
        group_count = len({member["group_name"] for member in SA_MEMBERS}) or db_group_count
        user_count = len(SA_MEMBERS) or db_user_count
        logger.info(
            "%s-group sale-photo alert bot active with %s official users. Work days=%s, interval=%s minutes",
            group_count,
            user_count,
            settings.WORK_DAYS,
            settings.ALERT_INTERVAL_MINUTES,
        )

        app.run_polling(
            # Edited sale forms/captions are common in the field. If edited_message
            # is excluded, a user can visibly have a completed form + pin in
            # Telegram while the database never receives the final form text.
            # That was a major source of false missing mentions.
            allowed_updates=["message", "edited_message"],
            # Do not discard forms/pins received while the bot briefly restarts.
            drop_pending_updates=False,
        )
    finally:
        if scheduler is not None and scheduler.running:
            try:
                scheduler.shutdown(wait=False)
                logger.info("Scheduler shutdown complete")
            except Exception:
                logger.exception("Scheduler shutdown failed")
        release_runtime_lock()
        logger.info("Database runtime lock released; bot shutdown complete")


if __name__ == "__main__":
    main()
