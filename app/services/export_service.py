# app/services/export_service.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math
import re
from pathlib import Path
import tempfile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.data.controllers import controllers_for_group, controllers_for_region, region_from_group
from app.data.sa_members import members_for_group, normalize_username
from app.database.models import MissingSalePhotoAlert, PinHistory, SalePhotoReport, SAList, SAUser
from app.services.user_service import looks_like_sale_form, normalize_name
from app.core.config import get_settings
from app.services.voice_quality import clean_voice_export_text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "Template_SA_Missing.xlsx"


def _completed_submission_at(
    db: Session,
    *,
    group_id: int,
    telegram_user_id: int,
    range_start: datetime,
    range_end: datetime,
    include_start: bool,
) -> datetime | None:
    """Return the earliest complete form/photo + GPS time in one range.

    A report with reply coordinates is already an atomic form+GPS submission.
    Otherwise the form/photo and pin must both belong to the same Telegram ID
    and group.  The returned time is when the later required component arrived.
    """
    report_time_column = SalePhotoReport.submitted_at
    pin_time_column = func.coalesce(PinHistory.event_at, PinHistory.pinned_at)
    report_start_filter = (
        report_time_column >= range_start
        if include_start
        else report_time_column > range_start
    )
    pin_start_filter = (
        pin_time_column >= range_start
        if include_start
        else pin_time_column > range_start
    )

    reports = db.query(SalePhotoReport).filter(
        SalePhotoReport.group_id == group_id,
        SalePhotoReport.telegram_user_id == telegram_user_id,
        report_start_filter,
        report_time_column <= range_end,
    ).order_by(report_time_column.asc()).all()
    if not reports:
        return None

    atomic_times = [
        report.submitted_at
        for report in reports
        if report.reply_lat is not None and report.reply_lon is not None
    ]

    pins = db.query(PinHistory).filter(
        PinHistory.group_id == group_id,
        PinHistory.telegram_user_id == telegram_user_id,
        pin_start_filter,
        pin_time_column <= range_end,
    ).order_by(pin_time_column.asc()).all()

    completion_times = list(atomic_times)
    if pins:
        # The earliest form and earliest pin in this exact interval are enough;
        # identity and group filters prevent cross-user/cross-group matching.
        completion_times.append(max(reports[0].submitted_at, pins[0].event_at or pins[0].pinned_at))
    return min(completion_times) if completion_times else None


def _display_position(raw: str | None) -> str:
    text = (raw or "SA").strip()
    key = text.lower().replace("_", " ").replace("-", " ")
    if "team leader" in key or "leader" in key:
        return "SA Team Leader"
    if "commando" in key or "cammondo" in key:
        return "Commando"
    return "SA"


def _clean_reason(raw: str | None) -> str:
    """Remove routing text and suppress outlet forms from old/new exports."""
    text = re.sub(r"(?<!\w)@[A-Za-z0-9_]{5,32}\b", " ", (raw or "").strip())
    text = " ".join(text.split()).strip(" |,-")
    # Defense in depth for rows already saved by an older build: re-exporting
    # the affected date must immediately produce a clean Reason column without
    # requiring a destructive database cleanup.
    return "" if looks_like_sale_form(text) else text


def _clean_voice_text(raw: str | None) -> str:
    return clean_voice_export_text(_clean_reason(raw))


@dataclass(frozen=True)
class MissingExportRow:
    region: str
    dealer: str
    name: str
    position: str
    missing_date: date
    time_submit: str
    time_late: str
    missing_count: int
    reason: str
    voice_to_text: str


def _official_member_metadata(
    group_name: str | None,
    name: str | None,
    username: str | None,
) -> dict | None:
    name_key = normalize_name(name)
    username_key = normalize_username(username)
    for member in members_for_group(group_name):
        if username_key and normalize_username(member.get("username")) == username_key:
            return member
        if name_key and normalize_name(member.get("name")) == name_key:
            return member
    return None


def build_missing_export_rows(
    db: Session,
    regions: tuple[str, ...],
    target_date: date,
) -> list[MissingExportRow]:
    """Build one Excel row for every missing alert window.

    Example: if the same SA misses 06:00-08:20 and 08:20-08:43,
    the export contains two rows. Each row keeps its own alert window,
    reason, and voice transcription.
    """
    start = datetime.combine(target_date, time.min)
    end = start + timedelta(days=1)
    alerts = (
        db.query(MissingSalePhotoAlert)
        .filter(
            MissingSalePhotoAlert.window_end >= start,
            MissingSalePhotoAlert.window_end < end,
        )
        .order_by(
            MissingSalePhotoAlert.window_end.asc(),
            MissingSalePhotoAlert.id.asc(),
        )
        .all()
    )

    group_ids = {alert.group_id for alert in alerts}
    users = (
        db.query(SAUser).filter(SAUser.group_id.in_(group_ids)).all()
        if group_ids
        else []
    )
    user_by_id = {(user.group_id, user.telegram_user_id): user for user in users}
    user_by_name = {(user.group_id, normalize_name(user.name)): user for user in users}
    user_by_username = {
        (user.group_id, normalize_username(user.username)): user
        for user in users
        if normalize_username(user.username)
    }

    rows: list[MissingExportRow] = []

    for alert in alerts:
        user = user_by_id.get((alert.group_id, alert.telegram_user_id))
        if user is None and normalize_username(alert.username):
            user = user_by_username.get(
                (alert.group_id, normalize_username(alert.username))
            )
        if user is None:
            user = user_by_name.get((alert.group_id, normalize_name(alert.name)))

        official = _official_member_metadata(
            alert.group_name,
            alert.name,
            alert.username,
        )
        region = (
            alert.region
            or (user.region if user else None)
            or (official.get("region") if official else None)
            or region_from_group(alert.group_name)
            or ""
        ).upper()
        if region not in regions:
            continue

        dealer = (
            alert.dealer
            or (user.dealer if user else None)
            or (official.get("dealer") if official else None)
            or ""
        )
        position = _display_position(
            alert.position
            or (user.position if user else None)
            or (official.get("position") if official else None)
            or "SA"
        )
        name = alert.name or (user.name if user else "Unknown")
        window_end = alert.window_end or alert.missed_at
        window_start = alert.window_start
        missing_date = window_end.date()

        if window_start:
            time_submit = (
                f"{window_start.strftime('%I:%M %p')} - "
                f"{window_end.strftime('%I:%M %p')}"
            )
        else:
            time_submit = window_end.strftime('%I:%M %p')

        # Defensive reconciliation: a complete submission inside the original
        # window must never remain in a missing export, even if an alert row was
        # created during a scheduler/update race.
        if window_start and _completed_submission_at(
            db,
            group_id=alert.group_id,
            telegram_user_id=alert.telegram_user_id,
            range_start=window_start,
            range_end=window_end,
            include_start=True,
        ) is not None:
            continue

        # Late time belongs only to the immediately following alert interval.
        # It can therefore never exceed ALERT_INTERVAL_MINUTES. If the user
        # still has no complete form/photo + GPS by that deadline, record the
        # full 20 minutes rather than borrowing a much later submission.
        interval_minutes = max(1, get_settings().ALERT_INTERVAL_MINUTES)
        grace_end = window_end + timedelta(minutes=interval_minutes)
        completed_at = _completed_submission_at(
            db,
            group_id=alert.group_id,
            telegram_user_id=alert.telegram_user_id,
            range_start=window_end,
            range_end=grace_end,
            include_start=False,
        )
        if completed_at is None:
            late_minutes = interval_minutes
        else:
            late_minutes = min(
                interval_minutes,
                max(1, math.ceil((completed_at - window_end).total_seconds() / 60)),
            )

        rows.append(
            MissingExportRow(
                region=region,
                dealer=dealer,
                name=name,
                position=position,
                missing_date=missing_date,
                time_submit=time_submit,
                time_late=f"{late_minutes} min",
                missing_count=1,
                reason=_clean_reason(alert.reason_text),
                voice_to_text=_clean_voice_text(alert.voice_to_text),
            )
        )

    return sorted(
        rows,
        key=lambda row: (
            row.region,
            row.dealer,
            row.name.casefold(),
            row.position,
            row.time_submit,
        ),
    )


def create_missing_export_workbook(
    rows: list[MissingExportRow],
    regions: tuple[str, ...],
    target_date: date,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
) -> Path:
    if not template_path.exists():
        raise FileNotFoundError(f"Export template not found: {template_path}")

    wb = load_workbook(template_path)
    ws = wb["Data"]

    # Preserve header and table styling, remove prior sample/output rows.
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)

    for index, row in enumerate(rows, start=1):
        excel_row = index + 1
        values = [
            index,
            row.region,
            row.dealer,
            row.name,
            row.position,
            row.missing_date,
            row.time_submit,
            row.time_late,
            f"{row.missing_count} ដង",
            row.reason,
            row.voice_to_text,
        ]
        for column, value in enumerate(values, start=1):
            cell = ws.cell(row=excel_row, column=column, value=value)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if column in {10, 11}:
                cell.font = Font(name="Khmer UI", size=11)
        ws.cell(row=excel_row, column=6).number_format = "dd/mm/yyyy"
        ws.cell(row=excel_row, column=7).number_format = "@"
        ws.cell(row=excel_row, column=8).number_format = "@"
        ws.row_dimensions[excel_row].height = 34

    final_row = max(2, len(rows) + 1)

    # Recreate the Excel table instead of only changing its ref.
    # The previous template contained non-sequential table-column IDs
    # (for example 1,2,3,4,5,6,10,7,9,8). Microsoft Excel treats that
    # table XML as unreadable and removes it during workbook repair.
    for table_name in list(ws.tables.keys()):
        del ws.tables[table_name]

    export_table = Table(
        displayName="MissingDataTable",
        ref=f"A1:K{final_row}",
    )
    export_table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium7",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(export_table)

    ws.freeze_panes = "A2"
    # The table already supplies filter buttons; do not create a second
    # worksheet-level AutoFilter over the same range.
    ws.auto_filter.ref = None
    widths = {
        "A": 8,
        "B": 10,
        "C": 12,
        "D": 34,
        "E": 22,
        "F": 14,
        "G": 24,
        "H": 14,
        "I": 12,
        "J": 38,
        "K": 48,
    }
    for column, width in widths.items():
        ws.column_dimensions[column].width = width

    # Ensure the template header remains correct even when an older template
    # without Time Late is deployed.
    ws.delete_rows(1, 1)
    ws.insert_rows(1, 1)
    headers = [
        "No", "Region", "Dealer", "SA Name", "Position", "Date",
        "Time Submit", "Time Late", "Missing", "Reason", "Voice To Text",
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="70AD47")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    def reason_text(row: MissingExportRow) -> str:
        return row.reason or row.voice_to_text

    summary = defaultdict(lambda: {"missing": 0, "with_reason": 0, "reasons": []})
    for row in rows:
        key = (row.region, row.dealer, row.name, row.position)
        summary[key]["missing"] += row.missing_count
        reason = reason_text(row)
        if reason:
            summary[key]["with_reason"] += 1
            if reason not in summary[key]["reasons"]:
                summary[key]["reasons"].append(reason)

    sa_data = []
    for (region, dealer, name, position), totals in sorted(summary.items()):
        sa_data.append([
            region, dealer, name, position, totals["missing"],
            totals["with_reason"], totals["missing"] - totals["with_reason"],
            " | ".join(totals["reasons"]),
        ])

    # Exactly two sheets are exported: Data and Summary. Summary contains only
    # the requested SA-level totals; region/dealer-only sections are omitted.
    summary_ws = wb["Summary"] if "Summary" in wb.sheetnames else wb.create_sheet("Summary")
    if summary_ws.max_row:
        summary_ws.delete_rows(1, summary_ws.max_row)
    for sheet_name in list(wb.sheetnames):
        if sheet_name not in {"Data", "Summary"}:
            del wb[sheet_name]

    def add_section(title: str, section_headers: list[str], section_rows: list[list], start_row: int) -> int:
        last_col = len(section_headers)
        summary_ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=last_col)
        title_cell = summary_ws.cell(start_row, 1, title)
        title_cell.font = Font(color="FFFFFF", bold=True, size=13)
        title_cell.fill = PatternFill("solid", fgColor="1F4E78")
        title_cell.alignment = Alignment(horizontal="left", vertical="center")
        summary_ws.row_dimensions[start_row].height = 24

        header_row = start_row + 1
        for col, header in enumerate(section_headers, 1):
            cell = summary_ws.cell(header_row, col, header)
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = PatternFill("solid", fgColor="4472C4")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for offset, values in enumerate(section_rows, start=1):
            target_row = header_row + offset
            for col, value in enumerate(values, 1):
                cell = summary_ws.cell(target_row, col, value)
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if col == last_col:
                    cell.font = Font(name="Khmer UI", size=11)
            if offset % 2 == 1:
                for col in range(1, last_col + 1):
                    summary_ws.cell(target_row, col).fill = PatternFill("solid", fgColor="D9EAF7")
        return header_row + max(1, len(section_rows)) + 2

    add_section(
        "Summary By SA Name",
        ["Region", "Dealer", "SA Name", "Position", "Total Missing", "With Reason", "Without Reason", "All Reason"],
        sa_data,
        1,
    )
    summary_ws.freeze_panes = "A3"
    summary_ws.sheet_view.showGridLines = False
    summary_widths = {"A": 12, "B": 14, "C": 34, "D": 22, "E": 16, "F": 16, "G": 18, "H": 55}
    for column, width in summary_widths.items():
        summary_ws.column_dimensions[column].width = width

    region_slug = "_".join(regions)
    output_dir = Path(tempfile.mkdtemp(prefix="sa_missing_export_"))
    output_path = (
        output_dir / f"SA_Missing_{region_slug}_{target_date.isoformat()}.xlsx"
    )
    wb.save(output_path)
    return output_path


def create_sa_list_workbook(db: Session) -> Path:
    """Export the editable SA_List source, including inactive/removed rows."""
    rows = (
        db.query(SAList)
        .filter(SAList.is_deleted == 0)
        .order_by(SAList.region, SAList.dealer, SAList.name)
        .all()
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "SA_List"
    headers = [
        "No", "Telegram ID", "SA Name", "Username", "Region", "Dealer",
        "Position", "Working Time", "Group", "Controller", "Status",
        "Profile Source", "Last Profile Update", "Employee ID",
    ]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for index, row in enumerate(rows, 1):
        assigned = controllers_for_group(row.group_name) or controllers_for_region(row.region)
        controller = ", ".join(c.name for c in assigned) or row.controller or ""
        ws.append([
            index, row.telegram_user_id or "", row.name,
            f"@{row.username}" if row.username else "",
            row.region, row.dealer, row.position, row.working_time,
            row.group_name, controller,
            "Active" if row.is_active == 1 else "Removed",
            "Telegram ID" if row.telegram_user_id else "Awaiting Telegram binding",
            row.updated_at,
            row.employee_id or "",
        ])
    widths = [8, 18, 38, 24, 10, 12, 24, 20, 34, 34, 12, 26, 22, 16]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, idx).column_letter].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:N{max(1, ws.max_row)}"
    output_dir = Path(tempfile.mkdtemp(prefix="sa_list_export_"))
    output_path = output_dir / f"SA_List_{datetime.now().date().isoformat()}.xlsx"
    wb.save(output_path)
    return output_path
