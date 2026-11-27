#!/usr/bin/env python3
"""
RD evaluation matrix (Auswertematrix) from BfArM Datenkranz submissions, in total and per
Leistungserbringer (LE).

Specification: docs/rd-evaluation-matrix.txt. Standard library only, so it can be shared with the
other clinical data nodes (KDKs).

Input, either
  - the result JSON of the evaluation module's "export-datenkranz" (table "datenkranz"), cases
    keyed by (site, patient, episodeOfCare), or
  - a JSON list of {"case": <any case key>, "submittedAt": <ISO datetime>, "content": <Datenkranz>},
    one entry per submission, for KDKs with their own export.

Output (<output> without extension):
  <output>.json          every value, in total and per LE, the cases to clarify with the LEs (not with
                         --rueckfragen-per-le), notes
  <output>.csv           long table LE | Abschnitt | Kategorie | Einheit | Wert | Bezug | Basis | Anteil
  <output>_rueckfragen.csv  cases to clarify with their LE (contains case keys of all LEs: only for the KDK);
                         with --rueckfragen-per-le (recommended) instead one <output>_rueckfragen_<LE>.csv per LE
Only the rows derived from the diagnosis codes, which tell which disease a case has (CODE_SECTIONS, CODE_ITEMS:
Orphacode, the disease groups with their status rows, "Diagnose ohne Erkrankungsgruppe"), are suppressed: there an
LE's count of 1 to --min-cell - 1 (default 5, 1 disables) is shown as "<5", in all outputs except the Rückfragen
(primary suppression only, decision of the data holders of 27.11.2026). There is no complementary suppression: a
suppressed count can follow from shown sums (Gesamt, the group totals, the other quarters). Gesamt and every other
count, share and duration are exact, also the diagnosis status and rates.
Follow-up (FU_SECTIONS): cases with GFK2 due, current or overdue at --reference-date with the interval
--follow-up-months (default 12), days from GFK2 to the first follow-up, mandatory fields of the follow-ups.
The xlsx is written from the JSON by fill_template.py (needs openpyxl).

Example:
  python3 rd_evaluation_matrix.py export-datenkranz_2026-10-30_10-00-00.json \\
    --orphanet en_product7.xml --from 2024-07-01 --until 2026-01-01 --output matrix
  uv run fill_template.py matrix.json Auswertmatrix_Template.xlsx --output matrix.xlsx
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace


# ---------------------------------------------------------------------------------------------
# Labels and mappings, see docs/rd-evaluation-matrix.txt
# ---------------------------------------------------------------------------------------------

TOTAL = "Gesamt"

DIAGNOSED, UNCLEAR, UNDIAGNOSED, OPEN = "Diagnose", "Unklar", "Undiagnostiziert", "offen"
STATUSES = (DIAGNOSED, UNCLEAR, UNDIAGNOSED)    # of the closed cases
CASE_STATUSES = STATUSES + (OPEN,)

ASSESSMENT_STATUS = {
    "confirmedGeneticDiagnosis":           DIAGNOSED,
    "partialGeneticDiagnosis":             UNCLEAR,
    "suspectedGeneticDiagnosis":           UNCLEAR,
    "furtherGeneticDiagnosticRecommended": UNCLEAR,
    "noGeneticDiagnosis":                  UNDIAGNOSED,
}
PARTIAL = "partialGeneticDiagnosis"

NEONATE_DAYS = 37  # as in the stakeholders' template (not the WHO's 28)
NEONATES, CHILDREN, ADOLESCENTS, ADULTS, SENIORS = (
    f"Neonaten (bis {NEONATE_DAYS} Tage)", f"Kinder ({NEONATE_DAYS + 1} Tage - 12 Jahre)", "Jugendliche (13-17 Jahre)",
    "Erwachsene (18-64 Jahre)", "Senioren (65+ Jahre)",
)
AGE_GROUPS = (NEONATES, CHILDREN, ADOLESCENTS, ADULTS, SENIORS)

GENOME_LIBRARIES = {"wgs": "WGS", "wgs_lr": "lrWGS"}
OTHER_LIBRARIES = {"panel": "Panel", "wes": "Exom (WES)", "none": "keine (noch nicht sequenziert)"}
EXTENTS = {"singleGenome": "Single", "duoGenome": "Duo", "trioGenome": "Trio"}

OTHER_TESTS = "Array, Einzelgentestung, Chromosomenanalyse, andere"
PRIOR_TYPES = ("Genom (SR)", "Genom (LR)", "Exom", "Panel", OTHER_TESTS)
PRIOR_TYPE = {
    "genomeShortRead": "Genom (SR)", "genomeLongRead": "Genom (LR)", "exome": "Exom", "panel": "Panel",
    "array": OTHER_TESTS, "single": OTHER_TESTS, "karyotyping": OTHER_TESTS, "other": OTHER_TESTS,
}
PRIOR_NONE_PERFORMED = "keine durchgeführt (genomicTestType none)"
PRIOR_NOT_DOCUMENTED = "nicht dokumentiert (priorRds fehlt)"
YEAR_BUCKETS = ("2026 und später", "2025/2024", "2023/2022", "2021/2020", "2019/2018", "2017 und älter", "unbekannt")

RECOMMENDATIONS = (
    ("studyRecommended",        "1) Klinische Studie"),
    ("counsellingRecommended",  "2) Humangenetische Beratung"),
    ("reEvaluationRecommended", "3) Re-Evaluation"),
    ("therapyRecommended",      "4) Therapieempfehlung"),
)

# strategy (all 9 of the schema) -> kind of the template; OPEN: to be confirmed by the stakeholders
NOT_ASSIGNABLE = "nicht zuordenbar"
THERAPY_KIND = {
    "systemicMedication":   "medikamentös",
    "targetedMedication":   "medikamentös",
    "preventionMedication": "medikamentös",
    "genetic":              "interventionell",
    "prophylactic":         "nicht medikamentös",
    "earlyDetection":       "nicht medikamentös",
    "nutrition":            "nicht medikamentös",
    "combination":          NOT_ASSIGNABLE,
    "other":                NOT_ASSIGNABLE,
}
THERAPY_KINDS = ("nicht medikamentös", "medikamentös", "interventionell", NOT_ASSIGNABLE)
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
MANAGEMENT_UNSPECIFIED = "8a) empfohlen, nicht näher angegeben"
MANAGEMENT_NOT_RECOMMENDED = "8b) nicht empfohlen (clinicalManagementRecommended = false)"

ORPHANET = "https://www.orpha.net"
ICD10GM = "http://fhir.de/CodeSystem/bfarm/icd-10-gm"

NEUROMUSCULAR = "Muskuläre / neuromuskul. Erkrankungen"
NAMED_GROUPS = (
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
)
# "unbekannt", split by reason
UNKNOWN_NO_ORPHA = "unbekannt: kein Orphacode"
UNKNOWN_NOT_LINEARISED = "unbekannt: Orphacode nicht in der Linearisierung"
UNKNOWN_UNDETERMINED = "unbekannt: ohne Diagnose nach vollständiger Abklärung (ORPHA:616874)"
UNKNOWN_HEAD = "unbekannt: Gruppenkopf ohne Vorlagengruppe (genetisch, infektiös, toxisch, Transplantation)"
UNKNOWN_GROUPS = (UNKNOWN_NO_ORPHA, UNKNOWN_NOT_LINEARISED, UNKNOWN_UNDETERMINED, UNKNOWN_HEAD)
DISEASE_GROUPS = NAMED_GROUPS + UNKNOWN_GROUPS

# Orphanet classification heads of the linearisation (Orphadata product 7) -> template group
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
    "616874": UNKNOWN_UNDETERMINED,                                  # Rare disorder without a determined diagnosis after full investigation
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

# optional variant fields whose fill rate is reported (molecular.*Variants[])
VARIANT_FIELDS = (
    ("zygosity",            "Zygotie"),
    ("segregationAnalysis", "Segregation"),
    ("modeOfInheritance",   "Erbgang"),
    ("acmgClass",           "ACMG-Klasse"),
    ("acmgCriteria",        "ACMG-Kriterien"),
    ("cdnaChange",          "HGVS c."),
    ("gdnaChange",          "HGVS g."),
    ("proteinChange",       "HGVS p."),
)
VARIANT_LISTS = ("smallVariants", "structuralVariants", "copyNumberVariants")
# optional case fields whose fill rate is reported, with the cases they apply to
CASE_FIELDS = (
    ("case.diagnosisRd.diagnosisGmfcs", "Fälle"),
    ("case.priorRds",                   "Fälle"),
    ("followUp",                        "Fälle"),
    ("followUp.diseaseProgression",     "Fälle mit Follow-up"),
    ("followUp.deathDate",              "Fälle mit Follow-up, verstorben"),
)

# Follow-up after GFK2, as the follow-up schedule of the oncology matrix (dnpm_evaluation_matrix.py): due one interval
# after GFK2, current with a followUpDate in the interval before the reference date (--follow-up-months)
FU_MONTHS = 12
FU_DECEASED, FU_NOT_DUE, FU_CURRENT, FU_OVERDUE = (
    "verstorben (kein Follow-up mehr fällig)", "noch nicht fällig (GFK2 weniger als ein Intervall vor dem Stichtag)",
    "fällig, aktuell (Follow-up im Intervall vor dem Stichtag)", "fällig, überfällig (kein Follow-up im Intervall vor dem Stichtag)")
FU_STATES = (FU_DECEASED, FU_NOT_DUE, FU_CURRENT, FU_OVERDUE)
FU_SECTION = "Follow-up (Fälle mit GFK2)"
FU_DAYS_SECTION = "Dauer GFK2 bis erstes Follow-up"
FU_FIELDS_SECTION = "Follow-up: Pflichtfelder (Follow-up-Einträge aller Einreichungen)"
FU_SECTIONS = (FU_SECTION, FU_DAYS_SECTION, FU_FIELDS_SECTION)
FU_NOTE = "Follow-up (Abschnitte Follow-up (Fälle mit GFK2) und folgende):"

# Problems to clarify with the LE, one entry per case (or submission)
P_GFK1_AFTER_SUBMISSION = "GFK1 nach dem Einreichungsdatum"
P_GFK2_AFTER_SUBMISSION = "GFK2 nach dem Einreichungsdatum"
P_SHORT_MEDICAL = "Medizinische Bearbeitungsdauer unplausibel kurz"
P_NO_GENOME = "Keine Genomsequenzierung (libraryType panel/wes)"
P_PRIOR_AFTER_GFK1 = "Vorbefund nach GFK1"
P_DIAGNOSIS_WITHOUT_GROUP = "Diagnose ohne Erkrankungsgruppe"
P_NOT_CONVERTED = "Einreichung nicht konvertierbar"
P_WITHOUT_INITIAL = "Episode ohne Erstmeldung"
P_UNREADABLE = "Fall nicht auswertbar"
STALE = "stale"   # losses: non-convertible submissions of an evaluated episode
PROBLEMS = (P_GFK1_AFTER_SUBMISSION, P_GFK2_AFTER_SUBMISSION, P_SHORT_MEDICAL, P_NO_GENOME,
            P_PRIOR_AFTER_GFK1, P_DIAGNOSIS_WITHOUT_GROUP, P_NOT_CONVERTED, P_WITHOUT_INITIAL, P_UNREADABLE)

# Scope of the small-cell suppression (--suppress-scope): only the rows derived from the diagnosis codes (default,
# decision of the data holders of 26.11.2026: "nur auf die Diagnosen selbst (Diagnosecodes)"), or every count. The
# former scope "diagnoses" (also the diagnosis status rows) is gone; results written with it carry
# suppress_scope "diagnoses" (fill_template.py and the dashboard still read them).
SUPPRESS_CODES = "codes"
SUPPRESS_SCOPES = (SUPPRESS_CODES,)
PROBLEMS_SECTION = "Rückfragen an LE (Anzahl, Einzelfälle siehe Rückfragen)"
DISEASE_GROUP_BLOCKS = ("Erkrankungsgruppen gesamt", "Erkrankungsgruppen Adult (ab 18)",
                        "Erkrankungsgruppen Pädiatrisch (unter 18)")
# The code rows: the rows that tell which disease a case has, derived from its diagnosis codes (Orphacode, ICD-10 for
# the neuromuscular group): the Orpha coding, the disease groups in total, Adult and Pädiatrisch with their status rows
# (group x status) and the Rückfrage "Diagnose ohne Erkrankungsgruppe". With SUPPRESS_CODES only their cells are
# suppressed; every other number is exact, also the diagnosis status rows (Diagnosestatus, Diagnoserate, by WGS
# extent and by age group, the prior findings by status): they tell whether a case has a diagnosis, not which one.
CODE_SECTIONS = ("Orphacode", *DISEASE_GROUP_BLOCKS)
CODE_ITEMS = ((PROBLEMS_SECTION, P_DIAGNOSIS_WITHOUT_GROUP),)
# Bases of code rows that are shown exactly: they count no code (the cases of the matrix, of the age block of a
# disease group block = the cases of its age groups). The base of a status row of a disease group (the cases of
# the group) is suppressed with the row.
EXACT_CODE_BASES = ("Fälle",) + tuple(f"Fälle ({s})" for s in DISEASE_GROUP_BLOCKS)


def is_code_row(row):
    """A row derived from the diagnosis codes (CODE_SECTIONS, CODE_ITEMS)"""
    return row.section in CODE_SECTIONS or (row.section, row.item) in CODE_ITEMS


def is_code_cell(row, field_):
    """A cell suppressed with SUPPRESS_CODES: the value of a code row, or its base unless that is exact"""
    return is_code_row(row) and (field_ == "value" or row.base_label not in EXACT_CODE_BASES)


def scope_argument(value):
    """--suppress-scope of the command lines: the former scopes are refused with a hint"""
    if value == "diagnoses":
        raise argparse.ArgumentTypeError("'diagnoses' was replaced by 'codes' (only the rows derived from the diagnosis "
                                         "codes, the diagnosis status exact; decision of the data holders, 26.11.2026)")
    if value == "all":
        raise argparse.ArgumentTypeError("'all' was removed: only the rows derived from the diagnosis codes are "
                                         "suppressed (decision of the data holders, 27.11.2026)")
    return value


# ---------------------------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------------------------

# metadata.reasonResearchConsentMissing of the DIP submission (service-base BroadConsent.ReasonMissing), exported by
# export-datenkranz in the column of that name beside the Datenkranz, which doesn't carry it
DIP_REASONS = {
    "patient-inability":     "Patient nicht einwilligungsfähig",
    "patient-refusal":       "Patient lehnt ab",
    "consent-not-returned":  "Einwilligung nicht zurückgegeben",
    "other-patient-reason":  "anderer patientenbezogener Grund",
    "technical-issues":      "technische Gründe",
    "organizational-issues": "organisatorische Gründe",
}
REASON_OTHER = "anderer Wert"
REASON_NONE = "kein Grund angegeben"
REASON_NOT_EXPORTED = "nicht im Export (Spalte reasonResearchConsentMissing fehlt)"
REASON_LABELS = tuple(DIP_REASONS.values()) + (REASON_OTHER, REASON_NONE, REASON_NOT_EXPORTED)
NOT_EXPORTED = object()     # SubmissionRow.reason of an input without the column


@dataclass
class SubmissionRow:
    case: str
    submitted_at: str
    content: dict
    site: str = None
    reason: object = NOT_EXPORTED   # reasonResearchConsentMissing of the export row, None if empty


@dataclass
class Failure:
    site: str
    case: str
    tan: str
    error: str


def load_submissions(path):
    """The submissions of the input file, and the submissions the export couldn't convert"""
    with open(path, encoding="utf-8") as f:
        return submissions_from(json.load(f), path)


def submissions_from(data, path="input"):
    """load_submissions of the parsed input file (monthly_matrices parses it once for RD and oncology)"""
    if isinstance(data, dict) and "tables" in data:
        tables = {t["name"]: t for t in data["tables"]}
        if "datenkranz" not in tables:
            raise ValueError(f"{path}: no table 'datenkranz', not a result of export-datenkranz")

        def key(r):
            return json.dumps([r.get("site"), r.get("patient"), r.get("episodeOfCare")])

        exported = "reasonResearchConsentMissing" in (tables["datenkranz"].get("columns") or [])
        rows = [
            SubmissionRow(key(r), r.get("submittedAt") or "", r["content"], r.get("site"),
                          r.get("reasonResearchConsentMissing") if exported else NOT_EXPORTED)
            for r in tables["datenkranz"]["rows"]
            if r.get("useCase") in (None, "RD")
        ]
        failures = [
            Failure(r.get("site"), key(r), r.get("tan"), str(r.get("error") or ""))
            for r in tables.get("failures", {}).get("rows", [])
            if r.get("useCase") in (None, "RD")
        ]
        return rows, failures

    if isinstance(data, list):
        def key(e):
            # the LE belongs to the key: the same local case id at two LEs are two cases; sort_keys makes an
            # object key independent of the order of its fields
            le = e.get("site") or e["content"].get("metaData", {}).get("submission", {}).get("submitterId")
            return json.dumps([le, e["case"]], sort_keys=True)

        exported = any("reasonResearchConsentMissing" in e for e in data)
        return [SubmissionRow(key(e), e.get("submittedAt") or "", e["content"], e.get("site"),
                              e.get("reasonResearchConsentMissing") if exported else NOT_EXPORTED)
                for e in data], []

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


def submission_meta(content):
    return content["metaData"]["submission"]


def submission_type(content):
    return submission_meta(content)["type"]


def submitted_order(row):
    """Sort key of a submission by submittedAt: parsed, time zones to UTC (a text sort misorders mixed offsets such
    as "+01:00" and "Z"); a value that can't be parsed sorts after the parsed ones, by its text"""
    try:
        t = datetime.fromisoformat(str(row.submitted_at).replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone(timezone.utc).replace(tzinfo=None)
        return (0, t, "")
    except ValueError:
        return (1, datetime.min, str(row.submitted_at))


def submission_date(content):
    return parse_date(submission_meta(content)["date"])


@dataclass
class Case:
    key: str
    le: str
    submissions: list  # non-test, ordered by submittedAt
    reasons: list = None   # SubmissionRow.reason of each submission

    @property
    def latest(self):
        return self.submissions[-1]

    @property
    def initial(self):
        return next(s for s in self.submissions if submission_type(s) == "initial")


def leistungserbringer(row):
    """The LE of a submission: the site of the export, else the submitterId"""
    if row.site:
        return str(row.site)
    return str(row.content.get("metaData", {}).get("submission", {}).get("submitterId") or "?")


def build_cases(rows, start=None, end=None):
    """
    The cases (episodes with an initial submission, initial submitted in [start, end)), the
    number of tests left out, the episodes without initial as (LE, key), and the submitterIds by LE
    """
    by_key = {}
    tests = 0
    submitters = {}
    for row in rows:
        content = row.content
        if content.get("metaData", {}).get("submission", {}).get("diseaseType") != "rare":
            continue
        if submission_type(content) == "test":
            tests += 1
            continue
        by_key.setdefault(row.case, []).append(row)
        submitter = submission_meta(content).get("submitterId")
        if row.site and submitter:
            submitters.setdefault(str(row.site), set()).add(str(submitter))

    cases, without_initial = [], []
    for key, subs in by_key.items():
        # stable sort by the parsed submittedAt (time zones to UTC); equal times keep the order of the input
        subs = sorted(subs, key=submitted_order)
        case = Case(key, leistungserbringer(subs[0]), [r.content for r in subs], [r.reason for r in subs])
        if not any(submission_type(s) == "initial" for s in case.submissions):
            without_initial.append((case.le, key, case.latest["metaData"].get("tanC")))
            continue
        try:
            initial_date = submission_date(case.initial)
        except (KeyError, TypeError, ValueError):
            initial_date = None
        if initial_date is None:
            # an initial submission without a readable date belongs to no period: like an episode without initial
            # submission (a Rückfrage in every matrix of a run), not an abort of the whole run
            without_initial.append((case.le, key, case.latest["metaData"].get("tanC")))
            continue
        if (start and initial_date < start) or (end and initial_date >= end):
            continue
        cases.append(case)
    return cases, tests, without_initial, submitters


def age(birth_month, at):
    """(full years, days) at a date, the birth date given by month taken as its first day"""
    born = date(int(birth_month[:4]), int(birth_month[5:7]), 1)
    years = at.year - born.year - ((at.month, at.day) < (born.month, born.day))
    return years, (at - born).days


def age_group(years, days):
    if days <= NEONATE_DAYS:
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
            and start is not None and minus_years(reference_date, 5) < start <= reference_date
            and (end is None or end >= reference_date)
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
            elif provision.get("type") == "permit" and (parse_date((provision.get("period") or {}).get("start")) or date.min) > reference_date:
                continue    # a permit that starts after the date is not in effect yet: it neither permits nor denies
            else:
                decisions[code] = permitted(provision)
    return any(decisions.values())


def dip_reason(case):
    """The reason for a missing broad consent the latest submission of the DIP states, as one of REASON_LABELS"""
    reason = case.reasons[-1] if case.reasons else NOT_EXPORTED
    if reason is NOT_EXPORTED:
        return REASON_NOT_EXPORTED
    if not reason:
        return REASON_NONE
    return DIP_REASONS.get(reason, REASON_OTHER)


def permits_mv_sequencing(mv_consent):
    """The latest metaData.mvConsent.scope entry of the domain mvSequencing is a permit"""
    scopes = [s for s in (mv_consent or {}).get("scope") or [] if s.get("domain") == "mvSequencing"]
    latest = max(scopes, key=lambda s: s.get("date") or "", default=None)
    return latest is not None and latest.get("type") == "permit"


def year_bucket(d):
    if d is None:
        return "unbekannt"
    if d.year >= 2026:
        return "2026 und später"
    if d.year >= 2024:
        return "2025/2024"
    if d.year >= 2022:
        return "2023/2022"
    if d.year >= 2020:
        return "2021/2020"
    if d.year >= 2018:
        return "2019/2018"
    return "2017 und älter"


@dataclass
class Evaluated:
    """The facts of a case the matrix counts"""
    key: str
    le: str
    tan: str
    status: str                 # Diagnose / Unklar / Undiagnostiziert
    case_status: str            # the same, or offen
    partial: bool
    sex: str
    age_group: str
    adult: bool
    insurance: str
    no_indication: bool
    library_type: str
    extent: str
    consent_at_submission: bool
    consent_at_reference: bool
    consent_transmitted: bool   # a researchConsents entry with a FHIR Consent in scope
    consent_reason: str         # reason for a missing consent as the DIP states it (REASON_LABELS)
    initial_month: str          # YYYY-MM of the initial submission
    mv_sequencing_consent: bool
    has_gfk2: bool
    impossible_dates: bool
    medical_days: object        # int or None
    documentary_days: object    # int or None
    prior_types: set            # (type, year bucket)
    prior_state: str            # documented / PRIOR_NONE_PERFORMED / PRIOR_NOT_DOCUMENTED
    recommendations: set
    therapies: set              # (category, strategy)
    therapy_count: int          # distinct recommended therapies
    management: set
    management_recommended: bool
    orpha_code: object          # str or None
    icd10_codes: list
    no_matching_code: bool
    case_fields: dict           # optional case field -> filled, or None if not applicable
    variants: list              # per variant: field -> filled
    problems: list = field(default_factory=list)   # (problem, detail)
    group: object = None        # disease group, with a linearisation
    fu_entries: list = field(default_factory=list)  # the followUpRds of all submissions, each once
    first_fu_days: object = None    # days from GFK2 to the first followUpDate from GFK2 on, None if none
    fu_state: object = None     # FU_STATES at the reference date, None without GFK2


def add_months(d, months):
    """The same day months later (earlier if negative), the last of the month if that has fewer days"""
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m + 1, day)
        except ValueError:
            continue


def readable(s):
    """A date, None if missing or unreadable"""
    try:
        return parse_date(s) if isinstance(s, str) else None
    except ValueError:
        return None


def follow_up_state(gfk2, fu_dates, deceased, reference_date, months=FU_MONTHS):
    """The follow-up state of a case with GFK2 at the reference date (FU_STATES)"""
    if deceased:
        return FU_DECEASED
    if add_months(gfk2, months) > reference_date:
        return FU_NOT_DUE
    since = add_months(reference_date, -months)     # counted back, so that a month end doesn't shorten the window
    current = any(since < d <= reference_date for d in fu_dates)
    return FU_CURRENT if current else FU_OVERDUE


def evaluate_case(case, reference_date, min_medical_days, follow_up_months=FU_MONTHS):
    latest = case.latest
    meta = latest["metaData"]
    diagnosis = latest["case"]["diagnosisRd"]
    problems = []

    assessment = diagnosis["diagnosticAssessment"]
    if assessment not in ASSESSMENT_STATUS:
        raise ValueError(f"unknown diagnosticAssessment '{assessment}'")
    status = ASSESSMENT_STATUS[assessment]

    gfk1 = parse_date(meta["molecularBoardDecisionDate"])
    years, days = age(meta["birthDate"], gfk1)

    # GFK2: the first case conference after the indication board, over all submissions
    plans = [
        (parse_date(s["plan"]["carePlanRd"]["molecularBoardDecisionDate"]), s)
        for s in case.submissions if s.get("plan")
    ]
    later = [(d, s) for d, s in plans if d > gfk1]
    medical_days = documentary_days = None
    # a submission can't report a board that took place after its own submission date; the
    # durations of a case with such an impossible date are left out
    impossible = False
    # each submission against its own date: a later submission must not hide an impossible date of an earlier one
    for s in case.submissions:
        try:
            board, submitted = parse_date(s["metaData"]["molecularBoardDecisionDate"]), submission_date(s)
        except (KeyError, TypeError, ValueError):
            continue    # an earlier submission whose dates can't be read doesn't make the case unreadable
        if board is not None and submitted is not None and board > submitted:
            impossible = True
            problems.append((P_GFK1_AFTER_SUBMISSION,
                             f"metaData.molecularBoardDecisionDate {board} nach metaData.submission.date "
                             f"{submitted} ({(board - submitted).days} Tage, Einreichung {s['metaData'].get('tanC')})"))
            break
    if later:
        gfk2 = min(d for d, _ in later)
        first = next(s for d, s in later if d == gfk2)  # submissions are ordered by time
        if gfk2 > submission_date(first):
            impossible = True
            problems.append((P_GFK2_AFTER_SUBMISSION,
                             f"plan.carePlanRd.molecularBoardDecisionDate {gfk2} nach metaData.submission.date "
                             f"{submission_date(first)} ({(gfk2 - submission_date(first)).days} Tage) "
                             f"der Einreichung {first['metaData'].get('tanC')}"))
        if not impossible:
            medical_days = (gfk2 - gfk1).days
            documentary_days = (submission_date(first) - gfk2).days
            if medical_days < min_medical_days:
                problems.append((P_SHORT_MEDICAL,
                                 f"GFK1 {gfk1} bis GFK2 {gfk2}: {medical_days} Tage (< {min_medical_days}), "
                                 "obwohl Sequenzierung und Auswertung dazwischenliegen"))

    library_type = diagnosis.get("libraryType") or "none"
    if library_type in ("panel", "wes"):
        problems.append((P_NO_GENOME,
                         f"case.diagnosisRd.libraryType = {library_type}; das MV GenomSeq verlangt WGS oder lrWGS: "
                         "Protokollabweichung oder falsch gesetzter libraryType"))

    # prior findings: each test, a case counts once per (type, year)
    priors = latest["case"].get("priorRds")
    prior_types = set()
    for p in priors or []:
        test_type = p.get("genomicTestType")
        if test_type == "none":
            continue
        prior_date = parse_date(p.get("diagnosticDate"))
        prior_types.add((PRIOR_TYPE.get(test_type, OTHER_TESTS), year_bucket(prior_date)))
        if prior_date and prior_date > gfk1:
            problems.append((P_PRIOR_AFTER_GFK1,
                             f"case.priorRds[].diagnosticDate {prior_date} nach GFK1 {gfk1} ({test_type})"))
    if priors is None or priors == []:
        prior_state = PRIOR_NOT_DOCUMENTED
    elif not prior_types:
        prior_state = PRIOR_NONE_PERFORMED
    else:
        prior_state = "documented"

    recommendations, therapies, therapy_ids, management = set(), set(), set(), set()
    management_recommended = False
    for s in case.submissions:
        plan = s.get("plan")
        if not plan:
            continue
        care_plan = plan["carePlanRd"]
        recommendations |= {label for flag, label in RECOMMENDATIONS if care_plan.get(flag)}
        for t in plan.get("recommendedTherapies") or []:
            therapies.add((t["type"], t["strategy"]))
            therapy_ids.add(t.get("identifier") or json.dumps(t, sort_keys=True))
        descriptions = set(care_plan.get("clinicalManagementDescriptions") or [])
        management |= descriptions
        management_recommended |= bool(care_plan.get("clinicalManagementRecommended")) or bool(descriptions)

    codings = diagnosis.get("diagnoses") or []
    orpha = next((c["code"] for c in codings if c.get("system") == ORPHANET), None)
    orpha_code = re.search(r"\d+", orpha).group() if orpha and re.search(r"\d+", orpha) else None

    follow_ups = (latest.get("followUp") or {}).get("followUpRds") or []
    follow_up = max(follow_ups, key=lambda f: f.get("followUpDate") or "", default=None)
    case_fields = {
        "case.diagnosisRd.diagnosisGmfcs": diagnosis.get("diagnosisGmfcs") is not None,
        "case.priorRds": bool(priors),
        "followUp": follow_up is not None,
        "followUp.diseaseProgression": None if follow_up is None else bool(follow_up.get("diseaseProgression")),
        "followUp.deathDate": (
            follow_up.get("deathDate") is not None if follow_up and follow_up.get("vitalStatus") == "deceased" else None
        ),
    }
    # the follow-ups of all submissions (a node may send only the latest one with each), each once
    fu_entries = list({json.dumps(f, sort_keys=True): f for s in case.submissions
                       for f in ((s.get("followUp") or {}).get("followUpRds") or []) if isinstance(f, dict)}.values())
    fu_dates = sorted({d for d in (readable(f.get("followUpDate")) for f in fu_entries) if d})
    gfk2 = min(d for d, _ in later) if later else None
    deceased = any(f.get("vitalStatus") == "deceased" for f in fu_entries)
    molecular = latest.get("molecular") or {}
    variants = [
        {name: v.get(name) not in (None, "", []) for name, _ in VARIANT_FIELDS}
        for kind in VARIANT_LISTS for v in molecular.get(kind) or []
    ]

    is_open = status == UNDIAGNOSED and not later
    return Evaluated(
        key                    = case.key,
        le                     = case.le,
        tan                    = meta.get("tanC"),
        status                 = status,
        case_status            = OPEN if is_open else status,
        partial                = assessment == PARTIAL,
        sex                    = {"male": "m", "female": "w", "other": "d"}.get(meta.get("gender"), "unbekannt"),
        age_group              = age_group(years, days),
        adult                  = years >= 18,
        insurance              = meta.get("coverageType") if meta.get("coverageType") in ("GKV", "PKV") else "andere",
        no_indication          = meta.get("decisionToInclude") is False,
        library_type           = library_type,
        extent                 = EXTENTS.get(diagnosis.get("diagnosticExtent"), "unbekannt"),
        consent_at_submission  = permits_research_use(meta.get("researchConsents") or [], submission_date(latest)),
        consent_at_reference   = permits_research_use(meta.get("researchConsents") or [], reference_date),
        consent_transmitted    = any(isinstance(rc.get("scope"), dict) for rc in meta.get("researchConsents") or []),
        consent_reason         = dip_reason(case),
        initial_month          = submission_date(case.initial).strftime("%Y-%m"),
        mv_sequencing_consent  = permits_mv_sequencing(meta.get("mvConsent")),
        has_gfk2               = bool(later),
        impossible_dates       = impossible,
        medical_days           = medical_days,
        documentary_days       = documentary_days,
        prior_types            = prior_types,
        prior_state            = prior_state,
        recommendations        = recommendations,
        therapies              = therapies,
        therapy_count          = len(therapy_ids),
        management             = management,
        management_recommended = management_recommended,
        orpha_code             = orpha_code,
        icd10_codes            = [c["code"] for c in codings if c.get("system") == ICD10GM],
        no_matching_code       = diagnosis.get("noMatchingCodeExists") is True,
        case_fields            = case_fields,
        variants               = variants,
        problems               = problems,
        fu_entries             = fu_entries,
        first_fu_days          = next(((d - gfk2).days for d in fu_dates if d >= gfk2), None) if gfk2 else None,
        fu_state               = follow_up_state(gfk2, fu_dates, deceased, reference_date, follow_up_months) if gfk2 else None,
    )


def disease_group(e, heads):
    """The disease group of a case, and the Orphanet head missing in HEAD_GROUP, if any"""
    if any(NEUROMUSCULAR_ICD10.match(code) for code in e.icd10_codes):
        return NEUROMUSCULAR, None
    if e.orpha_code is None:
        return UNKNOWN_NO_ORPHA, None
    if e.orpha_code not in heads:
        return UNKNOWN_NOT_LINEARISED, None
    head = heads[e.orpha_code]
    if head in HEAD_GROUP:
        return HEAD_GROUP[head], None
    return UNKNOWN_HEAD, (None if head in UNASSIGNED_HEADS else head)


# ---------------------------------------------------------------------------------------------
# Matrix: rows of (section, item), evaluated for all cases and per LE
# ---------------------------------------------------------------------------------------------

COUNT, VALUE = "Anzahl", "Tage"


@dataclass
class Row:
    section: str
    item: str
    unit: str                   # Anzahl (n of base) or Tage (a statistic)
    base_label: str = None      # what the percentage refers to
    n: object = None            # count or statistic
    base: int = None            # denominator of the percentage
    support: int = None         # number of values a statistic is computed from
    members: frozenset = None   # what a count counts (case keys, variant ids), for the complementary suppression
    of: frozenset = None        # the same of the base

    @property
    def key(self):
        return f"{self.section} | {self.item}"


def percent(n, base):
    if not base or not isinstance(n, int):
        return None
    return float((Decimal(n) * 100 / Decimal(base)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def rounded(x):
    return None if x is None else float(Decimal(x).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def quartiles(values):
    """(Q1, median, Q3), method 'inclusive' (as Excel's QUARTILE.INC)"""
    if len(values) == 1:
        return values[0], values[0], values[0]
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, q2, q3


def matrix_rows(cases, losses, problem_counts, with_groups, months=(), follow_up_months=FU_MONTHS):
    """The rows of the matrix for a set of cases; the same rows for every set"""
    rows = []
    total = len(cases)

    def add(section, item, n, base=None, base_label=None, members=None, of=None):
        rows.append(Row(section, item, COUNT, base_label, n, base, members=members, of=of))

    def count(section, item, pred, of=cases, base_label="Fälle"):
        hits = frozenset(e.key for e in of if pred(e))
        add(section, item, len(hits), len(of), base_label, hits, frozenset(e.key for e in of))

    # the diagnosis blocks count only cases that were sequenced: a case without indication or with
    # libraryType none never gets a GFK2 and would otherwise count as "offen"
    sequenced = [e for e in cases if not e.no_indication and e.library_type != "none"]

    s = "Fälle"
    add(s, "Fallzahl gesamt (Episoden mit Erstmeldung)", total, members=frozenset(e.key for e in cases))
    add(s, "davon nicht sequenziert (keine Indikation oder libraryType none, nicht im Diagnosestatus)", total - len(sequenced),
        members=frozenset(e.key for e in cases) - frozenset(e.key for e in sequenced))
    add(s, "Einreichungen nicht konvertierbar (nicht in den Fällen)", losses.get(P_NOT_CONVERTED, 0))
    add(s, "davon Episode mit älterem Stand ausgewertet (eine spätere Einreichung nicht konvertierbar)", losses.get(STALE, 0))
    add(s, "Episoden ohne Erstmeldung (nicht in den Fällen)", losses.get(P_WITHOUT_INITIAL, 0))
    add(s, "Fälle nicht auswertbar (nicht in den Fällen)", losses.get(P_UNREADABLE, 0))

    s = "Einwilligung"
    count(s, "Broad Consent gültig zum Datum der letzten Einreichung", lambda e: e.consent_at_submission)
    count(s, "Broad Consent gültig zum Stichtag", lambda e: e.consent_at_reference)
    count(s, "MV-Einwilligung Sequenzierung (mvSequencing: permit)", lambda e: e.mv_sequencing_consent)
    missing = [e for e in cases if not e.consent_transmitted]
    s = "Broad Consent nicht übermittelt: Grund laut DIP"
    count(s, "Fälle ohne übermittelten Broad Consent", lambda e: not e.consent_transmitted)
    for label in REASON_LABELS:
        count(s, label, lambda e: e.consent_reason == label, missing, "Fälle ohne übermittelten Broad Consent")
    s = "Broad Consent nicht übermittelt: Grund laut DIP, nach Monat der Erstmeldung"
    for month in months:
        of_month = [e for e in missing if e.initial_month == month]
        for label in REASON_LABELS[:-1]:
            count(s, f"{month}: {label}", lambda e: e.consent_reason == label, of_month,
                  f"Fälle ohne übermittelten Broad Consent, Erstmeldung {month}")
    s = "Broad Consent nach Altersgruppe (zum Datum der letzten Einreichung)"
    for group in AGE_GROUPS:
        count(s, group, lambda e: e.consent_at_submission, [e for e in cases if e.age_group == group], f"Fälle {group}")

    s = "Geschlecht"
    for sex in ("m", "w", "d", "unbekannt"):
        count(s, sex, lambda e: e.sex == sex)

    s = "Alter bei Einschluss (GFK1)"
    for group in AGE_GROUPS:
        count(s, group, lambda e: e.age_group == group)

    s = "Versicherung"
    for insurance in ("GKV", "PKV", "andere"):
        count(s, insurance, lambda e: e.insurance == insurance)

    s = "Indikation"
    count(s, "Keine Indikation zur Genomsequenzierung (metaData.decisionToInclude = false)", lambda e: e.no_indication)

    for s, attr in (("Bearbeitungsdauer medizinisch (GFK1 bis GFK2)", "medical_days"),
                    ("Bearbeitungsdauer dokumentarisch (GFK2 bis Einreichung beim KDK)", "documentary_days")):
        days = sorted(getattr(e, attr) for e in cases if getattr(e, attr) is not None)
        count(s, "Dauer berechenbar", lambda e: getattr(e, attr) is not None)
        count(s, "nicht berechenbar: noch keine GFK2", lambda e: not e.has_gfk2)
        count(s, "nicht berechenbar: unmögliches Datum (siehe Rückfragen)", lambda e: e.has_gfk2 and e.impossible_dates)
        q1, median, q3 = quartiles(days) if days else (None, None, None)
        for item, value in (("Mittelwert", statistics.mean(days) if days else None), ("Median", median),
                            ("1. Quartil", q1), ("3. Quartil", q3),
                            ("Minimum", min(days, default=None)), ("Maximum", max(days, default=None))):
            rows.append(Row(s, f"{item} (Tage)", VALUE, n=rounded(value), support=len(days)))

    s = "Sequenzierung (libraryType)"
    for code, label in {**GENOME_LIBRARIES, **OTHER_LIBRARIES}.items():
        count(s, label, lambda e: e.library_type == code)

    genome = [e for e in cases if e.library_type in GENOME_LIBRARIES]
    s = "WGS-Seq. Art (Fälle mit WGS/lrWGS)"
    for extent in ("Single", "Duo", "Trio"):
        count(s, extent, lambda e: e.extent == extent, genome, "Fälle mit WGS/lrWGS")

    s = "Diagnosestatus (alle Fälle)"
    for status in CASE_STATUSES:
        label = f"{OPEN} (Undiagnostiziert, noch keine GFK2)" if status == OPEN else status
        count(s, label, lambda e: e.case_status == status, sequenced, "sequenzierte Fälle")

    closed = [e for e in sequenced if e.case_status != OPEN]
    s = "Diagnoserate (abgeschlossene Fälle)"
    for status in STATUSES:
        count(s, status, lambda e: e.status == status, closed, "abgeschlossene Fälle")
    count(s, "davon partiell (unter Unklar)", lambda e: e.partial, closed, "abgeschlossene Fälle")

    s = "Diagnoserate nach WGS-Seq. Art (abgeschlossene Fälle mit WGS/lrWGS)"
    for extent in ("Single", "Duo", "Trio"):
        of_extent = [e for e in closed if e.library_type in GENOME_LIBRARIES and e.extent == extent]
        base_label = f"abgeschlossene {extent}-Genome"
        count(s, f"{extent}: Diagnose", lambda e: e.status == DIAGNOSED, of_extent, base_label)
        count(s, f"{extent}: davon partiell (unter Unklar)", lambda e: e.partial, of_extent, base_label)

    s = "Diagnosestatus nach Altersgruppe"
    for group in AGE_GROUPS:
        of_group = [e for e in sequenced if e.age_group == group]
        closed_group = [e for e in of_group if e.case_status != OPEN]
        for status in STATUSES:
            count(s, f"{group}: {status}", lambda e: e.status == status, closed_group, f"abgeschlossene Fälle {group}")
        count(s, f"{group}: {OPEN}", lambda e: e.case_status == OPEN, of_group, f"Fälle {group}")

    for status in CASE_STATUSES:
        of_status = [e for e in sequenced if e.case_status == status]
        base_label = f"Fälle {status}"
        s = f"Vorbefunde, Status {status} (Mehrfachnennung möglich)"
        for prior_type in PRIOR_TYPES:
            count(s, prior_type, lambda e: any(t == prior_type for t, _ in e.prior_types), of_status, base_label)
        for state in (PRIOR_NONE_PERFORMED, PRIOR_NOT_DOCUMENTED):
            count(s, state, lambda e: e.prior_state == state, of_status, base_label)
        s = f"Vorbefunde nach Jahr, Status {status}"
        for prior_type in PRIOR_TYPES:
            for bucket in YEAR_BUCKETS:
                count(s, f"{prior_type}: {bucket}", lambda e: (prior_type, bucket) in e.prior_types, of_status, base_label)

    s = "Weiterempfehlungen"
    for _, label in RECOMMENDATIONS:
        count(s, label, lambda e: label in e.recommendations)

    s = "Therapieempfehlungen (Fälle, Mehrfachnennung möglich)"
    for category, category_label in THERAPY_CATEGORY.items():
        for kind in THERAPY_KINDS:
            count(s, f"{category_label} {kind}",
                  lambda e: any(c == category and THERAPY_KIND.get(st, NOT_ASSIGNABLE) == kind for c, st in e.therapies))
    s = "Therapieempfehlungen nach Strategie (Fälle, Mehrfachnennung möglich)"
    for category, category_label in THERAPY_CATEGORY.items():
        for strategy in THERAPY_KIND:
            count(s, f"{category_label} {strategy}", lambda e: (category, strategy) in e.therapies)
    add("Therapieempfehlungen (Anzahl Empfehlungen)", "Empfehlungen gesamt", sum(e.therapy_count for e in cases))

    s = "Weitere Empfehlungen Krankheitsmanagement (Mehrfachnennung möglich)"
    for code, label in MANAGEMENT:
        count(s, label, lambda e: code in e.management)
    count(s, MANAGEMENT_UNSPECIFIED, lambda e: e.management_recommended and not e.management)
    count(s, MANAGEMENT_NOT_RECOMMENDED, lambda e: not e.management_recommended)

    s = "Orphacode"
    count(s, "Erkr. ohne Orphacode (keine Orphanet-Kodierung)", lambda e: e.orpha_code is None)
    count(s, "ohne Orphacode, davon noMatchingCodeExists = true", lambda e: e.orpha_code is None and e.no_matching_code)
    count(s, "mit Orphacode, aber noMatchingCodeExists = true", lambda e: e.orpha_code is not None and e.no_matching_code)

    if with_groups:
        for s, block in zip(DISEASE_GROUP_BLOCKS, (cases, [e for e in cases if e.adult], [e for e in cases if not e.adult])):
            for group in DISEASE_GROUPS:
                of_group = [e for e in block if e.group == group]
                count(s, group, lambda e: e.group == group, block, f"Fälle ({s})")
                for status in CASE_STATUSES:
                    count(s, f"{group}: {status}", lambda e: e.case_status == status, of_group, f"Fälle {group}")

    with_gfk2 = [e for e in cases if e.fu_state is not None]
    s = FU_SECTION
    count(s, "mit Follow-up (followUpRds, alle Einreichungen)", lambda e: bool(e.fu_entries), with_gfk2, "Fälle mit GFK2")
    for state in FU_STATES:
        count(s, f"Stand am Stichtag (Intervall {follow_up_months} Monate): {state}", lambda e: e.fu_state == state,
              with_gfk2, "Fälle mit GFK2")
    count(s, "Follow-up aktuell, von fälligen", lambda e: e.fu_state == FU_CURRENT,
          [e for e in with_gfk2 if e.fu_state in (FU_CURRENT, FU_OVERDUE)], "Fälle mit fälligem Follow-up")
    s = FU_DAYS_SECTION
    count(s, "Dauer berechenbar (Follow-up ab GFK2)", lambda e: e.first_fu_days is not None, with_gfk2, "Fälle mit GFK2")
    days = sorted(e.first_fu_days for e in with_gfk2 if e.first_fu_days is not None)
    q1, median, q3 = quartiles(days) if days else (None, None, None)
    for item, value in (("Mittelwert", statistics.mean(days) if days else None), ("Median", median),
                        ("1. Quartil", q1), ("3. Quartil", q3),
                        ("Minimum", min(days, default=None)), ("Maximum", max(days, default=None))):
        rows.append(Row(s, f"{item} (Tage)", VALUE, n=rounded(value), support=len(days)))
    s = FU_FIELDS_SECTION
    entries = {(e.key, i): f for e in cases for i, f in enumerate(e.fu_entries)}
    all_entries = frozenset(entries)
    add(s, "Follow-up-Einträge gesamt", len(entries), members=all_entries)

    def entry_count(item, pred, of=all_entries, base_label="Follow-up-Einträge"):
        hits = frozenset(k for k in of if pred(entries[k]))
        add(s, item, len(hits), len(of), base_label, hits, frozenset(of))
    entry_count("followUpDate lesbar", lambda f: readable(f.get("followUpDate")) is not None)
    entry_count("vitalStatus living oder deceased", lambda f: f.get("vitalStatus") in ("living", "deceased"))
    entry_count("diagnosisEstablished angegeben (true/false)", lambda f: isinstance(f.get("diagnosisEstablished"), bool))
    entry_count("deathDate, von Einträgen mit vitalStatus deceased", lambda f: bool(f.get("deathDate")),
                frozenset(k for k, f in entries.items() if f.get("vitalStatus") == "deceased"), "Einträge verstorben")

    s = "Füllgrad optionaler Felder (Fälle)"
    for name, base_label in CASE_FIELDS:
        applicable = [e for e in cases if e.case_fields[name] is not None]
        count(s, name, lambda e: e.case_fields[name], applicable, base_label)
    variants = {(e.key, i): v for e in cases for i, v in enumerate(e.variants)}
    all_variants = frozenset(variants)
    s = "Füllgrad optionaler Felder (Varianten der letzten Einreichung)"
    add(s, "Varianten gesamt", len(variants), members=all_variants)
    for name, label in VARIANT_FIELDS:
        filled = frozenset(k for k, v in variants.items() if v[name])
        add(s, f"{label} ({name})", len(filled), len(variants), "Varianten", filled, all_variants)

    s = PROBLEMS_SECTION
    for problem in PROBLEMS:
        add(s, problem, problem_counts.get(problem, 0))

    return rows


def suppressed(row, min_cell, hide_value=None, hide_base=None):
    """(value, base, percent) of a row, a suppressed count or statistic replaced by '<min_cell', its percentage left
    out. What to hide is decided by the caller (render); None: by size alone
    (a count 1..min_cell-1, a statistic from fewer values), as rd_time_series uses it"""
    hidden = f"<{min_cell}"
    if row.unit == VALUE:
        if hide_value is None:
            hide_value = row.n is not None and row.support < min_cell
        return (hidden if row.n is not None and hide_value else row.n), None, None
    n, base = row.n, row.base
    if hide_value is None:
        hide_value = 0 < n < min_cell
    if hide_base is None:
        hide_base = base is not None and 0 < base < min_cell
    pct = percent(n, base)
    if hide_value:
        n, pct = hidden, None
    if base is not None and hide_base:
        base, pct = hidden, None
    return n, base, pct


def suppression_note(min_cell, n):
    """The note on the suppressed cells (sheet Hinweise)"""
    if min_cell <= 1:
        return "Keine Zellen unterdrückt (--min-cell 1)."
    return (f"Unterdrückt sind nur Fallzahlen von 1 bis {min_cell - 1} (<{min_cell}) in den Diagnosezeilen (Erkrankungsgruppen "
            "mit Status, Diagnose ohne Erkrankungsgruppe, Orphacode) der LEs; keine Sekundärunterdrückung, einzelne Werte "
            "können sich aus Summen ergeben (Gesamt, die Summen der Gruppen, die übrigen Zeiträume). Gesamt und alle "
            f"anderen Zahlen sind exakt angegeben, auch unter {min_cell}: auch Diagnosestatus, Diagnoserate und die "
            f"Vorbefunde je Status, alle Anteile und Bearbeitungsdauern ({n} Zellen unterdrückt).")


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def run(rows, failures, heads, start, end, reference_date, min_cell=5, min_medical_days=14,
        suppress_scope=SUPPRESS_CODES, follow_up_months=FU_MONTHS):
    """The matrix of the cases with initial submission in [start, end) (None: open), suppressed (primary only)"""
    ev = evaluate(rows, failures, heads, start, end, reference_date, min_medical_days, suppress_scope, follow_up_months)
    return render(ev, min_cell)


def run_joint(rows, failures, heads, periods, reference_date, min_cell=5, min_medical_days=14,
              suppress_scope=SUPPRESS_CODES, follow_up_months=FU_MONTHS, start=None, end=None):
    """The total matrix over [start, end) and one matrix per period (start, end) of periods, each suppressed on its
    own (primary only). Returns (total result, [result per period])."""
    periods = list(periods)
    if not periods or any(a[1] != b[0] for a, b in zip(periods, periods[1:])):
        raise ValueError("run_joint: the periods must be contiguous and in order")
    if (start or periods[0][0]) != periods[0][0] or (end or periods[-1][1]) != periods[-1][1]:
        raise ValueError("run_joint: the total matrix must cover exactly the periods (start, end)")
    args = (reference_date, min_cell, min_medical_days, suppress_scope, follow_up_months)
    return (run(rows, failures, heads, periods[0][0], periods[-1][1], *args),
            [run(rows, failures, heads, s, e, *args) for s, e in periods])


def evaluate(rows, failures, heads, start, end, reference_date, min_medical_days=14, suppress_scope=SUPPRESS_CODES,
             follow_up_months=FU_MONTHS):
    """The rows of every LE and Gesamt (entities) and what the notes need, before the suppression"""
    if suppress_scope not in SUPPRESS_SCOPES:
        raise ValueError(f"suppress_scope must be one of {SUPPRESS_SCOPES}: {suppress_scope}")
    cases, tests, without_initial, submitters = build_cases(rows, start, end)

    problems = []   # dicts LE, Problem, Fall, Einreichung (tanC), Detail

    def problem(le, kind, case, tan, detail):
        problems.append({"le": le, "problem": kind, "case": case, "tan": tan, "detail": detail})

    evaluated = []
    for case in cases:
        try:
            evaluated.append(evaluate_case(case, reference_date, min_medical_days, follow_up_months))
        except (KeyError, TypeError, ValueError) as error:
            problem(case.le, P_UNREADABLE, case.key, case.latest.get("metaData", {}).get("tanC"), repr(error))

    unmapped_heads = set()
    if heads is not None:
        for e in evaluated:
            e.group, unmapped = disease_group(e, heads)
            if unmapped:
                unmapped_heads.add(unmapped)
            if e.status == DIAGNOSED and e.group in UNKNOWN_GROUPS:
                orpha = f"ORPHA:{e.orpha_code}" if e.orpha_code else "kein Orphacode"
                e.problems.append((P_DIAGNOSIS_WITHOUT_GROUP, f"{e.group.split(': ', 1)[1]} ({orpha})"))

    for e in evaluated:
        for kind, detail in e.problems:
            problem(e.le, kind, e.key, e.tan, detail)
    for le, key, tan in without_initial:
        problem(le, P_WITHOUT_INITIAL, key, tan, "nur Folgemeldungen/Ergänzungen/Korrekturen, keine Erstmeldung "
                "(oder eine Erstmeldung ohne lesbares metaData.submission.date)")
    evaluated_keys = {e.key for e in evaluated}
    stale = Counter()
    for f in failures:
        if f.case in evaluated_keys:
            # a later submission of an evaluated episode: the case is evaluated with an older state
            stale[str(f.site or "?")] += 1
        problem(str(f.site or "?"), P_NOT_CONVERTED, f.case, f.tan,
                ("Episode mit älterem Stand ausgewertet; " if f.case in evaluated_keys else "") + f.error[:300])

    les = sorted({e.le for e in evaluated} | {p["le"] for p in problems})
    # Rückfragen counted in cases (submissions for P_NOT_CONVERTED): a case with two problems of a kind counts once
    case_problems = {le: Counter() for le in les}
    for p in {(p["le"], p["problem"], p["case"], p["tan"] if p["problem"] == P_NOT_CONVERTED else None) for p in problems}:
        case_problems[p[0]][p[1]] += 1
    losses_by_le = {le: {k: case_problems[le][k] for k in (P_NOT_CONVERTED, P_WITHOUT_INITIAL, P_UNREADABLE)} for le in les}

    def losses(selected):
        out = {k: sum(losses_by_le[le][k] for le in selected) for k in (P_NOT_CONVERTED, P_WITHOUT_INITIAL, P_UNREADABLE)}
        out[STALE] = sum(stale[le] for le in selected)
        return out

    def counts(selected):
        return sum((case_problems[le] for le in selected), Counter())

    with_groups = heads is not None
    # the reasons by month only where the export has them, else the section would be all zeros
    reasons_exported = any(e.consent_reason != REASON_NOT_EXPORTED for e in evaluated)
    months = sorted({e.initial_month for e in evaluated}) if reasons_exported else ()
    entities = {TOTAL: matrix_rows(evaluated, losses(les), counts(les), with_groups, months, follow_up_months)}
    for le in les:
        entities[le] = matrix_rows([e for e in evaluated if e.le == le], losses([le]), counts([le]), with_groups, months,
                                   follow_up_months)

    if start or end:
        until = date.fromordinal(end.toordinal() - 1) if end else None
        period = f"{start.strftime('%d.%m.%Y') if start else '…'} - {until.strftime('%d.%m.%Y') if until else '…'}"
    else:
        dates = [submission_date(c.initial) for c in cases]
        period = (f"gesamter Export (Erstmeldungen {min(dates).strftime('%d.%m.%Y')} - {max(dates).strftime('%d.%m.%Y')})"
                  if dates else "gesamter Export")

    del rows, failures
    return SimpleNamespace(**{k: v for k, v in locals().items() if not callable(v) or k in ("losses", "counts")})


def render(ev, min_cell):
    """The result (JSON) of an evaluation: in the LE columns, the counts of 1 to min_cell - 1 of the code cells
    (is_code_cell) suppressed, nothing else"""
    entities, suppress_scope, reference_date = ev.entities, ev.suppress_scope, ev.reference_date
    les, submitters, tests, period, start, end, heads = ev.les, ev.submitters, ev.tests, ev.period, ev.start, ev.end, ev.heads
    min_medical_days, follow_up_months, problems = ev.min_medical_days, ev.follow_up_months, ev.problems
    reasons_exported, unmapped_heads = ev.reasons_exported, ev.unmapped_heads
    template_rows = entities[TOTAL]
    n_hidden = 0

    def small_code(name, r, field_, n):
        return name != TOTAL and n is not None and 0 < n < min_cell and r.unit == COUNT and is_code_cell(r, field_)
    result_rows = []
    for i, r in enumerate(template_rows):
        values = {}
        for name, entity_rows in entities.items():
            row = entity_rows[i]
            hide_value, hide_base = small_code(name, r, "value", row.n), small_code(name, r, "base", row.base)
            n_hidden += hide_value + hide_base
            n, base, pct = suppressed(row, min_cell, hide_value, hide_base)
            values[name] = {"value": n, "base": base, "percent": pct}
        result_rows.append({"key": r.key, "section": r.section, "item": r.item, "unit": r.unit,
                            "base_label": r.base_label, "values": values})

    labels = {le: f"{le} ({', '.join(sorted(submitters[le]))})" if le in submitters else le for le in les}

    notes = [
        f"{result_rows[0]['values'][TOTAL]['value']} Fälle (Episoden mit Erstmeldung) ausgewertet, Stand je Fall aus der "
        "letzten Nicht-Test-Einreichung; Spezifikation: docs/rd-evaluation-matrix.txt.",
        f"{tests} Testeinreichungen verworfen.",
        suppression_note(min_cell, n_hidden),
        "Diagnosestatus 'offen': Undiagnostiziert und noch keine Fallkonferenz nach dem Indikationsboard (GFK2); "
        "die Diagnoserate bezieht sich auf die abgeschlossenen Fälle. 'Undiagnostiziert' zählt daher nur Fälle mit GFK2: "
        "alle Fälle mit noGeneticDiagnosis sind Undiagnostiziert + offen; LEs mit unterschiedlichem Anteil offener Fälle "
        "nur so oder über die Diagnoserate vergleichen.",
        f"Broad Consent bewertet zum Datum der letzten Einreichung und zum Stichtag {reference_date.isoformat()}.",
        "Therapieempfehlungen: Zuordnung Strategie -> Kategorie (offen, mit den Stakeholdern abzustimmen): "
        + ", ".join(f"{k} -> {v}" for k, v in THERAPY_KIND.items()) + ". Ein Fall zählt je Zeile einmal, "
        "mit mehreren Empfehlungen ggf. in mehreren Zeilen; die Zeilen summieren sich daher nicht zur Fallzahl.",
        "Krankheitsmanagement: über alle Einreichungen des Falls, jede genannte Kategorie einmal je Fall "
        "(das DIP liefert je Versorgungsplan genau eine Kategorie).",
        "Vorbefunde: jeder Vortest in case.priorRds zählt (je Art und Jahr einmal je Fall). Das DIP übermittelt "
        "höchstens einen Vorbefund je Fall (den letzten Befund vor dem Indikationsboard) und immer mit Datum.",
        f"Medizinische Bearbeitungsdauer unter {min_medical_days} Tagen wird als unplausibel kurz zurückgefragt.",
        FU_NOTE + f" Stichtag {reference_date.isoformat()}, Intervall {follow_up_months} Monate (--follow-up-months): fällig ab "
        "einem Intervall nach der GFK2, aktuell mit einem followUpDate im Intervall vor dem Stichtag, sonst überfällig; "
        "Verstorbene (vitalStatus deceased) sind nicht mehr fällig. Follow-ups aller Einreichungen, jedes einmal.",
        f"LE: Standort des Exports, sonst metaData.submission.submitterId. LEs: " + ", ".join(labels[le] for le in les) + ".",
    ]
    if start or end:
        notes.append(f"Fälle mit Erstmeldung vom {start or '…'} bis vor dem {end or '…'}.")
    if heads is None:
        notes.append("Erkrankungsgruppen nicht ausgewertet: keine Orphanet-Linearisierung angegeben (--orphanet).")
    if not reasons_exported:
        notes.append("Grund für einen fehlenden Broad Consent: der Export enthält die Spalte reasonResearchConsentMissing nicht "
                     "(export-datenkranz ab der Fassung mit dieser Spalte); die Zeilen stehen unter 'nicht im Export', "
                     "die Aufteilung nach Monat entfällt.")
    else:
        notes.append("Grund für einen fehlenden Broad Consent: metadata.reasonResearchConsentMissing der letzten DIP-Einreichung "
                     "(Spalte des Exports); das Mapping in den Datenkranz übernimmt ihn nicht. Nach Monat: Monat der Erstmeldung.")
    if unmapped_heads:
        # on stderr for the KDK, not in the notes: naming a head would show that its cases occur, which the
        # suppressed rows hide ("unbekannt: Gruppenkopf" may be <5 for 0)
        print(f"{period}: Orphanet-Gruppenköpfe ohne Vorlagengruppe, gezählt unter 'unbekannt: Gruppenkopf': "
              + ", ".join(sorted(unmapped_heads)) + "; HEAD_GROUP ergänzen.", file=sys.stderr)

    return {
        "title": f"AUSWERTUNG {period}",
        "period": period,
        "min_cell": min_cell,
        "suppress_scope": suppress_scope,
        # every suppressed cell counts 1 to min_cell - 1: no complementary suppression
        "suppressed": {"cells": n_hidden, "complementary": 0},
        "entities": [TOTAL] + les,
        "labels": {TOTAL: TOTAL, **labels},
        "rows": result_rows,
        "problems": sorted(problems, key=lambda p: (p["le"], PROBLEMS.index(p["problem"]), p["case"])),
        "notes": notes,
    }


def write_csv(path, result, delimiter=";"):
    """Long table: one line per LE and row of the matrix"""
    def fmt(x):
        return str(x).replace(".", ",") if isinstance(x, float) and delimiter == ";" else ("" if x is None else x)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["LE", "Abschnitt", "Kategorie", "Einheit", "Wert", "Bezug", "Basis", "Anteil (%)"])
        for entity in result["entities"]:
            for r in result["rows"]:
                v = r["values"][entity]
                writer.writerow([result["labels"][entity], r["section"], r["item"], r["unit"],
                                 fmt(v["value"]), r["base_label"] or "", fmt(v["base"]), fmt(v["percent"])])


def write_problems_csv(path, result, delimiter=";"):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["LE", "Problem", "Fall", "Einreichung (tanC)", "Detail"])
        for p in result["problems"]:
            writer.writerow([result["labels"].get(p["le"], p["le"]), p["problem"], p["case"], p["tan"] or "", p["detail"]])


def write_rueckfragen(output, result, delimiter=";", per_le=False):
    """The Rückfragen: <output>_rueckfragen.csv of all LEs, or per_le only <output>_rueckfragen_<LE>.csv per LE (each
    LE gets only its own case keys). Returns the paths written"""
    if not per_le:
        write_problems_csv(f"{output}_rueckfragen.csv", result, delimiter)
        return [f"{output}_rueckfragen.csv"]
    paths = per_le_paths(output, result["entities"][1:])
    for le, path in paths.items():
        write_problems_csv(path, {**result, "problems": [p for p in result["problems"] if p["le"] == le]}, delimiter)
    return list(paths.values())


def per_le_paths(output, les):
    """{LE: <output>_rueckfragen_<LE>.csv}, the LE with characters other than letters, digits, . _ - as _; refuses
    two LEs with the same file (also differing only in case, as on macOS and Windows): one would get the other's
    case keys"""
    paths = {le: f"{output}_rueckfragen_{re.sub(r'[^A-Za-z0-9._-]', '_', str(le))}.csv" for le in les}
    seen = {}
    for le, path in paths.items():
        other = seen.setdefault(path.lower(), le)
        if other != le:
            raise SystemExit(f"LEs {other!r} and {le!r} would write the same file {path}; rename one (site of the export)")
    return paths


def write_json(path, result, per_le=False):
    """The result JSON; with per_le without the Rückfragen (case keys of all LEs), so the xlsx has none either"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items() if not (per_le and k == "problems")}, f, ensure_ascii=False, indent=2)


def write_outputs(output, result, delimiter=";", per_le=False):
    """The JSON, the CSV and the Rückfragen of a result, as main writes them; a file of them written before an error
    is removed again. Returns the paths"""
    paths = [f"{output}.json", f"{output}.csv"] + (list(per_le_paths(output, result["entities"][1:]).values())
                                                   if per_le else [f"{output}_rueckfragen.csv"])
    try:
        write_json(paths[0], result, per_le)
        write_csv(paths[1], result, delimiter)
        write_rueckfragen(output, result, delimiter, per_le)
    except BaseException:
        remove_files(paths)
        raise
    return paths


def remove_files(paths):
    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="result JSON of export-datenkranz, or a JSON list of submissions")
    parser.add_argument("--orphanet", help="Orphadata linearisation, en_product7.xml (needed for the disease groups)")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="initial submitted on or after (YYYY-MM-DD)")
    parser.add_argument("--until", dest="end", type=date.fromisoformat, help="initial submitted before (YYYY-MM-DD)")
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date.today(),
                        help="date the research consents are additionally evaluated at (default: today)")
    parser.add_argument("--min-cell", type=int, default=5,
                        help="counts below are suppressed, 1 disables (default: %(default)s)")
    parser.add_argument("--suppress-scope", type=scope_argument, choices=SUPPRESS_SCOPES, default=SUPPRESS_CODES,
                        help="small counts are suppressed only in the rows derived from the diagnosis codes "
                             "(CODE_SECTIONS), all other numbers exact (the only scope; default: %(default)s)")
    parser.add_argument("--min-medical-days", type=int, default=14,
                        help="shorter GFK1-GFK2 durations are listed for clarification (default: %(default)s)")
    parser.add_argument("--follow-up-months", type=int, default=FU_MONTHS,
                        help="follow-up interval: due one interval after GFK2, current with a follow-up in the interval "
                             "before the reference date (default: %(default)s)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s, for Excel with German locale)")
    parser.add_argument("--output", default="rd-evaluation-matrix", help="output path without extension (default: %(default)s)")
    parser.add_argument("--rueckfragen-per-le", action="store_true",
                        help="write the Rückfragen only per LE, <output>_rueckfragen_<LE>.csv, not the file of all LEs, "
                             "and leave them out of the JSON (and so of the xlsx); recommended")
    args = parser.parse_args(argv)

    rows, failures = load_submissions(args.input)
    heads = load_linearisation(args.orphanet) if args.orphanet else None
    result = run(rows, failures, heads, args.start, args.end, args.reference_date, args.min_cell, args.min_medical_days,
                 args.suppress_scope, args.follow_up_months)

    written = write_outputs(args.output, result, args.delimiter, args.rueckfragen_per_le)

    for note in result["notes"]:
        print(note, file=sys.stderr)
    n_problems = len(result["problems"])
    print(f"{n_problems} Rückfragen an LEs, siehe {args.output}_rueckfragen*.csv", file=sys.stderr)
    print("Written: " + ", ".join(written), file=sys.stderr)


if __name__ == "__main__":
    main()
