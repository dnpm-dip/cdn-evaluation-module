# DNPM - Central Data Node Evaluation Module

Computes statistics requested by stakeholders on the backups of the
[Central Clinical Data Node](https://github.com/dnpm-dip/central-data-node) (CCDN): the reports and
submissions in the `backup` collection of its MongoDB. It runs on demand, as a service container of
the central-data-node-deployment, which also provides its Docker configuration.

Each run evaluates one statistic, and needs the RSA private key (and its passphrase) matching the
public key the CCDN encrypts the backups with.

## Architecture

A run consists of two steps:

1. **Data collection** (`collect`, common to all statistics): reads the backup documents matching
   the filter given on the command line, decrypts them (RSA-OAEP-SHA256 + AES-256-CBC, reassembling
   ciphertexts the CCDN split into `largeBackupParts`) and groups them into `Case`s: all
   submissions with their reports (one per TAN) of one episode of care of a patient, i.e. with the
   same site, use case, patient and `episodeOfCare`, plus the patient's deletions.
   - The reports are always read, since they define the cases; they are small and held in memory.
     Older reports without `episodeOfCare` get the episode their submission refers to, or, like on
     the DIP node, the one of the patient record in whose period the report was created.
   - Submissions and deletions are only read if the statistic needs them. Submissions are fetched
     case by case while the cases are streamed, so they are never all in memory at once.
   - Deletions remove their TAN's report and submission, so their episode is unknown: a deletion
     event is attached to all cases of its patient, or forms a case of its own if none are left. A
     manual deletion notice names no patient, so it always forms a case of its own.
   - An unreadable report or deletion is left out on its own, a case with an unreadable submission
     as a whole; both are counted.
2. **Aggregation** (`statistics`, one implementation per statistic): computes tables from the cases.

The result is JSON: the tables plus everything needed to interpret and reproduce them (statistic,
module version, filter, parameters, numbers of documents read and cases left out). The tables are
written row by row, so the notes and the numbers of the collection follow them.

## Usage

```
run <statistic> [options]   Runs a statistic on the backups
list                        Lists the available statistics and their parameters
help                        Shows all options
extract-scripts [DIR]       Copies the Python scripts (see below) onto the host (Docker image only)
```

Filters on the unencrypted fields (combined with AND; lists comma-separated or the option repeated):

| Option | |
|---|---|
| `--site CODE[,CODE...]` | Site code(s), e.g. `UKT,UKFR` |
| `--usecase UC[,UC...]` | `MTB` and/or `RD` |
| `--from DATE` | Cases with a submission at or after DATE (ISO, e.g. `2026-01-01` or `2026-01-01T12:00`) |
| `--until DATE` | Cases with a submission before DATE (exclusive) |

The time frame selects whole cases: those with at least one submission in it, with all their
submissions, also those outside of it. Cases without submissions (deletions only) are left out
when a time frame is given.

Statistic-specific parameters are set with `--param NAME=VALUE`, see `list`.

Configuration, each as option or environment variable:

| Option | Environment variable | |
|---|---|---|
| `--mongodb-uri URI` | `CCDN_MONGODB_URI` | Same as for the CCDN, e.g. `mongodb://mongodb:27017/ccdn` |
| `--key FILE` | `CCDN_BACKUP_PRIVATE_KEY_PATH` | RSA private key, PKCS#8 PEM, as created per the deployment's README |
| `--passphrase-file FILE` | `CCDN_BACKUP_KEY_PASSPHRASE` | The key's passphrase; prompted for if neither is given (needs a terminal) |
| `--output DIR` | `CCDN_EVALUATION_OUTPUT_DIR` | Writes `<statistic>_<time>.json` and one CSV per table into DIR, instead of JSON to stdout |

There is deliberately no option for the passphrase itself, since command lines end up in the shell
history and process list.

Logs go to stderr, so stdout carries only the result. Exit codes: `0` success, `1` failure,
`2` result written, but documents couldn't be read and were left out (see the log).

Example, the quarterly submission counts of 2026 for two sites:
```
run submission-counts --site UKT,UKFR --from 2026-01-01 --until 2027-01-01 --param period=quarter
```

For evaluations not implemented as statistic, `export` writes all matching documents as stored,
with their content decrypted, e.g. the reports and submissions of one site:
```
run export --site UKT --output /out
```

`export-datenkranz` writes the submissions converted into the BfArM "Datenkranz" schema (oncology
for MTB, rare diseases for RD) by the mappings of
[bfarm-schema-dto-model](https://github.com/dnpm-dip/bfarm-schema-dto-model), listing those that
couldn't be read or converted with the error. The submitter and data node IDs come from the
mappings' config, given as file in `CCDN_BFARM_MAPPINGS_CONFIG_FILE`, else its built-in defaults.
```
run export-datenkranz --site UKT --usecase RD --output /out
```

The RD evaluation matrix (Auswertematrix) is computed from such an export by
`scripts/rd_evaluation_matrix/rd_evaluation_matrix.py`, in Python (standard library only) and on
the Datenkranz alone, so that it can be shared with the other KDKs. It reports every value in total
and per Leistungserbringer (counts below 5 of the rows derived from the diagnosis codes suppressed, plus
complementary cells in these rows so that none can be recomputed from sums, see below), and lists the cases to clarify with their LE
(Rückfragen). Specification: `docs/rd-evaluation-matrix.txt`. Export without `--until`, since later
follow-ups determine the status of a case, and set the evaluation period in the script instead:
```
python3 scripts/rd_evaluation_matrix/rd_evaluation_matrix.py /out/export-datenkranz_<time>.json \
  --orphanet en_product7.xml --from 2024-07-01 --until 2026-01-01 --output matrix
uv run --with openpyxl python scripts/rd_evaluation_matrix/fill_template.py matrix.json \
  scripts/rd_evaluation_matrix/Auswertmatrix_Template.xlsx --output matrix.xlsx
```
This writes `matrix.json`, `matrix.csv` (long table LE | Abschnitt | Kategorie | ... for per-LE
analyses), `matrix_rueckfragen.csv` (case keys of all LEs: for the KDK only) and `matrix.xlsx` (one
value column per LE). Recommended: `--rueckfragen-per-le`, which writes only `matrix_rueckfragen_<LE>.csv` per LE
and leaves the Rückfragen out of the JSON and so of the xlsx, so that each LE gets only its own case keys. For a
total matrix together with matrices per quarter, use `monthly_matrices.py` (below), not separate runs. `--orphanet` is the Orphadata linearisation
(https://www.orphadata.com/data/xml/en_product7.xml), needed for the disease groups.
Tests: `python3 -m unittest discover -s scripts/rd_evaluation_matrix`.

Small-cell suppression (`--min-cell`, default 5, 1 disables; `--suppress-scope codes|all`, default `codes`).
By decision of the data holders (26.11.2026) only the diagnosis codes themselves are suppressed: with `codes` only
the rows that tell *which* disease a case has, i.e. the rows derived from its diagnosis codes (Orphacode, and the
ICD-10 codes of the neuromuscular group), are suppressed (`CODE_SECTIONS`, `CODE_ITEMS` in
`rd_evaluation_matrix.py`):
- `Orphacode` (without Orphacode, `noMatchingCodeExists`),
- `Erkrankungsgruppen gesamt`, `Erkrankungsgruppen Adult (ab 18)`, `Erkrankungsgruppen Pädiatrisch (unter 18)`,
  each group with its status rows (group × status),
- the row `Rückfragen an LE (…) | Diagnose ohne Erkrankungsgruppe`.

Every other count, share and duration statistic is shown exactly, also below 5, including the diagnosis status rows
(`Diagnosestatus (alle Fälle)`, `Diagnoserate (abgeschlossene Fälle)`, `Diagnoserate nach WGS-Seq. Art`,
`Diagnosestatus nach Altersgruppe`, `Vorbefunde, Status …` and `Vorbefunde nach Jahr, Status …`): they tell whether
a case has a diagnosis, not which one. The bases of the code rows are suppressed with them (the cases of a disease
group, base of its status rows), except those that count no code (`EXACT_CODE_BASES`: the cases, the cases of the
age block of a disease group block). Every suppressed cell, also every complementary one and every one of the
protection below, is a code cell: the suppression never spills into another row. The complementary suppression
stays closed within the code rows: per row over the LEs and Gesamt, and per sum relation of a column, also against
an exact total or margin (e.g. the groups sum to the exact cases of the block, a status summed over the groups is
the exactly shown `Diagnosestatus` where no case is left unsequenced). The relations are the partitions found from
the counted sets and those of `sum_relations()`, which also run across sections: a disease group against its four
status rows and their base, Adult + Pädiatrisch = gesamt, the statuses over the groups against `Diagnosestatus`
(per age block against `Diagnosestatus nach Altersgruppe`), "ohne Orphacode" against the group "unbekannt: kein
Orphacode", "Diagnose ohne Erkrankungsgruppe" against the unknown groups, and those a reader knows from the data
where they hold. Where the only other parts of such a relation are exact or zeros, a zero code cell is suppressed
instead (preferably one whose row has a nonzero Gesamt), so that a suppressed count isn't the exact total minus
shown zeros; a zero that is known to be 0 anyway (next to exact zeros, also through other relations, as all counts
are ≥ 0) is never chosen, and where one side of a relation shows only zeros, a zero of that side is suppressed too
(in a row the zero of Gesamt). Gesamt is suppressed where it is the count of a single LE, and then complementarily
within the Gesamt column, also in rows with several LEs. The former scope `diagnoses` (also the status rows) is
gone; `--suppress-scope diagnoses` is refused with a hint, and results written with it (`"suppress_scope":
"diagnoses"`) are still read by `fill_template.py` and the dashboard. A code cell's quantity is kept apart from an exact cell
that happens to count the same cases. `--suppress-scope all` suppresses every row, with
the same relations. Tested on random synthetic sets (`SumRelationTests`): no relation has exactly one suppressed
cell. Several files of the same cases (a total matrix and the matrices per quarter) are suppressed
together (`run_joint`, used by `monthly_matrices.py`): each cell of the total is then also the sum of the same cell
of the periods (per LE and Gesamt), so that no period follows from the total and the other periods by a single
relation (tested in `scripts/monthly_matrices`, `JointSuppressionTests`). The losses that have no date (submissions
that couldn't be converted, episodes without a readable initial submission) are counted in every matrix of a run
and are equal there, not added up (`NOT_ADDITIVE`). Separate runs are each closed on their own but not together. The submissions of an episode are ordered by `submittedAt` parsed as a time (time zones to UTC), as in the
oncology matrix; the latest one gives the case's state.

**Protection against the whole system.** The closure closes every single relation, not systems of them: several
relations, each with two or more suppressed cells, can together still determine a suppressed count (e.g. an LE's
quarter from two quarter totals, two LE totals and shown zeros; also where every case has a GFK2, the exact row
"noch keine GFK2" = 0 makes the open cases 0 and the closed cases the exact sequenced cases). After the closure,
every run therefore checks the whole linear system of all files of the run (`protect` in `rd_evaluation_matrix.py`):
the relations between single cells as a reader sees them (Gesamt = Σ LEs per row, total = Σ quarters per cell,
the categories of a section, `sum_relations()`, cells that count the same set in every column), with the zeros
a reader can infer (all counts are ≥ 0) as constants. A suppressed count follows exactly if it is 0 in every
solution of the homogeneous system; this is checked with one pseudo-random solution (sparse elimination modulo
a 61-bit prime), which errs, if at all, towards "follows". As long as a count of 1 to `--min-cell − 1` follows,
further cells are suppressed (per such count the cheapest one next to it through the suppressed cells: a nonzero
LE cell of the smallest count first, zeros, Gesamt and the total last; more of them where a count stays
computable), then the closure again; deterministic (every choice is ordered by the cells, the same output
whatever `PYTHONHASHSEED`). With `--suppress-scope codes` these further cells are code cells too, zeros included
where a reader can't infer them. Where a code count can't be protected cell by cell (it stays computable for long,
or follows even with every code cell suppressed), the whole code block of that column is suppressed (every code
row of the LE in that file, zeros included), then with Gesamt of the file, then the LE and Gesamt in every file,
then every column, always the code rows only and never another row; the notes give the number of such columns
(`suppressed.whole_blocks` in the JSON). **The output is blocked** if a suppressed count still
followed (it shouldn't): `rd_evaluation_matrix.py` and `monthly_matrices.py` then write nothing (files of the run
already written are removed, those of an earlier run are left), print the cells (LE, section, item; no counts,
for the KDK only) and exit with an error. Tested with an independent exact solver (fractions) on random synthetic
sets, also with tiny LEs, and on the counterexamples of the review (`ProtectionTests`, `JointProtectionTests`).
Runtime on a synthetic set of 25 LEs, ~710 rows, five quarters and their total (6 files): see the PR (about one
to two minutes with `codes`, 20 to 35 s with `--suppress-scope all`).
Not checked: bounds (all counts are ≥ 0, so a suppressed cell is at most a shown count it is part of, e.g. the
open cases at most "noch keine GFK2") and, with `--suppress-scope all`, the duration statistics, which are hidden
per file: the extrema and quartiles of the total bound the hidden ones of the periods (all equal where minimum =
maximum). Known limitation: in a row where only one LE counts, its count is protected, but the zeros of the other LEs
show where the cases are. The data owners decided (26.11.2026) that
only the diagnosis codes are suppressed, and that showing the small counts of every other row per LE (diagnosis
status, sex, age group, insurance, consent, durations of single cases, …) is acceptable; `--suppress-scope all`
remains as the opt-out.

Follow-up (sections `Follow-up (Fälle mit GFK2)`, `Dauer GFK2 bis erstes Follow-up`,
`Follow-up: Pflichtfelder (Follow-up-Einträge aller Einreichungen)`, not code rows, so exact), the same
schedule logic as the follow-up SOP metrics of the oncology matrix, with one interval for RD
(`--follow-up-months`, default 12), all at `--reference-date`. The follow-ups are those of all submissions
(`followUp.followUpRds[]`), each once.
- Base: the cases with GFK2. `mit Follow-up`: at least one follow-up entry.
- `Stand am Stichtag`, a partition of the base: `verstorben` (`vitalStatus` deceased in a follow-up, no further
  follow-up due); `noch nicht fällig` (GFK2 less than one interval before the reference date); `fällig, aktuell`
  (a `followUpDate` in the interval before the reference date, counted back from it: after the reference date
  minus the interval, up to the reference date); `fällig, überfällig` (none). `Follow-up aktuell,
  von fälligen` = current / (current + overdue).
- `Dauer GFK2 bis erstes Follow-up`: days from GFK2 to the earliest `followUpDate` on or after GFK2; count,
  mean, median, quartiles (QUARTILE.INC), minimum, maximum.
- Mandatory fields per follow-up entry: `followUpDate` readable, `vitalStatus` living or deceased,
  `diagnosisEstablished` given; `deathDate` of the entries with `vitalStatus` deceased.

The RD time series keeps its own follow-up block (`--follow-up-due-days`, "fällig: … innerhalb der Frist"); the
quarterly matrices (`monthly_matrices.py --follow-up-months`) contain the sections above per period.

Time series of the matrix's key figures, per quarter (default) or month (`--period`), in total and per Leistungserbringer, are
computed from the same export by `scripts/rd_evaluation_matrix/rd_time_series.py`, also standard library
only. It uses the functions of `rd_evaluation_matrix.py`, so that cases, LEs, the status of a case and the
validity of its broad consent are defined in one place: over the whole period (Zeitraum `gesamt`) its figures
equal those of the matrix. By the period of a case's initial submission (the date `--from`/`--until` refer
to) it reports
- the cases,
- the broad consent of the latest submission: transmitted, valid, valid of the transmitted ones
  (Zustimmungsquote), why not valid (denied, expired, no research provision) and why not transmitted
  (`metaData.researchConsents[].noScopeJustification`),
- the diagnosis status of all cases and of the closed ones, as the sections of the matrix,
- the follow-ups: cases with a follow-up submission and with follow-up content, of all cases and of those
  with GFK2; median and quartiles of the days from GFK2 to the first follow-up,
- the process timeline (block `Zeitstrahl: Tage relativ zur GFK1`): per milestone M (MV-Einwilligung =
  earliest `mvConsent.scope[].date` of domain `mvSequencing`, Symptombeginn = `case.diagnosisRd.symptomOnsetDate`
  without `1800-01`, ZSE-Erstkontakt = earliest `case.priorRds[].zseContactDate`, GFK2, Erstmeldung beim KDK =
  date of the initial submission, Erstes Follow-up = earliest `followUpRds[].followUpDate`) the lines
  `M: Fälle` (cases with an offset, base: the cases) and `M: 1. Quartil (Tage)`, `M: Median (Tage)`,
  `M: 3. Quartil (Tage)` (the quartile of date(M) − GFK1 in days as the value, QUARTILE.INC, left empty below
  `--min-cell` values; no minimum or maximum, they are single cases). For GFK2, Erstmeldung and Erstes
  Follow-up, which must follow GFK1, `M: unplausibel (vor GFK1)` counts offsets < 0, which are left out of
  `M: Fälle` and the quartiles. `ohne GFK1: Fälle` counts the cases that can't be evaluated for want of a
  GFK1 (`metaData.molecularBoardDecisionDate`). Dates given as a month (`YYYY-MM`) count as the 15th,

and by the calendar period of the submissions themselves their number by type, the submissions the export
couldn't convert and the episodes without initial submission, e.g. to reconcile with the number of
Meldebestätigungen.
```
python3 scripts/rd_evaluation_matrix/rd_time_series.py /out/export-datenkranz_<time>.json \
  --from 2024-07-01 --until 2026-11-01 --period quarter --follow-up-due-days 365 --output time-series
uv run --with matplotlib python scripts/rd_evaluation_matrix/plot_time_series.py time-series.csv \
  --output time-series
```
This writes `time-series.csv` (long table LE | Zeitraum | Block | Kennzahl | Wert | Bezug | Nenner | Anteil_% |
Median | Q1 | Q3 | n) and `time-series.json`, and from the CSV `time-series.pdf` and
`time-series_<figure>.png`: per figure one page of small multiples, a panel per LE with the total as
reference line, ordered by the value of the LE over the whole period (`--order`).

Three things to know when reading the figures:
- The Zustimmungsquote (valid, of the transmitted consents) is an upper bound of the share of patients who
  agreed: who declines without signing has no consent transmitted, but the justification
  `patient refuses to sign consent`. A second rate counts these cases in its base. For exports of DIP nodes
  the justifications are missing ("ohne Begründung"), as long as the Datenkranz mapping of
  bfarm-schema-dto-model doesn't write `noScopeJustification`; until then both rates are upper bounds.
  The reason the DIP submission itself states (`metadata.reasonResearchConsentMissing`) is counted in the lines
  "nicht übermittelt, Grund laut DIP: …" per LE and period, read from the column `reasonResearchConsentMissing`
  of the export when the export has it (see export-datenkranz).
- By the period of the initial submission, a recent period holds only the early follow-ups, so its
  follow-up rates are lower and its durations shorter. With `--follow-up-due-days N` the lines
  "fällig: … innerhalb der Frist" compare between periods: cases whose GFK2 is at least N days before
  `--reference-date` (best the date of the export), follow-ups within N days of the GFK2.
- Counts below `--min-cell` (default 5) are suppressed as in the matrix, percentages with a suppressed count
  or base and medians of fewer values are left out, and, stricter than the matrix, every count within fewer
  cases, also a zero; the plots show such periods as gaps. Neither output contains case keys or TANs. The
  cells per LE and month are small all the same, and part of the suppressed ones follow from the totals and
  the other cells: the tables, suppressed or not (`--min-cell 1`), are meant for the data nodes and the LE
  concerned, not for publication, and not to be handed out next to the quarterly matrices (months and quarters
  aren't suppressed together: a quarter is the sum of its months). `--suppress-scope codes` (default) applies this,
  as in the matrix, only to the rows derived from the diagnosis codes; the time series has none, so with `codes`
  every figure is exact, also `Diagnosestatus (alle Fälle)` and `Diagnoserate (abgeschlossene Fälle)`;
  `--suppress-scope all` suppresses every block.

The tests run with those of the matrix (`python3 -m unittest discover -s scripts/rd_evaluation_matrix`);
those of the plots are skipped without matplotlib.

The data quality queries (sheet "Datenqualität_KDK (V2) opt" of
`docs/Daten Qualitätsabfragen und Statistik.V4sb.xlsx`) are computed from such an export by
`scripts/data_quality_mvh/data_quality_mvh.py`, also standard library only. It reads the export row by row,
so it isn't limited by memory, and writes a CSV for Excel: per field of the sheet and value, the
count over all sites and per Leistungserbringer (submitterId).
```
python3 scripts/data_quality_mvh/data_quality_mvh.py /out/export-datenkranz_<time>.json \
  --from 2026-01-01 --until 2027-01-01 --output datenqualitaet.csv
```
Tests: `python3 -m unittest discover -s scripts/data_quality_mvh`.

The RD and oncology matrices for a whole period together with one matrix per calendar quarter (one evaluation
per quarter of initial submission, one xlsx each, Q4/2025 = Erstmeldung 2025-10-01 to before 2026-01-01; oncology
also per month with `--matrix dnpm --period month`) come from one run of `scripts/monthly_matrices/monthly_matrices.py` (standard
library only, on PROD). It reads the export once and writes `<dir>/rd/rd_<period>.json` and
`<dir>/dnpm/dnpm_<period>.json` (`<period>` = `YYYY-Qn` or `YYYY-MM`), and the total matrix of exactly these
periods as `<period>` = `gesamt`. The first period is that of the earliest initial submission in the export, so
there are no empty leading periods; `--from`/`--until` (`YYYY-MM` or `YYYY-Qn`) narrow the range.
- RD (SE): **only per quarter**, `--period month` is refused for `--matrix rd` and `both` (monthly RD cells are
  small, and months next to quarters would have to be suppressed together). The total and the quarter matrices are
  **suppressed together** (`rd_evaluation_matrix.run_joint`) and checked against the whole system of all files
  (see *Protection against the whole system* above): no suppressed count follows from the total and the quarters,
  also not from several relations together; otherwise **nothing is written** (no RD and no oncology file of the
  run, those of an earlier run stay) and the run ends with an error. `--min-cell`, `--suppress-scope` (default
  `codes`) and `--follow-up-months` apply to the RD matrices. Hand out only the total and the period matrices
  of one run; a total from a separate run of `rd_evaluation_matrix.py` is suppressed on its own, and together with
  these period matrices suppressed cells can become computable.
- Oncology: **not suppressed** (decision of the data holders, see `scripts/dnpm_evaluation_matrix/README.md`); the
  total matrix carries the time series by the same periods.

`--reference-date` is the same for all periods. `--rueckfragen-per-le` (recommended) writes the Rückfragen only per
LE (`<period>_rueckfragen_<LE>.csv`), no file of all LEs, and leaves them out of the RD JSON; two LEs whose names
give the same file name are refused. An `--output-dir` with files of an earlier run is refused (`--replace` deletes
the old `rd_*`/`dnpm_*` files first): an old period's JSON with the Rückfragen of all LEs would otherwise end up in
the xlsx of `fill_monthly.py` next to the new ones.
`scripts/monthly_matrices/fill_monthly.py` (openpyxl, outside of PROD) writes an xlsx next to each result (also
`gesamt`) by `fill_template.py`. For oncology, a template is created from the rows on the first run
(`<dir>/Auswertmatrix_MTB_Template.xlsx`, or `--dnpm-template`); its sheet "Rückfragen an LE" stays empty,
since the oncology JSON contains no case keys (see `dnpm_<period>_rueckfragen*.csv`).
```
python3 scripts/monthly_matrices/monthly_matrices.py /out/export-datenkranz_<time>.json \
  --orphanet en_product7.xml --reference-date 2026-11-24 --rueckfragen-per-le --output-dir quarterly
uv run scripts/monthly_matrices/fill_monthly.py quarterly
```
`--until` is exclusive (the periods starting before it); `--matrix rd|dnpm` restricts to one of them,
`--matrix dnpm --period month` writes the oncology matrices per month and their total. Tests: `python3 -m unittest discover -s scripts/monthly_matrices`.

### Running the scripts on the host

The Python scripts under `scripts/` evaluate the exports outside of the container. The Docker image
ships them (without tests and data), so that on PROD they can be unpacked into a host directory
and run there manually, in the version matching the module that wrote the export:
```
docker run --rm -v "$PWD/scripts:/scripts" ghcr.io/dnpm-dip/cdn-evaluation-module:<version> extract-scripts
python3 scripts/rd_evaluation_matrix/rd_evaluation_matrix.py export-datenkranz_<time>.json ...
```
If the module is a service of the deployment's Compose file, use the service name instead; this
takes the image version the deployment pins:
```
docker compose run --rm -v "$PWD/scripts:/scripts" <service> extract-scripts
```
Both start a new container from the image rather than naming a running one: the directory is
mounted when a container is created, which a running container can't do afterwards.
The directory must be mounted; create it beforehand (`mkdir scripts`), otherwise Docker creates
it owned by root. Files of the same name are
overwritten, others like results are kept; the copies get the owner of the directory, and
`VERSION.txt` records the module version they come from. The scripts need Python 3 on the host,
`fill_template.py` openpyxl and `plot_time_series.py` matplotlib.

Large submissions are decrypted in memory; for more heap, set `JAVA_OPTS=-Xmx4g` in the container.
`export` and `export-datenkranz` stream the documents into the output case by case, so only one
case (and the reports, see above) is held in memory at a time.

## Adding a statistic

When stakeholders request a new statistic:

1. Implement `de.dnpm.ccdn.evaluation.statistics.Statistic` as an object in that package, see
   `SubmissionCounts` (reports only) or `MtbDiagnoses` (reads the submissions) as examples:
   - `name`: the kebab-case name to run it by. Never change it once released, since stakeholders
     and scripts refer to it.
   - `documentTypes`: only what's needed. Reports are small (and always read, since they define the
     cases), submissions may be several MB each.
   - `parameters`: statistic-specific options with defaults, validated before the run.
   - `restrict`: e.g. to the use case it applies to.
   - `aggregate`: fold over the cases (they can be traversed only once) into `Result` tables.
     Tables too large for memory, like those of the exports, can instead be streamed: rows given
     as an `Iterator` over the cases are written while the cases are read (see `Export`), and the
     notes are evaluated after the tables, so they can report counts of the streamed rows.
     A case may hold several submissions (`Case.submissions`, ordered by time).
     Read submissions through the dnpm model (`SubmissionDocument.as`, `.mtb`, `.rd`); reading only
     the parts needed, e.g. `(json \ "diagnoses").validate[List[MTBDiagnosis]]`, tolerates
     deviations elsewhere in records from older DIP node versions.
     Add notes on anything needed to interpret the tables, e.g. cases left out.
2. Register it in `Statistics.all`.
3. Add tests to `StatisticsTests`, with cases built from `Fixtures`.
4. Merge with a conventional commit message (`feat: statistic ...`): release-please then opens a
   release PR, whose merge builds and publishes the Docker image. Once the deployment pulls it,
   the statistic can be run (and rerun with other filters) by its name.

## Building
`sbt test` runs the tests, `sbt assembly` creates `target/scala-2.13/dnpm-ccdn-evaluation.jar` and
copies it into the project root, so that `docker build .` picks it up.
Releases are packaged as Docker image through the GitHub action in `.github/workflows/release.yml`,
like the central-data-node itself. The dnpm libraries are resolved from GitHub Packages, which
requires `GITHUB_TOKEN` to be set.

Dependencies are kept at the versions of the central-data-node, whose backups this reads, except
for service-base, mtb-dto-model and rd-dto-model, raised to the versions bfarm-schema-dto-model
(the Datenkranz mappings) requires. Reports
are read as `Submission.Report` of service-base; the CCDN's own processing statuses in their
`status` field are mapped onto that class's `unsubmitted`/`submitted` (`model/Report.scala`).
Check this when the CCDN's backup format changes.
