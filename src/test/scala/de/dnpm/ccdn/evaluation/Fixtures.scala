package de.dnpm.ccdn.evaluation

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.collect.{BackupDocument, BackupFilter, BackupSource, DocumentType}
import de.dnpm.ccdn.evaluation.model.{Case, CaseSubmission, SubmissionDocument}
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
    submissionType: Submission.Type.Value = Submission.Type.Initial,
    episodeOfCare: Option[String] = Some("episode-1")
  ): Report =
    Report(
      id                           = Id(tan),
      createdAt                    = createdAt,
      patient                      = Id(patient),
      episodeOfCare                = episodeOfCare.map(Id(_)),
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
    submissionType: Submission.Type.Value = Submission.Type.Initial,
    episodesOfCare: Seq[(String, String, Option[String])] = Seq.empty  // (id, start, end)
  ): JsObject =
    Json.obj(
      "patient"     -> Json.obj("id" -> patient),
      "episodesOfCare" -> episodesOfCare.map { case (id, start, end) =>
        Json.obj("id" -> id, "period" -> (Json.obj("start" -> start) ++ end.fold(Json.obj())(e => Json.obj("end" -> e))))
      },
      "diagnoses"   -> icd10.zipWithIndex.map { case (code, i) => mtbDiagnosis(s"diagnosis-$i", code) },
      "metadata"    -> Json.obj("type" -> submissionType, "transferTAN" -> tan),
      "submittedAt" -> "2026-02-03T09:00:00"
    )

  /** A case of the first report's site, use case, patient and episode */
  def caseOf(submissions: (Report, Option[JsObject])*): Case = {
    val first = submissions.head._1
    Case(
      site          = first.site.code.value,
      useCase       = first.useCase,
      patient       = Some(first.patient.value),
      episodeOfCare = first.episodeOfCare.map(_.value),
      submissions   = submissions.map { case (r, s) => CaseSubmission(r.id.value, r.createdAt, r, Json.toJsObject(r), s.map(SubmissionDocument)) },
      deletions     = Seq.empty
    )
  }

  def caseOf(report: Report): Case =
    caseOf(report -> None)

  def caseOf(report: Report, submission: JsObject): Case =
    caseOf(report -> Some(submission))

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

  def deletionEvent(tan: String, patient: String, at: LocalDateTime): JsObject =
    Json.obj("patient" -> patient, "tan" -> tan, "dateTime" -> at)

  def deletionDocument(tan: String, patient: String, at: LocalDateTime, site: String = "UKT", useCase: UseCase.Value = UseCase.MTB): BackupDocument =
    BackupDocument(tan, site, useCase.toString, DocumentType.Deletion, BackupFilter.format(at),
      TestCrypto.encrypt(deletionEvent(tan, patient, at)))

  def manualDeletionDocument(tan: String, at: LocalDateTime, comment: String, site: String = "UKT", useCase: UseCase.Value = UseCase.MTB): BackupDocument =
    BackupDocument(tan, site, useCase.toString, DocumentType.ManualDeletion, BackupFilter.format(at), BackupDocument.Plain(comment))

  /** Applies the filter's sites and use cases like the MongoDB source */
  final class InMemorySource(docs: Seq[BackupDocument]) extends BackupSource
  {
    override def documents(filter: BackupFilter, types: Set[DocumentType.Value]): Iterator[BackupDocument] =
      docs.filter(d =>
        types.contains(d.documentType) && filter.useCases.exists(_.toString == d.useCase) &&
        (filter.sites.isEmpty || filter.sites.contains(d.site))
      )
      .iterator

    override def documents(site: String, useCase: String, tans: Set[String], types: Set[DocumentType.Value]): Iterator[BackupDocument] =
      docs.filter(d => types.contains(d.documentType) && d.site == site && d.useCase == useCase && tans.contains(d.tan)).iterator

    override def close(): Unit = ()
  }
}
