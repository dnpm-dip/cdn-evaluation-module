#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["openpyxl"]
# ///
"""
Write one Auswertematrix (xlsx) per quarter or month from the results of monthly_matrices.py, by
fill_template.py of rd_evaluation_matrix: <dir>/rd/rd_<period>.json -> rd_<period>.xlsx and
<dir>/dnpm/dnpm_<period>.json -> dnpm_<period>.xlsx (<period> = YYYY-Qn or YYYY-MM; next to the JSON, or
into --output-dir).

Templates: RD by default rd_evaluation_matrix/Auswertmatrix_Template.xlsx. For oncology there is none
in the repository: if --dnpm-template doesn't exist, it is created from the rows of the first month
(fill_template.py --new-template; the rows are the same for every period) and can be restyled for
later runs. The oncology JSON contains no Rückfragen (they contain case keys), so its sheet
"Rückfragen an LE" stays empty: see dnpm_<period>_rueckfragen.csv.

Example:
  uv run fill_monthly.py quarterly
  uv run fill_monthly.py quarterly --dnpm-template Auswertmatrix_MTB_Template.xlsx --output-dir xlsx
"""

import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rd_evaluation_matrix"))

import fill_template   # noqa: E402

RD, DNPM = "rd", "dnpm"
DEFAULT_RD_TEMPLATE = os.path.join(HERE, "..", "rd_evaluation_matrix", "Auswertmatrix_Template.xlsx")
DEFAULT_DNPM_TEMPLATE = "Auswertmatrix_MTB_Template.xlsx"


def results(directory, kind):
    """The result JSONs of a kind per quarter or month, by period"""
    pattern = re.compile(rf"{kind}_(\d{{4}}-(\d{{2}}|Q[1-4])|gesamt)\.json$")
    return sorted(p for p in glob.glob(os.path.join(directory, kind, f"{kind}_*.json"))
                  if pattern.search(os.path.basename(p)))


def fill_all(paths, template, output_dir):
    for path in paths:
        out_dir = output_dir or os.path.dirname(path)
        os.makedirs(out_dir, exist_ok=True)
        output = os.path.join(out_dir, os.path.basename(path)[:-len(".json")] + ".xlsx")
        fill_template.main([path, template, "--output", output])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dir", help="--output-dir of monthly_matrices.py")
    parser.add_argument("--matrix", choices=(RD, DNPM, "both"), default="both", help="default: %(default)s")
    parser.add_argument("--rd-template", default=DEFAULT_RD_TEMPLATE, help="default: %(default)s")
    parser.add_argument("--dnpm-template", help=f"default: <dir>/{DEFAULT_DNPM_TEMPLATE}, created if missing")
    parser.add_argument("--output-dir", help="for the xlsx (default: next to the JSON)")
    args = parser.parse_args(argv)

    found = False
    if args.matrix in (RD, "both"):
        paths = results(args.dir, RD)
        if paths:
            found = True
            if not os.path.exists(args.rd_template):
                parser.error(f"RD template not found: {args.rd_template}")
            fill_all(paths, args.rd_template, args.output_dir)
    if args.matrix in (DNPM, "both"):
        paths = results(args.dir, DNPM)
        if paths:
            found = True
            template = args.dnpm_template or os.path.join(args.dir, DEFAULT_DNPM_TEMPLATE)
            if not os.path.exists(template):
                fill_template.main([paths[0], template, "--new-template"])
            fill_all(paths, template, args.output_dir)
    if not found:
        parser.error(f"no monthly results in {args.dir} (expected {RD}/{RD}_YYYY-Qn.json, {RD}/{RD}_YYYY-MM.json or the same under {DNPM}/)")


if __name__ == "__main__":
    main()
