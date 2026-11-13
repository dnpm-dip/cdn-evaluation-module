#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["openpyxl"]
# ///
"""
Fill the Auswertematrix template with the result JSON of rd_evaluation_matrix.py.

  - "cells":  each value written to its coordinate on the sheet "Übersicht"
  - "extras": a table below the template (from row 150), "<section>: <item>" in B, value in C
  - "notes":  a list below the extras, one note per row in B

The input files are left unchanged.

Example:
  uv run fill_template.py rd-evaluation-matrix_old.json Auswertmatrix_Template.xlsx \\
    --output Auswertmatrix_filled.xlsx
"""

import argparse
import json
from copy import copy

import openpyxl

SHEET = "Übersicht"
EXTRAS_ROW = 150  # first row below the template


def copy_style(dst, src):
    for attr in ("font", "fill", "border", "alignment", "number_format"):
        setattr(dst, attr, copy(getattr(src, attr)))


def fill(ws, result):
    for ref, value in result["cells"].items():
        ws[ref].value = value

    # header and row styles as those of the section "Geschlechterverteilung" (rows 7-8)
    header, label, value = ws["B7"], ws["B8"], ws["C8"]

    row = EXTRAS_ROW
    ws[f"B{row}"] = "Zusätzliche Werte (nicht in der Vorlage)"
    ws[f"C{row}"] = "Anzahl"
    copy_style(ws[f"B{row}"], header)
    copy_style(ws[f"C{row}"], ws["C7"])
    for extra in result.get("extras", []):
        row += 1
        ws[f"B{row}"] = f"{extra['section']}: {extra['item']}"
        ws[f"C{row}"] = extra["value"]
        copy_style(ws[f"B{row}"], label)
        copy_style(ws[f"C{row}"], value)

    row += 2
    ws[f"B{row}"] = "Hinweise"
    ws[f"B{row}"].font = copy(header.font)
    for note in result.get("notes", []):
        row += 1
        ws[f"B{row}"] = note
        ws[f"B{row}"].font = copy(label.font)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("result", help="result JSON of rd_evaluation_matrix.py")
    parser.add_argument("template", help="the Auswertematrix template (.xlsx)")
    parser.add_argument("--output", default="Auswertmatrix_filled.xlsx", help="filled copy (default: %(default)s)")
    args = parser.parse_args(argv)

    with open(args.result, encoding="utf-8") as f:
        result = json.load(f)
    wb = openpyxl.load_workbook(args.template)
    ws = wb[SHEET]

    occupied = [r for r in range(EXTRAS_ROW, ws.max_row + 1) if any(c.value is not None for c in ws[r])]
    if occupied:
        raise SystemExit(f"template already has content from row {EXTRAS_ROW} on (row {occupied[0]}), not a blank template")

    fill(ws, result)
    wb.save(args.output)

    check = openpyxl.load_workbook(args.output)[SHEET]
    wrong = [ref for ref, v in result["cells"].items() if check[ref].value != v]
    if wrong:
        raise SystemExit(f"cells not written as expected: {wrong}")
    print(f"Written: {args.output} ({len(result['cells'])} cells, "
          f"{len(result.get('extras', []))} extras, {len(result.get('notes', []))} notes)")


if __name__ == "__main__":
    main()
