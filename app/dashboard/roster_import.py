import csv
from io import BytesIO, StringIO
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.data.controllers import controllers_for_group, controllers_for_region, group_code_from_name, region_from_group
from app.data.sa_members import normalize_group, normalize_username
from app.database.models import SAList, SAUser, TelegramGroup
from app.services.export_service import _display_position
from app.services.time_service import local_now
from app.services.user_service import (
    canonical_working_time_for_member,
    flow_key_for_position,
    normalize_name,
    sync_group_members_from_list,
)


def parse_master_sa_rows(rows):
    header_row = next(rows, None)
    if not header_row:
        raise ValueError("The uploaded file is empty.")
    aliases = {
        "telegram id": "telegram_id",
        "telegram_id": "telegram_id",
        "telegram user id": "telegram_id",
        "telegram_user_id": "telegram_id",
        "sa name": "name",
        "sa_name": "name",
        "name": "name",
        "username": "username",
        "region": "region",
        "dealer": "dealer",
        "position": "position",
        "working time": "working_time",
        "working_time": "working_time",
        "shift": "working_time",
        "group": "group",
        "group name": "group",
        "group_name": "group",
        "controller": "controller",
    }
    headers = [aliases.get(str(v or "").strip().lower(), str(v or "").strip().lower()) for v in header_row]
    required = ("name", "region", "dealer", "group")
    for req in required:
        if req not in headers:
            raise ValueError(
                "Include columns: Telegram ID, SA Name, Username, Region, Dealer, Position, Working Time, Group, Controller."
            )

    idx = {col: headers.index(col) for col in set(headers)}

    def cell(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return ""
        val = row[i]
        return "" if val is None else str(val).strip()

    parsed = []
    for line, row in enumerate(rows, 2):
        if line > 10001:
            raise ValueError("Upload up to 10,000 rows at a time.")
        if all(v in (None, "") for v in row):
            continue
        name = cell(row, "name")
        group_name = cell(row, "group")
        if not name or not group_name:
            continue
        raw_tid = cell(row, "telegram_id").split(".")[0].strip()
        telegram_id = int(raw_tid) if raw_tid.isdigit() and int(raw_tid) > 0 else None
        username = normalize_username(cell(row, "username"))
        code = group_code_from_name(group_name)
        region = (cell(row, "region") or region_from_group(group_name) or (code.split("-")[0] if code else "")).strip().upper()
        dealer = (cell(row, "dealer") or (code.split("-", 1)[1] if code and "-" in code else "")).strip().upper()
        if region not in {f"R{i}" for i in range(1, 9)} or not dealer:
            raise ValueError(f"Row {line}: enter a valid Region (R1-R8) and Dealer code.")
        position = _display_position(cell(row, "position") or "SA")
        raw_wt = cell(row, "working_time")
        working_time = canonical_working_time_for_member({"position": position, "working_time": raw_wt})
        assigned = controllers_for_group(group_name) or controllers_for_region(region)
        default_ctrl = ", ".join(c.name for c in assigned)
        raw_ctrl = cell(row, "controller")
        if not raw_ctrl or "neab sona" in raw_ctrl.lower():
            controller = default_ctrl
        else:
            controller = raw_ctrl
        parsed.append({
            "telegram_user_id": telegram_id,
            "name": name,
            "username": username,
            "region": region,
            "dealer": dealer,
            "position": position,
            "working_time": working_time,
            "group_name": group_name,
            "controller": controller,
        })

    if not parsed:
        raise ValueError("The uploaded file contains no valid SA member rows.")
    return parsed


def import_master_sa(db, content: bytes, extension: str):
    if extension == "csv":
        try:
            records = parse_master_sa_rows(csv.reader(StringIO(content.decode("utf-8-sig")), strict=True))
        except (UnicodeError, csv.Error):
            raise ValueError("Provide a UTF-8 CSV or an Excel workbook.") from None
    else:
        try:
            with ZipFile(BytesIO(content)) as archive:
                if sum(v.file_size for v in archive.infolist()) > 30_000_000:
                    raise ValueError("The workbook is too large after decompression.")
            book = load_workbook(BytesIO(content), read_only=True, data_only=True)
            try:
                records = parse_master_sa_rows(book.active.values)
            finally:
                book.close()
        except (BadZipFile, InvalidFileException, KeyError, SyntaxError, OSError):
            raise ValueError("This file could not be read as an Excel workbook.") from None

    now = local_now()
    existing_all = db.query(SAList).all()
    by_tid = {
        (normalize_group(r.group_name), int(r.telegram_user_id)): r
        for r in existing_all if r.telegram_user_id and int(r.telegram_user_id) > 0
    }
    by_name = {
        (normalize_group(r.group_name), normalize_name(r.name)): r
        for r in existing_all
    }
    retained_ids = set()

    for m in records:
        gnorm = normalize_group(m["group_name"])
        nkey = normalize_name(m["name"])
        tid = m["telegram_user_id"]
        row = None
        if tid:
            row = by_tid.get((gnorm, tid))
        if row is None:
            row = by_name.get((gnorm, nkey))
        identity = f"id:{tid}" if tid else f"username:{m['username']}" if m["username"] else f"name:{nkey}"
        source_key = f"{gnorm}|{identity}"
        if row is not None:
            retained_ids.add(row.id)
            row.telegram_user_id = tid
            row.name = m["name"]
            row.username = m["username"]
            row.region = m["region"]
            row.dealer = m["dealer"]
            row.position = m["position"]
            row.working_time = m["working_time"]
            row.group_name = m["group_name"]
            row.controller = m["controller"]
            row.source_key = source_key
            row.dashboard_override = 1
            row.is_deleted = 0
            row.is_active = 1
            row.updated_at = now
        else:
            row = SAList(
                telegram_user_id=tid,
                name=m["name"],
                username=m["username"],
                region=m["region"],
                dealer=m["dealer"],
                position=m["position"],
                working_time=m["working_time"],
                group_name=m["group_name"],
                controller=m["controller"],
                source_type="excel",
                source_key=source_key,
                dashboard_override=1,
                is_deleted=0,
                is_active=1,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            retained_ids.add(row.id)
            by_name[(gnorm, nkey)] = row
            if tid:
                by_tid[(gnorm, tid)] = row

    for g in db.query(TelegramGroup).filter(TelegramGroup.group_id < 0).all():
        sync_group_members_from_list(db, g.group_id, g.group_name)

    db.commit()
    return {"count": len(records), "message": f"Uploaded {len(records)} Master SA records."}
