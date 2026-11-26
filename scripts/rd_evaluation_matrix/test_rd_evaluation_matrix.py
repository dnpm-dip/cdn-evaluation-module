"""Tests of rd_evaluation_matrix.py: python3 -m unittest discover scripts/rd_evaluation_matrix"""

import json
import os
import random
import tempfile
import unittest
from datetime import date

import rd_evaluation_matrix as rdm

HERE = os.path.dirname(os.path.abspath(__file__))

MDAT_RESEARCH_USE = "2.16.840.1.113883.3.1937.777.24.5.3.8"


def broad_consent(start="2025-01-01", kind="permit", code=MDAT_RESEARCH_USE):
    """A FHIR Consent as in researchConsents[].scope, with one nested provision"""
    return {
        "resourceType": "Consent",
        "status": "active",
        "provision": {
            "type": "deny",
            "period": {"start": start},
            "provision": [
                {"type": kind, "period": {"start": start, "end": "2055-01-01"},
                 "code": [{"coding": [{"system": "urn:oid:2.16.840.1.113883.3.1937.777.24.5.3", "code": code}]}]}
            ],
        },
    }


def datenkranz(
    tan, type_="initial", submitted="2025-03-01", gender="female", birth="2015-06", coverage="GKV",
    gfk1="2025-01-10", plan_date="2025-02-10", assessment="confirmedGeneticDiagnosis", extent="trioGenome",
    library="wgs", include=True, consents=(), orpha="ORPHA:558", icd10="Q87.4", prior=None, plan=None,
    submitter="260000001", mv_consent="permit", priors=None, molecular=None, follow_up=None,
):
    """A RD Datenkranz submission with the fields the matrix reads"""
    diagnoses = [{"code": icd10, "system": rdm.ICD10GM}]
    if orpha:
        diagnoses.append({"code": orpha, "system": rdm.ORPHANET})
    content = {
        "metaData": {
            "submission": {"date": submitted, "type": type_, "diseaseType": "rare", "submitterId": submitter},
            "mvConsent": {"version": "1", "scope": [{"date": "2025-01-01", "domain": "mvSequencing", "type": mv_consent}]},
            "coverageType": coverage,
            "researchConsents": [{"presentationDate": "2025-01-01", "scope": c} for c in consents],
            "tanC": tan,
            "gender": gender,
            "birthDate": birth,
            "decisionToInclude": include,
            "molecularBoardDecisionDate": gfk1,
        },
        "case": {
            "diagnosisRd": {
                "molecularBoardDecisionDate": gfk1,
                "diagnosticExtent": extent,
                "libraryType": library,
                "diagnosticAssessment": assessment,
                "diagnoses": diagnoses,
            }
        },
    }
    if prior:
        content["case"]["priorRds"] = [prior]
    if priors is not None:
        content["case"]["priorRds"] = priors
    if molecular:
        content["molecular"] = molecular
    if follow_up:
        content["followUp"] = {"followUpRds": [follow_up]}
    if plan_date:
        content["plan"] = {
            "carePlanRd": {
                "molecularBoardDecisionDate": plan_date,
                "studyRecommended": False, "counsellingRecommended": False, "reEvaluationRecommended": False,
                "therapyRecommended": False, "clinicalManagementRecommended": False,
                **(plan or {}).get("carePlanRd", {}),
            },
            **{k: v for k, v in (plan or {}).items() if k != "carePlanRd"},
        }
    return content


def row(case, content, submitted_at="2025-03-01T10:00:00", site=None):
    return rdm.SubmissionRow(json.dumps(case), submitted_at, content, site)


# Orpha code -> head, as from the linearisation
HEADS = {"558": "93419", "98006": "98006", "777": "98053", "999": "123456", "616874": "616874"}

REFERENCE = date(2026, 10, 30)


def run(rows, heads=HEADS, start=None, end=None, failures=(), min_cell=1, scope=rdm.SUPPRESS_ALL):
    """The matrix; suppression in all rows unless scope says otherwise (the tests of the rules of the suppression)"""
    return rdm.run(rows, list(failures), heads, start, end, REFERENCE, min_cell=min_cell, suppress_scope=scope)


def get(result, section, item, entity=rdm.TOTAL, field="value"):
    """A value of the result, by section (or its start) and item"""
    matches = [r for r in result["rows"] if r["section"].startswith(section) and r["item"] == item]
    if len(matches) != 1:
        raise KeyError(f"{section} | {item}: {len(matches)} rows")
    return matches[0]["values"][entity][field]


def cases(*contents, **kwargs):
    return run([row(str(i), c) for i, c in enumerate(contents)], **kwargs)


def problems(result, kind):
    return [p for p in result["problems"] if p["problem"] == kind]


class CaseTests(unittest.TestCase):

    def test_one_case_per_episode_with_initial_status_from_latest_non_test(self):
        rows = [
            row("a", datenkranz("t1", assessment="noGeneticDiagnosis"), "2025-03-01T10:00:00"),
            row("a", datenkranz("t2", type_="followup", submitted="2025-06-01"), "2025-06-01T10:00:00"),
            row("a", datenkranz("t3", type_="test", assessment="noGeneticDiagnosis"), "2025-07-01T10:00:00"),
            row("b", datenkranz("t4", type_="followup")),               # no initial
            row("c", datenkranz("t5", type_="test")),                    # test only
        ]
        result = run(rows)
        self.assertEqual(get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)"), 1)
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", "Diagnose"), 1)          # from t2
        self.assertEqual(get(result, "Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)"), 1)
        self.assertEqual([p["tan"] for p in problems(result, rdm.P_WITHOUT_INITIAL)], ["t4"])
        self.assertIn("2 Testeinreichungen verworfen.", result["notes"])

    def test_latest_submission_by_parsed_submitted_at_across_time_zones(self):
        # the follow-up is later in time (09:30 UTC) than the initial (10:00+02:00 = 08:00 UTC), but sorts first as text
        rows = [
            row("a", datenkranz("t1", assessment="noGeneticDiagnosis"), "2025-03-01T10:00:00+02:00"),
            row("a", datenkranz("t2", type_="followup", submitted="2025-03-01"), "2025-03-01T09:30:00Z"),
        ]
        result = run(rows)
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", "Diagnose"), 1)          # latest = t2
        self.assertEqual([r.submitted_at for r in sorted([rows[1], rows[0]], key=rdm.submitted_order)],
                         ["2025-03-01T10:00:00+02:00", "2025-03-01T09:30:00Z"])

    def test_period_applies_to_the_initial_submission_and_names_the_title(self):
        rows = [
            row("a", datenkranz("t1", submitted="2024-06-30")),
            row("b", datenkranz("t2", submitted="2024-07-01")),
            row("b", datenkranz("t3", type_="followup", submitted="2026-02-01"), "2026-02-01T00:00:00"),
            row("c", datenkranz("t4", submitted="2026-01-01")),
        ]
        result = run(rows, start=date(2024, 7, 1), end=date(2026, 1, 1))
        self.assertEqual(get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)"), 1)
        self.assertEqual(result["title"], "AUSWERTUNG 01.07.2024 - 31.12.2025")
        result = run(rows)
        self.assertEqual(result["period"], "gesamter Export (Erstmeldungen 30.06.2024 - 01.01.2026)")

    def test_other_disease_types_are_ignored(self):
        mtb = datenkranz("t1")
        mtb["metaData"]["submission"]["diseaseType"] = "oncological"
        self.assertEqual(get(run([row("a", mtb)]), "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)"), 0)


class LeTests(unittest.TestCase):

    def test_values_per_le_by_site_else_submitter(self):
        rows = [
            row("a", datenkranz("t1"), site="UKT"),
            row("b", datenkranz("t2", assessment="noGeneticDiagnosis"), site="UKT"),
            row("c", datenkranz("t3", submitter="260000002")),
        ]
        result = run(rows)
        self.assertEqual(result["entities"], [rdm.TOTAL, "260000002", "UKT"])
        self.assertEqual(result["labels"]["UKT"], "UKT (260000001)")
        total = "Fallzahl gesamt (Episoden mit Erstmeldung)"
        self.assertEqual([get(result, "Fälle", total, e) for e in result["entities"]], [3, 1, 2])
        self.assertEqual(get(result, "Diagnoserate", "Diagnose", "UKT", "percent"), 50.0)
        # every LE has the same rows
        self.assertTrue(all(set(r["values"]) == set(result["entities"]) for r in result["rows"]))

    def test_small_counts_are_suppressed(self):
        result = cases(*[datenkranz(f"t{i}", gender="male" if i < 2 else "female") for i in range(8)], min_cell=5)
        self.assertEqual(get(result, "Geschlecht", "m"), "<5")
        self.assertIsNone(get(result, "Geschlecht", "m", field="percent"))
        # w would give m away as the case count minus w: suppressed as well (complementary); the case count is shown
        self.assertEqual((get(result, "Geschlecht", "w"), get(result, "Geschlecht", "w", field="percent")), ("<5", None))
        self.assertEqual(get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)"), 8)
        self.assertEqual(get(result, "Geschlecht", "d"), 0)
        # a duration statistic from 8 values is shown, from 1 value suppressed
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch", "Median (Tage)"), 31.0)
        few = cases(datenkranz("t1"), min_cell=5)
        self.assertEqual(get(few, "Bearbeitungsdauer medizinisch", "Median (Tage)"), "<5")

    def test_gesamt_is_not_suppressed_unless_it_is_a_single_les_count(self):
        rows = ([row(f"a{i}", datenkranz(f"a{i}", gender="male" if i < 2 else "female"), site="A") for i in range(8)]
                + [row(f"b{i}", datenkranz(f"b{i}", gender="male" if i < 1 else "female"), site="B") for i in range(8)])
        result = run(rows, min_cell=5)
        # m: 2 + 1, small in both LEs, shown in Gesamt; so is w (13), which no longer protects m in Gesamt
        self.assertEqual([get(result, "Geschlecht", "m", e) for e in (rdm.TOTAL, "A", "B")], [3, "<5", "<5"])
        self.assertEqual([get(result, "Geschlecht", "w", e) for e in (rdm.TOTAL, "A", "B")], [13, "<5", "<5"])
        self.assertEqual(get(result, "Geschlecht", "m", field="percent"), 18.8)
        # a count of LE A alone (the other LE 0): Gesamt would give it away, so it is suppressed as well
        single = run(rows[:8] + [row(f"b{i}", datenkranz(f"b{i}", gender="female"), site="B") for i in range(8)],
                     min_cell=5)
        self.assertEqual([get(single, "Geschlecht", "m", e) for e in (rdm.TOTAL, "A", "B")], ["<5", "<5", 0])

    def test_losses_per_le(self):
        failures = [rdm.Failure("UKT", '["UKT", "p9", "e1"]', "t9", "conversion failed: x")]
        result = run([row("a", datenkranz("t1"), site="UKT")], failures=failures)
        self.assertEqual(get(result, "Fälle", "Einreichungen nicht konvertierbar (nicht in den Fällen)", "UKT"), 1)
        self.assertEqual(problems(result, rdm.P_NOT_CONVERTED)[0]["detail"], "conversion failed: x")
        self.assertEqual(get(result, "Fälle", "davon Episode mit älterem Stand ausgewertet (eine spätere Einreichung nicht konvertierbar)", "UKT"), 0)

    def test_a_failure_of_an_evaluated_episode_is_marked(self):
        # a later submission of an evaluated episode could not be converted: the case keeps its older state
        failures = [rdm.Failure("UKT", json.dumps("a"), "t2", "conversion failed: x")]
        result = run([row("a", datenkranz("t1"), site="UKT")], failures=failures)
        self.assertEqual(get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)", "UKT"), 1)
        self.assertEqual(get(result, "Fälle", "davon Episode mit älterem Stand ausgewertet (eine spätere Einreichung nicht konvertierbar)", "UKT"), 1)
        self.assertTrue(problems(result, rdm.P_NOT_CONVERTED)[0]["detail"].startswith("Episode mit älterem Stand ausgewertet"))


class RowTests(unittest.TestCase):

    def test_consents(self):
        result = cases(
            datenkranz("t1", consents=[broad_consent()]),
            datenkranz("t2", consents=[broad_consent(start="2020-06-01")]),   # older than 5 years at the reference date
            datenkranz("t3", consents=[broad_consent(kind="deny")]),
            datenkranz("t4", consents=[broad_consent(code="2.16.840.1.113883.3.1937.777.24.5.3.99")], mv_consent="deny"),
            datenkranz("t5"),
        )
        self.assertEqual(get(result, "Einwilligung", "Broad Consent gültig zum Stichtag"), 1)
        # at the submission (2025-03-01) the consent of 2020 was still valid
        self.assertEqual(get(result, "Einwilligung", "Broad Consent gültig zum Datum der letzten Einreichung"), 2)
        self.assertEqual(get(result, "Einwilligung", "MV-Einwilligung Sequenzierung (mvSequencing: permit)"), 4)
        self.assertEqual(get(result, "Broad Consent nach Altersgruppe", rdm.CHILDREN, field="percent"), 40.0)

    def test_reasons_for_a_missing_consent_per_le_and_month(self):
        def r(case, tan, submitted, reason, site, consents=()):
            return rdm.SubmissionRow(json.dumps(case), submitted + "T10:00:00",
                                     datenkranz(tan, submitted=submitted, consents=consents), site, reason)
        rows = [
            r("a", "t1", "2025-03-01", "patient-refusal", "UKT"),
            r("b", "t2", "2025-03-02", "technical-issues", "UKT"),
            r("c", "t3", "2025-04-01", None, "UKT"),
            r("d", "t4", "2025-04-02", "something-else", "UKE"),
            r("e", "t5", "2025-04-03", None, "UKE", consents=[broad_consent()]),     # transmitted: not counted
            # the reason of the latest submission counts
            r("a", "t6", "2025-05-01", "organizational-issues", "UKT"),
        ]
        rows[-1].content["metaData"]["submission"]["type"] = "correction"
        result = run(rows)
        s = "Broad Consent nicht übermittelt: Grund laut DIP"
        self.assertEqual(get(result, s, "Fälle ohne übermittelten Broad Consent"), 4)
        self.assertEqual([get(result, s, l, "UKT") for l in ("organisatorische Gründe", "technische Gründe", "kein Grund angegeben")], [1, 1, 1])
        self.assertEqual(get(result, s, "Patient lehnt ab", "UKT"), 0)
        self.assertEqual(get(result, s, "anderer Wert", "UKE"), 1)
        self.assertEqual(get(result, s, "organisatorische Gründe", field="base"), 4)
        m = "Broad Consent nicht übermittelt: Grund laut DIP, nach Monat der Erstmeldung"
        self.assertEqual(get(result, m, "2025-03: organisatorische Gründe", "UKT"), 1)
        self.assertEqual(get(result, m, "2025-03: technische Gründe", "UKT"), 1)
        self.assertEqual(get(result, m, "2025-04: kein Grund angegeben", "UKT"), 1)
        self.assertEqual(get(result, m, "2025-04: anderer Wert", "UKE"), 1)
        self.assertEqual(get(result, m, "2025-04: kein Grund angegeben", "UKE", field="base"), 1)
        self.assertNotIn("something-else", json.dumps(result, ensure_ascii=False))

    def test_reasons_without_the_export_column(self):
        result = cases(datenkranz("t1"), datenkranz("t2", consents=[broad_consent()]))
        s = "Broad Consent nicht übermittelt: Grund laut DIP"
        self.assertEqual(get(result, s, rdm.REASON_NOT_EXPORTED), 1)
        self.assertFalse([r for r in result["rows"] if r["section"].endswith("nach Monat der Erstmeldung")])
        self.assertTrue(any("reasonResearchConsentMissing nicht" in n for n in result["notes"]))

    def test_a_denial_wins_over_later_consents(self):
        self.assertFalse(rdm.permits_research_use(
            [{"scope": broad_consent(kind="deny")}, {"scope": broad_consent()}], REFERENCE))

    def test_sex_age_insurance(self):
        result = cases(
            datenkranz("t1", gender="male", birth="2024-12", gfk1="2025-01-08", coverage="PKV", plan_date=None),  # 38 days
            datenkranz("t2", birth="2025-01", gfk1="2025-02-07", plan_date=None),                                 # 37 days
            datenkranz("t3", gender="other", birth="2007-01", gfk1="2025-01-01", coverage="BG"),                  # 18
            datenkranz("t4", gender="unknown", birth="2007-02", gfk1="2025-01-31"),                               # 17
            datenkranz("t5", birth="1960-01", gfk1="2025-01-10"),                                                 # 65
        )
        self.assertEqual([get(result, "Geschlecht", s) for s in ("m", "w", "d", "unbekannt")], [1, 2, 1, 1])
        self.assertEqual([get(result, "Alter bei Einschluss", g) for g in rdm.AGE_GROUPS], [1, 1, 1, 1, 1])
        self.assertEqual(rdm.NEONATES, "Neonaten (bis 37 Tage)")
        self.assertEqual([get(result, "Versicherung", i) for i in ("GKV", "PKV", "andere")], [3, 1, 1])

    def test_durations_from_gfk1_to_gfk2_and_to_its_first_submission(self):
        rows = [
            # only the indication care plan at first, GFK2 comes with the follow-up
            row("a", datenkranz("t1", gfk1="2025-01-10", plan_date="2025-01-10", submitted="2025-01-20"), "2025-01-20T00:00:00"),
            row("a", datenkranz("t2", type_="followup", gfk1="2025-01-10", plan_date="2025-03-11", submitted="2025-04-10"), "2025-04-10T00:00:00"),
            row("a", datenkranz("t3", type_="followup", gfk1="2025-01-10", plan_date="2025-06-01", submitted="2025-06-05"), "2025-06-05T00:00:00"),
            row("b", datenkranz("t4", gfk1="2025-01-10", plan_date="2025-01-20", submitted="2025-01-21")),
            row("c", datenkranz("t5", plan_date=None)),
        ]
        result = run(rows)
        medical, documentary = "Bearbeitungsdauer medizinisch", "Bearbeitungsdauer dokumentarisch"
        self.assertEqual(get(result, medical, "Mittelwert (Tage)"), 35.0)    # (60 + 10) / 2
        self.assertEqual(get(result, medical, "Median (Tage)"), 35.0)
        self.assertEqual(get(result, medical, "1. Quartil (Tage)"), 22.5)
        self.assertEqual(get(result, medical, "Maximum (Tage)"), 60.0)
        self.assertEqual(get(result, documentary, "Mittelwert (Tage)"), 15.5)  # (30 + 1) / 2
        self.assertEqual((get(result, medical, "Dauer berechenbar"), get(result, medical, "Dauer berechenbar", field="percent")),
                         (2, 66.7))
        self.assertEqual(get(result, medical, "nicht berechenbar: noch keine GFK2"), 1)
        # 10 days from GFK1 to GFK2 is implausibly short
        self.assertEqual([p["case"] for p in problems(result, rdm.P_SHORT_MEDICAL)], ['"b"'])

    def test_impossible_dates_are_problems_and_left_out_of_the_durations(self):
        result = cases(
            datenkranz("t1", gfk1="2025-01-10", plan_date="2025-02-10", submitted="2025-03-01"),
            # GFK2 three years after the submission reporting it
            datenkranz("t2", gfk1="2025-01-10", plan_date="2028-02-10", submitted="2025-03-01"),
            # GFK1 after the submission reporting it
            datenkranz("t3", gfk1="2025-04-10", plan_date="2025-05-10", submitted="2025-03-01"),
        )
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch", "Mittelwert (Tage)"), 31.0)
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch", "nicht berechenbar: unmögliches Datum (siehe Rückfragen)"), 2)
        gfk2 = problems(result, rdm.P_GFK2_AFTER_SUBMISSION)
        self.assertEqual([p["tan"] for p in gfk2], ["t2", "t3"])
        self.assertIn("2028-02-10", gfk2[0]["detail"])
        self.assertIn("1076 Tage", gfk2[0]["detail"])
        self.assertEqual([p["tan"] for p in problems(result, rdm.P_GFK1_AFTER_SUBMISSION)], ["t3"])
        self.assertEqual(get(result, "Rückfragen an LE", rdm.P_GFK2_AFTER_SUBMISSION), 2)

    def test_an_impossible_date_of_an_earlier_submission_is_not_hidden_by_a_later_one(self):
        rows = [
            # the initial submission reports a board after its own submission date
            row("a", datenkranz("t1", submitted="2025-01-05", gfk1="2025-01-10", plan_date=None), "2025-01-05T10:00:00"),
            row("a", datenkranz("t2", type_="followup", submitted="2025-04-01", gfk1="2025-01-10", plan_date="2025-03-01"),
                "2025-04-01T10:00:00"),
        ]
        result = run(rows)
        self.assertEqual([p["tan"] for p in problems(result, rdm.P_GFK1_AFTER_SUBMISSION)], ["t2"])
        self.assertIn("Einreichung t1", problems(result, rdm.P_GFK1_AFTER_SUBMISSION)[0]["detail"])
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch", "Dauer berechenbar"), 0)

    def test_sequencing_and_diagnosis_rates(self):
        result = cases(
            datenkranz("t1", extent="trioGenome", library="wgs", birth="1990-01"),
            datenkranz("t2", extent="trioGenome", library="wgs_lr", assessment="partialGeneticDiagnosis", birth="1990-01"),
            datenkranz("t3", extent="singleGenome", library="none", assessment="noGeneticDiagnosis", include=False, plan_date=None),
            datenkranz("t4", extent="singleGenome", library="panel", assessment="suspectedGeneticDiagnosis"),
            datenkranz("t5", extent="duoGenome", assessment="noGeneticDiagnosis"),
        )
        self.assertEqual([get(result, "Sequenzierung", l) for l in ("WGS", "lrWGS", "Panel")], [2, 1, 1])
        # only genome sequenced cases by extent; the panel is a problem
        self.assertEqual([get(result, "WGS-Seq. Art", e) for e in ("Single", "Duo", "Trio")], [0, 1, 2])
        self.assertEqual([p["tan"] for p in problems(result, rdm.P_NO_GENOME)], ["t4"])
        self.assertEqual(get(result, "Indikation", "Keine Indikation zur Genomsequenzierung (metaData.decisionToInclude = false)"), 1)
        # t3 was not sequenced: counted as a case, but not in the diagnosis status (it would be "offen" forever)
        self.assertEqual(get(result, "Fälle", "davon nicht sequenziert (keine Indikation oder libraryType none, nicht im Diagnosestatus)"), 1)
        self.assertEqual([get(result, "Diagnosestatus (alle Fälle)", s) for s in
                          ("Diagnose", "Unklar", "Undiagnostiziert", "offen (Undiagnostiziert, noch keine GFK2)")], [1, 2, 1, 0])
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", "Diagnose", field="base"), 4)
        self.assertEqual((get(result, "Diagnoserate", "Diagnose"), get(result, "Diagnoserate", "Diagnose", field="percent")), (1, 25.0))
        self.assertEqual(get(result, "Diagnoserate", "davon partiell (unter Unklar)"), 1)
        self.assertEqual(get(result, "Diagnoserate nach WGS-Seq. Art", "Trio: Diagnose", field="percent"), 50.0)
        self.assertEqual(get(result, "Diagnosestatus nach Altersgruppe", f"{rdm.ADULTS}: Unklar"), 1)
        # the only child (t3) was not sequenced
        self.assertEqual(get(result, "Diagnosestatus nach Altersgruppe", f"{rdm.CHILDREN}: offen"), 0)

    def test_prior_findings(self):
        result = cases(
            datenkranz("t1", priors=[{"genomicTestType": "exome", "diagnosticDate": "2021-05-01"},
                                     {"genomicTestType": "panel", "diagnosticDate": "2026-01-02"}], gfk1="2025-01-10"),
            datenkranz("t2", prior={"genomicTestType": "karyotyping"}),
            datenkranz("t3"),
            datenkranz("t4", prior={"genomicTestType": "none"}),
            datenkranz("t5", assessment="noGeneticDiagnosis", prior={"genomicTestType": "genomeLongRead", "diagnosticDate": "2016-01-01"}),
        )
        s = "Vorbefunde, Status Diagnose"
        self.assertEqual([get(result, s, t) for t in ("Exom", "Panel", rdm.OTHER_TESTS, rdm.PRIOR_NONE_PERFORMED, rdm.PRIOR_NOT_DOCUMENTED)],
                         [1, 1, 1, 1, 1])
        self.assertEqual(get(result, s, "Exom", field="percent"), 25.0)
        years = "Vorbefunde nach Jahr, Status Diagnose"
        self.assertEqual(get(result, years, "Exom: 2021/2020"), 1)
        self.assertEqual(get(result, years, "Panel: 2026 und später"), 1)
        self.assertEqual(get(result, years, f"{rdm.OTHER_TESTS}: unbekannt"), 1)
        self.assertEqual(get(result, "Vorbefunde nach Jahr, Status Undiagnostiziert", "Genom (LR): 2017 und älter"), 1)
        # the panel of 2026 is after the indication board of 2025-01-10
        self.assertEqual([p["tan"] for p in problems(result, rdm.P_PRIOR_AFTER_GFK1)], ["t1"])

    def test_recommendations_over_all_submissions(self):
        rows = [
            row("a", datenkranz("t1", plan={"carePlanRd": {"studyRecommended": True, "clinicalManagementRecommended": True,
                                                           "clinicalManagementDescriptions": ["gp"]}}), "2025-03-01T00:00:00"),
            row("a", datenkranz("t2", type_="followup", plan={
                "carePlanRd": {"therapyRecommended": True, "clinicalManagementRecommended": True,
                               "clinicalManagementDescriptions": ["specialist"]},
                "recommendedTherapies": [
                    {"identifier": "r1", "type": "symptomatic", "strategy": "systemicMedication"},
                    {"identifier": "r2", "type": "symptomatic", "strategy": "targetedMedication"},
                    {"identifier": "r3", "type": "causal", "strategy": "genetic"},
                    {"identifier": "r4", "type": "causal", "strategy": "combination"},
                    {"identifier": "r5", "type": "causal", "strategy": "nutrition"},
                ]}), "2025-04-01T00:00:00"),
            row("b", datenkranz("t3", plan={"carePlanRd": {"clinicalManagementRecommended": True}})),
            row("c", datenkranz("t4")),
        ]
        result = run(rows)
        self.assertEqual([get(result, "Weiterempfehlungen", l) for _, l in rdm.RECOMMENDATIONS], [1, 0, 0, 1])
        s = "Therapieempfehlungen (Fälle"
        self.assertEqual([get(result, s, i) for i in ("Symptomatisch medikamentös", "Kausal interventionell",
                                                      "Kausal nicht medikamentös", "Kausal nicht zuordenbar")], [1, 1, 1, 1])
        self.assertEqual(get(result, "Therapieempfehlungen nach Strategie", "Kausal combination"), 1)
        self.assertEqual(get(result, "Therapieempfehlungen (Anzahl", "Empfehlungen gesamt"), 5)
        s = "Weitere Empfehlungen Krankheitsmanagement"
        self.assertEqual([get(result, s, i) for i in ("6) Hausarzt", "7) Niedergelassener Facharzt",
                                                      rdm.MANAGEMENT_UNSPECIFIED, rdm.MANAGEMENT_NOT_RECOMMENDED)], [1, 1, 1, 1])

    def test_without_orphacode(self):
        no_orpha = datenkranz("t1", orpha=None)
        no_orpha["case"]["diagnosisRd"]["noMatchingCodeExists"] = True
        with_orpha = datenkranz("t3")
        with_orpha["case"]["diagnosisRd"]["noMatchingCodeExists"] = True
        result = cases(no_orpha, datenkranz("t2", orpha=None), with_orpha, datenkranz("t4"))
        self.assertEqual((get(result, "Orphacode", "Erkr. ohne Orphacode (keine Orphanet-Kodierung)"),
                          get(result, "Orphacode", "Erkr. ohne Orphacode (keine Orphanet-Kodierung)", field="percent")), (2, 50.0))
        self.assertEqual(get(result, "Orphacode", "ohne Orphacode, davon noMatchingCodeExists = true"), 1)
        self.assertEqual(get(result, "Orphacode", "mit Orphacode, aber noMatchingCodeExists = true"), 1)

    def test_fill_rates(self):
        variant = {"zygosity": "heterozygous", "acmgClass": "5", "cdnaChange": "c.1A>G"}
        result = cases(
            datenkranz("t1", molecular={"smallVariants": [variant, {"zygosity": "homozygous"}], "copyNumberVariants": [{}]},
                       follow_up={"followUpDate": "2025-06-01", "vitalStatus": "deceased", "diseaseProgression": "x"}),
            datenkranz("t2", follow_up={"followUpDate": "2025-06-01", "vitalStatus": "living"}),
            datenkranz("t3"),
        )
        s = "Füllgrad optionaler Felder (Varianten"
        self.assertEqual(get(result, s, "Varianten gesamt"), 3)
        self.assertEqual((get(result, s, "Zygotie (zygosity)"), get(result, s, "Zygotie (zygosity)", field="percent")), (2, 66.7))
        self.assertEqual(get(result, s, "HGVS p. (proteinChange)"), 0)
        s = "Füllgrad optionaler Felder (Fälle)"
        self.assertEqual((get(result, s, "followUp"), get(result, s, "followUp", field="base")), (2, 3))
        self.assertEqual((get(result, s, "followUp.diseaseProgression"), get(result, s, "followUp.diseaseProgression", field="base")), (1, 2))
        self.assertEqual((get(result, s, "followUp.deathDate"), get(result, s, "followUp.deathDate", field="base")), (0, 1))


class FollowUpTests(unittest.TestCase):
    """Follow-up after GFK2 at the reference date (2026-10-30), interval --follow-up-months"""

    def test_state_days_and_mandatory_fields(self):
        def fu(d, vital="living", **extra):
            return {"followUpDate": d, "diagnosisEstablished": False, "vitalStatus": vital, **extra}
        rows = [
            # GFK2 2025-02-10, follow-up 2 months before the reference: current
            row("a", datenkranz("a1")), row("a", datenkranz("a2", type_="followup", submitted="2026-09-01",
                                                            follow_up=fu("2026-08-30")), "2026-09-01T10:00:00"),
            # follow-up 15 months before: overdue (an earlier follow-up of another submission counts once)
            row("b", datenkranz("b1", follow_up=fu("2025-07-01"))),
            # GFK2 2 months before the reference: not due
            row("c", datenkranz("c1", gfk1="2026-07-01", plan_date="2026-08-30", submitted="2026-09-01")),
            # deceased, without deathDate and diagnosisEstablished
            row("d", datenkranz("d1", follow_up={"followUpDate": "2025-12-01", "vitalStatus": "deceased"})),
            # no GFK2: not in the follow-up rows
            row("e", datenkranz("e1", plan_date=None, assessment="undiagnosed")),
        ]
        result = run(rows)
        s = rdm.FU_SECTION
        state = [get(result, s, f"Stand am Stichtag (Intervall 12 Monate): {st}") for st in rdm.FU_STATES]
        self.assertEqual(state, [1, 1, 1, 1])
        self.assertEqual(get(result, s, "mit Follow-up (followUpRds, alle Einreichungen)"), 3)
        self.assertEqual(get(result, s, "mit Follow-up (followUpRds, alle Einreichungen)", field="base"), 4)
        self.assertEqual(get(result, s, "Follow-up aktuell, von fälligen", field="percent"), 50.0)
        s = rdm.FU_DAYS_SECTION
        self.assertEqual(get(result, s, "Dauer berechenbar (Follow-up ab GFK2)"), 3)
        self.assertEqual(get(result, s, "Minimum (Tage)"), 141.0)             # 2025-02-10 to 2025-07-01
        s = rdm.FU_FIELDS_SECTION
        self.assertEqual(get(result, s, "Follow-up-Einträge gesamt"), 3)
        self.assertEqual(get(result, s, "diagnosisEstablished angegeben (true/false)"), 2)
        self.assertEqual(get(result, s, "deathDate, von Einträgen mit vitalStatus deceased"), 0)
        self.assertEqual(get(result, s, "deathDate, von Einträgen mit vitalStatus deceased", field="base"), 1)
        # with an interval of 24 months no case is due yet; the rows are exact in the diagnosis scope
        longer = rdm.run(rows, [], HEADS, None, None, REFERENCE, min_cell=5, follow_up_months=24)
        self.assertEqual(get(longer, rdm.FU_SECTION, f"Stand am Stichtag (Intervall 24 Monate): {rdm.FU_NOT_DUE}"), 3)
        self.assertTrue(any(n.startswith(rdm.FU_NOTE) for n in longer["notes"]))


class DiseaseGroupTests(unittest.TestCase):

    def test_groups_per_block_add_up_to_the_case_count(self):
        result = cases(
            datenkranz("t1", birth="1980-01"),                                      # bone disease, adult
            datenkranz("t2", orpha="ORPHA:98006", assessment="noGeneticDiagnosis"),  # neurologic, pediatric
            datenkranz("t3", icd10="G71.0", orpha="ORPHA:98006"),                   # neuromuscular by ICD-10-GM
            datenkranz("t4", orpha="ORPHA:777"),                                    # genetic disease head
            datenkranz("t5", orpha="ORPHA:999"),                                    # unmapped head
            datenkranz("t6", orpha=None),
            datenkranz("t7", orpha="ORPHA:616874", assessment="noGeneticDiagnosis"),
            datenkranz("t8", orpha="ORPHA:4711"),                                   # not in the linearisation
        )
        total = {g: get(result, "Erkrankungsgruppen gesamt", g) for g in rdm.DISEASE_GROUPS}
        adult = {g: get(result, "Erkrankungsgruppen Adult", g) for g in rdm.DISEASE_GROUPS}
        pediatric = {g: get(result, "Erkrankungsgruppen Pädiatrisch", g) for g in rdm.DISEASE_GROUPS}
        self.assertEqual(sum(total.values()), 8)
        self.assertEqual({g: adult[g] + pediatric[g] for g in rdm.DISEASE_GROUPS}, total)
        self.assertEqual(total[rdm.NEUROMUSCULAR], 1)
        self.assertEqual(adult["Knochenkrankheiten, Bindegewebe- und muskuloskelettale Krankheiten"], 1)
        self.assertEqual([total[g] for g in rdm.UNKNOWN_GROUPS], [1, 1, 1, 2])
        neurologic = "Neurologische Erkrankungen, Entwicklungsstörungen: Undiagnostiziert"
        self.assertEqual(get(result, "Erkrankungsgruppen gesamt", neurologic, field="percent"), 100.0)
        self.assertTrue(any("123456" in note for note in result["notes"]))
        # diagnosed cases without group are clarified with the LE
        self.assertEqual(sorted(p["tan"] for p in problems(result, rdm.P_DIAGNOSIS_WITHOUT_GROUP)), ["t4", "t5", "t6", "t8"])

    def test_no_groups_without_linearisation(self):
        result = run([row("a", datenkranz("t1"))], heads=None)
        self.assertFalse(any(r["section"].startswith("Erkrankungsgruppen") for r in result["rows"]))
        self.assertEqual(problems(result, rdm.P_DIAGNOSIS_WITHOUT_GROUP), [])

    def test_linearisation(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?><JDBOR><DisorderList>
          <Disorder id="1"><OrphaCode>58</OrphaCode><Name lang="en">Alexander disease</Name>
            <DisorderDisorderAssociationList count="1"><DisorderDisorderAssociation>
              <TargetDisorder id="2"><OrphaCode>98006</OrphaCode><Name lang="en">Rare neurologic disease</Name></TargetDisorder>
              <RootDisorder id="1" cycle="true"/>
            </DisorderDisorderAssociation></DisorderDisorderAssociationList></Disorder>
          <Disorder id="2"><OrphaCode>98006</OrphaCode><Name lang="en">Rare neurologic disease</Name>
            <DisorderDisorderAssociationList count="1"><DisorderDisorderAssociation>
              <TargetDisorder id="2" cycle="true"/>
              <RootDisorder id="1"><OrphaCode>58</OrphaCode><Name lang="en">Alexander disease</Name></RootDisorder>
            </DisorderDisorderAssociation></DisorderDisorderAssociationList></Disorder>
        </DisorderList></JDBOR>"""
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(xml)
        try:
            self.assertEqual(rdm.load_linearisation(f.name), {"58": "98006", "98006": "98006"})
        finally:
            os.unlink(f.name)


class InputOutputTests(unittest.TestCase):

    def test_reads_the_export_and_plain_lists(self):
        export = {"tables": [
            {"name": "datenkranz", "rows": [
                {"site": "UKT", "useCase": "RD", "patient": "p1", "episodeOfCare": "e1", "tan": "t1",
                 "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1")},
                {"site": "UKT", "useCase": "MTB", "patient": "p2", "episodeOfCare": "e1", "tan": "t2",
                 "submittedAt": "2025-03-01T00:00:00", "content": {}},
            ]},
            {"name": "failures", "rows": [{"site": "UKT", "useCase": "RD", "tan": "t3", "error": "e"},
                                          {"site": "UKT", "useCase": "MTB", "tan": "t4", "error": "e"}]},
        ]}
        plain = [{"case": {"id": 1}, "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1")}]
        with tempfile.TemporaryDirectory() as tmp:
            for name, data, expected in (("export.json", export, (1, 1, "UKT")), ("plain.json", plain, (1, 0, None))):
                path = os.path.join(tmp, name)
                with open(path, "w") as f:
                    json.dump(data, f)
                rows, failures = rdm.load_submissions(path)
                self.assertEqual((len(rows), len(failures), rows[0].site), expected)

    def test_plain_list_keys_include_the_le_and_ignore_field_order(self):
        plain = [
            {"case": {"site": "A", "id": 1}, "submittedAt": "2025-03-01T10:00:00", "content": datenkranz("t1", assessment="noGeneticDiagnosis")},
            {"case": {"id": 1, "site": "A"}, "submittedAt": "2025-04-01T10:00:00",
             "content": datenkranz("t2", type_="followup", submitted="2025-04-01")},
            # the same local case id at another LE is another case
            {"case": {"site": "A", "id": 1}, "submittedAt": "2025-03-02T10:00:00",
             "content": datenkranz("t3", submitter="260000002", assessment="noGeneticDiagnosis")},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plain.json")
            with open(path, "w") as f:
                json.dump(plain, f)
            rows, _ = rdm.load_submissions(path)
        result = run(rows)
        self.assertEqual(get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)"), 2)
        self.assertEqual(get(result, "Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)"), 0)
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", "Diagnose", "260000001"), 1)
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", "Diagnose", "260000002"), 0)

    def test_writes_json_and_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump([{"case": "a", "submittedAt": "2025-03-01T00:00:00",
                            "content": datenkranz("t1", plan_date="2028-01-01")}], f)
            out = os.path.join(tmp, "matrix")
            rdm.main([path, "--output", out, "--min-cell", "1"])
            with open(out + ".csv", encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
            with open(out + "_rueckfragen.csv", encoding="utf-8-sig") as f:
                problem_lines = f.read().splitlines()
        self.assertEqual(lines[0], "LE;Abschnitt;Kategorie;Einheit;Wert;Bezug;Basis;Anteil (%)")
        self.assertIn("Gesamt;Geschlecht;w;Anzahl;1;Fälle;1;100,0", lines)
        self.assertIn("260000001;Geschlecht;w;Anzahl;1;Fälle;1;100,0", lines)
        self.assertEqual(len(problem_lines), 2)
        self.assertTrue(problem_lines[1].startswith(f"260000001;{rdm.P_GFK2_AFTER_SUBMISSION};"))


def synthetic(seed=7, sizes=(("LE-A", 41), ("LE-B", 13), ("LE-C", 6))):
    """A random synthetic case set over three LEs, with small cells in most sections"""
    rnd = random.Random(seed)
    rows = []
    for le, size in sizes:
        for i in range(size):
            transmitted = rnd.random() < 0.6
            content = datenkranz(
                f"{le}-{i}", submitted=f"2025-0{rnd.randint(3, 5)}-0{rnd.randint(1, 9)}",
                gender=rnd.choice(["female"] * 5 + ["male"] * 4 + ["other", None]),
                birth=rnd.choice(["1950-01", "1985-06", "2000-03", "2010-09", "2020-02", "2024-12"]),
                coverage=rnd.choice(["GKV"] * 8 + ["PKV", "BG"]),
                assessment=rnd.choice(list(rdm.ASSESSMENT_STATUS)),
                library=rnd.choice(["wgs"] * 8 + ["wgs_lr", "panel", "none"]),
                extent=rnd.choice(["singleGenome", "duoGenome", "trioGenome", "trioGenome"]),
                include=rnd.random() < 0.95, plan_date=rnd.choice(["2025-02-10", "2025-02-10", None]),
                orpha=rnd.choice(["ORPHA:558", "ORPHA:98006", "ORPHA:777", "ORPHA:616874", None]),
                consents=[broad_consent()] if transmitted else [],
            )
            reason = None if transmitted else rnd.choice(list(rdm.DIP_REASONS) + [None])
            rows.append(rdm.SubmissionRow(json.dumps([le, i]), "2025-06-01T10:00:00", content, le, reason))
    return rows


def partitions(result):
    """Independently of the implementation: the partitions of a column as (total, parts), cells as (section, item, field)"""
    def rows_of(section, items):
        return [(section, item, "value") for item in items]

    cases = ("Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)", "value")
    status = "Diagnosestatus (alle Fälle)"
    status_items = [r["item"] for r in result["rows"] if r["section"] == status]
    sequenced = (status, status_items[0], "base")
    rate = "Diagnoserate (abgeschlossene Fälle)"
    by_age = "Diagnosestatus nach Altersgruppe"
    reasons = "Broad Consent nicht übermittelt: Grund laut DIP"
    missing = (reasons, rdm.REASON_LABELS[0], "base")
    months = sorted({r["item"].split(":")[0] for r in result["rows"] if r["section"] == reasons + ", nach Monat der Erstmeldung"})
    out = [
        (cases, rows_of("Geschlecht", ["m", "w", "d", "unbekannt"])),
        (cases, rows_of("Alter bei Einschluss (GFK1)", rdm.AGE_GROUPS)),
        (cases, rows_of("Versicherung", ["GKV", "PKV", "andere"])),
        (cases, rows_of("Sequenzierung (libraryType)", ["WGS", "lrWGS", "Panel", "Exom (WES)", "keine (noch nicht sequenziert)"])),
        (cases, rows_of("Erkrankungsgruppen gesamt", rdm.DISEASE_GROUPS)),
        (cases, [("Fälle", "davon nicht sequenziert (keine Indikation oder libraryType none, nicht im Diagnosestatus)", "value"),
                 sequenced]),
        (sequenced, rows_of(status, status_items)),
        ((rate, rdm.DIAGNOSED, "base"), rows_of(rate, rdm.STATUSES)),
        (missing, [(reasons, "Fälle ohne übermittelten Broad Consent", "value")]),
        (missing, rows_of(reasons, rdm.REASON_LABELS)),
        (missing, [(reasons + ", nach Monat der Erstmeldung", f"{m}: {rdm.REASON_LABELS[0]}", "base") for m in months]),
    ]
    for s in ("Bearbeitungsdauer medizinisch (GFK1 bis GFK2)", "Bearbeitungsdauer dokumentarisch (GFK2 bis Einreichung beim KDK)"):
        out.append((cases, rows_of(s, ("Dauer berechenbar", "nicht berechenbar: noch keine GFK2",
                                       "nicht berechenbar: unmögliches Datum (siehe Rückfragen)"))))
    for group in rdm.AGE_GROUPS:
        closed = (by_age, f"{group}: {rdm.DIAGNOSED}", "base")
        out.append((closed, rows_of(by_age, [f"{group}: {st}" for st in rdm.STATUSES])))
        out.append(((by_age, f"{group}: {rdm.OPEN}", "base"), [closed, (by_age, f"{group}: {rdm.OPEN}", "value")]))
    return out



def varied(seed):
    """A random synthetic case set: 2 to 6 LEs of 1 to 60 cases, with neuromuscular ICD-10 codes, prior findings
    (one per case, as the DIP sends them) and LEs without unsequenced cases"""
    rnd = random.Random(seed)
    rows = []
    for k in range(rnd.randint(2, 6)):
        le = f"LE-{k}"
        all_sequenced = rnd.random() < 0.4
        for i in range(rnd.choice([1, 1, 2, 3, 4, 5, 7, 12, 25, 60])):
            prior = rnd.choice([None, {"genomicTestType": "none"}] + [
                {"genomicTestType": t, "diagnosticDate": d} for t in ("exome", "panel", "genomeShortRead", "array")
                for d in ("2019-03-01", "2024-05-01")])
            content = datenkranz(
                f"{le}-{i}", submitted=f"2025-0{rnd.randint(3, 5)}-0{rnd.randint(1, 9)}",
                gender=rnd.choice(["female", "male"]),
                birth=rnd.choice(["1950-01", "1985-06", "2000-03", "2010-09", "2020-02", "2024-12"]),
                assessment=rnd.choice(list(rdm.ASSESSMENT_STATUS)),
                library="wgs" if all_sequenced else rnd.choice(["wgs"] * 6 + ["wgs_lr", "panel", "none"]),
                extent=rnd.choice(["singleGenome", "duoGenome", "trioGenome", "trioGenome"]),
                include=all_sequenced or rnd.random() < 0.9, plan_date=rnd.choice(["2025-02-10", "2025-02-10", None]),
                orpha=rnd.choice(["ORPHA:558", "ORPHA:98006", "ORPHA:777", "ORPHA:616874", None]),
                icd10=rnd.choice(["Q87.4", "Q87.4", "G71.0", "G12.1"]), prior=prior,
                consents=[broad_consent()] if rnd.random() < 0.6 else [],
            )
            rows.append(rdm.SubmissionRow(json.dumps([le, i]), "2025-06-01T10:00:00", content, le, None))
    return rows


def sum_relations(result):
    """
    Independently of the implementation: the sum relations of a column that a reader of the matrix knows, as
    (always, lhs, rhs) with cells (section, item, field), sum(lhs) = sum(rhs). always False: known only from the
    data (no unsequenced case, one prior finding per case, ...), checked where it holds. Besides these, Gesamt is
    the sum of the LEs in every row.
    """
    out = []
    status, rate, age = "Diagnosestatus (alle Fälle)", "Diagnoserate (abgeschlossene Fälle)", "Diagnosestatus nach Altersgruppe"
    items = [r["item"] for r in result["rows"] if r["section"] == status]
    label = dict(zip(rdm.CASE_STATUSES, items))
    v = lambda s, i: (s, i, "value")
    sequenced, closed = (status, items[0], "base"), (rate, rdm.DIAGNOSED, "base")
    cases = v("Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)")
    out.append((True, [cases], [v("Fälle", r["item"]) for r in result["rows"]
                                if r["section"] == "Fälle" and r["item"].startswith("davon nicht sequenziert")] + [sequenced]))
    out.append((True, [sequenced], [v(status, i) for i in items]))
    out.append((True, [closed], [v(rate, s) for s in rdm.STATUSES]))
    out.append((True, [sequenced], [closed, v(status, label[rdm.OPEN])]))
    out += [(True, [v(rate, s)], [v(status, s)]) for s in rdm.STATUSES]
    # Diagnosestatus nach Altersgruppe: per age group over the statuses, per status over the age groups
    for g in rdm.AGE_GROUPS:
        closed_g, of_g = (age, f"{g}: {rdm.DIAGNOSED}", "base"), (age, f"{g}: {rdm.OPEN}", "base")
        out.append((True, [closed_g], [v(age, f"{g}: {s}") for s in rdm.STATUSES]))
        out.append((True, [of_g], [closed_g, v(age, f"{g}: {rdm.OPEN}")]))
        out += [(True, [closed_g], [(age, f"{g}: {s}", "base")]) for s in rdm.STATUSES[1:]]
        out.append((False, [of_g], [v("Alter bei Einschluss (GFK1)", g)]))
    out += [(True, [v(rate, s)], [v(age, f"{g}: {s}") for g in rdm.AGE_GROUPS]) for s in rdm.STATUSES]
    out.append((True, [v(status, label[rdm.OPEN])], [v(age, f"{g}: {rdm.OPEN}") for g in rdm.AGE_GROUPS]))
    out.append((True, [closed], [(age, f"{g}: {rdm.DIAGNOSED}", "base") for g in rdm.AGE_GROUPS]))
    out.append((True, [sequenced], [(age, f"{g}: {rdm.OPEN}", "base") for g in rdm.AGE_GROUPS]))
    # by WGS extent: where every closed case is a genome of known extent
    wgs = "Diagnoserate nach WGS-Seq. Art (abgeschlossene Fälle mit WGS/lrWGS)"
    out.append((False, [closed], [(wgs, f"{x}: Diagnose", "base") for x in ("Single", "Duo", "Trio")]))
    out.append((False, [v(rate, rdm.DIAGNOSED)], [v(wgs, f"{x}: Diagnose") for x in ("Single", "Duo", "Trio")]))
    # prior findings: the base is the status count; with one prior per case, partitions
    for s in rdm.CASE_STATUSES:
        prior, years = f"Vorbefunde, Status {s} (Mehrfachnennung möglich)", f"Vorbefunde nach Jahr, Status {s}"
        prior_items = [r["item"] for r in result["rows"] if r["section"] == prior]
        out.append((True, [(prior, prior_items[0], "base")], [v(status, label[s])]))
        out.append((True, [(years, f"{rdm.PRIOR_TYPES[0]}: {rdm.YEAR_BUCKETS[0]}", "base")], [v(status, label[s])]))
        out.append((False, [(prior, prior_items[0], "base")], [v(prior, i) for i in prior_items]))
        for t in rdm.PRIOR_TYPES:
            out.append((False, [v(prior, t)], [v(years, f"{t}: {b}") for b in rdm.YEAR_BUCKETS]))
    # disease groups: block = sum of the groups, group = sum of its statuses = base of their rows, Adult +
    # Pädiatrisch = gesamt; without unsequenced cases the statuses over the groups are those of Diagnosestatus
    whole, adult, paediatric = rdm.DISEASE_GROUP_BLOCKS
    for block in rdm.DISEASE_GROUP_BLOCKS:
        out.append((True, [(block, rdm.DISEASE_GROUPS[0], "base")], [v(block, g) for g in rdm.DISEASE_GROUPS]))
        for g in rdm.DISEASE_GROUPS:
            out.append((True, [v(block, g)], [v(block, f"{g}: {s}") for s in rdm.CASE_STATUSES]))
            out += [(True, [v(block, g)], [(block, f"{g}: {s}", "base")]) for s in rdm.CASE_STATUSES]
    for g in rdm.DISEASE_GROUPS:
        for item in [g] + [f"{g}: {s}" for s in rdm.CASE_STATUSES]:
            out.append((True, [v(whole, item)], [v(adult, item), v(paediatric, item)]))
    for s in rdm.CASE_STATUSES:
        out.append((False, [v(status, label[s])], [v(whole, f"{g}: {s}") for g in rdm.DISEASE_GROUPS]))
        out.append((False, [v(age, f"{a}: {s}") for a in (rdm.ADULTS, rdm.SENIORS)],
                    [v(adult, f"{g}: {s}") for g in rdm.DISEASE_GROUPS]))
        out.append((False, [v(age, f"{a}: {s}") for a in (rdm.NEONATES, rdm.CHILDREN, rdm.ADOLESCENTS)],
                    [v(paediatric, f"{g}: {s}") for g in rdm.DISEASE_GROUPS]))
    out.append((True, [v(rdm.PROBLEMS_SECTION, rdm.P_DIAGNOSIS_WITHOUT_GROUP)],
                [v(whole, f"{g}: {rdm.DIAGNOSED}") for g in rdm.UNKNOWN_GROUPS]))
    out.append((False, [v("Orphacode", "Erkr. ohne Orphacode (keine Orphanet-Kodierung)")], [v(whole, rdm.UNKNOWN_NO_ORPHA)]))
    return out


def recoverable(full, result):
    """
    The suppressed cells that follow from shown ones by a single sum relation: a row (Gesamt = sum of the LEs) or a
    relation of a column (sum_relations) with exactly one suppressed cell, or a suppressed cell on one side of a
    relation whose other side is shown and 0 (all counts are >= 0). Each finding as text.
    """
    hidden = f"<{result['min_cell']}"
    rows = {(r["section"], r["item"]): r for r in result["rows"]}
    true = {(r["section"], r["item"]): r for r in full["rows"]}
    found = []
    relations = []      # (entity or None for a row, cells as (entity, section, item, field))
    for r in result["rows"]:
        if r["unit"] == rdm.COUNT:
            for f in ("value", "base"):
                if r["values"][rdm.TOTAL][f] is None:
                    continue
                relations.append(("row", [[(rdm.TOTAL, r["section"], r["item"], f)],
                                          [(e, r["section"], r["item"], f) for e in result["entities"] if e != rdm.TOTAL]]))
    for always, lhs, rhs in sum_relations(full):
        for e in result["entities"]:
            sides = [[(e,) + c for c in side] for side in (lhs, rhs)]
            values = [[true[s, i]["values"][en][f] for en, s, i, f in side] for side in sides]
            if None in values[0] + values[1]:
                continue
            holds = sum(values[0]) == sum(values[1])
            if always and not holds:
                found.append(f"relation does not hold: {lhs} = {rhs} in {e}")
            elif holds:
                relations.append((e, sides))

    def shown(c):
        return rows[c[1], c[2]]["values"][c[0]][c[3]]

    for where, sides in relations:
        cells = sides[0] + sides[1]
        n = sum(shown(c) == hidden for c in cells)
        if n == 1:
            found.append(f"one suppressed cell in {where}: {cells}")
        for side, other in ((sides[0], sides[1]), (sides[1], sides[0])):
            if all(shown(c) != hidden for c in side) and sum(shown(c) for c in side) == 0 and any(shown(c) == hidden for c in other):
                found.append(f"suppressed beside a shown 0 in {where}: {cells}")
    return found


def derivable_cells(files, additive_across=True):
    """
    Independently of the implementation, with exact fractions: the suppressed counts of 1 to min_cell - 1 that
    follow from what a reader of all files sees, solving the whole linear system at once (not one relation at a
    time): per file the rows (Gesamt = sum of the LEs), the relations of sum_relations() and partitions(), a base
    shown in several rows of a section as one number; across files, with additive_across and the first file the
    total, each cell of the total = the sum of the same cell of the other files (a missing LE or row counts 0; the
    losses that every period repeats, see rdm.NOT_ADDITIVE, equal in all files). Shown zeros, the cells of a side of a
    relation whose other side is zeros, and the open cases where a row "noch keine GFK2" shows 0 are known zeros.
    files: [(full, result)], full the same matrix unsuppressed. Findings as text.
    """
    from fractions import Fraction
    hidden_mark = f"<{files[0][1]['min_cell']}"
    min_cell = files[0][1]["min_cell"]
    value = {}      # (file, entity, section, item, field) -> shown value or None (suppressed)
    true = {}
    for n, (full, result) in enumerate(files):
        for fr, r in zip(full["rows"], result["rows"]):
            if r["unit"] != rdm.COUNT:
                continue
            for e in result["entities"]:
                for f in ("value", "base"):
                    t = fr["values"][e][f]
                    if t is None:
                        continue
                    shown = r["values"][e][f]
                    value[n, e, r["section"], r["item"], f] = None if shown == hidden_mark else shown
                    true[n, e, r["section"], r["item"], f] = t
    relations = []      # (lhs, rhs) of keys of value
    for n, (full, result) in enumerate(files):
        ents = result["entities"]
        les = [e for e in ents if e != rdm.TOTAL]
        rows = [r for r in full["rows"] if r["unit"] == rdm.COUNT]
        for r in rows:
            for f in ("value", "base"):
                if (n, rdm.TOTAL, r["section"], r["item"], f) in value:
                    relations.append(([(n, rdm.TOTAL, r["section"], r["item"], f)],
                                      [(n, e, r["section"], r["item"], f) for e in les if (n, e, r["section"], r["item"], f) in value]))
        bases = {}
        for r in rows:
            if r["base_label"] is not None:
                bases.setdefault((r["section"], r["base_label"]), []).append(r["item"])
        listed = [(a, rest) for a, rest in [([(s_, i, f)], [c]) for (s_, _), items in bases.items()
                                             for i, c in [(items[0], (s_, it, "base")) for it in items[1:]] for f in ("base",)]]
        listed += [(lhs, rhs) for always, lhs, rhs in sum_relations(full)]
        listed += [([t], parts) for t, parts in partitions(full)]
        for e in ents:
            for lhs, rhs in listed:
                keys = [[(n, e) + c for c in side] for side in (lhs, rhs)]
                if any(k not in value for k in keys[0] + keys[1]):
                    continue
                if sum(true[k] for k in keys[0]) == sum(true[k] for k in keys[1]):     # holds in this column
                    relations.append(tuple(keys))
    if additive_across and len(files) > 1:
        for k in [k for k in value if k[0] == 0]:
            parts = [(m,) + k[1:] for m in range(1, len(files)) if (m,) + k[1:] in value]
            if (k[2], k[3]) in rdm.NOT_ADDITIVE:
                relations += [([k], [p]) for p in parts]
            else:
                relations.append(([k], parts))
    # known zeros
    zero = {k for k, v in value.items() if v == 0}
    subsets = []
    for n, (full, result) in enumerate(files):
        for e in result["entities"]:
            wholes = [(n, e, s_, "nicht berechenbar: noch keine GFK2", "value") for s_ in
                      ("Bearbeitungsdauer medizinisch (GFK1 bis GFK2)", "Bearbeitungsdauer dokumentarisch (GFK2 bis Einreichung beim KDK)")]
            parts = [k for k in value if k[:2] == (n, e) and k[4] == "value" and (
                (k[2] == "Diagnosestatus (alle Fälle)" and k[3].startswith(f"{rdm.OPEN} "))
                or ((k[2] == "Diagnosestatus nach Altersgruppe" or k[2] in rdm.DISEASE_GROUP_BLOCKS) and k[3].endswith(f": {rdm.OPEN}")))]
            subsets += [(pt, w) for pt in parts for w in wholes if w in value]
    changed = True
    while changed:
        changed = False
        for pt, w in subsets:
            if w in zero and pt not in zero:
                zero.add(pt)
                changed = True
        for lhs, rhs in relations:
            for side, other in ((lhs, rhs), (rhs, lhs)):
                if all(o in zero for o in other) and not all(k in zero for k in side):
                    zero.update(side)
                    changed = True
        for k in list(value):
            if k[4] == "value" and k[:4] + ("base",) in zero and k not in zero:
                zero.add(k)
                changed = True
    unknown = sorted(k for k, v in value.items() if v is None and k not in zero)
    index = {k: i for i, k in enumerate(unknown)}
    pivots = {}
    for lhs, rhs in relations:
        row = {}
        for side, sign in ((lhs, 1), (rhs, -1)):
            for k in side:
                if k in index:
                    row[index[k]] = row.get(index[k], 0) + sign
        row = {v: Fraction(c) for v, c in row.items() if c}
        while row:
            v = min(row)
            if v not in pivots:
                c = row[v]
                pivots[v] = {u: x / c for u, x in row.items()}
                break
            f = row[v]
            for u, x in pivots[v].items():
                y = row.get(u, 0) - f * x
                if y:
                    row[u] = y
                else:
                    row.pop(u, None)

    def follows(v):
        row = {v: Fraction(1)}
        while row:
            u = min(row)
            if u not in pivots:
                return False
            f = row[u]
            for w, x in pivots[u].items():
                y = row.get(w, 0) - f * x
                if y:
                    row[w] = y
                else:
                    row.pop(w, None)
        return True

    found = [f"follows from the shown numbers: {k}" for k in unknown if 0 < true[k] < min_cell and follows(index[k])]
    found += [f"known zero although suppressed and small: {k}" for k in sorted(zero) if value[k] is None and 0 < true[k] < min_cell]
    return found


class ComplementarySuppressionTests(unittest.TestCase):
    """No suppressed count can be recovered as a difference of shown counts"""

    def setUp(self):
        self.rows = synthetic()
        self.full = run(self.rows, min_cell=1)
        self.result = run(self.rows, min_cell=5)

    def cell(self, result, entity, section, item, field):
        matches = [r for r in result["rows"] if r["section"] == section and r["item"] == item]
        self.assertEqual(len(matches), 1, (section, item))
        return matches[0]["values"][entity][field]

    def test_no_suppressed_count_is_alone_in_its_row(self):
        # (a) Gesamt is the sum of the LEs: exactly one suppressed cell of a row would be the difference
        for r in self.result["rows"]:
            if r["unit"] != rdm.COUNT:
                continue
            for field in ("value", "base"):
                cells = [v[field] for v in r["values"].values()]
                self.assertNotEqual(cells.count("<5"), 1, (r["key"], field, cells))

    def test_no_suppressed_count_is_alone_in_a_partition(self):
        # (b) within a column, the parts of a partition sum to its total
        for entity in self.result["entities"]:
            for total, parts in partitions(self.result):
                if not parts:
                    continue
                full = [self.cell(self.full, entity, *c) for c in [total] + parts]
                self.assertEqual(full[0], sum(full[1:]), (entity, total))
                shown = [self.cell(self.result, entity, *c) for c in [total] + parts]
                self.assertNotEqual(shown.count("<5"), 1, (entity, total, parts, shown))

    def test_shown_values_are_true_and_suppressed_ones_were_small_or_complementary(self):
        primary = complementary = 0
        for r, f in zip(self.result["rows"], self.full["rows"]):
            for entity in self.result["entities"]:
                for field in ("value", "base"):
                    shown, true = r["values"][entity][field], f["values"][entity][field]
                    if shown == "<5":
                        if r["unit"] == rdm.COUNT:
                            self.assertIsNone(r["values"][entity]["percent"])
                            if true < 5:
                                primary += 1
                            else:
                                complementary += 1
                    else:
                        self.assertEqual(shown, true, (r["key"], entity, field))
        self.assertGreater(complementary, 0)
        self.assertEqual(self.result["suppressed"], {"primary": primary, "complementary": complementary})
        # the case count of all LEs stays
        self.assertEqual(self.cell(self.result, rdm.TOTAL, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)", "value"), 60)
        self.assertTrue(any(f"{complementary} Zellen zusätzlich zu {primary}" in n for n in self.result["notes"]))

    def test_without_suppression_nothing_is_added(self):
        self.assertEqual(self.full["suppressed"], {"primary": 0, "complementary": 0})


class DiagnosisScopeTests(unittest.TestCase):
    """--suppress-scope diagnoses: only the diagnosis rows are suppressed, all other numbers are exact"""

    def diag(self, result, r):
        return r["section"] in rdm.DIAGNOSIS_SECTIONS or (r["section"], r["item"]) in rdm.DIAGNOSIS_ITEMS

    def test_the_diagnosis_sections_exist(self):
        result = run(synthetic(), min_cell=1)
        sections = {r["section"] for r in result["rows"]}
        self.assertEqual([s for s in rdm.DIAGNOSIS_SECTIONS if s not in sections], [])
        for section, item in rdm.DIAGNOSIS_ITEMS:
            get(result, section, item)

    def test_only_diagnosis_rows_are_suppressed(self):
        result = run([row(f"a{i}", datenkranz(f"a{i}", gender="male" if i < 2 else "female",
                                              assessment="noGeneticDiagnosis" if i < 2 else "confirmedGeneticDiagnosis"),
                          site="LE-A") for i in range(8)], min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        self.assertEqual(result["suppress_scope"], rdm.SUPPRESS_DIAGNOSES)
        # not diagnosis: exact, also below 5, with its percentage
        self.assertEqual((get(result, "Geschlecht", "m"), get(result, "Geschlecht", "m", field="percent")), (2, 25.0))
        # diagnosis: suppressed, and complementarily the other closed status against the exact sequenced cases
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", rdm.UNDIAGNOSED, "LE-A"), "<5")
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", rdm.DIAGNOSED, "LE-A"), "<5")
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", rdm.DIAGNOSED, "LE-A", "base"), 8)
        # a duration statistic from one value is shown exactly
        few = cases(datenkranz("t1"), min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        self.assertEqual(get(few, "Bearbeitungsdauer medizinisch", "Median (Tage)"), 31.0)
        self.assertTrue(any(n.startswith("Unterdrückt werden nur die Diagnosezeilen") for n in result["notes"]))

    def test_a_diagnosis_cell_is_not_the_exact_total_minus_shown_zeros(self):
        # LE B: 3 sequenced cases, all with a diagnosis, 1 not sequenced. "Fälle" and "davon nicht sequenziert" are
        # exact, so the sequenced cases (3) are, and with the other statuses shown as 0 the diagnoses would be 3:
        # a zero of the partition is suppressed as well
        rows = ([row(f"a{i}", datenkranz(f"a{i}", assessment=list(rdm.ASSESSMENT_STATUS)[i % 5]), site="A")
                 for i in range(20)]
                + [row(f"b{i}", datenkranz(f"b{i}"), site="B") for i in range(3)]
                + [row("b9", datenkranz("b9", library="none"), site="B")])
        result = run(rows, min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        status = "Diagnosestatus (alle Fälle)"
        self.assertEqual(get(result, "Fälle", "Fallzahl gesamt (Episoden mit Erstmeldung)", "B"), 4)
        self.assertEqual(get(result, status, rdm.DIAGNOSED, "B", "base"), 3)
        items = [r["item"] for r in result["rows"] if r["section"] == status]
        shown = [get(result, status, item, "B") for item in items]
        self.assertEqual(shown[0], "<5")
        self.assertGreaterEqual(shown.count("<5"), 2, shown)
        self.assertTrue(all(v in ("<5", 0) for v in shown), shown)
        # the closed cases (base of the rate) are a diagnosis quantity: suppressed with it
        self.assertEqual(get(result, "Diagnoserate", rdm.DIAGNOSED, "B", "base"), "<5")

    def test_the_same_holds_for_gesamt(self):
        # one sequenced case of a single LE, still open: Gesamt is that LE's count, and the sequenced cases of
        # Gesamt (1) are exact, so a zero of Gesamt's status partition is suppressed too
        rows = ([row("a0", datenkranz("a0", assessment="noGeneticDiagnosis", plan_date=None), site="A")]
                + [row(f"b{i}", datenkranz(f"b{i}", library="none"), site="B") for i in range(6)])
        result = run(rows, min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        status = "Diagnosestatus (alle Fälle)"
        items = [r["item"] for r in result["rows"] if r["section"] == status]
        self.assertEqual(get(result, status, items[0], field="base"), 1)
        shown = [get(result, status, item) for item in items]
        self.assertEqual(shown[-1], "<5")      # offen
        self.assertEqual(shown.count("<5"), 2, shown)

    def test_non_diagnosis_cells_exact_and_no_suppressed_cell_follows_from_shown_ones(self):
        # a set with larger LEs, and one with tiny LEs, where partitions of zeros next to an exact total occur
        for rows in (synthetic(), synthetic(seed=14, sizes=(("LE-A", 2), ("LE-B", 1)))):
            with self.subTest(cases=len(rows)):
                self.check_scope(rows)

    def check_scope(self, rows):
        full = run(rows, min_cell=1)
        result = run(rows, min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        hidden = 0
        for r, f in zip(result["rows"], full["rows"]):
            for entity in result["entities"]:
                for field in ("value", "base"):
                    shown, true = r["values"][entity][field], f["values"][entity][field]
                    if shown == "<5":
                        hidden += 1
                        self.assertTrue(self.diag(result, r), r["key"])
                        self.assertTrue(field == "value" or r["base_label"] not in rdm.EXACT_DIAGNOSIS_BASES, r["key"])
                    else:
                        self.assertEqual(shown, true, (r["key"], entity, field))
                if not self.diag(result, r):
                    self.assertEqual(r["values"][entity]["percent"], f["values"][entity]["percent"], r["key"])
            for field in ("value", "base"):
                cells = [v[field] for v in r["values"].values()]
                self.assertNotEqual(cells.count("<5"), 1, (r["key"], field, cells))
        self.assertGreater(hidden, 0)
        for entity in result["entities"]:
            for total, parts in partitions(result):
                shown = [ComplementarySuppressionTests.cell(self, result, entity, *c) for c in [total] + parts]
                self.assertNotEqual(shown.count("<5"), 1, (entity, total, parts, shown))
        self.assertEqual(sum(result["suppressed"].values()), hidden)

    def test_the_scopes_differ_and_an_unknown_one_is_refused(self):
        rows = synthetic()
        self.assertNotEqual(run(rows, min_cell=5)["rows"], run(rows, min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)["rows"])
        with self.assertRaises(ValueError):
            run(rows, scope="none")


class SumRelationTests(unittest.TestCase):
    """
    Every sum relation the matrix exposes (sum_relations of the test, written independently of the implementation:
    rows over the LEs, partitions of a column, Diagnosestatus nach Altersgruppe in both directions, disease group =
    its statuses, Adult + Pädiatrisch = gesamt, ...): none has exactly one suppressed cell, on many random sets
    """

    def check(self, rows, scope, min_cell=5):
        full = run(rows, min_cell=1)
        found = recoverable(full, run(rows, min_cell=min_cell, scope=scope))
        self.assertEqual(found, [], found[:3])

    def test_random_sets_in_both_scopes(self):
        for seed in range(40):
            for scope in rdm.SUPPRESS_SCOPES:
                with self.subTest(seed=seed, scope=scope):
                    self.check(varied(seed), scope)

    def test_tiny_les_and_another_threshold(self):
        for seed in range(40, 60):
            with self.subTest(seed=seed):
                self.check(synthetic(seed=seed, sizes=(("LE-A", 3), ("LE-B", 1), ("LE-C", 2))), rdm.SUPPRESS_DIAGNOSES)
                self.check(varied(seed), rdm.SUPPRESS_DIAGNOSES, min_cell=3)

    def test_the_relations_hold(self):
        # the relations marked always hold in every column (recoverable reports one that doesn't)
        rows = varied(3)
        full = run(rows, min_cell=1)
        self.assertGreater(len(sum_relations(full)), 100)
        self.assertEqual([f for f in recoverable(full, full) if f.startswith("relation does not hold")], [])

    def test_a_disease_group_with_shown_zero_statuses_is_not_suppressed_alone(self):
        # LE B: 2 adult cases, both neuromuscular. Its other groups are zeros next to the exact cases of the block,
        # so a zero group is suppressed as well; its four status rows (and their base, the group count) then must
        # not all show 0
        rows = ([row(f"a{i}", datenkranz(f"a{i}", icd10=["Q87.4", "G71.0"][i % 2], birth=["1985-06", "2015-06"][i % 3 == 0]),
                     site="A") for i in range(30)]
                + [row(f"b{i}", datenkranz(f"b{i}", icd10="G71.0", birth="1985-06"), site="B") for i in range(2)])
        result = run(rows, min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        block = "Erkrankungsgruppen Adult (ab 18)"
        heads = [g for g in rdm.DISEASE_GROUPS if get(result, block, g, "B") == "<5"]
        self.assertGreaterEqual(len(heads), 2)
        for g in heads:
            statuses = [get(result, block, f"{g}: {s}", "B") for s in rdm.CASE_STATUSES]
            self.assertNotEqual(statuses.count("<5"), 0, (g, statuses))
            self.assertEqual(get(result, block, f"{g}: {rdm.DIAGNOSED}", "B", "base"), "<5")
        self.assertEqual(recoverable(run(rows, min_cell=1), result), [])

    def test_gesamt_is_suppressed_where_a_single_le_needs_it(self):
        # Vorbefunde, Status Unklar: Genom (LR) only at LE A, so its Gesamt is A's count and suppressed. The prior
        # findings add up to the cases with status Unklar (one per case), exact in Gesamt: one more cell of the
        # Gesamt column is suppressed, here Exom, although two LEs count there; shown, it would give Genom (LR)
        def r(le, i, prior, assessment="suspectedGeneticDiagnosis"):
            return row(f"{le}{i}", datenkranz(f"{le}{i}", assessment=assessment,
                                              prior={"genomicTestType": prior, "diagnosticDate": "2024-05-01"}), site=le)
        rows = ([r("A", 0, "genomeLongRead")] + [r("A", i, "exome") for i in range(1, 7)] + [r("B", i, "exome") for i in range(6)]
                + [r("C", i, "panel") for i in range(8)] + [r("B", 10 + i, "panel") for i in range(7)]
                + [r(le, 20 + i, "panel", ["confirmedGeneticDiagnosis", "noGeneticDiagnosis"][i % 2])
                   for le in "ABC" for i in range(10)])
        result = run(rows, min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        s = "Vorbefunde, Status Unklar"
        items = rdm.PRIOR_TYPES + (rdm.PRIOR_NONE_PERFORMED, rdm.PRIOR_NOT_DOCUMENTED)
        self.assertEqual([get(result, s, "Genom (LR)", le) for le in ("A", "B", "C")], ["<5", 0, 0])
        self.assertEqual((get(result, s, "Genom (LR)"), get(result, s, "Exom"), get(result, s, "Exom", "B")), ("<5", "<5", 6))
        self.assertIsInstance(get(result, s, "Exom", field="base"), int)
        self.assertEqual([get(result, s, t) for t in items].count("<5"), 2)
        self.assertTrue(any("ergänzend in den Summenbeziehungen der Spalte Gesamt" in n for n in result["notes"]))
        self.assertEqual(recoverable(run(rows, min_cell=1), result), [])

    def test_the_note_says_that_a_suppressed_cell_can_be_large(self):
        result = run(synthetic(), min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
        note = next(n for n in result["notes"] if n.startswith("Unterdrückt werden nur"))
        self.assertIn("auch weit mehr", note)
        self.assertIn("Diagnosestatus nach Altersgruppe je Status", note)


class AuditTests(unittest.TestCase):
    """The audit of the whole linear system (rdm.derivable) reports every suppressed count that the independent solver
    derivable_cells() finds; it only reports (result["derivable"], <output>_audit.csv), the JSON and CSV don't have it"""

    def test_the_audit_finds_what_the_independent_solver_finds(self):
        import ast
        found_any = False
        for seed in range(6):
            for scope in rdm.SUPPRESS_SCOPES:
                with self.subTest(seed=seed, scope=scope):
                    rows = varied(seed)
                    result, full = run(rows, min_cell=5, scope=scope), run(rows, min_cell=1, scope=scope)
                    independent = {ast.literal_eval(f.split(": ", 1)[1])[1:] for f in derivable_cells([(full, result)])
                                   if f.startswith("follows")}
                    reported = {tuple(c) for c in result["derivable"]}
                    self.assertLessEqual(independent, reported)
                    found_any = found_any or bool(reported)
        self.assertTrue(found_any)      # the single-relation closure leaves such cells on these sets

    def test_the_command_line_writes_the_audit_apart(self):
        rows = varied(0)
        data = [{"case": r.case, "submittedAt": r.submitted_at, "content": r.content, "site": r.site} for r in rows]
        with tempfile.TemporaryDirectory() as tmp:
            src, out = os.path.join(tmp, "in.json"), os.path.join(tmp, "m")
            with open(src, "w", encoding="utf-8") as f:
                json.dump(data, f)
            from contextlib import redirect_stderr
            from io import StringIO
            err = StringIO()
            with redirect_stderr(err):
                rdm.main([src, "--output", out, "--reference-date", "2026-10-30"])
            result = run(rows, min_cell=5, scope=rdm.SUPPRESS_DIAGNOSES)
            if result["derivable"]:
                self.assertTrue(os.path.exists(f"{out}_audit.csv"))
                self.assertIn("WARNUNG", err.getvalue())
            with open(f"{out}.json", encoding="utf-8") as f:
                self.assertNotIn("derivable", json.load(f))


if __name__ == "__main__":
    unittest.main()
