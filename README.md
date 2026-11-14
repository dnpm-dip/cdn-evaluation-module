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
and per Leistungserbringer (counts below 5 suppressed), and lists the cases to clarify with their LE
(Rückfragen). Specification: `docs/rd-evaluation-matrix.txt`. Export without `--until`, since later
follow-ups determine the status of a case, and set the evaluation period in the script instead:
```
python3 scripts/rd_evaluation_matrix/rd_evaluation_matrix.py /out/export-datenkranz_<time>.json \
  --orphanet en_product7.xml --from 2024-07-01 --until 2026-01-01 --output matrix
uv run --with openpyxl python scripts/rd_evaluation_matrix/fill_template.py matrix.json \
  scripts/rd_evaluation_matrix/Auswertmatrix_Template.xlsx --output matrix.xlsx
```
This writes `matrix.json`, `matrix.csv` (long table LE | Abschnitt | Kategorie | ... for per-LE
analyses), `matrix_rueckfragen.csv` (case keys: only for the respective LE) and `matrix.xlsx` (one
value column per LE). `--orphanet` is the Orphadata linearisation
(https://www.orphadata.com/data/xml/en_product7.xml), needed for the disease groups.
Tests: `python3 -m unittest discover -s scripts/rd_evaluation_matrix`.

Time series of the matrix's key figures, per month or quarter, in total and per Leistungserbringer, are
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
  concerned, not for publication.

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
