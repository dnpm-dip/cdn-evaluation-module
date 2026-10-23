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
   the filter given on the command line, only of the types the statistic needs (e.g. only the small
   reports), decrypts them (RSA-OAEP-SHA256 + AES-256-CBC, reassembling ciphertexts the CCDN split
   into `largeBackupParts`) and groups them by TAN, site and use case into `Case`s: report,
   submission and deletions of one data submission. Cases are streamed, not loaded into memory all
   at once. A case with an unreadable document is left out and counted.
2. **Aggregation** (`statistics`, one implementation per statistic): computes tables from the cases.

The result is JSON: the tables plus everything needed to interpret and reproduce them (statistic,
module version, filter, parameters, numbers of documents read and cases left out).

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
| `--tan TAN[,TAN...]` | Transfer TAN(s) |
| `--from DATE` | Submitted at or after DATE (ISO, e.g. `2026-01-01` or `2026-01-01T12:00`) |
| `--until DATE` | Submitted before DATE (exclusive) |

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
`2` result written, but cases were left out since documents couldn't be read (see the log).

Example, the quarterly submission counts of 2026 for two sites:
```
run submission-counts --site UKT,UKFR --from 2026-01-01 --until 2027-01-01 --param period=quarter
```

Large submissions are decrypted in memory; for more heap, set `JAVA_OPTS=-Xmx4g` in the container.

## Adding a statistic

When stakeholders request a new statistic:

1. Implement `de.dnpm.ccdn.evaluation.statistics.Statistic` as an object in that package, see
   `SubmissionCounts` (reports only) or `MtbDiagnoses` (reads the submissions) as examples:
   - `name`: the kebab-case name to run it by. Never change it once released, since stakeholders
     and scripts refer to it.
   - `documentTypes`: only what's needed. Reports are small, submissions may be several MB each.
   - `parameters`: statistic-specific options with defaults, validated before the run.
   - `restrict`: e.g. to the use case it applies to.
   - `aggregate`: fold over the cases (they can be traversed only once) into `Result` tables.
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
`sbt test` runs the tests, `sbt assembly` creates `target/scala-2.13/dnpm-ccdn-evaluation.jar`.
Releases are packaged as Docker image through the GitHub action in `.github/workflows/release.yml`,
like the central-data-node itself. The dnpm libraries are resolved from GitHub Packages, which
requires `GITHUB_TOKEN` to be set.

Dependencies are kept at the versions of the central-data-node, whose backups this reads. Reports
are read as `Submission.Report` of service-base; the CCDN's own processing statuses in their
`status` field are mapped onto that class's `unsubmitted`/`submitted` (`model/Report.scala`).
Check this when the CCDN's backup format changes.
# cdn-evaluation-module
