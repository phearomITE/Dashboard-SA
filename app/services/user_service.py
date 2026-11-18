# app/services/user_service.py

from __future__ import annotations

import hashlib
import re
import time as _time
import unicodedata
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.data.controllers import Controller, controllers_for_region, normalize_group_code, remove_controller_for_region, replace_group_controller_assignments, set_controller_for_region
from app.data.sa_members import MASTER_ASSIGNMENTS, SA_MEMBERS, normalize_group, normalize_username
from app.database.models import (
    ControllerGroupAssignment,
    MissingSalePhotoAlert,
    SAAlertExclusion,
    SAList,
    PinHistory,
    RegionalController,
    SAUser,
    SalePhotoReport,
    TelegramGroup,
    VoiceReasonHistory,
)
from app.services.shift_config import flow_key_for_position
from app.services.time_service import get_current_interval_end, get_current_interval_start, local_now
from app.services.live_map import update_pin_route

settings = get_settings()
APPROVED_POSITIONS = {"SA", "Commando", "SA Team Leader"}

# Match the displayed alert window exactly at the start boundary.
# A submission from the previous minute/window must not be credited forward.
ALERT_GRACE_MINUTES = 0
ALERT_END_GRACE_SECONDS = 59


def interval_start_with_grace(interval_start: datetime) -> datetime:
    return interval_start - timedelta(minutes=ALERT_GRACE_MINUTES)


def interval_end_with_grace(interval_end: datetime) -> datetime:
    # Count messages in the same displayed minute as the window end.
    # Example: displayed window 09:06-09:29 accepts 09:29:30 if it arrived before the alert ran.
    return interval_end + timedelta(seconds=ALERT_END_GRACE_SECONDS)


def normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_name(value: str | None) -> str:
    """Normalize Telegram/Excel names without losing Khmer letters."""
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(ch for ch in text if ch.isalnum())


def identity_name_core(
    value: str | None,
    *,
    region: str | None = None,
    dealer: str | None = None,
) -> str:
    """Return the stable person-name part of an SA display name.

    Telegram display names frequently omit or add roster suffixes such as
    ``SA Water``, ``R2`` and ``KSV5``.  Those labels are not part of the
    person's identity.  Removing only known role/assignment words lets a
    no-username user bind automatically, while the caller still requires the
    resulting core to identify exactly one official member in the group.
    """
    text = unicodedata.normalize("NFKC", value or "").casefold()
    tokens = [token for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE) if token]
    ignored = {
        "sa", "water", "commando", "camondo", "team", "leader", "specialist",
        "outlet", "outlets", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9",
    }
    for assignment in (region, dealer):
        if assignment:
            ignored.add(normalize_name(assignment))
    kept = [normalize_name(token) for token in tokens if normalize_name(token) not in ignored]
    return "".join(token for token in kept if token)


def is_group_chat_id(group_id: int | None) -> bool:
    # Telegram groups and supergroups use negative chat IDs.
    return bool(group_id is not None and group_id < 0)


def looks_like_sale_form(text: str | None) -> bool:
    """Return True only for outlet/order form text, not normal chat messages.

    Users send a text form + a Google Maps pin. This bot should count that
    as a valid submission. Chat messages like "busy with customer" should
    not count as a report.
    """
    raw = text or ""
    t = normalize_text(raw)
    if not t:
        return False
    strong_keys = (
        "id:", "dealer:", "outlet name", "telephone:", "outlet type",
        "location(ទីតាំង)", "ការកម្មង់", "pre-order",
        "c cola", "water pet", "wurkz", "ize", "exprez", "cbc can",
    )
    hits = sum(1 for key in strong_keys if key in t)
    # Require at least 2 form markers to avoid counting casual text.
    return hits >= 2


def telegram_name(user) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return full_name or user.username or str(user.id)


def chat_name(chat) -> str | None:
    if chat is None:
        return None
    return getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat.id)


def create_maps_link(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    return f"https://maps.google.com/maps?q={lat},{lon}&ll={lat},{lon}&z=16"


def _placeholder_user_id(group_name: str | None, name: str, username: str | None) -> int:
    raw = f"{group_name or ''}|{name}|{normalize_username(username) or ''}"
    digest = int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12], 16)
    return -1 * (digest % 900_000_000_000 + 100_000_000_000)


def canonical_working_time_for_member(member: dict) -> str:
    """Return one of the four approved schedules from the official SA list."""
    explicit = (member.get("working_time") or "").strip()
    approved = {
        "7:00am-5:00pm",
        "6:30am-4:30pm",
        "7:30am-5:00pm",
        "9:00am-8:00pm",
    }
    if explicit in approved:
        return explicit

    # Backward-compatible fallback for manually constructed legacy rows.
    name_key = normalize_name(member.get("name"))
    dealer = (member.get("dealer") or "").strip().upper()
    position = (member.get("position") or "").strip().casefold()

    if name_key == normalize_name("OUUN MAKARA CAMMONDO R5"):
        return "9:00am-8:00pm"
    if name_key == normalize_name("CHOU SOK LIM SA. CA9"):
        return "7:30am-5:00pm"
    if dealer in {"CA3", "CA6"} and ("sa" in position or "water" in position):
        return "6:30am-4:30pm"
    if "water" in position:
        return "6:30am-4:30pm"
    return "7:00am-5:00pm"


def _controller_text(region: str | None) -> str:
    return ", ".join(controller.name for controller in controllers_for_region(region))


def seed_sa_list(db: Session) -> int:
    """Synchronize official rows while preserving controller-managed fields.

    Excel rows are authoritative for member identity and group assignment.
    Position, working time and active status on an existing row remain editable
    through management commands. Stale Excel rows are removed so an updated
    name cannot leave a second alert identity behind; manual /add rows are never
    removed here.
    """
    official_rows = db.query(SAList).filter(SAList.source_type == "excel").all()
    by_source_key = {row.source_key: row for row in official_rows if row.source_key}
    by_key = {
        (normalize_group(row.group_name), normalize_name(row.name)): row
        for row in official_rows
    }
    by_telegram_id = {
        (normalize_group(row.group_name), int(row.telegram_user_id)): row
        for row in official_rows
        if int(row.telegram_user_id or 0) > 0
    }
    by_username: dict[str, SAList] = {}
    duplicate_usernames: set[str] = set()
    for row in official_rows:
        username = normalize_username(row.username)
        if not username:
            continue
        if username in by_username:
            duplicate_usernames.add(username)
        else:
            by_username[username] = row
    for username in duplicate_usernames:
        by_username.pop(username, None)

    created = 0
    changed = False
    retained_ids: set[int] = set()
    now = local_now()
    roster_source = db.info.get("official_roster", MASTER_ASSIGNMENTS)
    if not roster_source and "official_roster" not in db.info:
        return 0
    if "official_roster" not in db.info and official_rows:
        return 0
    for member in roster_source:
        member_user_id = int(member.get("telegram_user_id") or 0)
        key = (normalize_group(member.get("group_name")), normalize_name(member.get("name")))
        username = normalize_username(member.get("username"))
        identity = (f"id:{member_user_id}" if member_user_id > 0
                    else f"username:{username}" if username else f"name:{key[1]}")
        source_key = f"{key[0]}|{identity}"
        # Numeric Telegram ID is authoritative. This also preserves a profile
        # name hydrated after the previous startup when Excel names are blank.
        row = by_source_key.get(source_key)
        if row is None:
            row = by_telegram_id.get((key[0], member_user_id)) if member_user_id > 0 else None
        if row is None:
            row = by_key.get(key)
        if row is None and username and member_user_id <= 0:
            row = by_username.get(username)

        region = (member.get("region") or "").strip().upper()
        if row is not None:
            retained_ids.add(row.id)
            if row.source_key != source_key:
                row.source_key = source_key
                changed = True
            if row.dashboard_override or row.is_deleted:
                continue
            hydrated = member_user_id > 0 and int(row.telegram_user_id or 0) == member_user_id
            identity_values = {
                "region": region,
                "dealer": (member.get("dealer") or "").strip().upper(),
                "name": row.name if hydrated else (member.get("name") or "Unknown").strip(),
                "username": row.username if hydrated else username,
                "group_name": (member.get("group_name") or "").strip(),
                "controller": _controller_text(region),
                "telegram_user_id": int(member.get("telegram_user_id") or 0) or None,
            }
            if any(getattr(row, field) != value for field, value in identity_values.items()):
                for field, value in identity_values.items():
                    setattr(row, field, value)
                row.updated_at = now
                changed = True
            continue

        row = SAList(
            region=region,
            dealer=(member.get("dealer") or "").strip().upper(),
            name=(member.get("name") or "Unknown").strip(),
            position=(member.get("position") or "SA").strip(),
            working_time=canonical_working_time_for_member(member),
            username=username,
            telegram_user_id=int(member.get("telegram_user_id") or 0) or None,
            group_name=(member.get("group_name") or "").strip(),
            controller=_controller_text(region),
            source_type="excel",
            source_key=source_key,
            is_active=member.get("is_active", 1),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        created += 1
        changed = True

    for row in official_rows:
        if row.id not in retained_ids and not row.dashboard_override and not row.is_deleted:
            db.delete(row)
            changed = True

    if changed:
        db.commit()
    return created


def load_controller_overrides(db: Session) -> int:
    """Load persisted controller changes into the shared authorization map."""
    count = 0
    removed_stale = False
    for row in db.query(RegionalController).all():
        if int(row.user_id) <= 0 or "neab sona" in (row.name or "").lower() or "," in (row.name or ""):
            db.delete(row)
            removed_stale = True
            continue
        set_controller_for_region(
            row.region,
            Controller(name=row.name, username=row.username, user_id=int(row.user_id)),
        )
        count += 1
    assignments = [
        (row.group_code, Controller(row.name, row.username, int(row.user_id)))
        for row in db.query(ControllerGroupAssignment).order_by(ControllerGroupAssignment.id).all()
    ]
    replace_group_controller_assignments(assignments)
    updated_roster = False
    for sa_row in db.query(SAList).filter(SAList.is_deleted == 0).all():
        expected = _controller_text(sa_row.region)
        if expected and (not sa_row.controller or "neab sona" in (sa_row.controller or "").lower()):
            sa_row.controller = expected
            updated_roster = True
    if removed_stale or updated_roster:
        db.commit()
    return count + len(assignments)


def add_controller_groups(db: Session, *, user_id: int, group_codes: list[str], name: str,
                          username: str, changed_by_user_id: int) -> tuple[list[str], list[str]]:
    if int(user_id) <= 0:
        raise ValueError("Controller Telegram ID must be a positive number")
    codes = list(dict.fromkeys(normalize_group_code(code) for code in group_codes))
    if not codes:
        raise ValueError("At least one group is required")
    added, existing = [], []
    for code in codes:
        row = db.query(ControllerGroupAssignment).filter_by(user_id=int(user_id), group_code=code).first()
        if row:
            existing.append(code)
            row.name, row.username, row.updated_at = name, normalize_username(username) or "", local_now()
        else:
            db.add(ControllerGroupAssignment(user_id=int(user_id), group_code=code, name=name,
                username=normalize_username(username) or "", changed_by_user_id=int(changed_by_user_id),
                created_at=local_now(), updated_at=local_now()))
            added.append(code)
    db.commit()
    load_controller_overrides(db)
    return added, existing


def remove_controller_by_user_id(db: Session, *, user_id: int) -> list[str]:
    rows = db.query(ControllerGroupAssignment).filter_by(user_id=int(user_id)).all()
    removed = sorted({row.group_code for row in rows})
    for row in rows:
        db.delete(row)
    db.commit()
    load_controller_overrides(db)
    return removed


def change_region_controller(
    db: Session,
    *,
    region: str,
    name: str,
    username: str,
    user_id: int,
    changed_by_user_id: int,
) -> Controller:
    normalized_region = (region or "").strip().upper()
    if normalized_region not in {f"R{i}" for i in range(1, 9)}:
        raise ValueError("Region must be R1-R8")
    clean_name = (name or "").strip()
    clean_username = normalize_username(username) or ""
    if not clean_name or not clean_username or int(user_id) <= 0:
        raise ValueError("Controller name, username and positive Telegram user ID are required")
    row = db.get(RegionalController, normalized_region)
    if row is None:
        row = RegionalController(region=normalized_region)
        db.add(row)
    row.name = clean_name
    row.username = clean_username
    row.user_id = int(user_id)
    row.changed_by_user_id = int(changed_by_user_id)
    row.updated_at = local_now()
    for sa_row in db.query(SAList).filter(SAList.region == normalized_region).all():
        sa_row.controller = clean_name
        sa_row.updated_at = local_now()
    db.commit()
    controller = Controller(name=clean_name, username=clean_username, user_id=int(user_id))
    set_controller_for_region(normalized_region, controller)
    return controller


def remove_region_controller(db: Session, *, region: str, changed_by_user_id: int) -> None:
    """Persist an intentionally unassigned region until /change controller is used."""
    normalized = (region or "").strip().upper()
    if normalized not in {f"R{i}" for i in range(1, 9)}:
        raise ValueError("Region must be R1-R8")
    row = db.get(RegionalController, normalized)
    if row is None:
        row = RegionalController(region=normalized)
        db.add(row)
    row.name, row.username, row.user_id = "Removed", "removed", 0
    row.changed_by_user_id = int(changed_by_user_id)
    row.updated_at = local_now()
    for sa_row in db.query(SAList).filter(SAList.region == normalized).all():
        sa_row.controller = None
    db.commit()
    remove_controller_for_region(normalized)


def sa_list_members_for_group(db: Session, group_name: str | None, *, include_inactive: bool = False) -> list[dict]:
    seed_sa_list(db)
    target = normalize_group(group_name)
    query = db.query(SAList).filter(SAList.is_deleted == 0)
    if not include_inactive:
        query = query.filter(SAList.is_active == 1)
    rows = [row for row in query.all() if normalize_group(row.group_name) == target]
    return [{
        "region": row.region,
        "dealer": row.dealer,
        "name": row.name,
        "position": row.position,
        "working_time": row.working_time,
        "username": row.username,
        "group_name": row.group_name,
        "controller": row.controller,
        "telegram_user_id": row.telegram_user_id,
        "alert_time": None,
        "source_type": row.source_type,
    } for row in rows]


def _apply_member_fields(item: SAUser, member: dict) -> None:
    item.region = member.get("region")
    item.dealer = member.get("dealer")
    item.position = member.get("position")

    # A controller/admin working-time change is an intentional operational
    # override and must survive future /sync_sa_list runs.
    excel_working_time = member.get("working_time")
    item.working_time = item.working_time_override or excel_working_time

    # When an override exists, it is the source of truth for alert flow.
    # Otherwise, continue using the workbook's explicit alert time first.
    alert_time = None if item.working_time_override else member.get("alert_time")
    item.flow_key = flow_key_for_position(
        item.position,
        item.working_time,
        alert_time,
    )
    item.is_from_list = 1
    item.source_type = member.get("source_type") or "excel"


def _official_member_keys(db: Session, group_name: str | None) -> tuple[set[str], set[str], set[int]]:
    members = sa_list_members_for_group(db, group_name)
    official_usernames = {normalize_username(m.get("username")) for m in members if normalize_username(m.get("username"))}
    official_names = {normalize_name(m.get("name")) for m in members if normalize_name(m.get("name"))}
    official_ids = {
        int(m.get("telegram_user_id"))
        for m in members
        if m.get("telegram_user_id") not in (None, "") and int(m.get("telegram_user_id")) > 0
    }
    return official_usernames, official_names, official_ids


def _exclusion_rows(db: Session, group_id: int) -> list[SAAlertExclusion]:
    return db.query(SAAlertExclusion).filter(SAAlertExclusion.group_id == group_id).all()


def _member_is_excluded(member: dict, exclusions: list[SAAlertExclusion]) -> bool:
    member_name = normalize_name(member.get("name"))
    member_username = normalize_username(member.get("username")) or ""
    member_user_id = int(member.get("telegram_user_id") or 0)
    for exclusion in exclusions:
        if member_user_id > 0 and exclusion.telegram_user_id == member_user_id:
            return True
        if member_username and exclusion.normalized_username == member_username:
            return True
        if member_name and exclusion.normalized_name == member_name:
            return True
    return False


def cleanup_private_and_stale_rows(db: Session, group_id: int | None = None, group_name: str | None = None) -> int:
    """Remove rows that create wrong mentions.

    Fixes these cases:
    - private /start created a row in telegram_groups (positive group_id)
    - private /start or duplicate rows created sa_users with null region/dealer/position
    - old username rows remain after an SA changes username

    This is safe because scheduled alerts should only use real groups (negative IDs)
    and official Excel-list users.
    """
    deleted = 0

    # Never keep private chat registrations in the group table.
    for tg in db.query(TelegramGroup).filter(TelegramGroup.group_id > 0).all():
        db.delete(tg)
        deleted += 1

    # Remove private chat users. They should not be part of group alert tracking.
    for u in db.query(SAUser).filter(SAUser.group_id > 0).all():
        db.delete(u)
        deleted += 1

    if group_id is not None and is_group_chat_id(group_id):
        official_usernames, official_names, official_ids = _official_member_keys(db, group_name)
        if official_usernames or official_names:
            rows = db.query(SAUser).filter(SAUser.group_id == group_id).all()
            for u in rows:
                uname = normalize_username(u.username)
                nkey = normalize_name(u.name)
                official_match = (uname in official_usernames) or (nkey in official_names) or (u.telegram_user_id in official_ids)

                # Delete non-official/null-metadata rows in this group.
                # These are the rows that caused examples like:
                #   NENG THEAREN personal chat in telegram_groups
                #   Da Ro duplicate row with null region/dealer/position
                if (u.source_type or "excel") == "manual":
                    continue
                if not official_match and (u.is_from_list != 1 or not u.region or not u.dealer or not u.position):
                    db.delete(u)
                    deleted += 1

    if deleted:
        db.commit()
    return deleted


def _best_candidate(candidates: list[SAUser], official_username: str | None) -> SAUser | None:
    if not candidates:
        return None

    def score(u: SAUser) -> tuple[int, int, int, int, int]:
        uname_match = 1 if official_username and normalize_username(u.username) == official_username else 0
        real_id = 1 if (u.telegram_user_id or 0) > 0 else 0
        from_list = 1 if u.is_from_list == 1 else 0
        has_meta = 1 if u.region and u.dealer and u.position else 0
        # Prefer older row if otherwise equal, because it likely has history.
        older = -int(u.id or 0)
        return (uname_match, real_id, from_list, has_meta, older)

    return sorted(candidates, key=score, reverse=True)[0]



def _move_activity_refs_to_user(db: Session, from_user_id: int | None, to_user_id: int | None) -> None:
    """Move form/pin canonical refs before duplicate SA rows are deleted."""
    if not from_user_id or not to_user_id or int(from_user_id) == int(to_user_id):
        return
    db.query(PinHistory).filter(PinHistory.sa_user_id == int(from_user_id)).update(
        {PinHistory.sa_user_id: int(to_user_id)}, synchronize_session=False
    )
    db.query(SalePhotoReport).filter(SalePhotoReport.sa_user_id == int(from_user_id)).update(
        {SalePhotoReport.sa_user_id: int(to_user_id)}, synchronize_session=False
    )


def sync_group_members_from_list(db: Session, group_id: int, group_name: str | None) -> int:
    """Seed/update SA users from the uploaded Excel list for this Telegram group.

    The Excel list is the source of truth. This function also merges/deletes
    stale duplicates so alerts mention only correct official users.
    """
    if not is_group_chat_id(group_id):
        cleanup_private_and_stale_rows(db)
        return 0

    members = sa_list_members_for_group(db, group_name)
    if not members:
        cleanup_private_and_stale_rows(db, group_id, group_name)
        return 0

    now = local_now()
    changed = 0
    kept_ids: set[int] = set()
    exclusions = _exclusion_rows(db, group_id)

    # First pass: for every official Excel member, pick one DB row to keep.
    for member in members:
        if _member_is_excluded(member, exclusions):
            continue
        username_norm = normalize_username(member.get("username"))
        name_norm = normalize_name(member.get("name"))
        member_user_id = int(member.get("telegram_user_id") or 0)

        q = db.query(SAUser).filter(SAUser.group_id == group_id)
        candidates: list[SAUser] = []
        if member_user_id > 0:
            candidates.extend(q.filter(SAUser.telegram_user_id == member_user_id).all())
        if username_norm:
            candidates.extend(
                q.filter(
                    or_(
                        func.lower(func.replace(SAUser.username, "@", "")) == username_norm,
                        func.lower(func.replace(SAUser.official_username, "@", "")) == username_norm,
                    )
                ).all()
            )
        if name_norm:
            by_name = q.all()
            candidates.extend([
                u for u in by_name
                if normalize_name(u.name) == name_norm
                or normalize_name(u.official_name) == name_norm
            ])

        # De-duplicate candidates by PK.
        unique = {}
        for c in candidates:
            unique[c.id] = c
        candidates = list(unique.values())

        item = _best_candidate(candidates, username_norm)

        if item is None:
            item = SAUser(
                telegram_user_id=(
                    member_user_id
                    if member_user_id > 0
                    else _placeholder_user_id(group_name, member.get("name") or "", member.get("username"))
                ),
                group_id=group_id,
                group_name=group_name,
                name=member.get("name") or "Unknown",
                username=username_norm,
                official_name=member.get("name") or "Unknown",
                official_username=username_norm,
                created_at=now,
                updated_at=now,
            )
            db.add(item)
            db.flush()
            changed += 1
        else:
            # Preserve a real Telegram ID learned from live activity. A workbook
            # ID can be stale, so only use it while the row is still unbound.
            if member_user_id > 0 and (item.telegram_user_id or 0) <= 0:
                item.telegram_user_id = member_user_id
            item.group_name = group_name
            # Preserve the live Telegram name/username once a real account is bound.
            # The workbook identity is stored separately for future matching.
            if (item.telegram_user_id or 0) <= 0:
                item.name = member.get("name") or item.name
                item.username = username_norm
            item.updated_at = now

        item.official_name = member.get("name") or item.official_name or item.name
        item.official_username = username_norm
        _apply_member_fields(item, member)
        db.flush()
        kept_ids.add(item.id)

        # Delete other duplicate rows for same official username/name in same group.
        for dup in candidates:
            if dup.id != item.id:
                _move_activity_refs_to_user(db, dup.id, item.id)
                db.delete(dup)
                changed += 1

    db.commit()

    # Second pass: remove every old Excel row that is no longer in this group's
    # current list. This is the key protection against alerting transferred or
    # removed SA users after the workbook is updated.
    official_usernames, official_names, official_ids = _official_member_keys(db, group_name)
    rows = db.query(SAUser).filter(SAUser.group_id == group_id).all()
    removed = 0
    for u in rows:
        if (u.source_type or "excel") == "manual":
            # /add records remain active even when they are not yet in the Excel list.
            continue
        uname = normalize_username(u.official_username or u.username)
        nkey = normalize_name(u.official_name or u.name)
        official_match = (uname in official_usernames) or (nkey in official_names) or (u.telegram_user_id in official_ids)
        if u.is_from_list == 1 and u.id not in kept_ids:
            db.delete(u)
            removed += 1
        elif not official_match and (u.is_from_list != 1 or not u.region or not u.dealer or not u.position):
            db.delete(u)
            removed += 1

    if removed:
        db.commit()
        changed += removed

    cleanup_private_and_stale_rows(db, group_id, group_name)
    return changed


def register_group(
    db: Session,
    group_id: int,
    group_name: str | None,
    *,
    sync_members: bool = True,
) -> TelegramGroup | None:
    # Do not register private chats as alert groups.
    # This prevents rows like a user's personal name inside telegram_groups.
    if not is_group_chat_id(group_id):
        cleanup_private_and_stale_rows(db)
        return None

    now = local_now()
    group = db.query(TelegramGroup).filter(TelegramGroup.group_id == group_id).first()

    if group is None:
        group = TelegramGroup(
            group_id=group_id,
            group_name=group_name,
            is_active=1,
            created_at=now,
            updated_at=now,
        )
        db.add(group)
    else:
        group.group_name = group_name
        group.updated_at = now

    db.commit()
    db.refresh(group)
    if sync_members:
        sync_group_members_from_list(db, group_id, group_name)
    return group


def _find_existing_user(db: Session, telegram_user_id: int, username: str | None, group_id: int) -> SAUser | None:
    # A bound row may only be selected by Telegram's immutable numeric ID.
    # Username is intentionally *not* a fallback here: usernames can change or
    # be reassigned, and using one here could overwrite another person's row.
    return (
        db.query(SAUser)
        .filter(SAUser.telegram_user_id == telegram_user_id, SAUser.group_id == group_id)
        .first()
    )


def _official_member_for_user(
    db: Session,
    group_name: str | None,
    telegram_user_id: int,
    name: str | None,
    username: str | None,
) -> dict | None:
    username_norm = normalize_username(username)
    name_norm = normalize_name(name)
    members = sa_list_members_for_group(db, group_name)
    # Matching priority is strict and every non-ID fallback must be unique in
    # this exact Telegram group. Ambiguous data requires /bind_sa; never guess.
    id_matches = [
        member for member in members
        if int(member.get("telegram_user_id") or 0) > 0
        and int(member.get("telegram_user_id") or 0) == telegram_user_id
    ]
    if len(id_matches) == 1:
        return id_matches[0]
    if len(id_matches) > 1:
        return None

    if username_norm:
        username_matches = [
            member for member in members
            if normalize_username(member.get("username")) == username_norm
        ]
        if len(username_matches) == 1:
            return username_matches[0]
        if len(username_matches) > 1:
            return None

    if name_norm:
        name_matches = [
            member for member in members
            if normalize_name(member.get("name")) == name_norm
        ]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1:
            return None

    # A user without a username/roster Telegram ID must still bind when their
    # live display name differs only by role/region/dealer suffixes.  Accept
    # this fallback only when it resolves to exactly one official member in
    # this group; ambiguity is deliberately rejected to prevent cross-user
    # credit and wrong mentions.
    core_matches = []
    for member in members:
        live_core = identity_name_core(
            name,
            region=member.get("region"),
            dealer=member.get("dealer"),
        )
        official_core = identity_name_core(
            member.get("name"),
            region=member.get("region"),
            dealer=member.get("dealer"),
        )
        if live_core and official_core and live_core == official_core:
            core_matches.append(member)
    if len(core_matches) == 1:
        return core_matches[0]
    return None


def sync_bound_telegram_profile(
    db: Session,
    *,
    telegram_user_id: int,
    group_id: int,
    current_name: str,
    current_username: str | None,
) -> SAUser | None:
    """Update a bound SA row when their Telegram name or username changes.

    Telegram user ID is stable and is the authoritative account identity.
    Region, dealer, position, and working time remain controlled by the official
    workbook; only the live Telegram display name and username are refreshed.
    """
    item = db.query(SAUser).filter(
        SAUser.group_id == group_id,
        SAUser.telegram_user_id == telegram_user_id,
        SAUser.is_from_list == 1,
    ).first()
    if item is None:
        return None

    clean_name = (current_name or "").strip() or item.name
    clean_username = normalize_username(current_username)
    old_name, old_username = item.name, item.username
    changed = False

    # One Telegram account can appear in more than one registered chat. Update
    # every row carrying this authoritative ID so no old profile survives as a
    # second active alert identity.
    for bound in db.query(SAUser).filter(
        SAUser.telegram_user_id == telegram_user_id,
        SAUser.is_from_list == 1,
    ).all():
        if bound.name != clean_name or normalize_username(bound.username) != clean_username:
            bound.name = clean_name
            bound.username = clean_username
            bound.updated_at = local_now()
            changed = True

    # Keep the editable/export roster aligned with the canonical live profile.
    for roster in db.query(SAList).filter(SAList.telegram_user_id == telegram_user_id).all():
        if roster.name != clean_name or normalize_username(roster.username) != clean_username:
            roster.name = clean_name
            roster.username = clean_username
            roster.updated_at = local_now()
            changed = True

    if changed:
        db.commit()
        print(
            f"Telegram profile updated user_id={telegram_user_id} "
            f"name={old_name!r}->{clean_name!r} username={old_username!r}->{clean_username!r}"
        )
    db.refresh(item)
    return item


def sync_roster_profile_by_telegram_id(
    db: Session,
    *,
    telegram_user_id: int,
    current_name: str,
    current_username: str | None,
) -> int:
    """Refresh all canonical/list rows when Telegram resolves a numeric ID."""
    if int(telegram_user_id) <= 0:
        return 0
    clean_name = (current_name or "").strip()
    clean_username = normalize_username(current_username)
    if not clean_name:
        return 0
    changed = 0
    for row in db.query(SAUser).filter(SAUser.telegram_user_id == int(telegram_user_id)).all():
        if row.name != clean_name or normalize_username(row.username) != clean_username:
            row.name, row.username, row.updated_at = clean_name, clean_username, local_now()
            changed += 1
    for row in db.query(SAList).filter(SAList.telegram_user_id == int(telegram_user_id)).all():
        if row.name != clean_name or normalize_username(row.username) != clean_username:
            row.name, row.username, row.updated_at = clean_name, clean_username, local_now()
            changed += 1
    if changed:
        db.commit()
    return changed


def bind_official_user_if_match(
    db: Session,
    telegram_user_id: int,
    name: str,
    username: str | None,
    group_id: int,
    group_name: str | None,
) -> SAUser | None:
    """Bind only an official Excel member; never create an admin/non-SA row."""
    if not is_group_chat_id(group_id):
        return None
    existing = (
        db.query(SAUser)
        .filter(
            SAUser.group_id == group_id,
            SAUser.telegram_user_id == telegram_user_id,
            SAUser.is_from_list == 1,
        )
        .first()
    )
    if existing is not None:
        return existing
    official_member = _official_member_for_user(db, group_name, telegram_user_id, name, username)
    if official_member is None:
        return None
    if _member_is_excluded(official_member, _exclusion_rows(db, group_id)):
        return None
    return register_or_update_user(
        db, telegram_user_id, name, username, group_id, group_name
    )


_GROUP_SEEN_CACHE: dict[int, tuple[str | None, float]] = {}


def _ensure_group_registered_fast(db: Session, group_id: int, group_name: str | None) -> None:
    if db.get_bind().dialect.name == "postgresql":
        cached = _GROUP_SEEN_CACHE.get(group_id)
        now_mono = _time.monotonic()
        if cached is not None and cached[0] == group_name and cached[1] > now_mono:
            return
        register_group(db, group_id, group_name, sync_members=False)
        if len(_GROUP_SEEN_CACHE) > 500:
            _GROUP_SEEN_CACHE.clear()
        _GROUP_SEEN_CACHE[group_id] = (group_name, now_mono + 300.0)
    else:
        register_group(db, group_id, group_name, sync_members=False)


def register_or_update_user(
    db: Session,
    telegram_user_id: int,
    name: str,
    username: str | None,
    group_id: int,
    group_name: str | None,
) -> SAUser:
    # Private chat should not create alert/group tracking rows.
    if not is_group_chat_id(group_id):
        cleanup_private_and_stale_rows(db)
        now = local_now()
        return SAUser(
            telegram_user_id=telegram_user_id,
            group_id=group_id,
            group_name=group_name,
            name=name,
            username=normalize_username(username),
            flow_key=flow_key_for_position(None, None),
            created_at=now,
            updated_at=now,
        )

    _ensure_group_registered_fast(db, group_id, group_name)
    item = _find_existing_user(db, telegram_user_id, username, group_id)
    username_norm = normalize_username(username)
    if (
        item is not None
        and item.is_from_list == 1
        and item.region
        and item.dealer
        and item.position
        and int(item.telegram_user_id or 0) == int(telegram_user_id)
        and item.group_name == group_name
        and item.name == name
        and item.username == username_norm
    ):
        return item

    now = local_now()
    official_member = _official_member_for_user(db, group_name, telegram_user_id, name, username)
    if official_member is not None and _member_is_excluded(official_member, _exclusion_rows(db, group_id)):
        official_member = None

    if item is None and official_member is not None:
        # Bind the official Excel row if it exists with a placeholder ID.
        official_username = normalize_username(official_member.get("username"))
        item = (
            db.query(SAUser)
            .filter(SAUser.group_id == group_id)
            .filter(
                (func.lower(func.replace(SAUser.username, "@", "")) == official_username)
                if official_username else (SAUser.name == official_member.get("name"))
            )
            .first()
        )

    if item is None:
        # Keep non-official users out of scheduled alerts by is_from_list=0.
        item = SAUser(
            telegram_user_id=telegram_user_id,
            group_id=group_id,
            group_name=group_name,
            name=name,
            username=username_norm,
            official_name=None,
            official_username=None,
            flow_key=flow_key_for_position(None, None),
            is_from_list=0,
            source_type="runtime",
            created_at=now,
            updated_at=now,
        )
        db.add(item)
    else:
        # If this row came from Excel with a temporary negative ID, bind it to the real Telegram ID.
        item.telegram_user_id = telegram_user_id
        item.group_name = group_name
        if not item.name or item.is_from_list == 0:
            item.name = name
        if username_norm:
            item.username = username_norm
        item.updated_at = now

    if official_member is not None:
        # Keep official roster identity separately, while the visible name and
        # username follow the user's current Telegram profile automatically.
        item.official_name = official_member.get("name") or item.official_name or item.name
        item.official_username = normalize_username(official_member.get("username"))
        item.name = name or item.name
        item.username = username_norm
        _apply_member_fields(item, official_member)
    elif not item.flow_key:
        item.flow_key = flow_key_for_position(item.position, item.working_time)

    db.commit()
    db.refresh(item)
    return item



def add_manual_user(
    db: Session,
    *,
    group_id: int,
    group_name: str | None,
    name: str,
    position: str,
    dealer: str,
    username: str | None,
    working_time: str,
    telegram_user_id: int | None = None,
) -> tuple[SAUser, bool]:
    """Add or update one controller-managed SA record.

    Manual records use ``is_from_list=1`` so they participate in scheduled
    alerts, and ``source_type='manual'`` so /sync_sa_list never deletes them.
    A replied Telegram user ID is preferred; otherwise a stable negative
    placeholder is used until that person sends /bind_sa in the group.
    """
    if not is_group_chat_id(group_id):
        raise ValueError("/add must be used inside a Telegram group")

    clean_name = (name or "").strip()
    clean_position = (position or "").strip()
    clean_dealer = (dealer or "").strip().upper()
    clean_username = normalize_username(username)
    clean_working_time = (working_time or "").strip()
    if not all((clean_name, clean_position, clean_dealer, clean_working_time)):
        raise ValueError("Name, position, dealer and working time are required")
    if clean_position not in APPROVED_POSITIONS:
        raise ValueError("Position must be SA, Commando, or SA Team Leader")

    register_group(db, group_id, group_name, sync_members=False)
    region_match = re.search(r"(?<![A-Z0-9])R([1-9])(?![0-9])", (group_name or "").upper())
    region = f"R{region_match.group(1)}" if region_match else None
    if not region:
        raise ValueError(f"Could not detect region from group name: {group_name}")

    exclusions = _exclusion_rows(db, group_id)
    manual_member = {"name": clean_name, "username": clean_username}
    if _member_is_excluded(manual_member, exclusions):
        raise ValueError("This SA is currently removed. Use /restore first, then /add again.")

    name_key = normalize_name(clean_name)
    candidates = db.query(SAUser).filter(SAUser.group_id == group_id).all()
    item = next(
        (
            row for row in candidates
            if (clean_username and normalize_username(row.username) == clean_username)
            or normalize_name(row.name) == name_key
            or (telegram_user_id and telegram_user_id > 0 and row.telegram_user_id == telegram_user_id)
        ),
        None,
    )
    created = item is None
    now = local_now()
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    final_user_id = (
        int(telegram_user_id)
        if telegram_user_id and int(telegram_user_id) > 0
        else _placeholder_user_id(group_name, clean_name, clean_username)
    )

    if item is None:
        item = SAUser(
            telegram_user_id=final_user_id,
            group_id=group_id,
            group_name=group_name,
            name=clean_name,
            username=clean_username,
            created_at=now,
            alert_active_from=next_day,
        )
        db.add(item)
    elif telegram_user_id and int(telegram_user_id) > 0:
        item.telegram_user_id = int(telegram_user_id)

    item.group_name = group_name
    item.name = clean_name
    item.username = clean_username
    item.region = region
    item.dealer = clean_dealer
    item.position = clean_position
    item.working_time = clean_working_time
    item.flow_key = flow_key_for_position(clean_position, clean_working_time)
    item.is_from_list = 1
    item.source_type = "manual"
    if created:
        item.alert_active_from = next_day
    item.updated_at = now

    list_row = (
        db.query(SAList)
        .filter(SAList.group_name == group_name, SAList.name == clean_name)
        .first()
    )
    if list_row is None:
        list_row = SAList(
            region=region,
            dealer=clean_dealer,
            name=clean_name,
            position=clean_position,
            working_time=clean_working_time,
            username=clean_username,
            telegram_user_id=final_user_id if final_user_id > 0 else None,
            group_name=group_name or str(group_id),
            controller=_controller_text(region),
            source_type="manual",
            is_active=1,
            created_at=now,
            updated_at=now,
        )
        db.add(list_row)
    else:
        list_row.region = region
        list_row.dealer = clean_dealer
        list_row.position = clean_position
        list_row.working_time = clean_working_time
        list_row.username = clean_username
        list_row.telegram_user_id = final_user_id if final_user_id > 0 else None
        list_row.source_type = "manual"
        list_row.is_active = 1
        list_row.updated_at = now

    db.commit()
    db.refresh(item)
    return item, created


def change_user_working_time(
    db: Session,
    *,
    group_id: int,
    name: str,
    username: str | None,
    new_working_time: str,
    telegram_user_id: int | None = None,
) -> tuple[SAUser, str, str]:
    """Persistently change one user's working time and alert flow.

    The override is stored separately so a later /sync_sa_list does not restore
    the old workbook time. Matching prefers username, then exact normalized
    live/official name inside the current Telegram group.
    """
    clean_name = (name or "").strip()
    clean_username = normalize_username(username)
    clean_time = (new_working_time or "").strip()
    if not clean_time or (not clean_name and not telegram_user_id):
        raise ValueError("Telegram ID and new working time are required")

    compact = (
        clean_time.lower()
        .replace(" ", "")
        .replace(".", ":")
        .replace("–", "-")
        .replace("—", "-")
    )
    canonical_by_compact = {
        "6:30am-4:30pm": "6:30am-4:30pm",
        "7:00am-5:00pm": "7:00am-5:00pm",
        "7:30am-5:00pm": "7:30am-5:00pm",
        "9:00am-8:00pm": "9:00am-8:00pm",
    }
    canonical = canonical_by_compact.get(compact)
    if canonical is None:
        raise ValueError(
            "Unsupported working time. Use 6:30am-4:30pm, 7:00am-5:00pm, "
            "7:30am-5:00pm, or 9:00am-8:00pm."
        )

    rows = db.query(SAUser).filter(SAUser.group_id == group_id).all()
    if telegram_user_id:
        candidates = [row for row in rows if row.telegram_user_id == int(telegram_user_id)]
    else:
        candidates = []
    name_key = normalize_name(clean_name)

    username_matches = [
        row for row in rows
        if clean_username
        and clean_username in {
            normalize_username(row.username),
            normalize_username(row.official_username),
        }
    ]
    name_matches = [
        row for row in rows
        if name_key
        and name_key in {
            normalize_name(row.name),
            normalize_name(row.official_name),
        }
    ]

    if candidates:
        pass
    elif username_matches:
        candidates = username_matches
        if name_matches:
            intersection = [row for row in username_matches if row in name_matches]
            if intersection:
                candidates = intersection
    else:
        candidates = name_matches

    if not candidates:
        raise ValueError(
            "SA not found in this group. Check the exact name/username or run /sync_sa_list."
        )
    if len(candidates) > 1:
        raise ValueError(
            "More than one SA matched. Use the exact @username to identify one account."
        )

    item = candidates[0]
    old_time = item.working_time or "Not set"
    item.working_time_override = canonical
    item.working_time = canonical
    item.flow_key = flow_key_for_position(item.position, canonical)
    item.updated_at = local_now()
    list_rows = db.query(SAList).filter(SAList.group_name == item.group_name).all()
    for row in list_rows:
        if normalize_name(row.name) == normalize_name(item.official_name or item.name):
            row.working_time = canonical
            row.updated_at = local_now()
    db.commit()
    db.refresh(item)
    return item, old_time, canonical


def change_user_position(
    db: Session,
    *,
    group_id: int,
    name: str,
    username: str | None,
    new_position: str,
    telegram_user_id: int | None = None,
) -> tuple[SAUser, str]:
    """Persist a position change in both SAUser and the editable SA_List."""
    if new_position not in APPROVED_POSITIONS:
        raise ValueError("Position must be SA, Commando, or SA Team Leader")
    clean_username = normalize_username(username)
    name_key = normalize_name(name)
    rows = db.query(SAUser).filter(SAUser.group_id == group_id).all()
    matches = [
        row for row in rows
        if (telegram_user_id and row.telegram_user_id == int(telegram_user_id))
        or (clean_username and clean_username in {
            normalize_username(row.username), normalize_username(row.official_username)
        })
        or (name_key and name_key in {
            normalize_name(row.name), normalize_name(row.official_name)
        })
    ]
    unique = {row.id: row for row in matches}
    if not unique:
        raise ValueError("SA not found in this group. Check the exact name/username.")
    if len(unique) > 1:
        raise ValueError("More than one SA matched. Use the exact @username.")
    item = next(iter(unique.values()))
    old_position = item.position or "Not set"
    item.position = new_position
    item.flow_key = flow_key_for_position(new_position, item.working_time)
    item.updated_at = local_now()
    for row in db.query(SAList).filter(SAList.group_name == item.group_name).all():
        if normalize_name(row.name) == normalize_name(item.official_name or item.name):
            row.position = new_position
            row.updated_at = local_now()
    db.commit()
    db.refresh(item)
    return item, old_position

def pause_alerts_for_period(
    db: Session,
    user: SAUser,
    period: str = "day",
    reason: str = "AL",
) -> tuple[datetime, datetime]:
    """Pause alerts for one local-calendar period only.

    ``morning`` covers 00:00:00-12:59:59 and ``afternoon`` covers
    13:00:00-23:59:59. ``day`` covers the full calendar day.
    The command time never carries into tomorrow.
    """
    now = local_now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    normalized = (period or "day").strip().lower()

    if normalized == "morning":
        pause_start = day_start
        pause_end = now.replace(hour=12, minute=59, second=59, microsecond=999999)
    elif normalized == "afternoon":
        pause_start = now.replace(hour=13, minute=0, second=0, microsecond=0)
        pause_end = day_end
    else:
        pause_start = day_start
        pause_end = day_end
        normalized = "day"

    user.alert_pause_start = pause_start
    user.alert_paused_until = pause_end
    user.alert_pause_reason = f"{reason}_{normalized.upper()}"
    user.updated_at = now
    db.commit()
    db.refresh(user)
    return pause_start, pause_end


def pause_alerts_for_dates(
    db: Session,
    user: SAUser,
    period: str,
    start_date: date,
    end_date: date | None = None,
    reason: str = "AL",
) -> tuple[datetime, datetime]:
    """Pause reporting windows for a chosen local date or inclusive date range."""
    if period not in {"day", "morning", "afternoon", "range"}:
        raise ValueError("Choose full day, morning, afternoon, or a date range.")
    last_day = end_date if period == "range" else start_date
    if last_day is None or last_day < start_date:
        raise ValueError("The end date must be on or after the start date.")
    if (last_day - start_date).days > 365:
        raise ValueError("Choose a leave range of 366 days or less.")
    if last_day < local_now().date():
        raise ValueError("Annual leave must include today or a future date.")
    start_hour = 13 if period == "afternoon" else 0
    end_hour = 12 if period == "morning" else 23
    pause_start = datetime.combine(start_date, time(start_hour, 0))
    pause_end = datetime.combine(last_day, time(end_hour, 59, 59, 999999))
    user.alert_pause_start = pause_start
    user.alert_paused_until = pause_end
    user.alert_pause_reason = f"{reason}_{period.upper()}"
    user.updated_at = local_now()
    db.commit()
    db.refresh(user)
    return pause_start, pause_end


def pause_alerts_for_today(db: Session, user: SAUser, reason: str = "AL") -> datetime:
    """Backward-compatible full-day AL helper."""
    _, paused_until = pause_alerts_for_period(db, user, period="day", reason=reason)
    return paused_until


def resume_alerts(db: Session, user: SAUser) -> None:
    user.alert_pause_start = None
    user.alert_paused_until = None
    user.alert_pause_reason = None
    user.updated_at = local_now()
    db.commit()


def _match_user_query(users: list[SAUser], query: str) -> list[SAUser]:
    raw = (query or "").strip()
    if raw.isdigit():
        target_id = int(raw)
        return [user for user in users if user.telegram_user_id == target_id]
    query_username = normalize_username(query) or ""
    query_name = normalize_name(query)

    exact = [
        user for user in users
        if (query_username and normalize_username(user.username) == query_username)
        or (query_name and normalize_name(user.name) == query_name)
    ]
    if exact:
        return exact

    return [
        user for user in users
        if (query_username and query_username in (normalize_username(user.username) or ""))
        or (query_name and query_name in normalize_name(user.name))
    ]


def find_official_users_by_query(db: Session, group_id: int, query: str) -> list[SAUser]:
    users = get_all_users_by_group(db, group_id)
    return _match_user_query(users, query)


def remove_user_from_alerts(
    db: Session,
    user: SAUser,
    removed_by_user_id: int | None,
    removed_by_name: str | None,
) -> SAAlertExclusion:
    normalized_name = normalize_name(user.name)
    normalized_username = normalize_username(user.username) or ""
    exclusion = (
        db.query(SAAlertExclusion)
        .filter(
            SAAlertExclusion.group_id == user.group_id,
            SAAlertExclusion.normalized_name == normalized_name,
            SAAlertExclusion.normalized_username == normalized_username,
        )
        .first()
    )
    if exclusion is None:
        exclusion = SAAlertExclusion(
            group_id=user.group_id,
            group_name=user.group_name,
            name=user.name,
            username=user.username,
            telegram_user_id=user.telegram_user_id if user.telegram_user_id > 0 else None,
            normalized_name=normalized_name,
            normalized_username=normalized_username,
            removed_by_user_id=removed_by_user_id,
            removed_by_name=removed_by_name,
            removed_at=local_now(),
        )
        db.add(exclusion)
    else:
        exclusion.name = user.name
        exclusion.username = user.username
        exclusion.telegram_user_id = user.telegram_user_id if user.telegram_user_id > 0 else None
        exclusion.removed_by_user_id = removed_by_user_id
        exclusion.removed_by_name = removed_by_name
        exclusion.removed_at = local_now()

    for row in db.query(SAList).filter(SAList.group_name == user.group_name).all():
        if normalize_name(row.name) == normalize_name(user.official_name or user.name):
            row.is_active = 0
            row.updated_at = local_now()
    db.delete(user)
    db.commit()
    db.refresh(exclusion)
    return exclusion


def restore_user_to_alerts(
    db: Session,
    group_id: int,
    group_name: str | None,
    query: str,
) -> tuple[int, int]:
    exclusions = db.query(SAAlertExclusion).filter(SAAlertExclusion.group_id == group_id).all()
    query_username = normalize_username(query) or ""
    query_name = normalize_name(query)
    query_id = int(query) if (query or "").strip().isdigit() else None
    matches = [
        exclusion for exclusion in exclusions
        if (query_id and exclusion.telegram_user_id == query_id)
        or (query_username and query_username in exclusion.normalized_username)
        or (query_name and query_name in exclusion.normalized_name)
    ]
    for exclusion in matches:
        for row in db.query(SAList).filter(SAList.group_name == group_name).all():
            if normalize_name(row.name) == exclusion.normalized_name or (
                exclusion.normalized_username
                and normalize_username(row.username) == exclusion.normalized_username
            ):
                row.is_active = 1
                row.updated_at = local_now()
        db.delete(exclusion)
    if matches:
        db.commit()
        sync_group_members_from_list(db, group_id, group_name)
    return len(matches), len(get_all_users_by_group(db, group_id))


def _to_local_naive(dt: datetime | None) -> datetime | None:
    """Convert an aware Telegram timestamp to a naive local datetime matching DB columns."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo(settings.APP_TIMEZONE)).replace(tzinfo=None)
    return dt


def save_location(
    db: Session,
    telegram_user_id: int,
    group_id: int,
    group_name: str | None,
    name: str,
    username: str | None,
    lat: float,
    lon: float,
    *,
    event_at: datetime | None = None,
    report_caption: str | None = None,
    telegram_update_id: int | None = None,
) -> SAUser:
    item = register_or_update_user(db, telegram_user_id, name, username, group_id, group_name)

    now = local_now()
    if telegram_update_id is not None and db.query(PinHistory.id).filter_by(telegram_update_id=telegram_update_id).first():
        return item
    effective_time = _to_local_naive(event_at) or now
    map_link = create_maps_link(lat, lon)
    window_start = get_current_interval_start(effective_time, settings.ALERT_INTERVAL_MINUTES)
    window_end = get_current_interval_end(effective_time, settings.ALERT_INTERVAL_MINUTES)

    item.lat = lat
    item.lon = lon
    item.map_link = map_link
    item.last_pin_at = now
    item.updated_at = now

    from app.services.outlet_form import report_details
    fields = report_details(report_caption)
    pin = PinHistory(
        outlet_name=fields.get("outlet name"),
        reported_employee_id=fields.get("id"),
        sa_user_id=item.id,
        telegram_user_id=telegram_user_id,
        group_id=group_id,
        group_name=group_name,
        name=item.name or name,
        username=item.username or normalize_username(username),
        lat=lat,
        lon=lon,
        map_link=map_link,
        pinned_at=now,
        window_start=window_start,
        window_end=window_end,
        event_at=effective_time,
        telegram_update_id=telegram_update_id,
    )
    db.add(pin)
    update_pin_route(db, pin, item)
    db.commit()
    db.refresh(item)
    return item


def save_sale_photo_report(
    db: Session,
    telegram_user_id: int,
    group_id: int,
    group_name: str | None,
    name: str,
    username: str | None,
    caption: str | None,
    reply_lat: float | None,
    reply_lon: float | None,
    telegram_message_id: int | None,
    replied_message_id: int | None,
    media_group_id: str | None = None,
    *,
    event_at: datetime | None = None,
) -> SalePhotoReport:
    item = register_or_update_user(db, telegram_user_id, name, username, group_id, group_name)

    now = local_now()
    submit_time = _to_local_naive(event_at) or now
    window_start = get_current_interval_start(submit_time, settings.ALERT_INTERVAL_MINUTES)
    window_end = get_current_interval_end(submit_time, settings.ALERT_INTERVAL_MINUTES)

    # A GPS reply supplies the coordinates for the original outlet message.
    # Keep its original submission time when enriching it or receiving an edit.
    if telegram_message_id is not None:
        identity = SalePhotoReport.telegram_message_id == telegram_message_id
        if media_group_id:
            identity = or_(identity, SalePhotoReport.media_group_id == media_group_id)
        existing = db.query(SalePhotoReport).filter_by(group_id=group_id,
            telegram_user_id=telegram_user_id).filter(identity).order_by(SalePhotoReport.id).first()
        if existing is not None:
            if media_group_id:
                existing.media_group_id = media_group_id
            if caption and (not existing.caption or looks_like_sale_form(caption) or not media_group_id):
                existing.caption = caption
                existing.telegram_message_id = telegram_message_id
            if reply_lat is not None and reply_lon is not None:
                existing.reply_lat, existing.reply_lon = reply_lat, reply_lon
                existing.reply_map_link = create_maps_link(reply_lat, reply_lon)
                existing.replied_message_id = replied_message_id
            db.commit()
            return existing
    report = SalePhotoReport(
        sa_user_id=item.id,
        telegram_user_id=telegram_user_id,
        group_id=group_id,
        group_name=group_name,
        name=item.name or name,
        username=item.username or normalize_username(username),
        caption=caption,
        media_group_id=media_group_id,
        reply_lat=reply_lat,
        reply_lon=reply_lon,
        reply_map_link=create_maps_link(reply_lat, reply_lon),
        telegram_message_id=telegram_message_id,
        replied_message_id=replied_message_id,
        submitted_at=submit_time,
        window_start=window_start,
        window_end=window_end,
    )

    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def save_text_form_report(
    db: Session,
    telegram_user_id: int,
    group_id: int,
    group_name: str | None,
    name: str,
    username: str | None,
    text: str,
    telegram_message_id: int | None,
    *,
    event_at: datetime | None = None,
) -> SalePhotoReport | None:
    """Save text outlet/order form.

    A valid business submission is text form + pin map. We store the text form
    here, then the interval checker confirms a PinHistory row also exists for
    the same user/window.
    """
    if not looks_like_sale_form(text):
        return None
    return save_sale_photo_report(
        db=db,
        telegram_user_id=telegram_user_id,
        group_id=group_id,
        group_name=group_name,
        name=name,
        username=username,
        caption=text,
        reply_lat=None,
        reply_lon=None,
        telegram_message_id=telegram_message_id,
        replied_message_id=None,
        event_at=event_at,
    )


def delete_my_location(db: Session, telegram_user_id: int, group_id: int) -> bool:
    item = db.query(SAUser).filter(SAUser.telegram_user_id == telegram_user_id, SAUser.group_id == group_id).first()
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


def get_all_users_by_group(db: Session, group_id: int, flow_key: str | None = None) -> list[SAUser]:
    # Display/alert only official Excel-list users.
    query = db.query(SAUser).filter(
        SAUser.group_id == group_id,
        SAUser.is_from_list == 1,
        SAUser.region.isnot(None),
        SAUser.dealer.isnot(None),
        SAUser.position.isnot(None),
    )
    if flow_key:
        query = query.filter(SAUser.flow_key == flow_key)
    return query.order_by(SAUser.position.asc().nulls_last(), SAUser.name.asc()).all()


def get_active_groups(db: Session) -> list[TelegramGroup]:
    # Only real Telegram groups/supergroups have negative chat IDs.
    # This prevents private /start rows from receiving scheduled alerts.
    return (
        db.query(TelegramGroup)
        .filter(TelegramGroup.is_active == 1, TelegramGroup.group_id < 0)
        .order_by(TelegramGroup.group_name.asc())
        .all()
    )


def _identity_username(value: str | None) -> str:
    return normalize_username(value) or ""


def _identity_name(value: str | None) -> str:
    return normalize_name(value)


def _eligible_users_query(
    db: Session,
    group_id: int,
    interval_start: datetime,
    interval_end: datetime,
    flow_key: str | None = None,
):
    """Return official users eligible for one alert window."""
    query = db.query(SAUser).filter(
        SAUser.group_id == group_id,
        SAUser.is_from_list == 1,
        SAUser.region.isnot(None),
        SAUser.dealer.isnot(None),
        SAUser.position.isnot(None),
        or_(SAUser.alert_active_from.is_(None), SAUser.alert_active_from <= interval_start),
        or_(
            SAUser.alert_paused_until.is_(None),
            SAUser.alert_paused_until < interval_start,
            SAUser.alert_pause_start > interval_end,
        ),
    )
    if flow_key:
        query = query.filter(SAUser.flow_key == flow_key)
    return query


def _resolve_submitted_user_ids(
    users: list[SAUser],
    reports: list[SalePhotoReport],
    pins: list[PinHistory],
) -> set[int]:
    if not users:
        return set()

    by_telegram_id: dict[int, SAUser] = {}
    username_owners: dict[str, list[SAUser]] = {}
    name_owners: dict[str, list[SAUser]] = {}

    for user in users:
        if (user.telegram_user_id or 0) > 0:
            by_telegram_id[int(user.telegram_user_id)] = user

        for raw_username in (user.username, user.official_username):
            key = _identity_username(raw_username)
            if key:
                username_owners.setdefault(key, []).append(user)

        for raw_name in (user.name, user.official_name):
            key = _identity_name(raw_name)
            if key:
                name_owners.setdefault(key, []).append(user)

    unique_usernames = {
        key: owners[0]
        for key, owners in username_owners.items()
        if len({owner.id for owner in owners}) == 1
    }
    unique_names = {
        key: owners[0]
        for key, owners in name_owners.items()
        if len({owner.id for owner in owners}) == 1
    }

    core_name_owners: dict[str, list[SAUser]] = {}
    for user in users:
        for raw_name in (user.name, user.official_name):
            key = identity_name_core(raw_name, region=user.region, dealer=user.dealer)
            if key:
                core_name_owners.setdefault(key, []).append(user)
    unique_core_names = {
        key: owners[0]
        for key, owners in core_name_owners.items()
        if len({owner.id for owner in owners}) == 1
    }

    eligible_by_db_id = {int(user.id): user for user in users if user.id is not None}

    def resolve(activity) -> SAUser | None:
        sa_user_id = int(getattr(activity, "sa_user_id", 0) or 0)
        if sa_user_id > 0 and sa_user_id in eligible_by_db_id:
            return eligible_by_db_id[sa_user_id]

        telegram_user_id = int(getattr(activity, "telegram_user_id", 0) or 0)
        if telegram_user_id > 0 and telegram_user_id in by_telegram_id:
            return by_telegram_id[telegram_user_id]

        username_key = _identity_username(getattr(activity, "username", None))
        if username_key and username_key in unique_usernames:
            return unique_usernames[username_key]

        name_key = _identity_name(getattr(activity, "name", None))
        if name_key and name_key in unique_names:
            return unique_names[name_key]

        for user in users:
            core_key = identity_name_core(
                getattr(activity, "name", None),
                region=user.region,
                dealer=user.dealer,
            )
            if core_key and unique_core_names.get(core_key) is user:
                return user
        return None

    form_user_ids = {
        user.id
        for activity in reports
        if (user := resolve(activity)) is not None
    }
    pin_user_ids = {
        user.id
        for activity in pins
        if (user := resolve(activity)) is not None
    }
    submitted = form_user_ids & pin_user_ids

    linked_form_and_gps_user_ids = {
        user.id
        for activity in reports
        if getattr(activity, "reply_lat", None) is not None
        and getattr(activity, "reply_lon", None) is not None
        and (user := resolve(activity)) is not None
    }
    submitted.update(linked_form_and_gps_user_ids)

    reports_by_tid: dict[int, list[SalePhotoReport]] = {}
    pins_by_tid: dict[int, list[PinHistory]] = {}
    for activity in reports:
        tid = int(getattr(activity, "telegram_user_id", 0) or 0)
        if tid > 0:
            reports_by_tid.setdefault(tid, []).append(activity)
    for activity in pins:
        tid = int(getattr(activity, "telegram_user_id", 0) or 0)
        if tid > 0:
            pins_by_tid.setdefault(tid, []).append(activity)

    for tid in set(reports_by_tid) & set(pins_by_tid):
        user = by_telegram_id.get(tid)
        if user is not None:
            submitted.add(user.id)
            continue
        pair = reports_by_tid[tid] + pins_by_tid[tid]
        canonical_users = {
            eligible_by_db_id[int(getattr(activity, "sa_user_id", 0) or 0)]
            for activity in pair
            if int(getattr(activity, "sa_user_id", 0) or 0) in eligible_by_db_id
        }
        if len({u.id for u in canonical_users}) == 1:
            submitted.add(next(iter(canonical_users)).id)
            continue
        pair_users: dict[int, SAUser] = {}
        for activity in pair:
            username_key = _identity_username(getattr(activity, "username", None))
            if username_key and username_key in unique_usernames:
                u = unique_usernames[username_key]
                pair_users[u.id] = u
            name_key = _identity_name(getattr(activity, "name", None))
            if name_key and name_key in unique_names:
                u = unique_names[name_key]
                pair_users[u.id] = u
        if len(pair_users) == 1:
            submitted.add(next(iter(pair_users.values())).id)

    return submitted


def _submitted_user_ids_for_interval(
    db: Session,
    users: list[SAUser],
    group_id: int,
    interval_start: datetime,
    interval_end: datetime,
) -> set[int]:
    """Resolve valid submissions without fragile correlated SQL.

    A valid submission requires both a sale form/photo and a pin in the same
    Telegram group and alert window. Identity matching rules are deliberately
    strict:

    1. Stable Telegram user ID.
    2. A username only when that username belongs to exactly one official user.
    3. A normalized name only when that name belongs to exactly one official user.

    This prevents false missing alerts after a Telegram profile change while
    also preventing one person's submission from being credited to another user
    with a duplicate or similar name.
    """
    if not users:
        return set()

    grace_start = interval_start_with_grace(interval_start)
    grace_end = interval_end_with_grace(interval_end)

    reports = (
        db.query(SalePhotoReport)
        .filter(
            SalePhotoReport.group_id == group_id,
            SalePhotoReport.submitted_at >= grace_start,
            SalePhotoReport.submitted_at <= grace_end,
        )
        .all()
    )
    pin_stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    pins = (
        db.query(PinHistory)
        .filter(
            PinHistory.group_id == group_id,
            pin_stamp >= grace_start,
            pin_stamp <= grace_end,
        )
        .all()
    )
    return _resolve_submitted_user_ids(users, reports, pins)


def get_missing_sale_photo_users_for_interval(
    db: Session,
    group_id: int,
    interval_start: datetime,
    interval_end: datetime,
    flow_key: str | None = None,
) -> list[SAUser]:
    users = (
        _eligible_users_query(db, group_id, interval_start, interval_end, flow_key)
        .order_by(SAUser.position.asc().nulls_last(), SAUser.name.asc())
        .all()
    )
    submitted_ids = _submitted_user_ids_for_interval(
        db, users, group_id, interval_start, interval_end
    )
    return [user for user in users if user.id not in submitted_ids]


def count_submitted_users_for_interval(
    db: Session,
    group_id: int,
    interval_start: datetime,
    interval_end: datetime,
    flow_key: str | None = None,
) -> int:
    users = _eligible_users_query(
        db, group_id, interval_start, interval_end, flow_key
    ).all()
    return len(
        _submitted_user_ids_for_interval(
            db, users, group_id, interval_start, interval_end
        )
    )

def record_missing_users_and_attach_today_count(
    db: Session,
    users: list[SAUser],
    group_id: int,
    interval_start: datetime,
    interval_end: datetime,
) -> list[SAUser]:
    if not users:
        return users

    today_start = interval_end.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    for user in users:
        stmt = (
            insert(MissingSalePhotoAlert)
            .values(
                telegram_user_id=user.telegram_user_id,
                group_id=group_id,
                group_name=user.group_name,
                name=user.name,
                username=user.username,
                region=user.region,
                dealer=user.dealer,
                position=user.position,
                missed_at=local_now(),
                window_start=interval_start,
                window_end=interval_end,
            )
            .on_conflict_do_nothing(constraint="uq_missing_sale_photo_user_window")
        )
        db.execute(stmt)

    db.commit()

    for user in users:
        count = (
            db.query(func.count(MissingSalePhotoAlert.id))
            .filter(
                MissingSalePhotoAlert.telegram_user_id == user.telegram_user_id,
                MissingSalePhotoAlert.group_id == group_id,
                MissingSalePhotoAlert.missed_at >= today_start,
                MissingSalePhotoAlert.missed_at < tomorrow_start,
            )
            .scalar()
        )
        user.miss_count_today = count or 0

    return users


def reset_locations(db: Session, group_id: int | None = None) -> int:
    query = db.query(SAUser)
    if group_id is not None:
        query = query.filter(SAUser.group_id == group_id)
    count = query.count()
    query.update({SAUser.lat: None, SAUser.lon: None, SAUser.map_link: None, SAUser.last_pin_at: None})
    db.query(SAUser).filter(
        SAUser.alert_paused_until.isnot(None),
        SAUser.alert_paused_until < local_now(),
    ).update({
        SAUser.alert_pause_start: None,
        SAUser.alert_paused_until: None,
        SAUser.alert_pause_reason: None,
    }, synchronize_session=False)
    db.commit()
    return count


def attach_alert_message_id(
    db: Session,
    group_id: int,
    interval_start: datetime,
    interval_end: datetime,
    telegram_user_ids: list[int],
    alert_message_id: int,
) -> int:
    """Attach the bot alert message ID to each missing-user record in that alert."""
    if not telegram_user_ids:
        return 0
    result = db.execute(
        update(MissingSalePhotoAlert)
        .where(
            MissingSalePhotoAlert.group_id == group_id,
            MissingSalePhotoAlert.window_start == interval_start,
            MissingSalePhotoAlert.window_end == interval_end,
            MissingSalePhotoAlert.telegram_user_id.in_(telegram_user_ids),
        )
        .values(alert_message_id=alert_message_id)
    )
    db.commit()
    return int(result.rowcount or 0)


def save_missing_reason_reply(
    db: Session,
    *,
    group_id: int,
    alert_message_id: int,
    sender_user_id: int,
    sender_username: str | None,
    sender_name: str,
    reason_text: str | None,
    voice_to_text: str | None,
    reason_message_id: int,
    submitted_at: datetime,
) -> int:
    """Save a text/voice reason only for the official SA replying to their alert."""
    # Reasons are person-directed writes, so authorize them by the immutable
    # Telegram account ID only. A recycled username or similar display name
    # must never be able to update another user's alert record.
    user = (
        db.query(SAUser)
        .filter(
            SAUser.group_id == group_id,
            SAUser.telegram_user_id == sender_user_id,
            SAUser.is_from_list == 1,
        )
        .first()
    )
    if user is None:
        return 0

    values: dict = {
        "reason_submitted_at": submitted_at,
        "reason_message_id": reason_message_id,
    }
    if reason_text:
        values["reason_text"] = reason_text.strip()
    if voice_to_text:
        values["voice_to_text"] = voice_to_text.strip()

    result = db.execute(
        update(MissingSalePhotoAlert)
        .where(
            MissingSalePhotoAlert.group_id == group_id,
            MissingSalePhotoAlert.alert_message_id == alert_message_id,
            MissingSalePhotoAlert.telegram_user_id == user.telegram_user_id,
        )
        .values(**values)
    )
    db.commit()
    return int(result.rowcount or 0)


def save_latest_missing_reason(
    db: Session,
    *,
    group_id: int,
    sender_user_id: int,
    reason_text: str | None,
    voice_to_text: str | None,
    reason_message_id: int,
    submitted_at: datetime,
) -> int:
    """Attach a non-reply reason to the sender's latest missing alert today.

    Numeric Telegram ID and group are mandatory, so a controller mention or a
    similar display name can never write a reason for another person.
    """
    user = db.query(SAUser).filter(
        SAUser.group_id == group_id,
        SAUser.telegram_user_id == sender_user_id,
        SAUser.is_from_list == 1,
    ).first()
    if user is None:
        return 0

    day_start = submitted_at.replace(hour=0, minute=0, second=0, microsecond=0)
    query = db.query(MissingSalePhotoAlert).filter(
        MissingSalePhotoAlert.group_id == group_id,
        MissingSalePhotoAlert.telegram_user_id == sender_user_id,
        MissingSalePhotoAlert.window_end >= day_start,
        MissingSalePhotoAlert.window_end <= submitted_at,
    )
    # Text and voice are independent audit fields. A voice message must still
    # be able to fill Voice To Text when the same row already has a typed
    # reason, and vice versa.
    if voice_to_text:
        query = query.filter(MissingSalePhotoAlert.voice_to_text.is_(None))
    elif reason_text:
        query = query.filter(MissingSalePhotoAlert.reason_text.is_(None))
    else:
        return 0
    alert = query.order_by(MissingSalePhotoAlert.window_end.desc()).first()
    if alert is None:
        return 0

    alert.reason_submitted_at = submitted_at
    alert.reason_message_id = reason_message_id
    if reason_text:
        alert.reason_text = reason_text.strip()
    if voice_to_text:
        alert.voice_to_text = voice_to_text.strip()
    db.commit()
    return 1


def start_voice_reason_history(
    db: Session,
    *,
    group_id: int,
    group_name: str | None,
    sender_user_id: int,
    sender_name: str,
    sender_username: str | None,
    alert_message_id: int | None,
    voice_message_id: int,
    voice_file_id: str,
    voice_file_unique_id: str | None,
    duration_seconds: int | None,
    file_size_bytes: int | None,
    received_at: datetime,
) -> tuple[VoiceReasonHistory | None, bool]:
    """Persist voice metadata before transcription and resolve its alert safely."""
    user = db.query(SAUser).filter(
        SAUser.group_id == group_id,
        SAUser.telegram_user_id == sender_user_id,
        SAUser.is_from_list == 1,
    ).first()
    if user is None:
        return None, False

    existing = db.query(VoiceReasonHistory).filter(
        VoiceReasonHistory.group_id == group_id,
        VoiceReasonHistory.voice_message_id == voice_message_id,
    ).first()
    if existing is not None:
        return existing, existing.missing_alert_id is not None

    target = None
    if alert_message_id is not None:
        candidates = db.query(MissingSalePhotoAlert).filter(
            MissingSalePhotoAlert.group_id == group_id,
            MissingSalePhotoAlert.alert_message_id == alert_message_id,
        ).all()
    else:
        day_start = received_at.replace(hour=0, minute=0, second=0, microsecond=0)
        candidates = db.query(MissingSalePhotoAlert).filter(
            MissingSalePhotoAlert.group_id == group_id,
            MissingSalePhotoAlert.window_end >= day_start,
            MissingSalePhotoAlert.window_end <= received_at,
            MissingSalePhotoAlert.voice_to_text.is_(None),
        ).order_by(MissingSalePhotoAlert.window_end.desc()).all()

    exact = [row for row in candidates if row.telegram_user_id == sender_user_id]
    if exact:
        target = exact[0]
    else:
        live_keys = {
            normalize_name(user.name),
            normalize_name(user.official_name),
        } - {""}
        compatible = [row for row in candidates if normalize_name(row.name) in live_keys]
        if len(compatible) == 1:
            target = compatible[0]

    history = VoiceReasonHistory(
        missing_alert_id=target.id if target else None,
        telegram_user_id=sender_user_id,
        group_id=group_id,
        group_name=group_name,
        sender_name=sender_name,
        sender_username=normalize_username(sender_username),
        alert_message_id=alert_message_id,
        voice_message_id=voice_message_id,
        voice_file_id=voice_file_id,
        voice_file_unique_id=voice_file_unique_id,
        duration_seconds=duration_seconds,
        file_size_bytes=file_size_bytes,
        status="pending" if target else "unlinked",
        received_at=received_at,
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    return history, target is not None


def finish_voice_reason_history(
    db: Session,
    *,
    history_id: int,
    transcript: str | None,
    error_text: str | None = None,
) -> bool:
    """Finish one voice attempt and write valid text to its exact alert row."""
    history = db.query(VoiceReasonHistory).filter(VoiceReasonHistory.id == history_id).first()
    if history is None:
        return False

    clean_transcript = (transcript or "").strip()
    if not clean_transcript:
        history.status = "failed"
        history.error_text = (error_text or "No speech text was produced")[:4000]
        history.transcribed_at = local_now()
        db.commit()
        return False

    alert = None
    if history.missing_alert_id is not None:
        alert = db.query(MissingSalePhotoAlert).filter(
            MissingSalePhotoAlert.id == history.missing_alert_id,
        ).first()
    if alert is None:
        history.status = "transcribed_unlinked"
        history.transcript = clean_transcript
        history.error_text = "Transcript saved in history; no missing alert row was linked"
        history.transcribed_at = local_now()
        db.commit()
        return True

    alert.voice_to_text = clean_transcript
    alert.reason_submitted_at = history.received_at
    alert.reason_message_id = history.voice_message_id
    history.status = "transcribed"
    history.transcript = clean_transcript
    history.error_text = None
    history.transcribed_at = local_now()
    db.commit()
    return True
