#!/usr/bin/env python3
"""
A synthetic export of oncology Datenkranz submissions (format of export-datenkranz, useCase MTB) for
developing dashboards on the output of dnpm_evaluation_matrix.py. All data are random: LEs are named
LE-A, LE-B, ..., patients p00001, ...; no value stems from real cases. The fields are those the DIP
mapping (bfarm-schema-dto-model oncology 1.0.7) writes; a few cases carry deliberate errors (impossible
dates, unconvertible submissions, episodes without initial submission) so the Rückfragen aren't empty.

Example:
  python3 make_synthetic.py --output synthetic-export.json --cases 1500 --les 6 --start 2025-09 --months 14
  python3 dnpm_evaluation_matrix.py synthetic-export.json --reference-date 2026-11-01 --output onko-matrix
"""

import argparse
import json
import random
from datetime import date, datetime, timedelta

ICD10 = "http://fhir.de/CodeSystem/bfarm/icd-10-gm"
ICDO3 = "urn:oid:2.16.840.1.113883.6.43.1"
ATC = "http://fhir.de/CodeSystem/bfarm/atc"
MDAT_RESEARCH_USE = "2.16.840.1.113883.3.1937.777.24.5.3.8"

# (ICD-10-GM main diagnosis, ICD-O-3 topography, ICD-O-3 morphology, weight)
ENTITIES = (
    ("C34.1", "C34.1", "8140/3", 15), ("C18.7", "C18.7", "8140/3", 9), ("C25.0", "C25.0", "8500/3", 8),
    ("C50.9", "C50.9", "8500/3", 7), ("C49.2", "C49.2", "8890/3", 9), ("C80.0", "C80.9", "8010/3", 6),
    ("C92.0", "C42.1", "9861/3", 5), ("D46.9", "C42.1", "9989/1", 2), ("C56", "C56.9", "8441/3", 5),
    ("C71.9", "C71.9", "9440/3", 5), ("C22.1", "C22.1", "8160/3", 5), ("C16.9", "C16.9", "8140/3", 4),
    ("C43.5", "C44.5", "8720/3", 3), ("C61", "C61.9", "8140/3", 3), ("C64", "C64.9", "8312/3", 3),
    ("C10.9", "C10.9", "8070/3", 3), ("C73", "C73.9", "8260/3", 1), ("C17.0", "C17.0", "8240/3", 1),
    ("D05.1", "C50.9", "8500/2", 1),
)
SUBSTANCES = ("L01EA01", "L01EB04", "L01FF02", "L01XK01", "L01EC01", "L01EX07", "L02BB04", "L01EH01")


def iso(d):
    return d.isoformat()


def add_months(d, months):
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, min(d.day, 28))


def broad_consent(start, kind="permit"):
    return {"resourceType": "Consent", "status": "active", "provision": {
        "type": "deny", "period": {"start": iso(start)},
        "provision": [{"type": kind, "period": {"start": iso(start), "end": iso(add_months(start, 360))},
                       "code": [{"coding": [{"system": "urn:oid:2.16.840.1.113883.3.1937.777.24.5.3",
                                             "code": MDAT_RESEARCH_USE}]}]}]}}


def make_case(rnd, i, le, initial_date):
    """The fixed facts of a synthetic case, and its submissions as (type, date, content) tuples"""
    main, topo, morph, _ = rnd.choices(ENTITIES, weights=[e[3] for e in ENTITIES])[0]
    paediatric = rnd.random() < 0.06
    birth = date(initial_date.year - (rnd.randint(1, 17) if paediatric else rnd.randint(22, 86)), rnd.randint(1, 12), 1)
    mtb = initial_date - timedelta(days=rnd.randint(2, 40))
    gfk1 = mtb - timedelta(days=max(8, int(rnd.gauss(55, 20))))
    consent_date = gfk1 - timedelta(days=rnd.randint(0, 20))
    included = rnd.random() > 0.02
    library = "none" if not included or rnd.random() < 0.04 else rnd.choices(["wgs", "wes", "panel", "wgs_lr"], [70, 22, 6, 2])[0]
    sequenced = library != "none"
    has_plan = sequenced and rnd.random() < 0.93
    if rnd.random() < 0.01:                    # deliberate error: MTB reported after the submission date
        mtb = initial_date + timedelta(days=5)

    n_recs = rnd.choices([0, 1, 2, 3, 4], [28, 30, 22, 12, 8])[0] if has_plan else 0
    recs = [{
        "identifier": f"rec-{i}-{k}",
        "type": rnd.choices(["inLabel", "offLabel", "compassionateUse", "unknown"], [25, 65, 5, 5])[0],
        "substances": [{"code": {"code": rnd.choice(SUBSTANCES), "system": ATC, "version": "2025"}}],
        "evidenceLevel": rnd.choices(["m1A", "m1B", "m1C", "m2A", "m2B", "m2C", "m3", "m4", "undefined"],
                                     [8, 10, 6, 14, 18, 12, 20, 8, 4])[0],
        "evidenceLevelDetails": rnd.sample(["is", "iv", "Z", "R"], rnd.choice([0, 0, 1, 2])),
        "priority": k + 1,
        **({"variants": [f"snv-{i}-0"]} if rnd.random() < 0.85 else {}),
        "therapeuticStrategy": rnd.choices(["ZS", "IM", "CZ", "IZ", "CH", "HO", "SO"], [55, 15, 8, 8, 6, 4, 4])[0],
    } for k in range(n_recs)]
    studies = [{"identifier": f"study-{i}", "register": rnd.choice(["NCT", "NCT", "DRKS", "Eudra-CT/CTIS", "yesButStudyIsUnknown"]),
                "name": "Synthetische Studie", "id": f"SYN{i:05d}", "evidenceLevel": rnd.choice(["m1B", "m2A", "m2B", "m3"]),
                "evidenceLevelDetails": [], "priority": n_recs + 1}] if has_plan and rnd.random() < 0.15 else []
    n_snv = min(int(rnd.expovariate(1 / 5)), 40) if sequenced else 0
    molecular = {
        "smallVariants": [{
            "identifier": f"snv-{i}-{k}", "genomicSource": "somatic", "chromosome": str(rnd.randint(1, 22)),
            "gene": {"code": f"HGNC:{rnd.randint(1, 50000)}", "display": "GEN", "system": "https://www.genenames.org/"},
            "localization": "coding", "startPosition": 1000 + k, "endPosition": 1000 + k, "ref": "C", "alt": "T",
            "dnaChange": "c.100C>T", **({"proteinChange": "p.R34*"} if rnd.random() < 0.9 else {}),
            "transcriptId": {"code": "ENST00000000000", "system": "https://www.ensembl.org"},
        } for k in range(n_snv)],
        "copyNumberVariants": [{
            "identifier": f"cnv-{i}-{k}", "genomicSource": "somatic", "localization": "coding",
            "cnvType": rnd.choice(["loss", "lowLevelGain", "highLevelGain"]), "chromosome": str(rnd.randint(1, 22)),
            "gene": {"code": f"HGNC:{rnd.randint(1, 50000)}", "display": "GEN"},
            **({"startPosition": 5000, "endPosition": 9000} if rnd.random() < 0.7 else {}),
        } for k in range(min(int(rnd.expovariate(1 / 2)), 15))],
        "complexBiomarkers": [{"identifier": f"bm-{i}"}],
    } if sequenced and rnd.random() < 0.97 else None
    if molecular and rnd.random() < 0.85:
        molecular["complexBiomarkers"][0]["tmb"] = round(rnd.lognormvariate(1.2, 1.0), 1)
    if molecular and rnd.random() < 0.4:       # the DIP copies the HRD interpretation into all three fields
        hrd = rnd.random() < 0.15
        molecular["complexBiomarkers"][0].update(hrdHigh=hrd, lstHigh=hrd, taiHigh=hrd)
    for key in ("smallVariants", "copyNumberVariants"):
        if molecular and not molecular[key]:
            del molecular[key]

    consents = [{"schemaVersion": "2025.0.3", "presentationDate": iso(consent_date),
                 "scope": broad_consent(consent_date, "permit" if rnd.random() < 0.97 else "deny")}] if rnd.random() < 0.72 else []

    def content(kind, submitted, follow_up=None, ecog=None):
        c = {
            "metaData": {
                "submission": {"date": iso(submitted), "type": kind, "submitterId": f"SYN-{le}", "clinicalDataNodeId": "SYN-KDK",
                               **({"genomicDataCenterId": "SYN-GRZ"} if sequenced else {}), "diseaseType": "oncological"},
                "coverageType": coverage,
                "mvConsent": {"presentationDate": iso(consent_date), "version": "2025.0.1",
                              "scope": [{"date": iso(consent_date), "domain": d, "type": "permit"}
                                        for d in ("mvSequencing", "reIdentification", "caseIdentification")]},
                "researchConsents": consents, "tanC": f"{le}-{i:05d}-{kind}-{iso(submitted)}",
                "gender": gender, "birthDate": f"{birth.year}-{birth.month:02d}", "addressAGS": "",
                "decisionToInclude": included, "molecularBoardDecisionDate": iso(gfk1),
            },
            "case": {"diagnosisOd": {
                "mainDiagnosis": {"code": main, "system": ICD10, "version": "2025", "date": iso(diagnosis_date)},
                "ecogPerformanceStatusScore": ecog or ecog_initial, "germlineDiagnosisConfirmed": germline,
                **({"germlineDiagnoses": [{"code": "Z80.3", "system": ICD10, "version": "2025"}]} if germline else {}),
                "histology": {"code": morph, "system": ICDO3, "version": "3.2"},
                "topography": {"code": topo, "system": ICDO3, "version": "3.2"},
                **({"grading": grading} if grading else {}),
                **({"tnmClassifications": tnm} if tnm else {}),
                "libraryType": library,
            }, **({"priorProcedures": procedures} if procedures else {})},
        }
        if molecular:
            c["molecular"] = molecular
        if has_plan:
            c["plan"] = {"carePlanOd": {
                "molecularBoardDecisionDate": iso(mtb), "studyRecommended": bool(studies),
                "counsellingRecommended": counselling, "reEvaluationRecommended": reevaluation,
                "interventionRecommended": False, **({"otherRecommendations": other} if other else {})}}
            if recs:
                c["plan"]["recommendedSystemicTherapies"] = recs
            if studies:
                c["plan"]["recommendedStudies"] = studies
        if follow_up:
            c["followUp"] = {"followUpOds": [follow_up]}
        return c

    gender = rnd.choices(["male", "female", "other"], [52, 47, 1])[0]
    coverage = rnd.choices(["GKV", "PKV", "BG", "SEL"], [86, 11, 1, 2])[0]
    diagnosis_date = gfk1 - timedelta(days=int(rnd.expovariate(1 / 500)) + 10)
    ecog_initial = rnd.choices(["0", "1", "2", "3", "unknown", "notApplicable"], [30, 40, 12, 3, 13, 2])[0]
    germline = rnd.random() < 0.05
    grading = rnd.choices(["1", "2", "3", "4", "X", "U", None], [5, 20, 30, 5, 5, 5, 30])[0]
    m = rnd.choice(["cM0", "cM1", "cM1a", "pM1"])
    tnm = [{"code": str(900000 + k), "system": "http://snomed.info/sct", "version": "2025",
            "display": f"{t} (UICC)"} for k, t in enumerate(("cT2", "cN1", m))] if rnd.random() < 0.6 else None
    procedures = [{"treatmentType": rnd.choice("OANIS"), "intention": rnd.choice("KPPPSX"),
                   "therapyStartDate": iso(diagnosis_date + timedelta(days=30 + 90 * k)),
                   **({"terminationReasonOBDS": rnd.choice("EPAPS")} if rnd.random() < 0.7 else {}),
                   **({"therapyResponse": rnd.choice(["PD", "PD", "SD", "PR"])} if rnd.random() < 0.5 else {})}
                  for k in range(rnd.choices([0, 1, 2, 3, 4], [20, 30, 25, 15, 10])[0])]
    counselling, reevaluation = rnd.random() < 0.12, rnd.random() < 0.05
    other = rnd.sample(["WW", "OP", "ST", "KW", "SO"], rnd.choice([0, 0, 0, 1]))

    submissions = [("initial", initial_date, content("initial", initial_date))]
    if rnd.random() < 0.08:
        d = initial_date + timedelta(days=rnd.randint(5, 60))
        submissions.append(("correction", d, content("correction", d)))
    if rnd.random() < 0.03:
        d = initial_date + timedelta(days=rnd.randint(5, 60))
        submissions.append(("addition", d, content("addition", d)))

    # follow-ups every 3 to 6 months while alive, reported a few weeks later
    started, therapies, fu_date, deceased = [], [], mtb, False
    while not deceased:
        fu_date = fu_date + timedelta(days=rnd.randint(90, 180))
        if rnd.random() < 0.25:
            break   # no (further) follow-up documented
        for rec in recs:
            if rec["identifier"] not in started and rnd.random() < 0.3:
                started.append(rec["identifier"])
                start = mtb + timedelta(days=rnd.randint(7, 70))
                therapies.append({"reference": rec["identifier"], "therapyStartDate": iso(start),
                                  **({"therapyEndDate": iso(start + timedelta(days=120)),
                                      "terminationReasonOBDS": rnd.choice("EPPAVS")} if rnd.random() < 0.4 else {}),
                                  **({"therapyResponse": rnd.choice(["PD", "SD", "PR", "CR"]),
                                      "therapyResponseDate": iso(start + timedelta(days=60))} if rnd.random() < 0.6 else {}),
                                  "substances": rec["substances"]})
        if rnd.random() < 0.1 and not therapies:
            therapies.append({"reference": f"own-{i}", "therapyStartDate": iso(fu_date - timedelta(days=30))})
        deceased = rnd.random() < 0.15
        fu = {"followUpDate": iso(fu_date), "metachroneDiagnoses": rnd.random() < 0.02,
              "ecogPerformanceStatusScore": rnd.choice(["0", "1", "2", "3", "unknown"]),
              "vitalStatus": "deceased" if deceased else "living",
              **({"deathDate": iso(fu_date - timedelta(days=10))} if deceased and rnd.random() < 0.9 else {}),
              **({"therapies": list(therapies)} if therapies else {})}
        d = fu_date + timedelta(days=rnd.randint(3, 40))
        submissions.append(("followup", d, content("followup", d, fu, fu["ecogPerformanceStatusScore"])))
    return submissions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="synthetic-export.json")
    parser.add_argument("--cases", type=int, default=1500)
    parser.add_argument("--les", type=int, default=6)
    parser.add_argument("--start", default="2025-09", help="first month of initial submissions (YYYY-MM)")
    parser.add_argument("--months", type=int, default=14)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    rnd = random.Random(args.seed)
    first = date.fromisoformat(args.start + "-01")
    end = add_months(first, args.months)          # the export date: nothing submitted after it
    les = [f"LE-{chr(ord('A') + k)}" for k in range(args.les)]
    weights = [rnd.uniform(0.5, 2.0) for _ in les]

    def row(le, patient, episode, tan, submitted, content):
        return {"site": le, "useCase": "MTB", "patient": patient, "episodeOfCare": episode, "tan": tan,
                "submittedAt": datetime.combine(submitted, datetime.min.time()).isoformat() + "Z",
                "content": content, "reasonResearchConsentMissing": None}

    rows, failures = [], []
    for i in range(args.cases):
        le = rnd.choices(les, weights)[0]
        # a ramp-up: more cases in later months
        initial = first + timedelta(days=int((end - first).days * rnd.random() ** 0.8))
        state = random.Random(rnd.random())
        submissions = make_case(state, i, le, initial)
        patient, episode = f"p{i:05d}", f"e{i:05d}-1"
        reason = None
        for kind, submitted, content in submissions:
            if submitted >= end:
                continue
            r = row(le, patient, episode, content["metaData"]["tanC"], submitted, content)
            if not content["metaData"]["researchConsents"]:
                reason = reason or state.choices(["patient-refusal", "consent-not-returned", "patient-inability", None],
                                                 [45, 30, 10, 15])[0]
                r["reasonResearchConsentMissing"] = reason
            if rnd.random() < 0.004:      # deliberate error: the export couldn't convert it
                failures.append({k: r[k] for k in ("site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt")}
                                | {"error": "conversion failed: synthetic error"})
                continue
            rows.append(r)
        if rnd.random() < 0.01:          # a test submission
            content = json.loads(json.dumps(submissions[0][2]))
            content["metaData"]["submission"]["type"] = "test"
            rows.append(row(le, f"t{i:05d}", "test", f"test-{i}", initial, content))
    # deliberate error: an episode without initial submission
    orphan = [r for r in rows if r["content"]["metaData"]["submission"]["type"] == "initial"][:3]
    rows = [r for r in rows if r not in orphan]

    export = {
        "statistic": "export-datenkranz",
        "notes": ["Synthetische Daten aus make_synthetic.py, keine echten Fälle."],
        "tables": [
            {"name": "datenkranz", "columns": ["site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt", "content",
                                               "reasonResearchConsentMissing"], "rows": rows},
            {"name": "failures", "columns": ["site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt", "error"],
             "rows": failures},
        ],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False)
    print(f"{len(rows)} submissions of {args.cases} synthetic cases, {len(failures)} failures: {args.output}")


if __name__ == "__main__":
    main()
