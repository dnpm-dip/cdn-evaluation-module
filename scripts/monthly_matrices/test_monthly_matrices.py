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
            files = self.main(tmp, entries)
            self.assertIn("rd_gesamt.json", files)
            with open(os.path.join(tmp, "out", "rd", "rd_gesamt.json"), encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(rdt.get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)"), 2)
        self.assertEqual(rdt.get(result, "Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)"), 1)


class JointAuditTests(unittest.TestCase):
    """
    The closure closes every single relation, not systems of them; the audit (rd.derivable) finds the suppressed counts
    that follow from the whole linear system of all files and reports them (result["derivable"], *_audit.csv, never in
    the JSON, CSV or xlsx). Checked against an independent solver over the published numbers (rdt.derivable_cells).
    """

    def files(self, rows, scope, ranges=None, min_cell=5):
        ranges = ranges or [(s, mm.next_period(s, mm.QUARTER)) for s in
                            (date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1))]
        run = lambda m: rd.run_joint(rows, [], rdt.HEADS, ranges, date(2026, 12, 31), m, suppress_scope=scope)
        (t, ps), (ft, fps) = run(min_cell), run(1)
        return [t] + ps, [ft] + fps

    def reported(self, results):
        return {(n,) + tuple(c) for n, r in enumerate(results) for c in r["derivable"]}

    def independent(self, results, fulls):
        import ast
        return {ast.literal_eval(f.split(": ", 1)[1]) for f in rdt.derivable_cells(list(zip(fulls, results)))
                if f.startswith("follows")}

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

    def test_a_system_of_relations_gives_a_cell_away_and_is_reported(self):
        # quarters (A, B, C, D): (3,3,1,0), (3,3,0,0), (0,0,3,3), (0,0,3,3), all suppressed in the LE columns; the
        # quarter totals, the totals of A and B and the zeros give C in Q1 = 7 + 6 - 6 - 6 = 1, although every single
        # relation has two suppressed cells
        results, fulls = self.files(self.quarters([(3, 3, 1, 0), (3, 3, 0, 0), (0, 0, 3, 3), (0, 0, 3, 3)]), rd.SUPPRESS_ALL)
        reported = self.reported(results)
        self.assertTrue(reported)
        self.assertLessEqual(self.independent(results, fulls), reported)
        # the single-relation checks find nothing: the closure itself is as before
        self.assertEqual(across_files(results[0], results[1:], fulls[0], fulls[1:]), [])

    def test_open_cases_are_known_to_be_zero_where_every_case_has_a_gfk2(self):
        # one LE, three undiagnosed cases with GFK2 in each of two quarters: "noch keine GFK2" is exact and 0, so the
        # open cases are 0 and the closed cases (3) follow from the exact sequenced cases
        results, fulls = self.files(self.quarters([(3,), (3,)], "noGeneticDiagnosis"), rd.SUPPRESS_DIAGNOSES,
                                    ranges=[(date(2026, 1, 1), date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 7, 1))])
        self.assertIn((1, "A", "Diagnoserate (abgeschlossene Fälle)", rd.DIAGNOSED, "base"), self.reported(results))
        self.assertLessEqual(self.independent(results, fulls), self.reported(results))

    def test_seven_cases_at_two_les(self):
        rows = rdt.synthetic(seed=0, sizes=(("A", 2), ("B", 5)))
        rnd = random.Random(0)
        for r in rows:
            r.content["metaData"]["submission"]["date"] = f"{rnd.choice(['2025-11', '2025-12', '2026-02', '2026-05'])}-0{rnd.randint(1, 9)}"
        ranges = [(s, mm.next_period(s, mm.QUARTER)) for s in (date(2025, 10, 1), date(2026, 1, 1), date(2026, 4, 1))]
        results, fulls = self.files(rows, rd.SUPPRESS_DIAGNOSES, ranges)
        self.assertIn((0, "A", "Diagnosestatus (alle Fälle)", rd.UNCLEAR, "value"), self.reported(results))
        self.assertLessEqual(self.independent(results, fulls), self.reported(results))

    def test_the_audit_agrees_with_the_independent_solver_on_random_sets(self):
        for seed in range(4):
            for scope in rd.SUPPRESS_SCOPES:
                with self.subTest(seed=seed, scope=scope):
                    total, parts = joint(spread(rdt.varied(seed), seed), scope=scope)
                    full_total, full_parts = joint(spread(rdt.varied(seed), seed), min_cell=1, scope=scope)
                    results, fulls = [total] + parts, [full_total] + full_parts
                    self.assertLessEqual(self.independent(results, fulls), self.reported(results))

    def test_the_audit_is_written_apart_from_the_shared_files(self):
        results, _ = self.files(self.quarters([(3, 3, 1, 0), (3, 3, 0, 0), (0, 0, 3, 3), (0, 0, 3, 3)]), rd.SUPPRESS_ALL)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "m")
            rd.write_json(f"{out}.json", results[1])
            rd.write_csv(f"{out}.csv", results[1])
            self.assertEqual(rd.write_audit(f"{out}_audit.csv", results[1]), f"{out}_audit.csv")
            with open(f"{out}.json", encoding="utf-8") as f:
                self.assertNotIn("derivable", json.load(f))
            self.assertIn("WARNUNG", rd.audit_message("Q1", results[1]))
        self.assertIsNone(rd.audit_message("x", {**results[1], "derivable": []}))


class NotAdditiveTests(unittest.TestCase):
    """Conversion failures and episodes without initial submission have no date: every matrix of a run counts all of
    them, so they are equal in the total and each period (rd.NOT_ADDITIVE), not a sum"""

    def test_losses_are_equal_in_every_file_and_suppressed_alike(self):
        def r(case, d, type_="initial", le="A"):
            return rd.SubmissionRow(case, f"{d}T10:00:00", rdt.datenkranz(case, submitted=d, type_=type_), le, None)
        rows = [r(f"a{i}", ["2026-02-03", "2026-05-03"][i % 2]) for i in range(20)] + [r("x", "2026-02-04", "followup")]
        rows += [r(f"b{i}", ["2026-02-03", "2026-05-03"][i % 2], le="B") for i in range(20)]
        failures = [rd.Failure("A", "f1", "tan", "error"), rd.Failure("B", "f2", "tan", "error"), rd.Failure("B", "f3", "tan", "error")]
        ranges = [(date(2026, 1, 1), date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 7, 1))]
        for min_cell in (1, 5):
            total, parts = rd.run_joint(rows, failures, rdt.HEADS, ranges, date(2026, 12, 31), min_cell, suppress_scope=rd.SUPPRESS_ALL)
            for section, item in rd.NOT_ADDITIVE:
                for le in ("A", "B"):
                    cells = [rdt.get(x, section, item, le) for x in [total] + parts]
                    self.assertEqual(len(set(map(str, cells))), 1, (section, item, le, cells))
            if min_cell == 1:
                self.assertEqual(rdt.get(total, "Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)", "A"), 1)

    def test_the_total_must_cover_exactly_the_periods(self):
        ranges = [(date(2026, 1, 1), date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 7, 1))]
        for kwargs in ({"start": date(2025, 1, 1)}, {"end": date(2027, 1, 1)}):
            with self.assertRaises(ValueError):
                rd.run_joint([], [], rdt.HEADS, ranges, date(2026, 12, 31), **kwargs)
        with self.assertRaises(ValueError):
            rd.run_joint([], [], rdt.HEADS, [ranges[1], ranges[0]], date(2026, 12, 31))


if __name__ == "__main__":
    unittest.main()
