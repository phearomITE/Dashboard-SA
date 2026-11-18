# app/services/telethon_recovery.py
"""Recover missed Telegram group pins/reports using Telethon (Telegram Client API)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logger import get_logger
from app.database.database import SessionLocal
from app.database.models import MissingSalePhotoAlert, PinHistory, SAUser, SalePhotoReport, TelegramGroup
from app.services.time_service import get_current_interval_end, get_current_interval_start, local_now
from app.services.user_service import (
    _submitted_user_ids_for_interval,
    _to_local_naive,
    looks_like_sale_form,
    save_location,
    save_sale_photo_report,
    save_text_form_report,
)

settings = get_settings()
logger = get_logger(__name__)

_RECOVERY_LOCK = asyncio.Lock()

PIN_TIME_TOLERANCE_SECONDS = 20.0
PIN_COORD_TOLERANCE_DEGREES = 0.00025
TIMESTAMP_REPAIR_THRESHOLD_SECONDS = 60.0


@dataclass(slots=True)
class RecoveredMessage:
    message_id: int
    sender_id: int
    sender_name: str
    sender_username: str | None
    sent_at: datetime
    text: str = ""
    has_photo: bool = False
    lat: float | None = None
    lon: float | None = None
    reply_to_message_id: int | None = None
    media_group_id: str | None = None


@dataclass(slots=True)
class RecoveryGroupStats:
    group_id: int
    group_name: str
    scanned_messages: int = 0
    recovered_pins: int = 0
    recovered_reports: int = 0
    repaired_timestamps: int = 0
    skipped_duplicates: int = 0


@dataclass(slots=True)
class RecoverySummary:
    target_date: date
    dry_run: bool
    groups_total: int = 0
    groups_synced: int = 0
    groups_missing_dialog: list[str] = field(default_factory=list)
    recovered_pins: int = 0
    recovered_reports: int = 0
    repaired_timestamps: int = 0
    skipped_duplicates: int = 0
    reconciled_missing_alerts: int = 0
    per_group: list[RecoveryGroupStats] = field(default_factory=list)


def _sender_display_name(sender, fallback_id: int) -> str:
    if sender is None:
        return str(fallback_id)
    first = getattr(sender, "first_name", None) or ""
    last = getattr(sender, "last_name", None) or ""
    full = " ".join(part for part in (first.strip(), last.strip()) if part).strip()
    if full:
        return full
    title = (getattr(sender, "title", None) or "").strip()
    if title:
        return title
    username = (getattr(sender, "username", None) or "").strip()
    return username or str(fallback_id)


def _find_matching_pin(
    existing_pins: list[PinHistory],
    *,
    sent_local: datetime,
    lat: float,
    lon: float,
) -> PinHistory | None:
    for pin in existing_pins:
        stamp = pin.event_at or pin.pinned_at
        if stamp is None:
            continue
        if abs((stamp - sent_local).total_seconds()) > PIN_TIME_TOLERANCE_SECONDS:
            continue
        if abs(float(pin.lat) - float(lat)) <= PIN_COORD_TOLERANCE_DEGREES and abs(
            float(pin.lon) - float(lon)
        ) <= PIN_COORD_TOLERANCE_DEGREES:
            return pin
    return None


def _repair_report_timestamp(report: SalePhotoReport, sent_local: datetime, *, dry_run: bool) -> bool:
    if report.submitted_at is None:
        return False
    if abs((report.submitted_at - sent_local).total_seconds()) <= TIMESTAMP_REPAIR_THRESHOLD_SECONDS:
        return False
    if not dry_run:
        report.submitted_at = sent_local
        report.window_start = get_current_interval_start(sent_local, settings.ALERT_INTERVAL_MINUTES)
        report.window_end = get_current_interval_end(sent_local, settings.ALERT_INTERVAL_MINUTES)
    return True


def backfill_group_messages(
    db: Session,
    *,
    group_id: int,
    group_name: str | None,
    messages: Iterable[RecoveredMessage],
    dry_run: bool = False,
) -> RecoveryGroupStats:
    """Persist missing pins/reports from historical Telegram messages idempotently."""
    msg_list = list(messages)
    stats = RecoveryGroupStats(
        group_id=group_id,
        group_name=group_name or str(group_id),
        scanned_messages=len(msg_list),
    )
    if not msg_list:
        return stats

    messages_by_id = {m.message_id: m for m in msg_list}
    ordered = sorted(msg_list, key=lambda m: (m.sent_at, m.message_id))

    sent_locals = [_to_local_naive(m.sent_at) or m.sent_at for m in ordered]
    min_local = min(sent_locals) - timedelta(seconds=60)
    max_local = max(sent_locals) + timedelta(seconds=60)

    pin_stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    existing_pins_by_user: dict[int, list[PinHistory]] = {}
    for pin in (
        db.query(PinHistory)
        .filter(
            PinHistory.group_id == group_id,
            pin_stamp >= min_local,
            pin_stamp <= max_local,
        )
        .all()
    ):
        existing_pins_by_user.setdefault(int(pin.telegram_user_id), []).append(pin)

    seen_albums: set[tuple[int, int, str]] = set()
    dirty = False

    for msg in ordered:
        sent_local = _to_local_naive(msg.sent_at) or msg.sent_at
        text = msg.text or ""
        replied = messages_by_id.get(msg.reply_to_message_id) if msg.reply_to_message_id else None
        same_reply_sender = bool(replied is not None and int(replied.sender_id) == int(msg.sender_id))

        # 1) Photos and structured text forms
        if msg.has_photo:
            ident = SalePhotoReport.telegram_message_id == msg.message_id
            if msg.media_group_id:
                ident = or_(ident, SalePhotoReport.media_group_id == msg.media_group_id)
            existing_rep = (
                db.query(SalePhotoReport)
                .filter_by(group_id=group_id, telegram_user_id=msg.sender_id)
                .filter(ident)
                .order_by(SalePhotoReport.id)
                .first()
            )
            if existing_rep is not None:
                if not dry_run and text and (not existing_rep.caption or looks_like_sale_form(text)):
                    existing_rep.caption = text
                    existing_rep.telegram_message_id = msg.message_id
                    dirty = True
                if _repair_report_timestamp(existing_rep, sent_local, dry_run=dry_run):
                    stats.repaired_timestamps += 1
                    dirty = True
                else:
                    stats.skipped_duplicates += 1
                if msg.media_group_id:
                    seen_albums.add((int(group_id), int(msg.sender_id), str(msg.media_group_id)))
            else:
                album_key = (
                    (int(group_id), int(msg.sender_id), str(msg.media_group_id))
                    if msg.media_group_id
                    else None
                )
                reply_has_gps = bool(same_reply_sender and replied and replied.lat is not None and replied.lon is not None)
                if album_key and not text.strip() and not reply_has_gps and album_key in seen_albums:
                    stats.skipped_duplicates += 1
                else:
                    if not dry_run:
                        save_sale_photo_report(
                            db,
                            msg.sender_id,
                            group_id,
                            group_name,
                            msg.sender_name,
                            msg.sender_username,
                            text,
                            replied.lat if reply_has_gps else None,
                            replied.lon if reply_has_gps else None,
                            msg.message_id,
                            replied.message_id if reply_has_gps and replied else None,
                            media_group_id=msg.media_group_id,
                            event_at=sent_local,
                        )
                    stats.recovered_reports += 1
                    if album_key:
                        seen_albums.add(album_key)
        elif text and not text.lstrip().startswith("/") and looks_like_sale_form(text):
            existing_rep = (
                db.query(SalePhotoReport)
                .filter_by(
                    group_id=group_id,
                    telegram_user_id=msg.sender_id,
                    telegram_message_id=msg.message_id,
                )
                .order_by(SalePhotoReport.id)
                .first()
            )
            if existing_rep is not None:
                if _repair_report_timestamp(existing_rep, sent_local, dry_run=dry_run):
                    stats.repaired_timestamps += 1
                    dirty = True
                else:
                    stats.skipped_duplicates += 1
            else:
                if not dry_run:
                    save_text_form_report(
                        db,
                        msg.sender_id,
                        group_id,
                        group_name,
                        msg.sender_name,
                        msg.sender_username,
                        text,
                        msg.message_id,
                        event_at=sent_local,
                    )
                stats.recovered_reports += 1

        # 2) GPS location pin
        if msg.lat is not None and msg.lon is not None:
            user_pins = existing_pins_by_user.setdefault(int(msg.sender_id), [])
            matched_pin = _find_matching_pin(user_pins, sent_local=sent_local, lat=msg.lat, lon=msg.lon)
            if matched_pin is not None:
                stats.skipped_duplicates += 1
                continue

            linked_caption = replied.text if (same_reply_sender and replied and replied.text) else None
            form_caption = linked_caption if (linked_caption and looks_like_sale_form(linked_caption)) else None
            if not dry_run:
                save_location(
                    db,
                    msg.sender_id,
                    group_id,
                    group_name,
                    msg.sender_name,
                    msg.sender_username,
                    msg.lat,
                    msg.lon,
                    event_at=sent_local,
                    pinned_at=sent_local,
                    report_caption=form_caption,
                )
                # Mirror handlers.receive_location Way 2 atomic proof when replying to own photo/form
                if same_reply_sender and replied and (replied.has_photo or form_caption):
                    save_sale_photo_report(
                        db,
                        msg.sender_id,
                        group_id,
                        group_name,
                        msg.sender_name,
                        msg.sender_username,
                        linked_caption or "",
                        msg.lat,
                        msg.lon,
                        replied.message_id,
                        msg.message_id,
                        media_group_id=replied.media_group_id if replied.has_photo else None,
                        event_at=sent_local,
                    )
                # Track synthetic pin in-memory so duplicate GeoLive edits/re-sends don't double-count in same batch
                user_pins.append(
                    PinHistory(
                        telegram_user_id=msg.sender_id,
                        group_id=group_id,
                        lat=msg.lat,
                        lon=msg.lon,
                        pinned_at=sent_local,
                        event_at=sent_local,
                    )
                )
            else:
                user_pins.append(
                    PinHistory(
                        telegram_user_id=msg.sender_id,
                        group_id=group_id,
                        lat=msg.lat,
                        lon=msg.lon,
                        pinned_at=sent_local,
                        event_at=sent_local,
                    )
                )
            stats.recovered_pins += 1

    if dirty and not dry_run:
        db.commit()

    return stats


def reconcile_missing_alerts_for_date(
    db: Session,
    target_date: date,
    *,
    group_id_filter: set[int] | None = None,
    dry_run: bool = False,
) -> int:
    """Remove false missing_sale_photo_alerts rows once recovered submissions exist in window."""
    day_start = datetime.combine(target_date, time.min)
    day_end = day_start + timedelta(days=1)

    query = db.query(MissingSalePhotoAlert).filter(
        MissingSalePhotoAlert.window_start >= day_start,
        MissingSalePhotoAlert.window_start < day_end,
    )
    if group_id_filter is not None:
        if not group_id_filter:
            return 0
        query = query.filter(MissingSalePhotoAlert.group_id.in_(group_id_filter))

    alerts = query.all()
    if not alerts:
        return 0

    buckets: dict[tuple[int, datetime, datetime], list[MissingSalePhotoAlert]] = {}
    for alert in alerts:
        buckets.setdefault((int(alert.group_id), alert.window_start, alert.window_end), []).append(alert)

    users_cache: dict[int, list[SAUser]] = {}
    cleared = 0

    for (gid, w_start, w_end), bucket_alerts in buckets.items():
        if gid not in users_cache:
            users_cache[gid] = (
                db.query(SAUser)
                .filter(
                    SAUser.group_id == gid,
                    SAUser.is_from_list == 1,
                    SAUser.region.isnot(None),
                    SAUser.dealer.isnot(None),
                    SAUser.position.isnot(None),
                )
                .all()
            )
        users = users_cache[gid]
        if not users:
            continue
        submitted_ids = _submitted_user_ids_for_interval(db, users, gid, w_start, w_end)
        submitted_tids = {int(u.telegram_user_id) for u in users if u.id in submitted_ids}
        for alert in bucket_alerts:
            if int(alert.telegram_user_id) in submitted_tids:
                cleared += 1
                if not dry_run:
                    db.delete(alert)

    if cleared and not dry_run:
        db.commit()
    return cleared


def _extract_geo(msg) -> tuple[float | None, float | None]:
    geo = getattr(msg, "geo", None)
    if geo is None:
        media = getattr(msg, "media", None)
        geo = getattr(media, "geo", None) if media is not None else None
    if geo is None:
        return None, None
    lat = getattr(geo, "lat", None)
    lon = getattr(geo, "long", None)
    if lat is None or lon is None:
        return None, None
    return float(lat), float(lon)


def _message_to_recovered(msg, sender) -> RecoveredMessage | None:
    if msg is None or getattr(msg, "date", None) is None:
        return None
    sender_id = getattr(msg, "sender_id", None)
    if not sender_id or int(sender_id) <= 0:
        return None
    if getattr(sender, "bot", False):
        return None

    lat, lon = _extract_geo(msg)
    has_photo = getattr(msg, "photo", None) is not None
    text = getattr(msg, "message", None) or getattr(msg, "raw_text", None) or ""
    if not has_photo and lat is None and not looks_like_sale_form(text):
        return None

    reply_header = getattr(msg, "reply_to", None)
    reply_msg_id = getattr(reply_header, "reply_to_msg_id", None) if reply_header else None
    grouped_id = getattr(msg, "grouped_id", None)

    return RecoveredMessage(
        message_id=int(msg.id),
        sender_id=int(sender_id),
        sender_name=_sender_display_name(sender, int(sender_id)),
        sender_username=getattr(sender, "username", None),
        sent_at=msg.date,
        text=text,
        has_photo=has_photo,
        lat=lat,
        lon=lon,
        reply_to_message_id=int(reply_msg_id) if reply_msg_id else None,
        media_group_id=str(grouped_id) if grouped_id else None,
    )


def telethon_is_configured() -> bool:
    """Return True when Telethon API credentials and a session/StringSession exist."""
    cfg = get_settings()
    if not cfg.TELETHON_API_ID or not cfg.TELETHON_API_HASH.strip():
        return False
    if cfg.TELETHON_STRING_SESSION.strip():
        return True
    session_file = Path(cfg.TELETHON_SESSION_FILE)
    return session_file.is_file()


async def recover_today_if_configured() -> RecoverySummary | None:
    """Safely backfill today's history in the background when Telethon is configured."""
    cfg = get_settings()
    if not telethon_is_configured():
        return None
    today = local_now().date()
    try:
        summary = await sync_history_with_telethon(
            api_id=int(cfg.TELETHON_API_ID),
            api_hash=cfg.TELETHON_API_HASH.strip(),
            target_date=today,
            start_time=time(0, 0),
            end_time=time(23, 59, 59),
            session_path=cfg.TELETHON_SESSION_FILE,
            string_session=cfg.TELETHON_STRING_SESSION.strip() or None,
            dry_run=False,
            reconcile_missing=True,
        )
        logger.info(
            "Startup Telethon recovery completed: date=%s groups=%s/%s pins=+%s reports=+%s repaired=%s cleared_alerts=%s",
            summary.target_date,
            summary.groups_synced,
            summary.groups_total,
            summary.recovered_pins,
            summary.recovered_reports,
            summary.repaired_timestamps,
            summary.reconciled_missing_alerts,
        )
        return summary
    except Exception:
        logger.exception("Startup Telethon recovery failed")
        return None


async def sync_history_with_telethon(
    *,
    api_id: int,
    api_hash: str,
    target_date: date,
    start_time: time = time(0, 0),
    end_time: time = time(23, 59, 59),
    session_path: str | None = None,
    string_session: str | None = None,
    group_filter: str | None = None,
    dry_run: bool = True,
    reconcile_missing: bool = True,
) -> RecoverySummary:
    """Scan active DB groups via Telethon and recover missing pins & reports for target_date."""
    from telethon import TelegramClient, utils
    from telethon.sessions import StringSession

    async with _RECOVERY_LOCK:
        tz = ZoneInfo(settings.APP_TIMEZONE)
        start_local = datetime.combine(target_date, start_time, tzinfo=tz)
        end_local = datetime.combine(target_date, end_time, tzinfo=tz)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc) + timedelta(seconds=1)

        if string_session:
            session = StringSession(string_session.strip())
        else:
            resolved = Path(session_path or settings.TELETHON_SESSION_FILE)
            if not resolved.is_file():
                raise RuntimeError(
                    "Telethon session file not found. Run --login first or set TELETHON_STRING_SESSION."
                )
            session = str(resolved)

        summary = RecoverySummary(target_date=target_date, dry_run=dry_run)

        db = SessionLocal()
        try:
            def _load_target_groups():
                groups_query = db.query(TelegramGroup).filter(
                    TelegramGroup.is_active == 1,
                    TelegramGroup.group_id < 0,
                )
                all_groups = groups_query.order_by(TelegramGroup.group_name.asc()).all()
                if group_filter:
                    needle = group_filter.strip().lower()
                    return [
                        (int(g.group_id), g.group_name)
                        for g in all_groups
                        if needle in (g.group_name or str(g.group_id)).lower()
                    ]
                return [(int(g.group_id), g.group_name) for g in all_groups]

            target_groups = await asyncio.to_thread(_load_target_groups)
        finally:
            db.close()

        summary.groups_total = len(target_groups)
        if not target_groups:
            return summary

        client = TelegramClient(session, int(api_id), api_hash.strip())
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telethon session is not authorized. Run scripts/recover_telegram_history.py --login first."
                )

            dialogs_by_peer_id = {}
            async for dialog in client.iter_dialogs():
                peer_id = int(utils.get_peer_id(dialog.entity))
                dialogs_by_peer_id[peer_id] = dialog.entity

            sender_cache: dict[int, object] = {}
            synced_group_ids: set[int] = set()

            for group_id, group_name in target_groups:
                entity = dialogs_by_peer_id.get(group_id)
                if entity is None:
                    summary.groups_missing_dialog.append(f"{group_name} ({group_id})")
                    continue

                recovered_msgs: list[RecoveredMessage] = []
                try:
                    async for msg in client.iter_messages(entity, offset_date=end_utc, reverse=False):
                        if msg.date is None:
                            continue
                        msg_utc = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                        if msg_utc < start_utc:
                            break
                        if msg_utc > end_utc:
                            continue

                        sid = getattr(msg, "sender_id", None)
                        sender = getattr(msg, "sender", None)
                        if sid and sender is None:
                            sender = sender_cache.get(int(sid))
                            if sender is None:
                                try:
                                    sender = await msg.get_sender()
                                except Exception:
                                    sender = None
                                if sender is not None:
                                    sender_cache[int(sid)] = sender
                        elif sid and sender is not None:
                            sender_cache[int(sid)] = sender

                        item = _message_to_recovered(msg, sender)
                        if item is not None:
                            recovered_msgs.append(item)
                except Exception as exc:
                    logger.warning("Failed reading Telethon history for %s (%s): %s", group_name, group_id, exc)
                    summary.groups_missing_dialog.append(f"{group_name} ({group_id}): {exc}")
                    continue

                def _persist_group_sync(gid=group_id, gname=group_name, batch=recovered_msgs):
                    gdb = SessionLocal()
                    try:
                        return backfill_group_messages(
                            gdb,
                            group_id=gid,
                            group_name=gname,
                            messages=batch,
                            dry_run=dry_run,
                        )
                    finally:
                        gdb.close()

                stats = await asyncio.to_thread(_persist_group_sync)

                synced_group_ids.add(group_id)
                summary.groups_synced += 1
                summary.recovered_pins += stats.recovered_pins
                summary.recovered_reports += stats.recovered_reports
                summary.repaired_timestamps += stats.repaired_timestamps
                summary.skipped_duplicates += stats.skipped_duplicates
                summary.per_group.append(stats)

                await asyncio.sleep(0.15)

            if reconcile_missing and synced_group_ids:
                def _reconcile_sync():
                    rdb = SessionLocal()
                    try:
                        return reconcile_missing_alerts_for_date(
                            rdb,
                            target_date,
                            group_id_filter=synced_group_ids if group_filter else None,
                            dry_run=dry_run,
                        )
                    finally:
                        rdb.close()

                summary.reconciled_missing_alerts = await asyncio.to_thread(_reconcile_sync)
        finally:
            await client.disconnect()

        return summary
