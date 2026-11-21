from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func

from app.data.controllers import group_code_from_name, region_from_group
from app.database.models import DealerLocation, PinHistory, SAList, SAUser, SalePhotoReport
from app.services.time_service import local_now

REPORT_DAILY_REGIONS: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")

REPORT_DAILY_DEALERS: tuple[tuple[str, str], ...] = (
    ("R1", "CA1"), ("R1", "CA2"), ("R1", "CA3"), ("R1", "CA4"), ("R1", "CA5"),
    ("R1", "CA6"), ("R1", "CA7"), ("R1", "CA8"), ("R1", "CA9"), ("R1", "PTM6"),
    ("R2", "BTI6"), ("R2", "CPH2"), ("R2", "KDL1"), ("R2", "KHT3"), ("R2", "KRV5"),
    ("R2", "KSV5"), ("R2", "KTB1"), ("R2", "PKB4"), ("R2", "PMR5"), ("R2", "SVA7"),
    ("R2", "SVR4"), ("R2", "TAK2"),
    ("R3", "CUK7"), ("R3", "KKG3"), ("R3", "KPS7"), ("R3", "KPT2"), ("R3", "KSM1"),
    ("R3", "KSP3"), ("R3", "KTR4"), ("R3", "PNP6"), ("R3", "SRA5"), ("R3", "TPG2"),
    ("R4", "BTB1"), ("R4", "BVL6"), ("R4", "KCG4"), ("R4", "KTL6"), ("R4", "MRS5"),
    ("R4", "PLN4"), ("R4", "PUS5"), ("R4", "VVG1"),
    ("R5", "BMC2"), ("R5", "CHK6"), ("R5", "KRG7"), ("R5", "KTH2"), ("R5", "KTM8"),
    ("R5", "PPR8"), ("R5", "PPT3"), ("R5", "PUK7"), ("R5", "SRP1"),
    ("R6", "BTY8"), ("R6", "KCM1"), ("R6", "MMT3"), ("R6", "STT4"), ("R6", "TKM2"),
    ("R7", "BKE7"), ("R7", "KRT1"), ("R7", "KSA5"), ("R7", "MDK2"), ("R7", "RTK4"),
    ("R7", "SNL6"), ("R7", "STR3"),
    ("R8", "AVG4"), ("R8", "CKS1"), ("R8", "PVH3"), ("R8", "SMR5"),
)

REPORT_DAILY_SKUS: tuple[str, ...] = (
    "C COLA CAN 250ML",
    "C COLA CAN 330ML",
    "C COLA CAN 330ML (Zero Sugar)",
    "C ENERGY DRINK CAN 250ML",
    "C SPORT DRINK PET 500ML",
    "WATER PET 1.5L",
    "WATER PET 500ML",
    "WATER PET 350ML",
    "EXPREZ MELON CAN 330ML",
    "EXPREZ STRAWBERRY CAN 330ML",
    "EXPREZ PET 300ML",
    "IZE PET 300ML",
    "IZE PET 500ML",
    "IZE PET 1.5L",
    "IZE CAN 250ML",
    "IZE CAN 330ML",
    "WURKZ ICE CAN 250ML",
    "DAZZ SUGAR FREE CAN 250ML",
    "WURKZ CAN 250ML",
    "DAZZ CAN 250ML",
    "CB CAN 330ML",
    "CBL CAN 330ML",
    "CBB CAN 330ML",
    "CB CAN 4.4",
)


def _norm_label(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9.()]+", " ", (text or "").lower()).split())


_SKU_ALIAS_MAP: dict[str, str] = {_norm_label(sku): sku for sku in REPORT_DAILY_SKUS}
_SKU_ALIAS_MAP.update({
    _norm_label("C COLA CAN 330ML ZERO SUGAR"): "C COLA CAN 330ML (Zero Sugar)",
    _norm_label("C COLA ZERO CAN 330ML"): "C COLA CAN 330ML (Zero Sugar)",
    _norm_label("CBC CAN 330ML"): "CB CAN 330ML",
    _norm_label("CBC CAN 4.4 (330ml)"): "CB CAN 4.4",
    _norm_label("CBC CAN 4.4"): "CB CAN 4.4",
    _norm_label("CB CAN 4.4 (330ml)"): "CB CAN 4.4",
})

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")


def extract_purchased_skus(caption: str | None) -> set[str]:
    """Return the set of template SKU names with a positive quantity in a Telegram caption."""
    purchased: set[str] = set()
    if not caption:
        return purchased
    for line in caption.splitlines():
        if ":" not in line and "：" not in line:
            continue
        sep = ":" if ":" in line else "："
        label, _, raw_qty = line.partition(sep)
        sku = _SKU_ALIAS_MAP.get(_norm_label(label))
        if not sku:
            continue
        qty_text = raw_qty.strip()
        if not qty_text:
            continue
        match = _NUM_RE.search(qty_text)
        if match and float(match.group(1)) > 0:
            purchased.add(sku)
    return purchased


def _position_bucket(raw: str | None) -> str:
    key = (raw or "SA").strip().lower().replace("_", " ").replace("-", " ")
    if "team leader" in key or "leader" in key:
        return "sa_leader"
    if "commando" in key or "cammondo" in key:
        return "commando"
    if "support ws" in key or "sa ws" in key or key == "ws":
        return "sa_ws"
    return "sa"


def _empty_bucket(region: str, dealer: str | None = None) -> dict:
    row = {
        "region": region,
        "sa": 0,
        "sa_ws": 0,
        "commando": 0,
        "sa_leader": 0,
        "al": 0,
        "active": 0,
        "district": 0,
        "commune": 0,
        "outlets_visit": 0,
        "avg_outlets_visit": 0.0,
        "skus": {sku: 0 for sku in REPORT_DAILY_SKUS},
    }
    if dealer is not None:
        row["dealer"] = dealer
    return row


def daily_report_data(db, from_date: date | None = None, to_date: date | None = None) -> dict:
    """Build the Report Daily summary (by Region) and detail (by Dealer) matching report_SO_template."""
    now = local_now()
    start_day = from_date or to_date or now.date()
    end_day = to_date or from_date or now.date()
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    start_dt = datetime.combine(start_day, time.min)
    end_dt = datetime.combine(end_day + timedelta(days=1), time.min)

    # 1. Roster members (Sales Team counts + fallback scope lookup)
    roster_rows = (
        db.query(SAList)
        .filter(SAList.is_deleted == 0, SAList.is_active == 1)
        .all()
    )
    roster_scope_by_tid: dict[int, tuple[str, str]] = {}

    dealer_keys: list[tuple[str, str]] = list(REPORT_DAILY_DEALERS)
    dealer_key_set = set(dealer_keys)
    for d in db.query(DealerLocation).all():
        key = ((d.region or "").strip().upper(), (d.dealer or "").strip().upper())
        if key[0] in REPORT_DAILY_REGIONS and key[1] and key not in dealer_key_set:
            dealer_keys.append(key)
            dealer_key_set.add(key)

    region_buckets = {r: _empty_bucket(r) for r in REPORT_DAILY_REGIONS}
    dealer_buckets = {k: _empty_bucket(k[0], k[1]) for k in dealer_keys}

    def _ensure_dealer(reg: str, dlr: str) -> tuple[str, str] | None:
        if reg not in region_buckets or not dlr:
            return None
        key = (reg, dlr)
        if key not in dealer_buckets:
            dealer_buckets[key] = _empty_bucket(reg, dlr)
            dealer_keys.append(key)
        return key

    for r in roster_rows:
        reg = (r.region or "").strip().upper()
        dlr = (r.dealer or "").strip().upper()
        if r.telegram_user_id and int(r.telegram_user_id) > 0 and reg in region_buckets:
            roster_scope_by_tid[int(r.telegram_user_id)] = (reg, dlr)
        bucket_key = _position_bucket(r.position)
        if reg in region_buckets:
            region_buckets[reg][bucket_key] += 1
        dkey = _ensure_dealer(reg, dlr)
        if dkey:
            dealer_buckets[dkey][bucket_key] += 1

    # 2. Annual Leave overlapping [start_dt, end_dt)
    al_users = (
        db.query(SAUser)
        .filter(
            SAUser.group_id < 0,
            SAUser.alert_paused_until.isnot(None),
            SAUser.alert_paused_until >= start_dt,
        )
        .all()
    )
    al_tids_region: dict[str, set[int]] = defaultdict(set)
    al_tids_dealer: dict[tuple[str, str], set[int]] = defaultdict(set)
    for u in al_users:
        p_start = u.alert_pause_start or u.updated_at or u.created_at
        if p_start and p_start >= end_dt:
            continue
        tid = int(u.telegram_user_id or 0)
        if tid <= 0:
            continue
        fallback = roster_scope_by_tid.get(tid, ("", ""))
        code = group_code_from_name(u.group_name)
        reg = (u.region or fallback[0] or region_from_group(u.group_name) or (code.split("-")[0] if code else "")).strip().upper()
        dlr = (u.dealer or fallback[1] or (code.split("-", 1)[1] if code and "-" in code else "")).strip().upper()
        if reg in region_buckets:
            al_tids_region[reg].add(tid)
        dkey = _ensure_dealer(reg, dlr)
        if dkey:
            al_tids_dealer[dkey].add(tid)

    # 3. Check-ins (PinHistory) -> Active SAs, Districts, Communes, # Outlets Visit
    stamp = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    pins = (
        db.query(PinHistory)
        .filter(PinHistory.group_id < 0, stamp >= start_dt, stamp < end_dt)
        .all()
    )
    active_tids_region: dict[str, set[int]] = defaultdict(set)
    active_tids_dealer: dict[tuple[str, str], set[int]] = defaultdict(set)
    districts_region: dict[str, set[tuple[str, str]]] = defaultdict(set)
    districts_dealer: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    communes_region: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    communes_dealer: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)

    for p in pins:
        tid = int(p.telegram_user_id or 0)
        fallback = roster_scope_by_tid.get(tid, ("", ""))
        code = group_code_from_name(p.group_name)
        reg = (p.region or fallback[0] or region_from_group(p.group_name) or (code.split("-")[0] if code else "")).strip().upper()
        dlr = (p.dealer or fallback[1] or (code.split("-", 1)[1] if code and "-" in code else "")).strip().upper()
        prov = (p.province or "").strip()
        dist = (p.district or "").strip()
        comm = (p.commune or "").strip()
        if reg in region_buckets:
            region_buckets[reg]["outlets_visit"] += 1
            if tid > 0:
                active_tids_region[reg].add(tid)
            if dist:
                districts_region[reg].add((prov, dist))
            if comm:
                communes_region[reg].add((prov, dist, comm))
        dkey = _ensure_dealer(reg, dlr)
        if dkey:
            dealer_buckets[dkey]["outlets_visit"] += 1
            if tid > 0:
                active_tids_dealer[dkey].add(tid)
            if dist:
                districts_dealer[dkey].add((prov, dist))
            if comm:
                communes_dealer[dkey].add((prov, dist, comm))

    # 4. SalePhotoReport -> Execution Purchase outlet By SKU (deduplicated per album / outlet)
    reports = (
        db.query(SalePhotoReport)
        .filter(
            SalePhotoReport.group_id < 0,
            SalePhotoReport.submitted_at >= start_dt,
            SalePhotoReport.submitted_at < end_dt,
        )
        .order_by(SalePhotoReport.submitted_at.asc(), SalePhotoReport.id.asc())
        .all()
    )
    seen_album_skus: dict[tuple, set[str]] = defaultdict(set)
    for rep in reports:
        skus = extract_purchased_skus(rep.caption)
        if not skus:
            continue
        tid = int(rep.telegram_user_id or 0)
        fallback = roster_scope_by_tid.get(tid, ("", ""))
        code = group_code_from_name(rep.group_name)
        reg = (fallback[0] or region_from_group(rep.group_name) or (code.split("-")[0] if code else "")).strip().upper()
        dlr = (fallback[1] or (code.split("-", 1)[1] if code and "-" in code else "")).strip().upper()
        if rep.media_group_id:
            dedup_key = ("mg", tid, int(rep.group_id or 0), str(rep.media_group_id))
        else:
            ts = rep.submitted_at
            minute_bucket = ts.strftime("%Y%m%d%H%M") if ts else str(rep.id)
            dedup_key = ("cap", tid, int(rep.group_id or 0), minute_bucket, (rep.caption or "").strip()[:120])
        new_skus = skus - seen_album_skus[dedup_key]
        if not new_skus:
            continue
        seen_album_skus[dedup_key].update(new_skus)
        if reg in region_buckets:
            for sku in new_skus:
                region_buckets[reg]["skus"][sku] += 1
        dkey = _ensure_dealer(reg, dlr)
        if dkey:
            for sku in new_skus:
                dealer_buckets[dkey]["skus"][sku] += 1

    # Finalize region rows & totals
    regions_out = []
    all_districts: set[tuple[str, str]] = set()
    all_communes: set[tuple[str, str, str]] = set()
    all_active: set[int] = set()
    all_al: set[int] = set()
    for reg in REPORT_DAILY_REGIONS:
        row = region_buckets[reg]
        row["al"] = len(al_tids_region[reg])
        row["active"] = len(active_tids_region[reg])
        row["district"] = len(districts_region[reg])
        row["commune"] = len(communes_region[reg])
        row["avg_outlets_visit"] = round(row["outlets_visit"] / row["active"], 1) if row["active"] > 0 else 0.0
        regions_out.append(row)
        all_districts.update(districts_region[reg])
        all_communes.update(communes_region[reg])
        all_active.update(active_tids_region[reg])
        all_al.update(al_tids_region[reg])

    regions_total = _empty_bucket("Grand Total")
    for row in regions_out:
        for field in ("sa", "sa_ws", "commando", "sa_leader", "outlets_visit"):
            regions_total[field] += row[field]
        for sku in REPORT_DAILY_SKUS:
            regions_total["skus"][sku] += row["skus"][sku]
    regions_total["al"] = len(all_al)
    regions_total["active"] = len(all_active)
    regions_total["district"] = len(all_districts)
    regions_total["commune"] = len(all_communes)
    regions_total["avg_outlets_visit"] = (
        round(regions_total["outlets_visit"] / regions_total["active"], 1) if regions_total["active"] > 0 else 0.0
    )

    # Finalize dealer rows & totals
    dealer_keys.sort(key=lambda k: (REPORT_DAILY_REGIONS.index(k[0]) if k[0] in REPORT_DAILY_REGIONS else 99, k[1]))
    dealers_out = []
    for dkey in dealer_keys:
        row = dealer_buckets[dkey]
        row["al"] = len(al_tids_dealer[dkey])
        row["active"] = len(active_tids_dealer[dkey])
        row["district"] = len(districts_dealer[dkey])
        row["commune"] = len(communes_dealer[dkey])
        row["avg_outlets_visit"] = round(row["outlets_visit"] / row["active"], 1) if row["active"] > 0 else 0.0
        dealers_out.append(row)

    dealers_total = _empty_bucket("Grand Total", "")
    for row in dealers_out:
        for field in ("sa", "sa_ws", "commando", "sa_leader", "outlets_visit"):
            dealers_total[field] += row[field]
        for sku in REPORT_DAILY_SKUS:
            dealers_total["skus"][sku] += row["skus"][sku]
    dealers_total["al"] = len(all_al)
    dealers_total["active"] = len(all_active)
    dealers_total["district"] = len(all_districts)
    dealers_total["commune"] = len(all_communes)
    dealers_total["avg_outlets_visit"] = (
        round(dealers_total["outlets_visit"] / dealers_total["active"], 1) if dealers_total["active"] > 0 else 0.0
    )

    return {
        "now": now.isoformat(),
        "from_date": start_day.isoformat(),
        "to_date": end_day.isoformat(),
        "dealer_count": len(dealers_out),
        "region_count": len(regions_out),
        "skus": list(REPORT_DAILY_SKUS),
        "regions": regions_out,
        "regions_total": regions_total,
        "dealers": dealers_out,
        "dealers_total": dealers_total,
    }


def build_daily_report_workbook(data: dict) -> bytes:
    """Export Report Daily to an Excel workbook matching report_SO_template with red data bars."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Sales Report"

    skus = data["skus"]
    total_cols = 12 + len(skus)  # A..L + 24 SKUs = 36 cols

    header_fill = PatternFill("solid", fgColor="14532D")
    sub_fill = PatternFill("solid", fgColor="E8F3EC")
    total_fill = PatternFill("solid", fgColor="F1F5F9")
    white_bold = Font(bold=True, color="FFFFFF", size=10)
    dark_bold = Font(bold=True, color="0F172A", size=10)
    title_font = Font(bold=True, color="14532D", size=12)
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    date_text = data["from_date"] if data["from_date"] == data["to_date"] else f"{data['from_date']} to {data['to_date']}"

    def write_section(start_row: int, title: str, rows: list[dict], total_row: dict, include_dealer: bool) -> int:
        ws.cell(start_row, 1, "Region: R1 to R8").font = dark_bold
        ws.cell(start_row + 1, 1, f"Date: {date_text}").font = dark_bold
        ws.cell(start_row + 2, 1, f"#Dealer: {data['dealer_count']}").font = dark_bold
        ws.cell(start_row + 3, 1, title).font = title_font

        h1 = start_row + 4
        h2 = start_row + 5
        h3 = start_row + 6

        ws.cell(h1, 1, "REGION")
        ws.merge_cells(start_row=h1, start_column=1, end_row=h3, end_column=1)
        ws.cell(h1, 2, "DEALER" if include_dealer else "")
        ws.merge_cells(start_row=h1, start_column=2, end_row=h3, end_column=2)

        ws.cell(h1, 3, "Sales Team")
        ws.merge_cells(start_row=h1, start_column=3, end_row=h2, end_column=8)

        ws.cell(h1, 9, "Location")
        ws.merge_cells(start_row=h1, start_column=9, end_row=h1, end_column=10)
        ws.cell(h2, 9, "Actual Visit Location")
        ws.merge_cells(start_row=h2, start_column=9, end_row=h2, end_column=10)

        ws.cell(h1, 11, "# Outlets Visit\nBased on Report")
        ws.merge_cells(start_row=h1, start_column=11, end_row=h3, end_column=11)
        ws.cell(h1, 12, "Average #\nof Outlets Visit")
        ws.merge_cells(start_row=h1, start_column=12, end_row=h3, end_column=12)

        ws.cell(h1, 13, "Execution Purchase outlet By SKU")
        ws.merge_cells(start_row=h1, start_column=13, end_row=h2, end_column=total_cols)

        sub_headers = ["SA", "SA WS", "Commando", "SA Leader", "AL", "Active", "District", "Commune"]
        for offset, label in enumerate(sub_headers, start=3):
            ws.cell(h3, offset, label)
        for offset, sku in enumerate(skus, start=13):
            ws.cell(h3, offset, sku)

        for r_idx in (h1, h2, h3):
            for c_idx in range(1, total_cols + 1):
                cell = ws.cell(r_idx, c_idx)
                cell.fill = header_fill if r_idx == h1 else sub_fill
                cell.font = white_bold if r_idx == h1 else dark_bold
                cell.alignment = center
                cell.border = border

        data_start = h3 + 1
        cur = data_start
        for item in rows:
            vals = [
                item["region"],
                item.get("dealer", "") if include_dealer else "",
                item["sa"], item["sa_ws"], item["commando"], item["sa_leader"], item["al"], item["active"],
                item["district"], item["commune"], item["outlets_visit"], item["avg_outlets_visit"],
                *[item["skus"].get(sku, 0) for sku in skus],
            ]
            for c_idx, val in enumerate(vals, start=1):
                cell = ws.cell(cur, c_idx, val)
                cell.border = border
                cell.alignment = center
            cur += 1

        data_end = cur - 1
        if data_end >= data_start:
            rule = DataBarRule(start_type="num", start_value=0, end_type="max", color="EF4444", showValue=True)
            for c_idx in range(3, total_cols + 1):
                col_ltr = get_column_letter(c_idx)
                ws.conditional_formatting.add(f"{col_ltr}{data_start}:{col_ltr}{data_end}", rule)

        tot_vals = [
            "Grand Total",
            "",
            total_row["sa"], total_row["sa_ws"], total_row["commando"], total_row["sa_leader"],
            total_row["al"], total_row["active"], total_row["district"], total_row["commune"],
            total_row["outlets_visit"], total_row["avg_outlets_visit"],
            *[total_row["skus"].get(sku, 0) for sku in skus],
        ]
        for c_idx, val in enumerate(tot_vals, start=1):
            cell = ws.cell(cur, c_idx, val)
            cell.fill = total_fill
            cell.font = dark_bold
            cell.border = border
            cell.alignment = center
        return cur + 2

    next_row = write_section(1, "Daily Sales Report", data["regions"], data["regions_total"], include_dealer=False)
    write_section(next_row, "Daily Sales Report by Dealer", data["dealers"], data["dealers_total"], include_dealer=True)

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 12
    for c_idx in range(3, 13):
        ws.column_dimensions[get_column_letter(c_idx)].width = 12
    for c_idx in range(13, total_cols + 1):
        ws.column_dimensions[get_column_letter(c_idx)].width = 16

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
