#!/usr/bin/env python3
"""
The evaluation matrices (Auswertematrix) of rare diseases (rd_evaluation_matrix.py) and oncology
(dnpm_evaluation_matrix.py) for each quarter (--period quarter, the default) or month (--period month) of a
period: one evaluation per period, the cases selected by the calendar quarter or month of their initial
submission (Erstmeldung; --from/--until of the scripts set to the period, Q4/2025 = 2025-10-01 to before
2026-01-01). The first period is that of the earliest initial submission in the export (or --from, if later),
the last one that of the latest initial submission (or the one before --until): no empty leading periods.

Standard library only, so it runs where the export is (PRO); the xlsx are written from the results
by fill_monthly.py next to this script, outside of PRO (needs openpyxl).

The export is read once, the periods are evaluated one after the other, each written as the
respective script writes it (<period> = YYYY-Qn or YYYY-MM):
  <output-dir>/rd/rd_<period>.json, .csv, _rueckfragen.csv
  <output-dir>/dnpm/dnpm_<period>.json, .csv, _zeitreihe.csv, _rueckfragen.csv
Small counts are suppressed only in the diagnosis rows (--suppress-scope diagnoses, the default of both
scripts; their DIAGNOSIS_SECTIONS), --suppress-scope all suppresses every row.
The Rückfragen contain case keys: only for the respective LEs. The RD JSON contains them as well
(as written by rd_evaluation_matrix.py), the oncology JSON doesn't.

Examples (all quarters of the export; months July 2025 to October 2026):
  python3 monthly_matrices.py export-datenkranz_2026-11-01_10-00-00.json \\
    --orphanet en_product7.xml --reference-date 2026-11-01 --output-dir quarterly
  python3 monthly_matrices.py export-datenkranz_2026-11-01_10-00-00.json --period month \\
    --from 2025-07 --until 2026-11 --orphanet en_product7.xml --reference-date 2026-11-01 --output-dir monthly
  uv run fill_monthly.py quarterly
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
MONTH, QUARTER = "month", "quarter"


def month(text):
    """YYYY-MM or YYYY-Qn -> the first day of the month or quarter"""
    try:
        year, rest = text.split("-")
        if rest[:1] in ("Q", "q"):
            if rest[1:] not in ("1", "2", "3", "4"):
                raise ValueError(text)
            return date(int(year), 3 * int(rest[1:]) - 2, 1)
        return date(int(year), int(rest), 1)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a month (YYYY-MM) or quarter (YYYY-Qn): {text}")


def period_start(d, period):
    """The first day of the month or calendar quarter of a date"""
    return date(d.year, d.month if period == MONTH else d.month - (d.month - 1) % 3, 1)


def next_period(d, period):
    """The first day of the month or quarter after the one starting at d"""
    m = d.year * 12 + d.month - 1 + (1 if period == MONTH else 3)
    return date(m // 12, m % 12 + 1, 1)


def label(d, period):
    """YYYY-MM or YYYY-Qn of the period starting at d, as in the file names"""
    return f"{d:%Y-%m}" if period == MONTH else f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def periods(first, last, period, start=None, end=None):
    """
    The first days of the periods from the one of the earliest initial submission (first) to the one of the latest
    (last), within [start, end): start is the period it falls into, end exclusive (periods starting before it)
    """
    if first is None:
        return []
    current = period_start(max(first, start) if start else first, period)
    result = []
    while current <= last and (end is None or current < end):
        result.append(current)
        current = next_period(current, period)
    return result


def rd_initial_dates(rows):
    """The dates of the initial submissions of the RD cases (as rd_evaluation_matrix selects them)"""
    cases, *_ = rd.build_cases(rows)
    dates = []
    for case in cases:
        try:
            dates.append(rd.submission_date(case.initial))
        except (KeyError, TypeError, ValueError):
            continue
    return [d for d in dates if d]


def dnpm_initial_dates(rows):
    """The dates of the initial submissions of the oncology cases (as dnpm_evaluation_matrix selects them)"""
    cases, *_ = dnpm.build_cases(rows)
    dates = (dnpm.submission_date(next(r for r in c.rows if dnpm.submission_type(r.content) == "initial")) for c in cases)
    return [d for d in dates if d]


def periods_of(dates, args):
    return periods(min(dates, default=None), max(dates, default=None), args.period, args.start, args.end)


def run_rd(path, args):
    rows, failures = rd.load_submissions(path)
    heads = rd.load_linearisation(args.orphanet) if args.orphanet else None
    out_dir = os.path.join(args.output_dir, RD)
    os.makedirs(out_dir, exist_ok=True)
    periods_ = periods_of(rd_initial_dates(rows), args)
    for start in periods_:
        output = os.path.join(out_dir, f"{RD}_{label(start, args.period)}")
        result = rd.run(rows, failures, heads, start, next_period(start, args.period), args.reference_date, args.min_cell,
                        args.min_medical_days, args.suppress_scope, args.follow_up_months)
        # as rd_evaluation_matrix.main writes it
        with open(f"{output}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        rd.write_csv(f"{output}.csv", result, args.delimiter)
        rd.write_problems_csv(f"{output}_rueckfragen.csv", result, args.delimiter)
        print(f"RD   {label(start, args.period)}: {output}.json ({cases(result)} Fälle)", file=sys.stderr)
    return len(periods_)


def run_dnpm(path, args):
    rows, failures = dnpm.load_submissions(path)
    out_dir = os.path.join(args.output_dir, DNPM)
    os.makedirs(out_dir, exist_ok=True)
    reference_date = args.reference_date or args.cutoff or date.today()
    periods_ = periods_of(dnpm_initial_dates(rows), args)
    resolution = dnpm.MONTH if args.period == MONTH else dnpm.QUARTER
    for start in periods_:
        output = os.path.join(out_dir, f"{DNPM}_{label(start, args.period)}")
        result = dnpm.run(rows, failures, start, next_period(start, args.period), reference_date, args.min_cell,
                          args.min_medical_days, resolution, args.cutoff, args.suppress_scope)
        # as dnpm_evaluation_matrix.main writes it
        with open(f"{output}.json", "w", encoding="utf-8") as f:     # without the case keys of the Rückfragen
            json.dump({k: v for k, v in result.items() if k != "problems"}, f, ensure_ascii=False, indent=1)
        dnpm.write_csv(f"{output}.csv", result, args.delimiter)
        dnpm.write_series_csv(f"{output}_zeitreihe.csv", result, args.delimiter)
        dnpm.write_problems_csv(f"{output}_rueckfragen.csv", result["problems"], result["labels"], args.delimiter)
        print(f"DNPM {label(start, args.period)}: {output}.json ({cases(result)} Fälle)", file=sys.stderr)
    return len(periods_)


def cases(result):
    """The total number of cases, as shown (possibly suppressed)"""
    return result["rows"][0]["values"][result["entities"][0]]["value"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="result JSON of export-datenkranz (or a JSON list of submissions)")
    parser.add_argument("--period", choices=(QUARTER, MONTH), default=QUARTER,
                        help="one matrix per calendar quarter or month of the initial submission (default: %(default)s)")
    parser.add_argument("--from", dest="start", type=month,
                        help="first month or quarter (YYYY-MM or YYYY-Qn; a month: its quarter); default and at the "
                             "earliest the period of the earliest initial submission")
    parser.add_argument("--until", dest="end", type=month,
                        help="exclusive like --until of the scripts (YYYY-MM or YYYY-Qn): the periods starting before; "
                             "default: up to the period of the latest initial submission")
    parser.add_argument("--matrix", choices=(RD, DNPM, "both"), default="both", help="default: %(default)s")
    parser.add_argument("--orphanet", help="RD: Orphadata linearisation, en_product7.xml (needed for the disease groups)")
    parser.add_argument("--reference-date", type=date.fromisoformat,
                        help="date consents (and due follow-ups) are evaluated at, the same for all months; best the "
                             "date of the export (default: RD today, oncology --cutoff, else today)")
    parser.add_argument("--cutoff", type=date.fromisoformat,
                        help="oncology: data as of this date, later submissions are ignored")
    parser.add_argument("--min-cell", type=int, default=5, help="counts below are suppressed, 1 disables (default: %(default)s)")
    parser.add_argument("--suppress-scope", choices=rd.SUPPRESS_SCOPES, default=rd.SUPPRESS_DIAGNOSES,
                        help="suppress small counts only in the diagnosis rows or in all rows (default: %(default)s)")
    parser.add_argument("--min-medical-days", type=int, default=14,
                        help="shorter GFK1-GFK2 durations are listed for clarification (default: %(default)s)")
    parser.add_argument("--follow-up-months", type=int, default=rd.FU_MONTHS,
                        help="RD: follow-up interval after GFK2 (oncology: the intervals of the follow-up SOP) "
                             "(default: %(default)s)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s)")
    parser.add_argument("--output-dir", default="monthly", help="default: %(default)s")
    args = parser.parse_args(argv)
    if args.start and args.end and args.start >= args.end:
        parser.error("--from must be before --until")
    if args.cutoff and args.reference_date and args.reference_date > args.cutoff:
        parser.error("--reference-date must not be after --cutoff")

    written = []
    if args.matrix in (RD, "both"):
        if not args.orphanet:
            print("RD: without --orphanet the disease groups aren't evaluated", file=sys.stderr)
        rd_args = argparse.Namespace(**vars(args))
        rd_args.reference_date = args.reference_date or date.today()
        written.append(f"RD {run_rd(args.input, rd_args)}")
    if args.matrix in (DNPM, "both"):
        written.append(f"oncology {run_dnpm(args.input, args)}")
    unit = "quarters" if args.period == QUARTER else "months"
    print(f"Written: {', '.join(written)} {unit} into {args.output_dir}; xlsx with fill_monthly.py", file=sys.stderr)


if __name__ == "__main__":
    main()
