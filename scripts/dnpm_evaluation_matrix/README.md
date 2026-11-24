# Oncology evaluation matrix (dnpm_evaluation_matrix)

The evaluation matrix (Auswertematrix) for oncology (DNPM / KDK-Onko, Modellvorhaben Genomsequenzierung,
§ 64e SGB V) from BfArM Datenkranz submissions: every metric in total, per Leistungserbringer (LE), for a
period (`--from`/`--until`) and per month or quarter. It is the counterpart of
`scripts/rd_evaluation_matrix/rd_evaluation_matrix.py` (rare diseases): same input, same CLI style, same
JSON structure (`fill_template.py` of the RD script reads it), German labels.

Standard library only, Python 3.10 or later (tests pass with 3.9 and 3.15).

```sh
python3 dnpm_evaluation_matrix.py export-datenkranz_2026-11-01_10-00-00.json \
  --from 2025-07-01 --until 2026-11-01 --reference-date 2026-11-01 --resolution month --output onko-matrix
python3 -m unittest discover -s scripts/dnpm_evaluation_matrix          # tests
python3 make_synthetic.py --output synthetic-export.json                # synthetic export for dashboards
```

Options: `--from`/`--until` (cases by the date of their initial submission, submissions and Rückfragen by
their own date), `--cutoff` (data as of a date: later submissions are ignored, so a new export doesn't
change old periods), `--reference-date` (consent validity, due follow-ups; default `--cutoff`, else today; must
not be after `--cutoff`), `--resolution month|quarter`, `--min-cell` (default 5, 1 disables), `--suppress-scope diagnoses|all`
(default `diagnoses`, see Suppression),
`--min-medical-days` (default 14), `--delimiter`, `--output`, `--rueckfragen-per-le` (also one
`<output>_rueckfragen_<LE>.csv` per LE, to hand each LE only its own cases).

## Input

- the result JSON of `export-datenkranz` of this module (table `datenkranz`, rows with `useCase` MTB;
  table `failures`), cases keyed by (site, patient, episodeOfCare); the column
  `reasonResearchConsentMissing` (branch `feat/datenkranz-consent-reason`) is read when present, or
- a JSON list of `{"case", "submittedAt", "content", "site"?, "reasonResearchConsentMissing"?}`, one entry
  per submission (KDKs with their own export); cases keyed by `json.dumps([site or submitterId, case], sort_keys=True)`.

Only `metaData.submission.diseaseType` `oncological` or `hereditary` is evaluated (the Oncology schema
covers both). Schema basis: bfarm-schema-dto-model e273825 (oncology 1.0.7, base 1.0.6), the DTOs the DIP
export maps to, and the BfArM KDK schema (MVGenomseq_KDK, main of 2026-08-05).

## Output

| File | Content |
|---|---|
| `<output>.json` | `title`, `period`, `min_cell`, `suppressed`, `reference_date`, `cutoff`, `entities` (`Gesamt` + LEs), `labels`, `rows`, `notes`, `time_series` (no case keys: the Rückfragen are in the CSV files only) |
| `<output>.csv` | LE; Abschnitt; Kategorie; Einheit; Wert; Bezug; Basis; Anteil (%) |
| `<output>_zeitreihe.csv` | LE; Zeitraum; Abschnitt; Kategorie; Einheit; Wert; Bezug; Basis; Anteil (%) |
| `<output>_rueckfragen.csv` | LE; Problem; Fall; Einreichung (tanC); Detail (case keys of all LEs: for the KDK; pass each LE its own lines) |
| `<output>_rueckfragen_<LE>.csv` | the same, one file per LE (`--rueckfragen-per-le`) |

`rows[]`: `{key, section, item, unit, base_label, values: {entity: {value, base, percent}}}` as in the RD
script. `time_series`: `{resolution, periods: ["2025-09", ...], suppressed, rows: [{key, section, item, unit,
base_label, values: {entity: {period: {value, base, percent}}}}]}`, the same rows in the same order.
`unit` is `Anzahl` for counts (value of base), else the unit of a statistic (`Tage`, `Anzahl je Fall`,
`mut/Mb`; value only, `base`/`percent` null). A suppressed value is `"<5"`: it marks a primary (1–4)
or a complementary suppression; the two are deliberately indistinguishable, see Suppression.
`suppressed: {primary, complementary}` counts the suppressed cells of the matrix (top level) and of the
time series (`time_series.suppressed`), as in the RD matrix. Notes and the title contain no count below `--min-cell` and no exact
date (the title gives the months of the first and last initial submission, or the `--from`/`--until` period).

## Conventions

- **Case**: an episode with an initial submission; tests are left out (counted in section Einreichungen).
  Its state is that of the latest non-test submission (ordered by `submittedAt`). `plan` and `molecular`
  are taken from the latest submission that has them; follow-ups (`followUp.followUpOds[]`) and their
  therapies are collected over all submissions (deduplicated).
- **Period**: a case belongs to the month/quarter of its initial submission (`metaData.submission.date`);
  section *Einreichungen* counts each submission in the period of its own date; Rückfragen follow the case,
  failures their `submittedAt`. Every period from `--from` (or the first date) to `--until` (or the last) is
  listed, also those without data.
- **Population**: *sequenced* = `metaData.decisionToInclude` not false and `case.diagnosisOd.libraryType`
  in wgs, wgs_lr, wes, panel. GFK2 outcome, implementation and molecular findings refer to the sequenced
  cases; *with GFK2* = sequenced and a `plan.carePlanOd`.
- **GFK1/GFK2**: the case conferences of the Modellvorhaben, named as in the RD matrix. GFK1 = erste
  Fallkonferenz (Indikation/Einschluss) = `metaData.molecularBoardDecisionDate` (RD: the same field; DIP:
  issue date of the indication care plan). GFK2 = zweite Fallkonferenz (Befundbesprechung, in oncology the
  MTB) = `plan.carePlanOd.molecularBoardDecisionDate` (RD: `plan.carePlanRd.molecularBoardDecisionDate`) **of
  the first submission that carries a plan**. The DIP mapping writes the latest therapy-board care plan at
  the time of each submission (`MTBMappings`: `maxByOption(issuedOn)`), so GFK2 is the latest MTB before
  that submission; an earlier MTB is not visible. Unlike the RD script, which takes the first plan date
  after GFK1, a GFK2 before GFK1 is not skipped but asked back ("GFK2 vor GFK1").
- **Implemented recommendation**: a `followUpOds[].therapies[].reference` equal to the `identifier` of a
  `recommendedSystemicTherapies[]` or `recommendedStudies[]` entry of the case (Technische Spezifikation:
  "Identifier der recommendedSystemicTherapies oder recommendedStudies").
- **Suppression** (`--min-cell` k, default 5): counts 1..k-1 → `<k`; every count whose base is 1..k-1 → `<k`
  (also a 0, it would describe each of these cases); statistics from fewer than k values → `<k`. Not
  suppressed in the Rückfragen. Complementary suppression (also `"<k"`, so a `"<k"` cell may hold k or more), so that no suppressed count can be
  derived: counts form groups that add up to a shown total, (a) a row across the LE columns and Gesamt,
  (b) a partition within a column (the categories of e.g. Geschlecht, ECOG, Tumorentität, evidence level,
  `PARTITIONS` in the script) and its base, (c) a row across the periods of the time series and the whole
  period of the matrix. In every group with exactly one suppressed cell, the smallest other nonzero cell is
  suppressed as well (an LE or a period before Gesamt or the whole period); repeated until no group
  changes. Limits: bases and the counts of overlapping rows (e.g. a category's count reappearing as the
  base of another section) are not part of the groups, and statistics (median etc.) are not additive, so
  the tables are still for the KDK and the LE concerned, not for publication.
- **Suppression scope** (`--suppress-scope`, default `diagnoses`): with `diagnoses` only the diagnosis rows are
  suppressed, every other count and statistic (also the time series, the notes and durations of single cases)
  is exact, also below k. The diagnosis rows (`DIAGNOSIS_SECTIONS`, `DIAGNOSIS_ITEMS`, `is_diagnosis_row()` in
  the script) are the sections `Tumorentität (Hauptdiagnose ICD-10-GM, gruppiert)`, `Tumorentität (sequenzierte
  Fälle)`, `Diagnose: Kodierung` (ICD-O-3 behaviour, topography, germline diagnosis),
  `GFK2 (MTB): Ergebnis nach Tumorentität (sequenzierte Fälle)`, `Tumorstadium (M-Kategorie aus
  tnmClassifications)` and `Grading (Differenzierungsgrad)`, and the rows `Fälle | davon hereditär (diseaseType
  hereditary)`, `Molekulare Befunde (sequenzierte Fälle) | Keimbahnvariante berichtet (genomicSource germline)`
  and, since they are the cases minus a diagnosis category, the fill rates `case.diagnosisOd.grading`,
  `case.diagnosisOd.tnmClassifications` and `TNM oder additionalClassification` of `Füllgrad optionaler Felder
  (Fälle)`. Not diagnosis rows: ECOG, the other molecular findings and fill rates, follow-up. The rules above
  apply within the diagnosis rows, plus, since the other rows are now exact: `DIAGNOSIS_PARTITIONS` (the
  behaviour codes with the cases with one; the per-entity rows "GFK2 dokumentiert" and "Therapie- oder
  Studienempfehlung", whose sums are shown exactly in `GFK2 (MTB): Ergebnis`) and `DIAGNOSIS_KEY_PARTITIONS`
  (fill rate of grading + grading `nicht angegeben`, fill rate of TNM + `kein TNM`, each = the exact cases) are
  groups too; the stage and grading categories are partitions of the exact cases; where the other cells of a
  group with one suppressed cell are zeros, a zero (with a nonzero base) is suppressed, so `<k` can also be 0;
  rows that are equal when another count is 0 (`DIAGNOSIS_LINKED`: `TNM oder additionalClassification` and the
  TNM fill rate) are suppressed together; and a base that is the count of another diagnosis row
  (`DIAGNOSIS_BASES`, e.g. `sequenzierte Fälle <entity>`) is suppressed where that count is. `--suppress-scope all` suppresses every row; its output equals that of
  the script before the option (only the JSON field `suppress_scope` is new).
- Robustness: `null` where a list or object is expected is read as empty; a case that still can't be read
  (no `metaData.molecularBoardDecisionDate`, no `birthDate`, no `case.diagnosisOd`, no dated initial)
  becomes a Rückfrage "Fall nicht auswertbar" instead of aborting the run. Each submission's dates are
  checked against its own date. A non-convertible later submission of an evaluated case is marked
  ("Episode mit älterem Stand ausgewertet").

## Sections and metrics

Base "Fälle" = all cases of the entity and period unless stated otherwise.

| Abschnitt | Kategorien (count of …) | Datenkranz path, values |
|---|---|---|
| Fälle | total; hereditary; not included; included, not sequenced; sequenced; with ≥1 followup / addition / correction; non-convertible submissions; thereof evaluated with older state; episodes without initial; unreadable cases | `metaData.submission.diseaseType`, `metaData.decisionToInclude`, `case.diagnosisOd.libraryType`, `metaData.submission.type` |
| Einreichungen (nach Einreichungsdatum) | submissions per type (initial, followup, addition, correction, test, other); total without test; follow-ups with death date / lastContactDate / therapy start; initial submissions with systemic therapy recommendation, per libraryType, per broad-consent state | `metaData.submission.type/date`, `followUp.followUpOds[].deathDate/lastContactDate/therapies[].therapyStartDate`, `plan.recommendedSystemicTherapies` |
| Einwilligung | MV consent sequencing (latest `mvConsent.scope[]` of domain `mvSequencing` is `permit`); broad consent valid at the reference date | `metaData.mvConsent.scope[].domain/type/date`, `metaData.researchConsents[].scope` |
| Broad Consent (Stand der letzten Einreichung) | transmitted and valid at the latest submission date (Consent.permitsResearchUse of service-base: research provisions 2.16.840.1.113883.3.1937.777.24.5.3.7/.8/.1 permit, start within 5 years, not ended); transmitted, not valid; not transmitted per reason (6 schema values, other, none); reason taken from the DIP column | `researchConsents[].noScopeJustification`, else DIP `reasonResearchConsentMissing` (patient-inability, patient-refusal, consent-not-returned, other-patient-reason, technical-issues, organizational-issues) |
| Geschlecht / Alter bei Einschluss (GFK1) / Versicherung | m, w, d, unbekannt; Pädiatrisch 0-17, 18-39, 40-59, 60-79, 80+ (age at GFK1, birth month as its 1st day); GKV, PKV, andere | `metaData.gender` (male/female/other), `birthDate`, `coverageType` |
| Sequenzierung (eingeschlossene Fälle) | wgs, wgs_lr, wes, panel, none, missing, other | `case.diagnosisOd.libraryType` |
| Tumorentität (all / sequenced) | 18 entity groups + no C/D code + missing (table below) | `case.diagnosisOd.mainDiagnosis.code` (ICD-10-GM) |
| Diagnose: Kodierung | histology with behaviour code; behaviour /0 /1 /2 /3 /6 /9; topography 3-char equal to main diagnosis (base: main diagnosis C00-C76, C80); germline diagnosis confirmed; germlineDiagnoses given (of confirmed) | `histology.code` (ICD-O-3-M), `topography.code` (ICD-O-3-T), `germlineDiagnosisConfirmed`, `germlineDiagnoses` |
| Tumorstadium (M-Kategorie) | M0, M1, MX, TNM without recognisable M, no TNM | `tnmClassifications[].display/text/code` matched by `[cpyra]*M[01X]` (e.g. "cM1a (UICC)"); a heuristic, SNOMED codes themselves are not resolved |
| ECOG bei Einschluss (+ zusammengefasst) | 0-5, unknown, notApplicable, missing, other; concrete value 0-5 | `case.diagnosisOd.ecogPerformanceStatusScore` (DIP default `unknown`) |
| Grading | 0,1,2,3,4,5,X,L,M,H,B,U,T, missing, other | `case.diagnosisOd.grading` |
| Vortherapien | number of prior systemic therapies 0/1/2/≥3; with intention K/P/S/X; with treatmentType O/A/N/I/S | `case.priorProcedures[]` (DIP: guideline therapies with a period) |
| Molekulare Vordiagnostik | per type (genomeShortRead … none); none documented | `case.priorDiagnostics[].type` |
| Dauer Erstdiagnose bis GFK1 | mean, median, Q1, Q3, min, max in days (QUARTILE.INC) | `mainDiagnosis.date` → GFK1 |
| Bearbeitungsdauer medizinisch (GFK1 bis GFK2) | computable; no GFK2 yet; impossible date; statistics | GFK1 → GFK2 |
| Bearbeitungsdauer dokumentarisch (GFK2 bis Einreichung beim KDK) | as above | GFK2 → `submission.date` of the first submission with a plan |
| Dauer GFK2 bis Beginn einer empfohlenen Therapie | computable; statistics | GFK2 → earliest `therapyStartDate` of an implemented recommendation |
| Zeitstrahl: Tage relativ zur GFK1 | `ohne GFK1: Fälle`; per milestone `M: Fälle`, `M: 1. Quartil (Tage)`, `M: Median (Tage)`, `M: 3. Quartil (Tage)` and, for milestones after GFK1, `M: unplausibel (vor GFK1)` | below |
| GFK2 (MTB): Ergebnis (sequenzierte Fälle) | GFK2 documented (of sequenced); of those with GFK2: ≥1 systemic therapy recommendation; ≥1 study recommendation; ≥1 of either; molecularly based (a recommendation with `variants[]`); no target (neither); care-plan flags; otherRecommendations WW/AS/WS/OP/ST/KW/SO | `plan.carePlanOd.*`, `plan.recommendedSystemicTherapies[]`, `plan.recommendedStudies[]` |
| Empfehlungen je Fall | statistics of therapy and study recommendations per case with GFK2 | as above |
| GFK2 (MTB): Ergebnis nach Tumorentität | per entity group: GFK2 documented; therapy or study recommendation | |
| Systemische Therapieempfehlungen (Empfehlungen) | per recommendation: evidence level m1A…m4, undefined, other; evidenceLevelDetails is/iv/Z/Z(FDA)/R; type inLabel/offLabel/compassionateUse/secPreventive/unknown; therapeuticStrategy CH…SO; with variants; priority 1 | `recommendedSystemicTherapies[].evidenceLevel/evidenceLevelDetails/type/therapeuticStrategy/variants/priority` |
| Studienempfehlungen (Empfehlungen) | register NCT/DRKS/Eudra-CT/CTIS/other/yesButStudyIsUnknown; evidence level | `recommendedStudies[].register/evidenceLevel` |
| Umsetzung | of cases with a recommendation: with follow-up; ≥1 recommendation started; the same of those with follow-up; therapy without reference to a recommendation; recommendations started (of all recommendations); termination reason E…U / none and response CR/PR/SD/PD / none of started recommendations | `followUp.followUpOds[].therapies[].reference/therapyStartDate/terminationReasonOBDS/therapyResponse` |
| Molekulare Befunde | molecular transmitted; ≥1 SNV/indel; ≥1 CNV; germline variant reported; TMB given; TMB ≥ 10 mut/Mb; HRD status given; HRD high; statistics of SNV and CNV per case and of TMB | `molecular.smallVariants[]`, `copyNumberVariants[]`, `genomicSource`, `complexBiomarkers[].tmb/hrdHigh` (max TMB if several) |
| Follow-up und Vitalstatus | with followup submission; with follow-up content; deceased; lastContactDate; metachronous tumours | `followUpOds[].vitalStatus/lastContactDate/metachroneDiagnoses` |
| Follow-up: Erstmeldung mindestens N Monate vor dem Stichtag (N = 4, 8, 12, 18, 24) | cases; without / with 1, 2, 3, ≥4 followup submissions; deceased | the intervals of the stakeholders' query document |
| Follow-up nach SOP-Intervall (sequenzierte Fälle mit GFK2), … je Kategorie | follow-up category 1/2/3; state at the reference date (deceased, not yet due, current, overdue), in total and per category; current of due; category 2 beyond 24 months; no contact for 12 months (lost to follow-up) | see *Follow-up after the MTB* below |
| Dauer GFK2 bis erstes Follow-up | computable; only follow-ups before GFK2; mean, median, Q1, Q3, min, max in days | GFK2 → earliest `followUpDate` on or after GFK2 |
| Follow-up: Pflichtfelder nach SOP | per follow-up entry: `followUpDate` readable, `vitalStatus` living/deceased, ECOG 0-5; per follow-up therapy: `therapyStartDate` readable, `substances`; `therapyResponseDate` of therapies with a response | `followUpOds[]`, `followUpOds[].therapies[]` |
| Follow-up: Ansprechen, Allgemeinzustand und Vitalstatus | response documented of started recommendations begun ≥ 8 weeks before the reference date; ECOG 0-5 in the latest follow-up; vital status of the latest follow-up (deceased, living, not given, no follow-up) | `therapyResponse`, `ecogPerformanceStatusScore`, `vitalStatus` |
| Füllgrad optionaler Felder (Fälle, Vortherapien, Therapieempfehlungen, Follow-up-Therapien, SNV/Indel, CNV) | share of entries with the field filled | the optional fields of OncologyCase, Plan, FollowUp, Molecular (see rows) |
| Rückfragen an LE | count per problem (cases; non-convertible: submissions) | below |

### Follow-up after the MTB (follow-up SOP of the network)

The follow-up schedule and mandatory fields as defined by the network's MTB follow-up SOP, as far as the
Datenkranz carries them. Base: the sequenced cases with a GFK2 date (`with_mtb` with
`plan.carePlanOd.molecularBoardDecisionDate`); all at `--reference-date`; follow-ups of all submissions, each once
(`followUpOds[]` deduplicated). Not diagnosis rows: exact with `--suppress-scope diagnoses`.

| Term | Definition |
|---|---|
| Kategorie 1 | an implemented recommendation (a follow-up therapy whose `reference` is a recommendation, as in *Umsetzung*); interval 3 months |
| Kategorie 2 | a therapy or study recommendation, none started; interval 3 months, from 24 months after GFK2 on 12 months (the SOP limits the follow-up of recommendations not started to two years; the case is then followed up for vital status like category 3) |
| Kategorie 3 | no therapy or study recommendation (no target); interval 12 months |
| verstorben | `vitalStatus` deceased in any follow-up: no further follow-up due |
| noch nicht fällig | GFK2 less than one interval before the reference date |
| fällig, aktuell | due, and a `followUpDate` in the interval before the reference date (after reference − interval, up to the reference date) |
| fällig, überfällig | due, and no such `followUpDate` |
| Follow-up aktuell, von fälligen | current / (current + overdue): the share of due follow-ups that are up to date |
| lost to follow-up | not deceased, GFK2 ≥ 12 months before the reference date, and no `followUpDate` or `lastContactDate` in the 12 months before it ("no contact for a year"). `followUpDate` is the date of the documentation, used as the date of contact |
| Ansprechen dokumentiert | `therapyResponse` given, of the started recommendations whose `therapyStartDate` is ≥ 8 weeks before the reference date (first staging 6-8 weeks after the start) |
| Pflichtfelder | the fields the SOP's documentation guide marks as mandatory for a follow-up and that the DTO has: follow-up date, vital status, ECOG, therapy start, substance, response date of a response (`deathDate` of the deceased in *Füllgrad*) |

The time series and the quarterly matrices contain these rows like every other section; by the period of the
initial submission, a recent period has more cases not yet due.

### Zeitstrahl (process timeline relative to GFK1)

Per milestone M the offset date(M) − GFK1 in days (GFK1 = `metaData.molecularBoardDecisionDate`), aggregated per
entity and period like every other section: `M: Fälle` counts the cases with an offset (base: the cases of
the entity and period), the three quartile rows are QUARTILE.INC as in the duration sections, suppressed
(`<k`) below `--min-cell` values. No mean, minimum or maximum: the extremes are single cases. Dates given as a
month (`YYYY-MM`) count as the 15th of that month.

| Milestone M | Date | After GFK1 |
|---|---|---|
| MV-Einwilligung | earliest `metaData.mvConsent.scope[].date` of domain `mvSequencing` (latest submission) | no (may be negative) |
| Erstdiagnose | `case.diagnosisOd.mainDiagnosis.date` | no (negative expected) |
| GFK2 (MTB) | GFK2 as above | yes |
| Erstmeldung beim KDK | `metaData.submission.date` of the initial submission | yes |
| Beginn empfohlene Therapie | earliest `therapyStartDate` of an implemented recommendation (as "Dauer GFK2 bis Beginn einer empfohlenen Therapie") | yes |
| Erstes Follow-up | earliest `followUp.followUpOds[].followUpDate` over all submissions | yes |

For a milestone that must lie after GFK1, an offset < 0 is counted in `M: unplausibel (vor GFK1)`, left out of
`M: Fälle` and of the quartiles, and asked back ("Meilenstein vor GFK1 (Zeitstrahl)"). `ohne GFK1: Fälle`
counts the cases without a readable `metaData.molecularBoardDecisionDate`; they can't be evaluated (they are
also in "Fälle nicht auswertbar") and have no timeline. The count rows take part in the complementary
suppression; the quartiles of a count suppressed only complementarily stay visible (they rest on ≥ k values).

Entity groups (ICD-10-GM, first match): Kopf-Hals C00-C14, C30-C32; Ösophagus/Magen C15-C16;
Kolorektal/Anus C18-C21; Leber/Gallenwege C22-C24; Pankreas C25; Lunge C33-C34; Knochen/Weichteile/
Mesotheliom C40-C41, C45-C49; Melanom C43; Mamma C50; Gynäkologisch C51-C58; Prostata C61; Niere/Harnwege
C64-C68; ZNS/Auge C69-C72; Schilddrüse/endokrin C73-C75; CUP/ungenau/sekundär C76-C80; Hämatologisch
C81-C96, D45-D47; sonstige bösartige (other C); in situ D00-D09; gutartig/unsicheres Verhalten (other D).

### Rückfragen (to clarify with the LE)

| Problem | Definition |
|---|---|
| GFK1 nach dem Einreichungsdatum | any submission whose `metaData.molecularBoardDecisionDate` is after its own `submission.date` |
| GFK2 (MTB) nach dem Einreichungsdatum | GFK2 after the date of the first submission with a plan |
| GFK2 vor GFK1 | GFK2 before GFK1 |
| Medizinische Bearbeitungsdauer unplausibel kurz | GFK1 → GFK2 < `--min-medical-days` |
| Erstdiagnose nach GFK1 (QS 7) | `mainDiagnosis.date` > GFK1 |
| Datum vor dem Geburtsmonat (QS 6) | GFK1, diagnosis, GFK2, follow-up, therapy start or prior therapy start before `birthDate`-01 |
| GFK1 vor der MV-Einwilligung (QS 9) | GFK1 before the date of the latest `mvSequencing` scope |
| Alter unplausibel (QS 1) | age at GFK1 outside 0-130 |
| Beginn einer empfohlenen Therapie vor GFK2 | earliest start of an implemented recommendation before GFK2 |
| Vitalstatus und Sterbedatum widersprüchlich | deceased without any `deathDate`, or a `deathDate` without deceased |
| studyRecommended widerspricht recommendedStudies | flag and list disagree (the spec asks for `yesButStudyIsUnknown` when no study is known) |
| TNM nicht genau drei Komponenten (QS 5) | `tnmClassifications` given with ≠ 3 entries |
| decisionToInclude = false, aber sequenziert | not included, libraryType ≠ none |
| genomicDataCenterId fehlt trotz Sequenzierung | QS part A b |
| noScopeJustification nicht mehr zulässig (QS 12) | technical/organisational reason in a submission dated from 2026-06-01 |
| Meilenstein vor GFK1 (Zeitstrahl) | GFK2, initial submission, start of an implemented recommendation or first follow-up before GFK1 (overlaps with "GFK1 nach dem Einreichungsdatum" and "GFK2 vor GFK1") |
| Einreichung nicht konvertierbar / Episode ohne Erstmeldung / Fall nicht auswertbar | as in the RD script |

## Mapping table (Datenkranz field → DTO → used for)

| Datenkranz path | DTO (bfarm-schema-dto-model) | Code values used | Used in |
|---|---|---|---|
| `metaData.submission.{date,type,diseaseType,submitterId,genomicDataCenterId}` | `base/Metadata.Submission` | type initial/followup/addition/correction/test; diseaseType oncological (+ hereditary of the BfArM schema) | cases, periods, LE, Einreichungen |
| `metaData.{gender,birthDate,coverageType,decisionToInclude,molecularBoardDecisionDate}` | `base/Metadata` | gender male/female/other; coverageType GKV/PKV/… | demographics, GFK1 |
| `metaData.mvConsent.scope[].{date,domain,type}` | `Metadata.MVConsent.Scope` | domain mvSequencing; type permit/deny | Einwilligung, QS 9 |
| `metaData.researchConsents[].{scope,noScopeJustification,presentationDate}` | `Metadata.ResearchConsent` | 6 NoScopeJustification values | Broad Consent |
| `case.diagnosisOd.mainDiagnosis.{code,date}` | `OncologyCase.Diagnosis.mainDiagnosis: CodingWithDate[ICD10GM]` | ICD-10-GM | entity, durations |
| `case.diagnosisOd.{histology,topography}.code` | `Coding[ICDO3.M]`, `Coding[ICDO3.T]` | ICD-O-3 | Diagnose: Kodierung |
| `case.diagnosisOd.ecogPerformanceStatusScore` | `Code[ECOG.Value]` | 0-5, unknown, notApplicable | ECOG |
| `case.diagnosisOd.grading` | `Option[Code[OBDSGrading.Value]]` | 0-5, X, L, M, H, B, U, T | Grading |
| `case.diagnosisOd.tnmClassifications[]`, `additionalClassification[]` | `Set[Coding[TNM.Systems]]`, `List[KeyCoding]` | SNOMED CT (display e.g. "cM1a (UICC)") | Tumorstadium, QS 5, fill rate |
| `case.diagnosisOd.{germlineDiagnosisConfirmed,germlineDiagnoses,additionalDiagnoses,libraryType}` | `OncologyCase.Diagnosis` | libraryType panel/wes/wgs/wgs_lr/none | population, germline |
| `case.priorDiagnostics[].type` | `PriorDiagnostics.type: DiagnosticType` | array, single, karyotyping, panel, exome, genomeShortRead, genomeLongRead, other, none | Vordiagnostik |
| `case.priorProcedures[].{treatmentType,intention,therapyStartDate,therapyEndDate,substances,terminationReasonOBDS,therapyResponse}` | `PriorTherapy` | treatmentType O/A/N/I/S; intention K/P/S/X; TerminationReason E,R,W,A,P,S,V,T,U; RECIST PD/SD/PR/CR | Vortherapien |
| `plan.carePlanOd.{molecularBoardDecisionDate,studyRecommended,counsellingRecommended,reEvaluationRecommended,interventionRecommended,otherRecommendations}` | `OncologyPlan.CarePlan` | otherRecommendations WW, AS, WS, OP, ST, KW, SO | GFK2 (MTB): Ergebnis, GFK2 date |
| `plan.recommendedSystemicTherapies[].{identifier,type,evidenceLevel,evidenceLevelDetails,priority,variants,therapeuticStrategy}` | `SystemicTherapyRecommendation` | type inLabel/offLabel/compassionateUse/secPreventive/unknown; evidence m1A…m4, undefined; details is/iv/Z/Z(FDA)/R; strategy CH, HO, IM, ZS, CI, CZ, CIZ, IZ, SZ, SO | recommendations |
| `plan.recommendedStudies[].{identifier,register,evidenceLevel}` | `StudyRecommendation` | register NCT, DRKS, Eudra-CT/CTIS, other (BfArM also yesButStudyIsUnknown) | Studienempfehlungen |
| `molecular.smallVariants[]`, `copyNumberVariants[]`, `complexBiomarkers[].{tmb,hrdHigh,lstHigh,taiHigh,ploidy}` | `OncologyMolecular` | genomicSource somatic/germline | Molekulare Befunde |
| `followUp.followUpOds[].{followUpDate,vitalStatus,deathDate,lastContactDate,metachroneDiagnoses,therapies[]}` | `FollowUp` | vitalStatus living/deceased | Follow-up, Umsetzung |
| `followUp.followUpOds[].therapies[].{reference,therapyStartDate,therapyEndDate,terminationReasonOBDS,therapyResponse,therapyResponseDate,substances}` | `FollowUp.Therapy` | RECIST CR/PR/SD/PD | Umsetzung |

## Not computable from the Datenkranz (as the DIP exports it)

| Wanted | Why not |
|---|---|
| MSI status | no field in the KDK schema (ComplexBiomarker has ploidy, tmb, hrdHigh, lstHigh, taiHigh only) |
| Tumour cell content | only in the GRZ schema (`tumorCellCount`), not in the KDK Datenkranz |
| Tumour/normal set-up (tumor-only, tumor+germline) | only in the GRZ schema (`genomicStudySubtype`); the DIP mapping writes `genomicSource` somatic for every variant, so germline findings in `molecular` are never reported |
| Fusions / structural variants, expression, SBS signatures | in the BfArM schema (`structuralVariants`, `expressionVariants`, `sbsSignatures`) but not in the DTO `OncologyMolecular` ("not represented/relevant in MTB Core Data Set"); counts would always be 0 |
| Ploidy, LST, TAI | DIP: ploidy always empty; lstHigh and taiHigh are copies of hrdHigh. Reported as fill rates only |
| Reasons for not implementing a recommendation | no field; `terminationReasonOBDS` exists only for started therapies, and the DIP maps only therapies with a period |
| Actual trial enrolment | only the recommendation (`recommendedStudies`); a follow-up therapy referencing a study recommendation is counted as started |
| UICC stage group, lines of therapy | not in the schema: stage only as TNM components (M category derived heuristically), prior therapies as a list (counted, not lines) |
| Operations recommended (`interventionRecommended`, `suitableInterventions`) | DIP writes false / nothing ("Not in MTB-KDS") |
| `preventiveMeasures`, `hpoTerms`, `phenotypes`, `diagnosticAssessment` (hereditary), `therapyResponseSource`, `priorDiagnostics[].simpleVariants` | in the BfArM schema, not in the DTOs (or always empty) |
| Follow-up SOP: cost coverage (application, approval) | no field in the Datenkranz (KDK schema / DTO); category 1 of the SOP is therefore approximated by "a recommendation started" |
| Follow-up SOP: therapy status (not done, on-going, stopped, completed), dose density, partial/complete implementation | `FollowUp.Therapy` has only start, end, termination reason, substances, response; the DIP maps only therapies with a period |
| Follow-up SOP: reason a recommended therapy was not started, actual study enrolment | not in the DTO (see above) |
| Follow-up SOP: staging dates and the first response 6-8 weeks after the start, assessment method (RECIST/RANO), radiological validation | the DIP writes only the latest response per therapy (`therapyResponseDate`, `therapyResponse`), no method or validation flag |
| Follow-up SOP: ECOG at each follow-up, non-MTB therapies after the MTB in detail | the Datenkranz holds one follow-up per submission (the DIP writes the latest), so only the ECOG of the follow-ups transmitted; non-MTB therapies only as "Therapie ohne Bezug zu einer Empfehlung" |
| Lost to follow-up | DIP sends no `followUp` at all when the latest follow-up is "lost to follow-up"; `lastContactDate` is filled only from an earlier such follow-up. The rows on `lastContactDate` are therefore labelled "früher lost to follow-up, Kontakt wieder aufgenommen": they see only patients lost earlier whose follow-up resumed, not those currently lost |
| `noScopeJustification` | the DIP mapping never fills it (`researchConsents: []` when a reason is given); the reason comes from the export column `reasonResearchConsentMissing` |
| Which parameters corrections change (stakeholder wish) | possible by diffing consecutive submissions, not implemented |

Caveats of the DIP mapping that affect the rates: `carePlanOd.molecularBoardDecisionDate` is the date of the
*latest* therapy-board care plan at conversion time, so GFK2 is the first MTB only if the first submission
with a plan was sent before a second MTB. Without `CarePlan.boardType`, the DIP selects care plans that carry
some recommendation; an MTB without any recommendation (no target) may then produce no `plan` at all, so
"ohne Therapie- und Studienempfehlung" undercounts and "GFK2 dokumentiert" is lower than the true MTB rate.
Therapies not based on a recommendation get a new random `reference` with every conversion; the script
tells them apart by start date and substances.

## Stakeholder query document (Daten Qualitätsabfragen und Statistik V4sb)

- *Statistik_monthly*, column MTB: submissions total / initial / correction / addition / follow-up, follow-ups
  with death date, lost to follow-up, therapy start, initial submissions per consent state and per library
  type, with therapy recommendation → section *Einreichungen*, monthly in the time series. "Anzahl s. initial
  no sequencing" = libraryType none.
- *Statistik FU detail*: initial submissions ≥ 4/8/12/18/24 months ago with 0/1/2/3/≥4 follow-ups and deceased
  → sections *Follow-up: Erstmeldung mindestens N Monate vor dem Stichtag* (relative to `--reference-date`).
- *Datenqualität (Oncology)*: ECOG (concrete vs unknown/NA), grading, topography, TNM T/N/M given,
  additionalClassification, libraryType, prior diagnostics type, prior procedures (type, intention, substances,
  termination reason, response), molecular fields, complex biomarkers, evidence level and details, strategy,
  study register, follow-up fields and follow-up therapies → the distribution and fill-rate sections.
  Not covered: `diagnosticAssessment`, preventive measures, follow-up phenotypes, structural variant
  description (not in the DTOs, see above); correctness of ICD-O-3/TNM codes (needs the code systems; the
  KDK QC checks them).

## Open questions for the BfArM

1. QS 7 (dates relative to the submission type): which date bounds a follow-up's therapy and response dates,
   and may GFK2 (`carePlanOd.molecularBoardDecisionDate`) of an initial submission be later than
   `metaData.molecularBoardDecisionDate`?
2. Definition of the "Therapieempfehlungsrate": per sequenced case, per case with GFK2, systemic therapies only
   or also studies; does a recommendation without `variants[]` count as molecularly informed?
3. Implementation rate: may `followUpOds[].therapies[].reference` point to a study recommendation (the
   Technische Spezifikation says so, the schema description names only systemic therapies)?
4. Are MSI and tumour cell content meant to be part of the KDK Datenkranz? Without them, MSI-based and
   purity-related analyses are impossible at the KDK.
5. TNM: should the KDK report a stage group, and which SNOMED CT value set identifies T, N and M?
6. Follow-up intervals for oncology (4/8/12/18/24 months in the stakeholder document) and the expected
   follow-up frequency per case.
7. How should "lost to follow-up" be transmitted (lastContactDate alone, vitalStatus unknown)?
8. Should hereditary tumour predisposition (`diseaseType` hereditary, `germlineDiagnosisConfirmed`) be
   reported separately, and is `diagnosticAssessment` expected for them?
9. Reasons for not implementing a recommendation: planned in the schema?
10. TMB cut-off (10 mut/Mb used here) and HRD definition for the reports.
