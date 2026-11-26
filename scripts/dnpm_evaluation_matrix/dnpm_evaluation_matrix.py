#!/usr/bin/env python3
"""
Oncology evaluation matrix (Auswertematrix MTB / KDK-Onko) from BfArM Datenkranz submissions of the
Modellvorhaben Genomsequenzierung (§ 64e SGB V), in total, per Leistungserbringer (LE) and per month
or quarter. The counterpart of scripts/rd_evaluation_matrix/rd_evaluation_matrix.py (rare diseases),
with the same input and the same JSON structure. Standard library only, Python 3.10 or later.

Metric definitions, schema paths and what can't be computed: README.md next to this script.

Input, either
  - the result JSON of the evaluation module's "export-datenkranz" (table "datenkranz", useCase MTB),
    cases keyed by (site, patient, episodeOfCare); the optional column reasonResearchConsentMissing
    (the DIP's reason for a missing broad consent) is read if present, or
  - a JSON list of {"case": <any case key>, "submittedAt": <ISO datetime>, "content": <Datenkranz>,
    optional "site", optional "reasonResearchConsentMissing"}, one entry per submission, for KDKs with
    their own export; cases are keyed by (site or submitterId, case).
Only submissions with metaData.submission.diseaseType oncological or hereditary are evaluated.

A case is an episode with an initial submission; its state is the one of its latest non-test
submission (submitted up to --cutoff). --from/--until select the cases by the date of their initial
submission, and the submissions of section "Einreichungen" by their own date.

Output (<output> without extension):
  <output>.json               every value, in total and per LE, notes, and under "time_series" the same
                              rows per month or quarter (no case keys)
  <output>.csv                long table LE | Abschnitt | Kategorie | Einheit | Wert | Bezug | Basis | Anteil
  <output>_zeitreihe.csv      long table LE | Zeitraum | Abschnitt | Kategorie | Einheit | Wert | Bezug | Basis | Anteil
  <output>_rueckfragen.csv    cases to clarify with their LE (contains case keys of all LEs: only for the KDK);
                              with --rueckfragen-per-le (recommended) instead one <output>_rueckfragen_<LE>.csv
                              per LE and no file of all LEs
No cell is suppressed: every count and statistic is exact, also below 5 (decision of the data holders, the
oncology matrix is for the data nodes and the LE concerned, not for publication; the RD matrix is suppressed).
The follow-up sections after the network's MTB follow-up SOP (FU_SECTIONS: category, due / current / overdue at
--reference-date, days from GFK2 to the first follow-up, mandatory fields, response) are described in README.md.

Example:
  python3 dnpm_evaluation_matrix.py export-datenkranz_2026-11-01_10-00-00.json \\
    --from 2025-07-01 --until 2026-11-01 --reference-date 2026-11-01 --resolution month --output onko-matrix
"""

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace


# ---------------------------------------------------------------------------------------------
# Labels and code values (bfarm-schema-dto-model oncology 1.0.7 / base, BfArM KDK schema)
# ---------------------------------------------------------------------------------------------

TOTAL = "Gesamt"
WHOLE = "gesamt"                    # the period of all cases in the time series
MONTH, QUARTER = "month", "quarter"
ONCO_DISEASE_TYPES = ("oncological", "hereditary")    # metaData.submission.diseaseType

SUBMISSION_TYPES = ("initial", "followup", "addition", "correction", "test")

LIBRARIES = (("wgs", "WGS"), ("wgs_lr", "lrWGS"), ("wes", "Exom (WES)"), ("panel", "Panel"),
             ("none", "keine Sequenzierung (none)"))
SEQUENCED = {"wgs", "wgs_lr", "wes", "panel"}
OTHER = "anderer Wert (nicht im Schema)"
MISSING = "nicht angegeben"

PAEDIATRIC = "Pädiatrisch (0-17 Jahre)"
AGE_GROUPS = (PAEDIATRIC, "18-39 Jahre", "40-59 Jahre", "60-79 Jahre", "80+ Jahre")

ECOG = ("0", "1", "2", "3", "4", "5", "unknown", "notApplicable")
GRADING = (
    ("0", "0 (primär erworbene Melanose)"), ("1", "1 (gut differenziert)"), ("2", "2 (mäßig differenziert)"),
    ("3", "3 (schlecht differenziert)"), ("4", "4 (undifferenziert)"), ("5", "5 (nur C61)"),
    ("X", "X (nicht bestimmbar)"), ("L", "L (low grade)"), ("M", "M (intermediate grade)"),
    ("H", "H (high grade)"), ("B", "B (Borderline)"), ("U", "U (unbekannt)"), ("T", "T (trifft nicht zu)"),
)
BEHAVIOUR = (("0", "/0 gutartig"), ("1", "/1 unsicheres Verhalten"), ("2", "/2 in situ"),
             ("3", "/3 bösartig (Primärtumor)"), ("6", "/6 bösartig (Metastase)"),
             ("9", "/9 bösartig, unklar ob primär"))
BEHAVIOUR_CODES = frozenset(code for code, _ in BEHAVIOUR)


def behaviour_code(histology):
    """The ICD-O-3 behaviour code of a morphology code (8140/3 -> 3), None without one of BEHAVIOUR_CODES (no "/", or
    an unknown character after it): the behaviour rows then add up to the cases with a behaviour code"""
    code = histology.split("/")[-1].strip()[:1] if "/" in histology else None
    return code if code in BEHAVIOUR_CODES else None


M_STATES = ("M0", "M1 (Fernmetastasen)", "MX", "TNM ohne erkennbare M-Kategorie", "kein TNM")

# Tumour entity groups by the ICD-10-GM code of case.diagnosisOd.mainDiagnosis: (label, letter, ranges
# of the two-digit number or None for any); the first matching group wins
ENTITY_GROUPS = (
    ("Kopf-Hals (C00-C14, C30-C32)",                       "C", ((0, 14), (30, 32))),
    ("Ösophagus, Magen (C15-C16)",                         "C", ((15, 16),)),
    ("Kolorektal, Anus (C18-C21)",                         "C", ((18, 21),)),
    ("Leber, Gallenwege (C22-C24)",                        "C", ((22, 24),)),
    ("Pankreas (C25)",                                     "C", ((25, 25),)),
    ("Lunge (C33-C34)",                                    "C", ((33, 34),)),
    ("Knochen, Weichteile, Mesotheliom (C40-C41, C45-C49)", "C", ((40, 41), (45, 49))),
    ("Melanom (C43)",                                      "C", ((43, 43),)),
    ("Mamma (C50)",                                        "C", ((50, 50),)),
    ("Gynäkologisch (C51-C58)",                            "C", ((51, 58),)),
    ("Prostata (C61)",                                     "C", ((61, 61),)),
    ("Niere, Harnwege (C64-C68)",                          "C", ((64, 68),)),
    ("ZNS, Auge (C69-C72)",                                "C", ((69, 72),)),
    ("Schilddrüse, endokrine Drüsen (C73-C75)",            "C", ((73, 75),)),
    ("CUP, ungenau bezeichnet, sekundär (C76-C80)",        "C", ((76, 80),)),
    ("Hämatologisch (C81-C96, D45-D47)",                   "C", ((81, 96),)),
    ("Hämatologisch (C81-C96, D45-D47)",                   "D", ((45, 47),)),
    ("Sonstige bösartige (übrige C-Codes)",                "C", None),
    ("In situ (D00-D09)",                                  "D", ((0, 9),)),
    ("Gutartig, unsicheres Verhalten (D10-D44, D48)",      "D", None),
)
NO_CD_CODE = "kein C/D-Code (ICD-10-GM)"
ENTITY_LABELS = tuple(dict.fromkeys(label for label, _, _ in ENTITY_GROUPS)) + (NO_CD_CODE, MISSING)

PRIOR_TYPES = ("genomeShortRead", "genomeLongRead", "exome", "panel", "single", "array", "karyotyping", "other", "none")
TREATMENT_TYPES = (("O", "O (ohne Bezug zu einer Operation)"), ("A", "A (adjuvant)"), ("N", "N (neoadjuvant)"),
                   ("I", "I (intraoperativ)"), ("S", "S (sonstiges)"))
INTENTIONS = (("K", "K (kurativ)"), ("P", "P (palliativ)"), ("S", "S (sonstiges)"), ("X", "X (keine Angabe)"))

EVIDENCE = ("m1A", "m1B", "m1C", "m2A", "m2B", "m2C", "m3", "m4", "undefined")
EVIDENCE_DETAILS = ("is", "iv", "Z", "Z(FDA)", "R")
THERAPY_TYPES = ("inLabel", "offLabel", "compassionateUse", "secPreventive", "unknown")
STRATEGIES = (
    ("CH", "CH Chemotherapie"), ("HO", "HO Hormontherapie"), ("IM", "IM Immun-/Antikörpertherapie"),
    ("ZS", "ZS zielgerichtete Substanzen"), ("CI", "CI Chemo- + Immuntherapie"),
    ("CZ", "CZ Chemotherapie + zielgerichtet"), ("CIZ", "CIZ Chemo- + Immuntherapie + zielgerichtet"),
    ("IZ", "IZ Immuntherapie + zielgerichtet"), ("SZ", "SZ Stammzelltransplantation"), ("SO", "SO Sonstiges"),
)
REGISTERS = ("NCT", "DRKS", "Eudra-CT/CTIS", "other", "yesButStudyIsUnknown")
CARE_PLAN_FLAGS = (
    ("studyRecommended",        "Studienteilnahme empfohlen (studyRecommended)"),
    ("counsellingRecommended",  "Humangenetische Beratung empfohlen (counsellingRecommended)"),
    ("reEvaluationRecommended", "Re-Evaluation empfohlen (reEvaluationRecommended)"),
    ("interventionRecommended", "Operation empfohlen (interventionRecommended)"),
)
OTHER_RECOMMENDATIONS = (
    ("WW", "WW Watchful Waiting"), ("AS", "AS Active Surveillance"), ("WS", "WS Wait and see"),
    ("OP", "OP Operation"), ("ST", "ST Strahlentherapie"), ("KW", "KW keine weitere tumorspezifische Therapie"),
    ("SO", "SO Sonstiges"),
)
TERMINATION = (
    ("E", "E reguläres Ende"), ("R", "R reguläres Ende mit Dosisreduktion"), ("W", "W reguläres Ende mit Substanzwechsel"),
    ("A", "A Abbruch wegen Nebenwirkungen"), ("P", "P Abbruch wegen Progress"), ("S", "S Abbruch aus sonstigen Gründen"),
    ("V", "V Patient verweigert weitere Therapie"), ("T", "T Patient verstorben"), ("U", "U unbekannt"),
)
RESPONSES = (("CR", "CR komplette Remission"), ("PR", "PR partielle Remission"), ("SD", "SD stabile Erkrankung"),
             ("PD", "PD Progression"))
TMB_HIGH = 10.0             # mut/Mb, the common cut-off (e.g. FDA approval of pembrolizumab, KEYNOTE-158)
FOLLOW_UP_MONTHS = (4, 8, 12, 18, 24)   # the intervals of the stakeholders' query document
QS12_FROM = date(2026, 6, 1)            # QS durch KDK 01.2, criterion 12
# Follow-up schedule after the MTB (GFK2) as defined by the network's MTB follow-up SOP: per category of a case
# (key, label, interval in months) the interval in which a follow-up is due
FU_STARTED, FU_NOT_STARTED, FU_NO_RECOMMENDATION = "1", "2", "3"
FU_CATEGORIES = (
    (FU_STARTED, "Kategorie 1: empfohlene Therapie begonnen", 3),
    (FU_NOT_STARTED, "Kategorie 2: Therapie- oder Studienempfehlung, nicht begonnen", 3),
    (FU_NO_RECOMMENDATION, "Kategorie 3: keine Therapie- oder Studienempfehlung (kein Target)", 12),
)
FU_NOT_STARTED_MONTHS = 24      # a recommendation not started is followed up for 2 years, then as category 3
FU_LOST_MONTHS = 12             # no contact for a year: lost to follow-up
FU_RESPONSE_WEEKS = 8           # first staging 6-8 weeks after the start of a therapy
FU_DECEASED, FU_NOT_DUE, FU_LOST, FU_CURRENT, FU_OVERDUE = (
    "verstorben (kein Follow-up mehr fällig)", "noch nicht fällig (GFK2 weniger als ein Intervall vor dem Stichtag)",
    f"lost to follow-up (nicht verstorben, kein Kontakt in den {FU_LOST_MONTHS} Monaten vor dem Stichtag; nicht fällig)",
    "fällig, aktuell (Follow-up im Intervall vor dem Stichtag)", "fällig, überfällig (kein Follow-up im Intervall vor dem Stichtag)")
FU_STATES = (FU_DECEASED, FU_NOT_DUE, FU_LOST, FU_CURRENT, FU_OVERDUE)
FU_SECTION = "Follow-up nach SOP-Intervall (sequenzierte Fälle mit GFK2)"
FU_CATEGORY_SECTION = "Follow-up nach SOP-Intervall je Kategorie"
FU_DAYS_SECTION = "Dauer GFK2 bis erstes Follow-up"
FU_FIELDS_SECTION = "Follow-up: Pflichtfelder nach SOP (Follow-up-Einträge und -Therapien)"
FU_RESPONSE_SECTION = "Follow-up: Ansprechen, Allgemeinzustand und Vitalstatus (sequenzierte Fälle mit GFK2)"
FU_NOTE = "Follow-up nach dem MTB-Follow-up-SOP des Netzwerks (Abschnitte Follow-up nach SOP-Intervall und folgende):"
FU_SECTIONS = (FU_SECTION, FU_CATEGORY_SECTION, FU_DAYS_SECTION, FU_FIELDS_SECTION, FU_RESPONSE_SECTION)

# Research provisions of the broad consent, as Consent.permitsResearchUse in service-base
RESEARCH_PROVISIONS = (
    "2.16.840.1.113883.3.1937.777.24.5.3.7",  # MDAT_STORE_AND_PROCESS
    "2.16.840.1.113883.3.1937.777.24.5.3.8",  # MDAT_RESEARCH_USE
    "2.16.840.1.113883.3.1937.777.24.5.3.1",  # PATDAT_STORE_AND_USE
)

# metaData.researchConsents[].noScopeJustification, and the DIP's metadata.reasonResearchConsentMissing
# (service-base BroadConsent.ReasonMissing) with the value it corresponds to (commented-out mapping in
# bfarm-schema-dto-model base Mappings.scala)
JUSTIFICATIONS = (
    ("patient unable to consent",                                             "Patient nicht einwilligungsfähig"),
    ("patient refuses to sign consent",                                       "Patient lehnt ab"),
    ("patient did not return consent documents",                              "Einwilligung nicht zurückgegeben"),
    ("other patient-related reason",                                          "anderer patientenbezogener Grund"),
    ("consent information cannot be submitted by LE due to technical reason", "technische Gründe (ab 01.06.2026 unzulässig)"),
    ("consent is not implemented at LE due to organizational issues",         "organisatorische Gründe (ab 01.06.2026 unzulässig)"),
)
NOT_PATIENT_RELATED = set(j for j, _ in JUSTIFICATIONS[4:])
DIP_REASONS = {
    "patient-inability":     "patient unable to consent",
    "patient-refusal":       "patient refuses to sign consent",
    "consent-not-returned":  "patient did not return consent documents",
    "other-patient-reason":  "other patient-related reason",
    "technical-issues":      "consent information cannot be submitted by LE due to technical reason",
    "organizational-issues": "consent is not implemented at LE due to organizational issues",
}
CONSENT_VALID = "Broad Consent übermittelt, gültig"
CONSENT_INVALID = "Broad Consent übermittelt, nicht gültig (deny, abgelaufen, ohne Forschungs-Provision)"
REASON_OTHER = "nicht übermittelt: anderer Wert"
REASON_NONE = "nicht übermittelt: kein Grund angegeben"
CONSENT_STATES = (CONSENT_VALID, CONSENT_INVALID) + tuple(f"nicht übermittelt: {label}" for _, label in JUSTIFICATIONS) \
    + (REASON_OTHER, REASON_NONE)
NOT_EXPORTED = object()     # reason of an input without the column reasonResearchConsentMissing

# Problems to clarify with the LE, one entry per case (or submission)
P_GFK1_AFTER_SUBMISSION = "GFK1 nach dem Einreichungsdatum"
P_GFK2_AFTER_SUBMISSION = "GFK2 (MTB) nach dem Einreichungsdatum"
P_GFK2_BEFORE_GFK1 = "GFK2 vor GFK1"
P_SHORT_MEDICAL = "Medizinische Bearbeitungsdauer unplausibel kurz"
P_DIAGNOSIS_AFTER_GFK1 = "Erstdiagnose nach GFK1 (QS 7)"
P_BEFORE_BIRTH = "Datum vor dem Geburtsmonat (QS 6)"
P_BEFORE_CONSENT = "GFK1 vor der MV-Einwilligung (QS 9)"
P_AGE = "Alter unplausibel (QS 1)"
P_THERAPY_BEFORE_GFK2 = "Beginn einer empfohlenen Therapie vor GFK2"
P_DEATH = "Vitalstatus und Sterbedatum widersprüchlich"
P_STUDY_FLAG = "studyRecommended widerspricht recommendedStudies"
P_TNM = "TNM nicht genau drei Komponenten (QS 5)"
P_INCLUSION = "decisionToInclude = false, aber sequenziert"
P_NO_GDC = "genomicDataCenterId fehlt trotz Sequenzierung"
P_JUSTIFICATION = "noScopeJustification nicht mehr zulässig (QS 12)"
P_NOT_CONVERTED = "Einreichung nicht konvertierbar"
P_WITHOUT_INITIAL = "Episode ohne Erstmeldung"
P_UNREADABLE = "Fall nicht auswertbar"
P_MILESTONE_BEFORE_GFK1 = "Meilenstein vor GFK1 (Zeitstrahl)"
PROBLEMS = (P_GFK1_AFTER_SUBMISSION, P_GFK2_AFTER_SUBMISSION, P_GFK2_BEFORE_GFK1, P_SHORT_MEDICAL, P_DIAGNOSIS_AFTER_GFK1,
            P_BEFORE_BIRTH, P_BEFORE_CONSENT, P_AGE, P_THERAPY_BEFORE_GFK2, P_DEATH, P_STUDY_FLAG, P_TNM, P_INCLUSION,
            P_NO_GDC, P_JUSTIFICATION, P_MILESTONE_BEFORE_GFK1, P_NOT_CONVERTED, P_WITHOUT_INITIAL, P_UNREADABLE)
STALE = "stale"     # losses: non-convertible submissions of an evaluated episode
NO_GFK1 = "ohne GFK1: Fälle"    # losses: cases not readable for want of metaData.molecularBoardDecisionDate
LOSSES = (P_NOT_CONVERTED, P_WITHOUT_INITIAL, P_UNREADABLE, STALE, NO_GFK1)

SAME_DAY = "davon GFK2 am Tag der GFK1 (0 Tage, nicht in Mittelwert, Median und Quartilen; Rückfrage)"

# Process timeline: milestones in days relative to GFK1 (label, must lie after GFK1)
TIMELINE = "Zeitstrahl: Tage relativ zur GFK1"
MILESTONES = (("MV-Einwilligung", False), ("Erstdiagnose", False), ("GFK2 (MTB)", True),
              ("Erstmeldung beim KDK", True), ("Beginn empfohlene Therapie", True), ("Erstes Follow-up", True))


# ---------------------------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------------------------

@dataclass
class SubmissionRow:
    case: str
    submitted_at: str
    content: object
    site: str = None
    reason: object = NOT_EXPORTED   # reasonResearchConsentMissing of the export row, None if empty


@dataclass
class Failure:
    site: str
    case: str
    tan: str
    submitted_at: str
    error: str


def dig(obj, *path):
    """The value at a path of dict keys, None if a step is missing or not a dict"""
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def items(x):
    """A list as is, anything else (null, a misplaced object) as an empty list"""
    return x if isinstance(x, list) else []


def load_submissions(path):
    """The submissions of the input file, and the submissions the export couldn't convert"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "tables" in data:
        tables = {t["name"]: t for t in data["tables"]}
        if "datenkranz" not in tables:
            raise ValueError(f"{path}: no table 'datenkranz', not a result of export-datenkranz")

        def key(r):
            return json.dumps([r.get("site"), r.get("patient"), r.get("episodeOfCare")])

        exported = "reasonResearchConsentMissing" in (tables["datenkranz"].get("columns") or [])
        rows = [
            SubmissionRow(key(r), r.get("submittedAt") or "", r.get("content"), r.get("site"),
                          r.get("reasonResearchConsentMissing") if exported else NOT_EXPORTED)
            for r in tables["datenkranz"]["rows"] if r.get("useCase") in (None, "MTB")
        ]
        failures = [
            Failure(r.get("site"), key(r), r.get("tan"), r.get("submittedAt") or "", str(r.get("error") or ""))
            for r in tables.get("failures", {}).get("rows", []) if r.get("useCase") in (None, "MTB")
        ]
        return rows, failures

    if isinstance(data, list):
        exported = any(isinstance(e, dict) and "reasonResearchConsentMissing" in e for e in data)

        def key(e):
            # the LE belongs to the key: the same local case id at two LEs are two cases; sort_keys makes an
            # object key independent of the order of its fields
            le = e.get("site") or dig(e.get("content"), "metaData", "submission", "submitterId")
            return json.dumps([le, e.get("case")], sort_keys=True)

        return [SubmissionRow(key(e), e.get("submittedAt") or "", e.get("content"), e.get("site"),
                              e.get("reasonResearchConsentMissing") if exported else NOT_EXPORTED)
                for e in data if isinstance(e, dict)], []

    raise ValueError(f"{path}: neither a result of export-datenkranz nor a list of submissions")


# ---------------------------------------------------------------------------------------------
# Dates and periods
# ---------------------------------------------------------------------------------------------

def parse_date(s):
    """A date from an ISO date or datetime, None if missing or unreadable"""
    if not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def milestone_date(s):
    """A date of the timeline: an ISO date or datetime, a month (YYYY-MM) as its 15th; None if missing or unreadable"""
    if isinstance(s, str) and re.fullmatch(r"\d{4}-\d{2}", s.strip()):
        try:
            return date(int(s[:4]), int(s[5:7]), 15)
        except ValueError:
            return None
    return parse_date(s)


def required_date(s, name):
    d = parse_date(s)
    if d is None:
        raise ValueError(f"{name} fehlt oder ist kein Datum: {s!r}")
    return d


def submission_type(content):
    return dig(content, "metaData", "submission", "type")


def submission_date(row):
    """metaData.submission.date of a submission, else the date (UTC) of the time the export has for it"""
    given = parse_date(dig(row.content, "metaData", "submission", "date"))
    if given:
        return given
    kind, t, _ = submitted_order(row)
    return t.date() if kind == 0 else parse_date(row.submitted_at)


def period_of(d, resolution):
    """The month (YYYY-MM) or quarter (YYYY-Qn) of a date"""
    return f"{d.year}-{d.month:02d}" if resolution == MONTH else f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def period_start(d, resolution):
    month = d.month if resolution == MONTH else d.month - (d.month - 1) % 3
    return date(d.year, month, 1)


def add_months(d, months):
    """The same day months later (earlier if negative), the last of the month if that has fewer days"""
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m + 1, day)
        except ValueError:
            continue


def periods_between(first, last, resolution):
    """All months or quarters from the one of a date to the one of another, also those without data"""
    periods, start = [], period_start(first, resolution)
    while start <= last:
        periods.append(period_of(start, resolution))
        start = add_months(start, 1 if resolution == MONTH else 3)
    return periods


# ---------------------------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------------------------

@dataclass
class Case:
    key: str
    le: str
    rows: list          # non-test SubmissionRows, ordered by submittedAt

    @property
    def submissions(self):
        return [r.content for r in self.rows]

    @property
    def latest(self):
        return self.rows[-1].content

    @property
    def initial(self):
        return initial_submission(self)


def leistungserbringer(row):
    """The LE of a submission: the site of the export, else the submitterId"""
    if row.site:
        return str(row.site)
    return str(dig(row.content, "metaData", "submission", "submitterId") or "?")


def submitted_order(row):
    """The sort key of a submission by submittedAt, parsed (time zones to UTC), else as text after the parsed ones"""
    try:
        t = datetime.fromisoformat(str(row.submitted_at).replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone(timezone.utc).replace(tzinfo=None)
        return (0, t, "")
    except ValueError:
        return (1, datetime.min, str(row.submitted_at))


def initial_submission(case):
    """The initial submission a case is dated by: of several the one with the earliest date (metaData.submission.date,
    else the time of the export), of equal dates the first sent"""
    return min((r for r in case.rows if submission_type(r.content) == "initial"),
               key=lambda r: (submission_date(r) or date.max, submitted_order(r)))


def meaningful_plan(plan):
    """A plan with content: a non-empty carePlanOd or recommendations ({} and {"carePlanOd": {}} are none)"""
    return isinstance(plan, dict) and bool((isinstance(plan.get("carePlanOd"), dict) and plan["carePlanOd"])
                                           or items(plan.get("recommendedSystemicTherapies")) or items(plan.get("recommendedStudies")))


def build_cases(rows):
    """
    The cases (episodes with an initial submission, whatever its date), the test submissions, the
    episodes without initial as Case, the unreadable submissions and the submitterIds by LE
    """
    by_key, tests, unreadable, submitters = {}, [], [], {}
    for row in rows:
        submission = dig(row.content, "metaData", "submission")
        if not isinstance(submission, dict):
            unreadable.append(row)
            continue
        if submission.get("diseaseType") not in ONCO_DISEASE_TYPES:
            continue
        if submission.get("type") == "test":
            tests.append(row)
            continue
        by_key.setdefault(row.case, []).append(row)
        if row.site and submission.get("submitterId"):
            submitters.setdefault(str(row.site), set()).add(str(submission["submitterId"]))

    cases, without_initial = [], []
    for key, subs in by_key.items():
        subs = sorted(subs, key=submitted_order)     # stable: equal times keep the input order
        case = Case(key, leistungserbringer(subs[0]), subs)
        (cases if any(submission_type(s) == "initial" for s in case.submissions) else without_initial).append(case)
    return cases, tests, without_initial, unreadable, submitters


def age(birth_month, at):
    """Full years at a date, the birth date given by month taken as its first day"""
    born = birth_date(birth_month)
    return at.year - born.year - ((at.month, at.day) < (born.month, born.day))


def birth_date(birth_month):
    if not isinstance(birth_month, str) or not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", birth_month):
        raise ValueError(f"metaData.birthDate fehlt oder ist kein Monat: {birth_month!r}")
    return date(int(birth_month[:4]), int(birth_month[5:7]), 1)


def age_group(years):
    if years < 0:
        return MISSING
    if years <= 17:
        return PAEDIATRIC
    if years <= 39:
        return "18-39 Jahre"
    if years <= 59:
        return "40-59 Jahre"
    if years <= 79:
        return "60-79 Jahre"
    return "80+ Jahre"


def minus_years(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


def permits_research_use(research_consents, reference_date):
    """As Consent.permitsResearchUse of service-base, on the FHIR Consents in researchConsents[].scope"""

    def has_code(provision, code):
        return any(isinstance(c, dict) and c.get("code") == code
                   for cc in items(provision.get("code")) for c in items(dig(cc, "coding")))

    def find(consent, code):
        top = consent.get("provision") if isinstance(consent.get("provision"), dict) else {}
        if has_code(top, code):
            return top
        return next((p for p in items(top.get("provision")) if isinstance(p, dict) and has_code(p, code)), None)

    def permitted(provision):
        start, end = parse_date(dig(provision, "period", "start")), parse_date(dig(provision, "period", "end"))
        return (provision.get("type") == "permit" and start is not None
                and minus_years(reference_date, 5) < start <= reference_date and (end is None or end >= reference_date))

    decisions = {}
    for rc in items(research_consents):
        consent = dig(rc, "scope")
        if not isinstance(consent, dict):
            continue
        for code in RESEARCH_PROVISIONS:
            if decisions.get(code) is False:  # a denial wins
                continue
            provision = find(consent, code)
            if provision is None:
                decisions.pop(code, None)
            elif provision.get("type") == "permit" and (parse_date(dig(provision, "period", "start")) or date.min) > reference_date:
                continue    # a permit that starts after the date is not in effect yet: it neither permits nor denies
            else:
                decisions[code] = permitted(provision)
    return any(decisions.values())


def mv_sequencing_scope(mv_consent):
    """The latest metaData.mvConsent.scope entry of the domain mvSequencing, None without"""
    scopes = [s for s in items(dig(mv_consent, "scope")) if isinstance(s, dict) and s.get("domain") == "mvSequencing"]
    return max(scopes, key=lambda s: str(s.get("date") or ""), default=None)


def consent_state(meta, reason, at):
    """
    The broad consent of a submission as one of CONSENT_STATES: transmitted (a FHIR Consent in
    researchConsents[].scope) and valid at a date, transmitted and not valid, or the reason it is
    missing: noScopeJustification of the latest entry, else the DIP's reasonResearchConsentMissing
    """
    consents = items(meta.get("researchConsents"))
    if any(isinstance(dig(rc, "scope"), dict) for rc in consents):
        return (CONSENT_VALID if permits_research_use(consents, at) else CONSENT_INVALID), False
    given = [(str(rc.get("presentationDate") or ""), i, rc["noScopeJustification"])
             for i, rc in enumerate(consents) if isinstance(rc, dict) and rc.get("noScopeJustification")]
    from_dip = False
    if given:
        justification = max(given)[2]
    elif reason is not NOT_EXPORTED and reason:
        justification, from_dip = DIP_REASONS.get(reason, reason), True
    else:
        return REASON_NONE, False
    label = dict(JUSTIFICATIONS).get(justification)
    return (f"nicht übermittelt: {label}" if label else REASON_OTHER), from_dip


def entity_group(code):
    """The entity group of an ICD-10-GM code (ENTITY_GROUPS)"""
    if not isinstance(code, str) or not code.strip():
        return MISSING
    m = re.match(r"([A-Z])(\d{2})", code.strip().upper())
    if not m:
        return NO_CD_CODE
    letter, number = m.group(1), int(m.group(2))
    for label, group_letter, ranges in ENTITY_GROUPS:
        if letter == group_letter and (ranges is None or any(lo <= number <= hi for lo, hi in ranges)):
            return label
    return NO_CD_CODE


def m_state(tnm):
    """The M category of the TNM codings (display, text or code such as 'cM1a (UICC)'), as one of M_STATES"""
    if not tnm:
        return "kein TNM"
    for coding in tnm:
        for text in (dig(coding, "display"), dig(coding, "text"), dig(coding, "code")):
            m = re.search(r"(?:^|[^A-Za-z])[cpyra]{0,3}M([01X])", str(text or ""))
            if m:
                return {"0": "M0", "1": "M1 (Fernmetastasen)", "X": "MX"}[m.group(1)]
    return "TNM ohne erkennbare M-Kategorie"


def filled(x):
    return x not in (None, "", [], {})


def rec_id(rec):
    return str(rec.get("identifier")) if rec.get("identifier") else json.dumps(rec, sort_keys=True)


def evaluate_case(case, reference_date, min_medical_days):
    """The facts of a case the matrix counts; raises on what makes the case unreadable"""
    latest = case.latest
    meta = latest.get("metaData")
    if not isinstance(meta, dict):
        raise ValueError("metaData fehlt")
    diagnosis = dig(latest, "case", "diagnosisOd")
    if not isinstance(diagnosis, dict):
        raise ValueError("case.diagnosisOd fehlt")
    problems = []

    def submitted(s):
        return parse_date(dig(s, "metaData", "submission", "date"))

    gfk1 = required_date(meta.get("molecularBoardDecisionDate"), "metaData.molecularBoardDecisionDate")
    born = birth_date(meta.get("birthDate"))
    years = age(meta["birthDate"], gfk1)
    if not 0 <= years <= 130:
        problems.append((P_AGE, f"{years} Jahre bei Einschluss (metaData.birthDate {meta['birthDate']}, GFK1 {gfk1})"))

    # every submission against its own date: a later submission must not hide an impossible date of an earlier one
    impossible = False
    for s in case.submissions:
        board, at = parse_date(dig(s, "metaData", "molecularBoardDecisionDate")), submitted(s)
        if board and at and board > at:
            impossible = True
            problems.append((P_GFK1_AFTER_SUBMISSION,
                             f"metaData.molecularBoardDecisionDate {board} nach metaData.submission.date {at} "
                             f"({(board - at).days} Tage, Einreichung {dig(s, 'metaData', 'tanC')})"))
            break

    # GFK2 (second case conference, the MTB; as plan.carePlanRd.molecularBoardDecisionDate in the RD script):
    # plan.carePlanOd.molecularBoardDecisionDate of the first submission that carries one. The DIP mapping writes
    # the latest therapy-board care plan at the time of each submission (MTBMappings: maxByOption(issuedOn)), so
    # this is the latest MTB before the first submission with a plan, not necessarily the very first MTB
    mtbs = [(parse_date(dig(s, "plan", "carePlanOd", "molecularBoardDecisionDate")), s)
            for s in case.submissions if isinstance(s.get("plan"), dict)]
    gfk2, first_reporting = next(((d, s) for d, s in mtbs if d), (None, None))   # submissions are ordered by time
    if gfk2:
        at = submitted(first_reporting)
        if at and gfk2 > at:
            impossible = True
            problems.append((P_GFK2_AFTER_SUBMISSION,
                             f"plan.carePlanOd.molecularBoardDecisionDate {gfk2} nach metaData.submission.date {at} "
                             f"({(gfk2 - at).days} Tage, Einreichung {dig(first_reporting, 'metaData', 'tanC')})"))
        if gfk2 < gfk1:
            impossible = True
            problems.append((P_GFK2_BEFORE_GFK1, f"GFK2 {gfk2} vor GFK1 {gfk1}"))
    medical_days = documentary_days = None
    if gfk2 and not impossible:
        medical_days = (gfk2 - gfk1).days
        at = submitted(first_reporting)
        documentary_days = (at - gfk2).days if at else None
        if medical_days < min_medical_days:
            problems.append((P_SHORT_MEDICAL, f"GFK1 {gfk1} bis GFK2 {gfk2}: {medical_days} Tage "
                             f"(< {min_medical_days}), obwohl Sequenzierung und Auswertung dazwischenliegen"))

    main = diagnosis.get("mainDiagnosis") if isinstance(diagnosis.get("mainDiagnosis"), dict) else {}
    diagnosis_date = parse_date(main.get("date"))
    diagnosis_days = None
    if diagnosis_date:
        if diagnosis_date > gfk1:
            problems.append((P_DIAGNOSIS_AFTER_GFK1, f"mainDiagnosis.date {diagnosis_date} nach GFK1 {gfk1}"))
        else:
            diagnosis_days = (gfk1 - diagnosis_date).days

    library = diagnosis.get("libraryType")
    library = library if library in dict(LIBRARIES) else (MISSING if library is None else OTHER)
    included = meta.get("decisionToInclude") is not False
    sequenced = included and library in SEQUENCED
    if not included and library in SEQUENCED:
        problems.append((P_INCLUSION, f"metaData.decisionToInclude = false, case.diagnosisOd.libraryType = {library}"))
    if library in SEQUENCED and not dig(meta, "submission", "genomicDataCenterId"):
        problems.append((P_NO_GDC, f"libraryType {library} ohne metaData.submission.genomicDataCenterId"))

    mv_scope = mv_sequencing_scope(meta.get("mvConsent"))
    mv_date = parse_date(dig(mv_scope, "date"))
    if mv_date and gfk1 < mv_date:
        problems.append((P_BEFORE_CONSENT, f"GFK1 {gfk1} vor mvConsent.scope[mvSequencing].date {mv_date}"))

    tnm = items(diagnosis.get("tnmClassifications"))
    if tnm and len(tnm) != 3:
        problems.append((P_TNM, f"case.diagnosisOd.tnmClassifications mit {len(tnm)} Einträgen"))

    priors = [p for p in items(dig(latest, "case", "priorDiagnostics")) if isinstance(p, dict)]
    procedures = [p for p in items(dig(latest, "case", "priorProcedures")) if isinstance(p, dict)]

    # plan and molecular: the latest submission that has them (each submission carries the whole case); an empty
    # plan or care plan ({} from a partial submission) is no plan and doesn't displace an earlier one
    plan = next((s["plan"] for s in reversed(case.submissions) if meaningful_plan(s.get("plan"))), None)
    care_plan = dig(plan, "carePlanOd") if isinstance(dig(plan, "carePlanOd"), dict) else {}
    therapies = [r for r in items(dig(plan, "recommendedSystemicTherapies")) if isinstance(r, dict)]
    studies = [r for r in items(dig(plan, "recommendedStudies")) if isinstance(r, dict)]
    if plan is not None and bool(care_plan.get("studyRecommended")) != bool(studies):
        problems.append((P_STUDY_FLAG, f"studyRecommended = {care_plan.get('studyRecommended')}, "
                         f"{len(studies)} recommendedStudies"))
    molecular = next((s["molecular"] for s in reversed(case.submissions) if isinstance(s.get("molecular"), dict)), None)

    # follow-ups of all submissions (a node may send only the latest one), the latest by followUpDate
    follow_ups = [(parse_date(f.get("followUpDate")), i, f) for i, s in enumerate(case.submissions)
                  for f in items(dig(s, "followUp", "followUpOds")) if isinstance(f, dict)]
    follow_up = max(follow_ups, key=lambda x: (x[0] or date.min, x[1]), default=(None, None, None))[2]
    # each follow-up once (a submission may repeat the earlier ones), and the dates of a contact
    fu_entries = list({json.dumps(f, sort_keys=True): f for _, _, f in follow_ups}.values())
    # the dates of the follow-ups and contacts, a month (YYYY-MM) as its 15th as on the timeline, sorted; the same
    # for the state (current, lost) and the days to the first follow-up
    fu_dates = sorted({d for d in (milestone_date(f.get("followUpDate")) for _, _, f in follow_ups) if d})
    contact_dates = sorted(set(fu_dates) | {d for d in (milestone_date(f.get("lastContactDate")) for f in fu_entries) if d})
    deceased = any(f.get("vitalStatus") == "deceased" for _, _, f in follow_ups)
    death_dates = [parse_date(f.get("deathDate")) for _, _, f in follow_ups if f.get("deathDate")]
    # deceased at the reference date: a follow-up up to it (or undated) with a death up to it (or undated); a later
    # export evaluated at an earlier date doesn't change the state then
    deceased_at = any(f.get("vitalStatus") == "deceased" and (d is None or d <= reference_date)
                      and (parse_date(f.get("deathDate")) or date.min) <= reference_date for d, _, f in follow_ups)
    if follow_up is not None and (deceased and not death_dates or death_dates and not deceased):
        problems.append((P_DEATH, "vitalStatus deceased ohne deathDate" if deceased else "deathDate bei vitalStatus living"))

    # therapies of the follow-ups over all submissions; implemented: the reference is a recommendation. The DIP
    # gives a therapy without recommendation a new random reference with every conversion, so those are told
    # apart by start and substances instead
    recommended = {rec_id(r) for r in therapies} | {rec_id(r) for r in studies}
    fu_therapies = {}
    for _, _, f in follow_ups:
        for t in items(f.get("therapies")):
            if isinstance(t, dict):
                linked = str(t.get("reference")) in recommended
                fu_therapies[(str(t.get("reference")) if linked else json.dumps(t.get("substances"), sort_keys=True),
                              str(t.get("therapyStartDate")))] = t
    implemented = [t for t in fu_therapies.values() if str(t.get("reference")) in recommended]
    starts = sorted(d for d in (parse_date(t.get("therapyStartDate")) for t in implemented) if d)
    start_days = None
    if starts and gfk2:
        if starts[0] < gfk2:
            problems.append((P_THERAPY_BEFORE_GFK2, f"therapyStartDate {starts[0]} vor GFK2 {gfk2}"))
        elif not impossible:
            start_days = (starts[0] - gfk2).days

    # process timeline: each milestone in days from GFK1, None if the case has no date for it
    mv_dates = [milestone_date(sc.get("date")) for sc in items(dig(meta, "mvConsent", "scope"))
                if isinstance(sc, dict) and sc.get("domain") == "mvSequencing"]
    initial_row = initial_submission(case)
    therapy_starts = [d for d in (milestone_date(t.get("therapyStartDate")) for t in implemented) if d]
    milestone_dates = {
        "MV-Einwilligung": min((d for d in mv_dates if d), default=None),
        "Erstdiagnose": milestone_date(main.get("date")),
        "GFK2 (MTB)": gfk2,
        "Erstmeldung beim KDK": submission_date(initial_row),
        "Beginn empfohlene Therapie": min(therapy_starts, default=None),
        "Erstes Follow-up": min(fu_dates, default=None),
    }
    timeline = {name: (d - gfk1).days if d else None for name, d in milestone_dates.items()}
    early_milestones = [f"{name} {milestone_dates[name]}" for name, after in MILESTONES
                        if after and timeline[name] is not None and timeline[name] < 0]
    if early_milestones:
        problems.append((P_MILESTONE_BEFORE_GFK1, ", ".join(early_milestones) + f" vor GFK1 {gfk1}"))

    # QS 6: no date before the birth month
    dates = [("metaData.molecularBoardDecisionDate", gfk1), ("mainDiagnosis.date", diagnosis_date), ("plan.carePlanOd.molecularBoardDecisionDate", gfk2)]
    dates += [("followUpDate", d) for d, _, _ in follow_ups] + [("therapyStartDate", d) for d in starts]
    dates += [("priorProcedures[].therapyStartDate", parse_date(p.get("therapyStartDate"))) for p in procedures]
    early = [(name, d) for name, d in dates if d and d < born]
    if early:
        problems.append((P_BEFORE_BIRTH, ", ".join(f"{name} {d}" for name, d in early) + f" vor Geburtsmonat {born}"))

    state, from_dip = consent_state(meta, case.rows[-1].reason, submitted(latest) or reference_date)
    # QS 12 on the reason in effect: noScopeJustification, and the DIP's reasonResearchConsentMissing where the matrix
    # takes the reason from it
    justifications = {rc.get("noScopeJustification") for rc in items(meta.get("researchConsents")) if isinstance(rc, dict)}
    dip_reason = case.rows[-1].reason
    if submitted(latest) and submitted(latest) >= QS12_FROM:
        given = sorted(j for j in justifications & NOT_PATIENT_RELATED if j)
        if given:
            problems.append((P_JUSTIFICATION, "noScopeJustification: " + ", ".join(given)))
        elif from_dip and DIP_REASONS.get(dip_reason) in NOT_PATIENT_RELATED:
            problems.append((P_JUSTIFICATION, f"reasonResearchConsentMissing (DIP): {dip_reason}"))

    complex_biomarkers = [b for b in items(dig(molecular, "complexBiomarkers")) if isinstance(b, dict)]
    tmbs = [b["tmb"] for b in complex_biomarkers if isinstance(b.get("tmb"), (int, float)) and not isinstance(b.get("tmb"), bool)]
    hrd = [b.get("hrdHigh") for b in complex_biomarkers if isinstance(b.get("hrdHigh"), bool)]
    small = [v for v in items(dig(molecular, "smallVariants")) if isinstance(v, dict)]
    cnvs = [v for v in items(dig(molecular, "copyNumberVariants")) if isinstance(v, dict)]
    histology = str(dig(diagnosis, "histology", "code") or "")
    topography = str(dig(diagnosis, "topography", "code") or "")
    meta_submission = meta.get("submission") if isinstance(meta.get("submission"), dict) else {}

    return SimpleNamespace(
        key=case.key, le=case.le, tan=meta.get("tanC"),
        hereditary=meta_submission.get("diseaseType") == "hereditary",
        types=Counter(submission_type(s) for s in case.submissions),
        sex={"male": "m", "female": "w", "other": "d"}.get(meta.get("gender"), "unbekannt"),
        age_group=age_group(years),
        initial_date=None,      # set by run
        insurance=meta.get("coverageType") if meta.get("coverageType") in ("GKV", "PKV") else "andere",
        included=included, library=library, sequenced=sequenced,
        mv_consent=dig(mv_scope, "type") == "permit",
        consent=state, consent_from_dip=from_dip,
        consent_at_reference=permits_research_use(meta.get("researchConsents"), reference_date),
        entity=entity_group(main.get("code")),
        topography_matches=(topography[:3].upper() == str(main.get("code") or "")[:3].upper()) if topography else None,
        topography=topography, main_code=str(main.get("code") or ""),
        behaviour=behaviour_code(histology),
        ecog=diagnosis.get("ecogPerformanceStatusScore"),
        grading=diagnosis.get("grading"),
        m_state=m_state(tnm), tnm=bool(tnm), additional_classification=bool(items(diagnosis.get("additionalClassification"))),
        germline=diagnosis.get("germlineDiagnosisConfirmed") is True,
        germline_codes=bool(items(diagnosis.get("germlineDiagnoses"))),
        additional_diagnoses=bool(items(diagnosis.get("additionalDiagnoses"))),
        prior_types={p.get("type") for p in priors}, priors=bool(priors),
        procedures=procedures,
        has_plan=bool(care_plan), care_plan=care_plan, therapies=therapies, studies=studies,
        other_recommendations=set(items(care_plan.get("otherRecommendations"))),
        has_molecular=molecular is not None, small=small, cnvs=cnvs,
        tmb=max(tmbs) if tmbs else None, hrd=(True in hrd) if hrd else None,
        biomarker_fields={f: any(b.get(f) is not None for b in complex_biomarkers)
                          for f in ("ploidy", "tmb", "hrdHigh", "lstHigh", "taiHigh")},
        germline_variants=any(v.get("genomicSource") == "germline" for v in small + cnvs),
        follow_up_submissions=sum(1 for s in case.submissions if submission_type(s) == "followup"),
        follow_up=follow_up, deceased=deceased_at, death_date=bool(death_dates),
        fu_therapies=list(fu_therapies.values()), implemented=implemented,
        implemented_ids={str(t.get("reference")) for t in implemented},
        unlinked=[t for t in fu_therapies.values() if str(t.get("reference")) not in recommended],
        medical_days=medical_days, documentary_days=documentary_days, diagnosis_days=diagnosis_days,
        start_days=start_days, impossible=impossible, has_gfk2=gfk2 is not None, timeline=timeline,
        gfk2=gfk2, fu_entries=fu_entries, fu_dates=fu_dates, contact_dates=contact_dates,
        first_fu_days=next(((d - gfk2).days for d in fu_dates if d >= gfk2), None) if gfk2 else None,
        problems=problems,
    )


def follow_up_category(e):
    """The category of a case with GFK2 in the follow-up schedule (FU_CATEGORIES)"""
    if e.implemented:
        return FU_STARTED
    return FU_NOT_STARTED if e.therapies or e.studies else FU_NO_RECOMMENDATION


def follow_up_interval(e, reference_date):
    """The follow-up interval in months of a case with GFK2 at the reference date"""
    category = follow_up_category(e)
    if category == FU_NOT_STARTED and add_months(e.gfk2, FU_NOT_STARTED_MONTHS) <= reference_date:
        return dict((k, m) for k, _, m in FU_CATEGORIES)[FU_NO_RECOMMENDATION]
    return dict((k, m) for k, _, m in FU_CATEGORIES)[category]


def within(dates, months, reference_date):
    """A date in the months before the reference date (up to it): (reference date - months, reference date], the
    months counted back from the reference date, so that a month end doesn't shorten the window"""
    since = add_months(reference_date, -months)
    return any(since < d <= reference_date for d in dates)


def follow_up_state(e, reference_date):
    """
    The follow-up state of a case with GFK2 at the reference date (FU_STATES). As the SOP has it, the follow-up ends
    with lost to follow-up: a living case whose GFK2 is at least FU_LOST_MONTHS before the reference date and that has
    no contact (followUpDate or lastContactDate) in these months is lost, not due and not overdue
    """
    months = follow_up_interval(e, reference_date)
    if e.deceased:
        return FU_DECEASED
    if add_months(e.gfk2, months) > reference_date:
        return FU_NOT_DUE
    if add_months(e.gfk2, FU_LOST_MONTHS) <= reference_date and not within(e.contact_dates, FU_LOST_MONTHS, reference_date):
        return FU_LOST
    return FU_CURRENT if within(e.fu_dates, months, reference_date) else FU_OVERDUE


def submission_fact(row, resolution):
    """What section "Einreichungen" counts of a submission (tests included)"""
    content = row.content
    meta = dig(content, "metaData") or {}
    kind = submission_type(content)
    d = submission_date(row)
    fu = [f for f in items(dig(content, "followUp", "followUpOds")) if isinstance(f, dict)]
    library = dig(content, "case", "diagnosisOd", "libraryType")
    state = consent_state(meta, row.reason, d or date.today())[0] if kind == "initial" else None
    return SimpleNamespace(
        le=leistungserbringer(row), date=d, period=period_of(d, resolution) if d else None,
        type=kind if kind in SUBMISSION_TYPES else OTHER,
        death=any(f.get("deathDate") for f in fu), last_contact=any(f.get("lastContactDate") for f in fu),
        therapy_start=any(isinstance(t, dict) and t.get("therapyStartDate") for f in fu for t in items(f.get("therapies"))),
        library=library if library in dict(LIBRARIES) else (MISSING if library is None else OTHER),
        recommendation=bool(items(dig(content, "plan", "recommendedSystemicTherapies"))),
        consent=state,
    )


# ---------------------------------------------------------------------------------------------
# Matrix: rows of (section, item), evaluated for a set of cases and submissions
# ---------------------------------------------------------------------------------------------

COUNT, DAYS, PER_CASE, TMB_UNIT = "Anzahl", "Tage", "Anzahl je Fall", "mut/Mb"


@dataclass
class Row:
    section: str
    item: str
    unit: str                   # Anzahl (n of base), else the unit of a statistic
    base_label: str = None      # what the percentage refers to
    n: object = None            # count or statistic
    base: int = None            # denominator of the percentage
    support: int = None         # number of values a statistic is computed from

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


def matrix_rows(cases, subs, losses, problem_counts, reference_date):
    """The rows of the matrix for a set of cases and submissions; the same rows for every set"""
    rows = []

    def add(section, item, n, base=None, base_label=None):
        rows.append(Row(section, item, COUNT, base_label, n, base))

    def count(section, item, pred, of=cases, base_label="Fälle"):
        add(section, item, sum(1 for e in of if pred(e)), len(of), base_label)

    def stats(section, label, values, unit):
        values = sorted(values)
        q1, median, q3 = quartiles(values) if values else (None, None, None)
        for item, value in (("Mittelwert", statistics.mean(values) if values else None), ("Median", median),
                            ("1. Quartil", q1), ("3. Quartil", q3),
                            ("Minimum", min(values, default=None)), ("Maximum", max(values, default=None))):
            rows.append(Row(section, f"{label}{item} ({unit})", unit, n=rounded(value), support=len(values)))

    sequenced = [e for e in cases if e.sequenced]
    with_mtb = [e for e in sequenced if e.has_plan]
    seq_label, mtb_label = "sequenzierte Fälle", "sequenzierte Fälle mit GFK2"

    s = "Fälle"
    add(s, "Fallzahl gesamt (Episoden mit Erstmeldung)", len(cases))
    count(s, "davon hereditär (diseaseType hereditary)", lambda e: e.hereditary)
    count(s, "davon nicht eingeschlossen (decisionToInclude = false)", lambda e: not e.included)
    count(s, "davon eingeschlossen, nicht sequenziert (libraryType none oder fehlt)",
          lambda e: e.included and not e.sequenced)
    count(s, "davon sequenziert (eingeschlossen, libraryType wgs/wgs_lr/wes/panel)", lambda e: e.sequenced)
    for kind, label in (("followup", "Folgemeldung"), ("addition", "Nachmeldung"), ("correction", "Korrektur")):
        count(s, f"Fälle mit mindestens einer {label} ({kind})", lambda e: e.types[kind] > 0)
    add(s, "Einreichungen nicht konvertierbar (nicht in den Fällen)", losses[P_NOT_CONVERTED])
    add(s, "davon Episode mit älterem Stand ausgewertet (eine spätere Einreichung nicht konvertierbar)", losses[STALE])
    add(s, "Episoden ohne Erstmeldung (nicht in den Fällen)", losses[P_WITHOUT_INITIAL])
    add(s, "Fälle nicht auswertbar (nicht in den Fällen)", losses[P_UNREADABLE])

    s = "Einreichungen (nach Einreichungsdatum)"
    for kind in SUBMISSION_TYPES + (OTHER,):
        add(s, f"Einreichungen {kind}", sum(1 for x in subs if x.type == kind))
    add(s, "Einreichungen gesamt (ohne Test)", sum(1 for x in subs if x.type != "test"))
    follow_ups = [x for x in subs if x.type == "followup"]
    count(s, "Folgemeldungen mit Sterbedatum (followUpOds[].deathDate)", lambda x: x.death, follow_ups, "Folgemeldungen")
    count(s, "Folgemeldungen mit lastContactDate (DIP: früher lost to follow-up, Kontakt wieder aufgenommen)", lambda x: x.last_contact, follow_ups, "Folgemeldungen")
    count(s, "Folgemeldungen mit Therapiebeginn (followUpOds[].therapies[].therapyStartDate)", lambda x: x.therapy_start,
          follow_ups, "Folgemeldungen")
    initials = [x for x in subs if x.type == "initial"]
    count(s, "Erstmeldungen mit systemischer Therapieempfehlung", lambda x: x.recommendation, initials, "Erstmeldungen")
    for code, label in LIBRARIES + ((MISSING, MISSING), (OTHER, OTHER)):
        count(s, f"Erstmeldungen libraryType: {label}", lambda x: x.library == code, initials, "Erstmeldungen")
    for state in CONSENT_STATES:
        count(s, f"Erstmeldungen Forschungseinwilligung: {state}", lambda x: x.consent == state, initials, "Erstmeldungen")

    s = "Einwilligung"
    count(s, "MV-Einwilligung Sequenzierung (mvSequencing: permit)", lambda e: e.mv_consent)
    count(s, "Broad Consent gültig zum Stichtag", lambda e: e.consent_at_reference)
    s = "Broad Consent (Stand der letzten Einreichung, gültig zu deren Datum)"
    for state in CONSENT_STATES:
        count(s, state, lambda e: e.consent == state)
    missing = [e for e in cases if e.consent.startswith("nicht übermittelt") and e.consent != REASON_NONE]
    count(s, "Grund aus der DIP-Spalte reasonResearchConsentMissing (statt noScopeJustification)",
          lambda e: e.consent_from_dip, missing, "Fälle ohne Broad Consent mit Grund")

    s = "Geschlecht"
    for sex in ("m", "w", "d", "unbekannt"):
        count(s, sex, lambda e: e.sex == sex)
    s = "Alter bei Einschluss (GFK1)"
    for group in AGE_GROUPS + (MISSING,):
        count(s, group, lambda e: e.age_group == group)
    s = "Versicherung"
    for insurance in ("GKV", "PKV", "andere"):
        count(s, insurance, lambda e: e.insurance == insurance)

    s = "Sequenzierung (libraryType, eingeschlossene Fälle)"
    included = [e for e in cases if e.included]
    for code, label in LIBRARIES + ((MISSING, MISSING), (OTHER, OTHER)):
        count(s, label, lambda e: e.library == code, included, "eingeschlossene Fälle")

    s = "Tumorentität (Hauptdiagnose ICD-10-GM, gruppiert)"
    for group in ENTITY_LABELS:
        count(s, group, lambda e: e.entity == group)
    s = "Tumorentität (sequenzierte Fälle)"
    for group in ENTITY_LABELS:
        count(s, group, lambda e: e.entity == group, sequenced, seq_label)
    s = "Diagnose: Kodierung"
    count(s, "Histologie ICD-O-3-M mit Verhaltenscode", lambda e: e.behaviour is not None)
    for code, label in BEHAVIOUR:
        count(s, f"Histologie {label}", lambda e: e.behaviour == code)
    solid = [e for e in cases if re.match(r"C(0\d|[1-6]\d|7[0-6]|80)", e.main_code.upper())]
    count(s, "Topographie ICD-O-3-T dreistellig gleich der Hauptdiagnose", lambda e: e.topography_matches is True,
          solid, "Fälle mit Hauptdiagnose C00-C76, C80")
    count(s, "Keimbahndiagnose bestätigt (germlineDiagnosisConfirmed)", lambda e: e.germline)
    count(s, "germlineDiagnoses angegeben, von bestätigten", lambda e: e.germline_codes,
          [e for e in cases if e.germline], "Fälle mit bestätigter Keimbahndiagnose")

    s = "Tumorstadium (M-Kategorie aus tnmClassifications)"
    for state in M_STATES:
        count(s, state, lambda e: e.m_state == state)
    s = "ECOG bei Einschluss"
    for code in ECOG + (MISSING, OTHER):
        count(s, code, lambda e: (e.ecog if e.ecog in ECOG else MISSING if e.ecog is None else OTHER) == code)
    count("ECOG bei Einschluss (zusammengefasst)", "konkreter Wert 0-5", lambda e: e.ecog in ECOG[:6])
    s = "Grading (Differenzierungsgrad)"
    for code, label in GRADING + ((MISSING, MISSING), (OTHER, OTHER)):
        count(s, label, lambda e: (e.grading if e.grading in dict(GRADING) else MISSING if e.grading is None else OTHER) == code)

    s = "Vortherapien (priorProcedures)"
    for label, lo, hi in (("keine", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3 und mehr", 3, 10 ** 9)):
        count(s, f"Anzahl systemischer Vortherapien: {label}", lambda e: lo <= len(e.procedures) <= hi)
    for code, label in INTENTIONS:
        count(s, f"mit Intention {label} (Mehrfachnennung)", lambda e: any(p.get("intention") == code for p in e.procedures))
    for code, label in TREATMENT_TYPES:
        count(s, f"mit Stellung zur OP {label} (Mehrfachnennung)",
              lambda e: any(p.get("treatmentType") == code for p in e.procedures))
    s = "Molekulare Vordiagnostik (priorDiagnostics, Mehrfachnennung)"
    for code in PRIOR_TYPES:
        count(s, code, lambda e: code in e.prior_types)
    count(s, "keine Vordiagnostik dokumentiert", lambda e: not e.priors)

    for s, attr, label in (
        ("Dauer Erstdiagnose bis GFK1", "diagnosis_days", "Fälle mit Diagnosedatum"),
        ("Bearbeitungsdauer medizinisch (GFK1 bis GFK2)", "medical_days", "Fälle mit GFK2"),
        ("Bearbeitungsdauer dokumentarisch (GFK2 bis Einreichung beim KDK)", "documentary_days", "Fälle mit GFK2"),
        ("Dauer GFK2 bis Beginn einer empfohlenen Therapie", "start_days", "Fälle mit umgesetzter Empfehlung"),
    ):
        count(s, "Dauer berechenbar", lambda e: getattr(e, attr) is not None)
        if attr in ("medical_days", "documentary_days"):
            count(s, "nicht berechenbar: noch keine GFK2", lambda e: not e.has_gfk2)
            count(s, "nicht berechenbar: unmögliches Datum (siehe Rückfragen)", lambda e: e.has_gfk2 and e.impossible)
        values = [getattr(e, attr) for e in cases if getattr(e, attr) is not None]
        if attr == "medical_days":
            # GFK2 on the day of GFK1 is no processing time (sequencing and evaluation lie between): counted apart,
            # not in the statistics, and clarified with the LE (P_SHORT_MEDICAL)
            count(s, SAME_DAY, lambda e: e.medical_days == 0)
            values = [d for d in values if d != 0]
        stats(s, "", values, DAYS)

    # process timeline: per milestone the cases with an offset and its quartiles; offsets before GFK1 of a
    # milestone that must follow it are counted apart and left out of the quartiles. No minimum or maximum:
    # they are single cases
    s = TIMELINE
    add(s, NO_GFK1, losses[NO_GFK1])
    for name, after in MILESTONES:
        offsets = [e.timeline[name] for e in cases if e.timeline[name] is not None]
        valid = sorted(d for d in offsets if not (after and d < 0))
        add(s, f"{name}: Fälle", len(valid), len(cases), "Fälle")
        for label, value in zip(("1. Quartil", "Median", "3. Quartil"), quartiles(valid) if valid else (None,) * 3):
            rows.append(Row(s, f"{name}: {label} ({DAYS})", DAYS, n=rounded(value), support=len(valid)))
        if after:
            add(s, f"{name}: unplausibel (vor GFK1)", sum(1 for d in offsets if d < 0), len(cases), "Fälle")

    s = "GFK2 (MTB): Ergebnis (sequenzierte Fälle)"
    count(s, "GFK2 dokumentiert (plan.carePlanOd)", lambda e: e.has_plan, sequenced, seq_label)
    count(s, "mindestens eine systemische Therapieempfehlung", lambda e: bool(e.therapies), with_mtb, mtb_label)
    count(s, "mindestens eine Studienempfehlung (recommendedStudies)", lambda e: bool(e.studies), with_mtb, mtb_label)
    count(s, "mindestens eine Therapie- oder Studienempfehlung", lambda e: bool(e.therapies or e.studies), with_mtb, mtb_label)
    count(s, "Empfehlung molekular begründet (variants[] einer Empfehlung)",
          lambda e: any(items(r.get("variants")) for r in e.therapies + e.studies), with_mtb, mtb_label)
    count(s, "ohne Therapie- und Studienempfehlung (kein Target)", lambda e: not (e.therapies or e.studies), with_mtb, mtb_label)
    for flag, label in CARE_PLAN_FLAGS:
        count(s, label, lambda e: e.care_plan.get(flag) is True, with_mtb, mtb_label)
    for code, label in OTHER_RECOMMENDATIONS:
        count(s, f"andere Empfehlung {label} (otherRecommendations)", lambda e: code in e.other_recommendations,
              with_mtb, mtb_label)
    s = "Empfehlungen je Fall (sequenzierte Fälle mit GFK2)"
    stats(s, "Therapieempfehlungen: ", [len(e.therapies) for e in with_mtb], PER_CASE)
    stats(s, "Studienempfehlungen: ", [len(e.studies) for e in with_mtb], PER_CASE)

    s = "GFK2 (MTB): Ergebnis nach Tumorentität (sequenzierte Fälle)"
    for group in ENTITY_LABELS:
        of_group = [e for e in sequenced if e.entity == group]
        count(s, f"{group}: GFK2 dokumentiert", lambda e: e.has_plan, of_group, f"sequenzierte Fälle {group}")
        count(s, f"{group}: Therapie- oder Studienempfehlung", lambda e: bool(e.therapies or e.studies),
              [e for e in of_group if e.has_plan], f"sequenzierte Fälle mit GFK2 {group}")

    recs = [r for e in with_mtb for r in e.therapies]
    s = "Systemische Therapieempfehlungen (Empfehlungen)"
    add(s, "Empfehlungen gesamt", len(recs))
    for code in EVIDENCE:
        count(s, f"Evidenzgrad {code}", lambda r: r.get("evidenceLevel") == code, recs, "Empfehlungen")
    count(s, f"Evidenzgrad {OTHER}", lambda r: r.get("evidenceLevel") not in EVIDENCE, recs, "Empfehlungen")
    for code in EVIDENCE_DETAILS:
        count(s, f"Zusatzverweis {code} (Mehrfachnennung)", lambda r: code in items(r.get("evidenceLevelDetails")),
              recs, "Empfehlungen")
    for code in THERAPY_TYPES:
        count(s, f"Art {code}", lambda r: r.get("type") == code, recs, "Empfehlungen")
    for code, label in STRATEGIES:
        count(s, f"Strategie {label}", lambda r: r.get("therapeuticStrategy") == code, recs, "Empfehlungen")
    count(s, "mit variants[] (molekular begründet)", lambda r: bool(items(r.get("variants"))), recs, "Empfehlungen")
    count(s, "Priorität 1", lambda r: r.get("priority") == 1, recs, "Empfehlungen")

    studies = [r for e in with_mtb for r in e.studies]
    s = "Studienempfehlungen (Empfehlungen)"
    add(s, "Empfehlungen gesamt", len(studies))
    for code in REGISTERS:
        count(s, f"Register {code}", lambda r: r.get("register") == code, studies, "Studienempfehlungen")
    for code in EVIDENCE:
        count(s, f"Evidenzgrad {code}", lambda r: r.get("evidenceLevel") == code, studies, "Studienempfehlungen")

    s = "Umsetzung (sequenzierte Fälle mit Therapie- oder Studienempfehlung)"
    with_recs = [e for e in with_mtb if e.therapies or e.studies]
    base_label = "Fälle mit Therapie- oder Studienempfehlung"
    count(s, "mit Follow-up", lambda e: e.follow_up is not None, with_recs, base_label)
    count(s, "mindestens eine Empfehlung begonnen (followUpOds[].therapies[].reference)", lambda e: bool(e.implemented),
          with_recs, base_label)
    with_fu = [e for e in with_recs if e.follow_up is not None]
    count(s, "mindestens eine Empfehlung begonnen, von Fällen mit Follow-up", lambda e: bool(e.implemented),
          with_fu, "Fälle mit Empfehlung und Follow-up")
    count(s, "Therapie ohne Bezug zu einer Empfehlung", lambda e: bool(e.unlinked), with_recs, base_label)
    all_recs = [(e, rec_id(r)) for e in with_recs for r in e.therapies + e.studies]
    count(s, "Empfehlungen begonnen (Empfehlungen)", lambda x: x[1] in x[0].implemented_ids, all_recs, "Empfehlungen")
    implemented = [t for e in with_recs for t in e.implemented]
    for code, label in TERMINATION:
        count(s, f"Ende: {label}", lambda t: t.get("terminationReasonOBDS") == code, implemented, "begonnene Empfehlungen")
    count(s, "Ende: kein Grund (laufend oder nicht dokumentiert)", lambda t: not t.get("terminationReasonOBDS"),
          implemented, "begonnene Empfehlungen")
    for code, label in RESPONSES:
        count(s, f"Ansprechen: {label}", lambda t: t.get("therapyResponse") == code, implemented, "begonnene Empfehlungen")
    count(s, "Ansprechen: nicht dokumentiert", lambda t: not t.get("therapyResponse"), implemented, "begonnene Empfehlungen")

    s = "Molekulare Befunde (sequenzierte Fälle)"
    count(s, "molecular übermittelt", lambda e: e.has_molecular, sequenced, seq_label)
    count(s, "mindestens eine SNV/Indel (smallVariants)", lambda e: bool(e.small), sequenced, seq_label)
    count(s, "mindestens eine CNV (copyNumberVariants)", lambda e: bool(e.cnvs), sequenced, seq_label)
    count(s, "Keimbahnvariante berichtet (genomicSource germline)", lambda e: e.germline_variants, sequenced, seq_label)
    count(s, "TMB angegeben", lambda e: e.tmb is not None, sequenced, seq_label)
    with_tmb = [e for e in sequenced if e.tmb is not None]
    count(s, f"TMB-high (>= {TMB_HIGH:g} mut/Mb), von Fällen mit TMB", lambda e: e.tmb >= TMB_HIGH, with_tmb, "Fälle mit TMB")
    count(s, "HRD-Status angegeben (hrdHigh)", lambda e: e.hrd is not None, sequenced, seq_label)
    count(s, "HRD high, von Fällen mit HRD-Status", lambda e: e.hrd is True,
          [e for e in sequenced if e.hrd is not None], "Fälle mit HRD-Status")
    stats("Molekulare Befunde: Anzahl je Fall (sequenzierte Fälle mit molecular)", "SNV/Indel: ",
          [len(e.small) for e in sequenced if e.has_molecular], PER_CASE)
    stats("Molekulare Befunde: Anzahl je Fall (sequenzierte Fälle mit molecular)", "CNV: ",
          [len(e.cnvs) for e in sequenced if e.has_molecular], PER_CASE)
    stats("Molekulare Befunde: TMB (Fälle mit TMB)", "", [e.tmb for e in with_tmb], TMB_UNIT)

    s = "Follow-up und Vitalstatus"
    count(s, "Fälle mit Folgemeldung (submission.type followup)", lambda e: e.follow_up_submissions > 0)
    count(s, "Fälle mit Follow-up-Inhalt (followUp.followUpOds)", lambda e: e.follow_up is not None)
    count(s, "verstorben (vitalStatus deceased in einem Follow-up)", lambda e: e.deceased)
    count(s, "lastContactDate angegeben (DIP: früher lost to follow-up, Kontakt wieder aufgenommen)",
          lambda e: e.follow_up is not None and bool(e.follow_up.get("lastContactDate")))
    count(s, "metachrone Tumoren (metachroneDiagnoses)",
          lambda e: e.follow_up is not None and e.follow_up.get("metachroneDiagnoses") is True)
    for months in FOLLOW_UP_MONTHS:
        due = [e for e in cases if e.initial_date and add_months(e.initial_date, months) <= reference_date]
        s = f"Follow-up: Erstmeldung mindestens {months} Monate vor dem Stichtag"
        base_label = f"Fälle mit Erstmeldung >= {months} Monate vor Stichtag"
        add(s, "Fälle", len(due))
        for label, lo, hi in (("ohne Folgemeldung", 0, 0), ("mit 1 Folgemeldung", 1, 1), ("mit 2 Folgemeldungen", 2, 2),
                              ("mit 3 Folgemeldungen", 3, 3), ("mit 4 und mehr Folgemeldungen", 4, 10 ** 9)):
            count(s, label, lambda e: lo <= e.follow_up_submissions <= hi, due, base_label)
        count(s, "verstorben", lambda e: e.deceased, due, base_label)

    # follow-up schedule after the MTB (FU_CATEGORIES): the sequenced cases with a GFK2 date
    of_gfk2 = [e for e in with_mtb if e.gfk2]
    gfk2_label = "sequenzierte Fälle mit GFK2-Datum"
    category = {e.key: follow_up_category(e) for e in of_gfk2}
    state = {e.key: follow_up_state(e, reference_date) for e in of_gfk2}
    s = FU_SECTION
    for key, label, months in FU_CATEGORIES:
        count(s, f"{label} (Intervall {months} Monate)", lambda e: category[e.key] == key, of_gfk2, gfk2_label)
    for st in FU_STATES:
        count(s, f"Stand am Stichtag: {st}", lambda e: state[e.key] == st, of_gfk2, gfk2_label)
    due = [e for e in of_gfk2 if state[e.key] in (FU_CURRENT, FU_OVERDUE)]
    count(s, "Follow-up aktuell, von fälligen", lambda e: state[e.key] == FU_CURRENT, due, "fällige Fälle (ohne lost to follow-up)")
    count(s, f"Kategorie 2 ab {FU_NOT_STARTED_MONTHS} Monate nach GFK2 (danach Intervall wie Kategorie 3)",
          lambda e: add_months(e.gfk2, FU_NOT_STARTED_MONTHS) <= reference_date,
          [e for e in of_gfk2 if category[e.key] == FU_NOT_STARTED], "Fälle der Kategorie 2")
    alive = [e for e in of_gfk2 if not e.deceased and add_months(e.gfk2, FU_LOST_MONTHS) <= reference_date]
    count(s, f"kein dokumentierter Kontakt in den {FU_LOST_MONTHS} Monaten vor dem Stichtag (lost to follow-up nach SOP)",
          lambda e: not within(e.contact_dates, FU_LOST_MONTHS, reference_date), alive,
          f"nicht verstorbene Fälle mit GFK2 mindestens {FU_LOST_MONTHS} Monate vor Stichtag")
    s = FU_CATEGORY_SECTION
    for key, label, _ in FU_CATEGORIES:
        of_category = [e for e in of_gfk2 if category[e.key] == key]
        short = label.split(":")[0]
        for st in FU_STATES:
            count(s, f"{short}: {st}", lambda e: state[e.key] == st, of_category, f"Fälle der {short}")
        count(s, f"{short}: Follow-up aktuell, von fälligen", lambda e: state[e.key] == FU_CURRENT,
              [e for e in of_category if state[e.key] in (FU_CURRENT, FU_OVERDUE)], f"fällige Fälle der {short}")
    s = FU_DAYS_SECTION
    count(s, "Dauer berechenbar (Follow-up ab GFK2)", lambda e: e.first_fu_days is not None, of_gfk2, gfk2_label)
    count(s, "nur Follow-up vor GFK2", lambda e: e.fu_dates and e.first_fu_days is None, of_gfk2, gfk2_label)
    stats(s, "", [e.first_fu_days for e in of_gfk2 if e.first_fu_days is not None], DAYS)
    s = FU_FIELDS_SECTION
    entries = [f for e in cases for f in e.fu_entries]
    add(s, "Follow-up-Einträge gesamt", len(entries))
    count(s, "followUpDate lesbar", lambda f: parse_date(f.get("followUpDate")) is not None, entries, "Follow-up-Einträge")
    count(s, "vitalStatus living oder deceased", lambda f: f.get("vitalStatus") in ("living", "deceased"), entries,
          "Follow-up-Einträge")
    count(s, "ecogPerformanceStatusScore konkret (0-5)", lambda f: f.get("ecogPerformanceStatusScore") in ECOG[:6],
          entries, "Follow-up-Einträge")
    fu_therapies = [t for e in cases for t in e.fu_therapies]
    add(s, "Follow-up-Therapien gesamt", len(fu_therapies))
    count(s, "therapyStartDate lesbar", lambda t: parse_date(t.get("therapyStartDate")) is not None, fu_therapies, "Therapien")
    count(s, "substances angegeben", lambda t: filled(t.get("substances")), fu_therapies, "Therapien")
    count(s, "therapyResponseDate, von Therapien mit Ansprechen", lambda t: parse_date(t.get("therapyResponseDate")) is not None,
          [t for t in fu_therapies if t.get("therapyResponse")], "Therapien mit Ansprechen")
    s = FU_RESPONSE_SECTION
    response_due = [t for e in of_gfk2 for t in e.implemented if parse_date(t.get("therapyStartDate"))
                    and parse_date(t.get("therapyStartDate")) + timedelta(weeks=FU_RESPONSE_WEEKS) <= reference_date]
    count(s, f"Ansprechen dokumentiert, von begonnenen Empfehlungen mit Beginn mindestens {FU_RESPONSE_WEEKS} Wochen "
          "vor dem Stichtag", lambda t: bool(t.get("therapyResponse")), response_due,
          f"begonnene Empfehlungen, Beginn >= {FU_RESPONSE_WEEKS} Wochen vor Stichtag")
    with_fu = [e for e in of_gfk2 if e.follow_up is not None]
    count(s, "ECOG konkret (0-5) im letzten Follow-up", lambda e: e.follow_up.get("ecogPerformanceStatusScore") in ECOG[:6],
          with_fu, "sequenzierte Fälle mit GFK2 und Follow-up")

    def vital(e):
        if e.deceased:
            return "verstorben"
        if e.follow_up is None:
            return "ohne Follow-up"
        return "lebend" if e.follow_up.get("vitalStatus") == "living" else MISSING
    for label in ("verstorben", "lebend", MISSING, "ohne Follow-up"):
        count(s, f"Vitalstatus (letztes Follow-up): {label}", lambda e: vital(e) == label, of_gfk2, gfk2_label)

    s = "Füllgrad optionaler Felder (Fälle)"
    count(s, "case.diagnosisOd.additionalDiagnoses", lambda e: e.additional_diagnoses, cases, "Fälle")
    count(s, "case.diagnosisOd.grading", lambda e: e.grading is not None, cases, "Fälle")
    count(s, "case.diagnosisOd.tnmClassifications", lambda e: e.tnm, cases, "Fälle")
    count(s, "case.diagnosisOd.additionalClassification", lambda e: e.additional_classification, cases, "Fälle")
    count(s, "TNM oder additionalClassification", lambda e: e.tnm or e.additional_classification, cases, "Fälle")
    count(s, "case.priorDiagnostics", lambda e: e.priors, cases, "Fälle")
    count(s, "case.priorProcedures", lambda e: bool(e.procedures), cases, "Fälle")
    count(s, "molecular", lambda e: e.has_molecular, sequenced, seq_label)
    count(s, "plan", lambda e: e.has_plan, sequenced, seq_label)
    count(s, "followUp", lambda e: e.follow_up is not None, cases, "Fälle")
    fu_cases = [e for e in cases if e.follow_up is not None]
    count(s, "followUpOds[].lastContactDate", lambda e: bool(e.follow_up.get("lastContactDate")), fu_cases, "Fälle mit Follow-up")
    count(s, "followUpOds[].deathDate (verstorben)", lambda e: e.death_date, [e for e in cases if e.deceased], "verstorbene Fälle")
    for name in ("ploidy", "tmb", "hrdHigh", "lstHigh", "taiHigh"):
        count(s, f"molecular.complexBiomarkers[].{name}", lambda e: e.biomarker_fields[name],
             [e for e in sequenced if e.has_molecular], "sequenzierte Fälle mit molecular")

    for s, of, names, base_label in (
        ("Füllgrad optionaler Felder (Vortherapien)", [p for e in cases for p in e.procedures],
         ("intention", "therapyEndDate", "substances", "terminationReasonOBDS", "therapyResponse"), "Vortherapien"),
        ("Füllgrad optionaler Felder (Therapieempfehlungen)", recs, ("evidenceLevelDetails", "variants"), "Empfehlungen"),
        ("Füllgrad optionaler Felder (Follow-up-Therapien)", [t for e in cases for t in e.fu_therapies],
         ("therapyEndDate", "substances", "terminationReasonOBDS", "therapyResponse", "therapyResponseDate"), "Therapien"),
        ("Füllgrad optionaler Felder (SNV/Indel)", [v for e in sequenced for v in e.small],
         ("proteinChange", "transcriptId", "variantTypes", "loh"), "Varianten"),
        ("Füllgrad optionaler Felder (CNV)", [v for e in sequenced for v in e.cnvs],
         ("gene", "chromosome", "startPosition", "endPosition"), "Varianten"),
    ):
        add(s, f"{base_label} gesamt", len(of))
        for name in names:
            count(s, name, lambda x: filled(x.get(name)), of, base_label)

    s = "Rückfragen an LE (Anzahl, Einzelfälle siehe Rückfragen)"
    for problem in PROBLEMS:
        add(s, problem, problem_counts.get(problem, 0))

    return rows


def cell_values(row):
    """{value, base, percent} of a row: every value exact (the oncology matrix isn't suppressed)"""
    if row.unit != COUNT:
        return {"value": row.n, "base": None, "percent": None}
    return {"value": row.n, "base": row.base, "percent": percent(row.n, row.base)}


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def run(rows, failures, start, end, reference_date, min_medical_days=14, resolution=MONTH, cutoff=None):
    """
    The matrix with its time series, from the submissions (SubmissionRow) and failures of the export; the
    result's "problems" (case keys of all LEs) are for the Rückfragen files only, not for the JSON
    """
    if cutoff and reference_date > cutoff:
        raise ValueError(f"reference date {reference_date} after the cutoff {cutoff}")
    if cutoff:
        rows = [r for r in rows if not (submission_date(r) and submission_date(r) > cutoff)]
        failures = [f for f in failures if not (parse_date(f.submitted_at) and parse_date(f.submitted_at) > cutoff)]

    def in_range(d):
        return d is None or not ((start and d < start) or (end and d >= end))

    cases, tests, without_initial, unreadable_rows, submitters = build_cases(rows)

    problems = []   # dicts le, problem, case, tan, detail, and the period they are counted in

    def problem(le, kind, case, tan, detail, d):
        problems.append({"le": le, "problem": kind, "case": case, "tan": tan, "detail": detail,
                         "period": period_of(d, resolution) if d else None})

    evaluated, no_gfk1 = [], []     # no_gfk1: (LE, period) of the cases unreadable for want of GFK1
    for case in cases:
        initial_date = submission_date(initial_submission(case))
        if not in_range(initial_date):
            continue
        try:
            if initial_date is None:
                raise ValueError("Erstmeldung ohne lesbares Datum")
            e = evaluate_case(case, reference_date, min_medical_days)
        except Exception as error:     # a case that can't be read is clarified with its LE, the run goes on
            problem(case.le, P_UNREADABLE, case.key, dig(case.latest, "metaData", "tanC"), repr(error)[:300], initial_date)
            if initial_date and parse_date(dig(case.latest, "metaData", "molecularBoardDecisionDate")) is None:
                no_gfk1.append((case.le, period_of(initial_date, resolution)))
            continue
        e.initial_date = initial_date
        e.period = period_of(initial_date, resolution)
        evaluated.append(e)
        for kind, detail in e.problems:
            problem(e.le, kind, e.key, e.tan, detail, initial_date)

    for case in without_initial:
        d = submission_date(case.rows[0])
        if in_range(d):
            problem(case.le, P_WITHOUT_INITIAL, case.key, dig(case.latest, "metaData", "tanC"),
                    "nur Folgemeldungen/Nachmeldungen/Korrekturen, keine Erstmeldung", d)
    for row in unreadable_rows:
        d = parse_date(row.submitted_at)
        if in_range(d):
            problem(leistungserbringer(row), P_UNREADABLE, row.case, None, "Einreichung ohne lesbare metaData.submission", d)
    evaluated_keys = {e.key for e in evaluated}
    stale = Counter()
    for f in failures:
        d = parse_date(f.submitted_at)
        if not in_range(d):
            continue
        if f.case in evaluated_keys:
            stale[(str(f.site or "?"), period_of(d, resolution) if d else None)] += 1
        problem(str(f.site or "?"), P_NOT_CONVERTED, f.case, f.tan,
                ("Episode mit älterem Stand ausgewertet; " if f.case in evaluated_keys else "") + f.error[:300], d)

    subs = [submission_fact(r, resolution) for c in cases + without_initial for r in c.rows] \
        + [submission_fact(r, resolution) for r in tests]
    subs = [x for x in subs if x.date is None or in_range(x.date)]

    les = sorted({e.le for e in evaluated} | {p["le"] for p in problems} | {x.le for x in subs})

    def select(le, period):
        """cases, submissions, losses and problem counts of an LE (or all) and a period (or all)"""
        def ok(x_le, x_period):
            return (le == TOTAL or x_le == le) and (period == WHOLE or x_period == period)

        # Rückfragen counted in cases (submissions for P_NOT_CONVERTED): a case with two problems of a kind counts once
        distinct = {(p["problem"], p["case"], p["tan"] if p["problem"] == P_NOT_CONVERTED else None)
                    for p in problems if ok(p["le"], p["period"])}
        counts = Counter(kind for kind, _, _ in distinct)
        losses = {k: counts.get(k, 0) for k in LOSSES}
        losses[STALE] = sum(n for (x_le, x_period), n in stale.items() if ok(x_le, x_period))
        losses[NO_GFK1] = sum(1 for x_le, x_period in no_gfk1 if ok(x_le, x_period))
        return ([e for e in evaluated if ok(e.le, e.period)], [x for x in subs if ok(x.le, x.period)], losses, counts)

    entities = [TOTAL] + les

    def evaluate(le, period):
        cases_, subs_, losses, counts = select(le, period)
        return matrix_rows(cases_, subs_, losses, counts, reference_date)

    # the matrix (period WHOLE) and the time series: the cases by the period of their initial submission, the
    # submissions by their own
    dates = [e.initial_date for e in evaluated] + [x.date for x in subs if x.date]
    first, last = start or min(dates, default=None), (end - timedelta(days=1)) if end else max(dates, default=None)
    periods = periods_between(first, last, resolution) if first and last and first <= last else []
    matrix = {(le, p): evaluate(le, p) for le in entities for p in [WHOLE] + periods}
    template = matrix[(TOTAL, WHOLE)]
    values = {(i, le, p): cell_values(r) for (le, p), rows_ in matrix.items() for i, r in enumerate(rows_)}

    def described(i, r, cells):
        return {"key": r.key, "section": r.section, "item": r.item, "unit": r.unit, "base_label": r.base_label,
                "values": cells}

    result_rows = [described(i, r, {le: values[(i, le, WHOLE)] for le in entities}) for i, r in enumerate(template)]
    series_rows = [described(i, r, {le: {p: values[(i, le, p)] for p in periods} for le in entities})
                   for i, r in enumerate(template)]

    if start or end:
        until = end - timedelta(days=1) if end else None
        period = f"{start.strftime('%d.%m.%Y') if start else '…'} - {until.strftime('%d.%m.%Y') if until else '…'}"
    else:
        # month precision: the exact first and last date could single out a case
        initial_dates = [e.initial_date for e in evaluated]
        period = (f"gesamter Export (Erstmeldungen {min(initial_dates).strftime('%m.%Y')} - "
                  f"{max(initial_dates).strftime('%m.%Y')})" if initial_dates else "gesamter Export")

    labels = {le: f"{le} ({', '.join(sorted(submitters[le]))})" if le in submitters else le for le in les}
    unit = "Monat" if resolution == MONTH else "Quartal"
    notes = [
        f"{len(evaluated)} Fälle (Episoden mit Erstmeldung, diseaseType oncological/hereditary) ausgewertet, Stand je Fall "
        "aus der letzten Nicht-Test-Einreichung; plan und molecular aus der letzten Einreichung, die sie enthält; "
        "Follow-ups und deren Therapien über alle Einreichungen. Definitionen: scripts/dnpm_evaluation_matrix/README.md.",
        f"{len(tests)} Testeinreichungen nicht in den Fällen (nur im Abschnitt Einreichungen gezählt).",
        "Keine Zellen unterdrückt: alle Zahlen und Kennzahlen exakt, auch unter 5 (Entscheidung der Datenhalter: die "
        "onkologische Matrix wird nicht unterdrückt). Die Tabellen sind für die Datenknoten und den jeweiligen LE bestimmt, "
        "nicht zur Veröffentlichung.",
        "Population: GFK2-Ergebnis, Umsetzung und molekulare Befunde beziehen sich auf die sequenzierten Fälle "
        "(decisionToInclude nicht false und libraryType wgs, wgs_lr, wes oder panel).",
        "Umsetzung: eine Empfehlung gilt als begonnen, wenn followUpOds[].therapies[].reference ihren identifier "
        "(recommendedSystemicTherapies oder recommendedStudies) trägt. Gründe der Nicht-Umsetzung enthält der Datenkranz nicht.",
        f"Broad Consent bewertet zum Datum der letzten Einreichung und zum Stichtag {reference_date.isoformat()}; "
        "fehlt er, der Grund aus noScopeJustification, sonst aus der DIP-Spalte reasonResearchConsentMissing.",
        f"Zeitreihe ({unit}): Fälle nach dem {unit} ihrer Erstmeldung, Abschnitt 'Einreichungen' nach dem {unit} der "
        "jeweiligen Einreichung (metaData.submission.date), nicht konvertierbare Einreichungen nach submittedAt. "
        "Follow-up-Anteile junger Zeiträume sind mit älteren nicht vergleichbar.",
        "GFK1 = metaData.molecularBoardDecisionDate (erste Fallkonferenz, Indikation/Einschluss); GFK2 (MTB) = "
        "plan.carePlanOd.molecularBoardDecisionDate der ersten Einreichung mit plan. Das DIP-Mapping schreibt das jeweils "
        "letzte Therapieboard zum Zeitpunkt der Einreichung; frühere MTBs davor sind nicht sichtbar.",
        "lastContactDate: das DIP-Mapping lässt followUp weg, wenn das letzte Follow-up 'lost to follow-up' ist; die "
        "Zeilen zu lastContactDate zählen nur früher verlorene Patienten, deren Kontakt wieder aufgenommen wurde.",
        f"Medizinische Bearbeitungsdauer unter {min_medical_days} Tagen wird als unplausibel kurz zurückgefragt.",
        FU_NOTE + f" Stichtag {reference_date.isoformat()}: Kategorie 1 (eine Empfehlung begonnen) und 2 (Therapie- oder "
        f"Studienempfehlung, nicht begonnen) alle 3 Monate, Kategorie 3 (keine Empfehlung) und Kategorie 2 ab "
        f"{FU_NOT_STARTED_MONTHS} Monaten nach GFK2 alle 12 Monate. Fällig ist ein Follow-up ab einem Intervall nach der "
        "GFK2; aktuell, wenn ein followUpDate im Intervall vor dem Stichtag liegt, sonst überfällig; Verstorbene sind nicht "
        f"mehr fällig. Lost to follow-up: kein followUpDate oder lastContactDate in den {FU_LOST_MONTHS} Monaten vor dem "
        "Stichtag. Nicht aus dem Datenkranz: Kostenübernahme, Gründe für eine nicht begonnene Therapie, Therapiestatus, "
        "Studieneinschluss, Beurteilungsmethode und radiologische Validierung des Ansprechens, frühere Staging-Termine "
        "(nur das letzte Ansprechen je Therapie), der Status lost to follow-up selbst (das DIP-Mapping lässt das Follow-up dann weg).",
        f"{TIMELINE}: je Meilenstein Datum minus GFK1 in Tagen, Fälle mit Datum sowie 1. Quartil, Median und 3. Quartil. "
        "MV-Einwilligung: frühestes mvConsent.scope[mvSequencing].date; Erstdiagnose: "
        "mainDiagnosis.date; GFK2 (MTB) wie oben; Erstmeldung beim KDK: Datum der Erstmeldung; Beginn empfohlene "
        "Therapie: frühester therapyStartDate einer begonnenen Empfehlung; Erstes Follow-up: frühestes "
        "followUpOds[].followUpDate über alle Einreichungen. Monatsangaben (YYYY-MM) zählen als 15. des Monats. "
        "Meilensteine nach der GFK1 mit Datum davor zählen als unplausibel, nicht in den Quartilen (Rückfrage). "
        f"'{NO_GFK1}': nicht auswertbare Fälle ohne metaData.molecularBoardDecisionDate.",
        "LE: Standort des Exports, sonst metaData.submission.submitterId. LEs: " + ", ".join(labels[le] for le in les) + ".",
    ]
    if start or end:
        notes.append(f"Fälle mit Erstmeldung, Einreichungen und Rückfragen vom {start or '…'} bis vor dem {end or '…'}.")
    if cutoff:
        notes.append(f"Datenstand {cutoff.isoformat()}: spätere Einreichungen (metaData.submission.date) nicht berücksichtigt.")
    if all(r.reason is NOT_EXPORTED for r in rows):
        notes.append("Der Export enthält keine Spalte reasonResearchConsentMissing: Gründe fehlender Einwilligung nur "
                     "aus noScopeJustification, die das DIP-Mapping nicht befüllt.")
    if unreadable_rows:
        notes.append(f"{len(unreadable_rows)} Einreichungen ohne lesbare metaData.submission (Rückfrage 'Fall nicht auswertbar').")

    return {
        "title": f"AUSWERTUNG MTB {period}",
        "period": period,
        "reference_date": reference_date.isoformat(),
        "cutoff": cutoff.isoformat() if cutoff else None,
        "entities": entities,
        "labels": {TOTAL: TOTAL, **labels},
        "rows": result_rows,
        "problems": sorted(({k: v for k, v in p.items() if k != "period"} for p in problems),
                           key=lambda p: (p["le"], PROBLEMS.index(p["problem"]), str(p["case"]))),
        "notes": notes,
        "time_series": {"resolution": resolution, "periods": periods,
                        "rows": series_rows},
    }


def fmt(x, delimiter):
    return str(x).replace(".", ",") if isinstance(x, float) and delimiter == ";" else ("" if x is None else x)


def write_csv(path, result, delimiter=";"):
    """Long table: one line per LE and row of the matrix"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["LE", "Abschnitt", "Kategorie", "Einheit", "Wert", "Bezug", "Basis", "Anteil (%)"])
        for entity in result["entities"]:
            for r in result["rows"]:
                v = r["values"][entity]
                writer.writerow([result["labels"][entity], r["section"], r["item"], r["unit"], fmt(v["value"], delimiter),
                                 r["base_label"] or "", fmt(v["base"], delimiter), fmt(v["percent"], delimiter)])


def write_series_csv(path, result, delimiter=";"):
    """Long table: one line per LE, period and row of the matrix"""
    series = result["time_series"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["LE", "Zeitraum", "Abschnitt", "Kategorie", "Einheit", "Wert", "Bezug", "Basis", "Anteil (%)"])
        for entity in result["entities"]:
            for period in series["periods"]:
                for r in series["rows"]:
                    v = r["values"][entity][period]
                    writer.writerow([result["labels"][entity], period, r["section"], r["item"], r["unit"],
                                     fmt(v["value"], delimiter), r["base_label"] or "", fmt(v["base"], delimiter),
                                     fmt(v["percent"], delimiter)])


def write_problems_csv(path, problems, labels, delimiter=";"):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["LE", "Problem", "Fall", "Einreichung (tanC)", "Detail"])
        for p in problems:
            writer.writerow([labels.get(p["le"], p["le"]), p["problem"], p["case"], p["tan"] or "", p["detail"]])


def write_rueckfragen(output, result, delimiter=";", per_le=False):
    """The Rückfragen: <output>_rueckfragen.csv of all LEs, or per_le only <output>_rueckfragen_<LE>.csv per LE (each
    LE gets only its own case keys). Returns the paths written"""
    if not per_le:
        write_problems_csv(f"{output}_rueckfragen.csv", result["problems"], result["labels"], delimiter)
        return [f"{output}_rueckfragen.csv"]
    paths = per_le_paths(output, result["entities"][1:])
    for le, path in paths.items():
        write_problems_csv(path, [p for p in result["problems"] if p["le"] == le], result["labels"], delimiter)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="result JSON of export-datenkranz, or a JSON list of submissions")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="initial submitted on or after (YYYY-MM-DD)")
    parser.add_argument("--until", dest="end", type=date.fromisoformat, help="initial submitted before (YYYY-MM-DD)")
    parser.add_argument("--cutoff", type=date.fromisoformat,
                        help="data as of this date: later submissions (metaData.submission.date) are ignored")
    parser.add_argument("--reference-date", type=date.fromisoformat,
                        help="date consents and due follow-ups are evaluated at, not after --cutoff; best the date of "
                             "the export (default: --cutoff, else today)")
    parser.add_argument("--resolution", choices=(MONTH, QUARTER), default=MONTH, help="of the time series (default: %(default)s)")
    parser.add_argument("--min-medical-days", type=int, default=14,
                        help="shorter durations from GFK1 to GFK2 are listed for clarification (default: %(default)s)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s, for Excel with German locale)")
    parser.add_argument("--output", default="dnpm-evaluation-matrix", help="output path without extension (default: %(default)s)")
    parser.add_argument("--rueckfragen-per-le", action="store_true",
                        help="write the Rückfragen only per LE, <output>_rueckfragen_<LE>.csv, not the file of all LEs "
                             "(recommended: hand each LE only its own cases)")
    args = parser.parse_args(argv)
    if args.start and args.end and args.start >= args.end:
        parser.error("--from must be before --until")
    args.reference_date = args.reference_date or args.cutoff or date.today()
    if args.cutoff and args.reference_date > args.cutoff:
        parser.error("--reference-date must not be after --cutoff")

    rows, failures = load_submissions(args.input)
    result = run(rows, failures, args.start, args.end, args.reference_date, args.min_medical_days, args.resolution,
                 args.cutoff)

    with open(f"{args.output}.json", "w", encoding="utf-8") as f:     # without the case keys of the Rückfragen
        json.dump({k: v for k, v in result.items() if k != "problems"}, f, ensure_ascii=False, indent=1)
    write_csv(f"{args.output}.csv", result, args.delimiter)
    write_series_csv(f"{args.output}_zeitreihe.csv", result, args.delimiter)
    written = write_rueckfragen(args.output, result, args.delimiter, args.rueckfragen_per_le)

    for note in result["notes"]:
        print(note, file=sys.stderr)
    print(f"Rückfragen an LEs: siehe {args.output}_rueckfragen*.csv", file=sys.stderr)
    print(f"Written: {args.output}.json, {args.output}.csv, {args.output}_zeitreihe.csv, " + ", ".join(written),
          file=sys.stderr)


if __name__ == "__main__":
    main()
