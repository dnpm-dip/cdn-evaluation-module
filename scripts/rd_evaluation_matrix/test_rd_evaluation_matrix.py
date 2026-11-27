"""Tests of rd_evaluation_matrix.py: python3 -m unittest discover scripts/rd_evaluation_matrix"""

import contextlib
import io
import json
import os
import random
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

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


def run(rows, heads=HEADS, start=None, end=None, failures=(), min_cell=1, scope=rdm.SUPPRESS_CODES):
    """The matrix (min_cell 1: nothing suppressed)"""
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
        # with an interval of 24 months no case is due yet
        longer = rdm.run(rows, [], HEADS, None, None, REFERENCE, min_cell=1, follow_up_months=24)
        self.assertEqual(get(longer, rdm.FU_SECTION, f"Stand am Stichtag (Intervall 24 Monate): {rdm.FU_NOT_DUE}"), 3)
        self.assertTrue(any(n.startswith(rdm.FU_NOTE) for n in longer["notes"]))


class DiseaseGroupTests(unittest.TestCase):

    def test_groups_per_block_add_up_to_the_case_count(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = self.eight_cases()
        self.check_groups(result, err.getvalue())

    def eight_cases(self):
        return cases(
            datenkranz("t1", birth="1980-01"),                                      # bone disease, adult
            datenkranz("t2", orpha="ORPHA:98006", assessment="noGeneticDiagnosis"),  # neurologic, pediatric
            datenkranz("t3", icd10="G71.0", orpha="ORPHA:98006"),                   # neuromuscular by ICD-10-GM
            datenkranz("t4", orpha="ORPHA:777"),                                    # genetic disease head
            datenkranz("t5", orpha="ORPHA:999"),                                    # unmapped head
            datenkranz("t6", orpha=None),
            datenkranz("t7", orpha="ORPHA:616874", assessment="noGeneticDiagnosis"),
            datenkranz("t8", orpha="ORPHA:4711"),                                   # not in the linearisation
        )

    def check_groups(self, result, err):
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
        # the unmapped head only on stderr for the KDK: in the notes it would show that its cases occur
        self.assertIn("123456", err)
        self.assertFalse(any("123456" in note for note in result["notes"]))
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


def code_cell(r, field):
    """Independently of the implementation: a cell of the rows derived from the diagnosis codes, the base of such a
    row unless it counts no code (the cases, the cases of an age block)"""
    code = r["section"] in ("Orphacode", *rdm.DISEASE_GROUP_BLOCKS) or (
        r["section"] == rdm.PROBLEMS_SECTION and r["item"] == rdm.P_DIAGNOSIS_WITHOUT_GROUP)
    return code and (field == "value" or r["base_label"] not in ("Fälle", *(f"Fälle ({b})" for b in rdm.DISEASE_GROUP_BLOCKS)))


class PrimarySuppressionTests(unittest.TestCase):
    """Only counts of 1 to 4 of an LE in the rows of the diagnosis codes are suppressed (decision of 27.11.2026): no
    complementary suppression, Gesamt and every other cell exact"""

    def check(self, full, result, min_cell=5):
        n = 0
        for fr, r in zip(full["rows"], result["rows"]):
            for e in result["entities"]:
                for f in ("value", "base"):
                    shown, true = r["values"][e][f], fr["values"][e][f]
                    if shown == f"<{min_cell}":
                        n += 1
                        self.assertNotEqual(e, rdm.TOTAL, (r["key"], f))
                        self.assertTrue(code_cell(r, f), (r["section"], r["item"], f))
                        self.assertEqual(r["unit"], rdm.COUNT)
                        self.assertTrue(0 < true < min_cell, (r["key"], e, f, true))
                        self.assertIsNone(r["values"][e]["percent"])
                    else:
                        self.assertEqual(shown, true, (r["key"], e, f))
                        # small code counts of an LE are never shown
                        if e != rdm.TOTAL and r["unit"] == rdm.COUNT and code_cell(r, f) and true is not None:
                            self.assertFalse(0 < true < min_cell, (r["key"], e, f, true))
                if r["values"][e]["value"] != f"<{min_cell}" and r["values"][e]["base"] != f"<{min_cell}":
                    self.assertEqual(r["values"][e]["percent"], fr["values"][e]["percent"], (r["key"], e))
        self.assertEqual(result["suppressed"], {"cells": n, "complementary": 0})
        return n

    def test_only_small_code_cells_of_the_les_on_random_sets(self):
        hidden = 0
        for seed in range(6):
            for rows in (varied(seed), synthetic(seed)):
                with self.subTest(seed=seed):
                    hidden += self.check(run(rows, min_cell=1), run(rows, min_cell=5))
        self.assertGreater(hidden, 0)

    def test_gesamt_and_the_status_rows_exact(self):
        rows = ([row(f"a{i}", datenkranz(f"a{i}", orpha="ORPHA:558" if i < 2 else "ORPHA:98006",
                                         assessment="noGeneticDiagnosis" if i < 1 else "confirmedGeneticDiagnosis"),
                     site="A") for i in range(8)]
                + [row(f"b{i}", datenkranz(f"b{i}", orpha="ORPHA:98006"), site="B") for i in range(8)])
        result = run(rows, min_cell=5)
        bone = "Knochenkrankheiten, Bindegewebe- und muskuloskelettale Krankheiten"
        # the group count of 2 at A is suppressed, Gesamt (2) is shown: it gives it away (accepted, no complementary)
        self.assertEqual([get(result, "Erkrankungsgruppen gesamt", bone, e) for e in (rdm.TOTAL, "A", "B")], [2, "<5", 0])
        self.assertEqual(get(result, "Erkrankungsgruppen gesamt", "Neurologische Erkrankungen, Entwicklungsstörungen", "A"), 6)
        # the diagnosis status and every other row exact, also below 5
        self.assertEqual(get(result, "Diagnosestatus (alle Fälle)", rdm.STATUSES[0], "A"), 7)
        self.assertEqual(get(result, "Geschlecht", "w", "A"), 8)
        self.check(run(rows, min_cell=1), result)
        note = next(n for n in result["notes"] if n.startswith("Unterdrückt sind nur Fallzahlen von 1 bis 4"))
        self.assertIn("keine Sekundärunterdrückung, einzelne Werte können sich aus Summen ergeben", note)

    def test_min_cell_1_suppresses_nothing_and_the_former_scopes_are_refused(self):
        result = run(varied(2), min_cell=1)
        self.assertEqual(result["suppressed"], {"cells": 0, "complementary": 0})
        for scope in ("all", "diagnoses"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                rdm.main(["missing.json", "--suppress-scope", scope])


class FillTemplateLineTests(unittest.TestCase):
    """The line on the suppression above the matrix in the xlsx, for both scopes and for results of the former one"""

    def test_lines(self):
        try:
            import fill_template
        except ImportError:
            self.skipTest("fill_template.py needs openpyxl")
        line = lambda scope: fill_template.suppression_line({"min_cell": 5, "suppress_scope": scope})
        self.assertTrue(line("codes").startswith("Nur Zeilen aus den Diagnosecodes"))
        self.assertIn("auch Diagnosestatus und Diagnoserate", line("codes"))
        primary = fill_template.suppression_line({"min_cell": 5, "suppress_scope": "codes",
                                                  "suppressed": {"cells": 3, "complementary": 0}})
        self.assertIn("keine Sekundärunterdrückung", primary)
        self.assertTrue(line("diagnoses").startswith("Nur Diagnosezeilen"))    # written before 26.11.2026
        self.assertTrue(line("all").startswith("Zellen mit weniger als 5"))


if __name__ == "__main__":
    unittest.main()
