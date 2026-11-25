"""Tests of dnpm_evaluation_matrix.py: python3 -m unittest discover -s scripts/dnpm_evaluation_matrix"""

import contextlib
import importlib.util
import io
import json
import os
import random
import re
import subprocess
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

import dnpm_evaluation_matrix as dm

MDAT_RESEARCH_USE = "2.16.840.1.113883.3.1937.777.24.5.3.8"
REFERENCE = date(2026, 11, 1)
PREVIOUS = "647f21b"    # the last commit before --suppress-scope: its output equals that of --suppress-scope all


def broad_consent(start="2025-01-01", kind="permit"):
    """A FHIR Consent as in researchConsents[].scope, with one nested research provision"""
    return {"resourceType": "Consent", "status": "active", "provision": {
        "type": "deny", "period": {"start": start},
        "provision": [{"type": kind, "period": {"start": start, "end": "2055-01-01"},
                       "code": [{"coding": [{"system": "urn:oid:2.16.840.1.113883.3.1937.777.24.5.3",
                                             "code": MDAT_RESEARCH_USE}]}]}]}}


def therapy_rec(identifier, evidence="m2A", strategy="ZS", type_="offLabel", variants=("v1",), details=()):
    return {"identifier": identifier, "type": type_, "substances": [{"code": {"code": "L01EA01", "system": "atc"}}],
            "evidenceLevel": evidence, "evidenceLevelDetails": list(details), "priority": 1,
            "variants": list(variants), "therapeuticStrategy": strategy}


def study_rec(identifier, register="NCT"):
    return {"identifier": identifier, "register": register, "name": "Studie", "id": "NCT00000000",
            "evidenceLevel": "m1B", "evidenceLevelDetails": [], "priority": 2}


def datenkranz(
    tan, type_="initial", submitted="2025-03-01", gender="female", birth="1970-06", coverage="GKV",
    gfk1="2025-01-10", mtb="2025-02-20", main="C34.1", topography="C34.1", histology="8140/3", ecog="1",
    grading="3", tnm=("cT2 (UICC)", "cN1 (UICC)", "cM1a (UICC)"), library="wgs", include=True, consents=(),
    therapies=(), studies=(), care_plan=None, molecular=None, follow_ups=None, procedures=None, priors=None,
    disease="oncological", submitter="LE-A-ID", mv_consent_date="2025-01-05", germline=False, diagnosis_date="2024-10-15",
):
    """An oncology Datenkranz submission with the fields the matrix reads (as the DIP mapping writes them)"""
    content = {
        "metaData": {
            "submission": {"date": submitted, "type": type_, "diseaseType": disease, "submitterId": submitter,
                           "clinicalDataNodeId": "KDK-X", **({"genomicDataCenterId": "GRZ-X"} if library != "none" else {})},
            "coverageType": coverage,
            "mvConsent": {"version": "1", "scope": [{"date": mv_consent_date, "domain": "mvSequencing", "type": "permit"}]},
            "researchConsents": [{"presentationDate": "2025-01-01", "scope": c} for c in consents],
            "tanC": tan, "gender": gender, "birthDate": birth, "addressAGS": "",
            "decisionToInclude": include, "molecularBoardDecisionDate": gfk1,
        },
        "case": {"diagnosisOd": {
            "mainDiagnosis": {"code": main, "system": "http://fhir.de/CodeSystem/bfarm/icd-10-gm", "version": "2025",
                              "date": diagnosis_date},
            "ecogPerformanceStatusScore": ecog, "germlineDiagnosisConfirmed": germline,
            "histology": {"code": histology, "system": "urn:oid:2.16.840.1.113883.6.43.1", "version": "3.2"},
            "topography": {"code": topography, "system": "urn:oid:2.16.840.1.113883.6.43.1", "version": "3.2"},
            "libraryType": library,
        }},
    }
    if grading:
        content["case"]["diagnosisOd"]["grading"] = grading
    if tnm:
        content["case"]["diagnosisOd"]["tnmClassifications"] = [{"code": "1", "display": d, "system": "tnm"} for d in tnm]
    if procedures is not None:
        content["case"]["priorProcedures"] = procedures
    if priors is not None:
        content["case"]["priorDiagnostics"] = priors
    if mtb:
        content["plan"] = {"carePlanOd": {
            "molecularBoardDecisionDate": mtb, "studyRecommended": bool(studies), "counsellingRecommended": False,
            "reEvaluationRecommended": False, "interventionRecommended": False, **(care_plan or {})}}
        if therapies:
            content["plan"]["recommendedSystemicTherapies"] = list(therapies)
        if studies:
            content["plan"]["recommendedStudies"] = list(studies)
    if molecular is not None:
        content["molecular"] = molecular
    if follow_ups:
        content["followUp"] = {"followUpOds": list(follow_ups)}
    return content


def follow_up(fu_date="2025-08-01", vital="living", therapies=(), death=None, last_contact=None):
    f = {"followUpDate": fu_date, "metachroneDiagnoses": False, "ecogPerformanceStatusScore": "1",
         "vitalStatus": vital, "therapies": list(therapies)}
    if death:
        f["deathDate"] = death
    if last_contact:
        f["lastContactDate"] = last_contact
    return f


def row(case, content, submitted_at=None, site="LE-A", reason=dm.NOT_EXPORTED):
    submitted_at = submitted_at or (content["metaData"]["submission"]["date"] + "T10:00:00")
    return dm.SubmissionRow(json.dumps([site, case]), submitted_at, content, site, reason)


def run(rows, start=None, end=None, failures=(), min_cell=1, resolution=dm.MONTH, cutoff=None, scope=dm.SUPPRESS_ALL):
    """The matrix; suppression in all rows unless scope says otherwise (the tests of the rules of the suppression)"""
    return dm.run(rows, list(failures), start, end, cutoff or REFERENCE, min_cell=min_cell, resolution=resolution,
                  cutoff=cutoff, suppress_scope=scope)


def get(result, section, item, entity=dm.TOTAL, field="value"):
    """A value of the result, by section (or its start) and item"""
    matches = [r for r in result["rows"] if r["section"].startswith(section) and r["item"] == item]
    if len(matches) != 1:
        raise KeyError(f"{section} | {item}: {len(matches)} rows")
    return matches[0]["values"][entity][field]


def series(result, section, item, period, entity=dm.TOTAL, field="value"):
    matches = [r for r in result["time_series"]["rows"] if r["section"].startswith(section) and r["item"] == item]
    if len(matches) != 1:
        raise KeyError(f"{section} | {item}: {len(matches)} rows")
    return matches[0]["values"][entity][period][field]


def cases(*contents, **kwargs):
    return run([row(str(i), c) for i, c in enumerate(contents)], **kwargs)


def problems(result, kind):
    return [p for p in result["problems"] if p["problem"] == kind]


CASES = "Fallzahl gesamt (Episoden mit Erstmeldung)"


class CaseTests(unittest.TestCase):

    def test_one_case_per_episode_state_from_latest_non_test(self):
        rows = [
            row("a", datenkranz("t1", ecog="1"), "2025-03-01T10:00:00"),
            row("a", datenkranz("t2", type_="correction", submitted="2025-04-01", ecog="2"), "2025-04-01T10:00:00"),
            row("a", datenkranz("t3", type_="test", submitted="2025-05-01", ecog="3"), "2025-05-01T10:00:00"),
            row("b", datenkranz("t4", type_="followup")),                 # no initial
            row("c", datenkranz("t5", type_="test")),
            row("d", datenkranz("t6", disease="rare")),                    # not oncology
        ]
        result = run(rows)
        self.assertEqual(get(result, "Fälle", CASES), 1)
        self.assertEqual(get(result, "ECOG bei Einschluss", "2"), 1)
        self.assertEqual(get(result, "Fälle", "Fälle mit mindestens einer Korrektur (correction)"), 1)
        self.assertEqual(get(result, "Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)"), 1)
        self.assertEqual(get(result, "Einreichungen", "Einreichungen test"), 2)
        self.assertEqual(get(result, "Einreichungen", "Einreichungen gesamt (ohne Test)"), 3)
        self.assertIn("2 Testeinreichungen nicht in den Fällen (nur im Abschnitt Einreichungen gezählt).", result["notes"])

    def test_hereditary_counts_and_population(self):
        result = cases(datenkranz("t1", disease="hereditary"), datenkranz("t2", include=False, library="none", mtb=None),
                       datenkranz("t3", library="none", mtb=None), datenkranz("t4", library="panel"))
        self.assertEqual(get(result, "Fälle", CASES), 4)
        self.assertEqual(get(result, "Fälle", "davon hereditär (diseaseType hereditary)"), 1)
        self.assertEqual(get(result, "Fälle", "davon nicht eingeschlossen (decisionToInclude = false)"), 1)
        self.assertEqual(get(result, "Fälle", "davon eingeschlossen, nicht sequenziert (libraryType none oder fehlt)"), 1)
        self.assertEqual(get(result, "Fälle", "davon sequenziert (eingeschlossen, libraryType wgs/wgs_lr/wes/panel)"), 2)
        # the MTB rate refers to the sequenced cases only
        self.assertEqual(get(result, "GFK2 (MTB): Ergebnis", "GFK2 dokumentiert (plan.carePlanOd)", field="base"), 2)
        self.assertEqual(get(result, "Sequenzierung", "Panel", field="base"), 3)

    def test_list_key_includes_the_le_and_ignores_field_order(self):
        data = [{"case": {"id": 1, "x": 2}, "site": "LE-A", "submittedAt": "2025-03-01", "content": datenkranz("t1")},
                {"case": {"x": 2, "id": 1}, "site": "LE-A", "submittedAt": "2025-04-01",
                 "content": datenkranz("t2", type_="correction", submitted="2025-04-01")},
                {"case": {"id": 1, "x": 2}, "site": "LE-B", "submittedAt": "2025-03-01", "content": datenkranz("t3")}]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump(data, f)
            rows, _ = dm.load_submissions(path)
        result = run(rows)
        self.assertEqual(get(result, "Fälle", CASES), 2)
        self.assertEqual(result["entities"], [dm.TOTAL, "LE-A", "LE-B"])


class DemographicsConsentTests(unittest.TestCase):

    def test_sex_age_insurance(self):
        result = cases(datenkranz("t1", gender="male", birth="2015-06", coverage="PKV"),
                       datenkranz("t2", birth="1940-01", coverage="BG"), datenkranz("t3", birth="1980-02"))
        self.assertEqual(get(result, "Geschlecht", "m"), 1)
        self.assertEqual(get(result, "Geschlecht", "w"), 2)
        self.assertEqual(get(result, "Alter bei Einschluss", dm.PAEDIATRIC), 1)
        self.assertEqual(get(result, "Alter bei Einschluss", "80+ Jahre"), 1)
        self.assertEqual(get(result, "Alter bei Einschluss", "40-59 Jahre"), 1)
        self.assertEqual(get(result, "Versicherung", "andere"), 1)

    def test_broad_consent_and_reasons(self):
        rows = [
            row("a", datenkranz("t1", consents=[broad_consent()])),
            row("b", datenkranz("t2", consents=[broad_consent(kind="deny")])),
            row("c", datenkranz("t3"), reason="patient-refusal"),
            row("d", datenkranz("t4"), reason=None),
        ]
        result = run(rows)
        s = "Broad Consent (Stand"
        self.assertEqual(get(result, s, dm.CONSENT_VALID), 1)
        self.assertEqual(get(result, s, dm.CONSENT_INVALID), 1)
        self.assertEqual(get(result, s, "nicht übermittelt: Patient lehnt ab"), 1)
        self.assertEqual(get(result, s, dm.REASON_NONE), 1)
        self.assertEqual(get(result, s, "Grund aus der DIP-Spalte reasonResearchConsentMissing (statt noScopeJustification)"), 1)
        self.assertEqual(get(result, "Einwilligung", "MV-Einwilligung Sequenzierung (mvSequencing: permit)"), 4)
        self.assertEqual(get(result, "Einreichungen", f"Erstmeldungen Forschungseinwilligung: {dm.CONSENT_VALID}"), 1)

    def test_no_scope_justification_wins_and_qs12(self):
        content = datenkranz("t1", submitted="2026-07-01", gfk1="2026-05-01", mtb="2026-06-20")
        content["metaData"]["researchConsents"] = [
            {"presentationDate": "2026-05-01", "noScopeJustification": "consent is not implemented at LE due to organizational issues"}]
        result = run([row("a", content, reason="patient-refusal")])
        self.assertEqual(get(result, "Broad Consent (Stand", "nicht übermittelt: organisatorische Gründe (ab 01.06.2026 unzulässig)"), 1)
        self.assertEqual(len(problems(result, dm.P_JUSTIFICATION)), 1)


class TumourTests(unittest.TestCase):

    def test_entity_groups(self):
        for code, group in (("C34.1", "Lunge (C33-C34)"), ("C18.7", "Kolorektal, Anus (C18-C21)"),
                            ("C92.0", "Hämatologisch (C81-C96, D45-D47)"), ("D46.9", "Hämatologisch (C81-C96, D45-D47)"),
                            ("C17.0", "Sonstige bösartige (übrige C-Codes)"), ("D05.1", "In situ (D00-D09)"),
                            ("D32.0", "Gutartig, unsicheres Verhalten (D10-D44, D48)"), ("Z80.0", dm.NO_CD_CODE),
                            (None, dm.MISSING), ("C50.9", "Mamma (C50)"), ("C61", "Prostata (C61)")):
            self.assertEqual(dm.entity_group(code), group, code)

    def test_stage_ecog_grading_histology_topography(self):
        result = cases(datenkranz("t1"), datenkranz("t2", tnm=("cT1", "cN0", "cM0"), ecog="unknown", grading=None,
                                                    topography="C50.9", histology="8500/2"),
                       datenkranz("t3", tnm=(), ecog=None, grading="Q"))
        self.assertEqual(get(result, "Tumorstadium", "M1 (Fernmetastasen)"), 1)
        self.assertEqual(get(result, "Tumorstadium", "M0"), 1)
        self.assertEqual(get(result, "Tumorstadium", "kein TNM"), 1)
        self.assertEqual(get(result, "ECOG bei Einschluss (zusammengefasst)", "konkreter Wert 0-5"), 1)
        self.assertEqual(get(result, "ECOG bei Einschluss", dm.MISSING), 1)
        self.assertEqual(get(result, "Grading", dm.OTHER), 1)
        self.assertEqual(get(result, "Grading", dm.MISSING), 1)
        self.assertEqual(get(result, "Diagnose: Kodierung", "Histologie /2 in situ"), 1)
        self.assertEqual(get(result, "Diagnose: Kodierung", "Topographie ICD-O-3-T dreistellig gleich der Hauptdiagnose"), 2)

    def test_the_behaviour_codes_add_up_to_the_cases_with_one(self):
        # a character after "/" that is no behaviour code (or nothing) is no behaviour code: before, such a case
        # counted under "mit Verhaltenscode" but under no code
        result = cases(*(datenkranz(f"t{i}", histology=h)
                         for i, h in enumerate(("8140/3", "8140/32", "8500/2", "8140/?", "8140/", "8140", "8140/ 6"))))
        s = "Diagnose: Kodierung"
        self.assertEqual(get(result, s, "Histologie ICD-O-3-M mit Verhaltenscode"), 4)
        self.assertEqual(sum(get(result, s, f"Histologie {label}") for _, label in dm.BEHAVIOUR), 4)
        self.assertEqual(get(result, s, "Histologie /3 bösartig (Primärtumor)"), 2)
        self.assertEqual(get(result, s, "Histologie /6 bösartig (Metastase)"), 1)

    def test_prior_therapies(self):
        procedure = {"treatmentType": "O", "intention": "P", "therapyStartDate": "2024-11-01"}
        result = cases(datenkranz("t1", procedures=[procedure, {**procedure, "intention": "K"}]), datenkranz("t2"),
                       datenkranz("t3", priors=[{"type": "panel", "date": "2024-12-01"}]))
        self.assertEqual(get(result, "Vortherapien", "Anzahl systemischer Vortherapien: 2"), 1)
        self.assertEqual(get(result, "Vortherapien", "Anzahl systemischer Vortherapien: keine"), 2)
        self.assertEqual(get(result, "Vortherapien", "mit Intention P (palliativ) (Mehrfachnennung)"), 1)
        self.assertEqual(get(result, "Molekulare Vordiagnostik", "panel"), 1)
        self.assertEqual(get(result, "Füllgrad optionaler Felder (Vortherapien)", "intention"), 2)


class MtbTests(unittest.TestCase):

    def test_mtb_outcome_and_recommendations(self):
        result = cases(
            datenkranz("t1", therapies=[therapy_rec("r1", details=("Z",)), therapy_rec("r2", evidence="m3", variants=())]),
            datenkranz("t2", studies=[study_rec("s1")], care_plan={"counsellingRecommended": True}),
            datenkranz("t3", care_plan={"otherRecommendations": ["KW"]}),     # no target
            datenkranz("t4", mtb=None),                                        # no MTB yet
        )
        s = "GFK2 (MTB): Ergebnis (sequenzierte Fälle)"
        self.assertEqual(get(result, s, "GFK2 dokumentiert (plan.carePlanOd)"), 3)
        self.assertEqual(get(result, s, "mindestens eine systemische Therapieempfehlung"), 1)
        self.assertEqual(get(result, s, "mindestens eine Studienempfehlung (recommendedStudies)"), 1)
        self.assertEqual(get(result, s, "mindestens eine Therapie- oder Studienempfehlung", field="percent"), 66.7)
        self.assertEqual(get(result, s, "ohne Therapie- und Studienempfehlung (kein Target)"), 1)
        self.assertEqual(get(result, s, "Humangenetische Beratung empfohlen (counsellingRecommended)"), 1)
        self.assertEqual(get(result, s, "andere Empfehlung KW keine weitere tumorspezifische Therapie (otherRecommendations)"), 1)
        self.assertEqual(get(result, s, "Empfehlung molekular begründet (variants[] einer Empfehlung)"), 1)
        s = "Systemische Therapieempfehlungen (Empfehlungen)"
        self.assertEqual(get(result, s, "Empfehlungen gesamt"), 2)
        self.assertEqual(get(result, s, "Evidenzgrad m2A"), 1)
        self.assertEqual(get(result, s, "Evidenzgrad m3"), 1)
        self.assertEqual(get(result, s, "Zusatzverweis Z (Mehrfachnennung)"), 1)
        self.assertEqual(get(result, s, "Strategie ZS zielgerichtete Substanzen"), 2)
        self.assertEqual(get(result, "Studienempfehlungen", "Register NCT"), 1)
        # per case: 2, 0, 0 therapy recommendations among the 3 cases with MTB
        self.assertEqual(get(result, "Empfehlungen je Fall", "Therapieempfehlungen: Median (Anzahl je Fall)"), 0.0)
        self.assertEqual(get(result, "Empfehlungen je Fall", "Therapieempfehlungen: Maximum (Anzahl je Fall)"), 2.0)
        self.assertEqual(get(result, "GFK2 (MTB): Ergebnis nach Tumorentität", "Lunge (C33-C34): Therapie- oder Studienempfehlung"), 2)

    def test_study_flag_contradiction(self):
        content = datenkranz("t1", care_plan={"studyRecommended": True})
        self.assertEqual(len(problems(cases(content), dm.P_STUDY_FLAG)), 1)

    def test_turnaround_times(self):
        result = cases(datenkranz("t1", gfk1="2025-01-10", mtb="2025-02-09", submitted="2025-03-01"),
                       datenkranz("t2", gfk1="2025-01-10", mtb="2025-03-11", submitted="2025-03-21"),
                       datenkranz("t3", mtb=None))
        s = "Bearbeitungsdauer medizinisch"
        self.assertEqual(get(result, s, "Median (Tage)"), 45.0)
        self.assertEqual(get(result, s, "nicht berechenbar: noch keine GFK2"), 1)
        self.assertEqual(get(result, "Bearbeitungsdauer dokumentarisch", "Minimum (Tage)"), 10.0)
        self.assertEqual(get(result, "Dauer Erstdiagnose bis GFK1", "Median (Tage)"), 87.0)

    def test_first_mtb_over_all_submissions(self):
        rows = [row("a", datenkranz("t1", mtb="2025-02-20", submitted="2025-03-01")),
                row("a", datenkranz("t2", type_="followup", mtb="2025-06-01", submitted="2025-07-01"))]
        self.assertEqual(get(run(rows), "Bearbeitungsdauer medizinisch", "Median (Tage)"), 41.0)

    def test_impossible_dates_are_checked_per_submission(self):
        # the initial submission reports an inclusion board after its own date; the later one doesn't
        rows = [row("a", datenkranz("t1", gfk1="2025-03-05", mtb=None, submitted="2025-03-01")),
                row("a", datenkranz("t2", type_="correction", gfk1="2025-03-05", mtb="2025-04-01", submitted="2025-05-01"))]
        result = run(rows)
        self.assertEqual([p["tan"] for p in problems(result, dm.P_GFK1_AFTER_SUBMISSION)], ["t2"])
        self.assertIn("Einreichung t1", problems(result, dm.P_GFK1_AFTER_SUBMISSION)[0]["detail"])
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch", "Dauer berechenbar"), 0)
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch",
                             "nicht berechenbar: unmögliches Datum (siehe Rückfragen)"), 1)

    def test_plausibility_problems(self):
        result = cases(datenkranz("t1", mtb="2025-01-05"),                                # MTB before inclusion
                       datenkranz("t2", mtb="2025-01-15"),                                # 5 days
                       datenkranz("t3", diagnosis_date="2025-02-01"),                     # diagnosis after GFK1
                       datenkranz("t4", birth="2025-02"),                                 # dates before birth
                       datenkranz("t5", mv_consent_date="2025-02-01"),                    # board before consent
                       datenkranz("t6", tnm=("cT1", "cN0")),
                       datenkranz("t7", include=False))
        for kind, tan in ((dm.P_GFK2_BEFORE_GFK1, "t1"), (dm.P_SHORT_MEDICAL, "t2"), (dm.P_DIAGNOSIS_AFTER_GFK1, "t3"),
                          (dm.P_BEFORE_BIRTH, "t4"), (dm.P_BEFORE_CONSENT, "t5"), (dm.P_TNM, "t6"), (dm.P_INCLUSION, "t7")):
            self.assertEqual([p["tan"] for p in problems(result, kind)], [tan], kind)
        self.assertEqual(get(result, "Rückfragen", dm.P_TNM), 1)


class ImplementationFollowUpTests(unittest.TestCase):

    def test_implementation_of_recommendations(self):
        started = {"reference": "r1", "therapyStartDate": "2025-03-12", "therapyResponse": "PR",
                   "terminationReasonOBDS": "P"}
        other = {"reference": "own-1", "therapyStartDate": "2025-04-01"}
        result = cases(
            datenkranz("t1", therapies=[therapy_rec("r1"), therapy_rec("r2")],
                       follow_ups=[follow_up(therapies=[started, other])]),
            datenkranz("t2", therapies=[therapy_rec("r3")], follow_ups=[follow_up()]),
            datenkranz("t3", studies=[study_rec("s1")]),
        )
        s = "Umsetzung"
        self.assertEqual(get(result, s, "mindestens eine Empfehlung begonnen (followUpOds[].therapies[].reference)"), 1)
        self.assertEqual(get(result, s, "mindestens eine Empfehlung begonnen (followUpOds[].therapies[].reference)",
                             field="base"), 3)
        self.assertEqual(get(result, s, "mindestens eine Empfehlung begonnen, von Fällen mit Follow-up", field="percent"), 50.0)
        self.assertEqual(get(result, s, "Therapie ohne Bezug zu einer Empfehlung"), 1)
        self.assertEqual(get(result, s, "Empfehlungen begonnen (Empfehlungen)"), 1)
        self.assertEqual(get(result, s, "Empfehlungen begonnen (Empfehlungen)", field="base"), 4)
        self.assertEqual(get(result, s, "Ende: P Abbruch wegen Progress"), 1)
        self.assertEqual(get(result, s, "Ansprechen: PR partielle Remission"), 1)
        self.assertEqual(get(result, "Dauer GFK2 bis Beginn", "Median (Tage)"), 20.0)

    def test_unlinked_therapy_with_new_reference_per_submission_counts_once(self):
        def submission(tan, type_, submitted, reference):
            return datenkranz(tan, type_=type_, submitted=submitted, therapies=[therapy_rec("r1")],
                              follow_ups=[follow_up(therapies=[{"reference": reference, "therapyStartDate": "2025-05-01"}])])
        rows = [row("a", submission("t1", "initial", "2025-03-01", "uuid-1")),
                row("a", submission("t2", "followup", "2025-09-01", "uuid-2"), "2025-09-01T00:00:00")]
        self.assertEqual(get(run(rows), "Füllgrad optionaler Felder (Follow-up-Therapien)", "Therapien gesamt"), 1)

    def test_therapy_before_mtb(self):
        early = {"reference": "r1", "therapyStartDate": "2025-02-01"}
        result = cases(datenkranz("t1", therapies=[therapy_rec("r1")], follow_ups=[follow_up(therapies=[early])]))
        self.assertEqual(len(problems(result, dm.P_THERAPY_BEFORE_GFK2)), 1)
        self.assertEqual(get(result, "Dauer GFK2 bis Beginn", "Dauer berechenbar"), 0)

    def test_follow_up_intervals_vital_status(self):
        rows = [row("a", datenkranz("t1", submitted="2025-03-01")),                       # 20 months before reference
                row("a", datenkranz("t2", type_="followup", submitted="2025-09-01",
                                    follow_ups=[follow_up(vital="deceased", death="2025-08-20")]), "2025-09-01T00:00:00"),
                row("b", datenkranz("t3", submitted="2026-08-01", gfk1="2026-06-01", mtb="2026-07-01")),   # 3 months
                row("c", datenkranz("t4", follow_ups=[follow_up(vital="deceased")]))]
        result = run(rows)
        s = "Follow-up: Erstmeldung mindestens 4 Monate"
        self.assertEqual(get(result, s, "Fälle"), 2)
        self.assertEqual(get(result, s, "mit 1 Folgemeldung"), 1)
        self.assertEqual(get(result, s, "ohne Folgemeldung"), 1)
        self.assertEqual(get(result, s, "verstorben"), 2)
        self.assertEqual(get(result, "Follow-up: Erstmeldung mindestens 24 Monate", "Fälle"), 0)
        self.assertEqual(get(result, "Einreichungen", "Folgemeldungen mit Sterbedatum (followUpOds[].deathDate)"), 1)
        self.assertEqual([p["tan"] for p in problems(result, dm.P_DEATH)], ["t4"])


class FollowUpSopTests(unittest.TestCase):
    """Follow-up schedule and mandatory fields after the network's MTB follow-up SOP, at the reference date"""

    def setUp(self):
        started = {"reference": "r1", "therapyStartDate": "2026-02-01", "therapyResponse": "SD",
                   "therapyResponseDate": "2026-04-01", "substances": [{"code": "L01EA01"}]}
        recent = {"reference": "r9", "therapyStartDate": "2026-10-01"}       # less than 8 weeks before the reference
        no_substance = {"reference": "r8", "therapyStartDate": "2026-03-01", "therapyResponse": "PD"}
        late = dict(gfk1="2025-12-01", mtb="2026-01-10", submitted="2026-01-20")
        self.result = cases(
            # category 1, follow-up 2 months before the reference: current
            datenkranz("c1", therapies=[therapy_rec("r1")], **late, follow_ups=[follow_up("2026-09-01", therapies=[started])]),
            # category 2, last follow-up 8 months before: overdue
            datenkranz("c2", therapies=[therapy_rec("r2")], **late, follow_ups=[follow_up("2026-03-01")]),
            # category 3, GFK2 2 months before: not due
            datenkranz("c3", gfk1="2026-08-01", mtb="2026-09-01", submitted="2026-09-10"),
            # category 3, GFK2 20 months before, no follow-up: overdue and lost to follow-up
            datenkranz("c4"),
            # deceased
            datenkranz("c5", follow_ups=[follow_up("2025-06-01", vital="deceased", death="2025-05-20")]),
            # category 2 for more than 24 months: interval 12 months, follow-up 10 months before: current
            datenkranz("c6", therapies=[therapy_rec("r6")], gfk1="2024-05-01", mtb="2024-06-01", submitted="2024-06-10",
                       diagnosis_date="2024-01-01", follow_ups=[dict(follow_up("2026-01-15"), ecogPerformanceStatusScore="unknown")]),
            # category 1, two therapies of which one started recently
            datenkranz("c7", therapies=[therapy_rec("r9"), therapy_rec("r8")], **late,
                       follow_ups=[follow_up("2026-10-15", therapies=[recent, no_substance])]),
            # not sequenced: not in the follow-up schedule
            datenkranz("c8", library="none", mtb=None),
        )

    def test_categories_and_state_at_the_reference_date(self):
        r, s = self.result, dm.FU_SECTION
        label = {k: f"{lab} (Intervall {m} Monate)" for k, lab, m in dm.FU_CATEGORIES}
        self.assertEqual([get(r, s, label[k]) for k in "123"], [2, 2, 3])     # c1, c7; c2, c6; c3, c4, c5
        self.assertEqual(get(r, s, label["1"], field="base"), 7)
        states = [get(r, s, f"Stand am Stichtag: {st}") for st in dm.FU_STATES]
        self.assertEqual(states, [1, 1, 3, 2])          # c5; c3; c1, c6, c7; c2, c4
        self.assertEqual((get(r, s, "Follow-up aktuell, von fälligen"), get(r, s, "Follow-up aktuell, von fälligen", field="base")),
                         (3, 5))
        self.assertEqual(get(r, s, "Kategorie 2 ab 24 Monate nach GFK2 (danach Intervall wie Kategorie 3)"), 1)
        lost = "kein dokumentierter Kontakt in den 12 Monaten vor dem Stichtag (lost to follow-up nach SOP)"
        self.assertEqual((get(r, s, lost), get(r, s, lost, field="base")), (1, 2))   # c4 of c4, c6
        c = dm.FU_CATEGORY_SECTION
        self.assertEqual(get(r, c, f"Kategorie 2: {dm.FU_OVERDUE}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 2: {dm.FU_CURRENT}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 3: {dm.FU_NOT_DUE}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 3: {dm.FU_DECEASED}"), 1)
        self.assertEqual(get(r, c, "Kategorie 1: Follow-up aktuell, von fälligen", field="percent"), 100.0)

    def test_days_fields_response_and_vital_status(self):
        r = self.result
        s = dm.FU_DAYS_SECTION
        self.assertEqual(get(r, s, "Dauer berechenbar (Follow-up ab GFK2)"), 5)   # all but c3, c4
        self.assertEqual(get(r, s, "Minimum (Tage)"), 50.0)                      # c2: 2026-01-10 to 2026-03-01
        s = dm.FU_FIELDS_SECTION
        self.assertEqual(get(r, s, "Follow-up-Einträge gesamt"), 5)
        self.assertEqual(get(r, s, "ecogPerformanceStatusScore konkret (0-5)"), 4)
        self.assertEqual(get(r, s, "Follow-up-Therapien gesamt"), 3)
        self.assertEqual(get(r, s, "substances angegeben"), 1)
        self.assertEqual((get(r, s, "therapyResponseDate, von Therapien mit Ansprechen"),
                          get(r, s, "therapyResponseDate, von Therapien mit Ansprechen", field="base")), (1, 2))
        s = dm.FU_RESPONSE_SECTION
        item = "Ansprechen dokumentiert, von begonnenen Empfehlungen mit Beginn mindestens 8 Wochen vor dem Stichtag"
        self.assertEqual((get(r, s, item), get(r, s, item, field="base")), (2, 2))    # r1, r8; r9 too recent
        self.assertEqual(get(r, s, "ECOG konkret (0-5) im letzten Follow-up", field="base"), 5)
        self.assertEqual([get(r, s, f"Vitalstatus (letztes Follow-up): {v}") for v in ("verstorben", "lebend", dm.MISSING,
                          "ohne Follow-up")], [1, 4, 0, 2])

    def test_follow_up_rows_are_exact_in_the_diagnosis_scope(self):
        result = cases(datenkranz("c1"), datenkranz("c2", follow_ups=[follow_up("2026-10-01")]), min_cell=5,
                       scope=dm.SUPPRESS_DIAGNOSES)
        self.assertEqual(get(result, dm.FU_SECTION, f"Stand am Stichtag: {dm.FU_CURRENT}"), 1)
        self.assertEqual(get(result, dm.FU_DAYS_SECTION, "Median (Tage)"), 588.0)
        self.assertTrue(any(n.startswith(dm.FU_NOTE) for n in result["notes"]))


class TimelineTests(unittest.TestCase):
    """Zeitstrahl: milestones in days relative to GFK1"""

    T = dm.TIMELINE

    def test_offsets_of_every_milestone(self):
        started = {"reference": "r1", "therapyStartDate": "2025-03-12"}
        result = cases(datenkranz("t1", therapies=[therapy_rec("r1")],
                                  follow_ups=[follow_up("2025-08-01", therapies=[started]), follow_up("2025-06-01")]))
        # GFK1 2025-01-10: consent 2025-01-05, diagnosis 2024-10-15, MTB 2025-02-20, initial 2025-03-01
        for name, days in (("MV-Einwilligung", -5), ("Erstdiagnose", -87), ("GFK2 (MTB)", 41),
                           ("Erstmeldung beim KDK", 50), ("Beginn empfohlene Therapie", 61), ("Erstes Follow-up", 142)):
            self.assertEqual(get(result, self.T, f"{name}: Fälle"), 1, name)
            self.assertEqual(get(result, self.T, f"{name}: Fälle", field="base"), 1, name)
            for q in ("1. Quartil", "Median", "3. Quartil"):
                self.assertEqual(get(result, self.T, f"{name}: {q} (Tage)"), float(days), name)
        self.assertEqual(get(result, self.T, dm.NO_GFK1), 0)

    def test_row_labels(self):
        result = cases(datenkranz("t1"))
        items = [r["item"] for r in result["rows"] if r["section"] == self.T]
        expected = [dm.NO_GFK1]
        for name, after in dm.MILESTONES:
            expected += [f"{name}: Fälle", f"{name}: 1. Quartil (Tage)", f"{name}: Median (Tage)", f"{name}: 3. Quartil (Tage)"]
            expected += [f"{name}: unplausibel (vor GFK1)"] if after else []
        self.assertEqual(items, expected)
        self.assertFalse([i for i in items if "Minimum" in i or "Maximum" in i])
        units = {r["item"]: r["unit"] for r in result["rows"] if r["section"] == self.T}
        self.assertEqual(units["Erstes Follow-up: Fälle"], dm.COUNT)
        self.assertEqual(units["Erstes Follow-up: Median (Tage)"], dm.DAYS)
        self.assertEqual(units["Erstes Follow-up: unplausibel (vor GFK1)"], dm.COUNT)

    def test_quartiles_and_cases_without_a_date(self):
        result = cases(*[datenkranz(f"t{i}", follow_ups=[follow_up(f"2025-0{i + 2}-10")]) for i in range(5)],
                       datenkranz("t5"))
        # follow-ups 2025-02-10 .. 2025-06-10: 31, 59, 90, 120, 151 days after GFK1 2025-01-10
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Fälle"), 5)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Fälle", field="base"), 6)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Median (Tage)"), 90.0)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: 1. Quartil (Tage)"), 59.0)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: 3. Quartil (Tage)"), 120.0)

    def test_milestone_before_gfk1_is_implausible_and_left_out(self):
        result = cases(datenkranz("t1", follow_ups=[follow_up("2024-12-01")]),
                       datenkranz("t2", follow_ups=[follow_up("2025-02-09")]),
                       datenkranz("t3", mv_consent_date="2024-12-01"))
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Fälle"), 1)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: unplausibel (vor GFK1)"), 1)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Median (Tage)"), 30.0)
        # a consent before GFK1 is expected
        self.assertEqual(get(result, self.T, "MV-Einwilligung: Fälle"), 3)
        (p,) = problems(result, dm.P_MILESTONE_BEFORE_GFK1)
        self.assertIn("Erstes Follow-up 2024-12-01", p["detail"])
        self.assertEqual(get(result, "Rückfragen", dm.P_MILESTONE_BEFORE_GFK1), 1)

    def test_month_dates_count_as_the_15th(self):
        result = cases(datenkranz("t1", diagnosis_date="2024-10", mv_consent_date="2025-01"))
        self.assertEqual(get(result, self.T, "Erstdiagnose: Median (Tage)"), -87.0)      # 2024-10-15
        self.assertEqual(get(result, self.T, "MV-Einwilligung: Median (Tage)"), 5.0)     # 2025-01-15

    def test_cases_without_gfk1(self):
        broken = datenkranz("t1")
        del broken["metaData"]["molecularBoardDecisionDate"]
        result = run([row("a", broken), row("b", datenkranz("t2"))])
        self.assertEqual(get(result, self.T, dm.NO_GFK1), 1)
        self.assertEqual(get(result, self.T, "GFK2 (MTB): Fälle"), 1)

    def test_small_cells_hide_count_and_quartiles(self):
        rows = [row(f"a{i}", datenkranz(f"a{i}", follow_ups=[follow_up()] if i < 3 else None)) for i in range(8)]
        result = run(rows, min_cell=5)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Fälle"), "<5")
        for q in ("1. Quartil", "Median", "3. Quartil"):
            self.assertEqual(get(result, self.T, f"Erstes Follow-up: {q} (Tage)"), "<5")
        self.assertEqual(get(result, self.T, "GFK2 (MTB): Fälle"), 8)
        self.assertEqual(get(result, self.T, "GFK2 (MTB): Median (Tage)"), 41.0)

    def test_per_le_and_month(self):
        rows = [row("a", datenkranz("t1", submitted="2025-03-01")),
                row("b", datenkranz("t2", submitted="2025-04-11", gfk1="2025-04-01", mtb="2025-04-05"), site="LE-B")]
        result = run(rows)
        self.assertEqual(series(result, self.T, "Erstmeldung beim KDK: Median (Tage)", "2025-03"), 50.0)
        self.assertEqual(series(result, self.T, "Erstmeldung beim KDK: Median (Tage)", "2025-04", entity="LE-B"), 10.0)
        self.assertEqual(series(result, self.T, "Erstmeldung beim KDK: Fälle", "2025-04", entity="LE-A"), 0)
        self.assertIsNone(series(result, self.T, "Erstmeldung beim KDK: Median (Tage)", "2025-04", entity="LE-A"))
        self.assertEqual(get(result, self.T, "GFK2 (MTB): Median (Tage)", entity="LE-B"), 4.0)


class MolecularTests(unittest.TestCase):

    def test_variants_and_biomarkers(self):
        snv = {"identifier": "v1", "genomicSource": "somatic", "gene": {"code": "HGNC:1097", "display": "BRAF"},
               "dnaChange": "c.1799T>A", "proteinChange": "p.V600E", "localization": "coding"}
        cnv = {"identifier": "c1", "genomicSource": "somatic", "cnvType": "highLevelGain", "localization": "coding"}
        result = cases(
            datenkranz("t1", molecular={"smallVariants": [snv, {**snv, "proteinChange": None}], "copyNumberVariants": [cnv],
                                        "complexBiomarkers": [{"identifier": "b", "tmb": 12.5, "hrdHigh": True}]}),
            datenkranz("t2", molecular={"complexBiomarkers": [{"identifier": "b", "tmb": 3.0, "hrdHigh": False}]}),
            datenkranz("t3"),
        )
        s = "Molekulare Befunde (sequenzierte Fälle)"
        self.assertEqual(get(result, s, "molecular übermittelt"), 2)
        self.assertEqual(get(result, s, "mindestens eine SNV/Indel (smallVariants)"), 1)
        self.assertEqual(get(result, s, "TMB-high (>= 10 mut/Mb), von Fällen mit TMB"), 1)
        self.assertEqual(get(result, s, "HRD high, von Fällen mit HRD-Status", field="percent"), 50.0)
        self.assertEqual(get(result, "Molekulare Befunde: TMB", "Median (mut/Mb)"), 7.8)
        self.assertEqual(get(result, "Molekulare Befunde: Anzahl je Fall", "SNV/Indel: Maximum (Anzahl je Fall)"), 2.0)
        self.assertEqual(get(result, "Füllgrad optionaler Felder (SNV/Indel)", "proteinChange"), 1)
        self.assertEqual(get(result, "Füllgrad optionaler Felder (Fälle)", "molecular.complexBiomarkers[].ploidy"), 0)


class PeriodSuppressionTests(unittest.TestCase):

    def test_period_selects_initial_and_submissions(self):
        rows = [row("a", datenkranz("t1", submitted="2025-06-30")),
                row("b", datenkranz("t2", submitted="2025-07-01")),
                row("b", datenkranz("t3", type_="followup", submitted="2026-02-01"), "2026-02-01T00:00:00"),
                row("c", datenkranz("t4", submitted="2026-01-01"))]
        result = run(rows, start=date(2025, 7, 1), end=date(2026, 1, 1))
        self.assertEqual(get(result, "Fälle", CASES), 1)
        self.assertEqual(get(result, "Einreichungen", "Einreichungen followup"), 0)
        self.assertEqual(result["period"], "01.07.2025 - 31.12.2025")
        self.assertEqual(result["time_series"]["periods"][0], "2025-07")
        self.assertEqual(len(result["time_series"]["periods"]), 6)

    def test_cutoff_ignores_later_submissions(self):
        rows = [row("a", datenkranz("t1", ecog="1", submitted="2025-03-01")),
                row("a", datenkranz("t2", type_="correction", ecog="3", submitted="2025-09-01"), "2025-09-01T00:00:00")]
        self.assertEqual(get(run(rows, cutoff=date(2025, 6, 30)), "ECOG bei Einschluss", "1"), 1)
        self.assertEqual(get(run(rows), "ECOG bei Einschluss", "3"), 1)

    def test_monthly_and_quarterly_series(self):
        rows = [row("a", datenkranz("t1", submitted="2025-03-01")), row("b", datenkranz("t2", submitted="2025-03-20")),
                row("c", datenkranz("t3", submitted="2025-05-02"), site="LE-B")]
        result = run(rows)
        self.assertEqual(result["time_series"]["periods"], ["2025-03", "2025-04", "2025-05"])
        self.assertEqual(series(result, "Fälle", CASES, "2025-03"), 2)
        self.assertEqual(series(result, "Fälle", CASES, "2025-04"), 0)
        self.assertEqual(series(result, "Fälle", CASES, "2025-05", entity="LE-B"), 1)
        self.assertEqual(series(result, "Einreichungen", "Einreichungen initial", "2025-03", entity="LE-A"), 2)
        quarterly = run(rows, resolution=dm.QUARTER)
        self.assertEqual(quarterly["time_series"]["periods"], ["2025-Q1", "2025-Q2"])
        self.assertEqual(series(quarterly, "Fälle", CASES, "2025-Q2"), 1)

    def test_problems_follow_their_period_in_the_series(self):
        rows = [row("a", datenkranz("t1", submitted="2025-03-01", tnm=("cT1",))),
                row("b", datenkranz("t2", submitted="2025-04-01"))]
        result = run(rows)
        self.assertEqual(series(result, "Rückfragen", dm.P_TNM, "2025-03"), 1)
        self.assertEqual(series(result, "Rückfragen", dm.P_TNM, "2025-04"), 0)

    def test_suppression(self):
        result = cases(*[datenkranz(f"t{i}", gender="male" if i < 2 else "female") for i in range(7)], min_cell=5)
        self.assertEqual(get(result, "Geschlecht", "m"), "<5")
        self.assertIsNone(get(result, "Geschlecht", "m", field="percent"))
        # w = base - m would reveal m: complementary suppression
        self.assertEqual(get(result, "Geschlecht", "w"), "<5")
        self.assertEqual(get(result, "Geschlecht", "d"), 0)          # zero of a large base stays
        # a base below the threshold hides the value, also a zero
        self.assertEqual(get(result, "Diagnose: Kodierung", "germlineDiagnoses angegeben, von bestätigten", field="base"), 0)
        small = cases(*[datenkranz(f"t{i}") for i in range(3)], min_cell=5)
        self.assertEqual(get(small, "Geschlecht", "m"), "<5")
        self.assertEqual(get(small, "Geschlecht", "m", field="base"), "<5")
        self.assertEqual(get(small, "Bearbeitungsdauer medizinisch", "Median (Tage)"), "<5")
        self.assertEqual(series(small, "Geschlecht", "w", "2025-03"), "<5")


class RobustnessTests(unittest.TestCase):

    def test_null_where_lists_and_objects_are_expected(self):
        content = datenkranz("t1", therapies=[therapy_rec("r1")])
        content["metaData"]["researchConsents"] = None
        content["metaData"]["mvConsent"] = None
        content["case"]["priorProcedures"] = None
        content["case"]["diagnosisOd"]["tnmClassifications"] = None
        content["plan"]["recommendedSystemicTherapies"][0]["evidenceLevelDetails"] = None
        content["plan"]["recommendedStudies"] = None
        content["molecular"] = {"smallVariants": None, "complexBiomarkers": [None, {"tmb": None}]}
        content["followUp"] = {"followUpOds": [None, {"therapies": None, "followUpDate": None}]}
        result = cases(content, datenkranz("t2"))
        self.assertEqual(get(result, "Fälle", CASES), 2)
        self.assertEqual(problems(result, dm.P_UNREADABLE), [])

    def test_unreadable_case_becomes_a_problem(self):
        broken = datenkranz("t1")
        broken["metaData"]["molecularBoardDecisionDate"] = None
        no_case = datenkranz("t2")
        no_case["case"] = None
        rows = [row("a", broken), row("b", no_case), row("c", datenkranz("t3")),
                dm.SubmissionRow(json.dumps(["LE-A", "d"]), "2025-03-01", {"metaData": None}, "LE-A"),
                dm.SubmissionRow(json.dumps(["LE-A", "e"]), "2025-03-01", None, "LE-A")]
        result = run(rows)
        self.assertEqual(get(result, "Fälle", CASES), 1)
        self.assertEqual(len(problems(result, dm.P_UNREADABLE)), 4)
        self.assertEqual(get(result, "Fälle", "Fälle nicht auswertbar (nicht in den Fällen)"), 4)

    def test_failure_of_a_later_submission_marks_the_case(self):
        rows = [row("a", datenkranz("t1"))]
        failures = [dm.Failure("LE-A", json.dumps(["LE-A", "a"]), "t2", "2025-04-01T00:00:00", "conversion failed"),
                    dm.Failure("LE-A", json.dumps(["LE-A", "z"]), "t3", "2025-04-01T00:00:00", "unreadable")]
        result = run(rows, failures=failures)
        self.assertEqual(get(result, "Fälle", "Einreichungen nicht konvertierbar (nicht in den Fällen)"), 2)
        self.assertEqual(get(result, "Fälle", "davon Episode mit älterem Stand ausgewertet (eine spätere Einreichung nicht konvertierbar)"), 1)
        self.assertTrue(problems(result, dm.P_NOT_CONVERTED)[0]["detail"].startswith("Episode mit älterem Stand"))


def random_submissions(seed, n=300):
    """Random cases over 3 LEs and a year, with follow-ups, tests and broken submissions"""
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        le = rnd.choice(["LE-A", "LE-B", "LE-C"])
        month = rnd.randint(1, 12)
        submitted = f"2025-{month:02d}-{rnd.randint(10, 28):02d}"
        gfk1 = f"2025-{month:02d}-0{rnd.randint(1, 5)}"
        library = rnd.choice(["wgs", "wgs", "wes", "panel", "none", "wgs_lr"])
        recs = [therapy_rec(f"r{i}-{k}", evidence=rnd.choice(dm.EVIDENCE)) for k in range(rnd.randint(0, 3))]
        content = datenkranz(
            f"t{i}", submitted=submitted, gfk1=gfk1, mtb=None if library == "none" or rnd.random() < 0.1 else submitted[:8] + "09",
            gender=rnd.choice(["male", "female", "other", None]), birth=f"{rnd.randint(1930, 2020)}-0{rnd.randint(1, 9)}",
            coverage=rnd.choice(["GKV", "PKV", "SEL"]), main=rnd.choice(["C34.1", "C18.0", "C50.9", "C92.0", "D46.1", "Q85.1", None]),
            ecog=rnd.choice(dm.ECOG + (None,)), library=library, include=rnd.random() > 0.05, therapies=recs,
            consents=[broad_consent()] if rnd.random() < 0.6 else [],
        )
        rows.append(row(str(i), content, site=le, reason=rnd.choice([None, "patient-refusal", "technical-issues"])))
        if rnd.random() < 0.3:
            fu_month = min(month + rnd.randint(1, 6), 12)
            ref = recs[0]["identifier"] if recs and rnd.random() < 0.5 else "own"
            fu = datenkranz(f"t{i}f", type_="followup", submitted=f"2025-{fu_month:02d}-28", gfk1=gfk1, library=library,
                            mtb=content.get("plan", {}).get("carePlanOd", {}).get("molecularBoardDecisionDate"),
                            therapies=recs, follow_ups=[follow_up(f"2025-{fu_month:02d}-20", rnd.choice(["living", "deceased"]),
                                                                  [{"reference": ref, "therapyStartDate": f"2025-{fu_month:02d}-15"}])])
            rows.append(row(str(i), fu, site=le))
        if rnd.random() < 0.05:
            rows.append(row(f"x{i}", datenkranz(f"t{i}x", type_="correction", submitted=submitted), site=le))
        if rnd.random() < 0.05:
            rows.append(row(f"y{i}", datenkranz(f"t{i}y", type_="test", submitted=submitted), site=le))
    return rows


PARTITIONS = (   # sections whose items partition the cases (or their base)
    "Geschlecht", "Alter bei Einschluss (GFK1)", "Versicherung",
    "Sequenzierung (libraryType, eingeschlossene Fälle)", "Tumorentität (Hauptdiagnose ICD-10-GM, gruppiert)",
    "Tumorentität (sequenzierte Fälle)", "Tumorstadium (M-Kategorie aus tnmClassifications)", "ECOG bei Einschluss",
    "Grading (Differenzierungsgrad)", "Broad Consent (Stand der letzten Einreichung, gültig zu deren Datum)",
)


class PropertyTests(unittest.TestCase):

    def test_partitions_and_le_columns(self):
        for seed in range(3):
            result = run(random_submissions(seed))
            n = get(result, "Fälle", CASES)
            self.assertGreater(n, 200)
            for section in PARTITIONS:
                rows = [r for r in result["rows"] if r["section"] == section and not r["item"].startswith("Grund aus")]
                for entity in result["entities"]:
                    total = sum(r["values"][entity]["value"] for r in rows)
                    self.assertEqual(total, rows[0]["values"][entity]["base"], (seed, section, entity))
                if "sequenzierte" not in section and "eingeschlossene" not in section:
                    self.assertEqual(rows[0]["values"][dm.TOTAL]["base"], n, section)
            s = "Follow-up: Erstmeldung mindestens 4 Monate vor dem Stichtag"
            due = get(result, s, "Fälle")
            self.assertEqual(sum(get(result, s, item) for item in ("ohne Folgemeldung", "mit 1 Folgemeldung",
                             "mit 2 Folgemeldungen", "mit 3 Folgemeldungen", "mit 4 und mehr Folgemeldungen")), due)
            # the follow-up categories and states partition the cases with GFK2, the states each category
            fu = [r for r in result["rows"] if r["section"] == dm.FU_SECTION]
            for prefix in ("Kategorie ", "Stand am Stichtag"):
                part = [r for r in fu if r["item"].startswith(prefix)]
                self.assertEqual(sum(r["values"][dm.TOTAL]["value"] for r in part), part[0]["values"][dm.TOTAL]["base"])
            for key, _, _ in dm.FU_CATEGORIES:
                part = [r for r in result["rows"] if r["section"] == dm.FU_CATEGORY_SECTION
                        and r["item"].startswith(f"Kategorie {key}: ") and "von fälligen" not in r["item"]]
                self.assertEqual(len(part), len(dm.FU_STATES))
                self.assertEqual(sum(r["values"][dm.TOTAL]["value"] for r in part), part[0]["values"][dm.TOTAL]["base"])
            # every count of the total is the sum of the LE columns (before suppression), also per period
            les = result["entities"][1:]
            for r in result["rows"]:
                if r["unit"] == dm.COUNT:
                    self.assertEqual(sum(r["values"][le]["value"] for le in les), r["values"][dm.TOTAL]["value"], r["key"])
            for r in result["time_series"]["rows"]:
                if r["unit"] == dm.COUNT:
                    for p in result["time_series"]["periods"]:
                        self.assertEqual(sum(r["values"][le][p]["value"] for le in les), r["values"][dm.TOTAL][p]["value"])
            # the periods of the series add up to the whole period for the case counts
            row_ = next(r for r in result["time_series"]["rows"] if r["item"] == CASES)
            self.assertEqual(sum(row_["values"][dm.TOTAL][p]["value"] for p in result["time_series"]["periods"]), n)

    def test_rows_are_the_same_for_every_entity_and_period(self):
        result = run(random_submissions(7, 50))
        keys = [r["key"] for r in result["rows"]]
        self.assertEqual(keys, [r["key"] for r in result["time_series"]["rows"]])
        self.assertEqual(len(keys), len(set(keys)))


def hidden(v):
    return isinstance(v["value"], str) and v["value"].startswith("<")


def single_suppressed_groups(result):
    """The groups of count cells (row across the entities, partition in a column, row across the periods) with
    exactly one suppressed cell beside a nonzero visible one: that cell could be derived"""
    entities, periods = result["entities"], result["time_series"]["periods"]
    rows = [(r, s) for r, s in zip(result["rows"], result["time_series"]["rows"]) if r["unit"] == dm.COUNT]

    def at(pair, le, p):
        return pair[0]["values"][le] if p == dm.WHOLE else pair[1]["values"][le][p]

    groups = [[at(x, le, p) for le in entities] for x in rows for p in [dm.WHOLE] + periods]
    groups += [[at(x, le, p) for p in [dm.WHOLE] + periods] for x in rows for le in entities]
    parts = {}
    for x in rows:
        part = dm.partition_of(SimpleNamespace(section=x[0]["section"], item=x[0]["item"]))
        if part:
            parts.setdefault(part, []).append(x)
    groups += [[at(x, le, p) for x in xs] for xs in parts.values() for le in entities for p in [dm.WHOLE] + periods]
    return [g for g in groups if sum(hidden(v) for v in g) == 1
            and any(not hidden(v) and v["value"] for v in g)]


class LeakTests(unittest.TestCase):

    def test_notes_title_and_json_reveal_no_small_counts_or_case_keys(self):
        rows = [row(str(i), datenkranz(f"t{i}", submitted=f"2025-03-{10 + i}")) for i in range(3)]
        rows.append(row("x", datenkranz("tx", type_="test")))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump([{"case": r.case, "site": r.site, "submittedAt": r.submitted_at, "content": r.content,
                            **({"case": "a", "content": datenkranz("tp", tnm=("cT1",), submitted="2025-03-01")} if i == 0 else {})}
                           for i, r in enumerate(rows)], f)
            out = os.path.join(tmp, "m")
            dm.main([path, "--output", out, "--reference-date", "2026-11-01", "--rueckfragen-per-le", "--suppress-scope", "all"])
            with open(out + ".json", encoding="utf-8") as f:
                text = f.read()
            per_le = sorted(name for name in os.listdir(tmp) if name.startswith("m_rueckfragen_"))
            with open(os.path.join(tmp, per_le[0]), encoding="utf-8-sig") as f:
                le_lines = f.read().splitlines()
        result = json.loads(text)
        self.assertTrue(result["notes"][0].startswith("<5 Fälle"))
        self.assertTrue(result["notes"][1].startswith("<5 Testeinreichungen"))
        self.assertEqual(result["title"], "AUSWERTUNG MTB gesamter Export (Erstmeldungen 03.2025 - 03.2025)")
        self.assertNotIn("problems", result)
        self.assertNotIn('\\"a\\"', text)           # no case key
        self.assertEqual(per_le, ["m_rueckfragen_LE-A.csv"])
        self.assertEqual(len(le_lines), 2)
        self.assertTrue(le_lines[1].startswith(f"LE-A (LE-A-ID);{dm.P_TNM};"))


class ComplementarySuppressionTests(unittest.TestCase):

    def test_an_le_cell_is_not_derivable_from_gesamt(self):
        rows = [row(f"a{i}", datenkranz(f"a{i}", gender="male" if i < 2 else "female")) for i in range(8)]
        rows += [row(f"b{i}", datenkranz(f"b{i}", gender="male" if i < 9 else "female"), site="LE-B") for i in range(14)]
        result = run(rows, min_cell=5)
        self.assertEqual(get(result, "Geschlecht", "m", entity="LE-A"), "<5")
        # LE-A w = 8 - m would give m; then Gesamt w - LE-B w would give LE-A w, Gesamt m - LE-B m LE-A m
        self.assertEqual(get(result, "Geschlecht", "w", entity="LE-A"), "<5")
        self.assertEqual(get(result, "Geschlecht", "w", entity="LE-B"), "<5")
        self.assertEqual(get(result, "Geschlecht", "m", entity="LE-B"), "<5")
        self.assertEqual((get(result, "Geschlecht", "m"), get(result, "Geschlecht", "w")), (11, 11))
        self.assertEqual(single_suppressed_groups(result), [])

    def test_complementary_cells_use_the_primary_marker_and_are_counted(self):
        rows = [row(f"a{i}", datenkranz(f"a{i}", gender="male" if i < 2 else "female")) for i in range(8)]
        rows += [row(f"b{i}", datenkranz(f"b{i}", gender="male" if i < 9 else "female"), site="LE-B") for i in range(14)]
        result = run(rows, min_cell=5)
        values = [v["value"] for r in result["rows"] for v in r["values"].values()]
        series_values = [v["value"] for r in result["time_series"]["rows"] for le in r["values"].values()
                         for v in le.values()]
        self.assertFalse({x for x in values + series_values if isinstance(x, str)} - {"<5"})
        counts = result["suppressed"]
        self.assertGreaterEqual(counts["complementary"], 3)       # LE-A w, LE-B w, LE-B m at least
        self.assertEqual(counts["primary"] + counts["complementary"], values.count("<5"))
        series_counts = result["time_series"]["suppressed"]
        self.assertEqual(series_counts["primary"] + series_counts["complementary"], series_values.count("<5"))
        self.assertIn("kann daher auch 5 oder mehr", " ".join(result["notes"]))

    def test_a_category_is_not_derivable_from_the_base(self):
        result = cases(*[datenkranz(f"t{i}", coverage="PKV" if i < 3 else "GKV" if i < 9 else "BG") for i in range(16)],
                       min_cell=5)
        self.assertEqual(get(result, "Versicherung", "PKV"), "<5")
        self.assertEqual(get(result, "Versicherung", "GKV"), "<5")      # the smallest other nonzero
        self.assertEqual(get(result, "Versicherung", "andere"), 7)

    def test_a_period_is_not_derivable_from_the_whole_period(self):
        rows = [row(f"m{i}", datenkranz(f"m{i}", submitted="2025-03-05")) for i in range(2)]
        rows += [row(f"n{i}", datenkranz(f"n{i}", submitted="2025-04-05")) for i in range(6)]
        rows += [row(f"o{i}", datenkranz(f"o{i}", submitted="2025-05-05")) for i in range(7)]
        result = run(rows, min_cell=5)
        self.assertEqual(series(result, "Fälle", CASES, "2025-03"), "<5")
        self.assertEqual(series(result, "Fälle", CASES, "2025-04"), "<5")
        self.assertEqual(series(result, "Fälle", CASES, "2025-05"), 7)
        self.assertEqual(get(result, "Fälle", CASES), 15)

    def test_no_group_with_a_single_suppressed_cell(self):
        for seed in range(2):
            result = run(random_submissions(seed, 150), min_cell=5)
            self.assertEqual(single_suppressed_groups(result), [], seed)


def is_diagnosis(r):
    return dm.is_diagnosis_row(SimpleNamespace(section=r["section"], item=r["item"]))


def scope_groups(result):
    """The groups of diagnosis count cells the diagnosis scope closes (row across the entities, row across the
    periods, PARTITIONS and DIAGNOSIS_PARTITIONS in a column), as lists of (row index, entity, period)"""
    entities, periods = result["entities"], [dm.WHOLE] + result["time_series"]["periods"]
    rows = [i for i, r in enumerate(result["rows"]) if r["unit"] == dm.COUNT and is_diagnosis(r)]
    groups = [[(i, le, p) for le in entities] for i in rows for p in periods]
    groups += [[(i, le, p) for p in periods] for i in rows for le in entities]
    parts = {}
    for i in rows:
        r = SimpleNamespace(**result["rows"][i])
        if dm.partition_of(r):
            parts.setdefault(dm.partition_of(r), []).append(i)
        for n, (section, item) in enumerate(dm.DIAGNOSIS_PARTITIONS):
            if re.fullmatch(section, r.section) and re.match(item, r.item):
                parts.setdefault(n, []).append(i)
        for n, keys in enumerate(dm.DIAGNOSIS_KEY_PARTITIONS):
            if r.key in keys:
                parts.setdefault(("keys", n), []).append(i)
    groups += [[(i, le, p) for i in xs] for xs in parts.values() for le in entities for p in periods]
    return [g for g in groups if len(g) > 1]


def cell_at(result, i, le, p):
    return result["rows"][i]["values"][le] if p == dm.WHOLE else result["time_series"]["rows"][i]["values"][le][p]


class DiagnosisScopeTests(unittest.TestCase):
    """--suppress-scope diagnoses: only the diagnosis rows are suppressed, all other numbers are exact"""

    def test_the_diagnosis_sections_and_bases_exist(self):
        result = run(random_submissions(1, 60))
        sections = {r["section"] for r in result["rows"]}
        self.assertEqual([s for s in dm.DIAGNOSIS_SECTIONS if s not in sections], [])
        keys = {r["key"] for r in result["rows"]}
        labels = {r["base_label"] for r in result["rows"]}
        self.assertEqual([k for k in dm.DIAGNOSIS_BASES.values() if k not in keys], [])
        self.assertEqual([b for b in dm.DIAGNOSIS_BASES if b not in labels], [])
        self.assertEqual([x for x in dm.DIAGNOSIS_ITEMS if f"{x[0]} | {x[1]}" not in keys], [])
        self.assertEqual([k for x in dm.DIAGNOSIS_KEY_PARTITIONS + dm.DIAGNOSIS_LINKED for k in x if k not in keys], [])
        for section, item in dm.DIAGNOSIS_PARTITIONS:
            self.assertGreater(sum(1 for r in result["rows"] if re.fullmatch(section, r["section"])
                                   and re.match(item, r["item"])), 1, item)

    def test_stage_grading_hereditary_and_germline_variant_are_diagnosis_rows(self):
        germline = {"smallVariants": [{"genomicSource": "germline", "gene": {"code": "BRCA1"}}]}
        result = cases(*[datenkranz(f"t{i}", grading=None if i < 2 else "3", tnm=() if i == 0 else ("cT2", "cN1", "cM0"),
                                    disease="hereditary" if i == 0 else "oncological",
                                    molecular=germline if i == 1 else None) for i in range(8)],
                       min_cell=5, scope=dm.SUPPRESS_DIAGNOSES)
        self.assertEqual(get(result, dm.GRADING_SECTION, dm.MISSING), "<5")
        self.assertEqual(get(result, dm.GRADING_SECTION, "3 (schlecht differenziert)"), "<5")        # 8 - 2: complementary in the partition
        # the fill rate is the cases minus "nicht angegeben": suppressed with it
        self.assertEqual(get(result, dm.FILL_CASES, "case.diagnosisOd.grading"), "<5")
        self.assertEqual(get(result, dm.STAGE_SECTION, "kein TNM"), "<5")
        self.assertEqual(get(result, dm.FILL_CASES, "case.diagnosisOd.tnmClassifications"), "<5")
        self.assertEqual(get(result, dm.FILL_CASES, "TNM oder additionalClassification"), "<5")   # = TNM here
        self.assertEqual(get(result, "Fälle", "davon hereditär (diseaseType hereditary)"), "<5")
        self.assertEqual(get(result, "Molekulare Befunde", "Keimbahnvariante berichtet (genomicSource germline)"), "<5")
        self.assertEqual(get(result, "Fälle", CASES), 8)
        self.assertEqual(get(result, "ECOG bei Einschluss", "1"), 8)
        self.assertEqual(get(result, "Füllgrad optionaler Felder (Fälle)", "case.priorProcedures"), 0)

    def test_only_diagnosis_rows_are_suppressed(self):
        result = cases(*[datenkranz(f"t{i}", gender="male" if i < 2 else "female", main="C50.9" if i < 2 else "C34.1")
                         for i in range(7)], min_cell=5, scope=dm.SUPPRESS_DIAGNOSES)
        self.assertEqual(result["suppress_scope"], dm.SUPPRESS_DIAGNOSES)
        self.assertEqual((get(result, "Geschlecht", "m"), get(result, "Geschlecht", "m", field="percent")), (2, 28.6))
        self.assertEqual(get(result, "Tumorentität (Hauptdiagnose", "Mamma (C50)"), "<5")
        self.assertEqual(get(result, "Tumorentität (Hauptdiagnose", "Lunge (C33-C34)"), "<5")   # 7 - Mamma
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch", "Median (Tage)"), 41.0)
        self.assertTrue(result["notes"][0].startswith("7 Fälle"))
        self.assertTrue(any(n.startswith("Unterdrückt werden nur die Diagnosezeilen") for n in result["notes"]))
        few = cases(*[datenkranz(f"t{i}") for i in range(3)], min_cell=5, scope=dm.SUPPRESS_DIAGNOSES)
        self.assertEqual(get(few, "Fälle", CASES), 3)
        self.assertEqual(get(few, "Tumorentität (Hauptdiagnose", "Mamma (C50)"), "<5")   # a 0 of 3 cases, too

    def test_a_diagnosis_cell_is_not_the_exact_total_minus_shown_zeros(self):
        # LE-A: 10 sequenced lung cases without GFK2, 2 breast cases with: "GFK2 dokumentiert" (2) is exact, and
        # with the other entities shown as 0 the breast cases would be 2
        rows = [row(f"l{i}", datenkranz(f"l{i}", mtb=None)) for i in range(10)]
        rows += [row(f"m{i}", datenkranz(f"m{i}", main="C50.9")) for i in range(2)]
        rows += [row(f"b{i}", datenkranz(f"b{i}", main="C50.9"), site="LE-B") for i in range(9)]
        result = run(rows, min_cell=5, scope=dm.SUPPRESS_DIAGNOSES)
        self.assertEqual(get(result, "GFK2 (MTB): Ergebnis (sequenzierte", "GFK2 dokumentiert (plan.carePlanOd)", "LE-A"), 2)
        s = "GFK2 (MTB): Ergebnis nach Tumorentität"
        shown = [r["values"]["LE-A"]["value"] for r in result["rows"]
                 if r["section"].startswith(s) and r["item"].endswith(": GFK2 dokumentiert")]
        self.assertEqual(get(result, s, "Mamma (C50): GFK2 dokumentiert", "LE-A"), "<5")
        self.assertGreaterEqual(shown.count("<5"), 2, shown)
        self.assertEqual(get(result, s, "Lunge (C33-C34): GFK2 dokumentiert", "LE-A"), "<5")   # the 0 beside LE-B's 0
        # the base of a row that is the count of a suppressed diagnosis row is suppressed with it
        self.assertEqual(get(result, "Tumorentität (sequenzierte", "Lunge (C33-C34)", "LE-A"), "<5")
        self.assertEqual(get(result, s, "Lunge (C33-C34): GFK2 dokumentiert", "LE-A", "base"), "<5")
        self.assertEqual(single_scope_groups(result), [])

    def test_non_diagnosis_cells_exact_and_no_suppressed_cell_follows_from_shown_ones(self):
        for seed, n in ((0, 150), (3, 40), (5, 15)):
            with self.subTest(seed=seed):
                rows = random_submissions(seed, n)
                full = run(rows, resolution=dm.QUARTER)
                result = run(rows, min_cell=5, resolution=dm.QUARTER, scope=dm.SUPPRESS_DIAGNOSES)
                hidden_cells = 0
                for i, (r, f) in enumerate(zip(result["rows"], full["rows"])):
                    for le in result["entities"]:
                        for p in [dm.WHOLE] + result["time_series"]["periods"]:
                            shown, true = cell_at(result, i, le, p), cell_at(full, i, le, p)
                            if is_diagnosis(r):
                                hidden_cells += hidden(shown)
                                if not hidden(shown) and shown["base"] != "<5":
                                    self.assertEqual(shown, true, (r["key"], le, p))
                            else:
                                self.assertEqual(shown, true, (r["key"], le, p))
                self.assertGreater(hidden_cells, 0)
                self.assertEqual(single_scope_groups(result), [])
                # linked diagnosis rows are suppressed together
                index = {r["key"]: i for i, r in enumerate(result["rows"])}
                for a, b in dm.DIAGNOSIS_LINKED:
                    for le in result["entities"]:
                        for p in [dm.WHOLE] + result["time_series"]["periods"]:
                            self.assertEqual(hidden(cell_at(result, index[a], le, p)),
                                             hidden(cell_at(result, index[b], le, p)), (a, le, p))
                # a base that is a count of another row is suppressed where that count is
                index = {r["key"]: i for i, r in enumerate(result["rows"])}
                for j, r in enumerate(result["rows"]):
                    if r["base_label"] in dm.DIAGNOSIS_BASES:
                        for le in result["entities"]:
                            if hidden(cell_at(result, index[dm.DIAGNOSIS_BASES[r["base_label"]]], le, dm.WHOLE)):
                                self.assertEqual(cell_at(result, j, le, dm.WHOLE)["base"], "<5", (r["key"], le))

    def test_scope_all_is_unchanged_and_an_unknown_scope_is_refused(self):
        # the previous version of the script (before --suppress-scope), from the git history
        try:
            source = subprocess.run(["git", "show", f"{PREVIOUS}:scripts/dnpm_evaluation_matrix/dnpm_evaluation_matrix.py"],
                                    cwd=os.path.dirname(os.path.abspath(__file__)), capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest(f"commit {PREVIOUS} not available")
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "previous_matrix.py"), "wb") as f:
                f.write(source)
            spec = importlib.util.spec_from_file_location("previous_matrix", os.path.join(tmp, "previous_matrix.py"))
            previous = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(previous)
            for seed, n in ((2, 40), (4, 150)):
                rows = random_submissions(seed, n)
                path = os.path.join(tmp, "in.json")
                with open(path, "w") as f:
                    json.dump([{"case": r.case, "site": r.site, "submittedAt": r.submitted_at, "content": r.content}
                               for r in rows], f)
                outputs = []
                for name, main, scope in (("new", dm.main, ["--suppress-scope", "all"]), ("old", previous.main, [])):
                    out = os.path.join(tmp, name)
                    # without the rows and the note added since (follow-up after the SOP)
                    with contextlib.redirect_stderr(io.StringIO()), without_follow_up_sop():
                        main([path, "--output", out, "--reference-date", "2026-11-01", "--resolution", "quarter", *scope])
                    files = {}
                    for suffix in (".json", ".csv", "_zeitreihe.csv", "_rueckfragen.csv"):
                        with open(out + suffix, "rb") as f:
                            files[suffix] = f.read()
                    outputs.append(files)
                new, old = outputs
                self.assertEqual(json.loads(new.pop(".json")), {**json.loads(old.pop(".json")), "suppress_scope": "all"})
                self.assertEqual(new, old)      # the CSVs byte by byte
        with self.assertRaises(ValueError):
            run(random_submissions(1, 10), scope="none")


@contextlib.contextmanager
def without_follow_up_sop():
    """dm.run without the follow-up rows and note after the SOP (added after PREVIOUS)"""
    matrix_rows, run_ = dm.matrix_rows, dm.run

    def rows(*args, **kwargs):
        return [r for r in matrix_rows(*args, **kwargs) if r.section not in dm.FU_SECTIONS]

    def run(*args, **kwargs):
        result = run_(*args, **kwargs)
        result["notes"] = [n for n in result["notes"] if not n.startswith(dm.FU_NOTE)]
        return result
    dm.matrix_rows, dm.run = rows, run
    try:
        yield
    finally:
        dm.matrix_rows, dm.run = matrix_rows, run_


def single_scope_groups(result):
    """The diagnosis groups with exactly one suppressed cell: with the exact totals, that cell could be derived"""
    return [g for g in scope_groups(result) if sum(hidden(cell_at(result, *k)) for k in g) == 1]


class ReviewTests(unittest.TestCase):

    def test_cutoff_is_the_default_reference_date_and_bounds_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump([{"case": "a", "site": "LE-A", "submittedAt": "2025-03-01", "content": datenkranz("t1")}], f)
            out = os.path.join(tmp, "m")
            dm.main([path, "--output", out, "--cutoff", "2025-12-31"])
            with open(out + ".json", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["reference_date"], "2025-12-31")
            with self.assertRaises(SystemExit):
                dm.main([path, "--output", out, "--cutoff", "2025-12-31", "--reference-date", "2026-01-01"])
        with self.assertRaises(ValueError):
            dm.run([], [], None, None, date(2026, 1, 1), cutoff=date(2025, 12, 31))

    def test_gfk2_is_the_date_of_the_first_submission_with_a_plan(self):
        # a later correction reports an earlier board: the first submission with a plan counts
        rows = [row("a", datenkranz("t1", mtb=None, submitted="2025-02-01")),
                row("a", datenkranz("t2", type_="addition", mtb="2025-02-20", submitted="2025-03-01")),
                row("a", datenkranz("t3", type_="correction", mtb="2025-02-10", submitted="2025-04-01"))]
        result = run(rows)
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch (GFK1 bis GFK2)", "Median (Tage)"), 41.0)
        self.assertEqual(get(result, "Bearbeitungsdauer dokumentarisch (GFK2 bis Einreichung beim KDK)", "Median (Tage)"), 9.0)

    def test_last_contact_rows_say_what_they_count(self):
        result = cases(datenkranz("t1", follow_ups=[follow_up(last_contact="2025-07-01")]))
        self.assertEqual(get(result, "Follow-up und Vitalstatus",
                             "lastContactDate angegeben (DIP: früher lost to follow-up, Kontakt wieder aufgenommen)"), 1)
        self.assertFalse(any(r["item"].endswith("(DIP: lost to follow-up)") for r in result["rows"]))


class InputOutputTests(unittest.TestCase):

    def test_reads_the_export_with_reasons(self):
        export = {"tables": [
            {"name": "datenkranz", "columns": ["site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt",
                                               "content", "reasonResearchConsentMissing"],
             "rows": [{"site": "LE-A", "useCase": "MTB", "patient": "p1", "episodeOfCare": "e1", "tan": "t1",
                       "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1"),
                       "reasonResearchConsentMissing": "patient-inability"},
                      {"site": "LE-A", "useCase": "RD", "patient": "p2", "episodeOfCare": "e1", "tan": "t2",
                       "submittedAt": "2025-03-01T00:00:00", "content": {}}]},
            {"name": "failures", "rows": [{"site": "LE-A", "useCase": "MTB", "tan": "t3", "error": "e",
                                           "submittedAt": "2025-03-02T00:00:00"},
                                          {"site": "LE-A", "useCase": "RD", "tan": "t4", "error": "e"}]},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "export.json")
            with open(path, "w") as f:
                json.dump(export, f)
            rows, failures = dm.load_submissions(path)
        self.assertEqual((len(rows), len(failures), rows[0].reason), (1, 1, "patient-inability"))
        result = run(rows, failures=failures)
        self.assertEqual(get(result, "Broad Consent (Stand", "nicht übermittelt: Patient nicht einwilligungsfähig"), 1)

    def test_writes_json_and_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump([{"case": "a", "site": "LE-A", "submittedAt": "2025-03-01T00:00:00",
                            "content": datenkranz("t1", mtb="2025-04-01")}], f)
            out = os.path.join(tmp, "matrix")
            dm.main([path, "--output", out, "--min-cell", "1", "--reference-date", "2026-11-01"])
            with open(out + ".json", encoding="utf-8") as f:
                result = json.load(f)
            with open(out + ".csv", encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
            with open(out + "_zeitreihe.csv", encoding="utf-8-sig") as f:
                series_lines = f.read().splitlines()
            with open(out + "_rueckfragen.csv", encoding="utf-8-sig") as f:
                problem_lines = f.read().splitlines()
        self.assertEqual(set(result), {"title", "period", "min_cell", "suppress_scope", "suppressed", "reference_date", "cutoff", "entities", "labels",
                                       "rows", "notes", "time_series"})       # no case keys
        self.assertEqual(lines[0], "LE;Abschnitt;Kategorie;Einheit;Wert;Bezug;Basis;Anteil (%)")
        self.assertIn("Gesamt;Geschlecht;w;Anzahl;1;Fälle;1;100,0", lines)
        self.assertIn("LE-A (LE-A-ID);2025-03;Geschlecht;w;Anzahl;1;Fälle;1;100,0", series_lines)
        self.assertEqual(series_lines[0], "LE;Zeitraum;Abschnitt;Kategorie;Einheit;Wert;Bezug;Basis;Anteil (%)")
        self.assertTrue(problem_lines[1].startswith(f"LE-A (LE-A-ID);{dm.P_GFK2_AFTER_SUBMISSION};"))


if __name__ == "__main__":
    unittest.main()
