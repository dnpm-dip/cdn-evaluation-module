#!/usr/bin/env python3
"""
Offline HTML dashboard from the JSON outputs of the evaluation scripts, or straight from Datenkranz submissions.
One self-contained HTML file (German UI) that opens by double click (file://), without network. Standard library
only, Python 3.9 or later. Details: README.md next to this script.

Modes
  rd          result JSON(s) of rd_evaluation_matrix.py, optionally --time-series (rd_time_series.py, JSON or CSV)
  onko        result JSON(s) of dnpm_evaluation_matrix.py (their time_series is used)
  datenkranz  the result JSON of export-datenkranz or a JSON list of {case, submittedAt, content}: per LE and use
              case submissions, cases, fill rate of every Datenkranz path, value distributions of enum-like fields,
              months of date fields; use case RD suppressed like the RD matrix, oncology (MTB) exact

Usage
  python3 build_dashboard.py [--mode rd|onko|datenkranz] SOURCE [SOURCE ...] [--time-series FILE ...] --output dashboard.html

SOURCE is  file.json,  name=file.json  or  name@YYYY-MM-DD=file.json. The name is the period ("Zeitraum") of the
switch; without it, it is derived from "period" of the file. The date is the data state ("Datenstand"); without
it: reference_date, cutoff, "Stichtag YYYY-MM-DD" in the notes, the end of a whole-export period, the file's mtime
(in mode datenkranz: the latest submission). Several sources of one period with different data states give the
history view ("Verlauf").

Suppression applies to rare diseases (SE/RD) only: by decision of the data holders (26.11.2026) oncology data
(mode onko, use case MTB in mode datenkranz) is not suppressed, every value is exact; an oncology matrix or time
series with suppressed cells ("<5") is refused. RD: "<5" means 1 to 4 cases or hidden to protect a neighbouring cell
(then also 5 or more). The page shows it as "unterdrückt" without bounds, never as 0, and never computes it back.
Bounds 1-4 are shown only for files that state they have no complementary suppression ("suppressed":
{"complementary": 0}). problems[] (case keys, TANs) is never read.

Third-party files (sql.js, Nunito, Asap) are not part of the repository: they are downloaded once from the pinned
URLs into --vendor (default: .vendor next to this script), checked against SHA-256 pins, and embedded.
"""
import argparse
import base64
import csv
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
TOTAL = "Gesamt"

# ===================================================================== third-party files (pinned)
VENDOR_PINS = {
    "sql-asm.js": ("https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/sql-asm.js",
                   "be591a3c6b7677b4a3db8ea1e0fe4629bc99ce80fa30ef5bdfbe9c6b707b7db8"),
    "asap-latin.woff2": ("https://fonts.gstatic.com/s/asap/v34/KFO9CniXp96a4Tc2DaTeuDAoKsE615hJW36eA1Ef.woff2",
                         "1ea4e924903959a46a4a625f0b26aef715b3bfc3e4b42da1fb3fa030a8cb5e2c"),
    "asap-latin-ext.woff2": ("https://fonts.gstatic.com/s/asap/v34/KFO9CniXp96a4Tc2DaTeuDAoKsE615hHW36eA1EfWzU.woff2",
                             "865f4df4228a6ed1f477bbee5faddb1c97bba8d80ab35e6e1f62602f4c59cd70"),
    "nunito-latin.woff2": ("https://fonts.gstatic.com/s/nunito/v32/XRXV3I6Li01BKofINeaBTMnFcQ.woff2",
                           "20fc9b6fc618e7c3c68d3ac750a2a5dfbceb8521675458d2cea580b5693e4798"),
    "nunito-latin-ext.woff2": ("https://fonts.gstatic.com/s/nunito/v32/XRXV3I6Li01BKofIO-aBTMnFcQIG.woff2",
                               "511ce967375fd2d924483d798f1086a533259fc675181f62944ec1176fe8786d"),
}
UNICODE = {
    "latin": "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, "
             "U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    "latin-ext": "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, "
                 "U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, U+2113, U+2C60-2C7F, U+A720-A7FF",
}


def vendor_file(directory, name, pins=VENDOR_PINS, download=True):
    """Bytes of a pinned file: from the directory (also the layout fonts/<family>-<google name>), else downloaded into it"""
    url, sha = pins[name]
    family = name.split("-")[0]
    for path in (directory / name, directory / "fonts" / f"{family}-{url.rsplit('/', 1)[-1]}"):
        if path.is_file():
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise SystemExit(f"{path}: SHA-256 does not match the pin for {url}")
            return data
    if not download:
        raise SystemExit(f"{name} missing in {directory} (and --offline given)")
    print(f"downloading {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "cdn-evaluation-module dashboard builder"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit(f"{url}: downloaded file does not match its SHA-256 pin")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return data


# ===================================================================== domain settings
# Only patterns on section and item; names always come from the data. kern: columns of the LE table and tiles of the
# overview (the first five), (key, section pattern, item pattern, title); "{bezug}" is replaced by the row's base.
# gfk: rows that together are exactly 0 mean "no GFK2 documented"; diagnostic rates over closed cases are then
# flagged "GFK1=GFK2" on the page. suppressed: small-cell suppression applies (None: per use case, see ONCOLOGY_USE_CASES).
DOMAINS = {
    "rd": {
        "title": "RD-Auswertematrix", "text": "Seltene Erkrankungen, Modellvorhaben Genomsequenzierung", "nav": "Kennzahlen",
        "script": "rd_evaluation_matrix.py", "detect": r"^Diagnosestatus|^Diagnoserate|^Orphacode|^Vorbefunde",
        "kern": [
            ("diagnose", r"^Diagnosestatus \(", r"^Diagnose$", "Diagnose ({bezug})"),
            ("diagnoserate_abgeschlossen", r"^Diagnoserate \(abgeschlossene", r"^Diagnose$", "Diagnoserate, abgeschl. Fälle", True),
            ("einwilligung", r"^Einwilligung$", r"^Broad Consent gültig zum Stichtag", "Broad Consent gültig"),
            ("mit_gfk2", r"^Bearbeitungsdauer medizinisch", r"^Dauer berechenbar", "GFK2 dokumentiert"),
            ("trio", r"^WGS-Seq\. Art", r"^Trio", "Trio (von WGS/lrWGS)"),
            ("ohne_orphacode", r"^Orphacode", r"ohne Orphacode \(", "ohne Orphacode"),
        ],
        "gfk": [(r"^Bearbeitungsdauer medizinisch", r"^Dauer berechenbar"), (r"^Bearbeitungsdauer medizinisch", r"unmögliches Datum")],
        "suppressed": True,
    },
    "onko": {
        "title": "Onko-Auswertematrix", "text": "Onkologie, Modellvorhaben Genomsequenzierung", "nav": "Kennzahlen",
        "script": "dnpm_evaluation_matrix.py", "detect": r"MTB|^Tumor|^ECOG|^Molekulare Befunde|^Systemische Therapieempfehlungen",
        "kern": [
            ("sequenziert", r"^Fälle$", r"^davon sequenziert", "sequenziert"),
            ("mtb", r"Ergebnis \(sequenzierte Fälle\)$", r"^GFK2 dokumentiert", "GFK2 (MTB) dokumentiert"),
            ("empfehlung", r"Ergebnis \(sequenzierte Fälle\)$", r"^mindestens eine Therapie- oder Studienempfehlung", "Therapie- oder Studienempfehlung"),
            ("umsetzung", r"^Umsetzung", r"^mindestens eine Empfehlung begonnen(?!,)", "Empfehlung begonnen"),
            ("einwilligung", r"^Einwilligung$", r"^Broad Consent gültig zum Stichtag", "Broad Consent gültig"),
            ("followup", r"^Fälle$", r"^Fälle mit mindestens einer Folgemeldung", "mit Folgemeldung"),
        ],
        "gfk": [], "suppressed": False,
    },
    "datenkranz": {
        "title": "Datenkranz", "text": "Einreichungen und Füllgrad je Leistungserbringer", "nav": "Felder",
        "script": "build_dashboard.py --mode datenkranz", "detect": None,
        "kern": [
            ("initial", r"^Einreichungen$", r"^Typ: initial$", "Erstmeldungen (von Einreichungen)"),
            ("followup", r"^Einreichungen$", r"^Typ: followup$", "Folgemeldungen"),
            ("correction", r"^Einreichungen$", r"^Typ: correction$", "Korrekturen"),
            ("addition", r"^Einreichungen$", r"^Typ: addition$", "Nachmeldungen"),
            ("consent", r"^metaData$", r"^metaData\.researchConsents\[\]\.[^:]*$", "Broad Consent übermittelt (Fälle)"),
            ("plan", r"^plan$", r"^plan\.[^:]*$", "Versorgungsplan (erstes Feld)"),
        ],
        "gfk": [], "suppressed": None,
    },
    "allgemein": {"title": "Auswertematrix", "text": "Aggregate je Leistungserbringer", "nav": "Kennzahlen",
                  "script": "evaluation script", "detect": None, "kern": [], "gfk": [], "suppressed": True},
}
CASES_ROW = (r"^Fälle$", r"^Fallzahl")                     # case count; else the first count without base
SYNTHETIC = re.compile(r"(^|[\s(])(LE-[A-Z]{1,2}\b|SYN-)")   # LE-A … (generators of the scripts' tests), SYN-… (make_synthetic.py)
DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


# ===================================================================== inputs
def number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_source(arg):
    m = re.match(r"^([^=/@]*)(?:@(\d{4}-\d{2}-\d{2}))?=(.+)$", arg)
    return (m.group(1) or None, m.group(2), Path(m.group(3))) if m else (None, None, Path(arg))


def iso(t):
    m = DATE.fullmatch(t.strip()) if t else None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def period_name(period):
    if re.search(r"gesamt", period, re.I):
        return "Gesamt"
    m = re.search(r"(\S+)\s+-\s+(\S+)\s*$", period)
    if not m:
        return period
    start, end = (None if x == "…" else x for x in m.groups())
    return f"bis {end}" if start is None and end else f"ab {start}" if end is None and start else f"{start} – {end}"


def data_state(d, path, given):
    if given:
        return given, "given"
    for k in ("reference_date", "cutoff"):
        if isinstance(d.get(k), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d[k]):
            return d[k], k
    for n in d.get("notes") or []:
        m = re.search(r"Stichtag (\d{4}-\d{2}-\d{2})", str(n))
        if m:
            return m.group(1), "Stichtag in notes"
    m = re.search(r"gesamt.*?-\s*(\d{2}\.\d{2}\.\d{4})\)?\s*$", d.get("period", ""))
    if m:
        return iso(m.group(1)), "end of the whole export in period"
    return datetime.date.fromtimestamp(path.stat().st_mtime).isoformat(), "file modification date"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"{path}: not readable ({e})")


def is_matrix(d):
    return isinstance(d, dict) and all(k in d for k in ("rows", "entities", "labels", "period"))


def no_complementary(d):
    """Only if the file says so itself: no complementary suppression (then "<5" is at most min_cell - 1)"""
    s = d.get("suppressed")
    return isinstance(s, dict) and s.get("complementary") == 0


def cell(v, unit, bounds_allowed, min_cell):
    """JSON cell -> (wert_text, wert, unterdrueckt, wert_min, wert_max, anteil, anteil_min, anteil_max, nenner)"""
    v = v or {}
    value, base, percent = v.get("value"), v.get("base"), v.get("percent")
    denom = number(base)
    if isinstance(value, str):        # "<5": no bounds, unless the file has no complementary suppression
        lo = hi = plo = phi = None
        if bounds_allowed and unit == "Anzahl" and min_cell > 1:
            lo = 0 if denom is not None and denom < min_cell else 1
            hi = min(min_cell - 1, int(denom)) if denom is not None else min_cell - 1
            if denom:
                plo, phi = round(100 * lo / denom, 1), round(100 * hi / denom, 1)
        return value, None, 1, lo, hi, None, plo, phi, denom
    w, p = number(value), number(percent)
    return ("" if w is None else str(w)), w, 0, w, w, p, p, p, denom


def n_suppressed(cells):
    """Cells {value, base, percent} with a suppressed value or base ("<5")"""
    return sum(1 for v in cells if isinstance(v, dict) and any(isinstance(v.get(k), str) and v[k].startswith("<") for k in ("value", "base")))


# ===================================================================== distributions (detected in the data)
def stem(item):
    return item.split(": ")[0] if ": " in item else item.split(" ")[0]


def facets(rows, nr):
    """Distributions per section, from the Gesamt column: consecutive count rows with the same base that split the base
    exactly form a partition (one stacked bar), zero rows that fit are added; every other count row with a base is
    binary (share and rest)."""
    out, per = [], {}
    for r in rows:
        per.setdefault(r["section"], []).append(r)
    for section, rs in per.items():
        if re.match(r"^Rückfragen", section):
            continue
        cand = [r for r in rs if r["unit"] == "Anzahl" and r["base_label"] and "Mehrfachnennung" not in r["item"] + section
                and not section.startswith("Füllgrad")]
        g = lambda r: r["values"].get(TOTAL) or {}
        parts, run = [], []
        for r in cand:
            if run and (r["base_label"] != run[0]["base_label"] or rs.index(r) != rs.index(run[-1]) + 1):
                run = []
            if parts and parts[-1]["done"] and not run:
                t = parts[-1]["rows"]
                stems = {stem(x["item"]) for x in t}
                if (g(r).get("value") == 0 and r["base_label"] == t[-1]["base_label"] and rs.index(r) == rs.index(t[-1]) + 1
                        and (len(stems) > 1 or stem(r["item"]) in stems)):
                    t.append(r)
                    continue
                parts[-1]["done"] = False
            run.append(r)
            base = g(r).get("base")
            if not isinstance(base, (int, float)) or base <= 0:
                run = []
                continue
            value = lambda x: g(x).get("value") if isinstance(g(x).get("value"), (int, float)) else None
            while run and None not in map(value, run) and sum(map(value, run)) > base:
                run.pop(0)
            if sum(1 for x in run if value(x)) >= 2 and None not in map(value, run) and sum(map(value, run)) == base:
                parts.append({"rows": run, "done": True})
                run = []
        in_part = {r["key"] for t in parts for r in t["rows"]}
        n = 0
        for t in parts:
            stems = {stem(x["item"]) for x in t["rows"]}
            common = stems.pop() if len(stems) == 1 and len(t["rows"]) > 1 else None
            kats = [{"zeile": nr[x["key"]], "label": x["item"][len(common):].lstrip(": ").strip() if common else x["item"]} for x in t["rows"]]
            n += 1
            out.append((section, n, common if common and (len(parts) > 1 or ":" in t["rows"][0]["item"]) else None, "teilung", kats))
        for r in cand + [r for r in rs if r["unit"] == "Anzahl" and r["base_label"] and r not in cand]:
            if r["key"] in in_part:
                continue
            n += 1
            yes, no = ("befüllt", "nicht befüllt") if section.startswith("Füllgrad") else ("ja", "nein")
            out.append((section, n, r["item"], "binaer", [{"zeile": nr[r["key"]], "label": yes}, {"zeile": nr[r["key"]], "label": no, "rest": True}]))
    return out


# ===================================================================== time series
def series_from_matrix(d, main_values):
    """time_series of a matrix JSON -> (resolution, periods, series); series = (block, item, unit, base, {(le, period): cell})"""
    ts = d.get("time_series") or {}
    series = []
    for r in ts.get("rows") or []:
        cells = {(le, per): v for le, pers in r["values"].items() for per, v in pers.items()}
        for le, v in (main_values.get((r["section"], r["item"])) or {}).items():   # whole run as period "gesamt" (protects the months)
            cells.setdefault((le, "gesamt"), v)
        series.append((r["section"], r["item"], r["unit"], r["base_label"], cells))
    return ts.get("resolution") or "", ts.get("periods") or [], series


CSV_COLUMNS = {"le": ("LE",), "period": ("Zeitraum", "Periode"), "block": ("Block", "Abschnitt"), "item": ("Kennzahl", "Kategorie"),
               "value": ("Wert",), "base_label": ("Bezug",), "base": ("Nenner", "Basis"), "percent": ("Anteil_%", "Anteil (%)"),
               "median": ("Median",), "unit": ("Einheit",)}


def series_from_lines(lines, labels_back, main_values):
    """Lines of rd_time_series.py (JSON rows or CSV) -> like series_from_matrix"""
    per, periods = {}, []
    for z in lines:
        le = labels_back.get(z["le"], re.sub(r"\s*\([^()]*\)$", "", z["le"]))
        if z["period"] not in periods:
            periods.append(z["period"])
        unit = z.get("unit") or ("Tage" if z.get("median") is not None else "Anzahl")
        s = per.setdefault((z["block"], z["item"]), [z["block"], z["item"], unit, z.get("base_label"), {}])
        s[4][(le, z["period"])] = {"value": z.get("value"), "base": z.get("base"), "percent": z.get("percent")}
    series = []
    for (block, item), s in per.items():
        for le, v in (main_values.get((block, item)) or {}).items():
            s[4].setdefault((le, "gesamt"), v)
        series.append(tuple(s))
    return [p for p in periods if p != "gesamt"], series


def csv_number(t):
    t = (t or "").strip()
    if t == "" or t.startswith("<"):
        return t or None
    try:
        f = float(t.replace(",", "."))
        return int(f) if f.is_integer() and "," not in t and "." not in t else f
    except ValueError:
        return t


def read_time_series(path, labels_back, main_values):
    """-> (run, resolution, date, periods, series)"""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            first = f.readline()
            f.seek(0)
            reader = csv.DictReader(f, delimiter=";" if first.count(";") >= first.count(",") else ",")
            col = {k: next((n for n in names if n in (reader.fieldnames or [])), None) for k, names in CSV_COLUMNS.items()}
            if not all(col[k] for k in ("le", "period", "block", "item", "value")):
                raise SystemExit(f"{path}: unknown CSV columns {reader.fieldnames}")
            get = lambda r, k, conv=lambda x: x: conv(r.get(col[k])) if col[k] else None
            lines = [{"le": r[col["le"]], "period": r[col["period"]], "block": r[col["block"]], "item": r[col["item"]], "unit": get(r, "unit"),
                      "base_label": get(r, "base_label") or None, "value": csv_number(r[col["value"]]), "base": get(r, "base", csv_number),
                      "percent": get(r, "percent", csv_number), "median": get(r, "median", csv_number)} for r in reader]
        periods, series = series_from_lines(lines, labels_back, main_values)
        res = "quarter" if any(re.fullmatch(r"\d{4}-Q\d", p, re.I) for p in periods) else "month"
        return path.stem, res, None, periods, series
    d = read_json(path)
    if isinstance(d, dict) and "time_series" in d:
        res, periods, series = series_from_matrix(d, main_values)
        return path.stem, res, d.get("reference_date"), periods, series
    if isinstance(d, dict) and d.get("rows") and "period" in d["rows"][0]:
        periods, series = series_from_lines(d["rows"], labels_back, main_values)
        return path.stem, d.get("resolution") or "month", d.get("reference_date"), periods, series
    raise SystemExit(f"{path}: no time series (neither time_series nor rows with period)")


def series_blocks(series, cases_item):
    """Stackable blocks: partitions (detected like the distributions) and the case count"""
    rows = [{"key": f"{b}|{i}", "section": b, "item": i, "unit": u, "base_label": bl, "values": {TOTAL: c.get((TOTAL, "gesamt")) or {}}}
            for b, i, u, bl, c in series]
    nr = {r["key"]: n for n, r in enumerate(rows)}
    per = {}
    for f in facets(rows, nr):
        if f[3] == "teilung":
            per.setdefault(f[0], []).append(f)
    blocks, names = [], set()
    for section, fs in per.items():
        for f in fs:
            name = section if len(fs) == 1 else f"{section} · {f[2] or f[4][0]['label']}"
            names.add(name)
            blocks.append((name, [series[k["zeile"]] for k in f[4]]))
    s = next((x for x in series if x[:2] == cases_item), None)
    if s and not any(s in b for _, b in blocks):
        blocks.insert(0, (s[0] if s[0] not in names else f"{s[0]} · {s[1]}", [s]))
    return blocks


# ===================================================================== mode datenkranz
ID_LIKE = re.compile(r"(^id|Id(?=[A-Z]|$)|ID|_id|^tan|Tan(?=[A-Z]|$)|tanC|^patient|identifier|[Aa]ddress|[Nn]ame$|[Cc]omment|[Nn]ote|[Tt]ext$|[Dd]escription)")
DATE_VALUE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?$")
MAX_DISTINCT = 30
USE_CASE = {"rare": "RD", "oncological": "MTB", "hereditary": "MTB"}
# Oncology is not suppressed (decision of the data holders, 26.11.2026); RD and unknown use cases are, like the RD matrix.
ONCOLOGY_USE_CASES = {"MTB"}


def load_submissions(path):
    """export-datenkranz result (table "datenkranz", failures) or a list of {case, submittedAt, content}
    -> (submissions, failures per (le, use case)); case keys stay internal"""
    d = read_json(path)
    subs, failed = [], defaultdict(int)
    if isinstance(d, dict) and "tables" in d:
        tables = d["tables"] if isinstance(d["tables"], list) else [dict(v, name=k) for k, v in d["tables"].items()]
        for t in tables:
            for r in t.get("rows") or []:
                if t.get("name") == "failures" or r.get("content") is None:
                    failed[(r.get("site") or "?", r.get("useCase") or "?")] += 1
                    continue
                subs.append((json.dumps([r.get("site"), r.get("patient"), r.get("episodeOfCare")]), r.get("site"), r.get("useCase"), r.get("submittedAt"), r["content"]))
    elif isinstance(d, list):
        for r in d:
            if r.get("content") is None:
                failed[(r.get("site") or "?", r.get("useCase") or "?")] += 1
                continue
            subs.append((json.dumps([r.get("site"), r.get("case")]), r.get("site"), r.get("useCase"), r.get("submittedAt"), r["content"]))
    else:
        raise SystemExit(f"{path}: neither an export-datenkranz result nor a list of submissions")
    out = []
    for key, site, use_case, at, content in subs:
        meta = (content.get("metaData") or {}).get("submission") or {}
        le = site or meta.get("submitterId") or "?"
        uc = use_case or USE_CASE.get(meta.get("diseaseType"), "unbekannt")
        out.append({"key": key, "le": le, "uc": uc, "at": str(at or meta.get("date") or ""), "type": meta.get("type") or "unbekannt", "content": content})
    return out, failed


def walk(x, path=""):
    """(path, value) of every scalar; arrays as [], objects descended"""
    if isinstance(x, dict):
        for k, v in x.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v, path + "[]")
    elif x is not None and x != "":
        yield path, x


def skipped_name(path):
    return any(ID_LIKE.search(seg.replace("[]", "")) for seg in path.split("."))


class Counter2:
    """Counts per row, entity and period ("gesamt" = whole run)"""

    def __init__(self):
        self.c = defaultdict(int)

    def add(self, row, le, month):
        for e in (le, TOTAL):
            for p in {"gesamt", month}:
                self.c[(row, e, p)] += 1


def suppress(cells, groups, min_cell, hidden=None):
    """Primary (1 to min_cell - 1) and complementary suppression: in every group (total first, then its parts) with
    exactly one suppressed cell the smallest other nonzero cell is suppressed too, until none is left.
    -> (suppressed cells, number of primary ones)"""
    hidden = set(hidden or ()) | {k for k, v in cells.items() if 0 < v < min_cell}
    primary = len(hidden)
    changed = True
    while changed:
        changed = False
        for g in groups:
            g = [k for k in g if k in cells]
            if len(g) < 2 or sum(1 for k in g if k in hidden) != 1:
                continue
            rest = [k for k in g[1:] if k not in hidden and cells[k] > 0] or [k for k in g if k not in hidden and cells[k] > 0]
            if rest:
                hidden.add(min(rest, key=lambda k: cells[k]))
                changed = True
    return hidden, primary


def datenkranz_results(subs, failed, min_cell, use_cases):
    """One matrix-like result (keys of the matrix JSON) per use case selection (name, use cases, suppressed)
    -> list of (name, result), skipped paths"""
    # value catalogue over all submissions: kind of each path
    values = defaultdict(set)
    skipped = set()
    for s in subs:
        for p, v in walk(s["content"]):
            if skipped_name(p):
                skipped.add(p)
            elif len(values[p]) <= MAX_DISTINCT + 1:
                values[p].add(v if isinstance(v, (bool, int, float)) else str(v))
    kind = {}
    for p, vs in values.items():
        if p in skipped:
            continue
        if all(isinstance(v, str) and DATE_VALUE.match(v) for v in vs):
            kind[p] = "date"
        elif len(vs) <= MAX_DISTINCT:
            kind[p] = "enum"
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vs):
            kind[p] = "number"
        else:
            skipped.add(p)                                  # free text or identifier-like (more than 30 distinct strings)
    results = []
    for name, ucs, suppressed in use_cases:
        rows, cnt, groups = [], Counter2(), []
        row_index = {}

        def row(section, item, base_key=None, base_label=None):
            key = f"{section} | {item}"
            if key not in row_index:
                row_index[key] = len(rows)
                rows.append({"key": key, "section": section, "item": item, "unit": "Anzahl", "base_key": base_key, "base_label": base_label})
            return key

        mine = [s for s in subs if s["uc"] in ucs]
        les = sorted({s["le"] for s in mine} | {le for (le, uc) in failed if uc in ucs})
        # submissions by type and month of the submission
        total_sub = row("Einreichungen", "Einreichungen gesamt")
        type_rows = [row("Einreichungen", f"Typ: {ty}", total_sub, "Einreichungen") for ty in sorted({s["type"] for s in mine})]
        for s in mine:
            month = s["at"][:7]
            cnt.add(total_sub, s["le"], month)
            cnt.add(row("Einreichungen", f"Typ: {s['type']}"), s["le"], month)
        groups.append((total_sub, type_rows))
        nc = row("Einreichungen", "Einreichungen nicht konvertierbar")
        for (le, uc), n in failed.items():
            if uc in ucs:
                for _ in range(n):
                    cnt.add(nc, le, "gesamt")
        # cases: episodes with an initial submission, state from the latest non-test submission
        per_case = defaultdict(list)
        for s in mine:
            if s["type"] != "test":
                per_case[s["key"]].append(s)
        cases_key = row("Fälle", "Fallzahl (Episoden mit Erstmeldung)")
        no_initial = row("Fälle", "Episoden ohne Erstmeldung (nicht in den Fällen)")
        cases = []        # (le, month of the initial submission, {path: values}) of the latest non-test submission
        for key, ss in per_case.items():
            ss.sort(key=lambda s: s["at"])
            initial = [s for s in ss if s["type"] == "initial"]
            if not initial:
                cnt.add(no_initial, ss[-1]["le"], ss[-1]["at"][:7])
                continue
            fields = defaultdict(set)
            for p, v in walk(ss[-1]["content"]):
                if p in kind:
                    fields[p].add(v if isinstance(v, (bool, int, float)) else str(v))
            cases.append((ss[-1]["le"], initial[0]["at"][:7], fields))
            cnt.add(cases_key, ss[-1]["le"], initial[0]["at"][:7])
        # fields: fill rate per path, values of enum-like fields, months of dates (cases by month of their initial submission)
        paths = sorted({p for _, _, f in cases for p in f}, key=lambda p: (p.split(".")[0], p))
        for p in paths:
            section = p.split(".")[0].split("[")[0]
            filled = row(section, p, cases_key, "Fälle")
            parts = []
            if kind[p] in ("enum", "date"):                  # value rows in sorted order (months ascending)
                seen = sorted({(v[:7] if kind[p] == "date" else v) for _, _, f in cases for v in f.get(p, ())}, key=lambda v: (str(type(v)), str(v)))
                for v in seen:
                    row(section, f"{p}: {json.dumps(v) if isinstance(v, bool) else v}", filled, f"Fälle mit {p}")
            for le, month, f in cases:
                vs = f.get(p)
                if not vs:
                    continue
                cnt.add(filled, le, month)
                if kind[p] == "enum":
                    for v in sorted(vs, key=str):
                        k = row(section, f"{p}: {json.dumps(v) if isinstance(v, bool) else v}", filled, f"Fälle mit {p}")
                        cnt.add(k, le, month)
                        parts.append(k)
                elif kind[p] == "date":
                    for m in sorted({v[:7] for v in vs}):
                        k = row(section, f"{p}: {m}", filled, f"Fälle mit {p}")
                        cnt.add(k, le, month)
                        parts.append(k)
            if "[]" not in p and parts:
                groups.append((filled, list(dict.fromkeys(parts))))
        months = sorted({k[2] for k in cnt.c if k[2] != "gesamt" and re.fullmatch(r"\d{4}-\d{2}", k[2])})
        ents = [TOTAL] + les
        # every cell (0 where nothing was counted)
        cells = {}
        for r in rows:
            for e in ents:
                for p in ["gesamt"] + months:
                    cells[(r["key"], e, p)] = cnt.c.get((r["key"], e, p), 0)
        hidden = set()
        if suppressed:          # RD: the suppression groups
            gl = []
            for r in rows:
                for p in ["gesamt"] + months:
                    gl.append([(r["key"], e, p) for e in ents])                    # Gesamt and the LEs
                for e in ents:
                    gl.append([(r["key"], e, p) for p in ["gesamt"] + months])     # whole run and the months
            for total, parts in groups:
                for e in ents:
                    for p in ["gesamt"] + months:
                        gl.append([(total, e, p)] + [(k, e, p) for k in parts])    # a partition of a count
            # values whose base is below min_cell are suppressed too (also 0), then the complementary suppression again
            small_base = {(r["key"], e, p) for r in rows if r["base_key"] for e in ents for p in ["gesamt"] + months
                          if 0 < cells[(r["base_key"], e, p)] < min_cell}
            hidden, primary = suppress(cells, gl, min_cell, small_base)

        def out(k):
            r = rows[row_index[k[0]]]
            if k in hidden:
                return {"value": f"<{min_cell}", "base": None, "percent": None}
            v = cells[k]
            if not r["base_key"]:
                return {"value": v, "base": None, "percent": None}
            bk = (r["base_key"], k[1], k[2])
            if bk in hidden:
                return {"value": v, "base": f"<{min_cell}", "percent": None}
            b = cells[bk]
            return {"value": v, "base": b, "percent": round(100 * v / b, 1) if b else None}

        res_rows = [{"key": r["key"], "section": r["section"], "item": r["item"], "unit": "Anzahl", "base_label": r["base_label"],
                     "values": {e: out((r["key"], e, "gesamt")) for e in ents}} for r in rows]
        ts_rows = [{"key": r["key"], "section": r["section"], "item": r["item"], "unit": "Anzahl", "base_label": r["base_label"],
                    "values": {e: {p: out((r["key"], e, p)) for p in months} for e in ents}} for r in rows]
        last = max((s["at"][:10] for s in mine), default="")
        first = min((m for m in months), default="")
        result = {
            "title": f"DATENKRANZ {name}", "period": f"gesamter Export ({name}, Einreichungen {first} - {months[-1] if months else ''})",
            "reference_date": last if re.fullmatch(r"\d{4}-\d{2}-\d{2}", last) else None, "entities": ents, "labels": {e: e for e in ents},
            "rows": res_rows, "time_series": {"resolution": "month", "periods": months, "rows": ts_rows},
            "notes": [f"{len(cases)} Fälle (Episoden mit Erstmeldung, Use Case {name}), Stand je Fall aus der letzten Nicht-Test-Einreichung; "
                      "Füllgrad und Werte je Fall (ein Fall zählt je Wert einmal), Monat = Monat der Erstmeldung, bei Einreichungen der Monat der Einreichung.",
                      (f"Zellen mit 1 bis {min_cell - 1} Fällen sind unterdrückt (<{min_cell}), ebenso Werte mit einer Basis unter {min_cell}; ergänzend "
                       f"unterdrückt, ebenfalls als <{min_cell}, damit sich keine unterdrückte Zelle aus Gesamt und den übrigen LE, aus dem Gesamtzeitraum und den "
                       "Monaten oder aus einer Aufteilung ergibt; eine Zelle <5 kann daher auch 5 oder mehr Fälle zählen." if suppressed else
                       "Keine Unterdrückung (Onkologie, Entscheidung der Datenhalter vom 26.11.2026): alle Werte exakt, auch unter 5."),
                      f"Werte nur bei Feldern mit höchstens {MAX_DISTINCT} verschiedenen Werten; Datumsfelder als Monate. Nicht ausgewertet (Kennungen, "
                      f"Freitext, mehr als {MAX_DISTINCT} verschiedene Texte): " + (", ".join(sorted(skipped)) or "keine") + "."],
        }
        if suppressed:
            result.update(min_cell=min_cell, suppressed={"primary": primary, "complementary": len(hidden) - primary})
        results.append((name, result))
    return results, sorted(skipped)


# ===================================================================== database (schema the page reads)
SCHEMA = """
CREATE TABLE meta (schluessel TEXT PRIMARY KEY, wert TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE zeitraum (zeitraum TEXT PRIMARY KEY COLLATE NOCASE, reihenfolge INTEGER NOT NULL, bezeichnung TEXT NOT NULL, kurz TEXT NOT NULL,
  erstmeldung_von TEXT, erstmeldung_bis TEXT, titel_matrix TEXT, datei TEXT NOT NULL, hinweis TEXT) WITHOUT ROWID;
CREATE TABLE le (kuerzel TEXT PRIMARY KEY COLLATE NOCASE, ik TEXT, name TEXT, name_kurz TEXT, ort TEXT, zulassung TEXT, grz TEXT, grz_kennung TEXT,
  netzwerk TEXT, kdk TEXT, bezeichnung_matrix TEXT) WITHOUT ROWID;
CREATE TABLE kennzahl (zeile INTEGER PRIMARY KEY, schluessel TEXT NOT NULL UNIQUE, block TEXT NOT NULL, abschnitt TEXT NOT NULL, kategorie TEXT NOT NULL,
  merkmal TEXT, untermerkmal TEXT, status TEXT, teilmenge TEXT, einheit TEXT NOT NULL, bezug TEXT, bezug_zeilen TEXT, teil_von INTEGER,
  mehrfachnennung INTEGER NOT NULL DEFAULT 0, zeitraum_unabhaengig INTEGER NOT NULL DEFAULT 0, bedeutung TEXT NOT NULL, bedeutung_quelle TEXT NOT NULL,
  datenkranz_feld TEXT);
CREATE TABLE datenstand (datenstand TEXT PRIMARY KEY, reihenfolge INTEGER NOT NULL, faelle INTEGER, ergaenzende_unterdrueckung INTEGER NOT NULL,
  skript TEXT, ist_neuester INTEGER NOT NULL, hinweis TEXT) WITHOUT ROWID;
CREATE TABLE datenstand_zeitraum (datenstand TEXT NOT NULL, zeitraum TEXT NOT NULL COLLATE NOCASE, erstmeldung_von TEXT, erstmeldung_bis TEXT,
  titel_matrix TEXT, datei TEXT NOT NULL, PRIMARY KEY (datenstand, zeitraum)) WITHOUT ROWID;
CREATE TABLE kennzahl_stand (datenstand TEXT NOT NULL, zeile INTEGER NOT NULL, matrixzeile INTEGER NOT NULL, bezug TEXT, bezug_zeilen TEXT,
  PRIMARY KEY (datenstand, zeile)) WITHOUT ROWID;
CREATE TABLE wert_stand (datenstand TEXT NOT NULL, zeitraum TEXT NOT NULL COLLATE NOCASE, le TEXT NOT NULL COLLATE NOCASE, zeile INTEGER NOT NULL,
  wert_text TEXT NOT NULL, wert NUMERIC, unterdrueckt INTEGER NOT NULL, wert_min NUMERIC, wert_max NUMERIC, anteil_prozent REAL,
  anteil_prozent_min REAL, anteil_prozent_max REAL, nenner NUMERIC, spalte TEXT, PRIMARY KEY (datenstand, zeitraum, le, zeile),
  CHECK (unterdrueckt = 0 OR wert IS NULL)) WITHOUT ROWID;
CREATE TABLE wert_stand_alle_le (datenstand TEXT NOT NULL, zeitraum TEXT NOT NULL COLLATE NOCASE, zeile INTEGER NOT NULL, wert_text TEXT NOT NULL,
  wert NUMERIC, unterdrueckt INTEGER NOT NULL, wert_min NUMERIC, wert_max NUMERIC, anteil_prozent REAL, anteil_prozent_min REAL,
  anteil_prozent_max REAL, nenner NUMERIC, spalte TEXT, PRIMARY KEY (datenstand, zeitraum, zeile), CHECK (unterdrueckt = 0 OR wert IS NULL)) WITHOUT ROWID;
CREATE TABLE zeitreihe (lauf TEXT NOT NULL, geladen_am TEXT NOT NULL, le TEXT NOT NULL COLLATE NOCASE, periode TEXT NOT NULL, aufloesung TEXT NOT NULL,
  block TEXT NOT NULL, kennzahl TEXT NOT NULL, wert_text TEXT NOT NULL, wert NUMERIC, unterdrueckt INTEGER NOT NULL, wert_min NUMERIC, wert_max NUMERIC,
  bezug TEXT, nenner_text TEXT, nenner NUMERIC, anteil_prozent REAL, median REAL, q1 REAL, q3 REAL, n_text TEXT, n NUMERIC,
  PRIMARY KEY (lauf, le, periode, block, kennzahl), CHECK (unterdrueckt = 0 OR wert IS NULL)) WITHOUT ROWID;
CREATE TABLE zeitreihe_alle_le (lauf TEXT NOT NULL, geladen_am TEXT NOT NULL, periode TEXT NOT NULL, aufloesung TEXT NOT NULL, block TEXT NOT NULL,
  kennzahl TEXT NOT NULL, wert_text TEXT NOT NULL, wert NUMERIC, unterdrueckt INTEGER NOT NULL, wert_min NUMERIC, wert_max NUMERIC, bezug TEXT,
  nenner_text TEXT, nenner NUMERIC, anteil_prozent REAL, median REAL, q1 REAL, q3 REAL, n_text TEXT, n NUMERIC,
  PRIMARY KEY (lauf, periode, block, kennzahl), CHECK (unterdrueckt = 0 OR wert IS NULL)) WITHOUT ROWID;
CREATE TABLE facette (abschnitt TEXT NOT NULL, nr INTEGER NOT NULL, name TEXT, art TEXT NOT NULL, kategorien TEXT NOT NULL, PRIMARY KEY (abschnitt, nr)) WITHOUT ROWID;
CREATE TABLE kennzahl_notiz (zeile INTEGER, notiz_id INTEGER, stelle TEXT);
CREATE TABLE notiz (id INTEGER PRIMARY KEY, titel TEXT, abschnitt TEXT, text TEXT);
CREATE TABLE rueckfrage (nr INTEGER PRIMARY KEY, thema TEXT, prioritaet TEXT);
CREATE TABLE rueckfrage_le (le TEXT, nr INTEGER, teil TEXT DEFAULT '', thema TEXT, prioritaet TEXT, quelle TEXT);
CREATE TABLE beschreibung (objekt TEXT NOT NULL, spalte TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, PRIMARY KEY (objekt, spalte)) WITHOUT ROWID;
CREATE VIEW zeitraum_aktuell AS SELECT zeitraum, MAX(datenstand) AS datenstand FROM datenstand_zeitraum GROUP BY zeitraum;
CREATE VIEW wert AS SELECT w.zeitraum, w.le, w.zeile, w.wert_text, w.wert, w.unterdrueckt, w.wert_min, w.wert_max, w.anteil_prozent, w.anteil_prozent_min,
  w.anteil_prozent_max, w.nenner, w.spalte, w.datenstand
FROM wert_stand w JOIN zeitraum_aktuell a ON a.zeitraum = w.zeitraum AND a.datenstand = w.datenstand;
CREATE VIEW wert_alle_le AS SELECT w.zeitraum, w.zeile, w.wert_text, w.wert, w.unterdrueckt, w.wert_min, w.wert_max, w.anteil_prozent, w.anteil_prozent_min,
  w.anteil_prozent_max, w.nenner, w.spalte, w.datenstand
FROM wert_stand_alle_le w JOIN zeitraum_aktuell a ON a.zeitraum = w.zeitraum AND a.datenstand = w.datenstand;
"""
DISPLAY = ("CASE WHEN {t}.unterdrueckt = 1 AND k.einheit <> 'Anzahl' THEN 'unterdrückt (unter 5 Werte)' "
           "WHEN {t}.unterdrueckt = 1 AND {t}.wert_max IS NOT NULL THEN {t}.wert_min || '–' || {t}.wert_max "
           "WHEN {t}.unterdrueckt = 1 THEN 'unterdrückt' WHEN {t}.wert IS NULL THEN NULL ELSE CAST({t}.wert AS TEXT) END")
COMPARISON = """CREATE VIEW {v} AS
SELECT n.zeitraum, {le}n.zeile, k.block, k.kategorie, k.einheit, v.vorher AS datenstand_vorher, n.datenstand, a.wert AS wert_vorher, n.wert,
  n.wert - a.wert AS differenz, ROUND(n.anteil_prozent - a.anteil_prozent, 1) AS differenz_prozentpunkte,
  CASE WHEN a.zeile IS NULL THEN 'neue Kennzahl' WHEN a.unterdrueckt = 0 AND n.unterdrueckt = 1 AND a.wert >= 5 THEN 'jetzt unterdrückt (vorher ' || CAST(a.wert AS TEXT) || ')'
       WHEN a.unterdrueckt = 1 AND n.unterdrueckt = 0 THEN 'vorher unterdrückt' ELSE '' END AS hinweis
FROM {new} n
JOIN (SELECT d.zeitraum, (SELECT MAX(d2.datenstand) FROM datenstand_zeitraum d2 WHERE d2.zeitraum = d.zeitraum AND d2.datenstand < d.datenstand) AS vorher
      FROM zeitraum_aktuell d) v ON v.zeitraum = n.zeitraum AND v.vorher IS NOT NULL
JOIN kennzahl k ON k.zeile = n.zeile
LEFT JOIN {old} a ON a.datenstand = v.vorher AND a.zeitraum = n.zeitraum AND a.zeile = n.zeile{le_join}"""


def find(rows, sa, sk):
    return next((r for r in rows if re.search(sa, r["section"]) and re.search(sk, r["item"])), None)


def period_dates(period):
    found = [f"{a}.{b}.{c}" for a, b, c in DATE.findall(period)]
    return (iso(found[0]), iso(found[-1])) if found else (None, None)


def build_database(runs, series_sources, domain, synthetic, extra_files=()):
    """runs: list of dict(zr, zr_name, ds, ds_source, path, d, bounds), main run first. -> (sqlite bytes, settings for the page)"""
    cfg = DOMAINS[domain]
    main = runs[0]
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db = sqlite3.connect(tmp)
    try:
        db.executescript(SCHEMA)
        # rows: union over the runs (main run first, then the newest), grouped by section in order of first appearance
        per_section, seen = {}, set()
        for run in [main] + sorted(runs[1:], key=lambda l: l["ds"], reverse=True):
            for r in run["d"]["rows"]:
                if r["key"] not in seen:
                    seen.add(r["key"])
                    per_section.setdefault(r["section"], []).append(r)
        if domain == "datenkranz":     # field sections of several sources: by path, the fill row before its values
            for s, rs in per_section.items():
                if s not in ("Einreichungen", "Fälle"):
                    rs.sort(key=lambda r: (r["item"].split(": ")[0], ": " in r["item"], r["item"]))
        rows = [r for rs in per_section.values() for r in rs]
        nr = {r["key"]: i for i, r in enumerate(rows, 1)}
        last = {}
        for i, r in enumerate(rows, 1):
            item, section, unit = r["item"], r["section"], r["unit"]
            part_of = last.get(section) if item.startswith("davon ") else None
            if not item.startswith("davon "):
                last[section] = i
            field = re.findall(r"\(([A-Za-z][\w\[\]\.]*(?:[\.\[][\w\[\]\.]*)+)\)", item)
            if domain == "datenkranz":
                field = [item.split(": ")[0]]
            meaning = f"{section}: {item}. " + (f"Anzahl; Anteil bezogen auf {r['base_label']}." if unit == "Anzahl" and r["base_label"]
                                                else "Anzahl, ohne Anteil." if unit == "Anzahl" else f"Statistik in {unit}.")
            db.execute("INSERT INTO kennzahl VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,?,?,NULL,?,?,0,?,?,?)", (
                i, r["key"], section, section, item, unit, r["base_label"], part_of,
                int("Mehrfachnennung" in item or "Mehrfachnennung" in section), meaning, f"JSON-Ausgabe von {cfg['script']}", field[0] if field else None))
        for section, n, name, art, kats in facets(main["d"]["rows"], nr):
            db.execute("INSERT INTO facette VALUES (?,?,?,?,?)", (section, n, name, art, json.dumps(kats, ensure_ascii=False)))

        order = list(dict.fromkeys(l["zr"] for l in runs))
        for i, zr in enumerate(order, 1):
            l = max((x for x in runs if x["zr"] == zr), key=lambda x: x["ds"])
            db.execute("INSERT INTO zeitraum VALUES (?,?,?,?,?,?,?,?,?)", (zr, i, l["d"]["period"], l["zr_name"], *period_dates(l["d"]["period"]),
                                                                            l["d"].get("title"), l["path"].name, "\n".join(map(str, l["d"].get("notes") or []))))
        states = sorted({l["ds"] for l in runs})
        cases_r = find(rows, *CASES_ROW) or next((r for r in rows if r["unit"] == "Anzahl" and not r["base_label"]), rows[0])
        for i, ds in enumerate(states, 1):
            ls = [l for l in runs if l["ds"] == ds]
            n_cases = next((number(r["values"].get(TOTAL, {}).get("value")) for l in ls if l["zr"] == "gesamt" for r in l["d"]["rows"] if r["key"] == cases_r["key"]), None)
            db.execute("INSERT INTO datenstand VALUES (?,?,?,?,?,?,?)", (ds, i, n_cases, int(any(l["suppressed"] and not l["bounds"] for l in ls)), cfg["script"], int(ds == states[-1]),
                                                                         "; ".join(f"{l['path'].name}: {l['d'].get('title') or l['d']['period']} (Datenstand: {l['ds_source']})" for l in ls)))
            for l in ls:
                for r in l["d"]["rows"]:      # matrixzeile: position in the union, so every data state lists the rows in one order
                    db.execute("INSERT OR IGNORE INTO kennzahl_stand VALUES (?,?,?,?,NULL)", (ds, nr[r["key"]], nr[r["key"]], r["base_label"]))
        labels = {}
        for l in runs:
            db.execute("INSERT INTO datenstand_zeitraum VALUES (?,?,?,?,?,?)", (l["ds"], l["zr"], *period_dates(l["d"]["period"]), l["d"].get("title"), l["path"].name))
            labels.update({e: l["d"]["labels"].get(e, e) for e in l["d"]["entities"] if e != TOTAL})
        for le, label in sorted(labels.items()):
            m = re.search(r"\(([^()]*)\)\s*$", label)
            db.execute("INSERT INTO le (kuerzel, ik, name, name_kurz, bezeichnung_matrix) VALUES (?,?,?,?,?)", (le, m.group(1) if m else None, label, label, label))

        for l in runs:
            min_cell = l["d"].get("min_cell") or 5
            for r in l["d"]["rows"]:
                z = nr[r["key"]]
                for ent, v in r["values"].items():
                    c = cell(v, r["unit"], l["bounds"], min_cell)
                    if ent == TOTAL:
                        db.execute("INSERT INTO wert_stand_alle_le VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)", (l["ds"], l["zr"], z) + c)
                    else:
                        db.execute("INSERT INTO wert_stand VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)", (l["ds"], l["zr"], ent, z) + c)

        for v, src, le in (("v_wert", "wert", "w.le, "), ("v_wert_alle_le", "wert_alle_le", "")):
            db.execute(f"""CREATE VIEW {v} AS SELECT w.zeitraum, {le}w.zeile, k.block, k.abschnitt, k.kategorie, k.merkmal, k.untermerkmal, k.status,
  k.teilmenge, k.einheit, w.wert, {DISPLAY.format(t='w')} AS wert_anzeige, w.unterdrueckt, w.wert_min, w.wert_max, w.anteil_prozent,
  w.anteil_prozent_min, w.anteil_prozent_max, k.bezug, w.nenner, k.mehrfachnennung, k.teil_von, k.bedeutung, w.datenstand
FROM {src} w JOIN kennzahl k ON k.zeile = w.zeile""")
        db.execute(COMPARISON.format(v="v_datenstandvergleich", le="n.le, ", new="wert", old="wert_stand", le_join=" AND a.le = n.le"))
        db.execute(COMPARISON.format(v="v_datenstandvergleich_alle_le", le="", new="wert_alle_le", old="wert_stand_alle_le", le_join=""))

        # profile: case count and key figures (domain patterns, else per section the first count with a share)
        kern = []
        for e in cfg["kern"]:
            r = find(rows, e[1], e[2])
            if r:
                kern.append({"k": e[0], "t": e[3].replace("{bezug}", r["base_label"] or "Anzahl"), "gfk": len(e) > 4 and e[4], "zeile": nr[r["key"]]})
        if not cfg["kern"]:
            seen = set()
            for r in rows:
                if (r["section"] not in seen and r is not cases_r and r["unit"] == "Anzahl" and r["base_label"] and not r["section"].startswith("Rückfragen")
                        and isinstance(r["values"].get(TOTAL, {}).get("percent"), (int, float))):
                    seen.add(r["section"])
                    kern.append({"k": f"k{len(kern) + 1}", "t": r["item"] if len(r["item"]) <= 40 else r["item"][:39] + "…", "gfk": False, "zeile": nr[r["key"]]})
                if len(kern) == 6:
                    break
        profile = {"faelle": nr[cases_r["key"]], **{x["k"]: x["zeile"] for x in kern}}
        cols = []
        for k, z in profile.items():
            cols += [f"MAX(CASE WHEN w.zeile = {z} THEN w.wert END) AS {k}", f"MAX(CASE WHEN w.zeile = {z} THEN w.wert_anzeige END) AS {k}_text"]
            cols += [f"MAX(CASE WHEN w.zeile = {z} THEN w.anteil_prozent{s} END) AS {k}_prozent{s}" for s in ("", "_min", "_max")]
        for v, src, le in (("v_le_profil", "v_wert", "w.le, "), ("v_le_profil_alle_le", "v_wert_alle_le", "")):
            db.execute(f"CREATE VIEW {v} AS SELECT w.zeitraum, {le}{', '.join(cols)} FROM {src} w GROUP BY w.zeitraum{', w.le' if le else ''}")
        db.executemany("INSERT INTO beschreibung VALUES ('v_le_profil', ?, ?)", [(k, f"Matrixzeile {z}") for k, z in profile.items()])
        gfk = [nr[r["key"]] for r in (find(rows, a, b) for a, b in cfg["gfk"]) if r]
        gfk = gfk if len(gfk) == len(cfg["gfk"]) else []

        # time series: time_series of the main run, then --time-series; per block the page shows the last one loaded
        n_series = 0
        for i, (run, res, date, periods, series) in enumerate(series_sources):
            at = f"{date or main['ds']}T00:00:{i:02d}"
            res_text = {"month": "monat", "quarter": "quartal"}.get(res, res or "periode")
            for block, ss in series_blocks(series, (cases_r["section"], cases_r["item"])):
                for b, item, unit, base_label, cells in ss:
                    for (le, per), v in cells.items():
                        if le != TOTAL and le not in labels:
                            continue
                        c = cell(v, unit, False, 5)
                        values = (run, at) + ((le,) if le != TOTAL else ()) + (per, "gesamt" if per == "gesamt" else res_text, block, item, c[0], c[1], c[2],
                                                                               None, None, base_label, None if v.get("base") is None else str(v.get("base")), c[8], c[5])
                        table = "zeitreihe_alle_le" if le == TOTAL else "zeitreihe"
                        db.execute(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * (len(values) + 5))})", values + (None,) * 5)
                        n_series += 1

        now = datetime.datetime.now().replace(microsecond=0)
        meta = {"erstellt_am": now.isoformat(), "erstellt_mit": f"build_dashboard.py, SQLite {sqlite3.sqlite_version}", "domaene": domain,
                "synthetisch": "1" if synthetic else "0"}
        for p in [l["path"] for l in runs] + list(extra_files):
            data = p.read_bytes()
            meta[f"quelle:{p.name}"] = f"bytes={len(data)}; sha256={hashlib.sha256(data).hexdigest()}"
        db.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", meta.items())
        db.commit()
        bad = [f"{t}.{c[1]}" for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") for c in db.execute(f"PRAGMA table_info('{t}')")
               if re.fullmatch(r"(?i)(tan|fall_?id|fall_?schluessel|fallnummer|case_?id|pseudonym|vorgangsnummer|patient)", c[1])]
        if bad:
            raise SystemExit(f"columns that look like case keys: {bad}")
        db.execute("VACUUM")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("integrity_check failed")
        db.close()
        data = Path(tmp).read_bytes()
    finally:
        db.close()
        os.unlink(tmp)
    return data, {"kern": [{"k": x["k"], "t": x["t"], "gfk": bool(x["gfk"])} for x in kern], "profilZeile": profile, "gfkZeilen": gfk,
                  "states": states, "series_values": n_series}


# ===================================================================== page
FRAME = ('<!doctype html><html lang="de"><head><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">'
         "<title>{title}</title>"
         "<style>:root{{color-scheme:light;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}}"
         "body{{margin:0;font:14px system-ui,-apple-system,'Segoe UI',sans-serif}}img{{max-width:100%}}[hidden]{{display:none!important}}</style></head><body>")
# stand-in for fetch (embedded database only) and the downloads capability, labelling; __VALUES__ is set by the build
PAGE_JS = r"""
(function () {
  "use strict";
  const W = __VALUES__;
  window.__jsonDash = { kern: W.kern, profilZeile: W.profilZeile, gfkZeilen: W.gfkZeilen, navA: W.navA, unterdrueckung: W.unterdrueckung };
  const b64 = (t) => { const bin = atob(t), out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; };
  let bytes = null;
  window.fetch = async function (input) {
    const url = String(typeof input === "string" ? input : input && input.url || "");
    if (url === "rd.sqlite") {
      if (!bytes) { const el = document.getElementById("json-db"); bytes = b64(el.textContent.trim()); el.remove(); }
      return new Response(bytes.slice(), { status: 200, headers: { "content-type": "application/vnd.sqlite3" } });
    }
    throw new TypeError("Datei ohne Netz: " + url);
  };
  const TYP = { csv: "text/csv;charset=utf-8", txt: "text/plain;charset=utf-8", json: "application/json" };
  const downloads = Object.freeze({ save: async (req) => {
    if (!req || typeof req.filename !== "string" || !req.filename || req.filename.length > 512) throw { code: "bad_request", message: "filename" };
    const blob = req.data instanceof Blob ? req.data : new Blob([req.data], { type: TYP[(req.filename.split(".").pop() || "").toLowerCase()] || "application/octet-stream" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = req.filename; a.hidden = true;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    return { status: "saved" };
  } });
  window.claude = Object.freeze({ use: (name) => Promise.resolve(name === "downloads" ? downloads : null) });
  const el = (tag, attr, ...kids) => { const e = document.createElement(tag); Object.assign(e, attr || {}); e.append(...kids.filter((k) => k != null)); return e; };
  document.querySelector(".marke-titel").append(el("span", { className: "lokal-marke", title: "Quellen: " + W.sources.join(", ") },
    "Aus JSON · Datenstand " + W.state + " · erstellt " + W.built + " · " + W.sourcesShort));
  if (W.synthetic) document.querySelector(".kontext").before(el("p", { className: "synth", role: "status" }, "Synthetische Daten – keine echten Fälle"));
  document.getElementById("fuss").after(el("div", { className: "fuss muted lokal-fuss" },
    el("p", null, el("strong", null, "Aggregate je LE, nur intern. "), "Diese Datei enthält Aggregate je Leistungserbringer (LE) und ist nur für den internen Gebrauch; nicht weitergeben oder veröffentlichen."),
    el("p", null, "Erzeugt mit build_dashboard.py am " + W.built + " aus: " + W.sources.join("; ") + "."),
    W.notes.length ? el("details", null, el("summary", null, "Hinweise der Auswertung (" + W.notes.length + ")"), el("ul", null, ...W.notes.map((t) => el("li", null, t)))) : null,
    el("p", null, "Schriften Nunito und Asap: SIL Open Font License 1.1. Bibliothek: sql.js (MIT, enthält SQLite, gemeinfrei).")));
})();
"""
PAGE_CSS = (".lokal-marke{font-size:12px;color:var(--leiste-muted);border:1px solid var(--leiste-line);padding:2px 8px;border-radius:10px;overflow-wrap:anywhere}"
            ".fuss.lokal-fuss{margin-top:6px;border-top:0;padding-top:0}.lokal-fuss p{margin:0 0 6px}.lokal-fuss ul{margin:4px 0 8px;padding-left:18px}"
            ".synth{margin:10px 0 0;padding:6px 12px;border:2px solid var(--coral-ink);background:var(--warn-soft);color:var(--coral-ink);font-weight:700;text-align:center}")


def fonts_css(vendor, pins, download):
    out = []
    for family, weights in (("Asap", (500, 600, 700)), ("Nunito", (400, 600, 700))):
        for subset in ("latin-ext", "latin"):
            data = base64.b64encode(vendor_file(vendor, f"{family.lower()}-{subset}.woff2", pins, download)).decode("ascii")
            for w in weights:
                out.append(f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{w};font-display:swap;"
                           f"src:url(data:font/woff2;base64,{data}) format('woff2');unicode-range:{UNICODE[subset]}}}")
    return "\n".join(out)


def build_page(template, vendor, db, values, title, text, nav, pins=VENDOR_PINS, download=True):
    page = template.read_text(encoding="utf-8").replace("{{TITEL}}", title).replace("{{TEXT}}", text).replace("{{NAV_A}}", nav)
    head = re.search(r'<link rel="preconnect"[^\n]*\n<link rel="preconnect"[^\n]*\n<link rel="stylesheet" href="https://fonts\.googleapis\.com[^"]*">\n', page)
    scripts = re.search(r'<script src="https://cdnjs[^"]*sql-asm\.js"></script>\n<script src="https://cdnjs[^"]*d3\.min\.js"></script>\n'
                        r'<script src="https://cdn\.jsdelivr\.net[^"]*plot\.umd\.min\.js"></script>\n', page)
    if not head or not scripts:
        raise SystemExit(f"{template}: font links or library scripts not found")
    sqljs = vendor_file(vendor, "sql-asm.js", pins, download).decode("utf-8")
    if "</script" in sqljs.lower():
        raise SystemExit("sql-asm.js contains </script")
    style = ("<style>/* Nunito (Copyright 2014 The Nunito Project Authors) and Asap (Copyright 2018 The Asap Project Authors), "
             "SIL Open Font License 1.1, https://openfontlicense.org */\n" + fonts_css(vendor, pins, download) + "\n" + PAGE_CSS + "</style>\n")
    js_values = json.dumps(values, ensure_ascii=False).replace("</", "<\\/")
    embedded = (f'<script type="application/octet-stream" id="json-db">{base64.b64encode(db).decode("ascii")}</script>\n'
                "<script>/* sql.js 1.10.3 (asm.js build), MIT licence, https://github.com/sql-js/sql.js; contains SQLite (public domain) */\n" + sqljs + "\n</script>\n"
                + "<script>" + PAGE_JS.replace("__VALUES__", js_values) + "</script>\n")
    page = page[:head.start()] + style + page[head.end():scripts.start()] + embedded + page[scripts.end():]
    left = re.findall(r"(?:src|href)=\"(https?://[^\"]+)\"", page)
    if left:
        raise SystemExit("external addresses left: " + ", ".join(left))
    return FRAME.format(title=title.replace("<", "&lt;")) + page + "</body></html>\n"


# ===================================================================== main
def footer(suppressed):
    """Footer text of the page when not every run is suppressed (None: the template's text on suppression)"""
    if all(suppressed):
        return None
    exact = "nach Entscheidung der Datenhalter (26.11.2026) nicht unterdrückt, alle Werte sind exakt, auch unter 5."
    if not any(suppressed):
        return "Nur Aggregate je LE. Keine Unterdrückung kleiner Zellen: Onkologische Daten werden " + exact
    return ("Nur Aggregate je LE. Unterdrückt wird nur bei Seltenen Erkrankungen (RD); onkologische Daten (MTB) werden " + exact
            + " RD: Unterdrückte Zellen („<5“, schraffiert) werden nie als 0 gezeigt und nie zurückgerechnet; eine unterdrückte Zelle kann auch"
            " 5 oder mehr Fälle zählen (ergänzende Unterdrückung). Eine Veränderung ist „n. b.“ (nicht berechenbar), sobald eine Seite unterdrückt ist.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", metavar="SOURCE", help="[name][@YYYY-MM-DD]=file.json or file.json")
    ap.add_argument("--mode", choices=("rd", "onko", "datenkranz"), help="input kind (default: detected)")
    ap.add_argument("--title", help="heading (default per mode)")
    ap.add_argument("--time-series", action="append", default=[], type=Path, metavar="FILE",
                    help="monthly/quarterly values: rd_time_series.py output (JSON or CSV), a matrix JSON with time_series, or *_zeitreihe.csv")
    ap.add_argument("--min-cell", type=int, default=5, help="mode datenkranz, use case RD: counts below are suppressed (default: %(default)s)")
    ap.add_argument("--output", required=True, type=Path, help="HTML file to write")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--synthetic", action="store_true", help="label as synthetic data")
    g.add_argument("--real", action="store_true", help="do not label as synthetic (default: detected from LE names LE-A…, SYN-…)")
    ap.add_argument("--vendor", type=Path, default=HERE / ".vendor", help="cache of the pinned third-party files (default: %(default)s)")
    ap.add_argument("--offline", action="store_true", help="never download; the files must be in --vendor")
    ap.add_argument("--template", type=Path, default=TEMPLATE, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    parsed = [(name, date, path, read_json(path)) for name, date, path in map(parse_source, args.sources)]
    mode = args.mode or ("datenkranz" if not any(is_matrix(d) for *_, d in parsed) else None)
    runs, extra = [], []
    if mode == "datenkranz":
        if args.time_series:
            raise SystemExit("--time-series is not used in mode datenkranz (the months come from the submissions)")
        for name, date, path, _ in parsed:
            subs, failed = load_submissions(path)
            ucs = sorted({s["uc"] for s in subs} | {uc for _, uc in failed})
            sup = {uc: uc not in ONCOLOGY_USE_CASES for uc in ucs}
            # "Alle" only over use cases suppressed alike: next to an exact oncology use case it would give the RD cells away
            alle = len(ucs) > 1 and len(set(sup.values())) == 1
            if len(ucs) > 1 and not alle:
                print(f"{path.name}: no use case 'Alle' (oncology is not suppressed, RD is)", file=sys.stderr)
            sel = ([("Alle", set(ucs), sup[ucs[0]])] if alle else []) + [(uc, {uc}, sup[uc]) for uc in ucs]
            results, skipped = datenkranz_results(subs, failed, args.min_cell, sel)
            for (uc_name, d), (_, _, suppressed) in zip(results, sel):
                ds = date or d.get("reference_date") or datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()
                runs.append({"zr_name": uc_name if not name else f"{name} {uc_name}", "ds": ds, "ds_source": "given" if date else "latest submission",
                             "path": path, "d": d, "bounds": False, "suppressed": suppressed})
            print(f"{path.name}: {len(subs)} submissions, use cases {', '.join(ucs)}; skipped paths (identifiers, free text): {len(skipped)}", file=sys.stderr)
    else:
        for name, date, path, d in parsed:
            if not is_matrix(d):
                raise SystemExit(f"{path}: keys rows/entities/labels/period missing (not an output of rd_evaluation_matrix.py or dnpm_evaluation_matrix.py)")
            for r in d["rows"]:
                r.setdefault("key", f"{r['section']} | {r['item']}")
            ds, src = data_state(d, path, date)
            runs.append({"zr_name": name or period_name(d["period"]), "ds": ds, "ds_source": src, "path": path, "d": d, "bounds": no_complementary(d)})
        sections = {r["section"] for l in runs for r in l["d"]["rows"]}
        hits = {k: sum(1 for s in sections if re.search(c["detect"], s)) for k, c in DOMAINS.items() if c["detect"]}
        mode = mode or (max(hits, key=hits.get) if max(hits.values()) > 0 else "allgemein")
        for l in runs:
            l["suppressed"] = DOMAINS[mode]["suppressed"]
            if not l["suppressed"] and n_suppressed(v for r in l["d"]["rows"] for v in r["values"].values()):
                raise SystemExit(f"{l['path']}: suppressed cells (\"<5\"), but oncology is not suppressed (decision of the data holders, 26.11.2026); "
                                 "rebuild the matrix with the current dnpm_evaluation_matrix.py")
    cfg = DOMAINS[mode]

    names = list(dict.fromkeys(l["zr_name"] for l in runs))
    names.sort(key=lambda n: 0 if n.lower() in ("gesamt", "alle") else 1)
    keys = {}
    for i, n in enumerate(names):
        k = "gesamt" if i == 0 else (re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-") or f"zr{i + 1}")
        while k in keys.values() or (i and k == "gesamt"):
            k += "-2"
        keys[n] = k
    for l in runs:
        l["zr"] = keys[l["zr_name"]]
    pairs = [(l["zr"], l["ds"]) for l in runs]
    dup = {p for p in pairs if pairs.count(p) > 1}
    if dup:
        raise SystemExit(f"period and data state twice: {sorted(dup)}. Give a name or date (name@YYYY-MM-DD=file); a second resolution of "
                         "the same matrix via --time-series")
    main_run = max((l for l in runs if l["zr"] == "gesamt"), key=lambda l: l["ds"])
    runs.sort(key=lambda l: (l is not main_run, names.index(l["zr_name"]), l["ds"]))

    main_values = {(r["section"], r["item"]): r["values"] for r in main_run["d"]["rows"]}
    labels_back = {lab: e for l in runs for e, lab in l["d"]["labels"].items()}
    series, suppressed_series = [], []      # suppressed_series: runs ("Lauf") of the time series that are suppressed
    if main_run["d"].get("time_series"):
        res, periods, ss = series_from_matrix(main_run["d"], main_values)
        series.append((main_run["path"].stem, res, main_run["ds"], periods, ss))
        if main_run["suppressed"]:
            suppressed_series.append(main_run["path"].stem)
    for p in args.time_series:
        series.append(read_time_series(p, labels_back, main_values))
        extra.append(p)
        if cfg["suppressed"]:
            suppressed_series.append(series[-1][0])
        elif n_suppressed(v for *_, cells in series[-1][4] for v in cells.values()):
            raise SystemExit(f"{p}: suppressed cells (\"<5\"), but oncology is not suppressed (decision of the data holders, 26.11.2026); "
                             "rebuild it with the current dnpm_evaluation_matrix.py")

    label_texts = [lab for l in runs for e, lab in l["d"]["labels"].items() if e != TOTAL] + [e for l in runs for e in l["d"]["entities"] if e != TOTAL]
    synthetic = args.synthetic or (not args.real and any(SYNTHETIC.search(x) for x in label_texts))
    db, info = build_database(runs, series, mode, synthetic, extra)
    built = datetime.datetime.now()
    files = list(dict.fromkeys([l["path"].name for l in runs] + [p.name for p in extra]))
    sources = [f"{l['path'].name} ({l['zr_name']}, Datenstand {l['ds']})" for l in runs] + [f"{p.name} (Zeitreihe)" for p in extra]
    values = {
        "kern": info["kern"], "profilZeile": info["profilZeile"], "gfkZeilen": info["gfkZeilen"], "navA": cfg["nav"], "synthetic": synthetic,
        "state": ", ".join(datetime.date.fromisoformat(s).strftime("%d.%m.%Y") for s in info["states"]),
        "built": built.strftime("%d.%m.%Y %H:%M"), "sources": sources,
        "sourcesShort": ", ".join(files) if len(", ".join(files)) <= 80 else f"{files[0]} + {len(files) - 1} weitere",
        "notes": [str(n) for n in main_run["d"].get("notes") or []],
        "unterdrueckung": {"aktiv": any(l["suppressed"] for l in runs), "laeufe": suppressed_series, "fuss": footer([l["suppressed"] for l in runs])},
    }
    title = args.title or cfg["title"]
    html = build_page(args.template, args.vendor, db, values, title, cfg["text"], cfg["nav"], download=not args.offline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"<!-- {title}: offline dashboard built {built.isoformat(timespec='seconds')} by build_dashboard.py from {', '.join(files)}. "
                           "Aggregates per LE, internal use only. -->\n" + html, encoding="utf-8")
    print(f"{args.output}  {args.output.stat().st_size / 1048576:.1f} MiB (database {len(db) / 1048576:.2f} MiB); mode {mode}, "
          f"{len(names)} period(s), {len(info['states'])} data state(s), {info['series_values']} time series values, synthetic={'yes' if synthetic else 'no'}")
    for l in runs:
        print(f"  {l['path'].name}: period '{l['zr_name']}', data state {l['ds']} ({l['ds_source']})"
              + (", not suppressed" if not l["suppressed"] else "" if l["bounds"] else ", complementary suppression: no bounds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
