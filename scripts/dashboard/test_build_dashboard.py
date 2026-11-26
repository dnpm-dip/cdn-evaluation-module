"""Tests of build_dashboard.py on synthetic data only (LE-A, LE-B, ...): python3 -m unittest discover -s scripts/dashboard"""
import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_dashboard as bd  # noqa: E402


def matrix(complementary=3, period="gesamter Export (Erstmeldungen 01.01.2026 - 31.03.2026)", reference="2026-04-01", extra_case=0):
    """A small RD-like matrix in the JSON structure of the evaluation scripts"""
    v = lambda value, base=None: {"value": value, "base": base, "percent": round(100 * value / base, 1) if isinstance(value, int) and base else None}
    a, b = 40 + extra_case, 7
    rows = [
        {"key": "Fälle | Fallzahl gesamt", "section": "Fälle", "item": "Fallzahl gesamt", "unit": "Anzahl", "base_label": None,
         "values": {"Gesamt": v(a + b), "LE-A": v(a), "LE-B": v(b)}},
        {"key": "Diagnosestatus (alle Fälle) | Diagnose", "section": "Diagnosestatus (alle Fälle)", "item": "Diagnose", "unit": "Anzahl",
         "base_label": "sequenzierte Fälle", "values": {"Gesamt": v(12, a + b), "LE-A": v(10, a), "LE-B": {"value": "<5", "base": b, "percent": None}}},
        {"key": "Diagnosestatus (alle Fälle) | Unklar", "section": "Diagnosestatus (alle Fälle)", "item": "Unklar", "unit": "Anzahl",
         "base_label": "sequenzierte Fälle", "values": {"Gesamt": v(a + b - 12), "LE-A": v(a - 10, a), "LE-B": {"value": "<5", "base": b, "percent": None}}},
        {"key": "Bearbeitungsdauer medizinisch (GFK1 bis GFK2) | Median (Tage)", "section": "Bearbeitungsdauer medizinisch (GFK1 bis GFK2)",
         "item": "Median (Tage)", "unit": "Tage", "base_label": None, "values": {"Gesamt": v(70.0), "LE-A": v(68.5), "LE-B": {"value": "<5", "base": None, "percent": None}}},
    ]
    rows[2]["values"]["Gesamt"] = v(a + b - 12, a + b)
    return {"title": f"AUSWERTUNG {period}", "period": period, "min_cell": 5, "suppressed": {"primary": 2, "complementary": complementary},
            "reference_date": reference, "entities": ["Gesamt", "LE-A", "LE-B"], "labels": {"Gesamt": "Gesamt", "LE-A": "LE-A (260000001)", "LE-B": "LE-B (260000002)"},
            "rows": rows, "problems": [{"le": "LE-A", "case": "SECRET-CASE-1", "tan": "SECRET-TAN-1", "problem": "x", "detail": ""}], "notes": ["synthetisch"]}


def onko_matrix():
    """A small oncology matrix (dnpm_evaluation_matrix.py: no suppression, every value exact) with a time series"""
    v = lambda value, base=None: {"value": value, "base": base, "percent": round(100 * value / base, 1) if base else None}
    rows = [
        {"key": "Fälle | Fallzahl gesamt", "section": "Fälle", "item": "Fallzahl gesamt", "unit": "Anzahl", "base_label": None,
         "values": {"Gesamt": v(33), "LE-A": v(30), "LE-B": v(3)}},
        {"key": "MTB-Ergebnis (sequenzierte Fälle) | GFK2 dokumentiert", "section": "MTB-Ergebnis (sequenzierte Fälle)", "item": "GFK2 dokumentiert",
         "unit": "Anzahl", "base_label": "sequenzierte Fälle", "values": {"Gesamt": v(21, 33), "LE-A": v(19, 30), "LE-B": v(2, 3)}},
    ]
    ts = {"resolution": "month", "periods": ["2026-01", "2026-02"], "rows": [
        {"key": "Fälle | Fallzahl gesamt", "section": "Fälle", "item": "Fallzahl gesamt", "unit": "Anzahl", "base_label": None,
         "values": {"Gesamt": {"2026-01": v(20), "2026-02": v(13)}, "LE-A": {"2026-01": v(19), "2026-02": v(11)}, "LE-B": {"2026-01": v(1), "2026-02": v(2)}}}]}
    return {"title": "AUSWERTUNG gesamter Export", "period": "gesamter Export (Erstmeldungen 01.01.2026 - 28.02.2026)", "reference_date": "2026-03-01",
            "entities": ["Gesamt", "LE-A", "LE-B"], "labels": {"Gesamt": "Gesamt", "LE-A": "LE-A", "LE-B": "LE-B"}, "rows": rows, "time_series": ts,
            "notes": ["synthetisch"]}


def page_values(html):
    """The values the builder hands the page (window.__jsonDash)"""
    return json.loads(re.search(r"const W = (\{.*?\});\n", html).group(1))


def submissions(n_a=40, n_b=3, disease="rare"):
    """Datenkranz submissions as a JSON list {case, submittedAt, content, site}"""
    out = []
    for le, n in (("LE-A", n_a), ("LE-B", n_b)):
        for i in range(n):
            month = 1 + i % 3
            content = {"metaData": {"submission": {"date": f"2026-0{month}-10", "type": "initial", "submitterId": f"SUBMITTER-{le}", "diseaseType": disease},
                                    "tanC": f"TAN-{le}-{i:04d}", "localCaseId": f"LOCAL-{le}-{i}", "gender": "female" if i % 2 else "male",
                                    "birthDate": f"20{10 + i % 5}-0{1 + i % 9}"},
                       "case": {"diagnosisRd": {"libraryType": "wgs" if i % 4 else "panel", "freeTextRemark": f"Bemerkung {le} {i}",
                                                "diagnoses": [{"code": "ORPHA:558"}, {"code": f"ORPHA:{900 + i % 3}"}]}}}
            out.append({"case": f"CASE-{disease}-{le}-{i}", "site": le, "submittedAt": f"2026-0{month}-10T10:00:00", "content": content})
    out.append({"case": f"CASE-{disease}-LE-A-0", "site": "LE-A", "submittedAt": "2026-03-20T10:00:00",
                "content": {"metaData": {"submission": {"date": "2026-03-20", "type": "test", "diseaseType": disease}, "tanC": "TAN-TEST"}}})
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.vendor = self.dir / "vendor"
        self.vendor.mkdir()
        self.pins = dict(bd.VENDOR_PINS)
        for name in bd.VENDOR_PINS:      # fake third-party files with matching pins, no network
            data = b"/* fake sql.js */ var initSqlJs;" if name.endswith(".js") else os.urandom(64)
            (self.vendor / name).write_bytes(data)
            bd.VENDOR_PINS[name] = ("https://invalid.example/" + name, hashlib.sha256(data).hexdigest())

    def tearDown(self):
        bd.VENDOR_PINS.update(self.pins)
        self.tmp.cleanup()

    def write(self, name, obj):
        p = self.dir / name
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return p

    def build(self, *args):
        out = self.dir / "dashboard.html"
        bd.main([*map(str, args), "--vendor", str(self.vendor), "--offline", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        db_path = self.dir / "db.sqlite"
        db_path.write_bytes(base64.b64decode(re.search(r'id="json-db">([^<]+)<', html).group(1)))
        db = sqlite3.connect(db_path)
        self.addCleanup(db.close)
        return html, db


class MatrixModeTests(Base):
    def test_offline_page_and_suppression(self):
        p = self.write("rd.json", matrix())
        html, db = self.build("--mode", "rd", p)
        self.assertEqual(re.findall(r'(?:src|href)="(https?://[^"]+)"', html), [])
        self.assertNotIn("SECRET", html)                                          # problems[] is never read
        self.assertIn("Aggregate je LE, nur intern", html)
        self.assertIn('"synthetic": true', html)
        r = db.execute("SELECT wert, wert_anzeige, wert_min, wert_max, unterdrueckt FROM v_wert WHERE le = 'LE-B' AND kategorie = 'Diagnose'").fetchone()
        self.assertEqual(r, (None, "unterdrückt", None, None, 1))                 # complementary suppression: no bounds
        self.assertEqual(db.execute("SELECT faelle FROM v_le_profil WHERE le = 'LE-A'").fetchone()[0], 40)
        self.assertEqual(db.execute("SELECT art FROM facette WHERE abschnitt = 'Diagnosestatus (alle Fälle)'").fetchone()[0], "teilung")

    def test_bounds_only_without_complementary_suppression(self):
        p = self.write("rd.json", matrix(complementary=0))
        _, db = self.build(p)
        r = db.execute("SELECT wert_anzeige, wert_min, wert_max FROM v_wert WHERE le = 'LE-B' AND kategorie = 'Diagnose'").fetchone()
        self.assertEqual(r, ("1–4", 1, 4))

    def test_periods_and_data_states(self):
        old = self.write("old.json", matrix(reference="2026-03-01"))
        new = self.write("new.json", matrix(reference="2026-04-01", extra_case=5))
        part = self.write("part.json", matrix(period="… - 31.01.2026"))
        _, db = self.build(old, new, part)
        self.assertEqual(db.execute("SELECT zeitraum, kurz FROM zeitraum ORDER BY reihenfolge").fetchall(), [("gesamt", "Gesamt"), ("bis-31-01-2026", "bis 31.01.2026")])
        self.assertEqual([r[0] for r in db.execute("SELECT datenstand FROM datenstand ORDER BY reihenfolge")], ["2026-03-01", "2026-04-01"])
        self.assertEqual(db.execute("SELECT differenz FROM v_datenstandvergleich WHERE le = 'LE-A' AND zeile = 1").fetchone()[0], 5)

    def test_same_period_and_state_twice_is_an_error(self):
        p = self.write("rd.json", matrix())
        with self.assertRaises(SystemExit):
            self.build(p, p)

    def test_vendor_pin_mismatch(self):
        (self.vendor / "sql-asm.js").write_bytes(b"tampered")
        with self.assertRaises(SystemExit):
            self.build(self.write("rd.json", matrix()))


class OncologyNotSuppressedTests(Base):
    """Oncology is not suppressed (decision of the data holders, 26.11.2026): every value exact, also below 5"""

    def test_onko_matrix_exact(self):
        html, db = self.build("--mode", "onko", self.write("onko.json", onko_matrix()))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM wert_stand WHERE unterdrueckt = 1").fetchone()[0], 0)
        r = db.execute("SELECT wert, wert_anzeige, anteil_prozent FROM v_wert WHERE le = 'LE-B' AND kategorie = 'GFK2 dokumentiert'").fetchone()
        self.assertEqual(r, (2, "2", 66.7))
        self.assertEqual(db.execute("SELECT wert, unterdrueckt FROM zeitreihe WHERE le = 'LE-B' AND periode = '2026-01'").fetchone(), (1, 0))
        self.assertEqual(db.execute("SELECT ergaenzende_unterdrueckung FROM datenstand").fetchone()[0], 0)
        u = page_values(html)["unterdrueckung"]
        self.assertEqual((u["aktiv"], u["laeufe"]), (False, []))
        self.assertIn("Keine Unterdrückung", u["fuss"])

    def test_onko_matrix_with_suppressed_cells_refused(self):
        d = onko_matrix()
        d["rows"][1]["values"]["LE-B"] = {"value": "<5", "base": 3, "percent": None}
        with self.assertRaises(SystemExit):
            self.build("--mode", "onko", self.write("onko.json", d))

    def test_rd_page_unchanged(self):
        html, _ = self.build("--mode", "rd", self.write("rd.json", matrix(complementary=0) | {"time_series": onko_matrix()["time_series"]}))
        u = page_values(html)["unterdrueckung"]
        self.assertEqual((u["aktiv"], u["laeufe"], u["fuss"]), (True, ["rd"], None))

    def test_datenkranz_mtb_exact_rd_suppressed(self):
        p = self.write("export.json", submissions() + submissions(disease="oncological"))
        html, db = self.build("--mode", "datenkranz", p)
        self.assertEqual([r[0] for r in db.execute("SELECT kurz FROM zeitraum ORDER BY reihenfolge")], ["MTB", "RD"])     # no "Alle"
        cases = lambda zr: db.execute("SELECT le, wert_text FROM wert_stand w JOIN kennzahl k USING (zeile) JOIN zeitraum z USING (zeitraum) "
                                      "WHERE z.kurz = ? AND k.kategorie LIKE 'Fallzahl%' ORDER BY le", [zr]).fetchall()
        self.assertEqual(cases("MTB"), [("LE-A", "40"), ("LE-B", "3")])
        self.assertEqual(cases("RD"), [("LE-A", "<5"), ("LE-B", "<5")])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM wert_stand w JOIN zeitraum z USING (zeitraum) WHERE z.kurz = 'MTB' AND w.unterdrueckt = 1").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM zeitreihe WHERE unterdrueckt = 1").fetchone()[0], 0)        # time series of the main run: MTB
        u = page_values(html)["unterdrueckung"]
        self.assertEqual((u["aktiv"], u["laeufe"]), (True, []))
        self.assertIn("nur bei Seltenen Erkrankungen", u["fuss"])

    def test_datenkranz_mtb_only(self):
        html, db = self.build("--mode", "datenkranz", self.write("export.json", submissions(disease="oncological")))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM wert_stand WHERE unterdrueckt = 1").fetchone()[0], 0)
        self.assertIn("Keine Unterdrückung", db.execute("SELECT hinweis FROM zeitraum").fetchone()[0])
        self.assertFalse(page_values(html)["unterdrueckung"]["aktiv"])


class DatenkranzModeTests(Base):
    def test_no_identifiers_and_suppression(self):
        p = self.write("export.json", submissions())
        html, db = self.build("--mode", "datenkranz", p)
        text = " ".join(str(x) for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") for row in db.execute(f"SELECT * FROM {t}") for x in row)
        for secret in ("TAN-LE", "LOCAL-LE", "CASE-LE", "Bemerkung", "SUBMITTER-"):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, html)
        notes = db.execute("SELECT hinweis FROM zeitraum").fetchone()[0]
        for path in ("metaData.tanC", "metaData.localCaseId", "metaData.submission.submitterId", "case.diagnosisRd.freeTextRemark"):
            self.assertIn(path, notes)
        kats = {r[0] for r in db.execute("SELECT kategorie FROM kennzahl")}
        self.assertIn("metaData.gender: female", kats)
        self.assertIn("case.diagnosisRd.diagnoses[].code: ORPHA:558", kats)
        self.assertIn("metaData.birthDate: 2010-01", kats)                       # dates as months
        cases = db.execute("SELECT le, wert_text FROM wert_stand w JOIN kennzahl k USING (zeile) WHERE k.kategorie LIKE 'Fallzahl%' ORDER BY le").fetchall()
        # LE-B has 3 cases; with two LEs, Gesamt minus LE-A would give them away: LE-A is suppressed too (complementary)
        self.assertEqual(cases, [("LE-A", "<5"), ("LE-B", "<5")])
        self.assertEqual(db.execute("SELECT wert_text FROM wert_stand_alle_le w JOIN kennzahl k USING (zeile) WHERE k.kategorie LIKE 'Fallzahl%'").fetchone()[0], "43")
        self.assertIn('"navA": "Felder"', html)

    def test_no_group_with_one_suppressed_cell(self):
        p = self.write("export.json", submissions(n_a=23, n_b=9))
        _, db = self.build("--mode", "datenkranz", p)
        per = {}
        for zeile, le, unt in db.execute("SELECT zeile, le, unterdrueckt FROM wert_stand WHERE zeitraum = 'gesamt'"):
            per.setdefault(zeile, []).append(unt)
        for zeile, unt in db.execute("SELECT zeile, unterdrueckt FROM wert_stand_alle_le WHERE zeitraum = 'gesamt'"):
            per.setdefault(zeile, []).append(unt)
        for zeile, flags in per.items():
            self.assertNotEqual(sum(flags), 1, f"row {zeile}: exactly one suppressed cell over Gesamt and the LEs")

    def test_walk_and_names(self):
        self.assertEqual(list(bd.walk({"a": [{"b": 1}, {"b": None}], "c": ""})), [("a[].b", 1)])
        for name in ("metaData.tanC", "x.localCaseId", "plan.recommendedStudies[].id", "patient", "y.identifier", "metaData.addressAGS"):
            self.assertTrue(bd.skipped_name(name), name)
        for name in ("plan.evidenceLevel", "metaData.decisionToInclude", "case.diagnosisRd.libraryType"):
            self.assertFalse(bd.skipped_name(name), name)


class Review20261126Tests(Base):
    """Fixes after the cross-model review of 26.11.2026 (synthetic data)"""

    def test_the_note_shows_the_case_count_as_the_matrix_does(self):
        _, db = self.build("--mode", "datenkranz", self.write("export.json", submissions(n_a=3, n_b=0)))
        note = db.execute("SELECT hinweis FROM zeitraum").fetchone()[0]
        self.assertNotIn("3 Fälle", note)
        self.assertIn("<5 Fälle", note)

    def test_no_alle_next_to_a_suppressed_use_case(self):
        unknown = submissions(n_a=8, n_b=0, disease="unknownDisease")
        p = self.write("export.json", submissions(n_a=1, n_b=6) + unknown)
        _, db = self.build("--mode", "datenkranz", p)
        self.assertNotIn("Alle", [r[0] for r in db.execute("SELECT kurz FROM zeitraum")])

    def test_free_text_is_counted_as_filled_only(self):
        subs = submissions(n_a=1, n_b=0)
        subs[0]["content"]["case"]["diagnosisRd"]["extraInfo"] = "fictitious patient detail"
        subs[0]["content"]["case"]["diagnosisRd"]["freeTextRemark"] = "fictitious patient remark"
        html, db = self.build("--mode", "datenkranz", self.write("export.json", subs))
        text = " ".join(str(x) for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") for row in db.execute(f"SELECT * FROM {t}") for x in row)
        self.assertNotIn("fictitious", text)
        self.assertNotIn("fictitious", html)
        self.assertIn("case.diagnosisRd.extraInfo", {r[0] for r in db.execute("SELECT kategorie FROM kennzahl")})    # its fill rate
        self.assertTrue(bd.skipped_name("case.diagnosisRd.freeTextRemark"))

    def test_onko_refuses_a_suppressed_embedded_time_series(self):
        d = onko_matrix()
        d["time_series"]["rows"][0]["values"]["LE-B"]["2026-01"] = {"value": "<5", "base": None, "percent": None}
        with self.assertRaises(SystemExit):
            self.build("--mode", "onko", self.write("onko.json", d))
        second = onko_matrix() | {"period": "… - 31.01.2026"}
        second["time_series"] = d["time_series"]
        with self.assertRaises(SystemExit):
            self.build("--mode", "onko", self.write("a.json", onko_matrix()), self.write("b.json", second))

    def test_several_use_cases_have_no_time_series(self):
        _, db = self.build("--mode", "datenkranz", self.write("export.json", submissions() + submissions(disease="oncological")))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM zeitreihe").fetchone()[0], 0)
        _, db = self.build("--mode", "datenkranz", self.write("export1.json", submissions(disease="oncological")))
        self.assertGreater(db.execute("SELECT COUNT(*) FROM zeitreihe").fetchone()[0], 0)


class HelperTests(unittest.TestCase):
    def test_period_name(self):
        self.assertEqual(bd.period_name("gesamter Export (Erstmeldungen 01.01.2026 - 31.03.2026)"), "Gesamt")
        self.assertEqual(bd.period_name("… - 31.12.2025"), "bis 31.12.2025")
        self.assertEqual(bd.period_name("01.01.2026 - …"), "ab 01.01.2026")

    def test_suppress(self):
        cells = {("r", "Gesamt"): 10, ("r", "A"): 7, ("r", "B"): 3}
        hidden, primary = bd.suppress(cells, [[("r", "Gesamt"), ("r", "A"), ("r", "B")]], 5)
        self.assertEqual((primary, hidden), (1, {("r", "B"), ("r", "A")}))


if __name__ == "__main__":
    unittest.main()
