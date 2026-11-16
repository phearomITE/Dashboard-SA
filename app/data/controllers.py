# app/data/controllers.py
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True, slots=True)
class Controller:
    """Telegram controller assigned to one or more sales regions."""

    name: str
    username: str
    user_id: int

    @property
    def normalized_username(self) -> str:
        """Return username without a leading @."""
        return self.username.lstrip("@").strip()


# Staff assignments and administrator IDs are private server configuration.
# The file is mounted at runtime and deliberately excluded from Git and images.
_CONFIG_PATH = Path(os.environ.get("CONTROLLER_CONFIG_PATH", Path(__file__).with_name("controller_assignments.json")))
_CONFIG = json.loads(_CONFIG_PATH.read_text(encoding="utf-8")) if _CONFIG_PATH.is_file() else {}


def _controller(value: dict) -> Controller:
    return Controller(name=value["name"], username=value.get("username", ""), user_id=int(value["user_id"]))


CONTROLLERS_BY_REGION: dict[str, tuple[Controller, ...]] = {
    f"R{number}": tuple(_controller(item) for item in _CONFIG.get("regions", {}).get(f"R{number}", []))
    for number in range(1, 9)
}
CONTROLLERS_BY_GROUP_CODE: dict[str, tuple[Controller, ...]] = {}
_DEFAULT_ADMIN_IDS = [
    int(x) for x in os.environ.get("GLOBAL_MANAGEMENT_USER_IDS", "5400514256").split(",") if x.strip().isdigit()
]
GLOBAL_EXPORT_USER_IDS: frozenset[int] = frozenset(map(int, _CONFIG.get("global_export_user_ids", _DEFAULT_ADMIN_IDS)))
GLOBAL_MANAGEMENT_USER_IDS: frozenset[int] = frozenset(map(int, _CONFIG.get("global_management_user_ids", _DEFAULT_ADMIN_IDS)))
EXPORT_AUDIT_RECIPIENTS: tuple[Controller, ...] = tuple(_controller(item) for item in _CONFIG.get("export_audit_recipients", []))

EXPORT_REGION_SCOPE: dict[str, tuple[str, ...]] = {
    "R1": ("R1",),
    "R2": ("R2",),
    "R3": ("R3",),
    "R4": ("R4", "R8"),
    "R5": ("R5",),
    "R6": ("R6", "R7"),
    "R7": ("R6", "R7"),
    "R8": ("R4", "R8"),
}

_REGION_PATTERN = re.compile(r"(?<![A-Z0-9])R([1-9])(?![0-9])", re.IGNORECASE)


def region_from_group(group_name: str | None) -> str | None:
    """
    Extract the region code from a Telegram group name.

    Examples:
        R2-SA-SVR4                     -> R2
        R3 - COMMANDO - (KTR4) ...    -> R3
        r5_sa_puk7                     -> R5
    """
    group = (group_name or "").strip()
    if not group:
        return None

    match = _REGION_PATTERN.search(group)
    if not match:
        return None

    return f"R{match.group(1)}"


def controllers_for_region(region: str | None) -> tuple[Controller, ...]:
    """Return controllers assigned to a region code."""
    normalized = (region or "").strip().upper()
    return CONTROLLERS_BY_REGION.get(normalized, ())


def controllers_for_group(group_name: str | None) -> tuple[Controller, ...]:
    """Return exact Region-Dealer controllers, falling back to regional defaults."""
    code = group_code_from_name(group_name)
    if code and code in CONTROLLERS_BY_GROUP_CODE:
        return CONTROLLERS_BY_GROUP_CODE[code]
    return controllers_for_region(region_from_group(group_name))


def normalize_group_code(value: str | None) -> str:
    text = re.sub(r"[^A-Z0-9]+", "-", (value or "").strip().upper()).strip("-")
    match = re.fullmatch(r"(R[1-8])-([A-Z]{2,6}\d{1,3})", text)
    if not match:
        raise ValueError(f"Invalid group '{value}'. Expected format R1-CA1")
    return f"{match.group(1)}-{match.group(2)}"


def group_code_from_name(group_name: str | None) -> str | None:
    text = re.sub(r"[^A-Z0-9]+", "-", (group_name or "").upper())
    region = re.search(r"(?:^|-)(R[1-8])(?:-|$)", text)
    dealers = re.findall(r"(?:^|-)([A-Z]{2,6}\d{1,3})(?:-|$)", text)
    if not region:
        return None
    dealer = next((x for x in dealers if not re.fullmatch(r"R[1-8]", x) and x != "SA"), None)
    return f"{region.group(1)}-{dealer}" if dealer else None


def replace_group_controller_assignments(rows: list[tuple[str, Controller]]) -> None:
    grouped: dict[str, list[Controller]] = {}
    for code, controller in rows:
        grouped.setdefault(code, []).append(controller)
    CONTROLLERS_BY_GROUP_CODE.clear()
    CONTROLLERS_BY_GROUP_CODE.update({code: tuple(items) for code, items in grouped.items()})


def export_regions_for_group(group_name: str | None) -> tuple[str, ...]:
    """Return the regional export scope for the current Telegram group."""
    region = region_from_group(group_name)
    if not region:
        return ()

    return EXPORT_REGION_SCOPE.get(region, ())


def controller_user_ids_for_group(group_name: str | None) -> set[int]:
    """Return Telegram user IDs of controllers assigned to the group."""
    return {
        controller.user_id
        for controller in controllers_for_group(group_name)
    }


def is_controller_for_group(
    telegram_user_id: int | None,
    group_name: str | None,
) -> bool:
    """
    Check whether a Telegram user is an assigned controller for the group.

    Used by commands such as:
        /AL <SA name>
        /AL Morning <SA name>
        /AL Afternoon <SA name>
        /AL off <SA name>
        /export
    """
    if telegram_user_id is None:
        return False

    return int(telegram_user_id) in controller_user_ids_for_group(group_name)



def all_controller_user_ids() -> set[int]:
    """Return every configured regional controller Telegram user ID."""
    return {
        controller.user_id
        for controllers in CONTROLLERS_BY_REGION.values()
        for controller in controllers
    }


def export_audit_user_ids() -> set[int]:
    """Return Telegram user IDs that may request and receive audit exports."""
    return set(GLOBAL_EXPORT_USER_IDS) | {
        recipient.user_id for recipient in EXPORT_AUDIT_RECIPIENTS
    }


def can_request_export_globally(telegram_user_id: int | None) -> bool:
    """Allow every configured controller and audit recipient to export from any SA group."""
    if telegram_user_id is None:
        return False
    user_id = int(telegram_user_id)
    return user_id in all_controller_user_ids() or user_id in export_audit_user_ids()

def controller_by_user_id(telegram_user_id: int | None) -> Controller | None:
    """Find a controller globally by Telegram user ID."""
    if telegram_user_id is None:
        return None

    target_id = int(telegram_user_id)

    for controllers in CONTROLLERS_BY_REGION.values():
        for controller in controllers:
            if controller.user_id == target_id:
                return controller

    return None


def controller_regions_by_user_id(telegram_user_id: int | None) -> tuple[str, ...]:
    """Return every region controlled by the Telegram user."""
    if telegram_user_id is None:
        return ()

    target_id = int(telegram_user_id)

    regions = tuple(
        region
        for region, controllers in CONTROLLERS_BY_REGION.items()
        if any(controller.user_id == target_id for controller in controllers)
    )

    return regions


def controller_label_for_group(group_name: str | None) -> str:
    """
    Build a readable controller label for Telegram messages.

    Example:
        Controller: Chan Sela CE(R5) | @chan_sela
    """
    controllers = controllers_for_group(group_name)
    if not controllers:
        return "Controller: Not configured"

    prefix = "Controller" if len(controllers) == 1 else "Controllers"

    lines = [
        f"{index}. {controller.name} | @{controller.normalized_username}"
        for index, controller in enumerate(controllers, start=1)
    ]

    if len(lines) == 1:
        return f"{prefix}: {lines[0].split('. ', 1)[1]}"

    return f"{prefix}:\n" + "\n".join(lines)

def can_manage_group(telegram_user_id: int | None, group_name: str | None) -> bool:
    """Allow a global management admin or the assigned regional controller."""
    if telegram_user_id is None:
        return False
    user_id = int(telegram_user_id)
    return user_id in GLOBAL_MANAGEMENT_USER_IDS or user_id in controller_user_ids_for_group(group_name)


def set_controller_for_region(region: str, controller: Controller) -> None:
    """Update the process-wide controller mapping after a persisted change."""
    normalized = (region or "").strip().upper()
    if normalized not in CONTROLLERS_BY_REGION:
        raise ValueError("Region must be R1-R8")
    CONTROLLERS_BY_REGION[normalized] = (controller,)


def remove_controller_for_region(region: str) -> None:
    """Remove the current process-wide controller assignment for a region."""
    normalized = (region or "").strip().upper()
    if normalized not in CONTROLLERS_BY_REGION:
        raise ValueError("Region must be R1-R8")
    CONTROLLERS_BY_REGION[normalized] = ()
