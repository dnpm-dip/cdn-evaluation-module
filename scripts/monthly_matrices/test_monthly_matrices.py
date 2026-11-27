"""Tests of monthly_matrices.py: python3 -m unittest discover -s scripts/monthly_matrices"""

import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from types import SimpleNamespace
from io import StringIO
from unittest import mock

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
        dates = ["2025-11-30", "2025-12-31", "2026-01-01", "2026-03-31", "2026-04-01"]
        results = self.run_rd(dates, "--min-cell", "1")
        self.assertEqual(list(results), ["rd_2025-Q4", "rd_2026-Q1", "rd_2026-Q2", "rd_gesamt"])   # nothing before 2025-Q4
        cases = {k: r["rows"][0]["values"]["Gesamt"]["value"] for k, r in results.items()}
        self.assertEqual(cases, {"rd_2025-Q4": 2, "rd_2026-Q1": 2, "rd_2026-Q2": 1, "rd_gesamt": 5})
        q4 = results["rd_2025-Q4"]
        self.assertEqual(q4["period"], "01.10.2025 - 31.12.2025")
        self.assertIn("Fälle mit Erstmeldung vom 2025-10-01 bis vor dem 2026-01-01.", q4["notes"])
        # the total covers exactly the quarters
        self.assertEqual(results["rd_gesamt"]["period"], "01.10.2025 - 30.06.2026")
        self.assertFalse(any("Gemeinsam unterdrückt" in n for n in results["rd_gesamt"]["notes"]))     # each on its own
        # the default scope codes: only the rows derived from the diagnosis codes are suppressed, the case counts and
        # the diagnosis status of the only LE are exact
        q4 = self.run_rd(dates)["rd_2025-Q4"]
        self.assertEqual(q4["suppress_scope"], "codes")
        self.assertNotIn("derivable", q4)
        self.assertEqual(rdt.get(q4, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)", "LE-A"), 2)
        self.assertEqual(rdt.get(q4, "Diagnosestatus (alle Fälle)", rd.DIAGNOSED, "LE-A"), 2)
        # (no --orphanet: no disease groups; the Orphacode rows are 0, so nothing is suppressed at all)
        hidden = {(r["section"], r["item"]) for r in q4["rows"] for v in r["values"].values() if "<5" in (v["value"], v["base"])}
        self.assertEqual({k for k in hidden if not rd.is_code_row(SimpleNamespace(section=k[0], item=k[1]))}, set())
        # the former scope is refused with a hint
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()) as err:
            mm.main(["missing.json", "--suppress-scope", "diagnoses"])
        self.assertIn("replaced by 'codes'", err.getvalue())

    def test_oncology_periods_end_with_the_cutoff(self):
        # an initial submission after --cutoff is dropped by the evaluation, so it opens no period either
        entries = [{"case": c, "site": "LE-A", "submittedAt": f"{d}T10:00:00", "content": dnt.datenkranz(c, submitted=d)}
                   for c, d in (("a", "2026-03-10"), ("b", "2026-10-10"))]
        results, _ = self.run_mm("dnpm", entries, "--cutoff", "2026-06-30", "--reference-date", "2026-06-30")
        self.assertEqual(list(results), ["dnpm_2026-Q1", "dnpm_gesamt"])

    def test_rd_only_per_quarter_and_the_period_range(self):
        # RD (SE) matrices only per quarter: --period month is refused for rd and both, before anything is read
        for kind in ("rd", "both"):
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                mm.main(["missing.json", "--matrix", kind, "--period", "month"])
        results = self.run_rd(["2025-11-30", "2025-12-31", "2026-01-01"], "--until", "2026-Q1", "--min-cell", "1")
        self.assertEqual(list(results), ["rd_2025-Q4", "rd_gesamt"])
        self.assertEqual(results["rd_gesamt"]["rows"][0]["values"]["Gesamt"]["value"], 2)
        results = self.run_rd(["2025-11-30", "2026-05-01"], "--from", "2026-Q1")
        self.assertEqual(list(results), ["rd_2026-Q1", "rd_2026-Q2", "rd_gesamt"])     # an empty quarter within is written
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            mm.main(["missing.json", "--suppress-scope", "all"])        # removed (27.11.2026)
        # a single case: its case count is exact, only its code rows are suppressed
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


def joint(rows, min_cell=5, scope=rd.SUPPRESS_CODES):
    """The total and the quarterly RD matrices of one run"""
    starts = [date(2025, 10, 1), date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1)]
    ranges = [(s, mm.next_period(s, mm.QUARTER)) for s in starts]
    return rd.run_joint(rows, [], rdt.HEADS, ranges, date(2026, 11, 24), min_cell, suppress_scope=scope,
                        start=ranges[0][0], end=ranges[-1][1])


class OutputTests(unittest.TestCase):
    """Reruns into the same directory, colliding LE file names, an initial submission without date"""

    def entries(self, dates, site="LE-A"):
        return [{"case": f"c{site}{i}", "submittedAt": f"{d}T10:00:00", "site": site,
                 "content": rdt.datenkranz(f"t{i}", submitted=d, gfk1="2025-11-10", plan_date="2025-11-11")}
                for i, d in enumerate(dates)]

    def main(self, tmp, entries, *args):
        path = os.path.join(tmp, "export.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        with redirect_stderr(StringIO()):
            mm.main([path, "--matrix", "rd", "--reference-date", "2026-11-01", "--output-dir", os.path.join(tmp, "out"), *args])
        return sorted(os.listdir(os.path.join(tmp, "out", "rd")))

    def test_a_second_run_into_the_same_directory_is_refused_or_replaces_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = self.main(tmp, self.entries(["2025-11-03", "2026-02-03"]))
            self.assertIn("rd_2025-Q4_rueckfragen.csv", files)
            with self.assertRaises(SystemExit):
                self.main(tmp, self.entries(["2026-02-03"]), "--rueckfragen-per-le")
            files = self.main(tmp, self.entries(["2026-02-03"]), "--rueckfragen-per-le", "--replace")
            self.assertFalse([f for f in files if "2025-Q4" in f or f.endswith("_rueckfragen.csv")], files)
            with open(os.path.join(tmp, "out", "rd", "rd_2026-Q1.json"), encoding="utf-8") as f:
                self.assertNotIn("problems", json.load(f))

    def test_les_with_the_same_file_name_are_refused(self):
        with self.assertRaises(SystemExit):
            rd.per_le_paths("m", ["LE A", "LE_A"])
        with self.assertRaises(SystemExit):
            dnt.dm.per_le_paths("m", ["le-a", "LE-A"])
        self.assertEqual(len(rd.per_le_paths("m", ["LE-A", "LE-B"])), 2)

    def test_an_initial_submission_without_date_doesnt_abort_the_run(self):
        entries = self.entries(["2025-11-03", "2026-02-03", "2026-02-04"])
        del entries[2]["content"]["metaData"]["submission"]["date"]
        with tempfile.TemporaryDirectory() as tmp:
            files = self.main(tmp, entries, "--min-cell", "1")
            self.assertIn("rd_gesamt.json", files)
            with open(os.path.join(tmp, "out", "rd", "rd_gesamt.json"), encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(rdt.get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)"), 2)
        self.assertEqual(rdt.get(result, "Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)"), 1)


class WriteTests(unittest.TestCase):
    """Each RD file of a run suppressed on its own (primary only), written without blocking"""

    def test_each_file_primary_only_on_random_sets(self):
        check = rdt.PrimarySuppressionTests("test_gesamt_and_the_status_rows_exact")
        hidden = 0
        for seed in range(4):
            rows = spread(rdt.varied(seed), seed)
            total, parts = joint(rows)
            full_total, full_parts = joint(rows, min_cell=1)
            for full, result in zip([full_total] + full_parts, [total] + parts):
                with self.subTest(seed=seed, period=result["period"]):
                    hidden += check.check(full, result)
                    self.assertNotIn("derivable", result)
        self.assertGreater(hidden, 0)

    def test_the_command_line_writes_every_file(self):
        entries = [{"case": r.case, "submittedAt": r.submitted_at, "content": r.content, "site": r.site}
                   for r in self.quarters([(3, 3, 1, 0), (3, 3, 0, 0), (0, 0, 3, 3), (0, 0, 3, 3)])]
        with tempfile.TemporaryDirectory() as tmp:
            path, out = os.path.join(tmp, "export.json"), os.path.join(tmp, "out")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entries, f)
            with redirect_stderr(StringIO()):
                mm.main([path, "--reference-date", "2026-12-31", "--output-dir", out, "--rueckfragen-per-le"])
            names = sorted(os.listdir(os.path.join(out, "rd")))
            self.assertEqual([n for n in names if n.endswith(".json")],
                             ["rd_2026-Q1.json", "rd_2026-Q2.json", "rd_2026-Q3.json", "rd_2026-Q4.json", "rd_gesamt.json"])
            self.assertTrue(os.path.exists(os.path.join(out, "dnpm")))
            with open(os.path.join(out, "rd", "rd_2026-Q1.json"), encoding="utf-8") as f:
                result = json.load(f)
            self.assertEqual(result["suppressed"]["complementary"], 0)
            self.assertTrue(any("keine Sekundärunterdrückung" in n for n in result["notes"]))

    @staticmethod
    def quarters(counts, assessment="confirmedGeneticDiagnosis"):
        rows = []
        for qi, cs in enumerate(counts):
            for le, n in zip("ABCD", cs):
                for i in range(n):
                    d = f"2026-{3 * qi + 2:02d}-05"
                    rows.append(rd.SubmissionRow(f"{le}{qi}{i}", f"{d}T10:00:00",
                                                 rdt.datenkranz(f"{le}{qi}{i}", submitted=d, assessment=assessment), le, None))
        return rows

    def test_a_failed_write_removes_the_files_of_the_run(self):
        entries = [{"case": r.case, "submittedAt": r.submitted_at, "content": r.content, "site": r.site}
                   for r in self.quarters([(3, 3, 1, 0), (3, 3, 0, 0)])]
        original = rd.write_outputs
        calls = []

        def failing(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk full")
            return original(*a, **k)
        with tempfile.TemporaryDirectory() as tmp:
            path, out = os.path.join(tmp, "export.json"), os.path.join(tmp, "out")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entries, f)
            with mock.patch.object(rd, "write_outputs", failing), self.assertRaises(OSError), redirect_stderr(StringIO()):
                mm.main([path, "--matrix", "rd", "--reference-date", "2026-12-31", "--output-dir", out])
            self.assertEqual(os.listdir(os.path.join(out, "rd")), [])


class RunJointTests(unittest.TestCase):

    def test_the_total_must_cover_exactly_the_periods(self):
        ranges = [(date(2026, 1, 1), date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 7, 1))]
        for kwargs in ({"start": date(2025, 1, 1)}, {"end": date(2027, 1, 1)}):
            with self.assertRaises(ValueError):
                rd.run_joint([], [], rdt.HEADS, ranges, date(2026, 12, 31), **kwargs)
        with self.assertRaises(ValueError):
            rd.run_joint([], [], rdt.HEADS, [ranges[1], ranges[0]], date(2026, 12, 31))


if __name__ == "__main__":
    unittest.main()
