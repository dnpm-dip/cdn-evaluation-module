"""
Tests of rd_time_series.py and plot_time_series.py: python3 -m unittest discover scripts/rd_evaluation_matrix
(those of the plots are skipped without matplotlib)
"""

import contextlib
import importlib.util
import io
import json
import os
import random
import subprocess
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

import rd_evaluation_matrix as rdm
import rd_time_series as ts
from test_rd_evaluation_matrix import REFERENCE, broad_consent, datenkranz, row, synthetic

try:
    import plot_time_series as plots
except ImportError:     # matplotlib isn't installed: the plots aren't tested
    plots = None

OTHER_CODE = "2.16.840.1.113883.3.1937.777.24.5.3.99"   # not a research provision
PATDAT_STORE_AND_USE = "2.16.840.1.113883.3.1937.777.24.5.3.1"
REFUSES = "patient refuses to sign consent"


def run(rows, start=None, end=None, failures=(), min_cell=1, resolution=ts.MONTH, due_days=None, scope=rdm.SUPPRESS_ALL):
    """The time series; suppression in all blocks unless scope says otherwise (the tests of the rules of the suppression)"""
    return ts.run(rows, list(failures), start, end, REFERENCE, min_cell, resolution, due_days, suppress_scope=scope)


def get(result, block, item, period=ts.WHOLE, le=rdm.TOTAL, field="value"):
    """A value of the result, by block (or its start), item, period and LE"""
    matches = [r for r in result["rows"]
               if r["block"].startswith(block) and r["item"] == item and r["period"] == period and r["le"] == le]
    if len(matches) != 1:
        raise KeyError(f"{block} | {item} | {period} | {le}: {len(matches)} rows")
    return matches[0][field]


def matrix_value(matrix, section, item, entity, field="value"):
    """A value of a result of rd_evaluation_matrix.run"""
    (found,) = [r for r in matrix["rows"] if r["section"] == section and r["item"] == item]
    return found["values"][entity][field]


def cases(*contents, **kwargs):
    return run([row(str(i), c) for i, c in enumerate(contents)], **kwargs)


def main(*args):
    """Runs the script (by month unless --period is given; the default is quarter), returns the notes it prints"""
    notes = io.StringIO()
    with contextlib.redirect_stderr(notes):
        ts.main(list(args) if "--period" in args else ["--period", ts.MONTH, *args])
    return notes.getvalue()


def without_consent(tan, *reasons, **kwargs):
    """A submission without broad consent, with researchConsents entries of (presentationDate, noScopeJustification)"""
    content = datenkranz(tan, **kwargs)
    content["metaData"]["researchConsents"] = [{"presentationDate": d, "noScopeJustification": r} for d, r in reasons]
    return content


def submission_series(key, *submissions, site=None):
    """The rows of an episode, submitted at their submission dates"""
    return [row(key, s, s["metaData"]["submission"]["date"] + "T10:00:00", site) for s in submissions]


def follow_up(day):
    return {"followUpDate": day, "vitalStatus": "living"}


def quartile(r):
    """A quartile line of the timeline: a statistic as the value"""
    return r["block"] == ts.B_TIMELINE and r["item"].endswith("(Tage)")


def mixed_rows():
    """Cases of two LEs over four months with consents, all statuses, follow-ups, a test and an episode without initial"""
    a, b = "LE1", "LE2"
    return (
        submission_series("a1", datenkranz("t01", submitted="2025-11-30", consents=[broad_consent()]), site=a)
        + submission_series("a2", datenkranz("t02", submitted="2025-12-31", consents=[broad_consent(kind="deny")],
                                             assessment="noGeneticDiagnosis"), site=a)
        + submission_series("a3", datenkranz("t03", submitted="2026-01-01", assessment="noGeneticDiagnosis", plan_date=None),
                            datenkranz("t04", type_="followup", submitted="2026-02-20", plan_date="2026-02-10",
                                       assessment="partialGeneticDiagnosis", consents=[broad_consent()]), site=a)
        + submission_series("a4", datenkranz("t05", submitted="2026-01-15", consents=[broad_consent(start="2019-01-01")]),
                            datenkranz("t06", type_="followup", submitted="2026-02-01", follow_up=follow_up("2026-01-20"),
                                       consents=[broad_consent(start="2019-01-01")]), site=a)
        + submission_series("a5", datenkranz("t07", type_="test", submitted="2026-01-16"), site=a)
        + submission_series("a6", datenkranz("t08", type_="addition", submitted="2026-02-02"), site=a)   # no initial
        + submission_series("b1", datenkranz("t09", submitted="2025-12-01", assessment="suspectedGeneticDiagnosis",
                                             submitter="260000002"), site=b)
        + submission_series("b2", without_consent("t10", ("2025-01-01", REFUSES), submitted="2026-02-28",
                                                  assessment="noGeneticDiagnosis", plan_date=None, submitter="260000002"), site=b)
        + submission_series("b3", datenkranz("t11", submitted="2026-02-03", consents=[broad_consent()], plan_date="2028-01-01",
                                             submitter="260000002"), site=b)   # impossible GFK2
    )


class ReconciliationTests(unittest.TestCase):

    FAILURES = [("LE1", "2026-01-05T08:00:00"), ("LE2", "2025-12-24T08:00:00")]

    def setUp(self):
        failures = [rdm.Failure(site, "x", "t99", "conversion failed") for site, _ in self.FAILURES]
        self.matrix = rdm.run(mixed_rows(), failures, None, None, None, REFERENCE, min_cell=1)
        self.series = run(mixed_rows(), failures=self.FAILURES)

    def test_the_whole_period_equals_the_matrix(self):
        self.assertEqual(self.series["entities"], self.matrix["entities"])
        self.assertEqual(self.series["labels"], self.matrix["labels"])
        same = [   # (section and item of the matrix, block and item of the time series)
            (("Fälle", ts.CASES), (ts.B_CASES, ts.CASES)),
            (("Fälle", "Fälle nicht auswertbar (nicht in den Fällen)"), (ts.B_CASES, ts.UNREADABLE)),
            (("Fälle", "Einreichungen nicht konvertierbar (nicht in den Fällen)"), (ts.B_SUBMISSIONS, ts.NOT_CONVERTED)),
            (("Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)"), (ts.B_SUBMISSIONS, ts.WITHOUT_INITIAL)),
            (("Einwilligung", ts.CONSENT_VALID), (ts.B_CONSENT, ts.CONSENT_VALID)),
            (("Füllgrad optionaler Felder (Fälle)", "followUp"), (ts.B_FOLLOW_UP, ts.FOLLOW_UP_CONTENT)),
            (("Bearbeitungsdauer medizinisch (GFK1 bis GFK2)", "nicht berechenbar: noch keine GFK2"), None),
        ]
        same += [((ts.B_STATUS, r["item"]), (ts.B_STATUS, r["item"])) for r in self.matrix["rows"] if r["section"] == ts.B_STATUS]
        same += [((ts.B_RATE, s), (ts.B_RATE, s)) for s in rdm.STATUSES]
        self.assertEqual(len(same), 14)
        for entity in self.matrix["entities"]:
            for (section, item), series in same:
                expected = [matrix_value(self.matrix, section, item, entity, f) for f in ("value", "base", "percent")]
                if series is None:   # the matrix counts the cases without GFK2, the time series those with
                    total = matrix_value(self.matrix, "Fälle", ts.CASES, entity)
                    self.assertEqual(get(self.series, ts.B_FOLLOW_UP, ts.WITH_GFK2, le=entity), total - expected[0])
                else:
                    found = [get(self.series, *series, le=entity, field=f) for f in ("value", "base", "percent")]
                    self.assertEqual(found, expected, (entity, section, item))
        # 7 cases, 3 with a valid consent, of 2 LEs
        self.assertEqual([get(self.series, ts.B_CASES, ts.CASES, le=e) for e in self.series["entities"]], [7, 4, 3])
        self.assertEqual(get(self.series, ts.B_CONSENT, ts.CONSENT_VALID, field="percent"), 42.9)
        self.assertIn("1 Testeinreichungen verworfen.", self.matrix["notes"])
        self.assertEqual(get(self.series, ts.B_SUBMISSIONS, "Einreichungen test"), 1)

    def test_the_periods_add_up_to_the_whole_period(self):
        periods = self.series["periods"]
        self.assertEqual(periods, ["2025-11", "2025-12", "2026-01", "2026-02", ts.WHOLE])
        sums, whole = {}, {}
        for r in self.series["rows"]:
            key = (r["le"], r["block"], r["item"])
            if quartile(r):
                continue    # quartiles don't add up
            for field in ("value", "base"):
                if r[field] is not None:
                    target = whole if r["period"] == ts.WHOLE else sums
                    target[key + (field,)] = target.get(key + (field,), 0) + r[field]
        self.assertEqual(sums, whole)
        self.assertGreater(len(whole), 100)
        # and the LEs to the total
        for r in self.series["rows"]:
            if r["le"] == rdm.TOTAL and r["value"] is not None and not quartile(r):
                parts = [get(self.series, r["block"], r["item"], r["period"], le) for le in self.series["entities"][1:]]
                self.assertEqual(sum(parts), r["value"], (r["block"], r["item"], r["period"]))

    def test_each_period_equals_the_matrix_of_that_period(self):
        same = [(("Fälle", ts.CASES), (ts.B_CASES, ts.CASES)),
                (("Einwilligung", ts.CONSENT_VALID), (ts.B_CONSENT, ts.CONSENT_VALID)),
                (("Füllgrad optionaler Felder (Fälle)", "followUp"), (ts.B_FOLLOW_UP, ts.FOLLOW_UP_CONTENT))]
        same += [((ts.B_STATUS, r["item"]), (ts.B_STATUS, r["item"])) for r in self.matrix["rows"] if r["section"] == ts.B_STATUS]
        same += [((ts.B_RATE, s), (ts.B_RATE, s)) for s in rdm.STATUSES]
        months = {"2025-11": (date(2025, 11, 1), date(2025, 12, 1)), "2025-12": (date(2025, 12, 1), date(2026, 1, 1)),
                  "2026-01": (date(2026, 1, 1), date(2026, 2, 1)), "2026-02": (date(2026, 2, 1), date(2026, 3, 1))}
        quarters = {"2025-Q4": (date(2025, 10, 1), date(2026, 1, 1)), "2026-Q1": (date(2026, 1, 1), date(2026, 4, 1))}
        for resolution, periods in ((ts.MONTH, months), (ts.QUARTER, quarters)):
            series = run(mixed_rows(), resolution=resolution)
            self.assertEqual(series["periods"], list(periods) + [ts.WHOLE])
            for period, (start, end) in periods.items():
                matrix = rdm.run(mixed_rows(), [], None, start, end, REFERENCE, min_cell=1)
                for entity in matrix["entities"]:
                    for (section, item), (block, series_item) in same:
                        expected = [matrix_value(matrix, section, item, entity, f) for f in ("value", "base", "percent")]
                        found = [get(series, block, series_item, period, entity, f) for f in ("value", "base", "percent")]
                        self.assertEqual(found, expected, (period, entity, section, item))

    def test_cases_are_those_the_matrix_can_evaluate(self):
        # dates the matrix doesn't read: a followUpDate of an earlier submission, the date of an addition
        odd_date = datenkranz("t2", type_="followup", submitted="2025-06-01", follow_up=follow_up("2025-05"))
        no_date = datenkranz("t3", type_="addition")
        del no_date["metaData"]["submission"]["date"]
        rows = [
            row("a", datenkranz("t1"), "2025-03-01T10:00:00"),
            row("a", odd_date, "2025-06-01T10:00:00"),
            row("a", no_date, "2025-07-01T10:00:00"),
            row("a", datenkranz("t4", type_="followup", submitted="2025-09-01", follow_up=follow_up("2025-08-10")),
                "2025-09-01T10:00:00"),
            row("b", datenkranz("t5", submitted="2025-04-02", assessment="unknown"), "2025-04-02T10:00:00"),   # unreadable
        ]
        matrix = rdm.run(rows, [], None, None, None, REFERENCE, min_cell=1)
        series = run(rows)
        for item in (ts.CASES, ts.UNREADABLE):
            self.assertEqual(get(series, ts.B_CASES, item), matrix_value(matrix, "Fälle", item, rdm.TOTAL))
        self.assertEqual((get(series, ts.B_CASES, ts.CASES), get(series, ts.B_CASES, ts.UNREADABLE)), (1, 1))
        # the unreadable case in the month of its initial submission
        self.assertEqual([get(series, ts.B_CASES, ts.UNREADABLE, p) for p in ("2025-03", "2025-04")], [0, 1])
        # the date that can't be read is left out, the submission without date counted apart
        self.assertEqual([get(series, ts.B_FOLLOW_UP, ts.DAYS, field=f) for f in ("n", "median")], [1, 181.0])
        self.assertEqual(get(series, ts.B_SUBMISSIONS, ts.WITHOUT_DATE), 1)
        self.assertEqual(get(series, ts.B_SUBMISSIONS, ts.SUBMISSIONS), 4)
        self.assertEqual(get(series, ts.B_SUBMISSIONS, ts.WITHOUT_DATE, "2025-07"), 0)
        self.assertTrue(any(ts.WITHOUT_DATE in note for note in series["notes"]))

    def test_les_of_the_matrix_and_those_with_other_submissions(self):
        rows = (
            submission_series("a", datenkranz("t1", submitted="2026-02-01"), site="LE1")
            + submission_series("b", datenkranz("t2", type_="test", submitted="2026-02-02"), site="LE2")
            + submission_series("c", datenkranz("t3", type_="addition", submitted="2025-06-01"), site="LE3")
        )
        start, end = date(2026, 1, 1), date(2026, 3, 1)
        matrix = rdm.run(rows, [rdm.Failure("LE4", "x", "t9", "conversion failed")], None, start, end, REFERENCE, min_cell=1)
        series = run(rows, failures=[("LE4", "2025-05-01T08:00:00")], start=start, end=end)
        # the matrix lists LE3 and LE4 for what is to clarify outside of the period; LE2 has only tested
        self.assertEqual(matrix["entities"], [rdm.TOTAL, "LE1", "LE3", "LE4"])
        self.assertEqual(series["entities"], [rdm.TOTAL, "LE1", "LE2", "LE3", "LE4"])
        self.assertEqual({le: label for le, label in series["labels"].items() if le != "LE2"}, matrix["labels"])
        self.assertEqual(get(series, ts.B_SUBMISSIONS, "Einreichungen test", le="LE2"), 1)
        self.assertEqual([get(series, ts.B_SUBMISSIONS, ts.NOT_CONVERTED, le=le) for le in ("LE4", rdm.TOTAL)], [0, 0])

    def test_gfk2_as_in_the_matrix(self):
        built, _, _, _ = rdm.build_cases(mixed_rows())
        self.assertEqual(len(built), 7)
        for case in built:
            e = rdm.evaluate_case(case, REFERENCE, 14)
            board = ts.gfk2(case)
            self.assertEqual(board is not None, e.has_gfk2)
            if e.medical_days is not None:
                gfk1 = rdm.parse_date(case.latest["metaData"]["molecularBoardDecisionDate"])
                self.assertEqual((board[0] - gfk1).days, e.medical_days)
                self.assertEqual((rdm.submission_date(case.submissions[board[1]]) - board[0]).days, e.documentary_days)


class PeriodTests(unittest.TestCase):

    def test_month_and_quarter_of_the_initial_submission(self):
        rows = (
            submission_series("a", datenkranz("t1", submitted="2025-12-31"),
                              datenkranz("t2", type_="followup", submitted="2026-04-01"))
            + submission_series("b", datenkranz("t3", submitted="2026-01-01"))
        )
        months = run(rows)
        self.assertEqual(months["periods"], ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04", ts.WHOLE])
        self.assertEqual([get(months, ts.B_CASES, ts.CASES, p) for p in months["periods"]], [1, 1, 0, 0, 0, 2])
        # the follow-up counts for the case in the month of its initial, and as submission in its own month
        self.assertEqual(get(months, ts.B_FOLLOW_UP, ts.FOLLOW_UP_SUBMISSION, "2025-12"), 1)
        self.assertEqual(get(months, ts.B_FOLLOW_UP, ts.FOLLOW_UP_SUBMISSION, "2026-04"), 0)
        self.assertEqual(get(months, ts.B_SUBMISSIONS, "Einreichungen followup", "2026-04"), 1)
        self.assertEqual(get(months, ts.B_SUBMISSIONS, "Einreichungen followup", "2025-12"), 0)

        quarters = run(rows, resolution=ts.QUARTER)
        self.assertEqual(quarters["periods"], ["2025-Q4", "2026-Q1", "2026-Q2", ts.WHOLE])
        self.assertEqual([get(quarters, ts.B_CASES, ts.CASES, p) for p in quarters["periods"]], [1, 1, 0, 2])
        self.assertEqual(get(quarters, ts.B_SUBMISSIONS, "Einreichungen followup", "2026-Q2"), 1)

    def test_from_and_until_as_in_the_matrix(self):
        rows = [row("a", datenkranz("t1", submitted="2025-12-31")), row("b", datenkranz("t2", submitted="2026-01-01"))]
        until = run(rows, end=date(2026, 1, 1))
        self.assertEqual(until["periods"], ["2025-12", ts.WHOLE])
        self.assertEqual(get(until, ts.B_CASES, ts.CASES), 1)
        self.assertEqual(until["title"], "ZEITREIHEN … - 31.12.2025")
        since = run(rows, start=date(2026, 1, 1), end=date(2026, 4, 1), resolution=ts.QUARTER)
        self.assertEqual(since["periods"], ["2026-Q1", ts.WHOLE])
        self.assertEqual((get(since, ts.B_CASES, ts.CASES, "2026-Q1"), get(since, ts.B_SUBMISSIONS, ts.SUBMISSIONS)), (1, 1))
        self.assertEqual(run(rows)["period"], "gesamter Export (Erstmeldungen 31.12.2025 - 01.01.2026)")

    def test_periods(self):
        self.assertEqual([ts.period_of(date(2025, 12, 31), r) for r in (ts.MONTH, ts.QUARTER)], ["2025-12", "2025-Q4"])
        self.assertEqual([ts.period_of(date(2026, 1, 1), r) for r in (ts.MONTH, ts.QUARTER)], ["2026-01", "2026-Q1"])
        self.assertEqual(ts.periods_between(date(2025, 11, 15), date(2026, 2, 1), ts.MONTH),
                         ["2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual(ts.periods_between(date(2025, 11, 15), date(2026, 4, 1), ts.QUARTER), ["2025-Q4", "2026-Q1", "2026-Q2"])
        self.assertEqual(run([])["periods"], [ts.WHOLE])


class ConsentTests(unittest.TestCase):

    def test_transmitted_valid_or_not_and_not_transmitted_by_reason(self):
        withdrawn = datenkranz("t5", consents=[broad_consent(), broad_consent(kind="deny")])
        as_text = datenkranz("t9")
        as_text["metaData"]["researchConsents"] = [{"presentationDate": "2025-01-01", "scope": json.dumps(broad_consent())}]
        result = cases(
            datenkranz("t1", consents=[broad_consent()]),
            datenkranz("t2", consents=[broad_consent(kind="deny")]),
            datenkranz("t3", consents=[broad_consent(start="2019-06-01")]),   # older than 5 years at the submission
            datenkranz("t4", consents=[broad_consent(code=OTHER_CODE)]),
            withdrawn,
            datenkranz("t6"),                                                  # researchConsents: []
            without_consent("t7", ("2025-01-01", REFUSES)),
            without_consent("t8", ("2025-01-01", "some text of the LE")),
            as_text,
        )
        b = ts.B_CONSENT

        def values(item):
            return tuple(get(result, b, item, field=f) for f in ("value", "base", "percent"))

        self.assertEqual(values(ts.CONSENT_TRANSMITTED), (5, 9, 55.6))
        self.assertEqual(values(ts.CONSENT_VALID), (1, 9, 11.1))
        self.assertEqual(values(ts.CONSENT_RATE), (1, 5, 20.0))
        self.assertEqual(values(ts.CONSENT_INVALID), (4, 5, 80.0))
        invalid = [values(f"{ts.CONSENT_INVALID}: {label}")[0] for _, label in ts.INVALID]
        self.assertEqual(invalid, [2, 1, 1])     # denied (t2, t5), expired (t3), no research provision (t4)
        self.assertEqual(values(ts.CONSENT_MISSING), (4, 9, 44.4))
        self.assertEqual(values(f"{ts.CONSENT_MISSING}: {REFUSES}"), (1, 4, 25.0))
        self.assertEqual(values(f"{ts.CONSENT_MISSING}: {ts.NO_JUSTIFICATION}")[0], 1)
        # a scope that isn't the FHIR Consent object of the schema is no transmitted consent, and named as such
        self.assertEqual(values(f"{ts.CONSENT_MISSING}: {ts.NOT_AN_OBJECT}")[0], 1)
        self.assertEqual(sum(values(f"{ts.CONSENT_MISSING}: {r}")[0] for r in ts.REASONS), 4)
        # a value outside of the schema is counted, but not printed
        self.assertEqual(values(f"{ts.CONSENT_MISSING}: {ts.OTHER_JUSTIFICATION}")[0], 1)
        self.assertNotIn("some text of the LE", json.dumps(result))
        reasons = [values(f"{ts.CONSENT_MISSING}: {r}")[0] for r in ts.JUSTIFICATIONS]
        self.assertEqual(sum(reasons), 1)

    def test_rate_of_those_asked_counts_the_documented_refusals(self):
        # 6 valid consents, 4 patients who refused without signing: all transmitted consents are valid, 60 % agreed
        contents = [datenkranz(f"t{i}", consents=[broad_consent()]) for i in range(6)]
        refused = [without_consent(f"r{i}", ("2025-01-01", REFUSES)) for i in range(4)]
        result = cases(*contents, *refused)

        def values(result, item):
            return tuple(get(result, ts.B_CONSENT, item, field=f) for f in ("value", "base", "percent"))

        self.assertEqual(values(result, ts.CONSENT_RATE), (6, 6, 100.0))
        self.assertEqual(values(result, ts.CONSENT_RATE_REFUSALS), (6, 10, 60.0))
        self.assertFalse(any("Obergrenzen" in note for note in result["notes"]))
        # as exported without the reasons, the refusals can't be told: both rates are upper bounds
        unknown = cases(*contents, *(datenkranz(f"r{i}") for i in range(4)))
        self.assertEqual(values(unknown, ts.CONSENT_RATE_REFUSALS), (6, 6, 100.0))
        self.assertTrue(any("beide Zustimmungsquoten sind Obergrenzen" in note for note in unknown["notes"]))
        # another reason is no refusal
        unable = cases(*contents, without_consent("u", ("2025-01-01", ts.JUSTIFICATIONS[0])))
        self.assertEqual(values(unable, ts.CONSENT_RATE_REFUSALS), (6, 6, 100.0))

    def test_state_at_the_latest_submission_not_at_the_reference_date(self):
        # valid at the submission (2025-03-01), older than 5 years at the reference date (2026-10-30)
        result = cases(datenkranz("t1", consents=[broad_consent(start="2021-06-01")]))
        self.assertEqual(get(result, ts.B_CONSENT, ts.CONSENT_VALID), 1)
        self.assertEqual(get(result, ts.B_CONSENT, ts.CONSENT_INVALID), 0)
        self.assertEqual([get(result, ts.B_CONSENT, f"{ts.CONSENT_INVALID}: {label}") for _, label in ts.INVALID], [0, 0, 0])

    def test_state_of_the_latest_submission(self):
        rows = submission_series(
            "a", without_consent("t1", ("2025-01-01", REFUSES), submitted="2025-03-01"),
            datenkranz("t2", type_="followup", submitted="2025-06-01", consents=[broad_consent()]))
        result = run(rows)
        self.assertEqual(get(result, ts.B_CONSENT, ts.CONSENT_VALID, "2025-03"), 1)
        self.assertEqual(get(result, ts.B_CONSENT, ts.CONSENT_MISSING, "2025-03"), 0)

    def test_reason_of_the_latest_entry(self):
        unable = "patient unable to consent"
        self.assertEqual(ts.justification([{"presentationDate": "2025-05-01", "noScopeJustification": REFUSES},
                                           {"presentationDate": "2025-02-01", "noScopeJustification": unable}]), REFUSES)
        self.assertEqual(ts.justification([{"presentationDate": "2025-05-01"}]), ts.NO_JUSTIFICATION)
        self.assertEqual(ts.justification([]), ts.NO_JUSTIFICATION)
        self.assertEqual(ts.JUSTIFICATIONS[1], REFUSES)

    def test_states_follow_the_rule_of_the_matrix(self):
        at = date(2025, 3, 1)

        def state(*consents):
            return ts.consent_state([{"scope": c} for c in consents], at)

        expired, denied = broad_consent(start="2019-06-01"), broad_consent(kind="deny")
        self.assertEqual(state(broad_consent()), ts.VALID)
        self.assertEqual(state(expired), ts.EXPIRED)
        self.assertEqual(state(denied), ts.DENIED)
        ended = broad_consent()
        ended["provision"]["provision"][0]["period"]["end"] = "2025-02-01"      # before the date
        self.assertEqual(state(ended), ts.EXPIRED)
        self.assertEqual(state(broad_consent(code=OTHER_CODE)), ts.NO_PROVISION)
        self.assertEqual(state(), ts.NO_PROVISION)
        # one provision denied, another permitted, but expired: the period keeps the consent from being valid
        mixed = broad_consent(kind="deny")
        mixed["provision"]["provision"] += broad_consent(start="2019-06-01", code=PATDAT_STORE_AND_USE)["provision"]["provision"]
        self.assertEqual(state(mixed), ts.EXPIRED)
        # a consent without period, and one whose provision is missing in a later consent
        no_period = broad_consent()
        del no_period["provision"]["provision"][0]["period"]
        self.assertEqual(state(no_period), ts.EXPIRED)
        self.assertEqual(state(broad_consent(), broad_consent(code=OTHER_CODE)), ts.NO_PROVISION)
        # VALID exactly if the matrix counts the consent, the input is left as it was
        for consents in ([broad_consent()], [expired], [denied], [mixed], [expired, broad_consent()], [denied, broad_consent()]):
            entries = [{"scope": c} for c in consents]
            before = json.dumps(entries)
            self.assertEqual(ts.consent_state(entries, at) == ts.VALID, rdm.permits_research_use(entries, at))
            self.assertEqual(json.dumps(entries), before)


class DiagnosisTests(unittest.TestCase):

    def test_status_and_both_rates_per_period(self):
        result = cases(
            datenkranz("t1", submitted="2025-03-01"),
            datenkranz("t2", submitted="2025-03-15", assessment="partialGeneticDiagnosis"),
            datenkranz("t3", submitted="2025-03-31", assessment="noGeneticDiagnosis"),
            datenkranz("t4", submitted="2025-03-31", assessment="noGeneticDiagnosis", plan_date=None),   # open
            datenkranz("t5", submitted="2025-04-01"),
        )
        open_ = "offen (Undiagnostiziert, noch keine GFK2)"
        march = [get(result, ts.B_STATUS, s, "2025-03") for s in ("Diagnose", "Unklar", "Undiagnostiziert", open_)]
        self.assertEqual(march, [1, 1, 1, 1])
        # of all cases, and of the closed ones
        self.assertEqual(get(result, ts.B_STATUS, "Diagnose", "2025-03", field="percent"), 25.0)
        self.assertEqual(get(result, ts.B_RATE, "Diagnose", "2025-03", field="percent"), 33.3)
        self.assertEqual(get(result, ts.B_RATE, "Diagnose", "2025-03", field="base"), 3)
        self.assertEqual(get(result, ts.B_RATE, "Diagnose", "2025-04", field="percent"), 100.0)
        self.assertEqual(get(result, ts.B_RATE, "Diagnose", field="percent"), 50.0)


class FollowUpTests(unittest.TestCase):

    def rows(self):
        return (
            # a: GFK2 (2025-03-11) and the first follow-up come with one follow-up submission, 180 days apart
            submission_series("a", datenkranz("t1", submitted="2025-01-20", plan_date="2025-01-10"),
                              datenkranz("t2", type_="followup", submitted="2025-09-10", plan_date="2025-03-11",
                                         follow_up=follow_up("2025-09-07")))
            # b: follow-up documented: 181 days from GFK2 (2025-02-10) to the followUpDate
            + submission_series("b", datenkranz("t3"),
                                datenkranz("t4", type_="followup", submitted="2025-09-01", follow_up=follow_up("2025-08-10")))
            # c: follow-up submission without content: 120 days from GFK2 to the submission
            + submission_series("c", datenkranz("t5"), datenkranz("t6", type_="followup", submitted="2025-06-10"))
            # d: GFK2 on 2026-06-01, no follow-up
            + submission_series("d", datenkranz("t7", submitted="2026-06-10", gfk1="2026-04-01", plan_date="2026-06-01"))
            # e: no GFK2
            + submission_series("e", datenkranz("t8", plan_date=None))
            # f: an addition and a correction are no follow-up submissions
            + submission_series("f", datenkranz("t9"), datenkranz("t10", type_="addition", submitted="2025-06-10"),
                                datenkranz("t11", type_="correction", submitted="2025-07-10"))
            # g: follow-up submission and follow-up before the GFK2 (2025-05-01), which an addition reports
            + submission_series("g", datenkranz("t12", plan_date="2025-01-10"),
                                datenkranz("t13", type_="followup", submitted="2025-04-01", plan_date="2025-01-10",
                                           follow_up=follow_up("2025-03-20")),
                                datenkranz("t14", type_="addition", submitted="2025-05-10", plan_date="2025-05-01"))
            # h: follow-up 400 days after GFK2
            + submission_series("h", datenkranz("t15"),
                                datenkranz("t16", type_="followup", submitted="2026-03-20", follow_up=follow_up("2026-03-17")))
        )

    def test_rates_of_all_cases_of_those_with_gfk2_and_of_those_due(self):
        result = run(self.rows(), due_days=365)
        b = ts.B_FOLLOW_UP

        def values(item):
            return tuple(get(result, b, item, field=f) for f in ("value", "base", "percent"))

        self.assertEqual(values(ts.FOLLOW_UP_SUBMISSION), (5, 8, 62.5))     # a, b, c, g, h
        self.assertEqual(values(ts.FOLLOW_UP_CONTENT), (3, 8, 37.5))        # a, b, h
        self.assertEqual(values(ts.WITH_GFK2), (7, 8, 87.5))
        self.assertEqual(values(ts.GFK2_CONTENT), (3, 7, 42.9))
        self.assertEqual(values(ts.GFK2_SUBMISSION), (4, 7, 57.1))          # a, b, c, h: from the GFK2 report on
        # due at the reference date 2026-10-30: all but the GFK2 of 2026-06-01
        self.assertEqual(values(ts.DUE), (6, 7, 85.7))
        self.assertEqual(values(ts.DUE_CONTENT), (3, 6, 50.0))
        self.assertEqual(values(ts.DUE_SUBMISSION), (4, 6, 66.7))
        self.assertEqual(values(ts.DUE_IN_TIME), (3, 6, 50.0))              # a, b, c: within 365 days
        self.assertIn("Fälle mit GFK2 mindestens 365 Tage vor dem Stichtag",
                      {r["base_label"] for r in result["rows"] if r["item"] == ts.DUE_CONTENT})
        # without the option, no lines on due follow-ups
        due = (ts.DUE, ts.DUE_CONTENT, ts.DUE_SUBMISSION, ts.DUE_IN_TIME, ts.DUE_DAYS)
        self.assertFalse(any(r["item"] in due for r in run(self.rows())["rows"]))

    def test_days_from_gfk2_to_the_first_follow_up_else_to_the_follow_up_submission(self):
        result = run(self.rows(), due_days=365)
        b = ts.B_FOLLOW_UP

        def days(item, period=ts.WHOLE):
            return [get(result, b, item, period, field=f) for f in ("n", "base", "median", "q1", "q3")]

        self.assertEqual(days(ts.DAYS), [4, 7, 180.5, 165.0, 235.8])        # 120 (c), 180 (a), 181 (b), 400 (h)
        self.assertIsNone(get(result, b, ts.DAYS))
        self.assertEqual(get(result, b, ts.DAYS, field="base_label"), ts.WITH_GFK2)
        self.assertEqual((get(result, b, ts.DAYS_FROM_DATE), get(result, b, ts.DAYS_FROM_SUBMISSION)), (3, 1))
        self.assertEqual(get(result, b, ts.DAYS_FROM_SUBMISSION, field="base"), 4)
        # of the due cases, the follow-ups within 365 days
        self.assertEqual(days(ts.DUE_DAYS), [3, 6, 180.0, 150.0, 180.5])
        # by the month of the initial submission
        self.assertEqual([days(ts.DAYS, p)[:2] for p in ("2025-01", "2025-03", "2026-06")], [[1, 1], [3, 5], [0, 1]])
        self.assertEqual(days(ts.DAYS, "2025-01")[2], 180.0)
        self.assertIsNone(days(ts.DAYS, "2026-06")[2])

    def test_first_follow_up_of_all_submissions_not_before_gfk2(self):
        rows = (
            # each submission holds the latest follow-up only; the one before GFK2 (2025-02-10) doesn't count
            submission_series("a", datenkranz("t1", follow_up=follow_up("2025-01-31")),
                              datenkranz("t2", type_="followup", submitted="2025-06-01", follow_up=follow_up("2025-05-01")),
                              datenkranz("t3", type_="followup", submitted="2025-10-01", follow_up=follow_up("2025-09-01")))
            # only a follow-up before GFK2: the follow-up submission counts instead
            + submission_series("b", datenkranz("t4", follow_up=follow_up("2025-01-31")),
                                datenkranz("t5", type_="followup", submitted="2025-04-01", follow_up=follow_up("2025-01-31")))
            # GFK2 after the submission reporting it: no duration, as in the matrix
            + submission_series("c", datenkranz("t6", plan_date="2028-02-10"),
                                datenkranz("t7", type_="followup", submitted="2028-06-01", plan_date="2028-02-10",
                                           follow_up=follow_up("2028-05-01")))
        )
        result = run(rows)
        b = ts.B_FOLLOW_UP
        self.assertEqual([get(result, b, ts.DAYS, field=f) for f in ("n", "median")], [2, 65.0])   # 80 and 50 days
        self.assertEqual((get(result, b, ts.DAYS_FROM_DATE), get(result, b, ts.DAYS_FROM_SUBMISSION)), (1, 1))
        self.assertEqual(get(result, b, ts.FOLLOW_UP_CONTENT), 3)
        built, _, _, _ = rdm.build_cases(rows)
        self.assertEqual(ts.follow_up_dates(built[0]), [date(2025, 1, 31), date(2025, 5, 1), date(2025, 9, 1)])
        # a follow-up submission dated before the GFK2 (2025-02-10) gives no duration
        early = [row("a", datenkranz("t1"), "2025-03-01T10:00:00"),
                 row("a", datenkranz("t2", type_="followup", submitted="2025-02-01"), "2025-03-05T10:00:00")]
        result = run(early)
        self.assertEqual((get(result, b, ts.GFK2_SUBMISSION), get(result, b, ts.DAYS, field="n")), (1, 0))

    def test_boundary_days(self):
        def rows(gfk2, seen):
            return (datenkranz("t1", submitted="2025-11-10", gfk1="2025-09-01", plan_date=gfk2),
                    datenkranz("t2", type_="followup", submitted="2025-11-19", gfk1="2025-09-01", plan_date=gfk2,
                               follow_up=follow_up(seen)))

        # a: GFK2 365 days before the reference date 2026-10-30, follow-up on the day of the GFK2
        # b: GFK2 364 days before, follow-up the day before the GFK2: the submission counts instead, 19 days later
        both = submission_series("a", *rows("2025-10-30", "2025-10-30")) + submission_series("b", *rows("2025-10-31", "2025-10-30"))
        result = run(both, due_days=365)
        b = ts.B_FOLLOW_UP
        self.assertEqual(get(result, b, ts.DUE), 1)
        self.assertEqual([get(result, b, ts.DAYS, field=f) for f in ("n", "median")], [2, 9.5])     # 0 and 19 days
        self.assertEqual((get(result, b, ts.DAYS_FROM_DATE), get(result, b, ts.DAYS_FROM_SUBMISSION)), (1, 1))
        # within the interval: up to as many days
        self.assertEqual(get(run(both, due_days=19), b, ts.DUE_IN_TIME), 2)
        self.assertEqual(get(run(both, due_days=18), b, ts.DUE_IN_TIME), 1)
        self.assertEqual([get(run(both, due_days=18), b, ts.DUE_DAYS, field=f) for f in ("n", "base", "median")], [1, 2, 0.0])

    def test_due_cases_within_the_interval_compare_between_periods(self):
        def cohort(key, initial, gfk1, gfk2, *seen):
            rows = []
            for i, day in enumerate(seen):
                first = datenkranz(f"{key}{i}a", submitted=initial, gfk1=gfk1, plan_date=gfk2)
                later = [datenkranz(f"{key}{i}b", type_="followup", submitted=day, gfk1=gfk1, plan_date=gfk2,
                                    follow_up=follow_up(day))] if day else []
                rows += submission_series(f"{key}{i}", first, *later)
            return rows

        # the same course in both cohorts, follow-ups 100 and 300 days after GFK2; of the young cohort (GFK2 150
        # days before the reference date 2026-10-30) the second one hasn't taken place yet
        rows = (cohort("old", "2025-03-01", "2025-01-10", "2025-02-10", "2025-05-21", "2025-12-07")
                + cohort("young", "2026-06-10", "2026-04-01", "2026-06-02", "2026-09-10", None))
        result = run(rows, due_days=120)
        b = ts.B_FOLLOW_UP

        def by_period(item, field):
            return [get(result, b, item, p, field=field) for p in ("2025-03", "2026-06")]

        # all follow-ups known so far: the young cohort seems faster
        self.assertEqual(by_period(ts.DAYS, "median"), [200.0, 100.0])
        self.assertEqual(by_period(ts.GFK2_CONTENT, "percent"), [100.0, 50.0])
        # due cases, follow-ups within 120 days: the same
        self.assertEqual(by_period(ts.DUE, "value"), [2, 2])
        self.assertEqual(by_period(ts.DUE_DAYS, "median"), [100.0, 100.0])
        self.assertEqual(by_period(ts.DUE_IN_TIME, "percent"), [50.0, 50.0])
        self.assertTrue(any("nicht vergleichbar" in note and "innerhalb der Frist" in note for note in result["notes"]))
        # with 200 days the young cohort isn't due yet: none of its follow-ups count
        later = run(rows, due_days=200)
        self.assertEqual([get(later, b, ts.DUE_DAYS, p, field=f) for p in ("2025-03", "2026-06") for f in ("n", "base")], [1, 2, 0, 0])


class TimelineTests(unittest.TestCase):
    """Zeitstrahl: milestones in days relative to GFK1"""

    T = ts.B_TIMELINE

    @staticmethod
    def enriched(tan, onset=None, zse=(), consent_dates=None, **kwargs):
        content = datenkranz(tan, **kwargs)
        if onset:
            content["case"]["diagnosisRd"]["symptomOnsetDate"] = onset
        if zse:
            content["case"]["priorRds"] = [{"genomicTestType": "none", "zseContactDate": d} for d in zse]
        if consent_dates:
            content["metaData"]["mvConsent"]["scope"] = [{"date": d, "domain": domain, "type": "permit"}
                                                         for d, domain in consent_dates]
        return content

    def test_offsets_of_every_milestone(self):
        # GFK1 2025-01-10: consent 2025-01-01, GFK2 2025-02-10, initial 2025-03-01
        rows = submission_series(
            "a",
            self.enriched("t1", onset="2020-06", zse=("2024-03-01", "2023-11-20"),
                          consent_dates=[("2024-12-01", "reIdentification"), ("2025-01-01", "mvSequencing")]),
            datenkranz("t2", type_="followup", submitted="2025-09-01", follow_up=follow_up("2025-08-01")))
        rows[1].content["case"] = rows[0].content["case"]
        result = run(rows)
        for name, days in (("MV-Einwilligung", -9), ("Symptombeginn", -1670), ("ZSE-Erstkontakt", -417),
                           ("GFK2", 31), ("Erstmeldung beim KDK", 50), ("Erstes Follow-up", 203)):
            self.assertEqual(get(result, self.T, f"{name}: Fälle"), 1, name)
            self.assertEqual(get(result, self.T, f"{name}: Fälle", field="base"), 1, name)
            for label in ("1. Quartil", "Median", "3. Quartil"):
                self.assertEqual(get(result, self.T, f"{name}: {label} (Tage)"), float(days), name)
        self.assertEqual(get(result, self.T, ts.NO_GFK1), 0)

    def test_row_labels(self):
        result = cases(datenkranz("t1"))
        items = [r["item"] for r in result["rows"] if r["block"] == self.T and r["le"] == rdm.TOTAL and r["period"] == ts.WHOLE]
        expected = [ts.NO_GFK1]
        for name, after in ts.MILESTONES:
            expected += [f"{name}: Fälle", f"{name}: 1. Quartil (Tage)", f"{name}: Median (Tage)", f"{name}: 3. Quartil (Tage)"]
            expected += [f"{name}: unplausibel (vor GFK1)"] if after else []
        self.assertEqual(items, expected)
        self.assertFalse([i for i in items if "Minimum" in i or "Maximum" in i])

    def test_quartiles_unknown_onset_and_cases_without_a_date(self):
        result = cases(*[self.enriched(f"t{i}", onset=f"2024-0{i + 1}") for i in range(5)],
                       self.enriched("t5", onset="1800-01"), datenkranz("t6"))
        # onsets 2024-01-15 .. 2024-05-15 before GFK1 2025-01-10: -361, -330, -301, -270, -240
        self.assertEqual(get(result, self.T, "Symptombeginn: Fälle"), 5)
        self.assertEqual(get(result, self.T, "Symptombeginn: Fälle", field="base"), 7)
        self.assertEqual(get(result, self.T, "Symptombeginn: Fälle", field="percent"), 71.4)
        self.assertEqual(get(result, self.T, "Symptombeginn: 1. Quartil (Tage)"), -330.0)
        self.assertEqual(get(result, self.T, "Symptombeginn: Median (Tage)"), -301.0)
        self.assertEqual(get(result, self.T, "Symptombeginn: 3. Quartil (Tage)"), -270.0)
        for r in result["rows"]:
            if quartile(r):
                self.assertEqual((r["base"], r["percent"], r["median"], r["n"]), (None, None, None, None))

    def test_milestone_before_gfk1_is_implausible_and_left_out(self):
        rows = submission_series("a", datenkranz("t1"),
                                 datenkranz("t2", type_="followup", submitted="2025-04-01", follow_up=follow_up("2024-12-01")))
        rows += submission_series("b", datenkranz("t3"),
                                  datenkranz("t4", type_="followup", submitted="2025-04-01", follow_up=follow_up("2025-02-09")))
        rows += submission_series("c", datenkranz("t5", submitted="2025-01-05"))      # initial before GFK1
        result = run(rows)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Fälle"), 1)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: unplausibel (vor GFK1)"), 1)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Median (Tage)"), 30.0)
        self.assertEqual(get(result, self.T, "Erstmeldung beim KDK: unplausibel (vor GFK1)"), 1)
        self.assertEqual(get(result, self.T, "Erstmeldung beim KDK: Fälle"), 2)
        # GFK2 is the first board after GFK1, never before
        self.assertEqual(get(result, self.T, "GFK2: unplausibel (vor GFK1)"), 0)

    def test_cases_without_gfk1(self):
        broken = datenkranz("t1")
        del broken["metaData"]["molecularBoardDecisionDate"]
        result = cases(broken, datenkranz("t2"), min_cell=5)
        self.assertEqual(get(result, self.T, ts.NO_GFK1), "<5")
        self.assertEqual(get(cases(broken, datenkranz("t2")), self.T, ts.NO_GFK1), 1)
        self.assertEqual(get(cases(broken, datenkranz("t2")), ts.B_CASES, ts.UNREADABLE), 1)

    def test_small_cells_hide_count_and_quartiles(self):
        contents = [self.enriched(f"t{i}", zse=("2024-01-01",) if i < 3 else ()) for i in range(8)]
        result = cases(*contents, min_cell=5)
        self.assertEqual(get(result, self.T, "ZSE-Erstkontakt: Fälle"), "<5")
        for label in ("1. Quartil", "Median", "3. Quartil"):
            self.assertIsNone(get(result, self.T, f"ZSE-Erstkontakt: {label} (Tage)"))
        self.assertEqual(get(result, self.T, "GFK2: Fälle"), 8)
        self.assertEqual(get(result, self.T, "GFK2: Median (Tage)"), 31.0)

    def test_per_le_and_month(self):
        rows = submission_series("a", datenkranz("t1", submitted="2025-03-01"), site="LE1")
        rows += submission_series("b", datenkranz("t2", submitted="2025-04-11", gfk1="2025-04-01", plan_date="2025-04-05"),
                                  site="LE2")
        result = run(rows)
        self.assertEqual(get(result, self.T, "Erstmeldung beim KDK: Median (Tage)", period="2025-03"), 50.0)
        self.assertEqual(get(result, self.T, "Erstmeldung beim KDK: Median (Tage)", period="2025-04", le="LE2"), 10.0)
        self.assertEqual(get(result, self.T, "Erstmeldung beim KDK: Fälle", period="2025-04", le="LE1"), 0)
        self.assertIsNone(get(result, self.T, "Erstmeldung beim KDK: Median (Tage)", period="2025-04", le="LE1"))
        self.assertEqual(get(result, self.T, "GFK2: Median (Tage)", le="LE2"), 4.0)


class SubmissionTests(unittest.TestCase):

    def test_submissions_by_type_and_calendar_period_per_le(self):
        mtb = datenkranz("t9", submitted="2025-01-10")
        mtb["metaData"]["submission"]["diseaseType"] = "oncological"
        no_date = datenkranz("t11", type_="addition")
        del no_date["metaData"]["submission"]["date"]
        rows = (
            submission_series("a", datenkranz("t1", submitted="2025-01-10"),
                              datenkranz("t2", type_="followup", submitted="2025-03-05", follow_up=follow_up("2025-03-01")),
                              datenkranz("t3", type_="addition", submitted="2025-03-06"),
                              datenkranz("t4", type_="correction", submitted="2025-04-01"),
                              datenkranz("t10", type_="follow-up", submitted="2025-04-02"), site="LE1")   # not a type of the schema
            + [row("a", no_date, "2025-03-07T10:00:00", "LE1")]
            + submission_series("b", datenkranz("t5", type_="test", submitted="2025-01-11"), site="LE1")
            + submission_series("c", datenkranz("t6", type_="followup", submitted="2025-02-01"),
                                datenkranz("t7", type_="followup", submitted="2025-03-01"), site="LE2")   # no initial
            + submission_series("d", mtb, site="LE1")
        )
        failures = [("LE2", "2025-02-15T09:30:00"), ("LE2", "")]
        result = run(rows, failures=failures)
        b = ts.B_SUBMISSIONS

        def series(item, le=rdm.TOTAL):
            return [get(result, b, item, p, le) for p in result["periods"]]

        self.assertEqual(result["periods"], ["2025-01", "2025-02", "2025-03", "2025-04", ts.WHOLE])
        self.assertEqual(series("Einreichungen initial"), [1, 0, 0, 0, 1])
        self.assertEqual(series("Einreichungen followup"), [0, 1, 2, 0, 3])
        self.assertEqual(series("Einreichungen followup", "LE2"), [0, 1, 1, 0, 2])
        self.assertEqual(series("Einreichungen addition"), [0, 0, 1, 0, 1])
        self.assertEqual(series("Einreichungen correction"), [0, 0, 0, 1, 1])
        self.assertEqual(series("Einreichungen test"), [1, 0, 0, 0, 1])
        # a type outside of the schema is counted apart, and its value not printed
        self.assertEqual(series(ts.OTHER_TYPE), [0, 0, 0, 1, 1])
        self.assertFalse(any("follow-up" in r["item"] for r in result["rows"]))
        self.assertTrue(any(ts.OTHER_TYPE in note for note in result["notes"]))
        self.assertEqual(series(ts.SUBMISSIONS), [1, 1, 3, 2, 7])
        by_type = [series(f"Einreichungen {t}") for t in ("initial", "followup", "addition", "correction")] + [series(ts.OTHER_TYPE)]
        self.assertEqual([sum(counts) for counts in zip(*by_type)], series(ts.SUBMISSIONS))
        # a submission without date belongs to no period, and to no other line
        self.assertEqual(series(ts.WITHOUT_DATE), [0, 0, 0, 0, 1])
        self.assertEqual(series(ts.SUBMISSIONS_FOLLOW_UP), [0, 0, 1, 0, 1])
        # the episode without initial in the month of its first submission, per LE
        self.assertEqual(series(ts.WITHOUT_INITIAL), [0, 1, 0, 0, 1])
        self.assertEqual(series(ts.WITHOUT_INITIAL, "LE1"), [0, 0, 0, 0, 0])
        # failures by submittedAt, without it in the whole period only
        self.assertEqual(series(ts.NOT_CONVERTED, "LE2"), [0, 1, 0, 0, 2])
        self.assertEqual(result["entities"], [rdm.TOTAL, "LE1", "LE2"])
        self.assertEqual(get(result, ts.B_CASES, ts.CASES, le="LE2"), 0)

        # --from/--until select the submissions of this block by their own date
        march = run(rows, failures=failures, start=date(2025, 3, 1), end=date(2025, 4, 1))
        self.assertEqual(march["periods"], ["2025-03", ts.WHOLE])
        self.assertEqual([get(march, b, i) for i in (ts.SUBMISSIONS, "Einreichungen initial", ts.WITHOUT_INITIAL)], [3, 0, 0])
        self.assertEqual(get(march, ts.B_CASES, ts.CASES), 0)
        # of the failures, the one without submittedAt; the one of February is out of the period
        self.assertEqual(get(march, b, ts.NOT_CONVERTED, le="LE2"), 1)
        self.assertTrue(any("Kein Fall mit Erstmeldung" in note for note in march["notes"]))


class SuppressionTests(unittest.TestCase):

    def rows(self):
        rows = []
        for i in range(8):
            consents = [broad_consent(kind="permit" if i < 2 else "deny")] if i < 6 else []
            rows += submission_series(
                f"case-{i:04d}", datenkranz(f"TAN-{i:04d}-1", consents=consents),
                datenkranz(f"TAN-{i:04d}-2", type_="followup", submitted="2025-07-01", consents=consents,
                           follow_up=follow_up(f"2025-06-{10 + i}") if i < 4 else None),
                site="LE1")
        rows += submission_series("case-0009", datenkranz("TAN-0009-1"), site="LE2")
        return rows

    def test_counts_rates_and_medians(self):
        result = run(self.rows(), min_cell=5)
        b, le = ts.B_CONSENT, "LE1"

        def values(item, block=b, le=le):
            return tuple(get(result, block, item, le=le, field=f) for f in ("value", "base", "percent"))

        self.assertEqual(values(ts.CASES, ts.B_CASES), (8, None, None))
        self.assertEqual(values(ts.CASES, ts.B_CASES, "LE2"), ("<5", None, None))
        self.assertEqual(values(ts.CONSENT_TRANSMITTED), (6, 8, 75.0))
        # 2 of 8 and of 6: count suppressed, no rate
        self.assertEqual(values(ts.CONSENT_VALID), ("<5", 8, None))
        self.assertEqual(values(ts.CONSENT_RATE), ("<5", 6, None))
        # of the 2 cases without consent nothing is counted, neither the 2 without reason nor a zero
        self.assertEqual(values(ts.CONSENT_MISSING), ("<5", 8, None))
        self.assertEqual(values(f"{ts.CONSENT_MISSING}: {ts.NO_JUSTIFICATION}"), (None, "<5", None))
        self.assertEqual(values(f"{ts.CONSENT_MISSING}: {ts.JUSTIFICATIONS[0]}"), (None, "<5", None))
        # a zero of at least 5 cases is shown
        self.assertEqual(values(f"{ts.CONSENT_INVALID}: {ts.INVALID[2][1]}"), (0, 6, 0.0))
        # durations of 8 cases (4 by followUpDate, 4 by the submission), in total of 9 cases
        days = [get(result, ts.B_FOLLOW_UP, ts.DAYS, le=le, field=f) for f in ("n", "base", "median", "q1", "q3")]
        self.assertEqual(days, [8, 8, 132.0, 121.8, 141.0])
        self.assertEqual(values(ts.DAYS_FROM_DATE, ts.B_FOLLOW_UP), ("<5", 8, None))
        # of fewer than 5 values, no median or quartiles: 4 durations of 5 cases
        few = run(self.rows()[:8] + self.rows()[-1:], min_cell=5)
        self.assertEqual([get(few, ts.B_FOLLOW_UP, ts.DAYS, field=f) for f in ("n", "base", "median", "q1", "q3")],
                         ["<5", 5, None, None, None])

    def test_counts_within_fewer_cases_are_left_out(self):
        result = run(self.rows(), min_cell=5, due_days=30)
        values = ("value", "base", "percent", "median", "q1", "q3", "n")
        # of the single case of LE2 the number of cases only, and what isn't counted in the cases
        of_le2 = [r for r in result["rows"] if r["le"] == "LE2" and r["period"] in ("2025-03", ts.WHOLE)]
        self.assertEqual(len(of_le2), 2 * len([r for r in result["rows"] if r["le"] == "LE2" and r["period"] == ts.WHOLE]))
        for r in of_le2:
            if r["block"] not in (ts.B_CASES, ts.B_SUBMISSIONS) and r["item"] != ts.NO_GFK1:
                self.assertEqual([r[v] for v in values], [None] * 7, r["item"])
        self.assertEqual(get(result, ts.B_CASES, ts.CASES, le="LE2"), "<5")
        self.assertEqual(get(result, ts.B_CASES, ts.UNREADABLE, le="LE2"), 0)
        self.assertEqual(get(result, ts.B_SUBMISSIONS, "Einreichungen initial", le="LE2"), "<5")
        # nowhere a count, be it zero, next to a suppressed base
        for r in result["rows"]:
            if r["base"] == "<5":
                self.assertEqual((r["value"], r["n"], r["percent"], r["median"]), (None, None, None, None), r["item"])
        # the 2 refusals among the 8 cases of LE1 would follow from the base of the rate that includes them
        refusing = self.rows()
        for r in refusing[12:16]:
            r.content["metaData"]["researchConsents"] = [{"presentationDate": "2025-01-01", "noScopeJustification": REFUSES}]
        result = run(refusing, min_cell=5)
        rate = [get(result, ts.B_CONSENT, ts.CONSENT_RATE_REFUSALS, le="LE1", field=f) for f in ("value", "base", "percent")]
        self.assertEqual(rate, [None, None, None])
        self.assertEqual(get(result, ts.B_CONSENT, ts.CONSENT_RATE, le="LE1", field="base"), 6)
        # unsuppressed, every count is given
        plain = run(refusing, min_cell=1)
        self.assertEqual([get(plain, ts.B_CONSENT, ts.CONSENT_RATE_REFUSALS, le="LE1", field=f) for f in ("value", "base")], [2, 8])
        # (a quartile of no values is empty)
        self.assertFalse([r["item"] for r in plain["rows"] if r["value"] is None and r["n"] is None and not quartile(r)])

    def test_unsuppressed_with_min_cell_1(self):
        result = run(self.rows(), min_cell=1)
        self.assertEqual(get(result, ts.B_CONSENT, ts.CONSENT_RATE, le="LE1", field="percent"), 33.3)
        self.assertEqual(get(result, ts.B_CASES, ts.CASES, le="LE2"), 1)
        self.assertTrue(any("--min-cell 1" in note for note in result["notes"]))

    def test_no_case_keys_or_tans_in_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump([{"case": json.loads(r.case), "submittedAt": r.submitted_at, "content": r.content, "site": r.site}
                           for r in self.rows()], f)
            out = os.path.join(tmp, "series")
            main(path, "--output", out, "--min-cell", "1")
            self.assertEqual(sorted(os.listdir(tmp)), ["in.json", "series.csv", "series.json"])
            for name in ("series.csv", "series.json"):
                with open(os.path.join(tmp, name), encoding="utf-8-sig") as f:
                    text = f.read()
                self.assertNotIn("TAN-", text)
                self.assertNotIn("case-", text)


def synthetic_series(seed=11, sizes=(("LE-A", 48), ("LE-B", 17), ("LE-C", 7))):
    """A random synthetic case set over three LEs and six months"""
    rnd = random.Random(seed)
    rows = []
    for le, size in sizes:
        for i in range(size):
            submitted = f"2025-0{rnd.randint(1, 6)}-{rnd.randint(10, 28)}"
            content = datenkranz(
                f"{le}-{i}", submitted=submitted, gfk1="2024-12-01", plan_date=rnd.choice(["2024-12-20", None]),
                gender=rnd.choice(["female", "male", "male", "other"]), coverage=rnd.choice(["GKV"] * 6 + ["PKV"]),
                assessment=rnd.choice(list(rdm.ASSESSMENT_STATUS)), library=rnd.choice(["wgs"] * 6 + ["panel"]),
                consents=[broad_consent(kind=rnd.choice(["permit", "permit", "deny"]))] if rnd.random() < 0.7 else [],
            )
            rows.append(row(f"{le}-{i}", content, submitted + "T10:00:00", le))
    return rows


class ComplementarySuppressionTests(unittest.TestCase):
    """No suppressed count of a period is the whole period minus the other periods"""

    def test_no_period_is_alone_suppressed(self):
        rows = synthetic_series()
        full, result = run(rows, min_cell=1, due_days=30), run(rows, min_cell=5, due_days=30)
        self.assertEqual(len(full["rows"]), len(result["rows"]))
        figures, complementary = {}, 0
        for f, r in zip(full["rows"], result["rows"]):
            for field in ("value", "base"):
                if f[field] is None or quartile(r):     # a quartile is a statistic, not a count
                    continue
                hidden = r[field] in (None, "<5")
                figures.setdefault((r["le"], r["block"], r["item"], field), []).append(hidden)
                if hidden:
                    complementary += f[field] >= 5
                    self.assertIsNone(r["percent"])
                else:
                    self.assertEqual(r[field], f[field], (r["le"], r["period"], r["item"], field))
        self.assertGreater(complementary, 0)
        for figure, hidden in figures.items():
            self.assertEqual(len(hidden), len(result["periods"]), figure)
            self.assertNotEqual(sum(hidden), 1, figure)
        # the case count of all LEs over the whole period is shown
        self.assertEqual(get(result, ts.B_CASES, ts.CASES), 72)
        # the counts of the timeline are part of it, and suppressed over the periods as the others
        timeline = {k: v for k, v in figures.items() if k[1] == ts.B_TIMELINE and k[3] == "value"}
        self.assertIn(("LE-B", ts.B_TIMELINE, "GFK2: Fälle", "value"), timeline)
        self.assertGreater(sum(sum(hidden) for hidden in timeline.values()), 0)


class InputOutputTests(unittest.TestCase):

    def test_reads_the_failures_of_the_export_as_the_matrix(self):
        export = {"tables": [
            {"name": "datenkranz", "rows": [
                {"site": "LE1", "useCase": "RD", "patient": "p1", "episodeOfCare": "e1", "tan": "t1",
                 "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1")},
            ]},
            {"name": "failures", "rows": [
                {"site": "LE1", "useCase": "RD", "tan": "t3", "submittedAt": "2025-04-02T00:00:00", "error": "e"},
                {"site": "LE1", "useCase": "MTB", "tan": "t4", "submittedAt": "2025-04-03T00:00:00", "error": "e"},
            ]},
        ]}
        plain = [{"case": {"id": 1}, "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1")}]
        with tempfile.TemporaryDirectory() as tmp:
            for name, data, expected in (("export.json", export, [("LE1", "2025-04-02T00:00:00")]), ("plain.json", plain, [])):
                path = os.path.join(tmp, name)
                with open(path, "w") as f:
                    json.dump(data, f)
                self.assertEqual(ts.load_failures(path), expected)
                self.assertEqual(len(ts.load_failures(path)), len(rdm.load_submissions(path)[1]))
            out = os.path.join(tmp, "series")
            main(os.path.join(tmp, "export.json"), "--output", out, "--min-cell", "1")
            with open(out + ".json", encoding="utf-8") as f:
                result = json.load(f)
        self.assertEqual(result["labels"], {rdm.TOTAL: rdm.TOTAL, "LE1": "LE1 (260000001)"})
        self.assertEqual(get(result, ts.B_SUBMISSIONS, ts.NOT_CONVERTED, "2025-04", "LE1"), 1)
        self.assertEqual(result["periods"], ["2025-03", "2025-04", ts.WHOLE])

    def test_categorical_sections_per_period_with_the_labels_of_the_matrix(self):
        rows = [rdm.SubmissionRow(json.dumps(["LE1", str(i)]), d + "T10:00:00", datenkranz(f"t{i}", submitted=d, gender=g, library=lib), "LE1")
                for i, (d, g, lib) in enumerate([("2025-03-01", "male", "wgs"), ("2025-03-02", "female", "wgs_lr"),
                                                  ("2025-04-01", "female", "wgs")])]
        series = ts.run(rows, [], None, None, date(2026, 10, 30), min_cell=1)
        matrix = rdm.run(rows, [], None, None, None, date(2026, 10, 30), min_cell=1)
        self.assertEqual(get(series, ts.B_SEX, "w", "2025-03", "LE1"), 1)
        self.assertEqual(get(series, ts.B_SEX, "w", "2025-04", "LE1"), 1)
        self.assertEqual(get(series, ts.B_LIBRARY, "lrWGS", "2025-03", "LE1"), 1)
        # the whole period equals the matrix, row for row of these sections
        for block in (ts.B_SEX, ts.B_AGE, ts.B_INSURANCE, ts.B_LIBRARY, ts.B_EXTENT):
            for r in [r for r in matrix["rows"] if r["section"] == block]:
                self.assertEqual(get(series, block, r["item"], ts.WHOLE, "LE1"), r["values"]["LE1"]["value"], (block, r["item"]))

    def test_counts_the_dip_reason_for_a_missing_consent_per_period(self):
        def row(patient, tan, submitted, reason, consents=None):
            return {"site": "LE1", "useCase": "RD", "patient": patient, "episodeOfCare": "e1", "tan": tan,
                    "submittedAt": submitted + "T00:00:00",
                    "content": datenkranz(tan, submitted=submitted, consents=consents or []),
                    "reasonResearchConsentMissing": reason}
        rows = [row("p1", "t1", "2025-03-01", "patient-refusal"),
                row("p2", "t2", "2025-03-02", "technical-issues"),
                row("p3", "t3", "2025-04-01", None),
                row("p4", "t4", "2025-04-02", "something-else"),
                row("p5", "t5", "2025-04-03", None, consents=[broad_consent()])]
        with_column = {"tables": [{"name": "datenkranz", "columns": ["site", "useCase", "patient", "episodeOfCare", "tan",
                                                                     "submittedAt", "content", "reasonResearchConsentMissing"],
                                   "rows": rows}]}
        without = {"tables": [{"name": "datenkranz", "rows": [{k: v for k, v in r.items() if k != "reasonResearchConsentMissing"} for r in rows]}]}
        label = lambda r: f"{ts.CONSENT_MISSING_DIP}: {r}"
        with tempfile.TemporaryDirectory() as tmp:
            results = {}
            for name, data in (("with", with_column), ("without", without)):
                path = os.path.join(tmp, name + ".json")
                with open(path, "w") as f:
                    json.dump(data, f)
                out = os.path.join(tmp, name)
                main(path, "--output", out, "--min-cell", "1")
                with open(out + ".json", encoding="utf-8") as f:
                    results[name] = json.load(f)
                with open(out + ".csv", encoding="utf-8-sig") as f:
                    self.assertNotIn("something-else", f.read())
        r = results["with"]
        self.assertEqual(get(r, ts.B_CONSENT, label("Patient lehnt ab"), "2025-03", "LE1"), 1)
        self.assertEqual(get(r, ts.B_CONSENT, label("technische Gründe"), "2025-03", "LE1"), 1)
        self.assertEqual(get(r, ts.B_CONSENT, label(ts.DIP_NONE), "2025-04", "LE1"), 1)
        self.assertEqual(get(r, ts.B_CONSENT, label(ts.DIP_OTHER), "2025-04", "LE1"), 1)
        self.assertEqual(get(r, ts.B_CONSENT, label(ts.DIP_NOT_EXPORTED), ts.WHOLE, "LE1"), 0)
        # the case with a transmitted consent isn't counted among those without
        self.assertEqual(sum(get(r, ts.B_CONSENT, label(x), ts.WHOLE, "LE1")
                             for x in [l for _, l in ts.DIP_REASONS] + [ts.DIP_OTHER, ts.DIP_NONE]), 4)
        self.assertEqual(get(results["without"], ts.B_CONSENT, label(ts.DIP_NOT_EXPORTED), ts.WHOLE, "LE1"), 4)

    def test_writes_json_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump([
                    {"case": "a", "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1", consents=[broad_consent()])},
                    {"case": "a", "submittedAt": "2025-06-01T00:00:00",
                     "content": datenkranz("t2", type_="followup", submitted="2025-06-01", consents=[broad_consent()],
                                           follow_up=follow_up("2025-05-11"))},
                    {"case": "b", "submittedAt": "2025-03-02T00:00:00", "content": datenkranz("t3", submitted="2025-03-02")},
                    {"case": "c", "submittedAt": "2025-03-03T00:00:00", "content": datenkranz("t4", submitted="2025-03-03")},
                ], f)
            out = os.path.join(tmp, "series")
            notes = main(path, "--output", out, "--min-cell", "1", "--period", "quarter", "--follow-up-due-days", "365",
                         "--reference-date", "2026-10-30")
            with open(out + ".csv", encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
            with open(out + ".json", encoding="utf-8") as f:
                result = json.load(f)
        self.assertIn("Fälle mit GFK2 mindestens 365 Tage vor dem Stichtag 2026-10-30, Follow-ups bis 365 Tage nach", notes)
        self.assertEqual(lines[0], "LE;Zeitraum;Block;Kennzahl;Wert;Bezug;Nenner;Anteil_%;Median;Q1;Q3;n")
        self.assertIn(f"Gesamt;2025-Q1;Fälle;{ts.CASES};3;;;;;;;", lines)
        self.assertIn(f"260000001;gesamt;Forschungseinwilligung;{ts.CONSENT_VALID};1;Fälle;3;33,3;;;;", lines)
        self.assertIn(f"Gesamt;gesamt;Follow-up;{ts.DAYS};;{ts.WITH_GFK2};3;;90,0;90,0;90,0;1", lines)
        self.assertEqual(len(lines) - 1, len(result["rows"]))
        self.assertEqual((result["resolution"], result["follow_up_due_days"], result["reference_date"]), ("quarter", 365, "2026-10-30"))
        self.assertEqual(set(result["rows"][0]), {"le", "period", "block", "item", "base_label", "value", "base", "percent",
                                                  "median", "q1", "q3", "n"})


    def test_from_has_to_be_before_until(self):
        with self.assertRaises(SystemExit):
            main("in.json", "--from", "2026-12-01", "--until", "2026-01-01")
        with self.assertRaises(SystemExit):
            main("in.json", "--from", "2026-01-01", "--until", "2026-01-01")

    def test_notes_name_the_incomplete_periods(self):
        rows = [row("a", datenkranz("t1", submitted="2025-10-20")), row("b", datenkranz("t2", submitted="2026-11-05"))]

        def incomplete(**kwargs):
            return [note for note in run(rows, **kwargs)["notes"] if note.startswith("Unvollständige Zeiträume")]

        cut = incomplete(start=date(2025, 10, 15), end=date(2026, 11, 13))
        self.assertEqual(len(cut), 1)
        self.assertIn("der erste (2025-10) beginnt am 15.10.2025 und der letzte (2026-11) endet am 12.11.2026.", cut[0])
        self.assertIn("der letzte (2026-Q4) endet am 05.11.2026, dem Datum der letzten Einreichung",
                      incomplete(resolution=ts.QUARTER)[0])
        self.assertEqual(incomplete(start=date(2025, 10, 1), end=date(2026, 12, 1)), [])
        self.assertEqual(incomplete(start=date(2025, 10, 1), end=date(2027, 1, 1), resolution=ts.QUARTER), [])


PREVIOUS = "647f21b"    # the last commit before --suppress-scope: its output equals that of --suppress-scope all


class CodeScopeTests(unittest.TestCase):
    """--suppress-scope codes (default): only the rows derived from the diagnosis codes are suppressed, as in the
    matrix; the time series has no such block, so every number is exact, also the diagnosis status and rate"""

    def test_the_time_series_has_no_code_block(self):
        self.assertFalse(ts.is_code_line(ts.Line(ts.B_STATUS, rdm.DIAGNOSED)))
        self.assertFalse(ts.is_code_line(ts.Line(ts.B_RATE, rdm.DIAGNOSED)))
        self.assertTrue(ts.is_code_line(ts.Line("Orphacode", "Erkr. ohne Orphacode (keine Orphanet-Kodierung)")))
        self.assertTrue(ts.is_code_line(ts.Line(rdm.DISEASE_GROUP_BLOCKS[0], rdm.NEUROMUSCULAR)))
        result = run(synthetic(), min_cell=5, scope=rdm.SUPPRESS_CODES)
        self.assertEqual([r for r in result["rows"] if ts.is_code_line(SimpleNamespace(block=r["block"], item=r["item"]))], [])
        self.assertEqual(result["suppress_scope"], rdm.SUPPRESS_CODES)

    def test_every_cell_exact_also_the_diagnosis_status(self):
        for rows in (synthetic(), synthetic(seed=14, sizes=(("LE-A", 6), ("LE-B", 3)))):
            full = run(rows, resolution=ts.QUARTER)
            result = run(rows, min_cell=5, resolution=ts.QUARTER, scope=rdm.SUPPRESS_CODES)
            fields = ("value", "base", "percent", "median", "q1", "q3", "n")
            self.assertEqual([{x: r[x] for x in fields} for r in result["rows"]], [{x: f[x] for x in fields} for f in full["rows"]])
            self.assertTrue(any(isinstance(r["value"], int) and 0 < r["value"] < 5 for r in result["rows"] if r["block"] == ts.B_STATUS))

    def test_small_counts_are_shown_and_old_scopes_refused(self):
        result = cases(*[datenkranz(f"t{i}", gender="male" if i < 2 else "female",
                                    assessment="noGeneticDiagnosis" if i < 2 else "confirmedGeneticDiagnosis")
                         for i in range(7)], min_cell=5, scope=rdm.SUPPRESS_CODES)
        self.assertEqual(get(result, ts.B_SEX, "m"), 2)
        self.assertEqual(get(result, ts.B_STATUS, rdm.UNDIAGNOSED), 2)
        self.assertEqual(get(result, ts.B_STATUS, rdm.UNDIAGNOSED, field="base"), 7)
        self.assertTrue(result["notes"][0].startswith("7 Fälle"))
        self.assertTrue(any("die Zeitreihe hat keine solche Zeile" in n for n in result["notes"]))
        for scope in ("none", "diagnoses"):
            with self.assertRaises(ValueError):
                run([], scope=scope)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
            ts.main(["missing.json", "--suppress-scope", "diagnoses"])
        self.assertIn("replaced by 'codes'", err.getvalue())

    def test_scope_all_is_unchanged(self):
        # the previous version of the script (before --suppress-scope), from the git history
        here = os.path.dirname(os.path.abspath(__file__))
        try:
            source = subprocess.run(["git", "show", f"{PREVIOUS}:scripts/rd_evaluation_matrix/rd_time_series.py"],
                                    cwd=here, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest(f"commit {PREVIOUS} not available")
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "previous_series.py"), "wb") as f:
                f.write(source)
            spec = importlib.util.spec_from_file_location("previous_series", os.path.join(tmp, "previous_series.py"))
            previous = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(previous)
            for rows in (synthetic(), synthetic(seed=14, sizes=(("LE-A", 6), ("LE-B", 3)))):
                path = os.path.join(tmp, "in.json")
                with open(path, "w") as f:
                    json.dump([{"case": r.case, "site": r.site, "submittedAt": r.submitted_at, "content": r.content}
                               for r in rows], f)
                outputs = []
                for name, script, scope in (("new", ts, ["--suppress-scope", "all"]), ("old", previous, [])):
                    out = os.path.join(tmp, name)
                    with contextlib.redirect_stderr(io.StringIO()):
                        script.main([path, "--output", out, "--reference-date", "2026-11-01", "--period", "quarter",
                                     "--follow-up-due-days", "180", *scope])
                    with open(out + ".json", encoding="utf-8") as f:
                        result = json.load(f)
                    with open(out + ".csv", "rb") as f:
                        outputs.append((result, f.read()))
                (new, new_csv), (old, old_csv) = outputs
                self.assertEqual(new_csv, old_csv)
                self.assertEqual(new, {**old, "suppress_scope": "all"})


@unittest.skipIf(plots is None, "plot_time_series.py needs matplotlib")
class PlotTests(unittest.TestCase):

    FIGURES = {f.name: f for f in plots.FIGURES} if plots else {}

    def load(self, result):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "series.csv")
            ts.write_csv(path, result)
            return plots.load(path, ";")

    def rows(self):
        """LE1 with 6 cases a month, LE2 with 1, LE3 with tests only"""
        rows = []
        for month in ("2025-03", "2025-04"):
            for i in range(6):
                consents = [broad_consent()] if i < 5 else []
                rows += submission_series(f"{month}-{i}", datenkranz(f"t{month}{i}", submitted=f"{month}-10", consents=consents),
                                          site="LE1")
            rows += submission_series(f"{month}-x", datenkranz(f"x{month}", submitted=f"{month}-11", consents=[broad_consent()]),
                                      site="LE2")
            rows += submission_series(f"{month}-t", datenkranz(f"y{month}", type_="test", submitted=f"{month}-12"), site="LE3")
        return rows

    def panels(self, fig):
        return [ax.get_title(loc="left") for ax in fig.axes if ax.get_visible()]

    def test_suppressed_values_are_hidden_not_missing(self):
        les, periods, lines, threshold = self.load(run(self.rows(), min_cell=5))
        self.assertEqual((periods, threshold), (["2025-03", "2025-04"], 5))
        figure = self.FIGURES["consent_uebermittelt"]
        self.assertEqual(plots.value(figure, lines, "LE1 (260000001)", "2025-03"), 83.3)
        # of the single case of LE2 the CSV has no values: suppressed, not without cases
        self.assertIs(plots.value(figure, lines, "LE2 (260000001)", "2025-03"), plots.HIDDEN)
        self.assertIs(plots.value(self.FIGURES["faelle"], lines, "LE2 (260000001)", "2025-03"), plots.HIDDEN)
        # the rate whose base would disclose the refusals, of the one case without consent of LE1
        figure = self.FIGURES["consent_zustimmung_ablehnung"]
        self.assertIs(plots.value(figure, lines, "LE1 (260000001)", "2025-03"), plots.HIDDEN)
        # and with 5 refusals next to 6 consents
        contents = [datenkranz(f"t{i}", consents=[broad_consent()]) for i in range(6)]
        contents += [without_consent(f"r{i}", ("2025-01-01", REFUSES)) for i in range(5)]
        les, periods, lines, threshold = self.load(cases(*contents, min_cell=5))
        self.assertEqual(plots.value(figure, lines, "260000001", "2025-03"), 54.5)

    def test_stacked_bar_shows_what_is_not_suppressed(self):
        figure = self.FIGURES["consent_fehlt"]
        reasons = [f"{ts.CONSENT_MISSING}: {r}" for r in ts.REASONS]

        def bar(*counts):
            """Of 20 cases 12 without consent, by reason"""
            cells = {(ts.B_CASES, ts.CASES): {"Wert": 20.0, "Nenner": None},
                     (ts.B_CONSENT, ts.CONSENT_MISSING): {"Wert": 12.0, "Nenner": 20.0, "Anteil_%": 60.0}}
            cells.update({(ts.B_CONSENT, r): {"Wert": c, "Nenner": 12.0} for r, c in zip(reasons, counts + (0.0,) * 9)})
            return plots.bar(figure, {("LE", block, item): {"p": cell} for (block, item), cell in cells.items()}, "LE", "p")

        hidden = plots.HIDDEN
        # unable, refuses, not returned, other patient reason, technical, organizational: all shown
        heights, total, suppressed = bar(1.0, 6.0, 5.0, 0.0, 0.0, 0.0)
        self.assertEqual((heights, total, suppressed), ([30.0, 30.0, 0.0, 0.0], 60.0, 0))
        self.assertEqual(plots.rest(heights, total, suppressed), 0)
        # one reason suppressed: the other reasons of its group are drawn, the rest isn't, it would disclose the reason
        heights, total, suppressed = bar(hidden, 6.0, 5.0, 0.0, 0.0, 0.0)
        self.assertEqual((heights, suppressed), ([30.0, 25.0, 0.0, 0.0], 1))
        self.assertEqual(plots.rest(heights, total, suppressed), 0)
        # two suppressed: the rest up to the share of the LE is outlined
        heights, total, suppressed = bar(hidden, 6.0, hidden, 0.0, 0.0, 0.0)
        self.assertEqual((heights, suppressed), ([30.0, 0.0, 0.0, 0.0], 2))
        self.assertEqual(plots.rest(heights, total, suppressed), 30.0)
        self.assertEqual(plots.rest(heights, hidden, suppressed), 0)

    def test_pages(self):
        les, periods, lines, threshold = self.load(run(self.rows(), min_cell=1, due_days=30))
        self.assertEqual(les, [rdm.TOTAL, "LE1 (260000001)", "LE2 (260000001)", "LE3"])

        def page(name):
            return plots.page(self.FIGURES[name], les, periods, lines, threshold, "level")

        # a panel per LE with a value, in the order of the values: LE3 has only tested
        self.assertEqual(self.panels(page("faelle")), ["LE1", "LE2"])
        self.assertEqual(self.panels(page("consent_uebermittelt")), ["LE2", "LE1"])     # 100 %, 83.3 %
        self.assertEqual(self.panels(page("einreichungen")), ["LE1", "LE2"])
        # the durations of all follow-ups give way to those of the due cases; no page without values
        self.assertIsNone(page("followup_tage"))
        self.assertIsNone(page("followup_faellig_tage"))
        self.assertIsNone(page("consent_zustimmung_ablehnung"))     # no refusal is documented
        plots.plt.close("all")

    def test_writes_pdf_and_pngs_or_says_why_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, out = os.path.join(tmp, "series.csv"), os.path.join(tmp, "plots")
            ts.write_csv(path, run(self.rows(), min_cell=1))
            messages = io.StringIO()
            with contextlib.redirect_stderr(messages):
                plots.main([path, "--output", out])
            written = sorted(os.listdir(tmp))
            self.assertIn("plots.pdf", written)
            self.assertIn("plots_consent_zustimmung.png", written)
            self.assertNotIn("plots_followup_tage.png", written)
            self.assertIn("Not drawn, not in the CSV or without values:", messages.getvalue())
            # no periods: an error, not a division by zero
            ts.write_csv(path, run([]))
            with self.assertRaises(ValueError):
                plots.load(path, ";")
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                plots.main([path, "--output", out])


if __name__ == "__main__":
    unittest.main()
