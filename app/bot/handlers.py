# app/bot/handlers.py
from __future__ import annotations

import asyncio
from datetime import datetime
from html import escape
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import httpx

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.core.config import get_settings
from app.core.logger import get_logger
from app.data.controllers import (
    EXPORT_AUDIT_RECIPIENTS,
    GLOBAL_MANAGEMENT_USER_IDS,
    controller_user_ids_for_group,
    can_request_export_globally,
    can_manage_group,
    controllers_for_group,
    export_regions_for_group,
    region_from_group,
)
from app.database.database import SessionLocal
from app.database.models import PinHistory, SAList, SAUser, VoiceReasonHistory
from app.services.export_service import build_missing_export_rows, create_missing_export_workbook, create_sa_list_workbook
from app.services.formatter import controller_label, sale_photo_missing_alert_message, special_window_alert_message, status_message, user_label
from app.services.shift_config import ALL_FLOWS, previous_window_for_now
from app.services.time_service import (
    get_current_interval_end,
    get_current_interval_start,
    local_now,
)
from app.services.user_service import (
    add_controller_groups,
    add_manual_user,
    change_region_controller,
    change_user_position,
    change_user_working_time,
    bind_official_user_if_match,
    sync_bound_telegram_profile,
    sync_roster_profile_by_telegram_id,
    chat_name,
    count_submitted_users_for_interval,
    delete_my_location,
    find_official_users_by_query,
    get_all_users_by_group,
    get_missing_sale_photo_users_for_interval,
    is_group_chat_id,
    pause_alerts_for_period,
    pause_alerts_for_today,
    record_missing_users_and_attach_today_count,
    attach_alert_message_id,
    save_missing_reason_reply,
    save_latest_missing_reason,
    start_voice_reason_history,
    finish_voice_reason_history,
    remove_user_from_alerts,
    remove_region_controller,
    remove_controller_by_user_id,
    register_group,
    register_or_update_user,
    restore_user_to_alerts,
    resume_alerts,
    reset_locations,
    save_location,
    save_sale_photo_report,
    save_text_form_report,
    seed_sa_list,
    looks_like_sale_form,
    sync_group_members_from_list,
    telegram_name,
)

settings = get_settings()
logger = get_logger(__name__)


def location_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Pin Map Location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def is_private_chat(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.type == ChatType.PRIVATE


def permission_message(update: Update, message: str) -> str:
    """Show the immutable Telegram sender ID when an authorization check fails."""
    user = update.effective_user
    if user is None:
        return message
    username = f"@{user.username}" if user.username else "No username"
    return (
        f"{message}\n\n"
        f"Detected Telegram identity:\n"
        f"User ID: {user.id}\n"
        f"Username: {username}\n"
        "Global access requires a configured administrator User ID."
    )


def current_window(group_name: str | None = None):
    now = local_now()
    flow_window = previous_window_for_now(now, group_name)
    if flow_window:
        return flow_window.interval(now)

    return (
        get_current_interval_start(now, settings.ALERT_INTERVAL_MINUTES),
        get_current_interval_end(now, settings.ALERT_INTERVAL_MINUTES),
    )


def has_photo(message) -> bool:
    return bool(message and message.photo)


def get_caption(message) -> str:
    if not message:
        return ""
    return message.caption or message.text or ""


def has_sale_format_text(text: str) -> bool:
    return looks_like_sale_form(text)


async def safe_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    """Send a command response reliably in normal groups and forum topics.

    ``Message.reply_text`` can inherit an invalid/stale Telegram topic id and fail
    with ``BadRequest: Message thread not found``. Send through the bot directly,
    preserve a valid topic when available, and retry in the main group if Telegram
    rejects the thread.
    """
    chat = update.effective_chat
    message = update.effective_message
    if chat is None:
        return None

    thread_id = None
    if message is not None and getattr(message, "is_topic_message", False):
        thread_id = getattr(message, "message_thread_id", None)

    send_kwargs = dict(kwargs)
    send_kwargs.update({"chat_id": chat.id, "text": text})
    if thread_id is not None:
        send_kwargs["message_thread_id"] = thread_id

    try:
        return await context.bot.send_message(**send_kwargs)
    except BadRequest as exc:
        if "message thread not found" not in str(exc).lower():
            raise

        logger.warning(
            "Invalid Telegram thread for chat_id=%s thread_id=%s; retrying in main chat",
            chat.id,
            thread_id,
        )
        send_kwargs.pop("message_thread_id", None)
        return await context.bot.send_message(**send_kwargs)




def command_payload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Return text after the slash command for CommandHandler and regex fallback paths."""
    if getattr(context, "args", None):
        return " ".join(context.args).strip()
    message = update.effective_message
    text = (getattr(message, "text", None) or "").strip() if message else ""
    if not text:
        return ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str) -> bool:
    """Prevent duplicate execution when both CommandHandler and text fallback match."""
    update_id = getattr(update, "update_id", None)
    if update_id is None:
        return True
    claims = context.chat_data.setdefault("_management_command_claims", {})
    if claims.get(name) == update_id:
        return False
    claims[name] = update_id
    # Keep this tiny even in long-running groups.
    if len(claims) > 32:
        context.chat_data["_management_command_claims"] = {name: update_id}
    return True

async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat is None or update.effective_user is None:
        return False
    if int(update.effective_user.id) in GLOBAL_MANAGEMENT_USER_IDS:
        return True
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    except Exception:
        logger.exception("Could not check admin permission chat_id=%s user_id=%s", update.effective_chat.id, update.effective_user.id)
        return False
    return str(member.status).lower() in {"administrator", "creator", "owner"}


async def notify_group_controllers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    event_text: str,
    *,
    send_private: bool = True,
) -> tuple[int, int]:
    """Notify controllers assigned to the current group.

    The event is posted in the SA group with clickable controller mentions.
    The bot also tries to send the same event privately to each controller.
    Private delivery can only succeed after the controller has opened the bot
    and sent /start at least once. Failures are logged and do not fail the
    original /AL or /remove command.
    """
    if update.effective_chat is None:
        return 0, 0

    group_name = chat_name(update.effective_chat)
    controllers = controllers_for_group(group_name)
    if not controllers:
        logger.warning("No controller mapping found for group=%s", group_name)
        return 0, 0

    group_notice = (
        f"{event_text}\n\n"
        f"{controller_label(group_name)}"
    )
    await safe_reply(
        update,
        context,
        group_notice,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    private_sent = 0
    private_failed = 0
    if send_private:
        private_text = (
            "🔔 <b>SA Alert Management Update</b>\n\n"
            f"Group: <b>{escape(group_name)}</b>\n"
            f"{event_text}"
        )
        for controller in controllers:
            try:
                await context.bot.send_message(
                    chat_id=controller.user_id,
                    text=private_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                private_sent += 1
            except Exception:
                private_failed += 1
                logger.warning(
                    "Could not privately notify controller user_id=%s group=%s. "
                    "Controller may need to send /start to the bot first.",
                    controller.user_id,
                    group_name,
                    exc_info=True,
                )

    return private_sent, private_failed


async def can_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Allow exports in every SA group for admins, controllers, and audit users."""
    if update.effective_user is None or update.effective_chat is None:
        return False

    user_id = update.effective_user.id

    # Regional controllers and audit/reporting users can run /export from any SA group.
    # This avoids depending on Telegram admin lookups, which may occasionally fail.
    if can_request_export_globally(user_id):
        return True

    # Other Telegram group administrators may also export their own group's scope.
    return await is_group_admin(update, context)


def parse_export_date(args: list[str]) -> datetime.date | None:
    if not args:
        return local_now().date()
    raw = args[0].strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


async def bind_group_member_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bind an official Excel SA row to the sender's real Telegram user ID.

    This handler runs for every group message in handler group 1, after the
    normal command/photo/location handlers. It makes future missing alerts use
    a real ``tg://user?id=...`` mention, including for users with no username.
    """
    if update.effective_user is None or update.effective_chat is None:
        return
    if is_private_chat(update) or not is_group_chat_id(update.effective_chat.id):
        return
    if update.effective_user.is_bot:
        return

    db = SessionLocal()
    try:
        # Fast path: once a real Telegram ID is already bound, avoid a database
        # update/commit for every message in high-volume sales groups.
        existing = (
            db.query(SAUser)
            .filter(
                SAUser.group_id == update.effective_chat.id,
                SAUser.telegram_user_id == update.effective_user.id,
            )
            .first()
        )
        if (
            existing is not None
            and existing.is_from_list == 1
            and existing.region
            and existing.dealer
            and existing.position
        ):
            # Telegram user ID is stable. Refresh the live profile on every
            # message so renamed accounts and changed usernames are reflected
            # automatically in DB and future alert mentions.
            sync_bound_telegram_profile(
                db,
                telegram_user_id=update.effective_user.id,
                group_id=update.effective_chat.id,
                current_name=telegram_name(update.effective_user),
                current_username=update.effective_user.username,
            )
            return

        item = bind_official_user_if_match(
            db,
            update.effective_user.id,
            telegram_name(update.effective_user),
            update.effective_user.username,
            update.effective_chat.id,
            chat_name(update.effective_chat),
        )
        if item is not None and item.is_from_list == 1:
            logger.info(
                "Bound official SA mention group_id=%s telegram_user_id=%s name=%s username=%s",
                update.effective_chat.id,
                update.effective_user.id,
                item.name,
                item.username,
            )
    except Exception:
        # Mention binding must never interrupt the original message handler.
        logger.exception(
            "Could not bind Telegram member activity chat_id=%s user_id=%s",
            update.effective_chat.id,
            update.effective_user.id,
        )
    finally:
        db.close()


async def bind_sa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Let an SA bind their official row and preview the clickable mention."""
    if update.effective_user is None or update.effective_chat is None:
        return
    if is_private_chat(update):
        await safe_reply(update, context, "❌ Please send /bind_sa inside your official SA group.")
        return

    db = SessionLocal()
    try:
        item = bind_official_user_if_match(
            db,
            update.effective_user.id,
            telegram_name(update.effective_user),
            update.effective_user.username,
            update.effective_chat.id,
            chat_name(update.effective_chat),
        )
        is_official = item is not None and item.is_from_list == 1
        label = user_label(item) if item is not None else ""
    finally:
        db.close()

    if is_official:
        await safe_reply(
            update,
            context,
            "✅ SA mention linked successfully.\n"
            f"{label}\n\n"
            "Future missing alerts will mention this Telegram account.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await safe_reply(
            update,
            context,
            "❌ Your Telegram name/username was not matched to the official SA list "
            "for this group. Please check the Excel name or username, then run "
            "/sync_sa_list and /bind_sa again.",
        )



ALLOWED_ADD_POSITIONS = {
    "sa": "SA",
    "commando": "Commando",
    "sa team leader": "SA Team Leader",
}


async def add_sa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a manual SA or assign a controller to exact Region-Dealer groups."""
    if not claim_command(update, context, "add"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return
    raw = command_payload(update, context).strip()
    controller_match = re.fullmatch(r"controller\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|?", raw, re.I)
    if controller_match:
        if update.effective_user.id not in GLOBAL_MANAGEMENT_USER_IDS:
            await safe_reply(update, context, "❌ Only Ra Phearom can add a controller.")
            return
        controller_id = int(controller_match.group(1))
        group_codes = [item.strip() for item in controller_match.group(2).split(",") if item.strip()]
        try:
            profile = await context.bot.get_chat(controller_id)
            display_name = " ".join(filter(None, [getattr(profile, "first_name", None), getattr(profile, "last_name", None)])) or str(controller_id)
            username = getattr(profile, "username", None) or ""
        except Exception:
            display_name, username = f"Controller {controller_id}", ""
        db = SessionLocal()
        try:
            added, existing = add_controller_groups(db, user_id=controller_id, group_codes=group_codes,
                name=display_name, username=username, changed_by_user_id=update.effective_user.id)
        except ValueError as exc:
            await safe_reply(update, context, f"❌ {escape(str(exc))}")
            return
        finally:
            db.close()
        lines = ["✅ <b>Controller access updated</b>", f"Telegram ID: <code>{controller_id}</code>",
                 f"Controller: <b>{escape(display_name)}</b>"]
        if added:
            lines.append(f"Added groups: <b>{escape(', '.join(added))}</b>")
        if existing:
            lines.append(f"Already assigned: {escape(', '.join(existing))}")
        lines.append("The controller should open the bot privately and send /start for private alerts/exports.")
        await safe_reply(update, context, "\n".join(lines), parse_mode="HTML")
        return
    if is_private_chat(update):
        await safe_reply(update, context, "❌ /add must be used inside the target SA group.")
        return

    group_name = chat_name(update.effective_chat)
    if not can_manage_group(update.effective_user.id, group_name):
        await safe_reply(
            update,
            context,
            permission_message(update, "❌ Only Ra Phearom or the controller assigned to this group can use /add."),
        )
        return

    raw = command_payload(update, context)
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) != 4:
        await safe_reply(
            update,
            context,
            "Usage:\n"
            "/add <Telegram ID> | <Position> | <Dealer> | <Working time>\n\n"
            "Example:\n/add 1746266459 | SA | CA5 | 7:00am-5:00pm",
        )
        return

    user_id_raw, position_input, dealer, working_time = parts
    if not user_id_raw.isdigit() or int(user_id_raw) <= 0:
        await safe_reply(update, context, "❌ Telegram ID must be a positive numeric ID.")
        return
    telegram_user_id = int(user_id_raw)
    if not position_input or not dealer or not working_time:
        await safe_reply(
            update,
            context,
            "❌ Position, Dealer and Working time are required.",
        )
        return
    position_key = " ".join(position_input.lower().replace("_", " ").split())
    position = ALLOWED_ADD_POSITIONS.get(position_key)
    if position is None:
        await safe_reply(
            update,
            context,
            "❌ Invalid position. Use one of:\n"
            "SA | Commando | SA Team Leader",
        )
        return

    region = region_from_group(group_name)
    if not region:
        await safe_reply(update, context, f"❌ Could not detect a region from group: {group_name}")
        return

    # Telegram only returns a profile after that account has interacted with the
    # bot/shared chat. The numeric ID remains authoritative either way.
    try:
        profile = await context.bot.get_chat(telegram_user_id)
        name = " ".join(part for part in [profile.first_name, profile.last_name] if part).strip()
        username = profile.username or ""
    except Exception:
        name = f"Telegram User {telegram_user_id}"
        username = ""

    db = SessionLocal()
    try:
        item, created = add_manual_user(
            db,
            group_id=update.effective_chat.id,
            group_name=group_name,
            name=name,
            position=position,
            dealer=dealer,
            username=username,
            working_time=working_time,
            telegram_user_id=telegram_user_id,
        )
        result = {
            "name": item.name,
            "position": item.position or position,
            "region": item.region or region,
            "dealer": item.dealer or dealer.upper(),
            "group": item.group_name or group_name,
            "username": (item.username or "").lstrip("@"),
            "working_time": item.working_time or working_time,
            "source_type": item.source_type,
            "telegram_user_id": item.telegram_user_id,
        }
    except ValueError as exc:
        await safe_reply(update, context, f"❌ {escape(str(exc))}")
        return
    except Exception:
        logger.exception("/add failed for group %s", update.effective_chat.id)
        await safe_reply(update, context, "❌ Could not add the SA. Check the bot terminal log.")
        return
    finally:
        db.close()

    controller_name = telegram_name(update.effective_user)
    account = f"@{escape(result['username'])}" if result["username"] else "No username"
    binding_note = "Telegram ID is permanently linked; name and username refresh from future activity."
    action = "added" if created else "updated"
    await safe_reply(
        update,
        context,
        "✅ <b>SA added successfully</b>\n\n"
        f"Name: <b>{escape(result['name'])}</b>\n"
        f"Position: {escape(result['position'])}\n"
        f"Region: {escape(result['region'])}\n"
        f"Dealer: {escape(result['dealer'])}\n"
        f"Group: {escape(result['group'])}\n"
        f"Username: {account}\n"
        f"Working time: {escape(result['working_time'])}\n"
        f"Added by: <b>{escape(controller_name)}</b>\n\n"
        f"The user starts missing-alert monitoring tomorrow ({action}, manual source).\n"
        f"{escape(binding_note)}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def change_working_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Change one SA's working time and immediately move them to the matching alert flow.

    Supported syntax: /change working time <Telegram ID> | <New working time>
    """
    if not claim_command(update, context, "change"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return
    if is_private_chat(update):
        await safe_reply(update, context, "❌ /change working time must be used inside the target SA group.")
        return

    group_name = chat_name(update.effective_chat)
    if not can_manage_group(update.effective_user.id, group_name):
        await safe_reply(
            update,
            context,
            permission_message(update, "❌ Only Ra Phearom or the controller assigned to this group can change working time."),
        )
        return

    raw = command_payload(update, context)
    # Accept both 'working time' and 'working_time'. Username is optional.
    import re as _re
    prefix = _re.match(r"^working(?:\s+|_)time\s*\|?\s*(.*)$", raw, _re.I)
    fields = [part.strip() for part in prefix.group(1).split("|")] if prefix else []
    if len(fields) == 2 and fields[0].isdigit():
        user_id_raw, new_time = fields
        name = username = ""
    else:
        await safe_reply(
            update,
            context,
            "Usage:\n"
            "/change working time <Telegram ID> | <New working time>\n\n"
            "Example: /change working time 1746266459 | 7:30am-5:00pm\n\n"
            "Allowed times:\n"
            "6:30am-4:30pm | 7:00am-5:00pm | 7:30am-5:00pm | 9:00am-8:00pm",
        )
        return

    if not new_time:
        await safe_reply(update, context, "❌ Telegram ID and new working time are required.")
        return

    db = SessionLocal()
    try:
        item, old_time, canonical_time = change_user_working_time(
            db,
            group_id=update.effective_chat.id,
            name=name,
            username=username,
            new_working_time=new_time,
            telegram_user_id=int(user_id_raw),
        )
        result = {
            "name": item.name,
            "username": (item.username or item.official_username or "").lstrip("@"),
            "position": item.position or "",
            "old_time": old_time,
            "new_time": canonical_time,
            "flow_key": item.flow_key or "",
        }
    except ValueError as exc:
        await safe_reply(update, context, f"❌ {escape(str(exc))}")
        return
    except Exception:
        logger.exception("/change working time failed group_id=%s", update.effective_chat.id)
        await safe_reply(update, context, "❌ Could not change working time. Check the bot terminal log.")
        return
    finally:
        db.close()

    account = f"@{escape(result['username'])}" if result["username"] else "No username"
    changed_by = telegram_name(update.effective_user)
    await safe_reply(
        update,
        context,
        "✅ <b>Working time changed successfully</b>\n\n"
        f"Name: <b>{escape(result['name'])}</b>\n"
        f"Username: {account}\n"
        f"Position: {escape(result['position'])}\n"
        f"Old working time: <b>{escape(result['old_time'])}</b>\n"
        f"New working time: <b>{escape(result['new_time'])}</b>\n"
        f"New alert flow: <b>{escape(result['flow_key'])}</b>\n"
        f"Changed by: <b>{escape(changed_by)}</b>\n\n"
        "Future alert windows now follow the new working-time flow. "
        "The change will remain after /sync_sa_list.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def change_controller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Persistently replace one regional controller.

    /change controller | R8 | Name | @username | user_id
    /change controller | Name | @username | user_id   (inside target region)
    """
    if not claim_command(update, context, "change"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return
    raw = command_payload(update, context)
    import re as _re
    prefix = _re.match(r"^controller\s*\|\s*(.*)$", raw, _re.I)
    fields = [part.strip() for part in prefix.group(1).split("|")] if prefix else []
    if len(fields) == 4:
        region, name, username, user_id_raw = fields
        region = region.upper()
    elif len(fields) == 3 and not is_private_chat(update):
        name, username, user_id_raw = fields
        region = region_from_group(chat_name(update.effective_chat)) or ""
    else:
        await safe_reply(
            update,
            context,
            "Usage:\n"
            "/change controller | R8 | Example Controller | @example_controller | 123456789\n"
            "or inside the target regional group:\n"
            "/change controller | Example Controller | @example_controller | 123456789",
        )
        return
    if region not in {f"R{i}" for i in range(1, 9)}:
        await safe_reply(update, context, "❌ Region must be R1-R8.")
        return
    # Ra Phearom can change every region. The currently assigned controller may
    # change only their own region.
    if not can_manage_group(update.effective_user.id, f"{region}-SA-CONTROLLER"):
        await safe_reply(
            update,
            context,
            permission_message(update, "❌ Only Ra Phearom or the currently assigned controller for this region can change it."),
        )
        return
    try:
        user_id = int(user_id_raw)
    except ValueError:
        await safe_reply(update, context, "❌ Controller Telegram user ID must be a positive number.")
        return
    db = SessionLocal()
    try:
        controller = change_region_controller(
            db,
            region=region,
            name=name,
            username=username,
            user_id=user_id,
            changed_by_user_id=update.effective_user.id,
        )
    except ValueError as exc:
        await safe_reply(update, context, f"❌ {escape(str(exc))}")
        return
    finally:
        db.close()
    await safe_reply(
        update,
        context,
        "✅ <b>Regional controller changed</b>\n\n"
        f"Region: <b>{escape(region)}</b>\n"
        f"Controller: <b>{escape(controller.name)}</b>\n"
        f"Username: @{escape(controller.normalized_username)}\n"
        f"User ID: <code>{controller.user_id}</code>\n"
        f"Changed by: <b>{escape(telegram_name(update.effective_user))}</b>",
        parse_mode="HTML",
    )


async def change_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Change an SA position and persist it in SA_List."""
    if not claim_command(update, context, "change"):
        return
    if update.effective_user is None or update.effective_chat is None or is_private_chat(update):
        await safe_reply(update, context, "❌ /change position must be used inside the target SA group.")
        return
    group_name = chat_name(update.effective_chat)
    if not can_manage_group(update.effective_user.id, group_name):
        await safe_reply(update, context, permission_message(update, "❌ Only Ra Phearom or the assigned regional controller can change position."))
        return
    raw = command_payload(update, context)
    import re as _re
    prefix = _re.match(r"^position\s*\|\s*(.*)$", raw, _re.I)
    fields = [part.strip() for part in prefix.group(1).split("|")] if prefix else []
    if len(fields) == 2 and fields[0].isdigit():
        user_id_raw, new_position_raw = fields
        name = username = ""
    else:
        await safe_reply(
            update,
            context,
            "Usage: /change position | <Telegram ID> | <New position>",
        )
        return
    position_key = " ".join(new_position_raw.lower().replace("_", " ").split())
    new_position = ALLOWED_ADD_POSITIONS.get(position_key)
    if new_position is None:
        await safe_reply(
            update, context,
            "❌ Invalid position. Use SA | Commando | SA Team Leader",
        )
        return
    db = SessionLocal()
    try:
        item, old_position = change_user_position(
            db,
            group_id=update.effective_chat.id,
            name=name,
            username=username,
            new_position=new_position,
            telegram_user_id=int(user_id_raw),
        )
    except ValueError as exc:
        await safe_reply(update, context, f"❌ {escape(str(exc))}")
        return
    finally:
        db.close()
    await safe_reply(
        update,
        context,
        "✅ <b>Position changed successfully</b>\n\n"
        f"Name: <b>{escape(item.name)}</b>\n"
        f"Old position: {escape(old_position)}\n"
        f"New position: <b>{escape(new_position)}</b>\n"
        f"Working time: {escape(item.working_time or '')}\n"
        f"Alert flow: {escape(item.flow_key or '')}",
        parse_mode="HTML",
    )


async def change_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = command_payload(update, context).casefold()
    if payload.startswith("controller"):
        await change_controller(update, context)
    elif payload.startswith("position"):
        await change_position(update, context)
    else:
        await change_working_time(update, context)


async def annual_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global management admin or assigned controller sets/resumes SA annual leave."""
    if not claim_command(update, context, "al"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return
    if is_private_chat(update):
        await safe_reply(update, context, "❌ /AL must be used inside the SA group.")
        return

    group_name = chat_name(update.effective_chat)
    # Ra Phearom is configured in GLOBAL_MANAGEMENT_USER_IDS, so can_manage_group()
    # allows this global admin to use /AL in every registered SA group while
    # preserving the existing permission for the controller assigned to this group.
    if not can_manage_group(update.effective_user.id, group_name):
        await safe_reply(
            update,
            context,
            permission_message(update, "❌ Only Ra Phearom or a controller assigned to this group can use /AL."),
        )
        return

    payload = command_payload(update, context)
    args = payload.split() if payload else []
    if not args:
        await safe_reply(
            update,
            context,
            "Usage:\n"
            "/AL Veong Thiv SO PUK7\n"
            "/AL Morning Veong Thiv SO PUK7\n"
            "/AL Afternoon Veong Thiv SO PUK7\n"
            "/AL off Veong Thiv SO PUK7",
        )
        return

    first = args[0].strip().lower()
    is_resume = first in {"off", "resume", "back", "cancel"}
    period = "day"
    if first in {"morning", "morn", "morming", "am"}:
        period = "morning"
        args = args[1:]
    elif first in {"afternoon", "pm"}:
        period = "afternoon"
        args = args[1:]
    elif is_resume:
        args = args[1:]

    query = " ".join(args).strip().lstrip("|").strip()
    if not query:
        await safe_reply(update, context, "❌ Please include the SA Telegram name after the command.")
        return

    db = SessionLocal()
    event_text = None
    response = None
    try:
        sync_group_members_from_list(db, update.effective_chat.id, group_name)
        matches = find_official_users_by_query(db, update.effective_chat.id, query)
        if not matches:
            response = f"❌ No active official SA matched: {query}"
        elif len(matches) > 1:
            names = "\n".join(f"- {user.name}" for user in matches[:10])
            response = f"❌ More than one SA matched. Use a more exact name or @username:\n{names}"
        else:
            target = matches[0]
            target_name = target.name
            target_position = target.position or "SA"
            target_username = (target.username or "").lstrip("@").strip()
            manager_name = telegram_name(update.effective_user)
            account_text = f"@{escape(target_username)}" if target_username else "No username"

            if is_resume:
                resume_alerts(db, target)
                event_text = (
                    "✅ <b>AL monitoring resumed</b>\n"
                    f"Name: <b>{escape(target_name)}</b>\n"
                    f"Position: {escape(target_position)}\n"
                    f"Account: {account_text}\n"
                    f"Resumed by: <b>{escape(manager_name)}</b>\n"
                    "Result: Missing-alert monitoring is active again for the next applicable window."
                )
            else:
                pause_start, pause_end = pause_alerts_for_period(db, target, period=period, reason="AL")
                period_label = {
                    "day": "Full Day / ច្បាប់ពេញមួយថ្ងៃ",
                    "morning": "Morning / ច្បាប់កន្លះថ្ងៃព្រឹក",
                    "afternoon": "Afternoon / ច្បាប់កន្លះថ្ងៃរសៀល",
                }[period]
                event_text = (
                    "✅ <b>SA Annual Leave recorded</b>\n"
                    f"Name: <b>{escape(target_name)}</b>\n"
                    f"Position: {escape(target_position)}\n"
                    f"Account: {account_text}\n"
                    f"AL Type: <b>{escape(period_label)}</b>\n"
                    f"AL Date: {local_now().strftime('%d/%m/%Y')}\n"
                    f"Excluded Window: {pause_start.strftime('%I:%M %p')} - {pause_end.strftime('%I:%M %p')}\n"
                    f"Set by: <b>{escape(manager_name)}</b>\n"
                    "Result: No form/photo/GPS missing alert and no SA mention during this AL period. "
                    "Tomorrow, normal monitoring starts again from the first configured alert window."
                )
    finally:
        db.close()

    if event_text is None:
        await safe_reply(update, context, response)
        return

    sent, failed = await notify_group_controllers(update, context, event_text)
    if failed:
        await safe_reply(
            update,
            context,
            f"ℹ️ Controller group notification sent. Private delivery: {sent} sent, {failed} failed. "
            "Controllers who did not receive it should open the bot privately and send /start.",
        )


async def remove_sa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove an SA; global admin, assigned controller, or Telegram admin may use it."""
    if not claim_command(update, context, "remove"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return
    raw_remove = command_payload(update, context).strip()
    import re as _re
    controller_id_remove = _re.fullmatch(r"controller\s*\|\s*(\d+)\s*\|?", raw_remove, _re.I)
    if controller_id_remove:
        if update.effective_user.id not in GLOBAL_MANAGEMENT_USER_IDS:
            await safe_reply(update, context, "❌ Only Ra Phearom can remove a controller.")
            return
        controller_id = int(controller_id_remove.group(1))
        db = SessionLocal()
        try:
            removed_groups = remove_controller_by_user_id(db, user_id=controller_id)
        finally:
            db.close()
        if not removed_groups:
            await safe_reply(update, context, f"ℹ️ Telegram ID {controller_id} has no exact group assignments.")
        else:
            await safe_reply(update, context,
                f"✅ Controller <code>{controller_id}</code> removed from: <b>{escape(', '.join(removed_groups))}</b>",
                parse_mode="HTML")
        return
    controller_remove = _re.fullmatch(r"controller\s*\|?\s*(R[1-8])", raw_remove, _re.I)
    if controller_remove:
        region = controller_remove.group(1).upper()
        if update.effective_user.id not in GLOBAL_MANAGEMENT_USER_IDS:
            await safe_reply(update, context, "❌ Only Ra Phearom can remove a regional controller.")
            return
        db = SessionLocal()
        try:
            remove_region_controller(db, region=region, changed_by_user_id=update.effective_user.id)
        finally:
            db.close()
        await safe_reply(update, context, f"✅ Controller removed for {region}. Use /change controller to assign a replacement.")
        return
    if is_private_chat(update):
        await safe_reply(update, context, "❌ /remove must be used inside the SA group.")
        return
    group_name = chat_name(update.effective_chat)
    if not can_manage_group(update.effective_user.id, group_name) and not await is_group_admin(update, context):
        await safe_reply(
            update,
            context,
            permission_message(update, "❌ Only Ra Phearom, the assigned controller, or a Telegram group admin can use /remove."),
        )
        return

    query = command_payload(update, context).lstrip("|").strip()
    if not query:
        await safe_reply(update, context, "Usage: /remove <Telegram ID>")
        return

    db = SessionLocal()
    event_text = None
    try:
        sync_group_members_from_list(db, update.effective_chat.id, group_name)
        matches = find_official_users_by_query(db, update.effective_chat.id, query)
        if not matches:
            response = f"❌ No active official SA matched: {query}"
        elif len(matches) > 1:
            names = "\n".join(f"- {user.name}" for user in matches[:10])
            response = f"❌ More than one SA matched. Use the numeric Telegram ID:\n{names}"
        else:
            target = matches[0]
            target_name = target.name
            target_position = target.position or "SA"
            target_username = (target.username or "").lstrip("@").strip()
            admin_name = telegram_name(update.effective_user)
            remove_user_from_alerts(
                db,
                target,
                removed_by_user_id=update.effective_user.id,
                removed_by_name=admin_name,
            )
            account_text = f"@{escape(target_username)}" if target_username else "No username"
            event_text = (
                "🗑️ <b>SA removed from missing alerts</b>\n"
                f"Name: <b>{escape(target_name)}</b>\n"
                f"Position: {escape(target_position)}\n"
                f"Account: {account_text}\n"
                f"Removed by admin: <b>{escape(admin_name)}</b>\n"
                f"Group: {escape(group_name)}\n"
                "Result: The SA was removed from the active database, will not be re-added by "
                "/sync_sa_list, and will no longer be mentioned in missing alerts.\n"
                f"Restore command: <code>/restore {target.telegram_user_id}</code>"
            )
            response = None
    finally:
        db.close()

    if event_text is None:
        await safe_reply(update, context, response)
        return

    sent, failed = await notify_group_controllers(update, context, event_text)
    if failed:
        await safe_reply(
            update,
            context,
            f"ℹ️ Controller group notification sent. Private delivery: {sent} sent, {failed} failed. "
            "Controllers who did not receive it should open the bot privately and send /start.",
        )


async def restore_sa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not claim_command(update, context, "restore"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return
    if is_private_chat(update):
        await safe_reply(update, context, "❌ /restore must be used inside the SA group.")
        return
    group_name = chat_name(update.effective_chat)
    if not can_manage_group(update.effective_user.id, group_name) and not await is_group_admin(update, context):
        await safe_reply(
            update,
            context,
            permission_message(update, "❌ Only Ra Phearom, the assigned controller, or a Telegram group admin can use /restore."),
        )
        return
    query = command_payload(update, context).lstrip("|").strip()
    if not query:
        await safe_reply(update, context, "Usage: /restore <Telegram ID>")
        return

    db = SessionLocal()
    try:
        restored, total = restore_user_to_alerts(
            db, update.effective_chat.id, group_name, query
        )
    finally:
        db.close()
    if restored:
        await safe_reply(update, context, f"✅ Restored {restored} SA. Active users in group: {total}")
    else:
        await safe_reply(update, context, f"❌ No removed SA matched: {query}")


async def export_missing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a missing report and deliver it privately to authorized recipients.

    A configured administrator is a global export user and may
    run this command from every group. Regional controllers may also run it
    globally, while Telegram group administrators may export the current
    group's regional scope.
    """
    if not claim_command(update, context, "export"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return

    requester = update.effective_user
    requester_id = int(requester.id)
    requester_is_global = can_request_export_globally(requester_id)

    logger.info(
        "Export requested: user_id=%s username=%s chat_id=%s chat_name=%s global=%s",
        requester_id,
        requester.username,
        update.effective_chat.id,
        chat_name(update.effective_chat),
        requester_is_global,
    )

    # Global export users may also run /export in private chat. In that case,
    # export every configured region because there is no group name to infer.
    if is_private_chat(update) and not requester_is_global:
        await safe_reply(update, context, "❌ /export must be used inside a registered SA group.")
        return

    if not requester_is_global and not await can_export(update, context):
        await safe_reply(
            update,
            context,
            permission_message(update, "❌ Only a group admin, configured controller, or export audit user can use /export."),
        )
        return

    group_name = chat_name(update.effective_chat)
    if is_private_chat(update):
        regions = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")
        controllers = ()
    else:
        regions = export_regions_for_group(group_name)
        controllers = controllers_for_group(group_name)

    export_payload = command_payload(update, context)
    target_date = parse_export_date(export_payload.split() if export_payload else [])
    if target_date is None:
        await safe_reply(update, context, "❌ Invalid date. Use /export, /export 2026-07-17, or /export 17/07/2026.")
        return

    # Ra Phearom and other global export users must always receive a response.
    # For an unusual group name without an R1-R8 code, fall back to all regions.
    if not regions and requester_is_global:
        regions = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")

    if not regions:
        await safe_reply(update, context, f"❌ Could not detect an export region from group: {group_name}")
        return

    await safe_reply(
        update,
        context,
        f"⏳ Preparing export for {', '.join(regions)} on {target_date.strftime('%d/%m/%Y')}...",
    )

    db = SessionLocal()
    try:
        rows = build_missing_export_rows(db, regions, target_date)
    except Exception:
        logger.exception("/export failed while reading report rows")
        await safe_reply(update, context, "❌ Export failed while reading the report data. Check the bot terminal log.")
        return
    finally:
        db.close()

    try:
        output_path = create_missing_export_workbook(rows, regions, target_date)
    except Exception:
        logger.exception("/export failed while creating workbook")
        await safe_reply(update, context, "❌ Export failed while creating the Excel workbook. Check the bot terminal log.")
        return

    recipients_by_id: dict[int, str] = {
        controller.user_id: controller.name for controller in controllers
    }
    for recipient in EXPORT_AUDIT_RECIPIENTS:
        recipients_by_id.setdefault(recipient.user_id, recipient.name)

    requester_name = requester.full_name or requester.username or str(requester.id)
    recipients_by_id.setdefault(requester_id, requester_name)

    sent: list[str] = []
    failed: list[str] = []
    requester_private_sent = False
    try:
        for recipient_user_id, recipient_name in recipients_by_id.items():
            try:
                with output_path.open("rb") as document:
                    await context.bot.send_document(
                        chat_id=recipient_user_id,
                        document=document,
                        filename=output_path.name,
                        caption=(
                            f"SA Missing Report: {', '.join(regions)}\n"
                            f"Date: {target_date.strftime('%d/%m/%Y')}\n"
                            f"Records: {len(rows)}"
                        ),
                    )
                sent.append(recipient_name)
                if recipient_user_id == requester_id:
                    requester_private_sent = True
            except Exception as exc:
                logger.exception(
                    "Could not DM export to user_id=%s name=%s: %s",
                    recipient_user_id,
                    recipient_name,
                    exc,
                )
                failed.append(recipient_name)
        # If Telegram private delivery to the requester is unavailable, deliver
        # the workbook back to the group where /export was requested. This makes
        # the command visibly useful even before the requester has sent /start.
        if not is_private_chat(update) and not requester_private_sent:
            try:
                thread_id = None
                message = update.effective_message
                if message is not None and getattr(message, "is_topic_message", False):
                    thread_id = getattr(message, "message_thread_id", None)
                with output_path.open("rb") as document:
                    kwargs = {
                        "chat_id": update.effective_chat.id,
                        "document": document,
                        "filename": output_path.name,
                        "caption": (
                            f"SA Missing Report: {', '.join(regions)}\n"
                            f"Date: {target_date.strftime('%d/%m/%Y')}\n"
                            f"Records: {len(rows)}\n"
                            "Private delivery to requester was unavailable, so this copy was sent here."
                        ),
                    }
                    if thread_id is not None:
                        kwargs["message_thread_id"] = thread_id
                    await context.bot.send_document(**kwargs)
            except Exception:
                logger.exception("Could not send /export fallback workbook to the request group")
    finally:
        shutil.rmtree(output_path.parent, ignore_errors=True)

    lines = [
        "✅ Export completed.",
        f"Regions: {', '.join(regions)}",
        f"Date: {target_date.strftime('%d/%m/%Y')}",
        f"Rows: {len(rows)}",
        f"Sent directly: {len(sent)}/{len(recipients_by_id)} recipient(s)",
    ]
    if failed:
        lines.append(
            "⚠️ Private delivery failed for: "
            + ", ".join(failed)
            + ". Each person must open the bot privately and send /start once."
        )
    await safe_reply(update, context, "\n".join(lines))


async def export_sa_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export the complete editable SA_List and deliver it to Ra Phearom."""
    if not claim_command(update, context, "export_sa_list"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return
    if not can_request_export_globally(update.effective_user.id):
        await safe_reply(update, context, permission_message(update, "❌ Only Ra Phearom or a configured regional controller can use /export_SA_list."))
        return
    await safe_reply(update, context, "⏳ Preparing the complete SA List export...")
    db = SessionLocal()
    try:
        seed_sa_list(db)
        # Best-effort live hydration. Telegram only resolves accounts that have
        # interacted with the bot/shared chat; unresolved IDs remain safely
        # bound by number and are never guessed from names.
        telegram_ids = {
            int(row.telegram_user_id)
            for row in db.query(SAList).filter(SAList.telegram_user_id.isnot(None)).all()
            if int(row.telegram_user_id or 0) > 0
        }
        refreshed = 0
        unresolved = 0
        for telegram_id in sorted(telegram_ids):
            try:
                profile = await context.bot.get_chat(telegram_id)
                current_name = " ".join(
                    part for part in [getattr(profile, "first_name", None), getattr(profile, "last_name", None)]
                    if part
                ).strip()
                refreshed += sync_roster_profile_by_telegram_id(
                    db,
                    telegram_user_id=telegram_id,
                    current_name=current_name,
                    current_username=getattr(profile, "username", None),
                )
            except Exception:
                unresolved += 1
        output_path = create_sa_list_workbook(db)
    except Exception:
        logger.exception("/export_SA_list failed")
        await safe_reply(update, context, "❌ SA List export failed. Check the bot terminal log.")
        return
    finally:
        db.close()
    recipient = EXPORT_AUDIT_RECIPIENTS[0]
    delivered = False
    try:
        with output_path.open("rb") as document:
            await context.bot.send_document(
                chat_id=recipient.user_id,
                document=document,
                filename=output_path.name,
                caption="Complete SA List: active, removed, manual, working time, group and controller.",
            )
        delivered = True
    except Exception:
        logger.exception("Could not deliver SA List export to Ra Phearom")
    finally:
        shutil.rmtree(output_path.parent, ignore_errors=True)
    if delivered:
        await safe_reply(
            update,
            context,
            "✅ Complete SA List sent privately to Ra Phearom (@Phearom252005).\n"
            f"Profile rows refreshed: {refreshed}; Telegram IDs not currently resolvable: {unresolved}.",
        )
    else:
        await safe_reply(
            update,
            context,
            "❌ Could not send the file privately. Ra Phearom must open the bot and send /start once.",
        )


START_COMMAND_MENU = """📋 Track_SA_alert Commands

👤 General
/start - Register bot and show this command menu
/myid - Show your Telegram user ID
/chatid - Show current chat/group ID
/pin - Show how to send map location
/status - Show current group/user tracking status
/bind_sa - Bind your Telegram account to the official SA row
/dashboard - Get a one-time dashboard sign-in code (management admins, private chat)

👥 SA / Controller Management
/add <Telegram ID> | <Position> | <Dealer> | <Working time>
/change working time <Telegram ID> | <New working time>
/change position | <Telegram ID> | <New position>
/change controller | <Region> | <Name> | <@username> | <Telegram ID>
/remove <Telegram ID>
/restore <Telegram ID>
/AL <Name>
/AL Morning <Name>
/AL Afternoon <Name>
/AL off <Name>

📊 Reports / Setup
/export [date]
/export_sa_list
/register_topic
/setup_group
/sync_sa_list

🛠 Admin / Test
/delete_pin
/alert_now
/reset_today
/test_afternoon
/test_alert
/test_alert_all_time

ℹ️ Some commands require Ra Phearom, regional-controller, or group-admin permission."""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)

    if not is_private_chat(update):
        db = SessionLocal()
        try:
            register_group(db, group_id, group_name)
            sync_group_members_from_list(db, group_id, group_name)
        finally:
            db.close()

    if is_private_chat(update):
        await safe_reply(
            update,
            context,
            "✅ Registered. Please pin your map location.\n\n" + START_COMMAND_MENU,
            reply_markup=location_keyboard(),
        )
    else:
        await safe_reply(
            update,
            context,
            "✅ Registered successfully for this group.\n"
            "This group is now active for alerts.\n\n" + START_COMMAND_MENU,
        )


async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return

    if is_private_chat(update):
        await safe_reply(update, context, 
            "Click button below to send your current map location.",
            reply_markup=location_keyboard(),
        )
    else:
        await safe_reply(update, context, 
            "📍 To pin in group:\n"
            "1. Click 📎 Attachment\n"
            "2. Select Location\n"
            "3. Send Current Location"
        )


async def receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if (
        update.effective_user is None
        or msg is None
        or msg.location is None
        or update.effective_chat is None
    ):
        return

    if is_private_chat(update):
        # Private pins should not create alert-group rows. Users must submit in the work group/topic.
        await safe_reply(update, context, "✅ Pin received. For work tracking, please send form + pin map in your SA group.")
        return

    user = update.effective_user
    loc = msg.location
    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)

    db = SessionLocal()
    try:
        replied = msg.reply_to_message
        author = getattr(replied, "from_user", None)
        linked_caption = get_caption(replied) if author and author.id == user.id else None
        save_location(
            db,
            user.id,
            group_id,
            group_name,
            telegram_name(user),
            user.username,
            loc.latitude,
            loc.longitude,
            event_at=getattr(msg, "edit_date", None) or getattr(msg, "date", None),
            telegram_update_id=getattr(update, "update_id", None),
            report_caption=linked_caption if has_sale_format_text(linked_caption or "") else None,
        )

        # Way 2:
        # User sends photo + caption first, then replies to that photo with map/location.
        replied_photo = msg.reply_to_message
        replied_sender = getattr(replied_photo, "from_user", None) if replied_photo else None
        same_sender = bool(replied_sender and int(replied_sender.id) == int(user.id))
        if replied_photo and has_photo(replied_photo) and same_sender:
            # Store one atomic proof that the same Telegram account supplied
            # both the photo and its replied GPS.  The interval matcher accepts
            # this linked report even if historic duplicate cleanup left the
            # separate PinHistory row on an older canonical SA row.
            caption = get_caption(msg.reply_to_message)
            save_sale_photo_report(
                db,
                user.id,
                group_id,
                group_name,
                telegram_name(user),
                user.username,
                caption,
                loc.latitude,
                loc.longitude,
                msg.reply_to_message.message_id,
                msg.message_id,
                media_group_id=getattr(replied_photo, "media_group_id", None),
            )
        elif replied_photo and has_photo(replied_photo) and not same_sender:
            logger.warning(
                "GPS reply not linked to another sender's photo chat_id=%s gps_user_id=%s photo_user_id=%s",
                group_id,
                user.id,
                getattr(replied_sender, "id", None),
            )
        elif replied_photo and same_sender and has_sale_format_text(get_caption(replied_photo)):
            # Exact field workflow: an SA replies to their own outlet/order
            # text form with a Telegram location. Persist one atomic row with
            # the GPS coordinates even when the original/edited form update
            # was missed during a restart. This prevents the visible
            # form+location pair from being falsely mentioned as missing.
            save_sale_photo_report(
                db,
                user.id,
                group_id,
                group_name,
                telegram_name(user),
                user.username,
                get_caption(replied_photo),
                loc.latitude,
                loc.longitude,
                replied_photo.message_id,
                msg.message_id,
            )
    finally:
        db.close()


def _telegram_audio_suffix(audio, telegram_file) -> str:
    """Choose a safe extension for a Telegram voice note or audio upload."""
    for candidate in (
        getattr(audio, "file_name", None),
        getattr(telegram_file, "file_path", None),
    ):
        suffix = Path(str(candidate or "")).suffix.lower()
        if suffix in {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".flac"}:
            return suffix
    mime_type = str(getattr(audio, "mime_type", None) or "").lower()
    return {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
    }.get(mime_type, ".ogg")


async def _transcribe_voice_message(context: ContextTypes.DEFAULT_TYPE, voice) -> str | None:
    """Download and transcribe Telegram voice/audio with provider fallback."""
    if not settings.VOICE_TRANSCRIPTION_ENABLED:
        logger.warning(
            "VOICE_TRANSCRIPTION_ENABLED=false is deprecated; attempting "
            "transcription because voice reasons must contain real text"
        )

    provider = (settings.VOICE_TRANSCRIPTION_PROVIDER or "local").strip().lower()
    error_key = str(voice.file_id)
    bot_data = getattr(context, "bot_data", None)
    error_store = (
        bot_data.setdefault("_voice_transcription_errors", {})
        if isinstance(bot_data, dict)
        else {}
    )
    error_store.pop(error_key, None)

    def completed(value: str | None) -> str | None:
        if value:
            error_store.pop(error_key, None)
        return value

    try:
        payload = None
        telegram_file = None
        last_download_error = None
        for attempt in range(1, 4):
            try:
                telegram_file = await context.bot.get_file(voice.file_id)
                payload = await telegram_file.download_as_bytearray()
                if payload:
                    break
                raise ValueError("Telegram returned an empty voice file")
            except Exception as exc:
                last_download_error = exc
                logger.warning(
                    "Voice download attempt failed: attempt=%s/3 file_id_prefix=%s error=%s",
                    attempt,
                    str(voice.file_id)[:12],
                    type(exc).__name__,
                )
                if attempt < 3:
                    await asyncio.sleep(attempt)
        if not payload:
            raise RuntimeError(
                "Telegram voice download failed after 3 attempts"
            ) from last_download_error

        suffix = _telegram_audio_suffix(voice, telegram_file)

        async def transcribe_local() -> str:
            with tempfile.TemporaryDirectory(prefix="sa_voice_") as temp_dir:
                audio_path = Path(temp_dir) / f"telegram_voice{suffix}"
                audio_path.write_bytes(bytes(payload))
                return await asyncio.wait_for(
                    get_local_voice_transcriber().transcribe(audio_path),
                    timeout=max(30, settings.VOICE_TRANSCRIPTION_TIMEOUT_SECONDS),
                )

        async def transcribe_api() -> str:
            if not settings.VOICE_TRANSCRIPTION_API_KEY:
                raise RuntimeError("Voice transcription API key is not configured")

            endpoint = (
                settings.VOICE_TRANSCRIPTION_BASE_URL.rstrip("/")
                + "/audio/transcriptions"
            )
            headers = {
                "Authorization": f"Bearer {settings.VOICE_TRANSCRIPTION_API_KEY}"
            }
            data = {"model": settings.VOICE_TRANSCRIPTION_MODEL}
            if settings.VOICE_TRANSCRIPTION_LANGUAGE:
                data["language"] = settings.VOICE_TRANSCRIPTION_LANGUAGE
            files = {
                "file": (
                    f"telegram_voice{suffix}",
                    bytes(payload),
                    getattr(voice, "mime_type", None) or "audio/ogg",
                )
            }

            async with httpx.AsyncClient(
                timeout=settings.VOICE_TRANSCRIPTION_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    data=data,
                    files=files,
                )
                response.raise_for_status()
                body = response.json()
                return str(body.get("text") or "").strip()

        # With provider=local, a configured API key is used only if local
        # CUDA/model inference fails. API mode does the reverse. This keeps the
        # normal workflow offline while giving production a real fallback.
        if provider == "api":
            attempts = [("api", transcribe_api), ("local", transcribe_local)]
        elif provider == "auto" and settings.VOICE_TRANSCRIPTION_API_KEY:
            attempts = [("api", transcribe_api), ("local", transcribe_local)]
        else:
            attempts = [("local", transcribe_local)]
            if settings.VOICE_TRANSCRIPTION_API_KEY:
                attempts.append(("api", transcribe_api))

        attempt_errors: list[str] = []
        for attempt_name, callback in attempts:
            try:
                transcript = " ".join((await callback()).split()).strip()
                if not transcript:
                    raise ValueError("No speech was detected")
                if is_low_quality_transcript(transcript):
                    raise ValueError("Transcript was unreadable or low quality")
                logger.info(
                    "Voice transcription succeeded: provider=%s chars=%s",
                    attempt_name,
                    len(transcript),
                )
                return completed(transcript)
            except Exception as attempt_exc:
                detail = f"{attempt_name}: {type(attempt_exc).__name__}: {attempt_exc}"
                attempt_errors.append(detail)
                logger.exception("Voice transcription provider failed: %s", detail)

        raise RuntimeError(
            " | ".join(attempt_errors) or "No transcription provider was attempted"
        )

    except Exception as exc:
        logger.exception("Voice transcription failed: %s", exc)
        detail = f"{type(exc).__name__}: {exc}".strip()
        error_store[error_key] = detail[:1000]
        return None


async def receive_missing_reason_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Track text, photo-caption, or voice reasons for an official user."""
    if (
        update.effective_user is None
        or update.effective_chat is None
        or update.effective_message is None
        or is_private_chat(update)
    ):
        return

    message = update.effective_message
    replied = message.reply_to_message
    replied_to_bot = bool(
        replied is not None
        and replied.from_user is not None
        and replied.from_user.is_bot
    )

    # A normal sales photo/caption must never become a missing reason. Photo
    # captions are accepted only when the sender explicitly replies to a bot
    # message; save_missing_reason_reply() then verifies that the replied
    # message is an actual missing alert for this same immutable Telegram ID.
    if message.photo and not replied_to_bot:
        return

    # Mentions are routing/display text, not the business reason. This removes
    # controller, SA, Commando and Team Leader usernames such as
    # @Rathchantha_Np or @PIDOL022 while retaining text such as "ឡានខូច".
    raw_reason = (message.text or message.caption or "").strip()
    # An outlet/order form remains a sales submission even when it is posted
    # as a direct reply to the missing alert. Never export its complete
    # ID/Dealer/Outlet/product caption as a business reason.
    if raw_reason and looks_like_sale_form(raw_reason):
        logger.info(
            "Sales submission excluded from missing reason: group_id=%s "
            "telegram_user_id=%s message_id=%s replied_to_bot=%s",
            update.effective_chat.id,
            update.effective_user.id,
            message.message_id,
            replied_to_bot,
        )
        return
    cleaned_reason = re.sub(r"(?<!\w)@[A-Za-z0-9_]{5,32}\b", " ", raw_reason)
    cleaned_reason = " ".join(cleaned_reason.split()).strip(" |,-")

    # Only bot alerts recorded in missing_sale_photo_alerts are accepted.
    reason_text = cleaned_reason or None
    voice_to_text = None
    voice_history_id = None
    audio = message.voice or message.audio
    if audio is not None:
        db = SessionLocal()
        try:
            history, linked = start_voice_reason_history(
                db,
                group_id=update.effective_chat.id,
                group_name=chat_name(update.effective_chat),
                sender_user_id=update.effective_user.id,
                sender_username=update.effective_user.username,
                sender_name=telegram_name(update.effective_user),
                alert_message_id=replied.message_id if replied_to_bot else None,
                voice_message_id=message.message_id,
                voice_file_id=audio.file_id,
                voice_file_unique_id=audio.file_unique_id,
                duration_seconds=audio.duration,
                file_size_bytes=audio.file_size,
                received_at=local_now(),
            )
        finally:
            db.close()

        if history is None:
            await safe_reply(
                update,
                context,
                "❌ Voice was not processed because this Telegram ID is not "
                "bound to an official user in this group. Please use /bind_sa.",
            )
            return
        voice_history_id = history.id
        # Always transcribe a voice from a bound official account. When there
        # is no matching missing-alert row, keep the real transcript in audit
        # history but never attach it to another user/window.
        voice_to_text = await _transcribe_voice_message(context, audio)
        voice_error = context.bot_data.get("_voice_transcription_errors", {}).get(
            str(audio.file_id)
        )
        db = SessionLocal()
        try:
            voice_saved = finish_voice_reason_history(
                db,
                history_id=history.id,
                transcript=voice_to_text,
                error_text=None if voice_to_text else (voice_error or "No speech was detected by the model"),
            )
        finally:
            db.close()

        if not voice_to_text or not voice_saved:
            await safe_reply(
                update,
                context,
                f"⚠️ Voice history #{history.id} was saved, but no valid speech "
                "text was produced.\n"
                f"Cause: {voice_error or 'No speech was detected by the model'}\n"
                "An authorized controller can retry with "
                f"/retry_voice {history.id}.",
            )
            return

        if not linked:
            await safe_reply(
                update,
                context,
                f"✅ Voice history #{history.id} converted to text successfully.\n\n"
                f"Text: {voice_to_text}\n\n"
                "ℹ️ It was saved as voice history only because this voice was "
                "not linked to a missing alert; it was not written into a wrong "
                "user or Excel missing row.",
            )
            return

    if not reason_text and not voice_to_text:
        if audio is not None:
            await safe_reply(
                update,
                context,
                "⚠️ Voice was received, but no speech text was produced. "
                "Please retry with clear audio or send the reason as text.",
            )
        return

    if audio is not None:
        # The voice transcript was already written to the exact alert selected
        # by start_voice_reason_history(). A typed caption, if present, is saved
        # independently without moving the voice transcript to another window.
        updated = 1
        if reason_text:
            db = SessionLocal()
            try:
                updated += save_latest_missing_reason(
                    db,
                    group_id=update.effective_chat.id,
                    sender_user_id=update.effective_user.id,
                    reason_text=reason_text,
                    voice_to_text=None,
                    reason_message_id=message.message_id,
                    submitted_at=local_now(),
                )
            finally:
                db.close()
    else:
        db = SessionLocal()
        try:
            if replied_to_bot:
                updated = save_missing_reason_reply(
                    db,
                    group_id=update.effective_chat.id,
                    alert_message_id=replied.message_id,
                    sender_user_id=update.effective_user.id,
                    sender_username=update.effective_user.username,
                    sender_name=telegram_name(update.effective_user),
                    reason_text=reason_text,
                    voice_to_text=None,
                    reason_message_id=message.message_id,
                    submitted_at=local_now(),
                )
            else:
                updated = save_latest_missing_reason(
                    db,
                    group_id=update.effective_chat.id,
                    sender_user_id=update.effective_user.id,
                    reason_text=reason_text,
                    voice_to_text=None,
                    reason_message_id=message.message_id,
                    submitted_at=local_now(),
                )
        finally:
            db.close()

    if updated:
        logger.info(
            "Missing reason recorded: group_id=%s telegram_user_id=%s "
            "reason_message_id=%s has_text=%s has_voice_text=%s rows=%s",
            update.effective_chat.id,
            update.effective_user.id,
            message.message_id,
            bool(reason_text),
            bool(voice_to_text),
            updated,
        )
        if audio is not None:
            await safe_reply(
                update,
                context,
                f"✅ Voice history #{voice_history_id} transcribed and recorded for export report.",
            )
        else:
            await safe_reply(update, context, "✅ ទទួលបានហេតុផលរួចរាល់។")
    else:
        logger.warning(
            "Missing reason not linked: group_id=%s telegram_user_id=%s "
            "reason_message_id=%s replied_message_id=%s has_voice_text=%s",
            update.effective_chat.id,
            update.effective_user.id,
            message.message_id,
            replied.message_id if replied is not None else None,
            bool(voice_to_text),
        )
        await safe_reply(
            update,
            context,
            "⚠️ Reason/voice was not linked to your missing alert row. "
            "Please reply directly to the bot alert and resend it.",
        )


async def voice_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List persisted Telegram voice attempts for authorized management users."""
    if not claim_command(update, context, "voice_history"):
        return
    if update.effective_user is None or update.effective_chat is None:
        return

    requester_id = int(update.effective_user.id)
    global_access = can_request_export_globally(requester_id)
    group_name = chat_name(update.effective_chat)
    if is_private_chat(update):
        allowed = global_access
    else:
        allowed = global_access or can_manage_group(requester_id, group_name)
    if not allowed:
        await safe_reply(update, context, permission_message(update, "❌ You cannot view voice history for this scope."))
        return

    payload = command_payload(update, context)
    try:
        limit = max(1, min(50, int(payload or "20")))
    except ValueError:
        await safe_reply(update, context, "Usage: /voice_history [1-50]")
        return

    db = SessionLocal()
    try:
        query = db.query(VoiceReasonHistory)
        if not is_private_chat(update) and not global_access:
            query = query.filter(VoiceReasonHistory.group_id == update.effective_chat.id)
        rows = query.order_by(VoiceReasonHistory.received_at.desc()).limit(limit).all()
        lines = []
        for row in rows:
            detail = (row.transcript or row.error_text or "").replace("\n", " ").strip()
            if len(detail) > 80:
                detail = detail[:77] + "..."
            username = f"@{row.sender_username}" if row.sender_username else "No username"
            lines.append(
                f"#{row.id} | {row.received_at:%d/%m %H:%M} | {row.status} | "
                f"{row.sender_name} | {username} | {row.group_name or row.group_id}"
                + (f"\n{detail}" if detail else "")
            )
    finally:
        db.close()

    await safe_reply(update, context, "🎙 Voice history\n\n" + ("\n\n".join(lines) if lines else "No voice history found."))


async def retry_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retry one stored Telegram voice file without asking the user to resend it."""
    if not claim_command(update, context, "retry_voice"):
        return
    if update.effective_user is None:
        return
    payload = command_payload(update, context)
    try:
        history_id = int(payload)
    except (TypeError, ValueError):
        await safe_reply(update, context, "Usage: /retry_voice <history ID>")
        return

    db = SessionLocal()
    try:
        row = db.query(VoiceReasonHistory).filter(VoiceReasonHistory.id == history_id).first()
        if row is None:
            await safe_reply(update, context, f"❌ Voice history #{history_id} was not found.")
            return
        requester_id = int(update.effective_user.id)
        allowed = can_request_export_globally(requester_id) or can_manage_group(
            requester_id, row.group_name or ""
        )
        if not allowed:
            await safe_reply(update, context, permission_message(update, "❌ You cannot retry this voice record."))
            return
        linked = row.missing_alert_id is not None
        voice_file_id = row.voice_file_id
    finally:
        db.close()

    transcript = await _transcribe_voice_message(
        context,
        SimpleNamespace(file_id=voice_file_id),
    )
    voice_error = context.bot_data.get("_voice_transcription_errors", {}).get(
        str(voice_file_id)
    )
    db = SessionLocal()
    try:
        saved = finish_voice_reason_history(
            db,
            history_id=history_id,
            transcript=transcript,
            error_text=None if transcript else (voice_error or "Retry detected no speech"),
        )
    finally:
        db.close()

    if saved and linked:
        await safe_reply(update, context, f"✅ Voice history #{history_id} transcribed and linked successfully:\n{transcript}")
    elif saved:
        await safe_reply(
            update,
            context,
            f"✅ Voice history #{history_id} converted successfully:\n{transcript}\n\n"
            "ℹ️ History-only result: it is not attached to an Excel missing row.",
        )
    else:
        await safe_reply(
            update,
            context,
            f"⚠️ Retry failed for voice history #{history_id}.\n"
            f"Cause: {voice_error or 'No speech was detected by the model'}\n"
            "The Telegram file ID remains saved for another retry.",
        )


async def receive_sale_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save standard text outlet/order forms.

    Many SA users submit a text form then a Google Maps pin, not a photo.
    The alert checker counts a user as submitted only when both the text form
    and pin map exist in the same schedule window.
    """
    msg = update.effective_message
    if update.effective_user is None or msg is None or update.effective_chat is None:
        return

    text = msg.text or msg.caption or ""
    if not has_sale_format_text(text):
        return
    if is_private_chat(update):
        return

    user = update.effective_user
    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)

    db = SessionLocal()
    try:
        save_text_form_report(
            db,
            user.id,
            group_id,
            group_name,
            telegram_name(user),
            user.username,
            text,
            msg.message_id,
        )
    finally:
        db.close()


async def receive_sale_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store every photo sent by an official SA in the work group.

    The old handler stored a photo only when it directly replied to a location.
    In the real workflow users often send Location, Photo, Photo, and a captioned
    Photo as separate messages. That caused false missing alerts. A valid alert
    submission is now any photo (or sale form text) plus a pin from the same user
    inside the same scheduled window.
    """
    msg = update.effective_message
    if update.effective_user is None or msg is None or update.effective_chat is None:
        return
    if is_private_chat(update):
        return

    user = update.effective_user
    if not msg.photo:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)
    caption = msg.caption or ""
    reply_lat = None
    reply_lon = None
    replied_message_id = None

    if msg.reply_to_message and msg.reply_to_message.location:
        replied_sender = getattr(msg.reply_to_message, "from_user", None)
        if replied_sender and int(replied_sender.id) == int(user.id):
            reply_lat = msg.reply_to_message.location.latitude
            reply_lon = msg.reply_to_message.location.longitude
            replied_message_id = msg.reply_to_message.message_id
        else:
            logger.warning(
                "Photo not linked to another sender's GPS chat_id=%s photo_user_id=%s gps_user_id=%s",
                group_id,
                user.id,
                getattr(replied_sender, "id", None),
            )

    db = SessionLocal()
    try:
        save_sale_photo_report(
            db,
            user.id,
            group_id,
            group_name,
            telegram_name(user),
            user.username,
            caption,
            reply_lat,
            reply_lon,
            msg.message_id,
            replied_message_id,
            media_group_id=getattr(msg, "media_group_id", None),
        )
    finally:
        db.close()


async def delete_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None or update.effective_chat is None:
        return

    db = SessionLocal()
    try:
        success = delete_my_location(db, update.effective_user.id, update.effective_chat.id)
    finally:
        db.close()

    await safe_reply(update, context, 
        "✅ Your database row was deleted from this group."
        if success
        else "❌ No database row found for you in this group."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show role/working-time specific pin status for the latest active window."""
    if update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)
    now = local_now()
    logger.info(
        "/status received chat_id=%s group=%s user_id=%s thread_id=%s",
        group_id,
        group_name,
        update.effective_user.id if update.effective_user else None,
        getattr(update.effective_message, "message_thread_id", None),
    )

    db = SessionLocal()
    try:
        sync_group_members_from_list(db, group_id, group_name)
        all_users = get_all_users_by_group(db, group_id)
        lines = [
            "📍 <b>SA MAP PIN STATUS</b>",
            f"Group: <b>{escape(group_name or str(group_id))}</b>",
            f"Time: {now.strftime('%d %b %Y %I:%M %p')}",
            f"Total SA: {len(all_users)}",
            "",
        ]

        any_flow = False
        for flow in ALL_FLOWS:
            flow_users = [u for u in all_users if (u.flow_key or "sa") == flow.key]
            if not flow_users:
                continue
            window = previous_window_for_now(now, flow.label)
            if window is None:
                lines.append(f"<b>{escape(flow.label)}</b>: monitoring has not started yet.")
                lines.append("")
                continue
            start_at, end_at = window.interval(now)
            pins = (
                db.query(PinHistory)
                .filter(
                    PinHistory.group_id == group_id,
                    PinHistory.pinned_at >= start_at,
                    PinHistory.pinned_at <= end_at.replace(second=59, microsecond=999999),
                )
                .order_by(PinHistory.pinned_at.desc())
                .all()
            )
            latest_by_sa_id = {}
            latest_by_tid = {}
            for pin_item in pins:
                if pin_item.sa_user_id and pin_item.sa_user_id not in latest_by_sa_id:
                    latest_by_sa_id[int(pin_item.sa_user_id)] = pin_item
                if pin_item.telegram_user_id and int(pin_item.telegram_user_id) > 0 and int(pin_item.telegram_user_id) not in latest_by_tid:
                    latest_by_tid[int(pin_item.telegram_user_id)] = pin_item

            lines.append(
                f"<b>{escape(flow.label)}</b> | "
                f"{start_at.strftime('%I:%M %p')} - {end_at.strftime('%I:%M %p')}"
            )
            for idx, user in enumerate(flow_users, 1):
                if user.alert_pause_start and user.alert_paused_until and user.alert_pause_start <= end_at and user.alert_paused_until >= start_at:
                    lines.append(f"{idx}. 🟡 {user_label(user)} | AL/paused")
                    continue
                pin_item = latest_by_sa_id.get(int(user.id)) if user.id is not None else None
                if pin_item is None and (user.telegram_user_id or 0) > 0:
                    pin_item = latest_by_tid.get(int(user.telegram_user_id))
                if pin_item is not None:
                    lines.append(f"{idx}. ✅ {user_label(user)} | Pin {pin_item.pinned_at.strftime('%I:%M %p')}")
                else:
                    lines.append(f"{idx}. ❌ {user_label(user)} | No pin in this window")
            lines.append("")
            any_flow = True

        if not any_flow:
            lines.append("No SA users registered in this group yet.")
    finally:
        db.close()

    await safe_reply(
        update,
        context,
        "\n".join(lines).rstrip(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def alert_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)
    now = local_now()
    sent_any = False

    db = SessionLocal()
    try:
        sync_group_members_from_list(db, group_id, group_name)
        for flow in ALL_FLOWS:
            window = previous_window_for_now(now, flow.label)
            if not window:
                continue
            start, end = window.interval(now)
            users = get_missing_sale_photo_users_for_interval(db, group_id, start, end, flow_key=flow.key)
            submitted_count = count_submitted_users_for_interval(db, group_id, start, end, flow_key=flow.key)
            if not users:
                continue
            users = record_missing_users_and_attach_today_count(db, users, group_id, start, end)
            if window.include_submitted:
                text = special_window_alert_message(
                    users,
                    settings.APP_TIMEZONE,
                    start,
                    end,
                    group_name,
                    message=window.title,
                    submitted_count=submitted_count,
                    total_users=submitted_count + len(users),
                )
            else:
                text = sale_photo_missing_alert_message(
                    users,
                    settings.APP_TIMEZONE,
                    start,
                    end,
                    group_name,
                    title=window.title,
                    submitted_count=submitted_count,
                    total_users=submitted_count + len(users),
                )
            if text:
                sent_message = await safe_reply(
                    update,
                    context,
                    text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                if sent_message is not None:
                    attach_alert_message_id(
                        db,
                        group_id,
                        start,
                        end,
                        [user.telegram_user_id for user in users],
                        sent_message.message_id,
                    )
                sent_any = True
    finally:
        db.close()

    if not sent_any:
        await safe_reply(update, context, "✅ All users submitted for latest active window.")


async def reset_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_chat is None:
        return

    db = SessionLocal()
    try:
        count = reset_locations(db, update.effective_chat.id)
    finally:
        db.close()

    await safe_reply(update, context, f"✅ Reset {count} SA users in this group.")


async def register_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register the current Telegram group/topic in telegram_groups.

    This command is required when adding the bot to a new SA group.
    It also replies with the exact group_id and group_name so you can verify it in pgAdmin.
    """
    if update.message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)

    if not is_group_chat_id(group_id):
        await safe_reply(update, context, 
            "❌ /register_topic must be used inside a Telegram SA group, not private chat."
        )
        return

    db = SessionLocal()
    try:
        group = register_group(db, group_id, group_name)
        registered = group is not None
        total = len(get_all_users_by_group(db, group_id))
    finally:
        db.close()

    if not registered:
        await safe_reply(update, context, "❌ Could not register this group/topic.")
        return

    # Use Telegram scalar values after the SQLAlchemy session is closed.
    # Do not access ORM attributes here because a commit may expire/detach them.
    await safe_reply(update, context, 
        "✅ Telegram group registered.\n\n"
        f"Group: {group_name}\n"
        f"Group ID: {group_id}\n"
        f"SA users synced: {total}\n\n"
        "Next command: /sync_sa_list"
    )


async def setup_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One-step setup: register current group and sync SA users from Excel."""
    if update.message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)

    if not is_group_chat_id(group_id):
        await safe_reply(update, context, 
            "❌ /setup_group must be used inside a Telegram SA group, not private chat."
        )
        return

    db = SessionLocal()
    try:
        group = register_group(db, group_id, group_name)
        registered = group is not None
        changed = sync_group_members_from_list(db, group_id, group_name)
        total = len(get_all_users_by_group(db, group_id))
    finally:
        db.close()

    if not registered:
        await safe_reply(update, context, "❌ Could not setup this group/topic.")
        return

    # Use Telegram scalar values after the SQLAlchemy session is closed.
    # The previous code accessed group.group_name/group.group_id after close,
    # which raised DetachedInstanceError after sync_group_members_from_list committed.
    await safe_reply(update, context, 
        "✅ Setup completed.\n\n"
        f"Group: {group_name}\n"
        f"Group ID: {group_id}\n"
        f"Updated rows: {changed}\n"
        f"Total SA users in DB: {total}"
    )


async def sync_sa_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)

    if not is_group_chat_id(group_id):
        await safe_reply(update, context, 
            "❌ /sync_sa_list must be used inside a Telegram SA group, not private chat."
        )
        return

    db = SessionLocal()
    try:
        # Register the group first, then sync members.
        # This fixes the case where /sync_sa_list works but telegram_groups does not show the group.
        group = register_group(db, group_id, group_name)
        count = sync_group_members_from_list(db, group_id, group_name)
        total = len(get_all_users_by_group(db, group_id))
    finally:
        db.close()

    await safe_reply(update, context, 
        f"✅ Synced SA list for {group_name}.\n"
        f"Group ID: {group_id}\n"
        f"Updated rows: {count}\n"
        f"Total users in DB for this group: {total}"
    )


async def test_afternoon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Preview the 13:00-13:20 afternoon first-reminder format for this group."""
    if update.message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)
    now = local_now()
    sent_any = False

    db = SessionLocal()
    try:
        sync_group_members_from_list(db, group_id, group_name)
        for flow in ALL_FLOWS:
            window = next((w for w in flow.windows if w.code.endswith("afternoon_start")), None)
            if not window:
                continue
            start, end = window.interval(now)
            users = get_missing_sale_photo_users_for_interval(db, group_id, start, end, flow_key=flow.key)
            if not users:
                continue
            for user in users:
                user.miss_count_today = 1
            submitted_count = count_submitted_users_for_interval(db, group_id, start, end, flow_key=flow.key)
            text = special_window_alert_message(
                users,
                settings.APP_TIMEZONE,
                start,
                end,
                group_name,
                message=window.title,
                submitted_count=submitted_count,
                total_users=submitted_count + len(users),
            )
            await safe_reply(update, context, text, parse_mode="HTML", disable_web_page_preview=True)
            sent_any = True
    finally:
        db.close()

    if not sent_any:
        await safe_reply(update, context, "✅ Test afternoon: all users already submitted for 13:00-13:20.")


async def test_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Preview first, regular, afternoon, and last alert formats without waiting for schedule."""
    if update.message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)
    now = local_now()
    arg = (context.args[0].lower() if context.args else "all")
    sent_any = False

    db = SessionLocal()
    try:
        sync_group_members_from_list(db, group_id, group_name)
        for flow in ALL_FLOWS:
            if arg not in ("all", flow.key, flow.label.lower().replace(" ", "")):
                continue
            sample_windows = []
            first = next((w for w in flow.windows if "first_photo" in w.code), None)
            regular = next((w for w in flow.windows if not w.include_submitted and not w.is_last), None)
            afternoon = next((w for w in flow.windows if w.code.endswith("afternoon_start")), None)
            last = next((w for w in flow.windows if w.is_last), None)
            for w in (first, regular, afternoon, last):
                if w and w not in sample_windows:
                    sample_windows.append(w)

            for window in sample_windows:
                start, end = window.interval(now)
                users = get_missing_sale_photo_users_for_interval(db, group_id, start, end, flow_key=flow.key)
                if not users:
                    continue
                for user in users:
                    user.miss_count_today = 1
                submitted_count = count_submitted_users_for_interval(db, group_id, start, end, flow_key=flow.key)
                if window.include_submitted:
                    text = special_window_alert_message(
                        users, settings.APP_TIMEZONE, start, end, group_name,
                        message=window.title, submitted_count=submitted_count,
                        total_users=submitted_count + len(users),
                    )
                else:
                    text = sale_photo_missing_alert_message(
                        users, settings.APP_TIMEZONE, start, end, group_name,
                        title=window.title, submitted_count=submitted_count,
                        total_users=submitted_count + len(users),
                    )
                await safe_reply(update, context, text, parse_mode="HTML", disable_web_page_preview=True)
                sent_any = True
    finally:
        db.close()

    if not sent_any:
        await safe_reply(update, context, "✅ Test alert: no missing users for selected flow/window.")


async def test_alert_all_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Preview every configured alert window with the full message format.

    Usage:
      /test_alert_all_time water
      /test_alert_all_time sa
      /test_alert_all_time commando
      /test_alert_all_time all
    """
    if update.message is None or update.effective_chat is None:
        return

    group_id = update.effective_chat.id
    group_name = chat_name(update.effective_chat)
    now = local_now()
    arg = (context.args[0].lower().replace(" ", "_") if context.args else "all")
    sent_any = False

    db = SessionLocal()
    try:
        sync_group_members_from_list(db, group_id, group_name)
        for flow in ALL_FLOWS:
            accepted = {"all", flow.key, flow.label.lower().replace(" ", "_"), flow.label.lower().replace(" ", "")}
            if arg not in accepted:
                continue

            await safe_reply(update, context, f"🧪 Test all alert windows: {flow.label}")

            for window in flow.windows:
                start, end = window.interval(now)
                users = get_missing_sale_photo_users_for_interval(db, group_id, start, end, flow_key=flow.key)
                if not users:
                    continue

                # Preview command should not increase real miss-count history.
                for user in users:
                    user.miss_count_today = getattr(user, "miss_count_today", None) or 1

                submitted_count = count_submitted_users_for_interval(db, group_id, start, end, flow_key=flow.key)
                text = sale_photo_missing_alert_message(
                    users,
                    settings.APP_TIMEZONE,
                    start,
                    end,
                    group_name,
                    title=window.title,
                    submitted_count=submitted_count,
                    total_users=submitted_count + len(users),
                )
                if text:
                    await safe_reply(update, context, text, parse_mode="HTML", disable_web_page_preview=True)
                    sent_any = True
    finally:
        db.close()

    if not sent_any:
        await safe_reply(update, context, "✅ Test all time: no missing users found for selected flow.")


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_chat is None:
        return

    await safe_reply(update, context, f"Chat ID: {update.effective_chat.id}")


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Use this command to get Telegram numeric User ID.

    Example:
    A controller sends /myid
    Bot replies:
    User ID: 123456789
    Name: Example Controller
    Username: @example_controller
    """
    if update.effective_user is None or update.effective_message is None:
        return

    user = update.effective_user
    username = f"@{user.username}" if user.username else "No username"

    await safe_reply(update, context, 
        f"User ID: {user.id}\n"
        f"Name: {telegram_name(user)}\n"
        f"Username: {username}"
    )
