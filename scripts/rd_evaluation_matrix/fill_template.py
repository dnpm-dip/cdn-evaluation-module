#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["openpyxl"]
# ///
"""
Write the Auswertematrix (xlsx) from the result JSON of rd_evaluation_matrix.py.

Template layout (sheets "Anzahl" and "Anteil %" with the same rows):
  B1 title, B2 evaluation period, B3 note on suppressed cells
  row 5 header: A key (hidden), B Abschnitt / Kategorie, C Einheit or Bezug, D Gesamt,
                E onwards one column per Leistungserbringer (filled in from the result)
  from row 6: a header row per section, then one row per value, identified by the key in column A
Further sheets: "Rückfragen an LE" (cases to clarify, with case keys: only for the respective LE),
"Hinweise" (notes), and, written on filling, "Gründe je Monat": the reasons for a missing broad consent per month
of the initial submission and LE (rows that depend on the months of the data, so they aren't template rows).

The rows are matched by key, so the template may be restyled and reordered, and rows may be
removed; rows of the result missing in the template are refused.

Examples:
  uv run fill_template.py matrix.json Auswertmatrix_Template.xlsx --output matrix.xlsx
  uv run fill_template.py matrix.json Auswertmatrix_Template.xlsx --new-template   # (re)create the template
"""

import argparse
import json
from copy import copy

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

COUNTS, PERCENTS, PROBLEMS, NOTES = "Anzahl", "Anteil %", "Rückfragen an LE", "Hinweise"
REASONS_BY_MONTH = "Gründe je Monat"
MONTHLY_SECTION = "Broad Consent nicht übermittelt: Grund laut DIP, nach Monat der Erstmeldung"


def template_result_rows(result):
    """The rows of the result that are template rows: all but those by month"""
    return [r for r in result["rows"] if r["section"] != MONTHLY_SECTION]
HEADER_ROW, FIRST_ROW = 5, 6
KEY_COL, LABEL_COL, UNIT_COL, TOTAL_COL = 1, 2, 3, 4
FIRST_LE_COL = 5

THIN = Side(style="thin", color="BFBFBF")
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
SECTION_FILL = PatternFill("solid", fgColor="DDEBF7")


# ---------------------------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------------------------

def new_template(result, path):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name in (COUNTS, PERCENTS):
        ws = wb.create_sheet(name)
        ws["B1"] = "AUSWERTUNG <Zeitraum>"
        ws["B1"].font = Font(bold=True, size=14)
        ws["B2"] = "Auswertungszeitraum: <Zeitraum>"
        ws["B3"] = "<Hinweis zu unterdrückten Zellen>"
        ws["B3"].font = Font(italic=True, color="595959")
        header = ["Schlüssel", "Abschnitt / Kategorie", "Einheit" if name == COUNTS else "Anteil von", "Gesamt",
                  "<Leistungserbringer>"]
        for col, text in enumerate(header, 1):
            cell = ws.cell(HEADER_ROW, col, text)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center" if col >= TOTAL_COL else "left", vertical="center", wrap_text=True)
            cell.border = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)

        row, section = FIRST_ROW, None
        for r in template_result_rows(result):
            if r["section"] != section:
                section = r["section"]
                if row > FIRST_ROW:
                    row += 1
                for col in range(LABEL_COL, FIRST_LE_COL + 1):
                    ws.cell(row, col).fill = SECTION_FILL
                ws.cell(row, LABEL_COL, section).font = Font(bold=True)
                row += 1
            ws.cell(row, KEY_COL, r["key"])
            ws.cell(row, LABEL_COL, r["item"]).alignment = Alignment(indent=1)
            if name == COUNTS:
                ws.cell(row, UNIT_COL, r["unit"])
            else:
                ws.cell(row, UNIT_COL, r["base_label"] or "")
            for col in (TOTAL_COL, FIRST_LE_COL):
                cell = ws.cell(row, col)
                cell.border = Border(bottom=THIN, left=THIN, right=THIN)
                cell.alignment = Alignment(horizontal="right")
                cell.number_format = "0.0" if name == PERCENTS or r["unit"] != "Anzahl" else "0"
            row += 1

        ws.column_dimensions["A"].hidden = True
        ws.column_dimensions["B"].width = 70
        ws.column_dimensions["C"].width = 30
        ws.column_dimensions["D"].width = 12
        ws.column_dimensions["E"].width = 12
        ws.freeze_panes = ws.cell(FIRST_ROW, TOTAL_COL)

    ws = wb.create_sheet(PROBLEMS)
    for col, (text, width) in enumerate((("LE", 20), ("Problem", 45), ("Fall", 45), ("Einreichung (tanC)", 25),
                                         ("Detail", 100)), 1):
        cell = ws.cell(1, col, text)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"

    ws = wb.create_sheet(NOTES)
    ws["A1"] = "Hinweise"
    ws["A1"].font = Font(bold=True)
    ws.column_dimensions["A"].width = 150

    wb.save(path)


# ---------------------------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------------------------

def copy_style(dst, src):
    for attr in ("font", "fill", "border", "alignment", "number_format"):
        setattr(dst, attr, copy(getattr(src, attr)))


def template_rows(ws):
    """key -> row number of a matrix sheet"""
    return {ws.cell(r, KEY_COL).value: r for r in range(FIRST_ROW, ws.max_row + 1) if ws.cell(r, KEY_COL).value}


def fill_matrix(ws, result, field):
    rows = template_rows(ws)
    missing = [r["key"] for r in template_result_rows(result) if r["key"] not in rows]
    if missing:
        raise SystemExit(f"sheet '{ws.title}': rows of the result missing in the template, e.g. {missing[:3]} "
                         f"({len(missing)} in total); recreate the template with --new-template")

    ws["B1"] = result["title"]
    ws["B2"] = f"Auswertungszeitraum: {result['period']}"
    ws["B3"] = suppression_line(result)

    # the LE columns take the style of the template's LE column
    style_col = FIRST_LE_COL
    for i, entity in enumerate(result["entities"]):
        col = TOTAL_COL if i == 0 else FIRST_LE_COL + i - 1
        if col > style_col:
            copy_style(ws.cell(HEADER_ROW, col), ws.cell(HEADER_ROW, style_col))
            ws.column_dimensions[get_column_letter(col)].width = ws.column_dimensions[get_column_letter(style_col)].width
        ws.cell(HEADER_ROW, col, result["labels"][entity])
        for r in template_result_rows(result):
            row = rows[r["key"]]
            cell = ws.cell(row, col)
            if col > style_col:
                copy_style(cell, ws.cell(row, style_col))
            cell.value = r["values"][entity][field]
    if len(result["entities"]) == 1:   # no LE: drop the placeholder
        ws.cell(HEADER_ROW, FIRST_LE_COL).value = None


def suppression_line(result):
    """The line on the suppressed cells above the matrix (RD; the oncology JSON has no min_cell: not suppressed;
    a JSON without suppress_scope: all rows)"""
    if "min_cell" not in result:
        return "Keine Zellen unterdrückt: alle Zahlen exakt."
    if result["min_cell"] <= 1:
        return ""
    k = result["min_cell"]
    if result.get("suppress_scope", "all") == "diagnoses":
        return (f"Nur Diagnosezeilen (Abschnitte siehe Hinweise): Zellen mit weniger als {k} Fällen unterdrückt (<{k}), "
                "ergänzend weitere, damit keine unterdrückte Zahl als Differenz berechenbar ist. Alle anderen Zahlen exakt.")
    return (f"Zellen mit weniger als {k} Fällen unterdrückt (<{k}), ergänzend "
            "weitere, damit keine unterdrückte Zahl als Differenz berechenbar ist.")


def fill(wb, result):
    fill_matrix(wb[COUNTS], result, "value")
    fill_matrix(wb[PERCENTS], result, "percent")

    ws = wb[PROBLEMS]
    if ws.max_row > 1:
        raise SystemExit(f"sheet '{PROBLEMS}' isn't empty, not a blank template")
    for p in result.get("problems", []):   # not contained in the oncology JSON
        ws.append([result["labels"].get(p["le"], p["le"]), p["problem"], p["case"], p["tan"], p["detail"]])
    ws.auto_filter.ref = f"A1:E{max(ws.max_row, 1)}"

    ws = wb[NOTES]
    for i, note in enumerate(result["notes"], 2):
        ws.cell(i, 1, note).alignment = Alignment(wrap_text=True, vertical="top")

    fill_by_month(wb, result)


def fill_by_month(wb, result):
    """The reasons for a missing broad consent per month of the initial submission: count (share) per LE"""
    monthly = [r for r in result["rows"] if r["section"] == MONTHLY_SECTION]
    if not monthly:
        return
    if REASONS_BY_MONTH in wb.sheetnames:
        wb.remove(wb[REASONS_BY_MONTH])
    ws = wb.create_sheet(REASONS_BY_MONTH)
    ws["A1"] = MONTHLY_SECTION
    ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = ("Anzahl der Fälle ohne übermittelten Broad Consent je Grund laut DIP; in Klammern Anteil an diesen Fällen "
                "des Monats in %." + (f" Zellen mit weniger als {result['min_cell']} Fällen unterdrückt."
                                     if result.get("min_cell", 1) > 1 and result.get("suppress_scope", "all") == "all" else ""))
    header = ["Monat", "Grund"] + [result["labels"][e] for e in result["entities"]]
    for col, text in enumerate(header, 1):
        cell = ws.cell(4, col, text)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="left" if col <= 2 else "center", wrap_text=True)
    for i, r in enumerate(monthly, 5):
        month, reason = r["item"].split(": ", 1)
        ws.cell(i, 1, month)
        ws.cell(i, 2, reason)
        for j, entity in enumerate(result["entities"], 3):
            v = r["values"][entity]
            text = "" if v["value"] is None else str(v["value"])
            if v.get("percent") is not None:
                text += f" ({str(v['percent']).replace('.', ',')})"
            ws.cell(i, j, text).alignment = Alignment(horizontal="right")
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 40
    for j in range(3, len(header) + 1):
        ws.column_dimensions[get_column_letter(j)].width = 14
    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(header))}{ws.max_row}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("result", help="result JSON of rd_evaluation_matrix.py")
    parser.add_argument("template", help="the Auswertematrix template (.xlsx)")
    parser.add_argument("--output", default="Auswertmatrix_filled.xlsx", help="filled copy (default: %(default)s)")
    parser.add_argument("--new-template", action="store_true",
                        help="(re)create the template from the rows of the result instead of filling it")
    args = parser.parse_args(argv)

    with open(args.result, encoding="utf-8") as f:
        result = json.load(f)

    if args.new_template:
        new_template(result, args.template)
        print(f"Template written: {args.template} ({len(result['rows'])} rows)")
        return

    wb = openpyxl.load_workbook(args.template)
    fill(wb, result)
    wb.save(args.output)

    check = openpyxl.load_workbook(args.output)[COUNTS]
    rows = template_rows(check)
    wrong = [r["key"] for r in template_result_rows(result)
             if check.cell(rows[r["key"]], TOTAL_COL).value != r["values"]["Gesamt"]["value"]]
    if wrong:
        raise SystemExit(f"cells not written as expected: {wrong[:5]}")
    print(f"Written: {args.output} ({len(result['rows'])} rows, {len(result['entities']) - 1} LEs, "
          f"{len(result.get('problems', []))} Rückfragen, {len(result['notes'])} notes)")


if __name__ == "__main__":
    main()
