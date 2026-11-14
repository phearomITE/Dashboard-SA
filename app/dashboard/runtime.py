import asyncio
import logging
import socket
from contextlib import nullcontext

import uvicorn

from app.core.config import get_settings
from app.dashboard.access import DashboardAccess
from app.dashboard.api import create_api
from app.database.database import SessionLocal

access = DashboardAccess()
logger = logging.getLogger(__name__)


async def dashboard_command(update, context):
    settings = get_settings()
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Open a private chat with me and send /dashboard to sign in.")
        return
    if not settings.DASHBOARD_ENABLED:
        await update.effective_message.reply_text("The dashboard is disabled on this deployment.")
        return
    if "dashboard_server" not in context.application.bot_data:
        await update.effective_message.reply_text("The dashboard listener is unavailable. Check the bot startup log.")
        return
    try:
        code = access.issue(update.effective_user.id)
    except ValueError:
        await update.effective_message.reply_text("Dashboard access is limited to management administrators.")
        return
    await update.effective_message.reply_text(
        f"Fieldline dashboard sign-in\n\n{code}\n\nOpen http://127.0.0.1:8080/ on the computer running the bot and enter this code within 5 minutes.",
        protect_content=True,
    )


class DashboardServer(uvicorn.Server):
    # Telegram owns the process signal handlers and event loop.
    def capture_signals(self):
        return nullcontext()


async def start_dashboard(application):
    settings = get_settings()
    if not settings.DASHBOARD_ENABLED:
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((settings.DASHBOARD_HOST, settings.DASHBOARD_PORT))
        server = DashboardServer(uvicorn.Config(create_api(access, SessionLocal, application,
            auth_required=settings.DASHBOARD_AUTH_REQUIRED),
            host=settings.DASHBOARD_HOST, port=settings.DASHBOARD_PORT, log_config=None, access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        application.bot_data["dashboard_server"] = (server, task, sock)
    except OSError:
        sock.close()
        logger.exception("Dashboard listener unavailable. Check DASHBOARD_HOST and DASHBOARD_PORT.")


async def stop_dashboard(application):
    handle = application.bot_data.pop("dashboard_server", None)
    if handle:
        server, task, sock = handle
        server.should_exit = True
        try:
            await task
        finally:
            sock.close()
