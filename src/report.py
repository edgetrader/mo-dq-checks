"""
The Excel report.

Three sheets, in the order someone actually reads them:

  Summary   what happened, at a glance -- verdict banner, run details,
            totals, and a per-check breakdown showing which checks are
            failing across how many tables
  Results   the full matrix: one row per table, one column per check.
            Check headers are rotated so the grid stays narrow enough to
            scan without scrolling
  Failures  just the problems, as a flat list -- the sheet you work from

Cell values in the matrix are still the plain strings "PASS"/"FAIL", not
symbols, so the sheet stays filterable and machine-readable; the visual
weight comes from colour. Icons are used only where they can't be mistaken
for data (the verdict banner and the row-status column).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from checks import CHECK_NAMES, CheckResult

# --- palette ---------------------------------------------------------------
NAVY = "1F4E79"
SLATE = "595959"
RULE = "D9D9D9"

PASS_BG, PASS_FG = "C6EFCE", "006100"
FAIL_BG, FAIL_FG = "FFC7CE", "9C0006"
SKIP_BG, SKIP_FG = "F2F2F2", "BFBFBF"
BAND_BG = "F7F9FC"

TAB_OK, TAB_BAD = "00B050", "C00000"

PASS_ICON, FAIL_ICON, SKIP_TEXT = "✔", "✖", "–"

# --- reusable styles -------------------------------------------------------
TITLE_FONT = Font(name="Calibri", size=18, bold=True, color=NAVY)
SUBTITLE_FONT = Font(size=10, italic=True, color=SLATE)
SECTION_FONT = Font(size=11, bold=True, color="FFFFFF")
SECTION_FILL = PatternFill("solid", fgColor=NAVY)
LABEL_FONT = Font(size=10, bold=True, color=SLATE)
VALUE_FONT = Font(size=10)
HEADER_FONT = Font(size=10, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor=NAVY)

CENTER = Alignment(horizontal="center", vertical="center")
LEFT_TOP = Alignment(horizontal="left", vertical="top", wrap_text=True)
ROTATED = Alignment(textRotation=90, horizontal="center", vertical="bottom", wrap_text=False)

THIN = Side(style="thin", color=RULE)
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

CHECK_COL_WIDTH = 9
COMMENTS_WIDTH = 62
PATH_WIDTH = 58


@dataclass
class ReportData:
    """Everything the sheets need, derived once from the raw results."""

    yyyymm: str
    data_root: str
    run_time: datetime
    tables: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    by_table: dict[str, dict[str, CheckResult]] = field(default_factory=dict)
    file_paths: dict[str, str] = field(default_factory=dict)

    @property
    def all_results(self) -> list[CheckResult]:
        return [r for table in self.tables for r in self.by_table[table].values()]

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.all_results if r.status == "FAIL"]

    @property
    def total(self) -> int:
        return len(self.all_results)

    @property
    def passed(self) -> int:
        return self.total - len(self.failures)

    @property
    def failing_tables(self) -> list[str]:
        return [t for t in self.tables if any(r.status == "FAIL" for r in self.by_table[t].values())]

    @property
    def ok(self) -> bool:
        return not self.failures

    def status_of(self, table: str, check: str) -> str | None:
        result = self.by_table[table].get(check)
        return result.status if result else None

    def comments_for(self, table: str) -> str:
        results = self.by_table[table]
        return "\n".join(
            f"{check}: {results[check].message}" for check in self.checks
            if check in results and results[check].message
        )

    def check_stats(self, check: str) -> tuple[int, int]:
        """(tables the check ran on, tables it failed on)."""
        ran = sum(1 for t in self.tables if check in self.by_table[t])
        failed = sum(1 for t in self.tables if self.status_of(t, check) == "FAIL")
        return ran, failed


def organise(yyyymm: str, data_root: str, results: list[CheckResult], run_time: datetime) -> ReportData:
    data = ReportData(yyyymm=yyyymm, data_root=data_root, run_time=run_time)

    for result in results:
        if result.table_name not in data.by_table:
            data.by_table[result.table_name] = {}
            data.tables.append(result.table_name)
        data.by_table[result.table_name][result.check_name] = result

    emitted = {r.check_name for r in results}
    data.checks = [c for c in CHECK_NAMES if c in emitted]
    # Anything emitted that CHECK_NAMES doesn't know about still gets a
    # column rather than vanishing (the test suite treats that as a failure,
    # but a live run must never silently drop a result).
    data.checks += sorted(emitted - set(data.checks))

    # The file path is carried in the file_exists message. read_table now
    # exposes it properly as LoadedTable.path, but run_checks_for_table
    # returns only results, so it's still recovered here.
    for table in data.tables:
        result = data.by_table[table].get("file_exists")
        if result:
            prefix = "File not found: "
            message = result.message
            data.file_paths[table] = (
                message[len(prefix):] if message.startswith(prefix) else message
            )

    return data


def write_excel_report(
    yyyymm: str,
    data_root: str,
    results: list[CheckResult],
    output_dir: str,
    run_time: datetime,
) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dq_check_report_{yyyymm}_{run_time:%Y%m%d_%H%M%S}.xlsx"

    data = organise(yyyymm, str(Path(data_root).resolve()), results, run_time)

    workbook = Workbook()
    _summary_sheet(workbook.active, data)
    _results_sheet(workbook.create_sheet("Results"), data)
    _failures_sheet(workbook.create_sheet("Failures"), data)
    workbook.save(path)

    return path


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def _summary_sheet(ws, data: ReportData) -> None:
    ws.title = "Summary"
    ws.sheet_properties.tabColor = TAB_OK if data.ok else TAB_BAD
    ws.sheet_view.showGridLines = False

    for column, width in zip("ABCD", (26, 34, 12, 14)):
        ws.column_dimensions[column].width = width

    ws["A1"] = "DATA QUALITY REPORT"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:D1")
    ws["A2"] = "Monthly Excel table exports"
    ws["A2"].font = SUBTITLE_FONT
    ws.merge_cells("A2:D2")
    ws.row_dimensions[1].height = 26

    # Verdict banner -- the one thing to see on opening the file.
    if data.ok:
        verdict = f"{PASS_ICON}   ALL {data.total} CHECKS PASSED"
        background, foreground = PASS_BG, PASS_FG
    else:
        verdict = (
            f"{FAIL_ICON}   {len(data.failures)} OF {data.total} CHECKS FAILED "
            f"ACROSS {len(data.failing_tables)} TABLE(S)"
        )
        background, foreground = FAIL_BG, FAIL_FG

    ws["A4"] = verdict
    ws["A4"].font = Font(size=13, bold=True, color=foreground)
    ws["A4"].fill = PatternFill("solid", fgColor=background)
    ws["A4"].alignment = CENTER
    ws.merge_cells("A4:D4")
    ws.row_dimensions[4].height = 30

    row = _section(ws, 6, "RUN DETAILS")
    for label, value in (
        ("Reporting month", data.yyyymm),
        ("Data root", data.data_root),
        ("Run at", data.run_time.strftime("%Y-%m-%d %H:%M:%S")),
        ("Tables checked", len(data.tables)),
    ):
        row = _label_value(ws, row, label, value)

    row = _section(ws, row + 1, "CHECK TOTALS")
    row = _label_value(ws, row, "Checks run", data.total)
    row = _label_value(ws, row, "Passed", data.passed, colour=PASS_FG)
    row = _label_value(ws, row, "Failed", len(data.failures), colour=FAIL_FG if data.failures else None)
    row = _label_value(ws, row, "Tables with failures", len(data.failing_tables))

    _check_breakdown(ws, row + 1, data)


def _section(ws, row: int, title: str) -> int:
    cell = ws.cell(row=row, column=1, value=title)
    cell.font = SECTION_FONT
    cell.fill = SECTION_FILL
    for column in range(2, 5):
        ws.cell(row=row, column=column).fill = SECTION_FILL
    ws.row_dimensions[row].height = 18
    return row + 1


def _label_value(ws, row: int, label: str, value, colour: str | None = None) -> int:
    ws.cell(row=row, column=1, value=label).font = LABEL_FONT
    cell = ws.cell(row=row, column=2, value=value)
    cell.font = Font(size=10, bold=colour is not None, color=colour) if colour else VALUE_FONT
    return row + 1


def _check_breakdown(ws, start: int, data: ReportData) -> None:
    """Which checks are failing, and how widely -- spots systemic problems."""
    _section(ws, start, "BY CHECK")

    note = ws.cell(
        row=start + 1,
        column=1,
        value=f"Clean rate is over all {len(data.tables)} tables checked.",
    )
    note.font = SUBTITLE_FONT
    ws.merge_cells(start_row=start + 1, start_column=1, end_row=start + 1, end_column=4)

    header_row = start + 2
    for column, title in enumerate(("Check", "Tables failed", "Clean rate"), start=1):
        cell = ws.cell(row=header_row, column=column, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = BOX

    # Deliberately measured against every table checked, not against the
    # tables the check emitted a result for. Some checks only report when
    # they fail (analytics_completeness), so an "of those that ran" rate
    # would show them as 0% clean whenever they appear at all.
    total_tables = len(data.tables) or 1

    row = header_row + 1
    for check in data.checks:
        _ran, failed = data.check_stats(check)
        ws.cell(row=row, column=1, value=check).font = VALUE_FONT
        cell = ws.cell(row=row, column=2, value=failed)
        cell.alignment = CENTER
        if failed:
            cell.font = Font(size=10, bold=True, color=FAIL_FG)
            cell.fill = PatternFill("solid", fgColor=FAIL_BG)
        rate = ws.cell(row=row, column=3, value=(total_tables - failed) / total_tables)
        rate.number_format = "0%"
        rate.alignment = CENTER
        for column in range(1, 4):
            ws.cell(row=row, column=column).border = BOX
        row += 1

    if row > header_row + 1:
        ws.conditional_formatting.add(
            f"C{header_row + 1}:C{row - 1}",
            DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1, color="63C384"),
        )


# --------------------------------------------------------------------------
# Results matrix
# --------------------------------------------------------------------------


def _results_sheet(ws, data: ReportData) -> None:
    ws.sheet_properties.tabColor = TAB_OK if data.ok else TAB_BAD
    ws.sheet_view.showGridLines = False

    headers = ["", "Table", *data.checks, "Comments", "File"]
    for column, title in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=column, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BOX
        # Rotate the check headers so their columns can stay narrow; the
        # whole matrix then fits on screen without horizontal scrolling.
        cell.alignment = ROTATED if 2 < column <= len(data.checks) + 2 else CENTER
    ws.row_dimensions[1].height = 135

    for offset, table in enumerate(data.tables):
        row = offset + 2
        table_ok = table not in data.failing_tables
        banded = PatternFill("solid", fgColor=BAND_BG) if offset % 2 else None

        icon = ws.cell(row=row, column=1, value=PASS_ICON if table_ok else FAIL_ICON)
        icon.font = Font(size=12, bold=True, color=PASS_FG if table_ok else FAIL_FG)
        icon.alignment = CENTER

        name = ws.cell(row=row, column=2, value=table)
        name.font = Font(size=10, bold=not table_ok)
        if banded:
            name.fill = banded

        for index, check in enumerate(data.checks):
            column = index + 3
            status = data.status_of(table, check)
            cell = ws.cell(row=row, column=column, value=status or SKIP_TEXT)
            cell.alignment = CENTER
            cell.border = BOX
            if status == "PASS":
                cell.fill = PatternFill("solid", fgColor=PASS_BG)
                cell.font = Font(size=9, color=PASS_FG)
            elif status == "FAIL":
                cell.fill = PatternFill("solid", fgColor=FAIL_BG)
                cell.font = Font(size=9, bold=True, color=FAIL_FG)
            else:
                # Never ran: not applicable to this table, or an earlier
                # structural failure stopped the chain.
                cell.fill = PatternFill("solid", fgColor=SKIP_BG)
                cell.font = Font(size=9, color=SKIP_FG)

        comments_col = len(data.checks) + 3
        comment = ws.cell(row=row, column=comments_col, value=data.comments_for(table))
        comment.alignment = LEFT_TOP
        comment.font = Font(size=9)
        if banded:
            comment.fill = banded

        path = ws.cell(row=row, column=comments_col + 1, value=data.file_paths.get(table, ""))
        path.alignment = Alignment(horizontal="left", vertical="top")
        path.font = Font(size=8, color=SLATE)
        if banded:
            path.fill = banded

    last_column = len(headers)
    last_row = len(data.tables) + 1
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 26
    for index in range(len(data.checks)):
        ws.column_dimensions[get_column_letter(index + 3)].width = CHECK_COL_WIDTH
    ws.column_dimensions[get_column_letter(len(data.checks) + 3)].width = COMMENTS_WIDTH
    ws.column_dimensions[get_column_letter(len(data.checks) + 4)].width = PATH_WIDTH

    ws.freeze_panes = "C2"  # keep status + table name visible when scrolling
    ws.auto_filter.ref = f"A1:{get_column_letter(last_column)}{max(last_row, 1)}"

    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:1"


# --------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------


def _failures_sheet(ws, data: ReportData) -> None:
    ws.sheet_properties.tabColor = TAB_OK if data.ok else TAB_BAD
    ws.sheet_view.showGridLines = False

    for column, (title, width) in enumerate(
        (("Table", 26), ("Check", 26), ("What went wrong", 110)), start=1
    ):
        cell = ws.cell(row=1, column=column, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = BOX
        ws.column_dimensions[get_column_letter(column)].width = width

    if data.ok:
        cell = ws.cell(row=2, column=1, value=f"{PASS_ICON}  No failures — every check passed.")
        cell.font = Font(size=11, bold=True, color=PASS_FG)
        cell.fill = PatternFill("solid", fgColor=PASS_BG)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=3)
        ws.row_dimensions[2].height = 24
        return

    row = 2
    for table in data.failing_tables:
        for check in data.checks:
            result = data.by_table[table].get(check)
            if not result or result.status != "FAIL":
                continue
            ws.cell(row=row, column=1, value=table).font = Font(size=10, bold=True)
            ws.cell(row=row, column=2, value=check).font = Font(size=10, color=FAIL_FG)
            detail = ws.cell(row=row, column=3, value=result.message)
            detail.alignment = LEFT_TOP
            detail.font = Font(size=9)
            for column in range(1, 4):
                ws.cell(row=row, column=column).border = BOX
            row += 1

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:C{row - 1}"
