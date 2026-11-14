#!/usr/bin/env python3
"""
Time series of the RD evaluation matrix: its key figures per month or quarter, in total and per
Leistungserbringer (LE), from the same BfArM Datenkranz submissions as rd_evaluation_matrix.py.

The cases, their LE, status and broad consent are those of rd_evaluation_matrix.py, which has to be
in the same directory: over the whole evaluation period (Zeitraum "gesamt") the figures equal the
ones of the matrix. Standard library only.

Blocks by the period of the initial submission of a case (the date --from/--until refer to):
  Fälle                    cases
  Forschungseinwilligung   broad consent of the latest submission: transmitted, valid (of all cases
                           and of the transmitted ones), why not valid, why not transmitted
                           (metaData.researchConsents[].noScopeJustification)
  Diagnosestatus (alle Fälle), Diagnoserate (abgeschlossene Fälle)   as the sections of the matrix
  Follow-up                cases with a follow-up, of all cases, of those with GFK2 and (with
                           --follow-up-due-days) of those whose follow-up is due; days from GFK2 to the
                           first follow-up
Block by the calendar period of the submission itself (--from/--until select the submissions):
  Einreichungen            submissions by type, those with follow-up content, the submissions the
                           export couldn't convert, the episodes without initial submission; also
                           of LEs the matrix doesn't list, e.g. those that only sent tests

How to read two of the figures:
  - The Zustimmungsquote (valid, of the transmitted consents) is an upper bound of the share of
    patients who agreed: who declines without signing has no consent transmitted, but the
    noScopeJustification "patient refuses to sign consent". The rate "mit dokumentierter
    Ablehnung" counts these cases in its base, as far as the export has the justifications.
  - By the period of the initial submission, a recent period holds only the early follow-ups: its
    follow-up rates are lower and its durations shorter, whatever the LE does. The lines
    "fällig: ... innerhalb der Frist" (--follow-up-due-days N) compare between periods: cases
    whose GFK2 is at least N days before the reference date, follow-ups within N days of the GFK2.

Output (<output> without extension):
  <output>.csv   long table LE | Zeitraum | Block | Kennzahl | Wert | Bezug | Nenner | Anteil_% |
                 Median | Q1 | Q3 | n, one line per LE, period and figure
  <output>.json  the same lines, with the periods, the LEs and notes
Counts below --min-cell (default 5) are suppressed ("<5"), percentages are left out if their count
or base is suppressed, medians and quartiles if computed from fewer values, and every count within
a group of fewer cases, also a zero. Neither output contains case keys or TANs. The cells per LE and
period are small all the same, and part of the suppressed ones follow from the totals and the other
cells: the tables, suppressed or not (--min-cell 1), are meant for the data nodes and the LE
concerned, not for publication.
plot_time_series.py draws the curves from the CSV.

Example:
  python3 rd_time_series.py export-datenkranz_2026-10-30_10-00-00.json \\
    --from 2024-07-01 --until 2026-11-01 --period quarter --follow-up-due-days 365 --output time-series
  uv run plot_time_series.py time-series.csv --output plots
"""

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import rd_evaluation_matrix as rdm


# ---------------------------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------------------------

WHOLE = "gesamt"            # the period of all cases, i.e. what the matrix evaluates
MONTH, QUARTER = "month", "quarter"

B_CASES = "Fälle"
B_CONSENT = "Forschungseinwilligung"
B_STATUS = "Diagnosestatus (alle Fälle)"             # the sections of the matrix
B_RATE = "Diagnoserate (abgeschlossene Fälle)"
B_FOLLOW_UP = "Follow-up"
B_SUBMISSIONS = "Einreichungen (Kalenderzeitraum der Einreichung)"

OPEN = f"{rdm.OPEN} (Undiagnostiziert, noch keine GFK2)"                    # as in the matrix

CASES = "Fallzahl gesamt (Episoden mit Erstmeldung)"                        # as in the matrix
UNREADABLE = "Fälle nicht auswertbar (nicht in den Fällen)"                 # as in the matrix

CONSENT_TRANSMITTED = "Broad Consent übermittelt (researchConsents[].scope)"
CONSENT_VALID = "Broad Consent gültig zum Datum der letzten Einreichung"    # as in the matrix
CONSENT_RATE = "Zustimmungsquote: gültig, von den übermittelten"
CONSENT_RATE_REFUSALS = "Zustimmungsquote mit dokumentierter Ablehnung: gültig, von übermittelt oder abgelehnt"
CONSENT_INVALID = "übermittelt, nicht gültig"
CONSENT_MISSING = "nicht übermittelt"

# why a transmitted broad consent doesn't permit research use
VALID, EXPIRED, DENIED, NO_PROVISION = "valid", "expired", "denied", "no provision"
INVALID = (
    (DENIED,       "abgelehnt (Forschungs-Provision deny)"),
    (EXPIRED,      "abgelaufen oder Zeitraum nicht erfüllt"),
    (NO_PROVISION, "Forschungs-Provision fehlt"),
)

# metaData.researchConsents[].noScopeJustification: the values of the schema (KDK/Submission.json)
JUSTIFICATIONS = (
    "patient unable to consent",
    "patient refuses to sign consent",
    "patient did not return consent documents",
    "other patient-related reason",
    "consent information cannot be submitted by LE due to technical reason",
    "consent is not implemented at LE due to organizational issues",
)
REFUSAL = "patient refuses to sign consent"              # the consent was presented and declined
OTHER_JUSTIFICATION = "anderer Wert (nicht im Schema)"   # counted, but never printed: it may be free text
NOT_AN_OBJECT = "scope ist kein FHIR-Objekt (nicht im Schema)"
NO_JUSTIFICATION = "ohne Begründung"
REASONS = JUSTIFICATIONS + (OTHER_JUSTIFICATION, NOT_AN_OBJECT, NO_JUSTIFICATION)

FOLLOW_UP_SUBMISSION = "Fälle mit Folgemeldung (submission.type followup)"
FOLLOW_UP_CONTENT = "Fälle mit Follow-up-Inhalt (followUp.followUpRds der letzten Einreichung)"
WITH_GFK2 = "Fälle mit GFK2"
GFK2_CONTENT = "mit GFK2: Follow-up-Inhalt"
GFK2_SUBMISSION = "mit GFK2: Folgemeldung ab der GFK2-Meldung"
DUE = "mit GFK2: Follow-up fällig"
DUE_CONTENT = "fällig: Follow-up-Inhalt"
DUE_SUBMISSION = "fällig: Folgemeldung ab der GFK2-Meldung"
DUE_IN_TIME = "fällig: erstes Follow-up innerhalb der Frist"
DAYS = "Tage von GFK2 bis zum ersten Follow-up"
DAYS_FROM_DATE = "Dauer aus followUpDate"
DAYS_FROM_SUBMISSION = "Dauer aus dem Datum der Folgemeldung (kein followUpDate ab GFK2)"
DUE_DAYS = "fällig: Tage von GFK2 bis zum ersten Follow-up innerhalb der Frist"

SUBMISSION_TYPES = ("initial", "followup", "addition", "correction", "test")    # metaData.submission.type
FOLLOW_UP = "followup"
OTHER_TYPE = "Einreichungen anderer Typ (nicht im Schema)"   # counted, the value is never printed
SUBMISSIONS = "Einreichungen gesamt (ohne Test)"
SUBMISSIONS_FOLLOW_UP = "Einreichungen mit Follow-up-Inhalt (followUp.followUpRds, ohne Test)"
WITHOUT_DATE = "Einreichungen ohne lesbares Datum (in keiner anderen Zeile, nur im Zeitraum gesamt)"
NOT_CONVERTED = "Einreichungen nicht konvertierbar (nach submittedAt)"
WITHOUT_INITIAL = "Episoden ohne Erstmeldung (nach ihrer ersten Einreichung)"


# ---------------------------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------------------------

def load_failures(path):
    """
    (site, submittedAt) of the submissions the export couldn't convert, as rdm.load_submissions
    selects them; it doesn't keep their time. Call it before rdm.load_submissions, so that the
    export is in memory only once.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not (isinstance(data, dict) and "tables" in data):
        return []
    tables = {t["name"]: t for t in data["tables"]}
    return [(r.get("site"), r.get("submittedAt") or "")
            for r in tables.get("failures", {}).get("rows", [])
            if r.get("useCase") in (None, "RD")]


def readable_date(s):
    """A date, None if there is none or it can't be read: the matrix reads only some of the dates"""
    try:
        return rdm.parse_date(s)
    except (TypeError, ValueError):
        return None


def readable_submission_date(content):
    try:
        return rdm.submission_date(content)
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------------------------

def period_of(d, resolution):
    """The month (YYYY-MM) or quarter (YYYY-Qn) of a date"""
    return f"{d.year}-{d.month:02d}" if resolution == MONTH else f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def period_bounds(d, resolution):
    """The first and the last day of the month or quarter of a date"""
    step = 1 if resolution == MONTH else 3
    month = d.month - (d.month - 1) % step
    following = date(d.year + (month + step > 12), (month + step - 1) % 12 + 1, 1)
    return date(d.year, month, 1), following - timedelta(days=1)


def periods_between(first, last, resolution):
    """All months or quarters from the one of a date to the one of another, also those without data"""
    periods = []
    start = period_bounds(first, resolution)[0]
    while start <= last:
        periods.append(period_of(start, resolution))
        start = period_bounds(start, resolution)[1] + timedelta(days=1)
    return periods


# ---------------------------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------------------------

def consent_state(research_consents, at):
    """
    VALID if the research consents permit research use at a date (rdm.permits_research_use), else
    what would have to differ for that: the periods of their research provisions (EXPIRED), also
    their types (DENIED), or they have no research provision to decide by (NO_PROVISION)
    """
    def relaxed(provision, permit):
        if not isinstance(provision, dict):
            return provision
        changed = {**provision, "period": {"start": at.isoformat()}}
        if permit:
            changed["type"] = "permit"
        if provision.get("provision"):
            changed["provision"] = [relaxed(p, permit) for p in provision["provision"]]
        return changed

    def consents(permit):
        return [{**rc, "scope": {**rc["scope"], "provision": relaxed(rc["scope"].get("provision"), permit)}}
                if isinstance(rc.get("scope"), dict) else rc
                for rc in research_consents]

    if rdm.permits_research_use(research_consents, at):
        return VALID
    if rdm.permits_research_use(consents(permit=False), at):
        return EXPIRED
    if rdm.permits_research_use(consents(permit=True), at):
        return DENIED
    return NO_PROVISION


def justification(research_consents):
    """
    Why no consent is transmitted: the noScopeJustification of the latest entry (by presentationDate);
    NOT_AN_OBJECT if a scope is given, but not as the FHIR Consent object the matrix reads
    """
    if any(rc.get("scope") is not None and not isinstance(rc["scope"], dict) for rc in research_consents):
        return NOT_AN_OBJECT
    given = [(str(rc.get("presentationDate") or ""), i, rc["noScopeJustification"])
             for i, rc in enumerate(research_consents) if rc.get("noScopeJustification")]
    if not given:
        return NO_JUSTIFICATION
    latest = max(given)[2]
    return latest if latest in JUSTIFICATIONS else OTHER_JUSTIFICATION


def gfk2(case):
    """
    (date of the GFK2, index of the first submission reporting it), the GFK2 being the first case
    conference after the indication board as in rdm.evaluate_case; None without
    """
    gfk1 = rdm.parse_date(case.latest["metaData"]["molecularBoardDecisionDate"])
    plans = [
        (rdm.parse_date(s["plan"]["carePlanRd"]["molecularBoardDecisionDate"]), i)
        for i, s in enumerate(case.submissions) if s.get("plan")
    ]
    return min(((d, i) for d, i in plans if d > gfk1), default=None)


def follow_up_dates(case):
    """
    The followUpDates of all submissions of a case: the DIP nodes transmit only the latest
    follow-up with each submission. Dates that can't be read are left out; the matrix doesn't
    read them either.
    """
    dates = set()
    for s in case.submissions:
        follow_ups = s.get("followUp")
        for f in (follow_ups.get("followUpRds") or []) if isinstance(follow_ups, dict) else []:
            dates.add(readable_date(f.get("followUpDate")) if isinstance(f, dict) else None)
    return sorted(dates - {None})


@dataclass
class Fact:
    """A case of the matrix, with what the time series counts in addition"""
    e: object                   # rdm.Evaluated
    period: str                 # of the initial submission
    consent: object             # VALID / EXPIRED / DENIED / NO_PROVISION, None if no consent is transmitted
    justification: object       # why no consent is transmitted, None if one is
    follow_up_submission: bool  # a submission of type followup
    gfk2_follow_up: bool        # one from the submission reporting the GFK2 on
    due: bool                   # GFK2 at least --follow-up-due-days before the reference date
    days: object                # from GFK2 to the first follow-up, None if unknown
    days_from_submission: bool  # ... to the follow-up submission, for lack of a followUpDate


def fact(case, e, resolution, reference_date, due_days):
    consents = case.latest["metaData"].get("researchConsents") or []
    transmitted = any(isinstance(rc.get("scope"), dict) for rc in consents)

    follow_ups = [i for i, s in enumerate(case.submissions) if rdm.submission_type(s) == FOLLOW_UP]
    board = gfk2(case)
    since_gfk2, due, days, from_submission = [], False, None, False
    if board:
        gfk2_date, reported = board
        # a follow-up submission includes the follow-up data (specification of the Datenkranz), so
        # the one that reports the GFK2 counts as well: GFK2 and first follow-up may come together
        since_gfk2 = [i for i in follow_ups if i >= reported]
        due = due_days is not None and (reference_date - gfk2_date).days >= due_days
        # as in the matrix, no durations of cases with impossible dates
        if not e.impossible_dates:
            documented = [d for d in follow_up_dates(case) if d >= gfk2_date]
            submitted = [readable_submission_date(case.submissions[i]) for i in since_gfk2]
            submitted = [d for d in submitted if d is not None and d >= gfk2_date]
            if documented or submitted:
                days = ((documented or submitted)[0] - gfk2_date).days
                from_submission = not documented

    return Fact(
        e                    = e,
        period               = period_of(rdm.submission_date(case.initial), resolution),
        consent              = consent_state(consents, rdm.submission_date(case.latest)) if transmitted else None,
        justification        = None if transmitted else justification(consents),
        follow_up_submission = bool(follow_ups),
        gfk2_follow_up       = bool(since_gfk2),
        due                  = due,
        days                 = days,
        days_from_submission = from_submission,
    )


# ---------------------------------------------------------------------------------------------
# Lines: (block, item) with a count or durations, evaluated per LE and period
# ---------------------------------------------------------------------------------------------

@dataclass
class Line:
    block: str
    item: str
    base_label: str = None      # what the percentage refers to, or the durations are taken from
    n: int = None               # count
    base: int = None            # denominator of the percentage, or the number of cases the durations are taken from
    days: list = None           # durations: median and quartiles instead of a count
    part: tuple = None          # (count, cases it is counted in) the base includes: left out, it would follow from the base


def case_lines(facts, unreadable, due_days):
    """The lines of the blocks by initial submission for a set of cases; the same lines for every set"""
    lines = []

    def count(block, item, pred, of=facts, base_label="Fälle", part=None):
        lines.append(Line(block, item, base_label, sum(1 for f in of if pred(f)), len(of), part=part))

    lines.append(Line(B_CASES, CASES, n=len(facts)))
    lines.append(Line(B_CASES, UNREADABLE, n=unreadable))

    b = B_CONSENT
    transmitted = [f for f in facts if f.consent is not None]
    missing = [f for f in facts if f.consent is None]
    refused = [f for f in missing if f.justification == REFUSAL]
    of_transmitted = "Fälle mit übermitteltem Broad Consent"
    count(b, CONSENT_TRANSMITTED, lambda f: f.consent is not None)
    count(b, CONSENT_VALID, lambda f: f.e.consent_at_submission)
    count(b, CONSENT_RATE, lambda f: f.e.consent_at_submission, transmitted, of_transmitted)
    count(b, CONSENT_RATE_REFUSALS, lambda f: f.e.consent_at_submission, transmitted + refused,
          f"Fälle mit übermitteltem Broad Consent oder Begründung '{REFUSAL}'", part=(len(refused), len(missing)))
    count(b, CONSENT_INVALID, lambda f: not f.e.consent_at_submission, transmitted, of_transmitted)
    for state, label in INVALID:
        count(b, f"{CONSENT_INVALID}: {label}", lambda f: f.consent == state, transmitted, of_transmitted)
    count(b, CONSENT_MISSING, lambda f: f.consent is None)
    for reason in REASONS:
        count(b, f"{CONSENT_MISSING}: {reason}", lambda f: f.justification == reason, missing,
              "Fälle ohne übermittelten Broad Consent")

    for status in rdm.CASE_STATUSES:
        count(B_STATUS, OPEN if status == rdm.OPEN else status, lambda f: f.e.case_status == status)
    closed = [f for f in facts if f.e.case_status != rdm.OPEN]
    for status in rdm.STATUSES:
        count(B_RATE, status, lambda f: f.e.status == status, closed, "abgeschlossene Fälle")

    b = B_FOLLOW_UP
    with_gfk2 = [f for f in facts if f.e.has_gfk2]
    timed = [f for f in with_gfk2 if f.days is not None]
    of_timed = "Fälle mit Dauer bis zum Follow-up"
    count(b, FOLLOW_UP_SUBMISSION, lambda f: f.follow_up_submission)
    count(b, FOLLOW_UP_CONTENT, lambda f: f.e.case_fields["followUp"])
    count(b, WITH_GFK2, lambda f: f.e.has_gfk2)
    count(b, GFK2_CONTENT, lambda f: f.e.case_fields["followUp"], with_gfk2, WITH_GFK2)
    count(b, GFK2_SUBMISSION, lambda f: f.gfk2_follow_up, with_gfk2, WITH_GFK2)
    lines.append(Line(b, DAYS, WITH_GFK2, base=len(with_gfk2), days=sorted(f.days for f in timed)))
    count(b, DAYS_FROM_DATE, lambda f: not f.days_from_submission, timed, of_timed)
    count(b, DAYS_FROM_SUBMISSION, lambda f: f.days_from_submission, timed, of_timed)
    if due_days is not None:
        # the cases every follow-up within the interval is known of: the same view on every period
        due = [f for f in with_gfk2 if f.due]
        in_time = [f for f in due if f.days is not None and f.days <= due_days]
        of_due = f"Fälle mit GFK2 mindestens {due_days} Tage vor dem Stichtag"
        count(b, DUE, lambda f: f.due, with_gfk2, WITH_GFK2)
        count(b, DUE_CONTENT, lambda f: f.e.case_fields["followUp"], due, of_due)
        count(b, DUE_SUBMISSION, lambda f: f.gfk2_follow_up, due, of_due)
        count(b, DUE_IN_TIME, lambda f: f.days is not None and f.days <= due_days, due, of_due)
        lines.append(Line(b, DUE_DAYS, of_due, base=len(due), days=sorted(f.days for f in in_time)))

    return lines


def submission_lines(counts):
    """The lines of the block by calendar period, from the counts of an LE and period"""
    items = [f"Einreichungen {t}" for t in SUBMISSION_TYPES]
    items += [OTHER_TYPE, SUBMISSIONS, SUBMISSIONS_FOLLOW_UP, WITHOUT_DATE, NOT_CONVERTED, WITHOUT_INITIAL]
    return [Line(B_SUBMISSIONS, item, n=counts.get(item, 0)) for item in items]


def suppressed(line, min_cell, cases=None):
    """
    The values of a line, counts 1..min_cell-1 replaced by '<min_cell' as in the matrix. Beyond the
    matrix, a count within 1..min_cell-1 cases is left out, also a zero: it tells of each of these
    cases what the count would. That holds for a base of that size and for the cases of the LE and
    period the line belongs to (`cases`, then also the base is left out). A line is left out as
    well if a part of its base is, which would follow from the base.
    """
    values = dict.fromkeys(("value", "base", "percent", "median", "q1", "q3", "n"))

    def small(n):
        return n is not None and 0 < n < min_cell

    if small(cases) or (line.part and any(small(n) for n in line.part)):
        return values
    if line.days is None:
        row = rdm.Row(line.block, line.item, rdm.COUNT, line.base_label, line.n, line.base)
        values["value"], values["base"], values["percent"] = rdm.suppressed(row, min_cell)
        if small(line.base):
            values["value"] = None
    else:
        n = len(line.days)
        values["base"] = f"<{min_cell}" if small(line.base) else line.base
        if not small(line.base):
            values["n"] = f"<{min_cell}" if small(n) else n
            if n >= max(min_cell, 1):
                q1, median, q3 = rdm.quartiles(line.days)
                values.update(median=rdm.rounded(median), q1=rdm.rounded(q1), q3=rdm.rounded(q3))
    return values


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def run(rows, failures, start, end, reference_date, min_cell=5, resolution=MONTH, due_days=None):
    """
    The time series of the submissions (rdm.SubmissionRow) and the failures of the export, given
    as (site, submittedAt)
    """
    cases, _, without_initial, submitters = rdm.build_cases(rows, start, end)

    def in_range(d):
        return not (start and d < start) and not (end and d >= end)

    # blocks by the period of the initial submission; the cases are those the matrix can evaluate
    facts, unreadable, dates = [], Counter(), []
    for case in cases:
        initial_date = rdm.submission_date(case.initial)
        dates.append(initial_date)
        try:
            # the cases to clarify are those of the matrix, none are added here (min_medical_days)
            e = rdm.evaluate_case(case, reference_date, 0)
        except (KeyError, TypeError, ValueError):
            unreadable[(case.le, period_of(initial_date, resolution))] += 1
            continue
        facts.append(fact(case, e, resolution, reference_date, due_days))

    # block by the calendar period of the submissions; a period None is counted in WHOLE only
    events = Counter()      # (LE, period, item)
    first_rows = {}         # first non-test submission of each episode, as build_cases orders them
    for row in rows:
        content = row.content
        if content.get("metaData", {}).get("submission", {}).get("diseaseType") != "rare":
            continue
        kind, submitted = rdm.submission_type(content), readable_submission_date(content)
        if kind != "test" and (row.case not in first_rows or row.submitted_at < first_rows[row.case].submitted_at):
            first_rows[row.case] = row
        le = rdm.leistungserbringer(row)
        if submitted is None:
            events[(le, None, WITHOUT_DATE)] += 1
            continue
        if not in_range(submitted):
            continue
        dates.append(submitted)
        period = period_of(submitted, resolution)
        events[(le, period, f"Einreichungen {kind}" if kind in SUBMISSION_TYPES else OTHER_TYPE)] += 1
        if kind != "test":
            events[(le, period, SUBMISSIONS)] += 1
            follow_ups = content.get("followUp")
            if isinstance(follow_ups, dict) and follow_ups.get("followUpRds"):
                events[(le, period, SUBMISSIONS_FOLLOW_UP)] += 1
    for le, key, _ in without_initial:
        submitted = readable_submission_date(first_rows[key].content)
        if submitted is None or in_range(submitted):
            dates += [submitted] if submitted else []
            events[(le, period_of(submitted, resolution) if submitted else None, WITHOUT_INITIAL)] += 1
    for site, submitted_at in failures:
        submitted = readable_date(submitted_at)
        if submitted is None or in_range(submitted):
            dates += [submitted] if submitted else []
            events[(str(site or "?"), period_of(submitted, resolution) if submitted else None, NOT_CONVERTED)] += 1

    first = start or min(dates, default=None)
    last = end - timedelta(days=1) if end else max(dates, default=None)
    periods = periods_between(first, last, resolution) if first and last else []
    # the LEs of the matrix (it lists every LE with a case or a submission to clarify, whatever its
    # date), and those that only have submissions in this block, e.g. tests
    les = sorted({f.e.le for f in facts} | {le for le, _ in unreadable} | {le for le, _, _ in without_initial}
                 | {str(site or "?") for site, _ in failures} | {le for le, _, _ in events})

    # every count also in total and over the whole period
    def spread(le, period):
        return [(entity, p) for entity in (rdm.TOTAL, le) for p in (WHOLE, period) if p is not None]

    case_sets, unreadable_counts, event_counts = {}, Counter(), {}
    for f in facts:
        for key in spread(f.e.le, f.period):
            case_sets.setdefault(key, []).append(f)
    for (le, period), n in unreadable.items():
        for key in spread(le, period):
            unreadable_counts[key] += n
    for (le, period, item), n in events.items():
        for key in spread(le, period):
            event_counts.setdefault(key, Counter())[item] += n

    result_rows = []
    for le in [rdm.TOTAL] + les:
        for period in periods + [WHOLE]:
            key = (le, period)
            of = case_sets.get(key, [])
            lines = [(line, None if line.block == B_CASES else len(of))
                     for line in case_lines(of, unreadable_counts[key], due_days)]
            lines += [(line, None) for line in submission_lines(event_counts.get(key, {}))]
            for line, group in lines:
                result_rows.append({"le": le, "period": period, "block": line.block, "item": line.item,
                                    "base_label": line.base_label, **suppressed(line, min_cell, group)})

    # the evaluated period, named as in the matrix
    if start or end:
        until = end - timedelta(days=1) if end else None
        evaluated = f"{start.strftime('%d.%m.%Y') if start else '…'} - {until.strftime('%d.%m.%Y') if until else '…'}"
    else:
        initial_dates = [rdm.submission_date(c.initial) for c in cases]
        evaluated = (f"gesamter Export (Erstmeldungen {min(initial_dates).strftime('%d.%m.%Y')} - "
                     f"{max(initial_dates).strftime('%d.%m.%Y')})" if initial_dates else "gesamter Export")

    labels = {le: f"{le} ({', '.join(sorted(submitters[le]))})" if le in submitters else le for le in les}
    unit = "Monat" if resolution == MONTH else "Quartal"
    totals = event_counts.get((rdm.TOTAL, WHOLE), {})

    # the periods the evaluation covers in part only
    incomplete = []
    if periods:
        if first != period_bounds(first, resolution)[0]:
            incomplete.append(f"der erste ({periods[0]}) beginnt am {first.strftime('%d.%m.%Y')}")
        if last != period_bounds(last, resolution)[1]:
            incomplete.append(f"der letzte ({periods[-1]}) endet am {last.strftime('%d.%m.%Y')}"
                              + ("" if end else ", dem Datum der letzten Einreichung"))

    notes = [
        f"{len(facts)} Fälle (Episoden mit Erstmeldung) ausgewertet wie in der Auswertematrix (rd_evaluation_matrix.py), "
        f"Stand je Fall aus der letzten Nicht-Test-Einreichung; der Zeitraum '{WHOLE}' entspricht der Matrix.",
        f"Zeitraum der Blöcke '{B_CASES}', '{B_CONSENT}', '{B_STATUS}', '{B_RATE}' und '{B_FOLLOW_UP}': {unit} der "
        f"Erstmeldung des Falls. Zeitraum des Blocks '{B_SUBMISSIONS}': {unit} der jeweiligen Einreichung "
        "(metaData.submission.date, nicht konvertierbare Einreichungen nach submittedAt); mit --from/--until zählen "
        "dort nur Einreichungen in diesem Zeitraum, während die Matrix alle nicht konvertierbaren Einreichungen und "
        "alle Episoden ohne Erstmeldung des Exports zählt.",
        (f"Zellen mit 1 bis {min_cell - 1} Fällen sind unterdrückt (<{min_cell}), ebenso Anteile mit unterdrücktem Wert "
         f"oder Nenner sowie Median und Quartile aus weniger als {min_cell} Werten. Zählwerte innerhalb von 1 bis "
         f"{min_cell - 1} Fällen bleiben leer, auch die Null (die Matrix weist sie aus). Weitergabesicher ist die "
         "Tabelle damit nicht: ein Teil der unterdrückten Zellen folgt aus den Summen und den übrigen Zellen. Sie ist "
         "für die Datenknoten und den jeweiligen LE bestimmt."
         if min_cell > 1 else
         "Keine Zellen unterdrückt (--min-cell 1): nur für den internen Gebrauch der Datenknoten."),
        "Broad Consent bewertet zum Datum der letzten Einreichung. Übermittelt: mindestens ein Eintrag in "
        "metaData.researchConsents mit FHIR-Consent (scope). Nicht gültig, weil abgelaufen oder Zeitraum nicht "
        "erfüllt: mit gültigen Zeiträumen der Provisionen wäre der Consent gültig; abgelehnt: auch dann nicht, die "
        "Forschungs-Provisionen sind deny. Nicht übermittelt: nach noScopeJustification des jüngsten Eintrags.",
        "Zustimmungsquote: gültige unter den übermittelten Consents, also eine Obergrenze der Zustimmung unter den "
        "Gefragten. Wer ohne unterschriebenes Formular ablehnt, steht unter 'nicht übermittelt' "
        f"(noScopeJustification '{REFUSAL}'); die Quote mit dokumentierter Ablehnung nimmt diese Fälle in den Nenner. "
        "Abgelaufene Consents zählen als nicht gültig.",
        "Follow-up: 'Follow-up-Inhalt' ist followUp.followUpRds der letzten Einreichung, 'Folgemeldung ab der "
        "GFK2-Meldung' eine Einreichung vom Typ followup ab der Einreichung, die die GFK2 erstmals meldet (laut "
        "Spezifikation enthält eine Folgemeldung die Follow-up-Daten). Dauer: von der GFK2 bis zum frühesten "
        "followUpDate ab der GFK2 über alle Einreichungen des Falls, ersatzweise bis zum Datum der ersten Folgemeldung "
        "ab der GFK2-Meldung; ohne Fälle mit unmöglichem Datum (siehe Rückfragen der Matrix).",
        "Follow-up-Anteile und Dauern nach dem Zeitraum der Erstmeldung sind zwischen den Zeiträumen nicht "
        "vergleichbar: junge Zeiträume enthalten erst die frühen Follow-ups, ihr Anteil liegt niedriger und ihr "
        "Median kürzer, auch bei gleichem Verlauf. "
        + (f"Vergleichbar sind die Zeilen 'fällig: … innerhalb der Frist': Fälle mit GFK2 mindestens {due_days} Tage "
           f"vor dem Stichtag {reference_date.isoformat()}, Follow-ups bis {due_days} Tage nach der GFK2."
           if due_days is not None else
           "Mit --follow-up-due-days N kommen vergleichbare Zeilen hinzu (Fälle mit GFK2 mindestens N Tage vor dem "
           "Stichtag, Follow-ups bis N Tage nach der GFK2)."),
        "LE: Standort des Exports, sonst metaData.submission.submitterId. LEs: " + ", ".join(labels[le] for le in les) + ".",
    ]
    if start or end:
        notes.append(f"Fälle mit Erstmeldung vom {start or '…'} bis vor dem {end or '…'}.")
    if incomplete:
        notes.append(f"Unvollständige Zeiträume: {' und '.join(incomplete)}. Zählwerte dieser Zeiträume sind mit den "
                     "übrigen nicht vergleichbar.")
    if not facts:
        notes.append("Kein Fall mit Erstmeldung im ausgewerteten Zeitraum: die Blöcke nach Erstmeldung sind leer.")
    if any(f.consent is None for f in facts) and all(f.justification == NO_JUSTIFICATION for f in facts if f.consent is None):
        notes.append("Kein Fall ohne übermittelten Broad Consent trägt eine Begründung (noScopeJustification): "
                     "Ablehnungen sind in diesen Daten nicht erkennbar, beide Zustimmungsquoten sind Obergrenzen.")
    if totals.get(OTHER_TYPE):
        notes.append(f"Einreichungen mit metaData.submission.type außerhalb des Schemas ({', '.join(SUBMISSION_TYPES)}) "
                     f"stehen unter '{OTHER_TYPE}'.")
    if totals.get(WITHOUT_DATE):
        notes.append("Einreichungen ohne lesbares metaData.submission.date stehen, gleich welchen Typs, nur unter "
                     f"'{WITHOUT_DATE}'.")

    return {
        "title": f"ZEITREIHEN {evaluated}",
        "period": evaluated,
        "resolution": resolution,
        "periods": periods + [WHOLE],
        "min_cell": min_cell,
        "follow_up_due_days": due_days,
        "reference_date": reference_date.isoformat(),
        "entities": [rdm.TOTAL] + les,
        "labels": {rdm.TOTAL: rdm.TOTAL, **labels},
        "rows": result_rows,
        "notes": notes,
    }


def write_csv(path, result, delimiter=";"):
    """Long table: one line per LE, period and figure"""
    def fmt(x):
        return str(x).replace(".", ",") if isinstance(x, float) and delimiter == ";" else ("" if x is None else x)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["LE", "Zeitraum", "Block", "Kennzahl", "Wert", "Bezug", "Nenner", "Anteil_%",
                         "Median", "Q1", "Q3", "n"])
        for r in result["rows"]:
            writer.writerow([result["labels"][r["le"]], r["period"], r["block"], r["item"], fmt(r["value"]),
                             r["base_label"] or "", fmt(r["base"]), fmt(r["percent"]),
                             fmt(r["median"]), fmt(r["q1"]), fmt(r["q3"]), fmt(r["n"])])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="result JSON of export-datenkranz, or a JSON list of submissions")
    parser.add_argument("--from", dest="start", type=date.fromisoformat,
                        help="initial (block Einreichungen: any submission) submitted on or after (YYYY-MM-DD)")
    parser.add_argument("--until", dest="end", type=date.fromisoformat,
                        help="initial (block Einreichungen: any submission) submitted before (YYYY-MM-DD)")
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date.today(),
                        help="date the follow-ups are due at, see --follow-up-due-days; best the date of the export "
                             "(default: today)")
    parser.add_argument("--min-cell", type=int, default=5,
                        help="counts below are suppressed, 1 disables (default: %(default)s)")
    parser.add_argument("--period", choices=(MONTH, QUARTER), default=MONTH, help="resolution (default: %(default)s)")
    parser.add_argument("--follow-up-due-days", type=int,
                        help="also report the follow-ups of the cases whose GFK2 is at least this many days before "
                             "the reference date, and those within as many days after the GFK2 (default: not reported)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s, for Excel with German locale)")
    parser.add_argument("--output", default="rd-time-series", help="output path without extension (default: %(default)s)")
    args = parser.parse_args(argv)
    if args.start and args.end and args.start >= args.end:
        parser.error("--from must be before --until")

    failures = load_failures(args.input)
    rows, _ = rdm.load_submissions(args.input)
    result = run(rows, failures, args.start, args.end, args.reference_date, args.min_cell, args.period,
                 args.follow_up_due_days)

    with open(f"{args.output}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    write_csv(f"{args.output}.csv", result, args.delimiter)

    for note in result["notes"]:
        print(note, file=sys.stderr)
    print(f"Written: {args.output}.json, {args.output}.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
