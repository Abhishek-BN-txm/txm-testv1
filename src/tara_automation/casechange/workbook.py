"""Write the case-change workbook template.

The template is generated from `schema.py`, so the sheet a user fills in and
the reader that parses it can never disagree. Every header carries a hover
comment with its help text, choice columns get dropdowns, and each sheet
carries one greyed EXAMPLE row that the reader skips.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from tara_automation.casechange.schema import (
    CHANGE_SHEETS, README_SHEET, SHEETS, Sheet,
)

FONT = "Arial"
HDR_FILL = PatternFill("solid", fgColor="1F4E78")
REQ_FILL = PatternFill("solid", fgColor="2E75B6")
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
TITLE = Font(name=FONT, bold=True, size=14)
SUB = Font(name=FONT, italic=True, size=9, color="555555")
BASE = Font(name=FONT, size=10)
EXAMPLE_FONT = Font(name=FONT, size=10, italic=True, color="808080")
EXAMPLE_FILL = PatternFill("solid", fgColor="F2F2F2")
SECTION = Font(name=FONT, bold=True, size=11, color="1F4E78")
_THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

EXAMPLE_PREFIX = "EXAMPLE: "


def _write_sheet(wb: Workbook, sh: Sheet) -> None:
    ws = wb.create_sheet(sh.name)
    for i, col in enumerate(sh.columns, start=1):
        cell = ws.cell(row=1, column=i, value=col.name)
        cell.fill = REQ_FILL if col.required else HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        cell.border = BORDER
        bits = [f"{col.name} — {'REQUIRED' if col.required else 'optional'}"]
        if col.default not in (None, ""):
            bits.append(f"Default if blank: {col.default}")
        if col.choices:
            bits.append("Allowed: " + ", ".join(str(c) for c in col.choices))
        if col.help:
            bits.append(col.help)
        cell.comment = Comment("\n\n".join(bits), "tara-automation")
        cell.comment.width = 320
        cell.comment.height = 160
        ws.column_dimensions[get_column_letter(i)].width = col.width

        if col.choices:
            dv = DataValidation(
                type="list",
                formula1='"' + ",".join(str(c) for c in col.choices) + '"',
                allow_blank=not col.required, showDropDown=False)
            dv.error = f"{col.name} must be one of: " + ", ".join(
                str(c) for c in col.choices)
            dv.errorTitle = "Not an allowed value"
            ws.add_data_validation(dv)
            dv.add(f"{get_column_letter(i)}2:{get_column_letter(i)}2000")

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    if sh.example:
        for i, val in enumerate(sh.example, start=1):
            col = sh.columns[i - 1]
            if col.name == "Comment":
                val = EXAMPLE_PREFIX + str(val)
            cell = ws.cell(row=2, column=i, value=val)
            cell.font = EXAMPLE_FONT
            cell.fill = EXAMPLE_FILL
            cell.border = BORDER

    ws.auto_filter.ref = (
        f"A1:{get_column_letter(len(sh.columns))}"
        f"{max(2, 1 + (1 if sh.example else 0))}")


def _write_readme(wb: Workbook) -> None:
    ws = wb.create_sheet(README_SHEET, 0)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 11
    ws.column_dimensions["C"].width = 9
    ws.column_dimensions["D"].width = 13
    ws.column_dimensions["E"].width = 96

    r = 1
    ws.cell(row=r, column=1, value="TARA case-change workbook").font = TITLE
    r += 1
    ws.cell(row=r, column=1, value=(
        "Describe the changes you want made to a base case. "
        "scripts/case_change.py turns this into one TARA INCH file per "
        "scenario, applies it, and writes out a new EPC/RAW case.")).font = SUB
    r += 2

    for text in (
        "How it works",
        "  1. List your scenarios on the Scenarios sheet — one row each, with a "
        "description.",
        "  2. Put your changes on the sheets below. Tag every row with the "
        "scenarios it belongs to in the Scenarios column: ALL, one ID, or a "
        "list like S1,S3.",
        "  3. One workbook drives every scenario. To add a scenario you add a "
        "row on Scenarios and tag existing change rows — you never copy the "
        "workbook.",
        "",
        "Filling it in",
        "  • Dark blue headers are REQUIRED. Medium blue are optional.",
        "  • Blank means 'use the default', never zero. Hover any header to "
        "see its default and help text.",
        "  • Grey italic rows are examples. They are ignored because their "
        "Comment starts with 'EXAMPLE:' — delete them, or edit the comment to "
        "make the row real.",
        "  • Leave BusNum blank on New_Buses and a number is assigned from the "
        "scenario's NewBusStart. Reference that bus anywhere else as @BusName.",
        "  • Only the cells you fill in are written to a MODIFY record, so "
        "re-rating a line does not disturb its impedance.",
        "",
        "Checking your work",
        "  python scripts/case_change.py validate --workbook <this file>",
        "  reports every problem at once — bad references, missing values, "
        "duplicate buses — before TARA ever sees the file.",
    ):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = SECTION if text and not text.startswith((" ", "•")) \
            else BASE
        r += 1
    r += 1

    for sh in SHEETS:
        ws.cell(row=r, column=1, value=sh.name).font = SECTION
        ws.cell(row=r, column=2, value=sh.purpose).font = BASE
        r += 1
        for i, head in enumerate(("Column", "Required", "Type", "Default",
                                  "What it means"), start=1):
            c = ws.cell(row=r, column=i, value=head)
            c.fill = HDR_FILL
            c.font = HDR_FONT
            c.border = BORDER
        r += 1
        for col in sh.columns:
            ws.cell(row=r, column=1, value=col.name).font = BASE
            ws.cell(row=r, column=2,
                    value="yes" if col.required else "").font = BASE
            ws.cell(row=r, column=3, value=col.kind).font = BASE
            ws.cell(row=r, column=4,
                    value="" if col.default in (None, "") else str(col.default)
                    ).font = BASE
            help_text = col.help
            if col.choices:
                help_text = ("One of: " + ", ".join(str(c) for c in col.choices)
                             + (". " + help_text if help_text else ""))
            hc = ws.cell(row=r, column=5, value=help_text)
            hc.font = BASE
            hc.alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
        for note in sh.notes:
            n = ws.cell(row=r, column=1, value="Note: " + note)
            n.font = SUB
            n.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=1, end_row=r,
                           end_column=5)
            r += 1
        r += 1

    ws.freeze_panes = "A2"


def write_template(path: Path) -> Path:
    """Write a blank, documented case-change workbook."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    for sh in SHEETS:
        _write_sheet(wb, sh)
    _write_readme(wb)
    wb.active = 0
    wb.save(path)
    return path


def append_rows(path: Path, sheet_name: str, rows: list[dict]) -> None:
    """Append rows (column name -> value) to a sheet of an existing workbook.

    Used to build worked examples and test fixtures from the same template the
    user gets, so a fixture can never encode a column layout the template does
    not actually have.
    """
    from openpyxl import load_workbook

    from tara_automation.casechange.schema import sheet as get_sheet

    wb = load_workbook(path)
    ws = wb[sheet_name]
    sh = get_sheet(sheet_name)
    order = [c.name for c in sh.columns]
    for row in rows:
        unknown = set(row) - set(order)
        if unknown:
            raise KeyError(f"{sheet_name}: no such column(s): "
                           f"{', '.join(sorted(unknown))}")
        cells = [row.get(name) for name in order]
        ws.append(cells)
        for i in range(1, len(order) + 1):
            c = ws.cell(row=ws.max_row, column=i)
            c.font = BASE
            c.border = BORDER
    wb.save(path)


def sheet_summary() -> str:
    """One-line-per-sheet description, for CLI output."""
    lines = [f"  {'Scenarios':<18} one row per scenario (IDs, descriptions, "
             f"output case)"]
    for sh in CHANGE_SHEETS:
        lines.append(f"  {sh.name:<18} {sh.purpose}")
    return "\n".join(lines)
