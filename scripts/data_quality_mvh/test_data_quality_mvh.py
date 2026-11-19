import csv
import json
import os
import tempfile
import unittest

import data_quality_mvh as dq


def submission(tan, submission_type="initial", disease="rare", submitter="260840108", date="2026-03-01", **parts):
    content = {
        "metaData": {
            "tanC": tan,
            "submission": {"date": date, "type": submission_type, "submitterId": submitter, "diseaseType": disease},
        }
    }
    content.update(parts)
    return content


def row(content, site="UKT", patient="p1", episode="e1"):
    use_case = "RD" if content["metaData"]["submission"]["diseaseType"] == "rare" else "MTB"
    return {"site": site, "useCase": use_case, "patient": patient, "episodeOfCare": episode,
            "tan": content["metaData"]["tanC"], "submittedAt": "2026-03-01T00:00:00", "content": content}


def rd_case(extent="trioGenome", **diagnosis):
    return {"diagnosisRd": {"diagnosticExtent": extent, **diagnosis}}


class ExportTests(unittest.TestCase):

    def write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def export(self, rows, failures=(), pretty=True):
        data = {
            "statistic": "export-datenkranz",
            "tables": [
                {"name": "datenkranz", "columns": ["site", "content"], "rows": rows},
                {"name": "failures", "columns": ["tan", "error"], "rows": list(failures)},
            ],
            "notes": ["2 submissions converted"],
            "collection": {"cases": 2},
        }
        return self.write(json.dumps(data, indent=2 if pretty else None))

    def test_streams_the_rows_with_small_chunks(self):
        rows = [row(submission(f"t{i}", case=rd_case(), note="x" * 300)) for i in range(5)]
        for pretty in (True, False):
            export = dq.Export(self.export(rows, failures=[{"tan": "t9", "error": "e"}], pretty=pretty))
            export.CHUNK = 7
            self.assertEqual([r["tan"] for r in export.rows()], ["t0", "t1", "t2", "t3", "t4"])
            self.assertEqual(export.failures, 1)
            self.assertEqual(export.notes, ["2 submissions converted"])

    def test_reads_the_format_of_the_evaluation_module(self):
        # as written by ResultWriter: pretty-printed values, notes and collection after the tables
        text = (
            '{\n  "statistic" : "export-datenkranz",\n  "tables" : [\n    {\n      "name" : "datenkranz",\n'
            '      "columns" : [ "site" ],\n      "rows" : [\n        ' + json.dumps(row(submission("t1")), indent=2) +
            ',\n        ' + json.dumps(row(submission("t2"))) + '\n      ]\n    },\n    {\n      "name" : "failures",\n'
            '      "columns" : [ "tan" ],\n      "rows" : [ ]\n    }\n  ],\n  "notes" : [ "n" ],\n  "collection" : { }\n}'
        )
        export = dq.Export(self.write(text))
        self.assertEqual([r["tan"] for r in export.rows()], ["t1", "t2"])
        self.assertEqual((export.failures, export.notes), (0, ["n"]))

    def test_empty_export(self):
        export = dq.Export(self.export([]))
        self.assertEqual(list(export.rows()), [])
        self.assertEqual(export.failures, 0)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            list(dq.Export(self.write('{"tables": [{"name": "documents", "rows": []}]}')).rows())
        with self.assertRaises(ValueError):
            list(dq.Export(self.write('[1, 2]')).rows())


class EvaluationTests(unittest.TestCase):

    def evaluate(self, rows, start=None, end=None):
        evaluation = dq.Evaluation(start, end)
        for r in rows:
            evaluation.add(r)
        les, lines = evaluation.lines(0)
        result = {}
        for line in lines:
            result[(line[1], line[7])] = (line[8], dict(zip(les, line[9:-1])))
        return evaluation, result

    def total(self, result, row, value):
        return result[(row, value)][0]

    def test_counts_all_known_values_unknown_and_missing(self):
        rows = [
            row(submission("t1", case=rd_case("trioGenome")), patient="p1"),
            row(submission("t2", case=rd_case("trioGenome")), patient="p2"),
            row(submission("t3", case=rd_case("quadGenome")), patient="p3"),
            row(submission("t4", case={"diagnosisRd": {}}), patient="p4"),
            # not counted for an RD field
            row(submission("t5", disease="oncological"), patient="p5"),
        ]
        _, result = self.evaluate(rows)
        self.assertEqual(self.total(result, 19, "trioGenome"), 2)
        self.assertEqual(self.total(result, 19, "duoGenome"), 0)
        self.assertEqual(self.total(result, 19, "quadGenome"), 1)
        self.assertEqual(self.total(result, 19, dq.MISSING), 1)
        self.assertEqual(self.total(result, 10, "oncological"), 1)

    def test_counts_list_elements_and_submissions_without(self):
        variants = {
            "smallVariants": [{"zygosity": "homozygous"}, {}],
            "copyNumberVariants": [{"zygosity": "homozygous"}],
        }
        rows = [
            row(submission("t1", molecular=variants), patient="p1"),
            row(submission("t2", molecular={"smallVariants": []}), patient="p2"),
            row(submission("t3"), patient="p3"),
        ]
        _, result = self.evaluate(rows)
        self.assertEqual(self.total(result, 31, "homozygous"), 2)
        self.assertEqual(self.total(result, 31, dq.MISSING), 1)
        self.assertEqual(self.total(result, 31, dq.NO_ENTRIES), 2)

    def test_counts_values_of_lists_and_presence(self):
        studies = {"recommendedStudies": [{"evidenceLevelDetails": ["is", "Z"]}, {"evidenceLevelDetails": []}, {}]}
        rows = [
            row(submission("t1", disease="oncological", plan=studies), patient="p1"),
            row(submission("t2", case=rd_case(noMatchingCodeExists=False)), patient="p2"),
            row(submission("t3", case=rd_case()), patient="p3"),
        ]
        _, result = self.evaluate(rows)
        self.assertEqual([self.total(result, 84, v) for v in ("is", "Z", "iv", dq.EMPTY_LIST, dq.MISSING)], [1, 1, 0, 1, 1])
        self.assertEqual((self.total(result, 23, dq.PRESENT), self.total(result, 23, dq.NOT_PRESENT)), (1, 1))

    def test_period_tests_and_submitters(self):
        rows = [
            row(submission("t1", date="2025-12-31"), patient="p1"),
            row(submission("t2", date="2026-01-01", submitter="A"), site="UKT", patient="p2"),
            row(submission("t3", date="2026-06-30", submitter="B"), site="UKFR", patient="p3"),
            row(submission("t4", "test", date="2026-02-01", submitter="B", case=rd_case("singleGenome")), site="UKFR", patient="p4"),
            row(submission("t5", date="2027-01-01"), patient="p5"),
        ]
        evaluation, result = self.evaluate(rows, "2026-01-01", "2027-01-01")
        self.assertEqual(result[(6, "initial")], (2, {"A": 1, "B": 1, "260840108": 0}))
        self.assertEqual(self.total(result, 6, "test"), 1)
        # tests left out of the field evaluations
        self.assertEqual(self.total(result, 19, "singleGenome"), 0)
        self.assertEqual(sum(evaluation.tests.values()), 1)
        self.assertEqual(evaluation.submitters["B"], "UKFR (B)")

    def test_changes_of_corrections_and_additions(self):
        rows = [
            row(submission("t1", case=rd_case("trioGenome", libraryType="wgs"), date="2025-01-01")),
            # the test is no baseline
            row(submission("t2", "test", case=rd_case("duoGenome"))),
            row(submission("t3", "correction", case=rd_case("singleGenome", libraryType="wgs"))),
            row(submission("t4", "addition", case=rd_case("singleGenome", libraryType="wgs"))),
            row(submission("t5", "correction", case=rd_case()), patient="p2"),
        ]
        _, result = self.evaluate(rows, "2026-01-01")
        self.assertEqual(self.total(result, 7, "case.diagnosisRd.diagnosticExtent"), 1)
        self.assertEqual(self.total(result, 7, dq.NO_CHANGE), 1)
        self.assertEqual(self.total(result, 7, dq.NO_PREVIOUS), 1)
        self.assertNotIn((7, "metaData.tanC"), result)

    def test_changes_within_lists(self):
        before = {"smallVariants": [{"zygosity": "hemi", "gene": "A"}, {"zygosity": "homozygous", "gene": "B"}]}
        reordered = {"smallVariants": [{"zygosity": "homozygous", "gene": "B"}, {"zygosity": "hemi", "gene": "A"}]}
        changed = {"smallVariants": [{"zygosity": "homozygous", "gene": "B"}, {"zygosity": "homozygous", "gene": "A"}]}
        rows = [row(submission("t1", molecular=before)), row(submission("t2", "correction", molecular=reordered)),
                row(submission("t3", "correction", molecular=changed))]
        _, result = self.evaluate(rows)
        self.assertEqual(self.total(result, 7, dq.NO_CHANGE), 1)
        self.assertEqual(self.total(result, 7, "molecular.smallVariants[].zygosity"), 1)
        self.assertNotIn((7, "molecular.smallVariants[].gene"), result)

    def test_no_corrections(self):
        _, result = self.evaluate([row(submission("t1"))])
        self.assertEqual(self.total(result, 7, dq.NO_CHANGES), 0)

    def test_notes_cases_out_of_order(self):
        rows = [row(submission("t1"), patient="p1"), row(submission("t2"), patient="p2"),
                row(submission("t3", "correction"), patient="p1")]
        evaluation, result = self.evaluate(rows)
        self.assertEqual(evaluation.out_of_order, 1)
        self.assertEqual(self.total(result, 7, dq.NO_PREVIOUS), 1)

    def test_fields_not_in_the_datenkranz_are_listed_without_counts(self):
        _, result = self.evaluate([row(submission("t1", disease="oncological"))])
        self.assertEqual(result[(57, "")][0], "")

    def test_main_writes_csv_for_excel(self):
        with tempfile.TemporaryDirectory() as d:
            export = os.path.join(d, "export.json")
            with open(export, "w", encoding="utf-8") as f:
                json.dump({"tables": [{"name": "datenkranz", "rows": [row(submission("t1", case=rd_case()))]}]}, f)
            out = os.path.join(d, "out.csv")
            self.assertEqual(dq.main([export, "--output", out]), 0)
            with open(out, encoding="utf-8-sig", newline="") as f:
                lines = list(csv.reader(f, delimiter=";"))
            self.assertEqual(lines[0][:9], ["Bereich", "Zeile", "Inhalt", "Datenkranz-Feldname", "angezeigtes Ergebnis",
                                            "ausgewertetes Feld", "Einheit", "Wert", "Gesamt"])
            self.assertEqual(lines[0][9:], ["UKT (260840108)", "Bemerkung"])
            self.assertIn(["RD Case", "19", "diagnosisRd", "Diagnostic Extent", "Auswertung (Enum)",
                           "case.diagnosisRd.diagnosticExtent", "Submissions", "trioGenome", "1", "1", ""], lines)
            # only rows of the sheet with an "angezeigtes Ergebnis"
            self.assertEqual({l[1] for l in lines[1:] if l[1]},
                             {str(r) for r in (6, 7, 10, 19, 20, 21, 23, 24, 25, 26, 31, 32, 33, 36, 38, 39, 42, 43, 51, 52,
                                               56, 57, 58, 59, 60, 63, 64, 66, 79, 81, 82, 83, 84, 90, 95, 97, 98)})


if __name__ == "__main__":
    unittest.main()
