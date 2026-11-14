# app/services/formatter.py
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.data.controllers import Controller, controllers_for_group
from app.database.models import SAUser

settings = get_settings()


def controller_info_for_group(group_name: str | None = None) -> tuple[Controller, ...]:
    """Return every controller assigned to this region/group."""
    return controllers_for_group(group_name)


def _controller_mention(controller: Controller) -> str:
    name = escape(controller.name)
    username = (controller.username or "").lstrip("@").strip()
    if controller.user_id and controller.user_id > 0:
        if username:
            return f'{name} | <a href="tg://user?id={controller.user_id}">@{escape(username)}</a>'
        return f'<a href="tg://user?id={controller.user_id}">{name}</a>'
    if username:
        return f"{name} | @{escape(username)}"
    return name


def controller_label(group_name: str | None = None) -> str:
    controllers = controller_info_for_group(group_name)
    if len(controllers) == 1:
        return f"Controller: {_controller_mention(controllers[0])}"

    lines = ["Controllers:"]
    for index, controller in enumerate(controllers, 1):
        lines.append(f"{index}. {_controller_mention(controller)}")
    return "\n".join(lines)



def is_alert_paused(user: SAUser) -> bool:
    pause_start = getattr(user, "alert_pause_start", None)
    paused_until = getattr(user, "alert_paused_until", None)
    if paused_until is None:
        return False
    now = datetime.now(ZoneInfo(settings.APP_TIMEZONE)).replace(tzinfo=None)
    # Legacy full-day rows may not have alert_pause_start.
    return (pause_start is None or pause_start <= now) and now <= paused_until


def position_label(user: SAUser) -> str:
    """Return the real Excel position while keeping common labels clean."""
    if is_alert_paused(user) and (getattr(user, "alert_pause_reason", None) or "").upper().startswith("AL"):
        return "SA AL"

    raw = (getattr(user, "position", None) or getattr(user, "flow_key", None) or "SA").strip()
    key = raw.lower().replace("_", " ").replace("-", " ")

    if "team leader" in key or "leader" in key:
        return "SA Team Leader"
    if "commando" in key or "cammondo" in key:
        return "Commando"
    return "SA"


def user_mention(user: SAUser) -> str:
    """Return a reliable clickable Telegram mention for an SA user.

    Best case: use the real positive Telegram user ID. This works even when
    the user has no public username and is the same mention style used for
    controllers. Official Excel rows initially use negative placeholder IDs;
    after the SA sends any message in the group, the activity binder replaces
    the placeholder with the real Telegram ID.
    """
    telegram_user_id = int(getattr(user, "telegram_user_id", 0) or 0)
    username_raw = (getattr(user, "username", None) or "").lstrip("@").strip()
    username = escape(username_raw)

    if telegram_user_id > 0:
        visible = f"@{username}" if username else "Mention user"
        return f'<a href="tg://user?id={telegram_user_id}">{visible}</a>'

    if username_raw:
        # Fallback before the bot has learned the real numeric Telegram ID.
        # The URL is explicitly wrapped in an HTML anchor so Telegram renders it
        # as a blue, clickable username.
        return f'<a href="https://t.me/{username}">@{username}</a>'

    return "No username — ask user to send /bind_sa"


def user_label(user: SAUser) -> str:
    name = escape(user.name or "")
    return f"{name} | {user_mention(user)}"


def format_time(value) -> str:
    return "-" if not value else value.strftime("%I:%M %p")


def format_window_time(start, end) -> str:
    """Format windows like: 07:43 AM - 08:06 AM."""
    if not start or not end:
        return "-"
    return f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}"


def format_alert_date(value, timezone: str) -> str:
    """Format the alert-window date as DD/MM/YYYY in the configured timezone."""
    if value is None:
        value = datetime.now(ZoneInfo(timezone))
    elif getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(ZoneInfo(timezone))
    return value.strftime("%d/%m/%Y")


def sale_photo_missing_alert_message(
    users: list[SAUser],
    timezone: str,
    interval_start=None,
    interval_end=None,
    group_name: str | None = None,
    title: str = "➡️🔴 SA មិនទាន់បានទម្លាក់រូបភាពលក់",
    submitted_count: int | None = None,
    total_users: int | None = None,
) -> str:
    if not users:
        return ""

    lines = [escape(title), ""]
    alert_date_value = interval_start or interval_end
    lines.append(f"Date: {format_alert_date(alert_date_value, timezone)}")
    if group_name:
        lines.append(f"Group: {escape(group_name)}")
    if interval_start and interval_end:
        lines.append(f"Time Submitted: {format_window_time(interval_start, interval_end)}")

    # Business format: show only missing total in the alert message.
    lines.append(f"Total: {len(users)}")
    lines.append("")
    lines.append(controller_label(group_name))
    lines.append("")

    for i, user in enumerate(users, 1):
        miss_count = getattr(user, "miss_count_today", None) or 0
        lines.append(f"{i}. {position_label(user)} | {user_label(user)} : {miss_count}ដង")

    return "\n".join(lines)


def special_window_alert_message(
    users: list[SAUser],
    timezone: str,
    interval_start=None,
    interval_end=None,
    group_name: str | None = None,
    message: str = "",
    submitted_count: int | None = None,
    total_users: int | None = None,
) -> str:
    if not users:
        return ""

    lines = [escape(message or "➡️🔴 SA មិនទាន់បានទម្លាក់រូបភាពលក់"), ""]
    alert_date_value = interval_start or interval_end
    lines.append(f"Date: {format_alert_date(alert_date_value, timezone)}")
    if group_name:
        lines.append(f"Group: {escape(group_name)}")
    if interval_start and interval_end:
        lines.append(f"Time Submitted: {format_window_time(interval_start, interval_end)}")
    # Business format: show only missing total in the alert message.
    lines.append(f"Total: {len(users)}")
    lines.append("")
    lines.append(controller_label(group_name))
    lines.append("")

    for i, user in enumerate(users, 1):
        miss_count = getattr(user, "miss_count_today", None) or 0
        lines.append(f"{i}. {position_label(user)} | {user_label(user)} : {miss_count}ដង")

    return "\n".join(lines)


def status_message(users: list[SAUser], timezone: str, interval_start=None, interval_end=None) -> str:
    now = datetime.now(ZoneInfo(timezone)).strftime("%d %b %Y %I:%M %p")
    lines = ["📍 SA MAP PIN STATUS", f"Time: {now}", f"Total SA: {len(users)}"]

    if interval_start and interval_end:
        lines.append(f"Window: {interval_start.strftime('%I:%M %p')} - {interval_end.strftime('%I:%M %p')}")

    lines.append("")
    if not users:
        lines.append("No SA users registered in this group yet.")
        return "\n".join(lines)

    for i, user in enumerate(users, 1):
        if is_alert_paused(user):
            until = format_time(user.alert_paused_until)
            lines.append(f"{i}. 🟡 {position_label(user)} | {user_label(user)}")
            lines.append(f"   Alert paused until: {until}")
            continue

        ok = user.lat is not None and user.lon is not None and user.map_link and user.last_pin_at is not None
        if ok:
            lines.append(f"{i}. ✅ {user_label(user)}")
            lines.append(f"   Last pin: {format_time(user.last_pin_at)}")
            lines.append(f"   {escape(user.map_link)}")
        else:
            lines.append(f"{i}. ❌ {user_label(user)}")
            if user.last_pin_at:
                lines.append(f"   Last pin: {format_time(user.last_pin_at)}")

    return "\n".join(lines)
