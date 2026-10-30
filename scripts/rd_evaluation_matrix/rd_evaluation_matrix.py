#!/usr/bin/env python3
"""
RD evaluation matrix ("Auswertematrix Geltungszeitraum 1") from BfArM Datenkranz submissions.

Specification: docs/rd-evaluation-matrix.txt. Standard library only, so it can be shared with the
other clinical data nodes (KDKs).

Input, either
  - the result JSON of the evaluation module's "export-datenkranz" (table "datenkranz"), cases
    keyed by (site, patient, episodeOfCare), or
  - a JSON list of {"case": <any case key>, "submittedAt": <ISO datetime>, "content": <Datenkranz>},
    one entry per submission, for KDKs with their own export.

Output: a JSON file with the value of every template cell (plus extras the template has no cells
for, and notes), and, with --template, the filled template.

Example:
  python3 rd_evaluation_matrix.py export-datenkranz_2026-10-30_10-00-00.json \\
    --orphanet en_product7.xml --from 2024-07-01 --until 2026-01-01 \\
    --template ../../docs/Update_Auswertematrix_GZ1.xlsx --output matrix
"""

import argparse
import json
import re
import statistics
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape


# ---------------------------------------------------------------------------------------------
# Labels and mappings, see docs/rd-evaluation-matrix.txt
# ---------------------------------------------------------------------------------------------

DIAGNOSED, UNCLEAR, UNDIAGNOSED = "Diagnose", "Unklar", "Undiagnostiziert"
STATUSES = (DIAGNOSED, UNCLEAR, UNDIAGNOSED)

ASSESSMENT_STATUS = {
    "confirmedGeneticDiagnosis":           DIAGNOSED,
    "partialGeneticDiagnosis":             UNCLEAR,
    "suspectedGeneticDiagnosis":           UNCLEAR,
    "furtherGeneticDiagnosticRecommended": UNCLEAR,
    "noGeneticDiagnosis":                  UNDIAGNOSED,
}
PARTIAL = "partialGeneticDiagnosis"

NEONATES, CHILDREN, ADOLESCENTS, ADULTS, SENIORS = (
    "Neonaten (bis 37 Tage)", "Kinder (0 -12 Jahre)", "Jugendliche (13-17 Jahre)",
    "Erwachsene (18-64 Jahre)", "Senioren (65+ Jahre)",
)
AGE_GROUPS = (NEONATES, CHILDREN, ADOLESCENTS, ADULTS, SENIORS)

EXTENTS = {"singleGenome": "Single", "duoGenome": "Duo", "trioGenome": "Trio"}

OTHER_TESTS = "Array, Einzelgentestung, Chromosomenanalyse, andere"
NO_TEST = "keine"
PRIOR_TYPES = ("Genom (SR)", "Genom (LR)", "Exom", "Panel", OTHER_TESTS, NO_TEST)  # left, rows in template order
PRIOR_TYPE = {
    "genomeShortRead": "Genom (SR)", "genomeLongRead": "Genom (LR)", "exome": "Exom", "panel": "Panel",
    "array": OTHER_TESTS, "single": OTHER_TESTS, "karyotyping": OTHER_TESTS, "other": OTHER_TESTS,
    "none": NO_TEST,
}
# right cross tables: the template orders the types differently
PRIOR_TYPES_BY_YEAR = (OTHER_TESTS, "Panel", "Exom", "Genom (SR)", "Genom (LR)", NO_TEST)
YEAR_BUCKETS = ("2025/2024", "2023/2022", "2021/2020", "2019/2018", "2017 und älter", "unbekannt")

RECOMMENDATIONS = (
    ("studyRecommended",        "1) Klinische Studie"),
    ("counsellingRecommended",  "2) Humangenetische Beratung"),
    ("reEvaluationRecommended", "3) Re-Evaluation"),
    ("therapyRecommended",      "4) Therapieempfehlung"),
)

# OPEN: assignment of the strategies, to be confirmed by the stakeholders
THERAPY_KIND = {
    "systemicMedication":   "medikamentös",
    "targetedMedication":   "medikamentös",
    "preventionMedication": "medikamentös",
    "genetic":              "interventionell",
    "prophylactic":         "nicht medikamentös",
    "earlyDetection":       "nicht medikamentös",
    "nutrition":            "nicht medikamentös",
}
NOT_ASSIGNABLE = "nicht zuordenbar"
THERAPY_ROWS = (  # (category, kind) per template row 68-72
    ("symptomatic", "nicht medikamentös"),
    ("symptomatic", "medikamentös"),
    ("symptomatic", "interventionell"),
    ("causal",      "medikamentös"),
    ("causal",      "interventionell"),
)
THERAPY_CATEGORY = {"symptomatic": "Symptomatisch", "causal": "Kausal"}

MANAGEMENT = (
    ("localCrd",                      "1) Eigenes ZSE"),
    ("otherCrd",                      "2) Anderes ZSE"),
    ("diseaseSpecificAmbulatoryCare", "3) Indikatorspezifische Ambulanz"),
    ("universityAmbulatoryCare",      "4) Andere Hochschulambulanz"),
    ("otherAmbulatoryCare",           "5) Andere Ambulanz"),
    ("gp",                            "6) Hausarzt"),
    ("specialist",                    "7) Niedergelassener Facharzt"),
)
NO_MANAGEMENT = "8) NA"

ORPHANET = "https://www.orpha.net"
ICD10GM = "http://fhir.de/CodeSystem/bfarm/icd-10-gm"

NEUROMUSCULAR = "Muskuläre / neuromuskul. Erkrankungen"
UNKNOWN_GROUP = "unbekannt"
DISEASE_GROUPS = (
    NEUROMUSCULAR,
    "Neurologische Erkrankungen, Entwicklungsstörungen",
    "Hereditäre Stoffwechselerkrankungen",
    "Endokrinologische Erkrankungen",
    "Hämatologische Erkrankungen",
    "Immunologische Erkrankungen, Autoimmun-/ rheumatologische Erkrankungen",
    "Kardiologische Erkrankungen",
    "Pulmonale Erkrankungen",
    "Gastro-intestinale Erkrankungen",
    "Nephrologische Erkrankungen",
    "Dermatologische Erkrankungen",
    "Ophthalmologische Erkrankungen",
    "Kraniofaziale Anomalien und Ohren-, Nasen-, Halsstörungen",
    "Knochenkrankheiten, Bindegewebe- und muskuloskelettale Krankheiten",
    "Seltene Tumorerkrankungen",
    "Kongenitale Fehlbildungen und seltene geistige Beeinträchtigungen",
    "Urogenitale Krankheiten",
    UNKNOWN_GROUP,
)

# Orphanet classification heads of the linearisation (Orphadata product 7) -> template group.
# Heads not listed (genetic, infectious, toxic, transplant-related) count as unbekannt.
HEAD_GROUP = {
    "98006":  "Neurologische Erkrankungen, Entwicklungsstörungen",   # Rare neurologic disease
    "68367":  "Hereditäre Stoffwechselerkrankungen",                 # Rare inborn error of metabolism
    "97978":  "Endokrinologische Erkrankungen",                      # Rare endocrine disease
    "97992":  "Hämatologische Erkrankungen",                         # Rare hematologic disease
    "98004":  "Immunologische Erkrankungen, Autoimmun-/ rheumatologische Erkrankungen",  # Rare immune disease
    "98023":  "Immunologische Erkrankungen, Autoimmun-/ rheumatologische Erkrankungen",  # Rare systemic or rheumatologic disease
    "98050":  "Immunologische Erkrankungen, Autoimmun-/ rheumatologische Erkrankungen",  # Rare allergic disease
    "97929":  "Kardiologische Erkrankungen",                         # Rare cardiac disease
    "98028":  "Kardiologische Erkrankungen",                         # Rare circulatory system disease
    "97965":  "Kardiologische Erkrankungen",                         # Rare surgical cardiac disease
    "97955":  "Pulmonale Erkrankungen",                              # Rare respiratory disease
    "97962":  "Pulmonale Erkrankungen",                              # Rare surgical thoracic disease
    "97935":  "Gastro-intestinale Erkrankungen",                     # Rare gastroenterologic disease
    "57146":  "Gastro-intestinale Erkrankungen",                     # Rare hepatic disease
    "165711": "Gastro-intestinale Erkrankungen",                     # Rare abdominal surgical disease
    "93626":  "Nephrologische Erkrankungen",                         # Rare renal disease
    "89826":  "Dermatologische Erkrankungen",                        # Rare skin disease
    "97966":  "Ophthalmologische Erkrankungen",                      # Rare ophthalmic disorder
    "98036":  "Kraniofaziale Anomalien und Ohren-, Nasen-, Halsstörungen",  # Rare otorhinolaryngologic disease
    "68329":  "Kraniofaziale Anomalien und Ohren-, Nasen-, Halsstörungen",  # Rare maxillo-facial surgical disease
    "98026":  "Kraniofaziale Anomalien und Ohren-, Nasen-, Halsstörungen",  # Rare odontologic disease
    "93419":  "Knochenkrankheiten, Bindegewebe- und muskuloskelettale Krankheiten",  # Rare bone disease
    "250908": "Seltene Tumorerkrankungen",                           # Rare neoplastic disease
    "93890":  "Kongenitale Fehlbildungen und seltene geistige Beeinträchtigungen",  # Rare developmental defect during embryogenesis
    "730393": "Kongenitale Fehlbildungen und seltene geistige Beeinträchtigungen",  # Genetic isolated posterior fossa cystic malformation
    "101433": "Urogenitale Krankheiten",                             # Rare urogenital disease
    "96344":  "Urogenitale Krankheiten",                             # Rare gynecologic or obstetric disease
    "98047":  "Urogenitale Krankheiten",                             # Rare infertility
}
UNASSIGNED_HEADS = {"98053", "68416", "108999", "506207", "565779"}

# Neuromuscular diseases have no Orphanet head of their own: assigned by ICD-10-GM
# (G12 SMA, G60-G64 neuropathies, G70-G73 myoneural junction and muscle diseases)
NEUROMUSCULAR_ICD10 = re.compile(r"^(G12|G6[0-4]|G7[0-3])")

# Research provisions of the broad consent, see Consent.permitsResearchUse in service-base
RESEARCH_PROVISIONS = (
    "2.16.840.1.113883.3.1937.777.24.5.3.7",  # MDAT_STORE_AND_PROCESS
    "2.16.840.1.113883.3.1937.777.24.5.3.8",  # MDAT_RESEARCH_USE
    "2.16.840.1.113883.3.1937.777.24.5.3.1",  # PATDAT_STORE_AND_USE
)


# ---------------------------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------------------------

@dataclass
class SubmissionRow:
    case: str
    submitted_at: str
    content: dict


def load_submissions(path):
    """The submissions of the input file, and the number of failures of the export"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "tables" in data:
        tables = {t["name"]: t for t in data["tables"]}
        if "datenkranz" not in tables:
            raise ValueError(f"{path}: no table 'datenkranz', not a result of export-datenkranz")
        rows = [
            SubmissionRow(
                json.dumps([r.get("site"), r.get("patient"), r.get("episodeOfCare")]),
                r.get("submittedAt") or "",
                r["content"],
            )
            for r in tables["datenkranz"]["rows"]
            if r.get("useCase") in (None, "RD")
        ]
        return rows, len(tables.get("failures", {}).get("rows", []))

    if isinstance(data, list):
        return [SubmissionRow(json.dumps(e["case"]), e.get("submittedAt") or "", e["content"]) for e in data], 0

    raise ValueError(f"{path}: neither a result of export-datenkranz nor a list of submissions")


def load_linearisation(path):
    """Orpha code -> Orpha code of its classification head, from Orphadata product 7 (en_product7.xml)"""
    heads = {}
    for _, element in ET.iterparse(path):
        if element.tag != "Disorder":
            continue
        code = element.findtext("OrphaCode")
        associations = element.findall("./DisorderDisorderAssociationList/DisorderDisorderAssociation")
        # the entity's own association has the head as (expanded) TargetDisorder; the heads
        # themselves list their children as RootDisorder instead
        targets = [a.find("TargetDisorder") for a in associations]
        head = next((t.findtext("OrphaCode") for t in targets if t is not None and t.findtext("OrphaCode")), None)
        if code:
            heads[code] = head or code
        element.clear()
    return heads


# ---------------------------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------------------------

def parse_date(s):
    return date.fromisoformat(s[:10]) if s else None


def submission_type(content):
    return content["metaData"]["submission"]["type"]


def submission_date(content):
    return parse_date(content["metaData"]["submission"]["date"])


@dataclass
class Case:
    key: str
    submissions: list  # non-test, ordered by submittedAt

    @property
    def latest(self):
        return self.submissions[-1]

    @property
    def initial(self):
        return next(s for s in self.submissions if submission_type(s) == "initial")


def build_cases(rows, start=None, end=None):
    """
    The cases (episodes with an initial submission, initial submitted in [start, end)), and the
    number of tests left out and of episodes without initial
    """
    by_key = {}
    tests = 0
    for row in rows:
        content = row.content
        if content.get("metaData", {}).get("submission", {}).get("diseaseType") != "rare":
            continue
        if submission_type(content) == "test":
            tests += 1
            continue
        by_key.setdefault(row.case, []).append(row)

    cases, without_initial = [], 0
    for key, subs in by_key.items():
        # stable sort: equal times keep the order of the input
        subs = [r.content for r in sorted(subs, key=lambda r: r.submitted_at)]
        case = Case(key, subs)
        if not any(submission_type(s) == "initial" for s in subs):
            without_initial += 1
            continue
        initial_date = submission_date(case.initial)
        if (start and initial_date < start) or (end and initial_date >= end):
            continue
        cases.append(case)
    return cases, tests, without_initial


def age(birth_month, at):
    """(full years, days) at a date, the birth date given by month taken as its first day"""
    born = date(int(birth_month[:4]), int(birth_month[5:7]), 1)
    years = at.year - born.year - ((at.month, at.day) < (born.month, born.day))
    return years, (at - born).days


def age_group(years, days):
    if days <= 37:
        return NEONATES
    if years <= 12:
        return CHILDREN
    if years <= 17:
        return ADOLESCENTS
    if years <= 64:
        return ADULTS
    return SENIORS


def minus_years(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


def permits_research_use(research_consents, reference_date):
    """As Consent.permitsResearchUse of service-base, on the FHIR Consents in researchConsents[].scope"""

    def has_code(provision, code):
        return any(c.get("code") == code for cc in provision.get("code") or [] for c in cc.get("coding") or [])

    def find(consent, code):
        top = consent.get("provision") or {}
        if has_code(top, code):
            return top
        return next((p for p in top.get("provision") or [] if has_code(p, code)), None)

    def permitted(provision):
        period = provision.get("period") or {}
        start, end = parse_date(period.get("start")), parse_date(period.get("end"))
        return (
            provision.get("type") == "permit"
            and start is not None and start > minus_years(reference_date, 5)
            and (end is None or end > reference_date)
        )

    decisions = {}
    for rc in research_consents:
        consent = rc.get("scope")
        if not isinstance(consent, dict):
            continue
        for code in RESEARCH_PROVISIONS:
            if decisions.get(code) is False:  # a denial wins
                continue
            provision = find(consent, code)
            if provision is None:
                decisions.pop(code, None)
            else:
                decisions[code] = permitted(provision)
    return any(decisions.values())


@dataclass
class Evaluated:
    """The facts of a case the matrix counts"""
    status: str
    partial: bool
    sex: str
    age_group: str
    adult: bool
    insurance: str
    no_indication: bool
    extent: str
    library_type: str
    research_consent: bool
    medical_days: object        # int or None
    documentary_days: object    # int or None
    prior_type: str
    prior_year: str
    prior_after_2025: bool
    recommendations: set
    therapies: set              # (category, kind)
    management: set
    orpha_code: object          # str or None
    icd10_codes: list
    no_matching_code: bool


def year_bucket(d):
    if d is None:
        return "unbekannt"
    if d.year >= 2024:
        return "2025/2024"
    if d.year >= 2022:
        return "2023/2022"
    if d.year >= 2020:
        return "2021/2020"
    if d.year >= 2018:
        return "2019/2018"
    return "2017 und älter"


def evaluate_case(case, reference_date):
    latest = case.latest
    meta = latest["metaData"]
    diagnosis = latest["case"]["diagnosisRd"]

    assessment = diagnosis["diagnosticAssessment"]
    if assessment not in ASSESSMENT_STATUS:
        raise ValueError(f"unknown diagnosticAssessment '{assessment}'")

    gfk1 = parse_date(meta["molecularBoardDecisionDate"])
    years, days = age(meta["birthDate"], gfk1)

    # GFK2: the first case conference after the indication board, over all submissions
    plans = [
        (parse_date(s["plan"]["carePlanRd"]["molecularBoardDecisionDate"]), s)
        for s in case.submissions if s.get("plan")
    ]
    later = [(d, s) for d, s in plans if d > gfk1]
    medical_days = documentary_days = None
    if later:
        gfk2 = min(d for d, _ in later)
        first = next(s for d, s in later if d == gfk2)  # submissions are ordered by time
        medical_days = (gfk2 - gfk1).days
        documentary_days = (submission_date(first) - gfk2).days

    priors = latest["case"].get("priorRds") or []
    prior = max(priors, key=lambda p: p.get("diagnosticDate") or "", default=None)
    prior_type = PRIOR_TYPE.get(prior["genomicTestType"], OTHER_TESTS) if prior else NO_TEST
    prior_date = parse_date(prior.get("diagnosticDate")) if prior and prior_type != NO_TEST else None

    recommendations, therapies, management = set(), set(), set()
    for s in case.submissions:
        plan = s.get("plan")
        if not plan:
            continue
        care_plan = plan["carePlanRd"]
        recommendations |= {label for flag, label in RECOMMENDATIONS if care_plan.get(flag)}
        therapies |= {
            (t["type"], THERAPY_KIND.get(t["strategy"], NOT_ASSIGNABLE)) for t in plan.get("recommendedTherapies") or []
        }
        management |= set(care_plan.get("clinicalManagementDescriptions") or [])

    codings = diagnosis.get("diagnoses") or []
    orpha = next((c["code"] for c in codings if c.get("system") == ORPHANET), None)
    orpha_code = re.search(r"\d+", orpha).group() if orpha and re.search(r"\d+", orpha) else None

    return Evaluated(
        status           = ASSESSMENT_STATUS[assessment],
        partial          = assessment == PARTIAL,
        sex              = {"male": "m", "female": "w", "other": "d"}.get(meta.get("gender"), "unbekannt"),
        age_group        = age_group(years, days),
        adult            = years >= 18,
        insurance        = meta.get("coverageType") if meta.get("coverageType") in ("GKV", "PKV") else "andere",
        no_indication    = meta.get("decisionToInclude") is False,
        extent           = EXTENTS.get(diagnosis.get("diagnosticExtent"), "unbekannt"),
        library_type     = diagnosis.get("libraryType") or "none",
        research_consent = permits_research_use(meta.get("researchConsents") or [], reference_date),
        medical_days     = medical_days,
        documentary_days = documentary_days,
        prior_type       = prior_type,
        prior_year       = year_bucket(prior_date),
        prior_after_2025 = prior_date is not None and prior_date.year > 2025,
        recommendations  = recommendations,
        therapies        = therapies,
        management       = management,
        orpha_code       = orpha_code,
        icd10_codes      = [c["code"] for c in codings if c.get("system") == ICD10GM],
        no_matching_code = diagnosis.get("noMatchingCodeExists") is True,
    )


def disease_group(e, heads):
    """The template's disease group of a case, and the unmapped Orphanet head, if any"""
    if any(NEUROMUSCULAR_ICD10.match(code) for code in e.icd10_codes):
        return NEUROMUSCULAR, None
    if e.orpha_code is None or e.orpha_code not in heads:
        return UNKNOWN_GROUP, None
    head = heads[e.orpha_code]
    if head in HEAD_GROUP:
        return HEAD_GROUP[head], None
    return UNKNOWN_GROUP, (None if head in UNASSIGNED_HEADS else head)


# ---------------------------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------------------------

def percent(n, base):
    if not base:
        return None
    return float((Decimal(n) * 100 / Decimal(base)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def mean(values):
    return float(Decimal(statistics.mean(values)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) if values else None


class Matrix:
    def __init__(self):
        self.cells = {}
        self.extras = []

    def count(self, row, n, base, count_col="C", percent_col="E"):
        self.cells[f"{count_col}{row}"] = n
        if percent_col:
            self.cells[f"{percent_col}{row}"] = percent(n, base)

    def extra(self, section, item, value):
        self.extras.append({"section": section, "item": item, "value": value})


def build_matrix(evaluated, heads, period_label=None):
    m = Matrix()
    total = len(evaluated)

    def n(pred, cases=evaluated):
        return sum(1 for e in cases if pred(e))

    m.cells["G5"] = f"Auswertungszeitraum: {period_label or 'gesamter Export'}"

    m.count(3, total, None, percent_col=None)
    m.count(5, n(lambda e: e.research_consent), total)

    for row, sex in zip((8, 9, 10), ("m", "w", "d")):
        m.count(row, n(lambda e: e.sex == sex), total)
    m.extra("Geschlechterverteilung", "unbekannt", n(lambda e: e.sex == "unbekannt"))

    for row, group in zip(range(12, 17), AGE_GROUPS):
        m.count(row, n(lambda e: e.age_group == group), total)

    m.count(18, n(lambda e: e.insurance == "GKV"), total)
    m.count(19, n(lambda e: e.insurance == "PKV"), total)
    m.extra("Versicherung", "andere", n(lambda e: e.insurance == "andere"))

    m.count(20, n(lambda e: e.no_indication), total)

    for row, attr, label in ((21, "medical_days", "medizinisch, GFK1 bis GFK2"),
                             (22, "documentary_days", "dokumentarisch, GFK2 bis Einreichung beim KDK")):
        days = [getattr(e, attr) for e in evaluated if getattr(e, attr) is not None]
        m.cells[f"C{row}"] = mean(days)
        section = f"Bearbeitungsdauer ({label}) in Tagen"
        m.extra(section, "Fälle", len(days))
        m.extra(section, "Median", statistics.median(days) if days else None)
        m.extra(section, "Minimum", min(days, default=None))
        m.extra(section, "Maximum", max(days, default=None))

    extents = ("Single", "Duo", "Trio")
    for row, extent in zip((24, 25, 26), extents):
        m.count(row, n(lambda e: e.extent == extent), total)

    m.count(28, n(lambda e: e.library_type == "wgs"), total)
    m.count(29, n(lambda e: e.library_type == "wgs_lr"), total)
    for library_type in ("none", "panel", "wes"):
        m.extra("Anzahl WGS/lRG", library_type, n(lambda e: e.library_type == library_type))

    for row, status in zip((31, 32, 33), STATUSES):
        m.count(row, n(lambda e: e.status == status), total)
    m.extra("Diagnoserate", "davon partiell (unter Unklar)", n(lambda e: e.partial))

    for row, extent in zip((34, 35, 36), extents):
        of_extent = [e for e in evaluated if e.extent == extent]
        m.count(row, n(lambda e: e.status == DIAGNOSED, of_extent), len(of_extent))
        m.extra(f"Diagnoserate {extent}-Genom", "davon partiell (unter Unklar)", n(lambda e: e.partial, of_extent))

    # diagnosis status by age group
    for row, group in zip(range(31, 37), AGE_GROUPS + ("SUMME",)):
        cases = evaluated if group == "SUMME" else [e for e in evaluated if e.age_group == group]
        for (count_col, percent_col), status in zip((("I", "J"), ("K", "L"), ("M", "N")), STATUSES):
            m.count(row, n(lambda e: e.status == status, cases), len(cases), count_col, percent_col)

    # prior findings per diagnosis status
    for first_row, status in zip((39, 47, 55), STATUSES):
        cases = [e for e in evaluated if e.status == status]
        for row, prior_type in zip(range(first_row, first_row + 6), PRIOR_TYPES):
            m.count(row, n(lambda e: e.prior_type == prior_type, cases), len(cases))
        for row, prior_type in zip(range(first_row, first_row + 6), PRIOR_TYPES_BY_YEAR):
            of_type = [e for e in cases if e.prior_type == prior_type]
            for col, bucket in zip("HIJKLM", YEAR_BUCKETS):
                m.cells[f"{col}{row}"] = n(lambda e: e.prior_year == bucket, of_type)
            m.cells[f"N{row}"] = len(of_type)

    for row, (_, label) in zip(range(64, 68), RECOMMENDATIONS):
        m.count(row, n(lambda e: label in e.recommendations), total)
    for row, therapy in zip(range(68, 73), THERAPY_ROWS):
        m.count(row, n(lambda e: therapy in e.therapies), total)
    for category, label in THERAPY_CATEGORY.items():
        for kind in ("nicht medikamentös", NOT_ASSIGNABLE) if category == "causal" else (NOT_ASSIGNABLE,):
            m.extra("Therapieempfehlung", f"{label} {kind}", n(lambda e: (category, kind) in e.therapies))

    for row, (code, _) in zip(range(75, 82), MANAGEMENT):
        m.count(row, n(lambda e: code in e.management), total)
    m.count(82, n(lambda e: not (e.management & {code for code, _ in MANAGEMENT})), total)

    m.count(84, n(lambda e: e.orpha_code is None), total)
    m.extra("Erkr. ohne Orphacode", "noMatchingCodeExists (alle Kodiersysteme)", n(lambda e: e.no_matching_code))

    unmapped_heads = set()
    if heads is not None:
        groups = []
        for e in evaluated:
            group, unmapped = disease_group(e, heads)
            groups.append(group)
            if unmapped:
                unmapped_heads.add(unmapped)
        blocks = (
            (88, list(zip(evaluated, groups))),
            (110, [(e, g) for e, g in zip(evaluated, groups) if e.adult]),
            (131, [(e, g) for e, g in zip(evaluated, groups) if not e.adult]),
        )
        for first_row, block in blocks:
            for row, group in zip(range(first_row, first_row + len(DISEASE_GROUPS)), DISEASE_GROUPS):
                of_group = [e for e, g in block if g == group]
                m.count(row, len(of_group), len(block), "C", "D")
                for (count_col, percent_col), status in zip((("E", "F"), ("G", "H"), ("I", "K")), STATUSES):
                    m.count(row, n(lambda e: e.status == status, of_group), len(of_group), count_col, percent_col)

    return m, unmapped_heads


# ---------------------------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------------------------

SHEET = "xl/worksheets/sheet1.xml"
MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# labels the cells are filled in for, to refuse a template of another layout
EXPECTED_LABELS = {
    "B3":   "Fallzahl gesamt",
    "B21":  "Bearbeitungsdauer (medzinisch, GFK1 bis GFK2)",
    "B22":  "Bearbeitungsdauer (dokumentarisch, GFK2 bis Einreichung beim KDK)",
    "B24":  "Single",
    "B36":  "Diagnoserate Trio-Genom",
    "G31":  "Neonaten (bis 37 Tage)",
    "B39":  "Genom (SR)",
    "G39":  "Array, Chromosomenanalyse, Einzelgentestung, andere",
    "B60":  "keine",
    "B64":  "1)",
    "B72":  "Kausal interventionell",
    "B82":  "8)",
    "B84":  "Erkr. ohne Orphacode",
    "I86":  "Undiagnostiziert",
    "B88":  "Muskuläre",
    "B105": "unbekannt",
    "B129": "Pädiatrisch",
    "B148": "unbekannt/andere",
}

ROW_RE = re.compile(r'<row r="(\d+)"[^>]*?(?:/>|>(.*?)</row>)', re.S)
CELL_RE = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)


def column_index(col):
    i = 0
    for ch in col:
        i = i * 26 + ord(ch) - 64
    return i


def cell_texts(archive):
    """Cell reference -> text of the string cells of the sheet"""
    shared = []
    if "xl/sharedStrings.xml" in archive.namelist():
        for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall(f"{MAIN_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{MAIN_NS}t")))
    texts = {}
    for c in ET.fromstring(archive.read(SHEET)).iter(f"{MAIN_NS}c"):
        v = c.find(f"{MAIN_NS}v")
        if c.get("t") == "s" and v is not None:
            texts[c.get("r")] = shared[int(v.text)]
        elif c.get("t") == "inlineStr":
            texts[c.get("r")] = "".join(t.text or "" for t in c.iter(f"{MAIN_NS}t"))
    return texts


def check_template(archive):
    texts = cell_texts(archive)
    wrong = [
        f"{ref}: expected '{label}…', found '{texts.get(ref, '')}'"
        for ref, label in EXPECTED_LABELS.items()
        if not texts.get(ref, "").replace("\xa0", " ").strip().startswith(label)
    ]
    if wrong:
        raise ValueError("template doesn't have the layout of Update_Auswertematrix_GZ1.xlsx:\n  " + "\n  ".join(wrong))


def cell_xml(ref, attrs, value):
    style = re.search(r'\ss="\d+"', attrs)
    style = style.group() if style else ""
    if value is None:
        return f'<c r="{ref}"{style}/>'
    if isinstance(value, str):
        return f'<c r="{ref}"{style} t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    return f'<c r="{ref}"{style}><v>{value}</v></c>'


def fill_sheet(sheet, cells):
    """The sheet XML with the cells set, keeping their styles; string manipulation, since
    rewriting with ElementTree would lose namespace declarations Excel relies on"""
    by_row = {}
    for ref, value in cells.items():
        col, row = re.match(r"([A-Z]+)(\d+)", ref).groups()
        by_row.setdefault(int(row), {})[col] = value

    found = set()

    def replace_row(match):
        row = int(match.group(1))
        if row not in by_row:
            return match.group(0)
        found.add(row)
        head = match.group(0)[:match.group(0).index(">") + 1].rstrip("/>").rstrip("/") + ">"
        existing = {c.group(1): (c.group(3), c.group(0)) for c in CELL_RE.finditer(match.group(2) or "")}
        merged = {col: xml for col, (_, xml) in existing.items()}
        for col, value in by_row[row].items():
            merged[col] = cell_xml(f"{col}{row}", existing.get(col, ("", ""))[0], value)
        return head + "".join(merged[c] for c in sorted(merged, key=column_index)) + "</row>"

    filled = ROW_RE.sub(replace_row, sheet)
    missing = set(by_row) - found
    if missing:
        raise ValueError(f"template has no rows {sorted(missing)}")
    return filled


def write_template(template, output, cells):
    with zipfile.ZipFile(template) as src:
        check_template(src)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == SHEET:
                    data = fill_sheet(data.decode("utf-8"), cells).encode("utf-8")
                dst.writestr(item, data)


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def run(rows, export_failures, heads, start, end, reference_date):
    cases, tests, without_initial = build_cases(rows, start, end)

    evaluated, unreadable = [], []
    for case in cases:
        try:
            evaluated.append(evaluate_case(case, reference_date))
        except (KeyError, TypeError, ValueError) as error:
            unreadable.append(f"{case.key}: {error!r}")

    period_label = None
    if start or end:
        until = date.fromordinal(end.toordinal() - 1) if end else None
        period_label = f"{start.strftime('%d.%m.%Y') if start else '…'} - {until.strftime('%d.%m.%Y') if until else '…'}"

    matrix, unmapped_heads = build_matrix(evaluated, heads, period_label)

    notes = [
        f"{len(evaluated)} cases (episodes of care with an initial submission) counted, the status of each from "
        "its latest non-test submission; specification: docs/rd-evaluation-matrix.txt.",
        f"{tests} test submissions left out, {without_initial} episodes without initial submission not counted.",
        f"Research consent evaluated as of {reference_date.isoformat()}.",
    ]
    if start or end:
        notes.append(f"Cases with initial submission from {start or '…'} until before {end or '…'}.")
    if export_failures:
        notes.append(f"{export_failures} submissions couldn't be converted by the export, see its table 'failures'.")
    if unreadable:
        notes.append(f"{len(unreadable)} cases left out, since their latest submission couldn't be read: " + "; ".join(unreadable))
    if heads is None:
        notes.append("Disease groups (rows 86-148) not filled: no Orphanet linearisation given (--orphanet).")
    if unmapped_heads:
        notes.append("Orphanet classification heads without template group, counted as unbekannt: "
                     + ", ".join(sorted(unmapped_heads)) + "; extend HEAD_GROUP.")
    if any(e.prior_after_2025 for e in evaluated):
        notes.append("Prior findings after 2025 are counted under '2025/2024'.")

    return {"cells": matrix.cells, "extras": matrix.extras, "notes": notes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="result JSON of export-datenkranz, or a JSON list of submissions")
    parser.add_argument("--orphanet", help="Orphadata linearisation, en_product7.xml (needed for the disease groups)")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="initial submitted on or after (YYYY-MM-DD)")
    parser.add_argument("--until", dest="end", type=date.fromisoformat, help="initial submitted before (YYYY-MM-DD)")
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date.today(),
                        help="date the research consents are evaluated at (default: today)")
    parser.add_argument("--template", help="Update_Auswertematrix_GZ1.xlsx, to write the filled template")
    parser.add_argument("--output", default="rd-evaluation-matrix", help="output path without extension (default: %(default)s)")
    args = parser.parse_args(argv)

    rows, export_failures = load_submissions(args.input)
    heads = load_linearisation(args.orphanet) if args.orphanet else None
    result = run(rows, export_failures, heads, args.start, args.end, args.reference_date)

    with open(f"{args.output}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    written = [f"{args.output}.json"]
    if args.template:
        write_template(args.template, f"{args.output}.xlsx", result["cells"])
        written.append(f"{args.output}.xlsx")

    for note in result["notes"]:
        print(note, file=sys.stderr)
    print("Written: " + ", ".join(written), file=sys.stderr)


if __name__ == "__main__":
    main()
