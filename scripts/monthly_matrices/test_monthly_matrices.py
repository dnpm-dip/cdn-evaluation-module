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
        self.assertTrue(any("Gemeinsam unterdrückt" in n for n in results["rd_gesamt"]["notes"]))
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
        results = self.run_rd(["2025-11-30", "2026-05-01"], "--from", "2026-Q1", "--suppress-scope", "all")
        self.assertEqual(list(results), ["rd_2026-Q1", "rd_2026-Q2", "rd_gesamt"])     # an empty quarter within is written
        self.assertEqual(results["rd_2026-Q2"]["suppress_scope"], "all")
        # all rows: the case count of the only LE is suppressed, and Gesamt with it (it is that LE's count)
        self.assertEqual(results["rd_2026-Q2"]["rows"][0]["values"]["Gesamt"]["value"], "<5")
        # a single case in the default scope: its case count is exact, only its code rows are suppressed
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


def across_files(total, parts, full_total, full_parts):
    """
    Independently of the implementation: every count cell of the total matrix (per row, LE and Gesamt, value and base)
    is the sum of the same cell of the period matrices (a missing LE or row counts 0). Findings as text: a relation
    that doesn't hold in the unsuppressed matrices, exactly one suppressed cell, or a suppressed cell beside a side of
    shown zeros, where that cell counts 1 to min_cell - 1 (see rdt.recoverable)
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
                present = [i for i, p in enumerate(parts) if cell(p, r["key"], e, f) is not None]
                shown = [cell(total, r["key"], e, f)] + [cell(parts[i], r["key"], e, f) for i in present]
                trues = [true] + [cell(full_parts[i], r["key"], e, f) for i in present]
                small = [s == hidden and 0 < t < total["min_cell"] for s, t in zip(shown, trues)]
                if shown.count(hidden) == 1 and any(small):
                    found.append(f"one suppressed cell: {r['key']} {e} {f}: {shown}")
                for side, other in (((0,), range(1, len(shown))), (range(1, len(shown)), (0,))):
                    if all(shown[i] != hidden for i in side) and sum(shown[i] for i in side) == 0 and any(small[i] for i in other):
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
        for seed in range(6):
            for scope in rd.SUPPRESS_SCOPES:
                with self.subTest(seed=seed, scope=scope):
                    self.check(spread(rdt.varied(seed), seed), scope)

    def test_another_threshold_and_larger_les(self):
        for seed in range(12, 15):
            with self.subTest(seed=seed):
                self.check(spread(rdt.varied(seed), seed), rd.SUPPRESS_CODES, min_cell=3)
                self.check(spread(rdt.synthetic(seed=seed), seed), rd.SUPPRESS_CODES)

    def test_a_period_is_not_the_exact_total_minus_the_other_periods(self):
        # LE B: 2 undiagnosed neuromuscular cases in 2025-Q4, 9 in 2026-Q1. Separately the total (11) is exact beside
        # Q1 (9) and Q4 "<5"
        def r(le, i, d, assessment, icd10="Q87.4"):
            return rd.SubmissionRow(f"{le}{i}", f"{d}T10:00:00", rdt.datenkranz(f"{le}{i}", submitted=d, assessment=assessment,
                                                                               icd10=icd10), le, None)
        rows = ([r("A", i, ["2025-11-03", "2026-02-03"][i % 2], list(rd.ASSESSMENT_STATUS)[i % 5]) for i in range(40)]
                + [r("B", i, "2025-11-05", "noGeneticDiagnosis", "G71.0") for i in range(2)]
                + [r("B", 10 + i, "2026-02-05", "noGeneticDiagnosis", "G71.0") for i in range(9)])
        total, parts = self.check(rows, rd.SUPPRESS_CODES)
        block, item = "Erkrankungsgruppen gesamt", f"{rd.NEUROMUSCULAR}: {rd.UNDIAGNOSED}"
        cells = [rdt.get(x, block, item, "B") for x in [total] + parts[:2]]
        self.assertEqual(cells[1], "<5")
        self.assertGreaterEqual(cells.count("<5"), 2, cells)
        # the diagnosis status is exact (2 in Q4)
        self.assertEqual(rdt.get(parts[0], "Diagnosestatus (alle Fälle)", rd.UNDIAGNOSED, "B"), 2)
        # separately suppressed, the total of B would show 11 beside Q1's 9: Q4 = 2
        alone = rd.run(rows, [], rdt.HEADS, date(2025, 10, 1), date(2026, 4, 1), date(2026, 11, 24), 5)
        self.assertEqual(rdt.get(alone, block, item, "B"), 11)


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


class JointProtectionTests(unittest.TestCase):
    """
    The closure closes every single relation, not systems of them; the suppression is therefore extended against the
    whole linear system of all files (rd.protect) until no suppressed count follows. Checked with an independent exact
    solver over the published numbers (rdt.derivable_cells): the counterexamples of the review are closed, random sets
    (also with tiny LEs) end with no computable cell, and where one is forced (the closure alone) nothing is written.
    """

    def files(self, rows, scope, ranges=None, min_cell=5, extend=True):
        ranges = ranges or [(s, mm.next_period(s, mm.QUARTER)) for s in
                            (date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1), date(2026, 10, 1))]
        original = rd.suppressed_files

        def run(m, extend_):
            with mock.patch.object(rd, "suppressed_files", lambda *a, **k: original(*a, **{**k, "extend": extend_})):
                return rd.run_joint(rows, [], rdt.HEADS, ranges, date(2026, 12, 31), m, suppress_scope=scope)
        (t, ps), (ft, fps) = run(min_cell, extend), run(1, True)
        return [t] + ps, [ft] + fps

    def reported(self, results):
        return {(n,) + tuple(c) for n, r in enumerate(results) for c in r["derivable"]}

    def independent(self, results, fulls):
        import ast
        return {ast.literal_eval(f.split(": ", 1)[1]) for f in rdt.derivable_cells(list(zip(fulls, results)))
                if f.startswith("follows")}

    def closed(self, results, fulls):
        self.assertEqual(self.reported(results), set())
        self.assertEqual(self.independent(results, fulls), set())
        self.assertEqual(across_files(results[0], results[1:], fulls[0], fulls[1:]), [])
        for full, result in zip(fulls, results):
            self.assertEqual(rdt.recoverable(full, result), [])

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

    def test_a_system_of_relations_no_longer_gives_a_cell_away(self):
        # review (Codex, Kimi): quarters (A, B, C, D): (3,3,1,0), (3,3,0,0), (0,0,3,3), (0,0,3,3), all suppressed in the
        # LE columns; with the closure alone the quarter totals, the totals of A and B and the zeros give C in Q1 = 1
        rows = self.quarters([(3, 3, 1, 0), (3, 3, 0, 0), (0, 0, 3, 3), (0, 0, 3, 3)])
        results, fulls = self.files(rows, rd.SUPPRESS_ALL, extend=False)
        self.assertTrue(self.reported(results))
        self.assertLessEqual(self.independent(results, fulls), self.reported(results))
        self.closed(*self.files(rows, rd.SUPPRESS_ALL))

    def test_one_small_le_per_quarter_stays_within_the_code_rows(self):
        # one LE, three undiagnosed cases with GFK2 in each of two quarters (review, Codex 1b, for the former scope):
        # the status rows are exact now; the code rows of A and Gesamt (the same numbers) are protected within the code
        # rows, if need be as a whole block, and every other row stays exact
        ranges = [(date(2026, 1, 1), date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 7, 1))]
        rows = self.quarters([(3,), (3,)], "noGeneticDiagnosis")
        results, fulls = self.files(rows, rd.SUPPRESS_CODES, ranges)
        self.closed(results, fulls)
        self.only_code_rows(results, fulls)
        self.assertEqual(rdt.get(results[1], "Diagnoserate (abgeschlossene Fälle)", rd.UNDIAGNOSED, "A"), 3)
        self.assertEqual(rdt.get(results[1], "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)", "A"), 3)

    def only_code_rows(self, results, fulls):
        """Every suppressed cell is a code cell, every other one exact"""
        for result, full in zip(results, fulls):
            for r, f in zip(result["rows"], full["rows"]):
                for e in result["entities"]:
                    if not rdt.code_row(r):
                        self.assertEqual(r["values"][e], f["values"][e], (r["key"], e))
                    for field in ("value", "base"):
                        if r["values"][e][field] == f"<{result['min_cell']}":
                            self.assertTrue(rdt.code_cell(r, field), (r["key"], e, field))

    def test_seven_cases_at_two_les(self):
        rows = rdt.synthetic(seed=0, sizes=(("A", 2), ("B", 5)))
        rnd = random.Random(0)
        for r in rows:
            r.content["metaData"]["submission"]["date"] = f"{rnd.choice(['2025-11', '2025-12', '2026-02', '2026-05'])}-0{rnd.randint(1, 9)}"
        ranges = [(s, mm.next_period(s, mm.QUARTER)) for s in (date(2025, 10, 1), date(2026, 1, 1), date(2026, 4, 1))]
        for scope in rd.SUPPRESS_SCOPES:
            with self.subTest(scope=scope):
                results, fulls = self.files(rows, scope, ranges, extend=False)
                self.assertLessEqual(self.independent(results, fulls), self.reported(results))
                results, fulls = self.files(rows, scope, ranges)
                self.closed(results, fulls)
                if scope == rd.SUPPRESS_CODES:
                    self.only_code_rows(results, fulls)

    def test_random_sets_end_with_no_computable_cell(self):
        sets = [(f"varied {seed}", spread(rdt.varied(seed), seed)) for seed in range(6)]
        sets += [(f"tiny {seed}", spread(rdt.synthetic(seed=seed, sizes=(("LE-A", 3), ("LE-B", 1), ("LE-C", 2))), seed))
                 for seed in range(40, 43)]
        for name, rows in sets:
            for scope in rd.SUPPRESS_SCOPES:
                with self.subTest(set=name, scope=scope):
                    total, parts = joint(rows, scope=scope)
                    full_total, full_parts = joint(rows, min_cell=1, scope=scope)
                    self.closed([total] + parts, [full_total] + full_parts)
                    if scope == rd.SUPPRESS_CODES:
                        self.only_code_rows([total] + parts, [full_total] + full_parts)

    def test_a_forced_computable_cell_blocks_the_output(self):
        entries = [{"case": r.case, "submittedAt": r.submitted_at, "content": r.content, "site": r.site}
                   for r in self.quarters([(3, 3, 1, 0), (3, 3, 0, 0), (0, 0, 3, 3), (0, 0, 3, 3)])]
        original = rd.suppressed_files
        with tempfile.TemporaryDirectory() as tmp:
            path, out = os.path.join(tmp, "export.json"), os.path.join(tmp, "out")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entries, f)
            args = [path, "--reference-date", "2026-12-31", "--output-dir", out, "--suppress-scope", "all"]
            with redirect_stderr(StringIO()):
                mm.main(args + ["--matrix", "rd"])
            before = sorted(os.listdir(os.path.join(out, "rd")))
            self.assertIn("rd_gesamt.json", before)
            with mock.patch.object(rd, "suppressed_files", lambda *a, **k: original(*a, **{**k, "extend": False})):
                for kind in ("rd", "both"):
                    with self.assertRaises(SystemExit) as raised, redirect_stderr(StringIO()):
                        mm.main(args + ["--matrix", kind, "--replace"])
                    self.assertIn("Ausgabe gesperrt", str(raised.exception.code))
            # nothing written, nothing of the earlier run deleted, no oncology either
            self.assertEqual(sorted(os.listdir(os.path.join(out, "rd"))), before)
            self.assertFalse(os.path.exists(os.path.join(out, "dnpm")))
            self.assertFalse([f for f in before if "audit" in f])

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
