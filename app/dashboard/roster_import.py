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
        "employee id": "employee_id",
        "employee_id": "employee_id",
        "empployee id": "employee_id",
        "empployee_id": "employee_id",
        "emp id": "employee_id",
        "emp_id": "employee_id",
        "staff id": "employee_id",
        "staff_id": "employee_id",
        "id": "employee_id",
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
    headers = []
    for v in header_row:
        col = " ".join(str(v or "").strip().lower().split())
        mapped = aliases.get(col)
        if not mapped and "emp" in col and "id" in col:
            mapped = "employee_id"
        headers.append(mapped or col)
    if "name" not in headers or ("group" not in headers and ("region" not in headers or "dealer" not in headers)):
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
        if not name:
            continue
        group_name = cell(row, "group")
        raw_tid = cell(row, "telegram_id").split(".")[0].strip()
        telegram_id = int(raw_tid) if raw_tid.isdigit() and int(raw_tid) > 0 else None
        username = normalize_username(cell(row, "username"))
        code = group_code_from_name(group_name) if group_name else ""
        region = (cell(row, "region") or region_from_group(group_name) or (code.split("-")[0] if code else "")).strip().upper()
        dealer = (cell(row, "dealer") or (code.split("-", 1)[1] if code and "-" in code else "")).strip().upper()
        if region not in {f"R{i}" for i in range(1, 9)} or not dealer:
            raise ValueError(f"Row {line}: enter a valid Region (R1-R8) and Dealer code.")
        position = _display_position(cell(row, "position") or "SA")
        raw_wt = cell(row, "working_time")
        working_time = canonical_working_time_for_member({"position": position, "working_time": raw_wt})
        lookup_group = group_name or f"{region}-SA-{dealer}"
        assigned = controllers_for_group(lookup_group) or controllers_for_region(region)
        default_ctrl = ", ".join(c.name for c in assigned)
        raw_ctrl = cell(row, "controller")
        if not raw_ctrl or "neab sona" in raw_ctrl.lower():
            controller = default_ctrl
        else:
            controller = raw_ctrl
        entry = {
            "telegram_user_id": telegram_id,
            "name": name,
            "username": username,
            "region": region,
            "dealer": dealer,
            "position": position,
            "working_time": working_time,
            "group_name": group_name,
            "controller": controller,
        }
        if "employee_id" in idx:
            entry["employee_id"] = cell(row, "employee_id").split(".")[0].strip() or None
        parsed.append(entry)

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
    registered_groups = db.query(TelegramGroup).filter(TelegramGroup.group_id < 0).all()
    group_name_by_id = {g.group_id: g.group_name for g in registered_groups if g.group_name}
    group_by_norm = {normalize_group(g.group_name): g.group_name for g in registered_groups if g.group_name}
    groups_by_code: dict[str, list[str]] = {}
    for g in registered_groups:
        if not g.group_name:
            continue
        gcode = group_code_from_name(g.group_name)
        if gcode:
            groups_by_code.setdefault(gcode, []).append(g.group_name)

    # Single query to load all Telegram group SAUser rows:
    # 1) resolves each SA's active Telegram group directly
    # 2) preserves known Telegram usernames when CSV Username is blank
    # 3) allows fast in-memory bulk sync without N+1 DB round-trips
    all_sa_users = db.query(SAUser).filter(SAUser.group_id < 0).all()
    sa_users_by_group_id: dict[int, list[SAUser]] = {}
    active_candidates_by_tid: dict[int, list[SAUser]] = {}
    for u in all_sa_users:
        sa_users_by_group_id.setdefault(u.group_id, []).append(u)
        if u.telegram_user_id and int(u.telegram_user_id) > 0:
            active_candidates_by_tid.setdefault(int(u.telegram_user_id), []).append(u)

    active_group_by_tid: dict[int, str] = {}
    active_username_by_tid: dict[int, str] = {}
    for tid_int, users in active_candidates_by_tid.items():
        users.sort(
            key=lambda u: (
                1 if u.group_id in group_name_by_id else 0,
                1 if u.last_pin_at is not None else 0,
                u.last_pin_at.timestamp() if u.last_pin_at is not None else 0.0,
                u.updated_at.timestamp() if u.updated_at is not None else 0.0,
                u.id or 0,
            ),
            reverse=True,
        )
        for u in users:
            resolved_tg_name = group_name_by_id.get(u.group_id) or (u.group_name or "").strip()
            if resolved_tg_name and tid_int not in active_group_by_tid:
                active_group_by_tid[tid_int] = group_by_norm.get(normalize_group(resolved_tg_name), resolved_tg_name)
            uname = normalize_username(u.username)
            if uname and tid_int not in active_username_by_tid:
                active_username_by_tid[tid_int] = uname

    existing_all = db.query(SAList).order_by(SAList.is_deleted.asc(), SAList.id.asc()).all()
    by_tid_group = {}
    by_tid_any: dict[int, list[SAList]] = {}
    by_name_group = {}
    by_name_any: dict[str, list[SAList]] = {}
    by_exact_pair: dict[tuple[str, str], SAList] = {}
    for r in existing_all:
        gn = normalize_group(r.group_name)
        nn = normalize_name(r.name)
        by_exact_pair[(r.group_name, r.name)] = r
        if r.telegram_user_id and int(r.telegram_user_id) > 0:
            tid_int = int(r.telegram_user_id)
            by_tid_group.setdefault((gn, tid_int), r)
            by_tid_any.setdefault(tid_int, []).append(r)
        if nn:
            by_name_group.setdefault((gn, nn), r)
            by_name_any.setdefault(nn, []).append(r)

    retained_rows: set[SAList] = set()
    touched_groups: set[str] = set()
    active_members_by_gnorm: dict[str, list[SAList]] = {}

    def _resolve_group(m: dict) -> str:
        # Priority 1: assign Group directly from the Telegram group where this SA is active
        tid = m["telegram_user_id"]
        if tid and tid in active_group_by_tid:
            return active_group_by_tid[tid]

        raw_group = (m["group_name"] or "").strip()
        if raw_group:
            gnorm = normalize_group(raw_group)
            if gnorm in group_by_norm:
                return group_by_norm[gnorm]
            gcode = group_code_from_name(raw_group)
            matches = groups_by_code.get(gcode, []) if gcode else []
            if len(matches) == 1:
                return matches[0]
            return raw_group

        if tid and tid in by_tid_any:
            for existing_row in by_tid_any[tid]:
                if (existing_row.region or "").strip().upper() == m["region"] and existing_row.group_name:
                    return group_by_norm.get(normalize_group(existing_row.group_name), existing_row.group_name)

        code = f"{m['region']}-{m['dealer']}"
        matches = groups_by_code.get(code, [])
        if not matches and "&" in m["dealer"]:
            first_dealer = m["dealer"].split("&", 1)[0].strip()
            matches = groups_by_code.get(f"{m['region']}-{first_dealer}", [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            is_commando = m["position"] == "Commando"
            for candidate in matches:
                if is_commando and "commando" in candidate.lower():
                    return candidate
                if not is_commando and "-sa-" in candidate.lower():
                    return candidate
            return matches[0]
        return f"{m['region']}-SA-{m['dealer']}"

    for m in records:
        resolved_group = _resolve_group(m)
        gnorm = normalize_group(resolved_group)
        nkey = normalize_name(m["name"])
        tid = m["telegram_user_id"]
        row = None
        if tid:
            cand = by_tid_group.get((gnorm, tid))
            if cand is not None and cand not in retained_rows:
                row = cand
            if row is None:
                for cand in by_tid_any.get(tid, ()):
                    if cand not in retained_rows:
                        row = cand
                        break
        if row is None:
            cand = by_name_group.get((gnorm, nkey))
            if cand is not None and cand not in retained_rows:
                row = cand
        if row is None and (resolved_group, m["name"]) in by_exact_pair:
            cand = by_exact_pair[(resolved_group, m["name"])]
            if cand not in retained_rows:
                row = cand
        if row is None and nkey:
            cands = [c for c in by_name_any.get(nkey, ()) if c not in retained_rows]
            if len(cands) == 1:
                row = cands[0]

        resolved_username = (
            m["username"]
            or (row.username if row is not None and row.username else None)
            or (active_username_by_tid.get(tid) if tid else None)
        )
        identity = f"id:{tid}" if tid else f"username:{resolved_username}" if resolved_username else f"name:{nkey}"
        source_key = f"{gnorm}|{identity}"
        if row is not None:
            retained_rows.add(row)
            old_gnorm = normalize_group(row.group_name)
            has_emp_col = "employee_id" in m
            emp_val = m.get("employee_id")
            changed = (
                row.telegram_user_id != tid
                or (has_emp_col and row.employee_id != emp_val)
                or row.name != m["name"]
                or row.username != resolved_username
                or row.region != m["region"]
                or row.dealer != m["dealer"]
                or row.position != m["position"]
                or row.working_time != m["working_time"]
                or row.group_name != resolved_group
                or row.controller != m["controller"]
                or row.is_deleted != 0
                or row.is_active != 1
            )
            row.telegram_user_id = tid
            if has_emp_col:
                row.employee_id = emp_val
            row.name = m["name"]
            row.username = resolved_username
            row.region = m["region"]
            row.dealer = m["dealer"]
            row.position = m["position"]
            row.working_time = m["working_time"]
            row.group_name = resolved_group
            row.controller = m["controller"]
            row.source_key = source_key
            row.dashboard_override = 1
            row.is_deleted = 0
            row.is_active = 1
            if changed:
                row.updated_at = now
                if old_gnorm:
                    touched_groups.add(old_gnorm)
                if gnorm:
                    touched_groups.add(gnorm)
        else:
            row = SAList(
                telegram_user_id=tid,
                employee_id=m.get("employee_id"),
                name=m["name"],
                username=resolved_username,
                region=m["region"],
                dealer=m["dealer"],
                position=m["position"],
                working_time=m["working_time"],
                group_name=resolved_group,
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
            retained_rows.add(row)
            if gnorm:
                touched_groups.add(gnorm)
            by_exact_pair[(resolved_group, m["name"])] = row
            by_name_group.setdefault((gnorm, nkey), row)
            by_name_any.setdefault(nkey, []).append(row)
            if tid:
                by_tid_group.setdefault((gnorm, tid), row)
                by_tid_any.setdefault(tid, []).append(row)

        if gnorm:
            active_members_by_gnorm.setdefault(gnorm, []).append(row)

    for old_row in existing_all:
        if old_row not in retained_rows and (old_row.is_deleted == 0 or old_row.is_active == 1):
            old_row.is_deleted = 1
            old_row.is_active = 0
            old_row.dashboard_override = 1
            old_row.updated_at = now
            old_gnorm = normalize_group(old_row.group_name)
            if old_gnorm:
                touched_groups.add(old_gnorm)

    # Fast in-memory bulk sync for SAUser rows in touched registered Telegram groups
    for g in registered_groups:
        gnorm = normalize_group(g.group_name)
        if not gnorm or gnorm not in touched_groups:
            continue
        group_users = sa_users_by_group_id.get(g.group_id, [])
        if not group_users:
            continue
        members = active_members_by_gnorm.get(gnorm, [])
        user_by_tid = {int(u.telegram_user_id): u for u in group_users if u.telegram_user_id and int(u.telegram_user_id) > 0}
        user_by_name = {normalize_name(u.official_name or u.name): u for u in group_users if normalize_name(u.official_name or u.name)}
        matched_users: set[SAUser] = set()
        for member in members:
            u = None
            if member.telegram_user_id and int(member.telegram_user_id) > 0:
                u = user_by_tid.get(int(member.telegram_user_id))
            if u is None:
                u = user_by_name.get(normalize_name(member.name))
            if u is not None:
                matched_users.add(u)
                u.group_name = g.group_name
                u.official_name = member.name
                if member.username:
                    u.official_username = member.username
                u.region = member.region
                u.dealer = member.dealer
                u.position = member.position
                u.flow_key = flow_key_for_position(member.position)
                u.working_time = member.working_time
                u.is_from_list = 1
                u.source_type = "excel"
                u.updated_at = now
        for u in group_users:
            if u not in matched_users and u.source_type == "excel" and u.is_from_list != 0:
                u.is_from_list = 0
                u.updated_at = now

    db.commit()
    return {"count": len(records), "message": f"Uploaded {len(records)} Master SA records."}
