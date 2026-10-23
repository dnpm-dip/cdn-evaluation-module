package de.dnpm.ccdn.evaluation

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.collect.{BackupDocument, BackupFilter, BackupSource, DocumentType}
import de.dnpm.ccdn.evaluation.model.{Case, SubmissionDocument}
import de.dnpm.dip.coding.Coding
import de.dnpm.dip.model.{HealthInsurance, Id, Site}
import de.dnpm.dip.service.mvh.{Consent, Submission, UseCase}
import de.dnpm.dip.service.mvh.Submission.Report
import play.api.libs.json.{JsObject, JsString, Json}


object Fixtures
{

  def report(
    tan: String,
    site: String = "UKT",
    useCase: UseCase.Value = UseCase.MTB,
    createdAt: LocalDateTime = LocalDateTime.of(2026, 2, 3, 10, 0),
    patient: String = "patient-1",
    submissionType: Submission.Type.Value = Submission.Type.Initial
  ): Report =
    Report(
      id                           = Id(tan),
      createdAt                    = createdAt,
      patient                      = Id(patient),
      episodeOfCare                = None,
      status                       = Report.Status.Unsubmitted,
      site                         = Coding[Site](site),
      useCase                      = useCase,
      `type`                       = submissionType,
      sequencingType               = None,
      diagnosticExtent             = None,
      sequenceTypes                = None,
      healthInsuranceType          = HealthInsurance.Type.GKV,
      consentStatus                = Some(Map(Consent.Category.ModelProject -> true)),
      consentRevocation            = None,
      reasonResearchConsentMissing = None
    )

  def mtbDiagnosis(id: String, icd10: String): JsObject =
    Json.obj(
      "id"         -> id,
      "patient"    -> Json.obj("id" -> "patient-1", "type" -> "Patient"),
      "recordedOn" -> "2025-12-01",
      "type"       -> Json.obj("history" -> Json.arr(Json.obj("value" -> Json.obj("code" -> "main"), "date" -> "2025-12-01"))),
      "code"       -> Json.obj("code" -> icd10, "system" -> "http://fhir.de/CodeSystem/bfarm/icd-10-gm", "version" -> "2025"),
      "topography" -> Json.obj("code" -> "C34.1", "system" -> "urn:oid:2.16.840.1.113883.6.43.1", "version" -> "Erste Revision")
    )

  /** Only the parts of a MTB submission the statistics read */
  def mtbSubmission(
    tan: String,
    patient: String = "patient-1",
    icd10: Seq[String] = Seq("C34.1"),
    submissionType: Submission.Type.Value = Submission.Type.Initial
  ): JsObject =
    Json.obj(
      "patient"     -> Json.obj("id" -> patient),
      "diagnoses"   -> icd10.zipWithIndex.map { case (code, i) => mtbDiagnosis(s"diagnosis-$i", code) },
      "metadata"    -> Json.obj("type" -> submissionType, "transferTAN" -> tan),
      "submittedAt" -> "2026-02-03T09:00:00"
    )

  def caseOf(report: Report, submission: Option[JsObject] = None): Case =
    Case(
      tan         = report.id.value,
      site        = report.site.code.value,
      useCase     = report.useCase,
      submittedAt = Some(report.createdAt),
      report      = Some(report),
      submission  = submission.map(SubmissionDocument),
      deletions   = Seq.empty
    )

  /**
   * Backup documents as the central-data-node stores them, reports with one of its own
   * processing statuses (the status of reports backed up so far)
   */
  def reportDocument(r: Report, status: String = "submissionbackedup"): BackupDocument =
    BackupDocument(r.id.value, r.site.code.value, r.useCase.toString, DocumentType.Report,
      BackupFilter.format(r.createdAt), TestCrypto.encrypt(Json.toJsObject(r) + ("status" -> JsString(status))))

  def submissionDocument(r: Report, submission: JsObject): BackupDocument =
    BackupDocument(r.id.value, r.site.code.value, r.useCase.toString, DocumentType.Submission,
      BackupFilter.format(r.createdAt), TestCrypto.encrypt(submission))

  /** Ignores the filter, but returns only the requested types, ordered like the MongoDB source */
  final class InMemorySource(docs: Seq[BackupDocument]) extends BackupSource
  {
    override def documents(filter: BackupFilter, types: Set[DocumentType.Value]): Iterator[BackupDocument] =
      docs.filter(d => types.contains(d.documentType))
        .sortBy(d => (d.tan, d.documentType.toString, d.site, d.useCase))
        .iterator

    override def close(): Unit = ()
  }
}
