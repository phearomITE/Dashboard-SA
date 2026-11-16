# app/data/sa_members.py
# Generated from the uploaded Excel list. Do not edit rows manually.
from __future__ import annotations

import re
import unicodedata

import json
from pathlib import Path

_MASTER_PATH = Path(__file__).with_name("master_sa_20261114.json")
MASTER_ASSIGNMENTS = json.loads(_MASTER_PATH.read_text(encoding="utf-8")) if _MASTER_PATH.is_file() else []


SA_MEMBERS = [row for row in MASTER_ASSIGNMENTS if row["is_active"]]


def normalize_username(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    return value.lstrip("@").lower()


def normalize_group(value: str | None) -> str:
    """Normalize harmless punctuation/spacing differences in Telegram group titles."""
    text = unicodedata.normalize("NFKC", value or "").upper()
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return "".join(ch for ch in text if ch.isalnum())


def _group_signature(value: str | None) -> tuple[str, str, str] | None:
    """Return a safe fallback key: (region, category, dealer)."""
    text = unicodedata.normalize("NFKC", value or "").upper()
    region_match = re.search(r"(?<![A-Z0-9])R\s*([1-9])(?![A-Z0-9])", text)
    if not region_match:
        region_match = re.search(r"R([1-9])", text)
    dealer_match = re.search(r"(?<![A-Z0-9])([A-Z]{2,4}\d+)(?![A-Z0-9])", text)
    if not region_match or not dealer_match:
        return None

    compact = normalize_group(text)
    if "COMMANDO" in compact or "CAMMONDO" in compact:
        category = "COMMANDO"
    elif "SPECIALIST" in compact:
        category = "SPECIALIST"
    elif "SA" in compact:
        category = "SA"
    else:
        category = "OTHER"
    return (f"R{region_match.group(1)}", category, dealer_match.group(1))


_EXACT_GROUP_INDEX: dict[str, list[dict]] = {}
_SIGNATURE_GROUP_NAMES: dict[tuple[str, str, str], set[str]] = {}
for _member in SA_MEMBERS:
    _exact = normalize_group(_member.get("group_name"))
    _EXACT_GROUP_INDEX.setdefault(_exact, []).append(_member)
    _sig = _group_signature(_member.get("group_name"))
    if _sig:
        _SIGNATURE_GROUP_NAMES.setdefault(_sig, set()).add(_exact)


def members_for_group(group_name: str | None) -> list[dict]:
    exact = normalize_group(group_name)
    direct = _EXACT_GROUP_INDEX.get(exact)
    if direct:
        return list(direct)

    signature = _group_signature(group_name)
    if not signature:
        return []

    group_names = _SIGNATURE_GROUP_NAMES.get(signature, set())
    if len(group_names) != 1:
        return []
    only_group = next(iter(group_names))
    return list(_EXACT_GROUP_INDEX.get(only_group, []))
