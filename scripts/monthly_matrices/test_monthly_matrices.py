"""Tests of monthly_matrices.py: python3 -m unittest discover -s scripts/monthly_matrices"""

import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from io import StringIO

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rd_evaluation_matrix"))
sys.path.insert(0, os.path.join(HERE, "..", "dnpm_evaluation_matrix"))

import monthly_matrices as mm               # noqa: E402
import rd_evaluation_matrix as rd           # noqa: E402
import test_dnpm_evaluation_matrix as dnt   # noqa: E402  (synthetic oncology submissions)
import test_rd_evaluation_matrix as rdt     # noqa: E402  (synthetic Datenkranz submissions)


class PeriodTests(unittest.TestCase):

    def test_quarter_bounds_and_labels(self):
        self.assertEqual(mm.period_start(date(2025, 12, 31), mm.QUARTER), date(2025, 10, 1))
        self.assertEqual(mm.period_start(date(2025, 1, 1), mm.QUARTER), date(2025, 1, 1))
        self.assertEqual(mm.next_period(date(2025, 10, 1), mm.QUARTER), date(2026, 1, 1))     # year boundary
        self.assertEqual(mm.next_period(date(2025, 12, 1), mm.MONTH), date(2026, 1, 1))
        self.assertEqual(mm.label(date(2025, 10, 1), mm.QUARTER), "2025-Q4")
        self.assertEqual(mm.label(date(2026, 1, 1), mm.QUARTER), "2026-Q1")
        self.assertEqual(mm.label(date(2025, 10, 1), mm.MONTH), "2025-10")

    def test_period_arguments(self):
        self.assertEqual(mm.month("2025-Q4"), date(2025, 10, 1))
        self.assertEqual(mm.month("2025-07"), date(2025, 7, 1))
        for bad in ("2025-Q5", "2025-13", "2025"):
            with self.assertRaises(Exception):
                mm.month(bad)

    def test_periods_start_with_the_earliest_initial_submission(self):
        first, last = date(2025, 11, 15), date(2026, 2, 3)
        self.assertEqual(mm.periods(first, last, mm.QUARTER), [date(2025, 10, 1), date(2026, 1, 1)])
        # an earlier --from doesn't add empty leading quarters, a later one starts at its quarter
        self.assertEqual(mm.periods(first, last, mm.QUARTER, start=date(2024, 1, 1)), [date(2025, 10, 1), date(2026, 1, 1)])
        self.assertEqual(mm.periods(first, last, mm.QUARTER, start=date(2026, 2, 1)), [date(2026, 1, 1)])
        # --until is exclusive: the periods starting before it
        self.assertEqual(mm.periods(first, last, mm.QUARTER, end=date(2026, 1, 1)), [date(2025, 10, 1)])
        self.assertEqual(mm.periods(first, last, mm.MONTH),
                         [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)])
        self.assertEqual(mm.periods(None, None, mm.QUARTER), [])


class RunTests(unittest.TestCase):

    def run_mm(self, kind, entries, *args):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "export.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        out = os.path.join(tmp.name, "out")
        with redirect_stderr(StringIO()):
            mm.main([path, "--matrix", kind, "--reference-date", "2026-11-01", "--output-dir", out, *args])
        directory = os.path.join(out, kind)
        results = {}
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json"):
                with open(os.path.join(directory, name), encoding="utf-8") as f:
                    results[name[:-len(".json")]] = json.load(f)
        return results, sorted(os.listdir(directory))

    def run_rd(self, dates, *args):
        entries = [{"case": f"c{i}", "submittedAt": f"{d}T10:00:00", "site": "LE-A",
                    "content": rdt.datenkranz(f"t{i}", submitted=d, gfk1="2024-01-10", plan_date="2024-02-10")}
                   for i, d in enumerate(dates)]
        return self.run_mm("rd", entries, *args)[0]

    def test_one_matrix_per_quarter_of_the_initial_submission_and_their_total(self):
        results = self.run_rd(["2025-11-30", "2025-12-31", "2026-01-01", "2026-03-31", "2026-04-01"])
        self.assertEqual(list(results), ["rd_2025-Q4", "rd_2026-Q1", "rd_2026-Q2", "rd_gesamt"])   # nothing before 2025-Q4
        cases = {k: r["rows"][0]["values"]["Gesamt"]["value"] for k, r in results.items()}
        self.assertEqual(cases, {"rd_2025-Q4": 2, "rd_2026-Q1": 2, "rd_2026-Q2": 1, "rd_gesamt": 5})
        q4 = results["rd_2025-Q4"]
        self.assertEqual(q4["period"], "01.10.2025 - 31.12.2025")
        self.assertIn("Fälle mit Erstmeldung vom 2025-10-01 bis vor dem 2026-01-01.", q4["notes"])
        # the total covers exactly the quarters
        self.assertEqual(results["rd_gesamt"]["period"], "01.10.2025 - 30.06.2026")
        self.assertTrue(any("Gemeinsam unterdrückt" in n for n in results["rd_gesamt"]["notes"]))
        # the default scope: the case counts below 5 are shown
        self.assertEqual(q4["suppress_scope"], "diagnoses")

    def test_months_and_the_period_range(self):
        results = self.run_rd(["2025-11-30", "2025-12-31", "2026-01-01"], "--period", "month", "--until", "2026-01")
        self.assertEqual(list(results), ["rd_2025-11", "rd_2025-12", "rd_gesamt"])
        self.assertIn("Fälle mit Erstmeldung vom 2025-11-01 bis vor dem 2025-12-01.", results["rd_2025-11"]["notes"])
        self.assertEqual(results["rd_gesamt"]["rows"][0]["values"]["Gesamt"]["value"], 2)
        results = self.run_rd(["2025-11-30", "2026-05-01"], "--from", "2026-Q1", "--suppress-scope", "all")
        self.assertEqual(list(results), ["rd_2026-Q1", "rd_2026-Q2", "rd_gesamt"])     # an empty quarter within is written
        self.assertEqual(results["rd_2026-Q2"]["suppress_scope"], "all")
        # all rows: the case count of the only LE is suppressed, and Gesamt with it (it is that LE's count)
        self.assertEqual(results["rd_2026-Q2"]["rows"][0]["values"]["Gesamt"]["value"], "<5")
        self.assertEqual(self.run_rd(["2026-05-01"])["rd_2026-Q2"]["rows"][0]["values"]["Gesamt"]["value"], 1)

    def test_follow_up_rows_and_interval(self):
        prefix = "Stand am Stichtag (Intervall "
        for args, months in (((), 12), (("--follow-up-months", "6"), 6)):
            result = self.run_rd(["2025-11-30"], *args)["rd_2025-Q4"]
            items = [r["item"] for r in result["rows"] if r["section"] == "Follow-up (Fälle mit GFK2)"]
            self.assertIn(f"{prefix}{months} Monate): fällig, überfällig (kein Follow-up im Intervall vor dem Stichtag)", items)

    def oncology(self, n=3, *args):
        entries = [{"case": f"c{i}", "submittedAt": f"2025-1{i % 2 + 1}-03T10:00:00", "site": ["LE-A", "LE-B"][i % 3 == 0],
                    "content": dnt.datenkranz(f"t{i}", submitted=f"2025-1{i % 2 + 1}-03",
                                              gender="male" if i == 0 else "female")} for i in range(n)]
        return self.run_mm("dnpm", entries, *args)

    def test_the_oncology_matrices_are_not_suppressed_and_have_a_total(self):
        results, _ = self.oncology(3)
        self.assertEqual(list(results), ["dnpm_2025-Q4", "dnpm_gesamt"])
        result = results["dnpm_2025-Q4"]
        self.assertEqual(result["rows"][0]["values"]["Gesamt"]["value"], 3)
        self.assertEqual(result["rows"][0]["values"]["LE-B"]["value"], 1)
        self.assertFalse({"min_cell", "suppress_scope", "suppressed"} & set(result))
        values = [v[f] for r in result["rows"] for v in r["values"].values() for f in ("value", "base")]
        self.assertFalse([v for v in values if isinstance(v, str)])
        self.assertTrue(any(r["section"].startswith("Follow-up nach SOP-Intervall") for r in result["rows"]))
        # the total has the time series by the same periods
        self.assertEqual(results["dnpm_gesamt"]["time_series"]["periods"], ["2025-Q4"])
        results, _ = self.oncology(3, "--period", "month")
        self.assertEqual(list(results), ["dnpm_2025-11", "dnpm_2025-12", "dnpm_gesamt"])
        self.assertEqual(results["dnpm_gesamt"]["time_series"]["periods"], ["2025-11", "2025-12"])

    def test_rueckfragen_per_le_writes_no_file_of_all_les(self):
        _, files = self.oncology(3, "--rueckfragen-per-le")
        self.assertFalse([f for f in files if f.endswith("_rueckfragen.csv")])
        self.assertIn("dnpm_2025-Q4_rueckfragen_LE-A.csv", files)
        self.assertIn("dnpm_gesamt_rueckfragen_LE-B.csv", files)
        entries = [{"case": "c0", "submittedAt": "2025-11-03T10:00:00", "site": "LE-A",
                    "content": rdt.datenkranz("t0", submitted="2025-11-03", gfk1="2025-11-10", plan_date="2025-11-11")}]
        results, files = self.run_mm("rd", entries, "--rueckfragen-per-le")
        self.assertFalse([f for f in files if f.endswith("_rueckfragen.csv")])
        self.assertIn("rd_2025-Q4_rueckfragen_LE-A.csv", files)
        self.assertNotIn("problems", results["rd_2025-Q4"])        # so the xlsx has no Rückfragen of all LEs either
        results, files = self.run_mm("rd", entries)
        self.assertIn("rd_2025-Q4_rueckfragen.csv", files)
        self.assertTrue(results["rd_2025-Q4"]["problems"])


def spread(rows, seed):
    """The synthetic RD submissions of test_rd_evaluation_matrix, their initial submissions spread over five quarters"""
    rnd = random.Random(seed)
    out = []
    for r in rows:
        d = f"{rnd.choice(['2025-11', '2025-12', '2026-02', '2026-05', '2026-08', '2026-11'])}-0{rnd.randint(1, 9)}"
        content = json.loads(json.dumps(r.content))
        content["metaData"]["submission"]["date"] = d
        out.append(rd.SubmissionRow(r.case, f"{d}T10:00:00", content, r.site, r.reason))
    return out


def joint(rows, min_cell=5, scope=rd.SUPPRESS_DIAGNOSES):
    """The total and the quarterly RD matrices of one run"""
    starts = [date(2025, 10, 1), date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)]
    ranges = [(s, mm.next_period(s, mm.QUARTER)) for s in starts]
    return rd.run_joint(rows, [], rdt.HEADS, ranges, date(2026, 11, 24), min_cell, suppress_scope=scope,
                        start=ranges[0][0], end=ranges[-1][1])


def across_files(total, parts, full_total, full_parts):
    """
    Independently of the implementation: every count cell of the total matrix (per row, LE and Gesamt, value and base)
    is the sum of the same cell of the period matrices (a missing LE or row counts 0). Findings as text: a relation
    that doesn't hold in the unsuppressed matrices, exactly one suppressed cell, or a suppressed cell beside a side of
    shown zeros
    """
    hidden = f"<{total['min_cell']}"
    found = []

    def cell(result, key, entity, field_):
        row = next((r for r in result["rows"] if r["key"] == key), None)
        return None if row is None or entity not in row["values"] else row["values"][entity][field_]

    for r in full_total["rows"]:
        if r["unit"] != rd.COUNT:
            continue
        for e in full_total["entities"]:
            for f in ("value", "base"):
                true = r["values"][e][f]
                if true is None:
                    continue
                parts_true = [cell(p, r["key"], e, f) for p in full_parts]
                if r["section"] != rd.PROBLEMS_SECTION and true != sum(x or 0 for x in parts_true):
                    found.append(f"does not hold: {r['key']} {e} {f}")
                shown = [cell(total, r["key"], e, f)] + [c for c in (cell(p, r["key"], e, f) for p in parts) if c is not None]
                if shown.count(hidden) == 1:
                    found.append(f"one suppressed cell: {r['key']} {e} {f}: {shown}")
                for side, other in ((shown[:1], shown[1:]), (shown[1:], shown[:1])):
                    if hidden not in side and sum(side) == 0 and hidden in other:
                        found.append(f"suppressed beside shown zeros: {r['key']} {e} {f}: {shown}")
    return found


class JointSuppressionTests(unittest.TestCase):
    """
    The total RD matrix and its period matrices of one run, suppressed together: no suppressed cell follows from the
    total and the other periods (across the files), and within each file none from its own relations
    (recoverable() of the RD tests: rows over the LEs, partitions, the sum relations across sections)
    """

    def check(self, rows, scope, min_cell=5):
        full_total, full_parts = joint(rows, min_cell=1, scope=scope)
        total, parts = joint(rows, min_cell=min_cell, scope=scope)
        found = across_files(total, parts, full_total, full_parts)
        for full, result in zip([full_total] + full_parts, [total] + parts):
            found += rdt.recoverable(full, result)
        self.assertEqual(found, [], found[:3])
        return total, parts

    def test_random_sets_in_both_scopes(self):
        for seed in range(12):
            for scope in rd.SUPPRESS_SCOPES:
                with self.subTest(seed=seed, scope=scope):
                    self.check(spread(rdt.varied(seed), seed), scope)

    def test_another_threshold_and_larger_les(self):
        for seed in range(12, 18):
            with self.subTest(seed=seed):
                self.check(spread(rdt.varied(seed), seed), rd.SUPPRESS_DIAGNOSES, min_cell=3)
                self.check(spread(rdt.synthetic(seed=seed), seed), rd.SUPPRESS_DIAGNOSES)

    def test_a_period_is_not_the_exact_total_minus_the_other_periods(self):
        # LE B: 2 undiagnosed cases in 2025-Q4, 9 in 2026-Q1. Separately the total (11) is exact beside Q1 (9) and Q4 "<5"
        def r(le, i, d, assessment):
            return rd.SubmissionRow(f"{le}{i}", f"{d}T10:00:00", rdt.datenkranz(f"{le}{i}", submitted=d, assessment=assessment), le, None)
        rows = ([r("A", i, ["2025-11-03", "2026-02-03"][i % 2], list(rd.ASSESSMENT_STATUS)[i % 5]) for i in range(40)]
                + [r("B", i, "2025-11-05", "noGeneticDiagnosis") for i in range(2)]
                + [r("B", 10 + i, "2026-02-05", "noGeneticDiagnosis") for i in range(9)])
        total, parts = self.check(rows, rd.SUPPRESS_DIAGNOSES)
        status = "Diagnosestatus (alle Fälle)"
        cells = [rdt.get(x, status, rd.UNDIAGNOSED, "B") for x in [total] + parts[:2]]
        self.assertEqual(cells[1], "<5")
        self.assertGreaterEqual(cells.count("<5"), 2, cells)
        # separately suppressed, the total of B would show 11 beside Q1's 9: Q4 = 2
        alone = rd.run(rows, [], rdt.HEADS, date(2025, 10, 1), date(2026, 4, 1), date(2026, 11, 24), 5)
        self.assertEqual(rdt.get(alone, status, rd.UNDIAGNOSED, "B"), 11)


if __name__ == "__main__":
    unittest.main()
