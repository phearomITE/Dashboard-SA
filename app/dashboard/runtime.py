import asyncio
import logging
import socket
import threading
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
    if not settings.DASHBOARD_AUTH_REQUIRED:
        await update.effective_message.reply_text("Dashboard sign-in is disabled for this test. Open your dashboard link directly.")
        return
    try:
        code = access.issue(update.effective_user.id)
    except ValueError:
        await update.effective_message.reply_text("Dashboard access is limited to management administrators.")
        return
    await update.effective_message.reply_text(
        f"Fieldline dashboard sign-in\n\n{code}\n\nOpen your dashboard link and enter this code within 5 minutes.",
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
    port = settings.PORT or settings.DASHBOARD_PORT
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((settings.DASHBOARD_HOST, port))
        bot_loop = asyncio.get_running_loop()
        server = DashboardServer(uvicorn.Config(create_api(access, SessionLocal, application,
            auth_required=settings.DASHBOARD_AUTH_REQUIRED, bot_loop=bot_loop),
            host=settings.DASHBOARD_HOST, port=port, log_config=None, access_log=False))

        def _serve():
            asyncio.run(server.serve(sockets=[sock]))

        thread = threading.Thread(target=_serve, name="dashboard-uvicorn", daemon=True)
        thread.start()
        application.bot_data["dashboard_server"] = (server, thread, sock)
    except OSError:
        sock.close()
        logger.exception("Dashboard listener unavailable. Check DASHBOARD_HOST and DASHBOARD_PORT.")


async def stop_dashboard(application):
    handle = application.bot_data.pop("dashboard_server", None)
    if handle:
        server, thread, sock = handle
        server.should_exit = True
        try:
            await asyncio.to_thread(thread.join, 5.0)
        finally:
            sock.close()
