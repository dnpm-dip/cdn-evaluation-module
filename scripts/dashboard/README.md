# Offline dashboard

`build_dashboard.py` turns the JSON outputs of the evaluation scripts, or Datenkranz submissions directly, into **one
self-contained HTML file**: it opens by double click (`file://`), needs no server and no network, and contains only
aggregates per Leistungserbringer (LE). The user interface is German, like the matrices. Standard library only,
Python 3.9 or later.

```sh
# RD: one or more matrices of rd_evaluation_matrix.py, optionally its time series
python3 build_dashboard.py --mode rd matrix.json --time-series time-series.json --output RD-Dashboard.html

# Oncology: matrices of dnpm_evaluation_matrix.py (their time_series is used)
python3 build_dashboard.py --mode onko onko-matrix.json --output Onko-Dashboard.html

# Datenkranz: the export-datenkranz result or a JSON list of {case, submittedAt, content}
python3 build_dashboard.py --mode datenkranz export-datenkranz_2026-11-01.json --output Datenkranz-Dashboard.html
```

Without `--mode` the kind is detected (matrix or submissions; RD or oncology from the section names).

## Sources, periods and data states

Each `SOURCE` is `file.json`, `name=file.json` or `name@YYYY-MM-DD=file.json`.

- **Period** (`Zeitraum`, first switch): the name, else derived from `period` of the matrix: `Gesamt` for the whole
  export, `bis 31.12.2025`, `ab 01.01.2026` or `von – bis`. `Gesamt` comes first.
- **Data state** (`Datenstand`, second switch): the date, else `reference_date`, `cutoff`, `Stichtag YYYY-MM-DD` in
  the notes, the end of a whole-export period, the file's modification date. In mode `datenkranz`: the latest
  submission. Several files of one period with different data states give the history view: changes per LE,
  dumbbells, "now suppressed".

```sh
python3 build_dashboard.py \
  Gesamt@2026-11-12=matrix_2026-11-12.json Gesamt@2026-11-19=matrix_2026-11-19.json \
  "bis 2025=matrix_until2025.json" "ab 2026=matrix_from2026.json" \
  --time-series time-series.csv --output RD-Dashboard.html
```

`--time-series` (repeatable) takes the output of `rd_time_series.py` (JSON or CSV), a matrix JSON with
`time_series`, or the `*_zeitreihe.csv` of `dnpm_evaluation_matrix.py`. The page shows, per block, the source given
last; blocks are the partitions found in the data (categories that add up to their base) and the case count.

## What the page shows

Navigation: **Übersicht** (case count, five key figures, largest changes between
data states), **Kennzahlen** (mode `datenkranz`: **Felder**; a tree by section, each figure across all LEs, a
distribution per section, history: data states, periods, months), **LE** (table of key figures, profile of one LE,
its history). Every detail view has the tabs **Stand** and **Verlauf**. Values link to "figure × LE".

- **rd**: key figures Diagnose, Diagnoserate (closed cases), Broad Consent, GFK2, Trio, without Orphacode. LEs
  without any documented GFK2 are flagged "GFK1=GFK2" at the diagnostic rate over closed cases.
- **onko**: sequenced, GFK2 (MTB), therapy or study recommendation, recommendation started, Broad Consent, follow-up.
- **datenkranz**: per LE and use case (RD, MTB; the switch "Zeitraum" selects the use case; `Alle` only when no use
  case is suppressed, so never next to RD or an unknown use case, see Privacy): submissions by type and
  month, cases (episodes with an initial submission, state of the latest non-test submission), episodes without
  initial submission, submissions that could not be converted; for every path of the Datenkranz (generic walk,
  arrays as `[]`) the share of cases where it is filled, for enum-like fields (at most 30 distinct values) the
  distribution of values, for dates the months. Months are those of the initial submission (submissions: of the
  submission itself).

Everything domain specific is a pattern on section and item names in `DOMAINS`; names, sections, distributions and
time series blocks come from the data. Unknown matrices get generic key figures.

## Privacy

- Only aggregates per LE. `problems[]` of the matrices (case keys, TANs) is never read; the Rückfragen of the
  matrices are shown as counts only.
- Mode `datenkranz` never outputs case keys, patient or episode ids, TANs, local case ids, free text or identifier-like
  fields: paths with a segment like `id`, `*Id`, `tan*`, `tanC`, `patient`, `identifier`, `address*`, `*name`,
  `comment`, `note`, `*text`, `description`, `*remark`, `freeText`, `Freitext`, `Bemerkung` are skipped, and so is
  every text field with more than 30 distinct values. Values are shown only for code-like strings (no blanks, at
  most 40 characters); a field with any other string (free text) shows its fill rate only, so a single distinct
  remark never becomes a value label. The skipped paths are listed by name in the notes of the page; the case count
  in the notes is the one shown (suppressed where the cell is).
- **Suppression applies to rare diseases (SE/RD) only.** By decision of the data holders (26.11.2026) oncology data
  is not suppressed: mode `onko` and the use case MTB of mode `datenkranz` show every value exactly, also below 5, and
  the page then says so instead of explaining suppression (footer, legends, month charts). An oncology matrix or time
  series that still contains suppressed cells (`<5`, from an older `dnpm_evaluation_matrix.py`) is refused.
- RD: `<5` means 1 to 4 cases, or hidden to protect a neighbouring cell (then also 5 or more). The page shows
  "unterdrückt" without bounds, never 0, and never computes a value back. Bounds (1–4) are shown only for files that
  state `"suppressed": {"complementary": 0}`. Changes are "n. b." (not computable) as soon as one side is suppressed.
  In the month charts, a month that would follow from the whole period minus the others is hidden too.
- Mode `datenkranz` suppresses the use case RD (and submissions without a known use case) like the RD matrix: counts
  from 1 to `--min-cell` − 1 (default 5), every value with a base below that, and, complementarily, the smallest
  further nonzero cell in every group with exactly one suppressed cell (a row over Gesamt and the LEs, the whole period
  and its months, the categories of a field and its fill count), until none is left. The use case MTB is exact. There
  is no use case `Alle` next to a suppressed use case: all use cases minus the exact MTB, or minus a separately
  suppressed unknown use case, would give the suppressed RD cells away. With several use cases the page has no
  time series (it would show the months of one use case for all). The closure is per single relation; unlike the
  RD matrix (see the main README, *Protection against the whole system*) it isn't checked against the whole system.
- The file is labelled "Synthetische Daten" when the LE names look synthetic (`LE-A`, `SYN-…`), `--synthetic` /
  `--real` override. The header names the source files, data states and build time; the footer says
  "Aggregate je LE, nur intern". The tables are meant for the data nodes and the LEs, not for publication.

## Third-party files

sql.js and the fonts are not in the repository. On the first run the script downloads the pinned versions and checks
their SHA-256 (`VENDOR_PINS`) into `--vendor` (default `.vendor/` next to the script, ignored by git); later runs
work offline (`--offline` never downloads). A directory with the files under their names (or Google's file names
under `fonts/`) can be given with `--vendor`.

| File | Source | Licence |
|---|---|---|
| `sql-asm.js` | sql.js 1.10.3 (asm.js build), cdnjs | MIT; contains SQLite (public domain) |
| `asap-*.woff2` | Asap (Google Fonts, latin and latin-ext) | SIL Open Font License 1.1 |
| `nunito-*.woff2` | Nunito (Google Fonts, latin and latin-ext) | SIL Open Font License 1.1 |

The embedded copies keep their licence notes; the page footer names them.

## Files and tests

- `build_dashboard.py`: the builder (inputs, Datenkranz aggregation and RD suppression, SQLite database for the page,
  page assembly).
- `template.html`: the page (HTML, CSS, JavaScript; sql.js reads the embedded database). Placeholders `{{TITEL}}`,
  `{{TEXT}}`, `{{NAV_A}}`.
- `test_build_dashboard.py`: synthetic data only, no network (fake third-party files):
  `python3 -m unittest discover -s scripts/dashboard`.

The page was tested in headless Chrome (file://, network blocked, light and dark, 1440 and 400 px wide): every view,
period and data state, no console messages, no network requests. Safari and Firefox were not tested automatically.
