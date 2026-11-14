from __future__ import annotations

from sqlalchemy.orm import Session

from app.data.sa_members import SA_MEMBERS, normalize_group, normalize_username
from app.database.models import SAList, SAUser
from app.services.shift_config import flow_key_for_position
from app.services.time_service import local_now


def _normalize_name(value: str | None) -> str:
    return "".join(ch for ch in (value or "").casefold() if ch.isalnum())


def canonical_position(value: str | None) -> str:
    key = (value or "SA").strip().casefold().replace("_", " ").replace("-", " ")
    if "team leader" in key or key.endswith(" leader"):
        return "SA Team Leader"
    if "commando" in key or "cammondo" in key:
        return "Commando"
    return "SA"


def migrate_legacy_roster_positions(db: Session) -> int:
    """Enforce the official 3 positions and canonical schedule-flow keys."""
    by_key = {
        (normalize_group(row.get("group_name")), _normalize_name(row.get("name"))): row
        for row in SA_MEMBERS
    }
    by_username = {
        normalize_username(row.get("username")): row
        for row in SA_MEMBERS
        if normalize_username(row.get("username"))
    }
    changed = 0

    for row in db.query(SAList).all():
        position = canonical_position(row.position)
        official = by_key.get(
            (normalize_group(row.group_name), _normalize_name(row.name))
        ) or by_username.get(normalize_username(row.username))
        if position != row.position:
            row.position = position
            if official is not None and row.source_type == "excel":
                row.working_time = official.get("working_time") or row.working_time
            row.updated_at = local_now()
            changed += 1

    for user in db.query(SAUser).all():
        position = canonical_position(user.position)
        official = by_key.get(
            (normalize_group(user.group_name), _normalize_name(user.official_name or user.name))
        ) or by_username.get(
            normalize_username(user.official_username or user.username)
        )
        expected_working_time = user.working_time
        if official is not None and not user.working_time_override:
            expected_working_time = official.get("working_time") or user.working_time
        expected_flow = flow_key_for_position(position, expected_working_time)
        if (
            position != user.position
            or expected_working_time != user.working_time
            or expected_flow != user.flow_key
        ):
            user.position = position
            user.working_time = expected_working_time
            user.flow_key = expected_flow
            user.updated_at = local_now()
            changed += 1

    if changed:
        db.commit()
    return changed
