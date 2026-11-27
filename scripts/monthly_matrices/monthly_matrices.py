#!/usr/bin/env python3
"""
The evaluation matrices (Auswertematrix) of rare diseases (rd_evaluation_matrix.py) and oncology
(dnpm_evaluation_matrix.py) for a whole period and for each quarter (--period quarter, the default) in it, in one
run: the single entry point for a total matrix together with its period matrices. RD (SE) only per quarter:
--period month is refused for RD (--matrix rd or both), monthly matrices are for oncology only (--matrix dnpm). Per period one evaluation, the cases selected by the calendar quarter or month of their initial
submission (Erstmeldung; --from/--until of the scripts set to the period, Q4/2025 = 2025-10-01 to before
2026-01-01). The first period is that of the earliest initial submission in the export (or --from, if later),
the last one that of the latest initial submission (or the one before --until): no empty leading periods. The
total matrix ("gesamt") covers exactly these periods, from the first day of the first one to the end of the last.

RD: each file (the total and every period) is suppressed on its own, primary only: an LE's count of 1 to
--min-cell - 1 in the rows derived from the diagnosis codes is "<5", nothing else (decision of the data holders,
27.11.2026). There is no complementary suppression: a suppressed count can follow from shown sums (Gesamt, the group
totals, the total minus the other periods).
Oncology: not suppressed (decision of the data holders), every value exact; the total matrix has the time
series by the same periods.

Standard library only, so it runs where the export is (PRO); the xlsx are written from the results
by fill_monthly.py next to this script, outside of PRO (needs openpyxl).

The export is parsed once for RD and oncology and written as the respective script writes it (<period> = YYYY-Qn or YYYY-MM, the
total matrix as <period> = gesamt):
  <output-dir>/rd/rd_<period>.json, .csv, _rueckfragen.csv
  <output-dir>/dnpm/dnpm_<period>.json, .csv, _zeitreihe.csv, _rueckfragen.csv
with --rueckfragen-per-le (recommended) instead of _rueckfragen.csv one _rueckfragen_<LE>.csv per LE, and the RD
JSON (so the xlsx) without the Rückfragen. RD: small counts are suppressed only in the rows derived from the
diagnosis codes (rd.CODE_SECTIONS: Orphacode, the disease groups, "Diagnose ohne Erkrankungsgruppe"), every other
number exact, also Gesamt and the diagnosis status.
The Rückfragen contain case keys: only for the respective LEs. The RD JSON contains them as well
(as written by rd_evaluation_matrix.py, unless --rueckfragen-per-le), the oncology JSON doesn't.

Examples (all quarters of the export and their total; oncology per month July 2025 to October 2026):
  python3 monthly_matrices.py export-datenkranz_2026-11-01_10-00-00.json \\
    --orphanet en_product7.xml --reference-date 2026-11-01 --rueckfragen-per-le --output-dir quarterly
  python3 monthly_matrices.py export-datenkranz_2026-11-01_10-00-00.json --matrix dnpm --period month \\
    --from 2025-07 --until 2026-11 --reference-date 2026-11-01 --output-dir monthly
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
    """The dates of the initial submissions of the RD cases (as rd_evaluation_matrix selects them; build_cases leaves
    out an initial submission without a readable date)"""
    cases, *_ = rd.build_cases(rows)
    return [rd.submission_date(case.initial) for case in cases]


def dnpm_initial_dates(rows, cutoff=None):
    """The dates of the initial submissions of the oncology cases (as dnpm_evaluation_matrix selects them, without
    the submissions after the cutoff)"""
    if cutoff:
        rows = [r for r in rows if not (dnpm.submission_date(r) and dnpm.submission_date(r) > cutoff)]
    cases, *_ = dnpm.build_cases(rows)
    dates = (dnpm.submission_date(dnpm.initial_submission(c)) for c in cases)
    return [d for d in dates if d]


def earlier_files(out_dir, kind, replace):
    """The files of an earlier run in the output directory of a kind (rd, dnpm); refused unless --replace: an old
    period's JSON with the Rückfragen of all LEs would otherwise go into the xlsx with the new ones (fill_monthly.py
    takes every <kind>_<period>.json), and old _rueckfragen.csv of all LEs would stay beside them"""
    old = sorted(f for f in os.listdir(out_dir) if f.startswith(f"{kind}_")) if os.path.isdir(out_dir) else []
    if old and not replace:
        raise SystemExit(f"{out_dir} has files of an earlier run ({', '.join(old[:3])}{', …' if len(old) > 3 else ''}); "
                         "use a new --output-dir or --replace (deletes them)")
    return old


def prepare_dir(out_dir, kind, replace):
    """The output directory of a kind, the files of an earlier run deleted (only with --replace, earlier_files)"""
    old = earlier_files(out_dir, kind, replace)
    os.makedirs(out_dir, exist_ok=True)
    for f in old:
        os.remove(os.path.join(out_dir, f))


def periods_of(dates, args):
    return periods(min(dates, default=None), max(dates, default=None), args.period, args.start, args.end)


TOTAL_LABEL = "gesamt"


def read_input(path):
    """The input file, parsed once for RD and oncology: (RD rows and failures, oncology rows and failures)"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return rd.submissions_from(data, path), dnpm.submissions_from(data, path)


def rd_results(source, args):
    """The RD total matrix and the period matrices, each suppressed on its own: [(name, result)] (empty without
    cases); source: the RD rows and failures"""
    rows, failures = source
    heads = rd.load_linearisation(args.orphanet) if args.orphanet else None
    periods_ = periods_of(rd_initial_dates(rows), args)
    if not periods_:
        return []
    ranges = [(start, next_period(start, args.period)) for start in periods_]
    total, parts = rd.run_joint(rows, failures, heads, ranges, args.reference_date, args.min_cell, args.min_medical_days,
                                args.suppress_scope, args.follow_up_months, start=ranges[0][0], end=ranges[-1][1])
    return [(TOTAL_LABEL, total)] + [(label(start, args.period), r) for start, r in zip(periods_, parts)]


def write_rd(results, args):
    """The RD files as rd_evaluation_matrix.main writes them; on an error the files of this run are removed"""
    out_dir = os.path.join(args.output_dir, RD)
    prepare_dir(out_dir, RD, args.replace)
    written = []
    try:
        for name, result in results:
            output = os.path.join(out_dir, f"{RD}_{name}")
            written += rd.write_outputs(output, result, args.delimiter, args.rueckfragen_per_le)
            print(f"RD   {name}: {output}.json ({cases(result)} Fälle)", file=sys.stderr)
    except BaseException:
        rd.remove_files(written)
        raise
    return len(results) - 1 if results else 0


def run_dnpm(source, args):
    """The oncology total matrix (time series by the periods) and the period matrices; the number of periods;
    source: the oncology rows and failures"""
    rows, failures = source
    out_dir = os.path.join(args.output_dir, DNPM)
    prepare_dir(out_dir, DNPM, args.replace)
    reference_date = args.reference_date or args.cutoff or date.today()
    periods_ = periods_of(dnpm_initial_dates(rows, args.cutoff), args)
    if not periods_:
        return 0
    resolution = dnpm.MONTH if args.period == MONTH else dnpm.QUARTER
    ranges = [(start, next_period(start, args.period)) for start in periods_]
    for name, (start, end) in [(TOTAL_LABEL, (ranges[0][0], ranges[-1][1]))] + [(label(s, args.period), (s, e)) for s, e in ranges]:
        output = os.path.join(out_dir, f"{DNPM}_{name}")
        result = dnpm.run(rows, failures, start, end, reference_date, args.min_medical_days, resolution, args.cutoff)
        # as dnpm_evaluation_matrix.main writes it
        with open(f"{output}.json", "w", encoding="utf-8") as f:     # without the case keys of the Rückfragen
            json.dump({k: v for k, v in result.items() if k != "problems"}, f, ensure_ascii=False, indent=1)
        dnpm.write_csv(f"{output}.csv", result, args.delimiter)
        dnpm.write_series_csv(f"{output}_zeitreihe.csv", result, args.delimiter)
        dnpm.write_rueckfragen(output, result, args.delimiter, args.rueckfragen_per_le)
        print(f"DNPM {name}: {output}.json ({cases(result)} Fälle)", file=sys.stderr)
    return len(periods_)


def cases(result):
    """The total number of cases, as shown (possibly suppressed)"""
    return result["rows"][0]["values"][result["entities"][0]]["value"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="result JSON of export-datenkranz (or a JSON list of submissions)")
    parser.add_argument("--period", choices=(QUARTER, MONTH), default=QUARTER,
                        help="one matrix per calendar quarter or month of the initial submission (default: %(default)s); "
                             "month only with --matrix dnpm (RD only per quarter)")
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
    parser.add_argument("--min-cell", type=int, default=5,
                        help="RD: counts below are suppressed, 1 disables (default: %(default)s; oncology: not suppressed)")
    parser.add_argument("--suppress-scope", type=rd.scope_argument, choices=rd.SUPPRESS_SCOPES, default=rd.SUPPRESS_CODES,
                        help="RD: small counts are suppressed only in the rows derived from the diagnosis codes (the only "
                             "scope; default: %(default)s)")
    parser.add_argument("--min-medical-days", type=int, default=14,
                        help="shorter GFK1-GFK2 durations are listed for clarification (default: %(default)s)")
    parser.add_argument("--follow-up-months", type=int, default=rd.FU_MONTHS,
                        help="RD: follow-up interval after GFK2 (oncology: the intervals of the follow-up SOP) "
                             "(default: %(default)s)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s)")
    parser.add_argument("--rueckfragen-per-le", action="store_true",
                        help="the Rückfragen only per LE (<period>_rueckfragen_<LE>.csv), no file of all LEs, and none in "
                             "the RD JSON; recommended")
    parser.add_argument("--output-dir", default="monthly", help="default: %(default)s")
    parser.add_argument("--replace", action="store_true",
                        help="delete the rd_*/dnpm_* files of an earlier run in --output-dir first (else refused)")
    args = parser.parse_args(argv)
    if args.start and args.end and args.start >= args.end:
        parser.error("--from must be before --until")
    if args.cutoff and args.reference_date and args.reference_date > args.cutoff:
        parser.error("--reference-date must not be after --cutoff")
    if args.period == MONTH and args.matrix in (RD, "both"):
        parser.error("--period month: the RD (SE) matrices only per quarter; monthly matrices with --matrix dnpm")

    written = []
    if args.matrix in (RD, "both"):
        earlier_files(os.path.join(args.output_dir, RD), RD, args.replace)     # refused before the long evaluation
        if args.matrix == "both":
            earlier_files(os.path.join(args.output_dir, DNPM), DNPM, args.replace)
    rd_source, dnpm_source = read_input(args.input)        # parsed once for both
    if args.matrix in (RD, "both"):
        if not args.orphanet:
            print("RD: without --orphanet the disease groups aren't evaluated", file=sys.stderr)
        rd_args = argparse.Namespace(**vars(args))
        rd_args.reference_date = args.reference_date or date.today()
        results = rd_results(rd_source, rd_args)
        written.append(f"RD {write_rd(results, rd_args)}")
    if args.matrix in (DNPM, "both"):
        written.append(f"oncology {run_dnpm(dnpm_source, args)}")
    unit = "quarters" if args.period == QUARTER else "months"
    print(f"Written: {', '.join(written)} {unit} and their total ({TOTAL_LABEL}) into {args.output_dir}; xlsx with "
          "fill_monthly.py", file=sys.stderr)


if __name__ == "__main__":
    main()
