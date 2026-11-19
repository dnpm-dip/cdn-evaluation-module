#!/usr/bin/env python3
"""
Data quality queries ("Daten Qualitätsabfragen und Statistik") on BfArM Datenkranz submissions.

Specification: sheet "Datenqualität_KDK (V2) opt" of docs/Daten Qualitätsabfragen und Statistik.V4sb.xlsx,
one entry of FIELDS per row with an "angezeigtes Ergebnis" (column C); rows without one are not
evaluated. Standard library only, so it can be shared with the other clinical data nodes (KDKs).

Input: the result JSON of the evaluation module's "export-datenkranz", read row by row, so that
exports larger than memory can be evaluated.

Output: a CSV (";"-separated, UTF-8 with BOM, for Excel) with one line per field and value: the
count over all sites ("Gesamt") and per Leistungserbringer (submitterId). "Auswertung (Enum)"
lists all values the schema allows (also those not occurring), values not in the schema, and
"(fehlt)" for elements without the field.

Every non-test submission in the period counts, including corrections, additions and follow-ups,
which repeat the whole record. Fields within lists (e.g. variants) are counted per list element;
submissions without any such element are counted as "(Submission ohne Einträge)".

Example:
  python3 data_quality_mvh.py export-datenkranz_2026-11-11_10-00-00.json \\
    --from 2026-01-01 --until 2027-01-01 --output datenqualitaet.csv
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------------------------
# Specification: the rows of the sheet with an "angezeigtes Ergebnis"
# ---------------------------------------------------------------------------------------------

ENUM, PRESENCE, CHANGES, NOT_AVAILABLE = "enum", "presence", "changes", "not-available"
RARE, ONCOLOGICAL = "rare", "oncological"

MISSING     = "(fehlt)"
EMPTY_LIST  = "(leere Liste)"
NO_ENTRIES  = "(Submission ohne Einträge)"
PRESENT     = "Eingabe"
NOT_PRESENT = "keine Eingabe"
NO_CHANGE   = "(keine Änderung)"
NO_PREVIOUS = "(keine vorherige Submission im Export)"
NO_CHANGES  = "(keine Korrekturen/Ergänzungen im Zeitraum)"

LIBRARY_TYPES    = ("panel", "wes", "wgs", "wgs_lr", "none")
DIAGNOSTIC_TYPES = ("array", "single", "karyotyping", "panel", "exome", "genomeShortRead", "genomeLongRead", "other", "none")
VITAL_STATUS     = ("living", "deceased")
REGISTERS        = ("NCT", "DRKS", "Eudra-CT/CTIS", "other")
RECIST           = ("CR", "PR", "SD", "PD")
TERMINATION      = ("E", "R", "W", "A", "P", "S", "V", "T", "U")
EVIDENCE_LEVELS  = ("m1A", "m1B", "m1C", "m2A", "m2B", "m2C", "m3", "m4", "undefined")
VARIANT_LISTS    = ("molecular.smallVariants[]", "molecular.structuralVariants[]", "molecular.copyNumberVariants[]")

# fields of the diff of corrections and additions that always differ
DIFF_IGNORED = {"metaData.submission.date", "metaData.submission.type", "metaData.tanC"}


@dataclass
class Field:
    row: int                  # row in the sheet
    section: str
    content: str              # column A "Inhalt"
    name: str                 # column B "Datenkranz-Feldname"
    result: str               # column C "angezeigtes Ergebnis"
    kind: str
    paths: tuple = ()         # JSON paths in the Datenkranz, "[]" marking lists
    known: tuple = ()         # the values the schema allows
    disease: str = None       # only submissions of this disease type
    with_tests: bool = False
    note: str = ""


def fields():
    ae = "Auswertung (Enum)"
    rd, onco = "RD Case", "Oncology"
    return [
        Field(6, "Metadaten", "Submission", "type:initial/correction/addition/follow-Up", "Anzahl (im definierten Zeitraum)",
              ENUM, ("metaData.submission.type",), ("initial", "correction", "addition", "followup", "test"), with_tests=True,
              note="einschließlich Testsubmissions, die sonst nicht ausgewertet werden"),
        Field(7, "Metadaten", "Submission", "type:correction/(addition)", "Anzeige korrigierter Parameter und Häufigkeit",
              CHANGES, note="Felder, in denen sich die Submission von der vorherigen ihres Falls unterscheidet; "
                            "Felder in Listen zusammengefasst ([])"),
        Field(10, "Metadaten", "Indikation", "diseaseType", "Hereditary/onco/rare",
              ENUM, ("metaData.submission.diseaseType",), ("oncological", "rare", "hereditary")),

        Field(19, rd, "diagnosisRd", "Diagnostic Extent", ae, ENUM, ("case.diagnosisRd.diagnosticExtent",),
              ("singleGenome", "duoGenome", "trioGenome"), RARE),
        Field(20, rd, "diagnosisRd", "library Type", ae, ENUM, ("case.diagnosisRd.libraryType",), LIBRARY_TYPES, RARE),
        Field(21, rd, "diagnosisRd", "diagnosticAssessment", ae, ENUM, ("case.diagnosisRd.diagnosticAssessment",),
              ("noGeneticDiagnosis", "suspectedGeneticDiagnosis", "furtherGeneticDiagnosticRecommended",
               "confirmedGeneticDiagnosis", "partialGeneticDiagnosis"), RARE),
        Field(23, rd, "diagnosisRd", "no matching code exists", "Anzahl keine Eingabe/Anzahl Eingabe",
              PRESENCE, ("case.diagnosisRd.noMatchingCodeExists",), disease=RARE),
        Field(24, rd, "diagnosisRd", "Diagnose GMFCS", ae, ENUM, ("case.diagnosisRd.diagnosisGmfcs",),
              ("I", "II", "III", "IV", "V"), RARE),
        Field(25, rd, "PriorDiagnosis", "genomicTestType", ae, ENUM, ("case.priorRds[].genomicTestType",), DIAGNOSTIC_TYPES, RARE),
        Field(26, rd, "PriorDiagnosis", "diagnostic result", ae, ENUM, ("case.priorRds[].diagnosticResult",),
              ("phenotypeNotCompletelyClarified", "structuralVariantWithUnclearBreakpoint", "unclearVariantInGeneticDisease",
               "noPathogenicVariantDetected", "other"), RARE),
        Field(31, rd, "Molecular Report", "zygosity", ae, ENUM, tuple(f"{v}.zygosity" for v in VARIANT_LISTS),
              ("heterozygous", "homozygous", "compHet", "hemi", "homoplasmic", "heteroplasmic"), RARE),
        Field(32, rd, "Molecular Report", "segregation analysis", ae, ENUM, tuple(f"{v}.segregationAnalysis" for v in VARIANT_LISTS),
              ("notPerformed", "deNovo", "fromFather", "fromMother", "fromMotherAndFather"), RARE),
        Field(33, rd, "Molecular Report", "Mode of inheritance", ae, ENUM, tuple(f"{v}.modeOfInheritance" for v in VARIANT_LISTS),
              ("dominant", "recessive", "mitochondrial", "XLinked", "unclear"), RARE),
        Field(36, rd, "Recommended studies", "register", ae, ENUM, ("plan.recommendedStudies[].register",), REGISTERS, RARE),
        Field(38, rd, "Recommended therapies", "typ", ae, ENUM, ("plan.recommendedTherapies[].type",), ("causal", "symptomatic"), RARE),
        Field(39, rd, "Recommended therapies", "strategy", ae, ENUM, ("plan.recommendedTherapies[].strategy",),
              ("systemicMedication", "targetedMedication", "preventionMedication", "genetic", "prophylactic",
               "earlyDetection", "combination", "nutrition", "other"), RARE),
        Field(42, rd, "Follow-up", "phenotypes[].change", ae, ENUM, ("followUp.followUpRds[].phenotypes[].change",),
              ("newlyAdded", "improved", "degraded", "noLongerObserved", "unchanged"), RARE),
        Field(43, rd, "Follow-up", "vitalStatus", ae, ENUM, ("followUp.followUpRds[].vitalStatus",), VITAL_STATUS, RARE),

        Field(51, onco, "Diagnose", "ecogPerformanceStatus (Score)", ae, ENUM, ("case.diagnosisOd.ecogPerformanceStatusScore",),
              ("0", "1", "2", "3", "4", "5", "unknown"), ONCOLOGICAL),
        Field(52, onco, "Diagnose", "Tumor Grading/Differenzierungsgrad", ae, ENUM, ("case.diagnosisOd.grading",),
              ("0", "1", "2", "3", "4", "5", "X", "L", "M", "H", "B", "T", "U"), ONCOLOGICAL),
        Field(56, onco, "Diagnose", "libraryType (Sequencing)", ae, ENUM, ("case.diagnosisOd.libraryType",), LIBRARY_TYPES, ONCOLOGICAL),
        Field(57, onco, "Diagnose", "diagnosticAssessment", ae, NOT_AVAILABLE, disease=ONCOLOGICAL,
              note="nicht im Datenkranz Onkologie enthalten (nur diagnosisRd)"),
        Field(58, onco, "PriorDiagnosis", "type", ae, ENUM, ("case.priorDiagnostics[].type",), DIAGNOSTIC_TYPES, ONCOLOGICAL),
        Field(59, onco, "PriorProcedures", "treatment type", ae, ENUM, ("case.priorProcedures[].treatmentType",),
              ("A", "I", "O", "N", "S"), ONCOLOGICAL),
        Field(60, onco, "PriorProcedures", "intention", ae, ENUM, ("case.priorProcedures[].intention",), ("K", "P", "S", "X"), ONCOLOGICAL),
        Field(63, onco, "PriorProcedures", "terminationReasonOB", ae, ENUM, ("case.priorProcedures[].terminationReasonOBDS",),
              TERMINATION, ONCOLOGICAL),
        Field(64, onco, "PriorProcedures", "DStherapyResponse", ae, ENUM, ("case.priorProcedures[].therapyResponse",), RECIST, ONCOLOGICAL),
        Field(66, onco, "molecular report", "genomicSource", ae, ENUM,
              ("molecular.smallVariants[].genomicSource", "molecular.copyNumberVariants[].genomicSource"),
              ("somatic", "germline"), ONCOLOGICAL),
        Field(79, onco, "recommendedSystemicTherapies", "evidenceLevel", ae, ENUM,
              ("plan.recommendedSystemicTherapies[].evidenceLevel",), EVIDENCE_LEVELS, ONCOLOGICAL),
        Field(81, onco, "recommendedSystemicTherapies", "Therapeutic strategy", ae, ENUM,
              ("plan.recommendedSystemicTherapies[].therapeuticStrategy",),
              ("CH", "HO", "IM", "ZS", "SZ", "CI", "CZ", "CIZ", "IZ", "SO"), ONCOLOGICAL),
        Field(82, onco, "recommendedStudies", "register", ae, ENUM, ("plan.recommendedStudies[].register",), REGISTERS, ONCOLOGICAL),
        Field(83, onco, "recommendedStudies", "evidenceLevel", ae, ENUM, ("plan.recommendedStudies[].evidenceLevel",),
              EVIDENCE_LEVELS, ONCOLOGICAL),
        Field(84, onco, "recommendedStudies", "evidenceLevel Detail", ae, ENUM, ("plan.recommendedStudies[].evidenceLevelDetails[]",),
              ("is", "iv", "Z", "R"), ONCOLOGICAL),
        Field(90, onco, "Follow up", "vitalStatus", ae, ENUM, ("followUp.followUpOds[].vitalStatus",), VITAL_STATUS, ONCOLOGICAL),
        Field(95, onco, "Follow up Therapy", "therapyResponse", ae, ENUM, ("followUp.followUpOds[].therapies[].therapyResponse",),
              RECIST, ONCOLOGICAL),
        Field(97, onco, "Follow up Therapy", "terminationReasonOB", ae, ENUM,
              ("followUp.followUpOds[].therapies[].terminationReasonOBDS",), TERMINATION, ONCOLOGICAL),
        Field(98, onco, "Prefventive Measures", "Result", ae, NOT_AVAILABLE, disease=ONCOLOGICAL,
              note="nicht im Datenkranz Onkologie enthalten"),
    ]


# ---------------------------------------------------------------------------------------------
# Input: the export, row by row
# ---------------------------------------------------------------------------------------------

class Export:
    """
    The rows of table "datenkranz" of an export-datenkranz result, parsed one at a time, so that
    the file is never in memory as a whole. Once they are consumed, `failures` (number of rows of
    table "failures") and `notes` are set.
    """

    CHUNK = 4 * 1024 * 1024
    _ROWS = re.compile(r'"rows"\s*:\s*\[')

    def __init__(self, path):
        self.path = path
        self.failures = None
        self.notes = []

    def rows(self):
        decoder = json.JSONDecoder()
        with open(self.path, encoding="utf-8") as f:
            buf, eof = "", False

            def more():
                nonlocal buf, eof
                chunk = f.read(self.CHUNK)
                eof = not chunk
                buf += chunk
                return not eof

            # header: everything up to the rows of the first table, which must be "datenkranz"
            while not (m := self._ROWS.search(buf)):
                if not more():
                    raise ValueError(f"{self.path}: no tables, not a result of export-datenkranz")
            header = self._parse(buf[:m.end()] + "]}]}", "header")
            tables = header.get("tables") or [{}]
            if tables[-1].get("name") != "datenkranz":
                raise ValueError(f"{self.path}: first table isn't 'datenkranz', not a result of export-datenkranz")
            self.notes = header.get("notes", [])
            buf = buf[m.end():]

            pos = 0
            while True:
                while True:
                    while pos < len(buf) and buf[pos] in " \t\r\n,":
                        pos += 1
                    if pos < len(buf) or not more():
                        break
                if pos >= len(buf):
                    raise ValueError(f"{self.path}: unexpected end of file in the rows")
                if buf[pos] == "]":
                    break
                try:
                    row, end = decoder.raw_decode(buf, pos)
                except json.JSONDecodeError:
                    if more():
                        continue
                    raise
                yield row
                buf, pos = buf[end:], 0

            # the rest (failures, notes, collection) is small: the remainder of the JSON, with
            # the table of the rows reopened
            rest = buf[pos + 1:] + f.read()
        tail = self._parse('{"tables":[{"rows":0' + rest, "end")
        failures = next((t for t in tail.get("tables", []) if t.get("name") == "failures"), {"rows": []})
        self.failures = len(failures.get("rows", []))
        self.notes = tail.get("notes", self.notes)

    def _parse(self, text, part):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{self.path}: unexpected structure of the {part} of the export: {e}") from None


# ---------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------

def label(value):
    if value is None:
        return MISSING
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if value.strip() else "(leer)"
    return json.dumps(value, ensure_ascii=False)


def elements(node, segments):
    """The objects at the path, through lists marked with "[]"; None for a missing object"""
    if not segments:
        return [node]
    segment, rest = segments[0], segments[1:]
    is_list = segment.endswith("[]")
    value = node.get(segment[:-2] if is_list else segment) if isinstance(node, dict) else None
    if is_list:
        return [e for item in (value if isinstance(value, list) else []) for e in elements(item, rest)]
    return elements(value, rest)


def field_values(content, paths, kind):
    """The values counted for a field of a submission: per element of the list containing it"""
    values, in_list = [], False
    for path in paths:
        *container, leaf = path.split(".")
        in_list = in_list or any(s.endswith("[]") for s in container)
        for element in elements(content, container):
            leaf_list = leaf.endswith("[]")
            value = element.get(leaf[:-2] if leaf_list else leaf) if isinstance(element, dict) else None
            if kind == PRESENCE:
                values.append(NOT_PRESENT if value is None else PRESENT)
            elif leaf_list and isinstance(value, list):
                if value:
                    values.extend(label(v) for v in value)
                else:
                    values.append(EMPTY_LIST)
            else:
                values.append(label(value))
    if in_list and not values:
        values.append(NO_ENTRIES)
    return values


def flatten(node, path="", out=None):
    """Leaf path (lists marked with "[]") -> multiset of the values there"""
    if out is None:
        out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            flatten(v, f"{path}.{k}" if path else k, out)
    elif isinstance(node, list):
        for v in node:
            flatten(v, path + "[]", out)
    else:
        out.setdefault(path, Counter())[json.dumps(node, sort_keys=True)] += 1
    return out


def changed_paths(previous, current):
    paths = (set(previous) | set(current)) - DIFF_IGNORED
    return sorted(p for p in paths if previous.get(p) != current.get(p))


@dataclass
class Evaluation:
    start: str = None
    end: str = None
    fields: list = field(default_factory=fields)
    counts: dict = field(default_factory=dict)       # field row -> value -> LE -> n
    submitters: dict = field(default_factory=dict)   # LE -> label
    submissions: Counter = field(default_factory=Counter)
    tests: Counter = field(default_factory=Counter)
    out_of_order: int = 0
    _case: tuple = None
    _previous: dict = None
    _seen: set = field(default_factory=set)

    def count(self, f, value, submitter):
        self.counts.setdefault(f.row, {}).setdefault(value, Counter())[submitter] += 1

    def add(self, row):
        content = row["content"]
        submission = content.get("metaData", {}).get("submission", {})
        submitter = str(submission.get("submitterId") or "?")
        self.submitters.setdefault(submitter, f"{row.get('site')} ({submitter})")
        is_test = submission.get("type") == "test"
        date = (submission.get("date") or "")[:10]
        in_period = (not self.start or date >= self.start) and (not self.end or date < self.end)

        # the previous submission of the case, for the changes of corrections and additions: the
        # export lists the submissions case by case, in the order of their submission
        case = (row.get("site"), row.get("useCase"), row.get("patient"), row.get("episodeOfCare"))
        if case != self._case:
            if case in self._seen:
                self.out_of_order += 1
            self._seen.add(case)
            self._case, self._previous = case, None
        current = flatten(content) if not is_test else None

        if in_period:
            if is_test:
                self.tests[submitter] += 1
            else:
                self.submissions[submitter] += 1
            for f in self.fields:
                if (is_test and not f.with_tests) or f.kind == NOT_AVAILABLE:
                    continue
                if f.disease and submission.get("diseaseType") != f.disease:
                    continue
                if f.kind == CHANGES:
                    if submission.get("type") in ("correction", "addition"):
                        if self._previous is None:
                            self.count(f, NO_PREVIOUS, submitter)
                        else:
                            changes = changed_paths(self._previous, current)
                            for path in changes or [NO_CHANGE]:
                                self.count(f, path, submitter)
                else:
                    for value in field_values(content, f.paths, f.kind):
                        self.count(f, value, submitter)

        if not is_test:
            self._previous = current

    def lines(self, failures):
        """The lines of the CSV, after the header"""
        les = sorted(self.submitters, key=lambda s: self.submitters[s])
        period = f"{self.start or 'Anfang'} bis {self.end or 'Ende'} (exklusiv)"

        def line(section, row, content, name, result, evaluated, unit, value, per_le, note=""):
            total = sum(per_le.values()) if per_le is not None else ""
            cells = [per_le.get(s, 0) for s in les] if per_le is not None else [""] * len(les)
            return [section, row, content, name, result, evaluated, unit, value, total, *cells, note]

        out = [
            line("Übersicht", "", "Submissions", "", "Anzahl ausgewertet", "", "Submissions", "ohne Tests",
                 self.submissions, f"Zeitraum (submission:date): {period}"),
            line("Übersicht", "", "Submissions", "", "Anzahl nicht ausgewertet", "", "Submissions", "Tests", self.tests),
        ]
        if failures:
            out.append(line("Übersicht", "", "Export", "", "nicht konvertierbar", "", "Submissions", "failures", None,
                            f"{failures} Submissions konnten nicht in den Datenkranz konvertiert werden, siehe Tabelle 'failures' des Exports"))
        if self.out_of_order:
            out.append(line("Übersicht", "", "Export", "", "Reihenfolge", "", "Fälle", "nicht zusammenhängend", None,
                            f"{self.out_of_order}x folgten Submissions eines Falls nicht aufeinander; deren Änderungen gegenüber der vorherigen Submission fehlen"))

        for f in self.fields:
            evaluated = ", ".join(f.paths)
            unit = self.unit(f)
            common = (f.section, f.row, f.content, f.name, f.result, evaluated, unit)
            if f.kind == NOT_AVAILABLE:
                out.append(line(*common, "", None, f.note))
                continue
            counts = self.counts.get(f.row, {})
            for value in self.ordered(f, counts):
                out.append(line(*common, value, counts.get(value, Counter()), f.note))
        return les, out

    @staticmethod
    def unit(f):
        if f.kind == CHANGES:
            return "Korrektur-/Ergänzungs-Submissions"
        lists = []
        for path in f.paths:
            *container, leaf = path.split(".")
            names = [s[:-2] for s in container if s.endswith("[]")]
            if names and names[-1] not in lists:
                lists.append(names[-1])
        if not lists:
            return "Submissions"
        return f"je Eintrag in {', '.join(lists)}" + (" (je Wert)" if any(p.endswith("[]") for p in f.paths) else "")

    @staticmethod
    def ordered(f, counts):
        """Known values in the schema's order, then unknown ones, then the special values"""
        special = (EMPTY_LIST, MISSING, NO_ENTRIES, PRESENT, NOT_PRESENT, NO_CHANGE, NO_PREVIOUS)
        if f.kind == CHANGES:
            changes = sorted((v for v in counts if v not in special), key=lambda v: (-sum(counts[v].values()), v))
            return (changes + [v for v in special if v in counts]) or [NO_CHANGES]
        if f.kind == PRESENCE:
            return [PRESENT, NOT_PRESENT] + [v for v in (NO_ENTRIES,) if v in counts]
        unknown = sorted(v for v in counts if v not in f.known and v not in special)
        return list(f.known) + unknown + [v for v in special if v in counts]


def write_csv(path, les, labels, lines, delimiter):
    header = ["Bereich", "Zeile", "Inhalt", "Datenkranz-Feldname", "angezeigtes Ergebnis", "ausgewertetes Feld",
              "Einheit", "Wert", "Gesamt", *(labels[s] for s in les), "Bemerkung"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(header)
        writer.writerows(lines)


def run(export, start=None, end=None):
    evaluation = Evaluation(start, end)
    for row in export.rows():
        evaluation.add(row)
    les, lines = evaluation.lines(export.failures)
    return evaluation, les, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", help="result JSON of export-datenkranz")
    parser.add_argument("--from", dest="start", metavar="DATE", help="submissions with submission:date at or after DATE (ISO)")
    parser.add_argument("--until", dest="end", metavar="DATE", help="submissions with submission:date before DATE (exclusive)")
    parser.add_argument("--output", default="datenqualitaet.csv", help="CSV file to write (default: %(default)s)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s, for Excel with German locale)")
    args = parser.parse_args(argv)

    try:
        evaluation, les, lines = run(Export(args.export), args.start, args.end)
    except (OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    write_csv(args.output, les, evaluation.submitters, lines, args.delimiter)
    print(f"{sum(evaluation.submissions.values())} submissions evaluated "
          f"({sum(evaluation.tests.values())} tests left out), written {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
