#!/usr/bin/env python3
"""
The evaluation matrices (Auswertematrix) of rare diseases (rd_evaluation_matrix.py) and oncology
(dnpm_evaluation_matrix.py) for each month of a period: one evaluation per month, the cases selected
by the month of their initial submission (--from/--until of the scripts set to the month).

Standard library only, so it runs where the export is (PRO); the xlsx are written from the results
by fill_monthly.py next to this script, outside of PRO (needs openpyxl).

The export is read once, the months are evaluated one after the other, each written as the
respective script writes it:
  <output-dir>/rd/rd_<YYYY-MM>.json, .csv, _rueckfragen.csv
  <output-dir>/dnpm/dnpm_<YYYY-MM>.json, .csv, _zeitreihe.csv, _rueckfragen.csv
The Rückfragen contain case keys: only for the respective LEs. The RD JSON contains them as well
(as written by rd_evaluation_matrix.py), the oncology JSON doesn't.

Example (months July 2025 to October 2026):
  python3 monthly_matrices.py export-datenkranz_2026-11-01_10-00-00.json \\
    --from 2025-07 --until 2026-11 --orphanet en_product7.xml --reference-date 2026-11-01 --output-dir monthly
  uv run fill_monthly.py monthly
"""

import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rd_evaluation_matrix"))
sys.path.insert(0, os.path.join(HERE, "..", "dnpm_evaluation_matrix"))

import dnpm_evaluation_matrix as dnpm   # noqa: E402
import rd_evaluation_matrix as rd       # noqa: E402

RD, DNPM = "rd", "dnpm"


def month(text):
    """YYYY-MM -> the first day of the month"""
    try:
        year, mon = text.split("-")
        return date(int(year), int(mon), 1)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a month (YYYY-MM): {text}")


def next_month(d):
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def months(start, end):
    """The first days of the months from start (inclusive) to end (exclusive)"""
    result = []
    while start < end:
        result.append(start)
        start = next_month(start)
    return result


def run_rd(path, months_, args):
    rows, failures = rd.load_submissions(path)
    heads = rd.load_linearisation(args.orphanet) if args.orphanet else None
    out_dir = os.path.join(args.output_dir, RD)
    os.makedirs(out_dir, exist_ok=True)
    for start in months_:
        output = os.path.join(out_dir, f"{RD}_{start:%Y-%m}")
        result = rd.run(rows, failures, heads, start, next_month(start), args.reference_date, args.min_cell,
                        args.min_medical_days)
        # as rd_evaluation_matrix.main writes it
        with open(f"{output}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        rd.write_csv(f"{output}.csv", result, args.delimiter)
        rd.write_problems_csv(f"{output}_rueckfragen.csv", result, args.delimiter)
        print(f"RD   {start:%Y-%m}: {output}.json ({cases(result)} Fälle)", file=sys.stderr)


def run_dnpm(path, months_, args):
    rows, failures = dnpm.load_submissions(path)
    out_dir = os.path.join(args.output_dir, DNPM)
    os.makedirs(out_dir, exist_ok=True)
    reference_date = args.reference_date or args.cutoff or date.today()
    for start in months_:
        output = os.path.join(out_dir, f"{DNPM}_{start:%Y-%m}")
        result = dnpm.run(rows, failures, start, next_month(start), reference_date, args.min_cell,
                          args.min_medical_days, dnpm.MONTH, args.cutoff)
        # as dnpm_evaluation_matrix.main writes it
        with open(f"{output}.json", "w", encoding="utf-8") as f:     # without the case keys of the Rückfragen
            json.dump({k: v for k, v in result.items() if k != "problems"}, f, ensure_ascii=False, indent=1)
        dnpm.write_csv(f"{output}.csv", result, args.delimiter)
        dnpm.write_series_csv(f"{output}_zeitreihe.csv", result, args.delimiter)
        dnpm.write_problems_csv(f"{output}_rueckfragen.csv", result["problems"], result["labels"], args.delimiter)
        print(f"DNPM {start:%Y-%m}: {output}.json ({cases(result)} Fälle)", file=sys.stderr)


def cases(result):
    """The total number of cases, as shown (possibly suppressed)"""
    return result["rows"][0]["values"][result["entities"][0]]["value"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="result JSON of export-datenkranz (or a JSON list of submissions)")
    parser.add_argument("--from", dest="start", type=month, required=True, help="first month (YYYY-MM)")
    parser.add_argument("--until", dest="end", type=month, required=True,
                        help="month after the last one (YYYY-MM, exclusive like --until of the scripts)")
    parser.add_argument("--matrix", choices=(RD, DNPM, "both"), default="both", help="default: %(default)s")
    parser.add_argument("--orphanet", help="RD: Orphadata linearisation, en_product7.xml (needed for the disease groups)")
    parser.add_argument("--reference-date", type=date.fromisoformat,
                        help="date consents (and due follow-ups) are evaluated at, the same for all months; best the "
                             "date of the export (default: RD today, oncology --cutoff, else today)")
    parser.add_argument("--cutoff", type=date.fromisoformat,
                        help="oncology: data as of this date, later submissions are ignored")
    parser.add_argument("--min-cell", type=int, default=5, help="counts below are suppressed, 1 disables (default: %(default)s)")
    parser.add_argument("--min-medical-days", type=int, default=14,
                        help="shorter GFK1-GFK2 durations are listed for clarification (default: %(default)s)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s)")
    parser.add_argument("--output-dir", default="monthly", help="default: %(default)s")
    args = parser.parse_args(argv)
    if args.start >= args.end:
        parser.error("--from must be before --until")
    if args.cutoff and args.reference_date and args.reference_date > args.cutoff:
        parser.error("--reference-date must not be after --cutoff")

    months_ = months(args.start, args.end)
    if args.matrix in (RD, "both"):
        if not args.orphanet:
            print("RD: without --orphanet the disease groups aren't evaluated", file=sys.stderr)
        rd_args = argparse.Namespace(**vars(args))
        rd_args.reference_date = args.reference_date or date.today()
        run_rd(args.input, months_, rd_args)
    if args.matrix in (DNPM, "both"):
        run_dnpm(args.input, months_, args)
    print(f"Written: {len(months_)} months into {args.output_dir}; xlsx with fill_monthly.py", file=sys.stderr)


if __name__ == "__main__":
    main()
