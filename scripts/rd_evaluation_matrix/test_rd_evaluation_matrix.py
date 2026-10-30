"""Tests of rd_evaluation_matrix.py: python3 -m unittest discover scripts/rd_evaluation_matrix"""

import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import date

import rd_evaluation_matrix as rdm

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "..", "docs", "Update_Auswertematrix_GZ1.xlsx")

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
):
    """A RD Datenkranz submission with the fields the matrix reads"""
    diagnoses = [{"code": icd10, "system": rdm.ICD10GM}]
    if orpha:
        diagnoses.append({"code": orpha, "system": rdm.ORPHANET})
    content = {
        "metaData": {
            "submission": {"date": submitted, "type": type_, "diseaseType": "rare"},
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


def row(case, content, submitted_at="2025-03-01T10:00:00"):
    return rdm.SubmissionRow(json.dumps(case), submitted_at, content)


# Orpha code -> head, as from the linearisation
HEADS = {"558": "93419", "98006": "98006", "777": "98053", "999": "123456"}

REFERENCE = date(2026, 10, 30)


def run(rows, heads=HEADS, start=None, end=None):
    return rdm.run(rows, 0, heads, start, end, REFERENCE)


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
        cells = result["cells"]
        self.assertEqual(cells["C3"], 1)
        self.assertEqual((cells["C31"], cells["E31"]), (1, 100.0))      # Diagnose, from t2
        self.assertIn("2 test submissions left out, 1 episodes without initial submission not counted.", result["notes"][1])

    def test_period_applies_to_the_initial_submission(self):
        rows = [
            row("a", datenkranz("t1", submitted="2024-06-30")),
            row("b", datenkranz("t2", submitted="2024-07-01")),
            row("b", datenkranz("t3", type_="followup", submitted="2026-02-01"), "2026-02-01T00:00:00"),
            row("c", datenkranz("t4", submitted="2026-01-01")),
        ]
        cells = run(rows, start=date(2024, 7, 1), end=date(2026, 1, 1))["cells"]
        self.assertEqual(cells["C3"], 1)
        self.assertEqual(cells["G5"], "Auswertungszeitraum: 01.07.2024 - 31.12.2025")

    def test_other_disease_types_are_ignored(self):
        mtb = datenkranz("t1")
        mtb["metaData"]["submission"]["diseaseType"] = "oncological"
        self.assertEqual(run([row("a", mtb)])["cells"]["C3"], 0)


class RowTests(unittest.TestCase):

    def cells(self, *contents):
        return run([row(str(i), c) for i, c in enumerate(contents)])["cells"]

    def test_research_consent(self):
        cells = self.cells(
            datenkranz("t1", consents=[broad_consent()]),
            datenkranz("t2", consents=[broad_consent(start="2020-01-01")]),         # older than 5 years
            datenkranz("t3", consents=[broad_consent(kind="deny")]),
            datenkranz("t4", consents=[broad_consent(code="2.16.840.1.113883.3.1937.777.24.5.3.99")]),
            datenkranz("t5"),
        )
        self.assertEqual((cells["C5"], cells["E5"]), (1, 20.0))

    def test_a_denial_wins_over_later_consents(self):
        self.assertFalse(rdm.permits_research_use(
            [{"scope": broad_consent(kind="deny")}, {"scope": broad_consent()}], REFERENCE))

    def test_sex_age_insurance(self):
        cells = self.cells(
            datenkranz("t1", gender="male", birth="2024-12", gfk1="2025-01-20", coverage="PKV"),  # 50 days: no neonate
            datenkranz("t2", birth="2025-01", gfk1="2025-02-07"),                                 # 37 days
            datenkranz("t3", gender="other", birth="2007-01", gfk1="2025-01-01", coverage="BG"),  # 18
            datenkranz("t4", gender="unknown", birth="2007-02", gfk1="2025-01-31"),               # 17
            datenkranz("t5", birth="1960-01", gfk1="2025-01-10"),                                 # 65
        )
        self.assertEqual([cells[f"C{r}"] for r in (8, 9, 10)], [1, 2, 1])
        self.assertEqual([cells[f"C{r}"] for r in range(12, 17)], [1, 1, 1, 1, 1])
        self.assertEqual([cells["C18"], cells["C19"]], [3, 1])

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
        self.assertEqual(result["cells"]["C21"], 35.0)   # (60 + 10) / 2
        self.assertEqual(result["cells"]["C22"], 15.5)   # (30 + 1) / 2
        extras = {(e["section"], e["item"]): e["value"] for e in result["extras"]}
        self.assertEqual(extras[("Bearbeitungsdauer (medizinisch, GFK1 bis GFK2) in Tagen", "Fälle")], 2)
        self.assertEqual(extras[("Bearbeitungsdauer (medizinisch, GFK1 bis GFK2) in Tagen", "Maximum")], 60)

    def test_diagnosis_rates(self):
        cells = self.cells(
            datenkranz("t1", extent="trioGenome", library="wgs", birth="1990-01"),
            datenkranz("t2", extent="trioGenome", library="wgs_lr", assessment="partialGeneticDiagnosis", birth="1990-01"),
            datenkranz("t3", extent="singleGenome", library="none", assessment="noGeneticDiagnosis", include=False),
            datenkranz("t4", extent="singleGenome", assessment="suspectedGeneticDiagnosis"),
        )
        self.assertEqual([cells[f"C{r}"] for r in (24, 25, 26)], [2, 0, 2])
        self.assertEqual([cells["C28"], cells["C29"]], [2, 1])
        self.assertEqual(cells["C20"], 1)
        self.assertEqual([cells[f"C{r}"] for r in (31, 32, 33)], [1, 2, 1])
        self.assertEqual((cells["C36"], cells["E36"]), (1, 50.0))
        self.assertEqual((cells["C35"], cells["E35"]), (0, None))
        # by age group: adults row 34, SUMME row 36
        self.assertEqual([cells["I34"], cells["J34"], cells["K34"], cells["L34"]], [1, 50.0, 1, 50.0])
        self.assertEqual([cells["I36"], cells["K36"], cells["M36"]], [1, 2, 1])

    def test_prior_findings(self):
        cells = self.cells(
            datenkranz("t1", prior={"genomicTestType": "exome", "diagnosticDate": "2021-05-01"}),
            datenkranz("t2", prior={"genomicTestType": "karyotyping"}),
            datenkranz("t3"),
            datenkranz("t4", assessment="noGeneticDiagnosis", prior={"genomicTestType": "genomeLongRead", "diagnosticDate": "2016-01-01"}),
        )
        # Diagnose: left Exom row 41, other tests row 43, keine row 44
        self.assertEqual([cells["C41"], cells["C43"], cells["C44"]], [1, 1, 1])
        self.assertEqual(cells["E41"], 33.3)
        # right: Exomanalyse row 41, 2021/2020 column J; other tests row 39, unbekannt column M
        self.assertEqual([cells["J41"], cells["N41"], cells["M39"], cells["M44"]], [1, 1, 1, 1])
        # Undiagnostiziert: Genom (LR) row 56 left, genome-long-read row 59 right, 2017 und älter column L
        self.assertEqual([cells["C56"], cells["L59"]], [1, 1])

    def test_recommendations_over_all_submissions(self):
        rows = [
            row("a", datenkranz("t1", plan={"carePlanRd": {"studyRecommended": True, "clinicalManagementRecommended": True,
                                                           "clinicalManagementDescriptions": ["gp"]}}), "2025-03-01T00:00:00"),
            row("a", datenkranz("t2", type_="followup", plan={
                "carePlanRd": {"therapyRecommended": True},
                "recommendedTherapies": [
                    {"type": "symptomatic", "strategy": "systemicMedication"},
                    {"type": "symptomatic", "strategy": "targetedMedication"},
                    {"type": "causal", "strategy": "genetic"},
                    {"type": "causal", "strategy": "combination"},
                ]}), "2025-04-01T00:00:00"),
            row("b", datenkranz("t3")),
        ]
        result = run(rows)
        cells = result["cells"]
        self.assertEqual([cells[f"C{r}"] for r in range(64, 68)], [1, 0, 0, 1])
        self.assertEqual([cells[f"C{r}"] for r in range(68, 73)], [0, 1, 0, 0, 1])
        self.assertEqual([cells["C80"], cells["C82"]], [1, 1])
        extras = {(e["section"], e["item"]): e["value"] for e in result["extras"]}
        self.assertEqual(extras[("Therapieempfehlung", "Kausal nicht zuordenbar")], 1)

    def test_without_orphacode(self):
        cells = self.cells(datenkranz("t1", orpha=None), datenkranz("t2"))
        self.assertEqual((cells["C84"], cells["E84"]), (1, 50.0))


class DiseaseGroupTests(unittest.TestCase):

    def test_groups_per_block_add_up_to_the_case_count(self):
        contents = [
            datenkranz("t1", birth="1980-01"),                                      # bone disease, adult
            datenkranz("t2", orpha="ORPHA:98006", assessment="noGeneticDiagnosis"),  # neurologic, pediatric
            datenkranz("t3", icd10="G71.0", orpha="ORPHA:98006"),                   # neuromuscular by ICD-10-GM
            datenkranz("t4", orpha="ORPHA:777"),                                    # genetic disease head: unbekannt
            datenkranz("t5", orpha="ORPHA:999"),                                    # unmapped head
            datenkranz("t6", orpha=None),
        ]
        result = run([row(str(i), c) for i, c in enumerate(contents)])
        cells = result["cells"]
        groups = rdm.DISEASE_GROUPS
        total = {g: cells[f"C{88 + i}"] for i, g in enumerate(groups)}
        adult = {g: cells[f"C{110 + i}"] for i, g in enumerate(groups)}
        pediatric = {g: cells[f"C{131 + i}"] for i, g in enumerate(groups)}
        self.assertEqual(sum(total.values()), 6)
        self.assertEqual({g: adult[g] + pediatric[g] for g in groups}, total)
        self.assertEqual(total[rdm.NEUROMUSCULAR], 1)
        self.assertEqual(total["Neurologische Erkrankungen, Entwicklungsstörungen"], 1)
        self.assertEqual(adult["Knochenkrankheiten, Bindegewebe- und muskuloskelettale Krankheiten"], 1)
        self.assertEqual(total[rdm.UNKNOWN_GROUP], 3)
        neurologic = 88 + groups.index("Neurologische Erkrankungen, Entwicklungsstörungen")
        self.assertEqual([cells[f"I{neurologic}"], cells[f"K{neurologic}"]], [1, 100.0])
        self.assertTrue(any("123456" in note for note in result["notes"]))

    def test_no_groups_without_linearisation(self):
        result = run([row("a", datenkranz("t1"))], heads=None)
        self.assertNotIn("C88", result["cells"])

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


class InputTests(unittest.TestCase):

    def test_reads_the_export_and_plain_lists(self):
        export = {"tables": [
            {"name": "datenkranz", "rows": [
                {"site": "UKT", "useCase": "RD", "patient": "p1", "episodeOfCare": "e1", "tan": "t1",
                 "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1")},
                {"site": "UKT", "useCase": "MTB", "patient": "p2", "episodeOfCare": "e1", "tan": "t2",
                 "submittedAt": "2025-03-01T00:00:00", "content": {}},
            ]},
            {"name": "failures", "rows": [{"tan": "t3"}]},
        ]}
        plain = [{"case": {"id": 1}, "submittedAt": "2025-03-01T00:00:00", "content": datenkranz("t1")}]
        with tempfile.TemporaryDirectory() as tmp:
            for name, data, expected in (("export.json", export, (1, 1)), ("plain.json", plain, (1, 0))):
                path = os.path.join(tmp, name)
                with open(path, "w") as f:
                    json.dump(data, f)
                rows, failures = rdm.load_submissions(path)
                self.assertEqual((len(rows), failures), expected)


@unittest.skipUnless(os.path.exists(TEMPLATE), "template not found")
class TemplateTests(unittest.TestCase):

    def test_fills_the_cells_keeping_the_rest(self):
        cells = run([row("a", datenkranz("t1"))])["cells"]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "matrix.xlsx")
            rdm.write_template(TEMPLATE, out, cells)
            with zipfile.ZipFile(out) as filled, zipfile.ZipFile(TEMPLATE) as template:
                self.assertEqual(filled.namelist(), template.namelist())
                texts = rdm.cell_texts(filled)
                sheet = filled.read(rdm.SHEET).decode("utf-8")
        self.assertEqual(texts["B3"], "Fallzahl gesamt")
        self.assertEqual(texts["G5"], "Auswertungszeitraum: gesamter Export")
        self.assertIn('<c r="C3" s="7"><v>1</v></c>', sheet)
        self.assertIn('<c r="E31" s="17"><v>100.0</v></c>', sheet)
        # K101 isn't in the template: inserted in column order
        self.assertRegex(sheet, r'<c r="I101"[^>]*>.*?</c><c r="K101"><v>')

    def test_refuses_another_layout(self):
        with zipfile.ZipFile(TEMPLATE) as template:
            sheet = template.read(rdm.SHEET).decode("utf-8")
        buffer = io.BytesIO()
        with zipfile.ZipFile(TEMPLATE) as src, zipfile.ZipFile(buffer, "w") as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == rdm.SHEET:
                    # move row 3's label away
                    data = sheet.replace('<c r="B3"', '<c r="A3"').encode("utf-8")
                dst.writestr(item, data)
        with zipfile.ZipFile(buffer) as broken:
            with self.assertRaisesRegex(ValueError, "B3"):
                rdm.check_template(broken)


if __name__ == "__main__":
    unittest.main()
