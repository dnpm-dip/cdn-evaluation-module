"""Tests of dnpm_evaluation_matrix.py: python3 -m unittest discover -s scripts/dnpm_evaluation_matrix"""

import contextlib
import io
import json
import os
import random
import tempfile
import unittest
from datetime import date

import dnpm_evaluation_matrix as dm

MDAT_RESEARCH_USE = "2.16.840.1.113883.3.1937.777.24.5.3.8"
REFERENCE = date(2026, 11, 1)


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


def run(rows, start=None, end=None, failures=(), resolution=dm.MONTH, cutoff=None):
    """The matrix (not suppressed)"""
    return dm.run(rows, list(failures), start, end, cutoff or REFERENCE, resolution=resolution, cutoff=cutoff)


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
            # category 3, GFK2 20 months before, no follow-up: lost to follow-up (SOP), not due
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
        self.assertEqual(states, [1, 1, 1, 3, 1])       # c5; c3; c4 lost; c1, c6, c7; c2
        # lost to follow-up is neither due nor overdue: the base of "aktuell, von fälligen" leaves it out
        self.assertEqual((get(r, s, "Follow-up aktuell, von fälligen"), get(r, s, "Follow-up aktuell, von fälligen", field="base")),
                         (3, 4))
        self.assertEqual(get(r, s, "Kategorie 2 ab 24 Monate nach GFK2 (danach Intervall wie Kategorie 3)"), 1)
        lost = "kein dokumentierter Kontakt in den 12 Monaten vor dem Stichtag (lost to follow-up nach SOP)"
        self.assertEqual((get(r, s, lost), get(r, s, lost, field="base")), (1, 2))   # c4 of c4, c6
        c = dm.FU_CATEGORY_SECTION
        self.assertEqual(get(r, c, f"Kategorie 2: {dm.FU_OVERDUE}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 2: {dm.FU_CURRENT}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 3: {dm.FU_NOT_DUE}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 3: {dm.FU_DECEASED}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 3: {dm.FU_LOST}"), 1)
        self.assertEqual(get(r, c, f"Kategorie 3: {dm.FU_OVERDUE}"), 0)
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

    def test_small_cells_and_their_quartiles_are_exact(self):
        rows = [row(f"a{i}", datenkranz(f"a{i}", follow_ups=[follow_up()] if i < 3 else None)) for i in range(8)]
        result = run(rows)
        self.assertEqual(get(result, self.T, "Erstes Follow-up: Fälle"), 3)
        self.assertIsInstance(get(result, self.T, "Erstes Follow-up: Median (Tage)"), float)
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

    def test_nothing_is_suppressed(self):
        # the oncology matrix is not suppressed (decision of the data holders): small counts, their bases, shares and
        # statistics of few values are exact, in the matrix and in the time series
        result = cases(*[datenkranz(f"t{i}", gender="male" if i < 2 else "female") for i in range(3)])
        self.assertEqual((get(result, "Geschlecht", "m"), get(result, "Geschlecht", "m", field="base"),
                          get(result, "Geschlecht", "m", field="percent")), (2, 3, 66.7))
        self.assertEqual(get(result, "Bearbeitungsdauer medizinisch", "Median (Tage)"), 41.0)
        self.assertEqual(series(result, "Geschlecht", "w", "2025-03"), 1)
        values = [v for r in result["rows"] for v in r["values"].values()]
        values += [v for r in result["time_series"]["rows"] for le in r["values"].values() for v in le.values()]
        self.assertFalse([v for v in values for f in ("value", "base") if isinstance(v[f], str)])
        self.assertFalse({"min_cell", "suppress_scope", "suppressed"} & set(result))
        self.assertTrue(any(n.startswith("Keine Zellen unterdrückt") for n in result["notes"]))
        self.assertFalse(any("<5" in n for n in result["notes"]))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            dm.main(["in.json", "--min-cell", "5"])      # the options are gone


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


class LeakTests(unittest.TestCase):

    def test_json_has_no_case_keys_and_rueckfragen_per_le_only(self):
        rows = [row(str(i), datenkranz(f"t{i}", submitted=f"2025-03-{10 + i}")) for i in range(3)]
        rows.append(row("x", datenkranz("tx", type_="test")))
        rows.append(row("b0", datenkranz("tb", submitted="2025-03-20"), site="LE-B"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w") as f:
                json.dump([{"case": r.case, "site": r.site, "submittedAt": r.submitted_at, "content": r.content,
                            **({"case": "a", "content": datenkranz("tp", tnm=("cT1",), submitted="2025-03-01")} if i == 0 else {})}
                           for i, r in enumerate(rows)], f)
            out = os.path.join(tmp, "m")
            with contextlib.redirect_stderr(io.StringIO()):
                dm.main([path, "--output", out, "--reference-date", "2026-11-01", "--rueckfragen-per-le"])
            with open(out + ".json", encoding="utf-8") as f:
                text = f.read()
            names = sorted(name for name in os.listdir(tmp) if "rueckfragen" in name)
            with open(os.path.join(tmp, "m_rueckfragen_LE-A.csv"), encoding="utf-8-sig") as f:
                le_lines = f.read().splitlines()
            with open(os.path.join(tmp, "m_rueckfragen_LE-B.csv"), encoding="utf-8-sig") as f:
                b_lines = f.read().splitlines()
        result = json.loads(text)
        self.assertTrue(result["notes"][0].startswith("4 Fälle"))
        self.assertNotIn("problems", result)
        self.assertNotIn('\\"a\\"', text)           # no case key
        # only the files per LE, not the one of all LEs
        self.assertEqual(names, ["m_rueckfragen_LE-A.csv", "m_rueckfragen_LE-B.csv"])
        self.assertEqual(len(le_lines), 2)
        self.assertTrue(le_lines[1].startswith(f"LE-A (LE-A-ID);{dm.P_TNM};"))
        self.assertEqual(len(b_lines), 1)               # the header only: LE-B has no Rückfrage


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


class Review20261125Tests(unittest.TestCase):
    """Fixes after the review of 25.11.2026 (synthetic data)"""

    def test_a_consent_starting_after_the_date_is_not_valid_yet_and_the_end_day_is(self):
        ref = date(2026, 11, 24)
        later = broad_consent(start="2027-01-01")
        self.assertFalse(dm.permits_research_use([{"scope": later}], ref))
        self.assertTrue(dm.permits_research_use([{"scope": broad_consent(start="2026-11-24")}], ref))
        ends = broad_consent(start="2025-01-01")
        ends["provision"]["provision"][0]["period"]["end"] = "2026-11-24"
        self.assertTrue(dm.permits_research_use([{"scope": ends}], ref))
        self.assertFalse(dm.permits_research_use([{"scope": ends}], date(2026, 11, 25)))
        result = cases(datenkranz("t1", consents=[later], submitted="2025-03-01"))
        self.assertEqual(get(result, "Einwilligung", "Broad Consent gültig zum Stichtag"), 0)

    def test_qs12_checks_the_dip_reason_too(self):
        rows = [row("a", datenkranz("t1", submitted="2026-07-01", gfk1="2026-06-01", mtb="2026-06-20",
                                    mv_consent_date="2026-05-01"), reason="technical-issues"),
                row("b", datenkranz("t2", submitted="2026-05-01", gfk1="2026-04-01", mtb="2026-04-20",
                                    mv_consent_date="2026-03-01"), reason="technical-issues")]
        found = problems(run(rows, cutoff=date(2026, 11, 1)), dm.P_JUSTIFICATION)
        self.assertEqual([(p["case"], p["detail"]) for p in found],
                         [(rows[0].case, "reasonResearchConsentMissing (DIP): technical-issues")])   # before 06/2026: none

    def test_gfk2_on_the_day_of_gfk1_is_counted_apart_from_the_statistics(self):
        result = cases(datenkranz("t1", gfk1="2025-01-10", mtb="2025-01-10"), datenkranz("t2", gfk1="2025-01-10", mtb="2025-02-20"),
                       datenkranz("t3", gfk1="2025-01-10", mtb="2025-02-09"))
        s = "Bearbeitungsdauer medizinisch (GFK1 bis GFK2)"
        self.assertEqual(get(result, s, "Dauer berechenbar"), 3)
        self.assertEqual(get(result, s, dm.SAME_DAY), 1)
        self.assertEqual(get(result, s, "Minimum (Tage)"), 30.0)
        self.assertEqual(get(result, s, "Median (Tage)"), 35.5)
        self.assertEqual(len(problems(result, dm.P_SHORT_MEDICAL)), 1)    # still a Rückfrage

    def test_the_follow_up_window_counts_back_from_the_reference_date(self):
        # 30.11. is within the 3 months before 28.02.; adding 3 months to it gives 28.02. and made it overdue
        self.assertTrue(dm.within([date(2025, 11, 30)], 3, date(2026, 2, 28)))
        self.assertFalse(dm.within([date(2025, 11, 28)], 3, date(2026, 2, 28)))     # exactly one interval before
        self.assertTrue(dm.within([date(2026, 2, 28)], 3, date(2026, 2, 28)))
        self.assertFalse(dm.within([date(2026, 3, 1)], 3, date(2026, 2, 28)))

    def test_a_case_is_dated_by_its_earliest_initial_submission_and_times_are_parsed(self):
        rows = [row("a", datenkranz("t2", submitted="2025-05-02"), "2025-05-02T10:00:00+02:00"),
                row("a", datenkranz("t1", submitted="2025-04-30"), "2025-04-30T23:30:00-02:00")]
        case = dm.build_cases(rows)[0][0]
        self.assertEqual(case.initial.content["metaData"]["tanC"], "t1")
        result = run(rows)
        self.assertEqual(result["time_series"]["periods"][0], "2025-04")
        self.assertEqual(series(result, "Fälle", CASES, "2025-04"), 1)
        # time zones: 23:30 -02:00 is 01:30 UTC of the next day, after 00:30 UTC
        early = dm.SubmissionRow("k", "2025-05-01T00:30:00+00:00", {}, "LE-A")
        late = dm.SubmissionRow("k", "2025-04-30T23:30:00-02:00", {}, "LE-A")
        self.assertLess(dm.submitted_order(early), dm.submitted_order(late))

    def test_an_empty_plan_is_no_documented_gfk2(self):
        content = datenkranz("t1", mtb=None)
        content["plan"] = {}
        result = cases(content, datenkranz("t2"))
        self.assertEqual(get(result, "GFK2 (MTB): Ergebnis (sequenzierte", "GFK2 dokumentiert (plan.carePlanOd)"), 1)


class Review20261126Tests(unittest.TestCase):
    """Fixes after the cross-model review of 26.11.2026 (synthetic data)"""

    def evaluated(self, *contents, ref=date(2026, 11, 1)):
        case = dm.build_cases([row("a", c) for c in contents])[0][0]
        return dm.evaluate_case(case, ref, 14)

    def test_an_empty_care_plan_is_no_gfk2_and_doesnt_displace_an_earlier_plan(self):
        empty = datenkranz("t1", mtb=None)
        empty["plan"] = {"carePlanOd": {}}
        result = cases(empty, datenkranz("t2"))
        self.assertEqual(get(result, "GFK2 (MTB): Ergebnis (sequenzierte", "GFK2 dokumentiert (plan.carePlanOd)"), 1)
        populated = datenkranz("t1", submitted="2025-03-01", therapies=[therapy_rec("r1")])
        later = datenkranz("t2", type_="followup", submitted="2025-04-01", mtb=None)
        later["plan"] = {"carePlanOd": {}}
        e = self.evaluated(populated, later)
        self.assertTrue(e.has_plan)
        self.assertEqual(len(e.therapies), 1)
        self.assertTrue(self.evaluated(datenkranz("t1", mtb="2025-02-20")).has_plan)     # a plan with only its date

    def test_month_dates_are_contacts_and_the_first_follow_up_is_the_earliest(self):
        e = self.evaluated(datenkranz("t1", mtb="2025-02-20", follow_ups=[follow_up("2026-10")]))
        self.assertEqual(e.contact_dates, [date(2026, 10, 15)])
        self.assertNotEqual(dm.follow_up_state(e, date(2026, 11, 1)), dm.FU_LOST)
        e = self.evaluated(datenkranz("t1", mtb="2025-02-20", follow_ups=[follow_up("2026-09-01"), follow_up("2025-03-10")]))
        self.assertEqual(e.first_fu_days, 18)
        self.assertEqual(e.fu_dates, sorted(e.fu_dates))

    def test_a_death_after_the_reference_date_doesnt_change_the_state_at_it(self):
        dead_later = follow_up("2026-12-01", vital="deceased", death="2026-11-20")
        self.assertFalse(self.evaluated(datenkranz("t1", follow_ups=[dead_later])).deceased)
        self.assertTrue(self.evaluated(datenkranz("t1", follow_ups=[dead_later]), ref=date(2026, 12, 15)).deceased)

    def test_a_permit_starting_after_the_date_doesnt_cancel_a_valid_one(self):
        ref = date(2026, 11, 1)
        valid, renewal = broad_consent(start="2024-06-01"), broad_consent(start="2027-01-01")
        self.assertTrue(dm.permits_research_use([{"scope": valid}, {"scope": renewal}], ref))
        self.assertTrue(dm.permits_research_use([{"scope": renewal}, {"scope": valid}], ref))
        self.assertFalse(dm.permits_research_use([{"scope": renewal}], ref))
        self.assertFalse(dm.permits_research_use([{"scope": valid}, {"scope": broad_consent(start="2024-06-01", kind="deny")}], ref))

    def test_without_a_submission_date_the_earliest_instant_dates_the_case(self):
        a, b = datenkranz("t1"), datenkranz("t2")
        for c in (a, b):
            del c["metaData"]["submission"]["date"]
        rows = [row("a", a, "2025-03-31T23:30:00-02:00"), row("a", b, "2025-04-01T00:30:00Z")]
        case = dm.build_cases(rows)[0][0]
        self.assertEqual(case.initial.content["metaData"]["tanC"], "t2")
        self.assertEqual(dm.submission_date(case.initial), date(2025, 4, 1))


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
            with contextlib.redirect_stderr(io.StringIO()):
                dm.main([path, "--output", out, "--reference-date", "2026-11-01"])
            with open(out + ".json", encoding="utf-8") as f:
                result = json.load(f)
            with open(out + ".csv", encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
            with open(out + "_zeitreihe.csv", encoding="utf-8-sig") as f:
                series_lines = f.read().splitlines()
            with open(out + "_rueckfragen.csv", encoding="utf-8-sig") as f:
                problem_lines = f.read().splitlines()
        self.assertEqual(set(result), {"title", "period", "reference_date", "cutoff", "entities", "labels", "rows",
                                       "notes", "time_series"})       # no case keys
        self.assertEqual(lines[0], "LE;Abschnitt;Kategorie;Einheit;Wert;Bezug;Basis;Anteil (%)")
        self.assertIn("Gesamt;Geschlecht;w;Anzahl;1;Fälle;1;100,0", lines)
        self.assertIn("LE-A (LE-A-ID);2025-03;Geschlecht;w;Anzahl;1;Fälle;1;100,0", series_lines)
        self.assertEqual(series_lines[0], "LE;Zeitraum;Abschnitt;Kategorie;Einheit;Wert;Bezug;Basis;Anteil (%)")
        self.assertTrue(problem_lines[1].startswith(f"LE-A (LE-A-ID);{dm.P_GFK2_AFTER_SUBMISSION};"))


def export_json(rows, failures=(), columns_with_reason=True):
    """An export-datenkranz result of SubmissionRows (keys site, patient = case, episodeOfCare), with an RD row"""
    columns = ["site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt", "content"]
    columns += ["reasonResearchConsentMissing"] if columns_with_reason else []
    data = [{"site": r.site, "useCase": "MTB", "patient": r.case, "episodeOfCare": "e1", "tan": f"t{i}",
             "submittedAt": r.submitted_at, "content": r.content,
             **({"reasonResearchConsentMissing": None} if columns_with_reason else {})} for i, r in enumerate(rows)]
    data.insert(1, {**data[0], "useCase": "RD", "patient": "rd"})
    return {"statistic": "export-datenkranz", "params": {"n": 12345},
            "tables": [{"name": "datenkranz", "columns": columns, "rows": data},
                       {"name": "failures", "columns": ["site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt", "error"],
                        "rows": list(failures)}],
            "notes": ["Größe ü", 1.5e3]}


class StreamingTests(unittest.TestCase):
    """The input read as a stream in two passes (InputFile, run_file): the same as read at once"""

    def rows(self):
        return ([row("a", datenkranz("t1", submitted="2025-03-01")),
                 row("b", datenkranz("t2", submitted="2025-03-02")),
                 row("a", datenkranz("t3", type_="followup", submitted="2025-05-01")),     # case a interleaved
                 row("c", datenkranz("t4", type_="followup", submitted="2025-04-01")),     # without initial
                 row("d", datenkranz("t5", type_="test", submitted="2025-04-01")),
                 row("e", {"metaData": "kaputt"}, "2025-04-02T10:00:00")]                   # unreadable
                + [row(f"n{i}", datenkranz(f"n{i}", submitted=f"2025-06-{10 + i}", mtb=None)) for i in range(8)])

    def write(self, tmp, data, indent=None):
        path = os.path.join(tmp, "in.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        return path

    def test_export_read_in_small_chunks_equals_json_load(self):
        failures = [{"site": "LE-A", "useCase": "MTB", "patient": "x", "episodeOfCare": "e1", "tan": "tx",
                     "submittedAt": "2025-03-01T10:00:00", "error": "conversion failed"},
                    {"site": "LE-A", "useCase": "RD", "patient": "y", "episodeOfCare": "e1", "tan": "ty",
                     "submittedAt": "2025-03-01T10:00:00", "error": "rd"}]
        data = export_json(self.rows(), failures)
        with tempfile.TemporaryDirectory() as tmp:
            for indent in (None, 2):
                path = self.write(tmp, data, indent)
                for chunk in (1, 7, 1 << 22):
                    with self.subTest(indent=indent, chunk=chunk), mock_chunk(chunk):
                        rows, failed = dm.load_submissions(path)
                        self.assertEqual([r.content for r in rows], [r.content for r in self.rows()])
                        self.assertEqual({r.case for r in rows}, {json.dumps([r.site, r.case, "e1"]) for r in self.rows()})
                        self.assertTrue(all(r.reason is None for r in rows))
                        self.assertEqual([(f.tan, f.error) for f in failed], [("tx", "conversion failed")])

    def test_run_file_equals_run_on_all_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            for data in (export_json(self.rows()), export_json(self.rows(), columns_with_reason=False),
                         [{"case": r.case, "site": r.site, "submittedAt": r.submitted_at, "content": r.content,
                           **({"reasonResearchConsentMissing": "abgelehnt"} if i == 0 else {})}
                          for i, r in enumerate(self.rows())]):
                path = self.write(tmp, data, 2)
                for kwargs in ({}, {"cutoff": date(2025, 4, 15)}, {"start": date(2025, 3, 2), "end": date(2025, 7, 1)}):
                    with self.subTest(kind=type(data).__name__, **kwargs), mock_chunk(5):
                        start, end, cutoff = kwargs.get("start"), kwargs.get("end"), kwargs.get("cutoff")
                        rows, failures = dm.load_submissions(path)
                        expected = dm.run(rows, failures, start, end, cutoff or REFERENCE, cutoff=cutoff)
                        actual = dm.run_file(dm.InputFile(path), start, end, cutoff or REFERENCE, cutoff=cutoff)
                        self.assertEqual(actual, expected)
                        self.assertEqual(get(actual, "Fälle", CASES), 9 if start else 2 if cutoff else 10)

    def test_broken_inputs_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = json.dumps(export_json(self.rows()))
            for content, message in ((text[:len(text) // 2], "invalid JSON"), (text + " {}", "extra data"),
                                     ('{"tables": [{"name": "other", "rows": []}]}', "no table 'datenkranz'"),
                                     ('{"rows": []}', "neither"), ('"text"', "neither"),
                                     ('{"tables": [{"rows": [], "name": "datenkranz"}]}', "before its name")):
                with self.subTest(message=message):
                    path = os.path.join(tmp, "in.json")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    with self.assertRaisesRegex(ValueError, message):
                        dm.run_file(dm.InputFile(path), None, None, REFERENCE)

    def test_input_changed_between_the_passes_is_refused(self):
        rows = self.rows()
        passes = {1: rows, 2: rows[:2]}
        with self.assertRaisesRegex(ValueError, "changed between the two passes"):
            dm.run_passes(lambda n: iter(passes[n]), lambda: [], None, None, REFERENCE)

    def test_progress_without_terminal_one_line_per_ten_percent(self):
        out = io.StringIO()
        progress = dm.Progress("Durchgang 1/2", 2_000_000_000, out)
        for done in range(0, 2_000_000_001, 100_000_000):
            progress.update(done)
        progress.finish()
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[-1].strip(), "Durchgang 1/2: 100 % von 2.0 GB")


class MergeReviewTests(unittest.TestCase):
    """Findings of the review of the merge with upstream 0.1.6 (streaming input)"""

    def test_run_reads_an_iterator_like_a_list(self):
        rows = [row("a", datenkranz("t1")), row("b", datenkranz("t2", submitted="2025-04-01"))]
        self.assertEqual(dm.run(iter(rows), iter([]), None, None, REFERENCE), dm.run(rows, [], None, None, REFERENCE))

    def test_a_complete_case_missing_in_the_second_pass_is_refused(self):
        rows = [row("a", datenkranz("t1")), row("b", datenkranz("t2"))]
        passes = {1: rows, 2: rows[:1]}
        with self.assertRaisesRegex(ValueError, "changed between the two passes"):
            dm.run_passes(lambda n: iter(passes[n]), lambda: [], None, None, REFERENCE)

    def test_a_number_cut_at_the_end_of_the_buffer(self):
        data = export_json([row("a", datenkranz("t1"))])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"count": 1.5, "flag": 12e1, ' + json.dumps(data)[1:])
            for chunk in range(1, 40):      # some chunk ends the buffer after "1." or "12e"
                with self.subTest(chunk=chunk), mock_chunk(chunk):
                    rows, _ = dm.load_submissions(path)
                    self.assertEqual(len(rows), 1)

    def test_columns_after_the_rows_keep_the_reason(self):
        data = export_json([row("a", datenkranz("t1"))])
        data["tables"][0]["rows"][0]["reasonResearchConsentMissing"] = "technical-issues"
        table = data["tables"][0]
        data["tables"][0] = {"name": table["name"], "rows": table["rows"], "columns": table["columns"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            rows, _ = dm.load_submissions(path)
        self.assertEqual(rows[0].reason, "technical-issues")

    def test_reason_column_known_from_rows_after_the_cutoff(self):
        entries = [{"case": "a", "site": "LE-A", "submittedAt": "2025-03-01T10:00:00", "content": datenkranz("t1")},
                   {"case": "b", "site": "LE-A", "submittedAt": "2025-06-01T10:00:00",
                    "content": datenkranz("t2", submitted="2025-06-01"), "reasonResearchConsentMissing": None}]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entries, f)
            cutoff = date(2025, 4, 15)
            rows, failures = dm.load_submissions(path)
            expected = dm.run(rows, failures, None, None, cutoff, cutoff=cutoff)
            actual = dm.run_file(dm.InputFile(path), None, None, cutoff, cutoff=cutoff)
        self.assertEqual(actual, expected)
        self.assertFalse(any("keine Spalte reasonResearchConsentMissing" in n for n in actual["notes"]))

    def test_a_failure_before_the_latest_converted_submission_is_not_stale(self):
        rows = [row("a", datenkranz("t1", submitted="2025-03-10"))]
        failures = [dm.Failure("LE-A", json.dumps(["LE-A", "a"]), "t0", "2025-03-01T00:00:00", "conversion failed")]
        result = run(rows, failures=failures)
        self.assertEqual(get(result, "Fälle", "davon Episode mit älterem Stand ausgewertet (eine spätere Einreichung nicht konvertierbar)"), 0)
        self.assertFalse(problems(result, dm.P_NOT_CONVERTED)[0]["detail"].startswith("Episode mit älterem Stand"))

    def test_therapy_started_after_the_reference_date_is_not_started_yet(self):
        later = {"reference": "r1", "therapyStartDate": "2026-12-01"}
        result = cases(datenkranz("t1", therapies=[therapy_rec("r1")], follow_ups=[follow_up(therapies=[later])]))
        s = dm.FU_SECTION
        self.assertEqual(get(result, s, "Kategorie 1: empfohlene Therapie begonnen (Intervall 3 Monate)"), 0)
        self.assertEqual(get(result, s, "Kategorie 2: Therapie- oder Studienempfehlung, nicht begonnen (Intervall 3 Monate)"), 1)

    def test_death_in_a_month_after_the_reference_date(self):
        result = cases(datenkranz("t1", follow_ups=[follow_up(fu_date="2026-12", vital="deceased", death="2026-12")]))
        self.assertEqual(get(result, dm.FU_SECTION, f"Stand am Stichtag: {dm.FU_DECEASED}"), 0)

    def test_a_later_plan_without_care_plan_keeps_the_documented_gfk2(self):
        later = datenkranz("t2", type_="followup", submitted="2025-05-01", therapies=[therapy_rec("r1")])
        del later["plan"]["carePlanOd"]
        result = run([row("a", datenkranz("t1")), row("a", later)])
        self.assertEqual(get(result, "GFK2 (MTB): Ergebnis", "GFK2 dokumentiert (plan.carePlanOd)"), 1)


@contextlib.contextmanager
def mock_chunk(size):
    previous = dm.JsonStream.CHUNK
    dm.JsonStream.CHUNK = size
    try:
        yield
    finally:
        dm.JsonStream.CHUNK = previous


if __name__ == "__main__":
    unittest.main()
