"""Tests of monthly_matrices.py: python3 -m unittest discover -s scripts/monthly_matrices"""

import json
import os
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
import test_dnpm_evaluation_matrix as dnt   # noqa: E402  (synthetic oncology submissions)
import test_rd_evaluation_matrix as rdt     # noqa: E402  (synthetic Datenkranz submissions)


def export(path, initial_dates):
    """A JSON list of synthetic RD submissions, one case per initial submission date"""
    entries = [{"case": f"c{i}", "submittedAt": f"{d}T10:00:00", "site": "LE-A",
                "content": rdt.datenkranz(f"t{i}", submitted=d, gfk1="2024-01-10", plan_date="2024-02-10")}
               for i, d in enumerate(initial_dates)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f)


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

    def run_rd(self, dates, *args):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "export.json")
        export(path, dates)
        out = os.path.join(tmp.name, "out")
        with redirect_stderr(StringIO()):
            mm.main([path, "--matrix", "rd", "--reference-date", "2026-11-01", "--output-dir", out, *args])
        directory = os.path.join(out, "rd")
        results = {}
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json"):
                with open(os.path.join(directory, name), encoding="utf-8") as f:
                    results[name[:-len(".json")]] = json.load(f)
        return results

    def test_one_matrix_per_quarter_of_the_initial_submission(self):
        results = self.run_rd(["2025-11-30", "2025-12-31", "2026-01-01", "2026-03-31", "2026-04-01"])
        self.assertEqual(list(results), ["rd_2025-Q4", "rd_2026-Q1", "rd_2026-Q2"])     # nothing before 2025-Q4
        cases = {k: r["rows"][0]["values"]["Gesamt"]["value"] for k, r in results.items()}
        self.assertEqual(cases, {"rd_2025-Q4": 2, "rd_2026-Q1": 2, "rd_2026-Q2": 1})
        q4 = results["rd_2025-Q4"]
        self.assertEqual(q4["period"], "01.10.2025 - 31.12.2025")
        self.assertIn("Fälle mit Erstmeldung vom 2025-10-01 bis vor dem 2026-01-01.", q4["notes"])
        # the default scope: the case counts below 5 are shown
        self.assertEqual(q4["suppress_scope"], "diagnoses")

    def test_months_and_the_period_range(self):
        results = self.run_rd(["2025-11-30", "2025-12-31", "2026-01-01"], "--period", "month", "--until", "2026-01")
        self.assertEqual(list(results), ["rd_2025-11", "rd_2025-12"])
        self.assertIn("Fälle mit Erstmeldung vom 2025-11-01 bis vor dem 2025-12-01.", results["rd_2025-11"]["notes"])
        results = self.run_rd(["2025-11-30", "2026-05-01"], "--from", "2026-Q1", "--suppress-scope", "all")
        self.assertEqual(list(results), ["rd_2026-Q1", "rd_2026-Q2"])        # an empty quarter within is written
        self.assertEqual(results["rd_2026-Q2"]["suppress_scope"], "all")
        # all rows: the case count of the only LE is suppressed, and Gesamt with it (it is that LE's count)
        self.assertEqual(results["rd_2026-Q2"]["rows"][0]["values"]["Gesamt"]["value"], "<5")
        self.assertEqual(self.run_rd(["2026-05-01"])["rd_2026-Q2"]["rows"][0]["values"]["Gesamt"]["value"], 1)

    def test_the_oncology_matrices_take_the_scope(self):
        for args, scope, n in (((), "diagnoses", 3), (("--suppress-scope", "all"), "all", "<5")):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "export.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump([{"case": f"c{i}", "submittedAt": "2025-11-03T10:00:00", "site": "LE-A",
                                "content": dnt.datenkranz(f"t{i}", submitted="2025-11-03")} for i in range(3)], f)
                out = os.path.join(tmp, "out")
                with redirect_stderr(StringIO()):
                    mm.main([path, "--matrix", "dnpm", "--reference-date", "2026-11-01", "--output-dir", out, *args])
                with open(os.path.join(out, "dnpm", "dnpm_2025-Q4.json"), encoding="utf-8") as f:
                    result = json.load(f)
            self.assertEqual(result["suppress_scope"], scope)
            self.assertEqual(result["rows"][0]["values"]["Gesamt"]["value"], n)


if __name__ == "__main__":
    unittest.main()
