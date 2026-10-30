package de.dnpm.ccdn.evaluation.collect

import java.time.LocalDateTime
import scala.util.Try
import de.dnpm.ccdn.evaluation.model.{BackedUpReport, Case, CaseSubmission, Deletion, EpisodeOfCare, SubmissionDocument}
import de.dnpm.dip.service.mvh.MVHService.DeletionEvent
import de.dnpm.dip.service.mvh.UseCase
import de.dnpm.dip.util.Logging
import play.api.libs.json.{JsError, JsObject, Json}


/**
 * Counts of the data collection, to be reported along with a statistic, so that its
 * completeness can be judged
 *
 * @param documents       number of documents read, by type (reports are always read, since they
 *                        define the cases; submissions read only to determine the episode of care
 *                        of older reports are not counted)
 * @param cases           number of cases handed over to the aggregation
 * @param skippedCases    number of cases left out, since one of their submissions couldn't be read
 * @param failedDocuments number of documents that couldn't be read (decrypted, parsed); an unreadable
 *                        report or deletion is left out on its own, since its case is unknown
 */
final case class CollectionSummary(
  documents: Map[DocumentType.Value, Int],
  cases: Int,
  skippedCases: Int,
  failedDocuments: Int
)


final class WrongKeyException(msg: String) extends Exception(msg)


/**
 * The common data collection step of all statistics: reads the backup documents matching a filter,
 * decrypts and parses them, and groups them into [[Case]]s.
 */
final class DataCollector(
  source: BackupSource,
  decryptor: RsaHybridDecryptor
)
{
  /**
   * The reports (and deletions) are read once the cases are first accessed, the submissions
   * case by case while they are consumed. The summary is complete once the cases are exhausted.
   */
  def collect(filter: BackupFilter, types: Set[DocumentType.Value]): Collection =
    new Collection(source, filter, types, decryptor)
}


object Collection
{
  /**
   * If this many decryptions fail because of the key, without any succeeding before,
   * the wrong private key was most likely given, so collection is aborted
   */
  private[evaluation] val KeyMismatchLimit = 3

  /** Number of TANs per query for the submissions of reports without episode of care */
  private val TansPerQuery = 100

  private val DeletionTypes = Set(DocumentType.Deletion, DocumentType.ManualDeletion)

  /** A readable report or deletion */
  private sealed trait Part
  private final case class ReportPart(site: String, useCase: UseCase.Value, submission: CaseSubmission) extends Part
  private final case class EventPart(site: String, useCase: UseCase.Value, patient: String, deletion: Deletion) extends Part
  private final case class ManualPart(site: String, useCase: UseCase.Value, deletion: Deletion) extends Part

  private final case class PatientKey(site: String, useCase: UseCase.Value, patient: String)
}


final class Collection private[collect] (
  source: BackupSource,
  filter: BackupFilter,
  types: Set[DocumentType.Value],
  decryptor: RsaHybridDecryptor
) extends Logging
{
  import Collection._

  private var documentCounts   = Map.empty[DocumentType.Value, Int]
  private var caseCount        = 0
  private var skippedCaseCount = 0
  private var failedDocuments  = 0
  private var decrypted        = 0
  private var keyMismatches    = 0

  def summary: CollectionSummary =
    CollectionSummary(documentCounts, caseCount, skippedCaseCount, failedDocuments)

  /**
   * Ordered by site, use case, patient and episode of care. Each case is read at most once,
   * so the iterator can be traversed only once.
   */
  lazy val cases: Iterator[Case] =
    if (types.contains(DocumentType.Submission)) selectedCases.iterator.flatMap(withSubmissions)
    else selectedCases.iterator.map { c => caseCount += 1; c }

  private def countDocument(doc: BackupDocument): Unit =
    documentCounts = documentCounts.updatedWith(doc.documentType)(n => Some(n.getOrElse(0) + 1))

  /**
   * The cases in the filter's time frame, without submissions: built from all reports (and
   * deletions) of the filter's sites and use cases, which are small enough to be held in memory
   */
  private def selectedCases: Seq[Case] = {
    val parts =
      source.documents(filter, Set(DocumentType.Report) ++ (types intersect DeletionTypes))
        .flatMap { doc =>
          countDocument(doc)
          toPart(doc).left.map { problem =>
            failedDocuments += 1
            log.error(s"Leaving out $doc: $problem")
          }
          .toOption
        }
        .toSeq

    val reports = parts.collect { case p: ReportPart => p }
    val events  = parts.collect { case EventPart(site, useCase, patient, d) => PatientKey(site, useCase, patient) -> d }.groupMap(_._1)(_._2)
    val derived = derivedEpisodes(reports.filter(_.submission.report.episodeOfCare.isEmpty))

    val episodeCases =
      reports
        .groupMap { r =>
          val report = r.submission.report
          (PatientKey(r.site, r.useCase, report.patient.value), report.episodeOfCare.map(_.value).orElse(derived.get(r.submission.tan)))
        }(_.submission)
        .map { case ((patient, episode), submissions) =>
          Case(patient.site, patient.useCase, Some(patient.patient), episode,
            submissions.sortBy(_.submittedAt), events.getOrElse(patient, Seq.empty).sortBy(_.at))
        }

    val patientsWithCases = episodeCases.map(c => PatientKey(c.site, c.useCase, c.patient.get)).toSet

    val deletionCases =
      events.collect { case (patient, deletions) if !patientsWithCases(patient) =>
        Case(patient.site, patient.useCase, Some(patient.patient), None, Seq.empty, deletions.sortBy(_.at))
      } ++
      parts.collect { case ManualPart(site, useCase, d) => Case(site, useCase, None, None, Seq.empty, Seq(d)) }

    val noTimeFrame = filter.from.isEmpty && filter.until.isEmpty

    (episodeCases ++ deletionCases).toSeq
      .filter(c => noTimeFrame || c.submissions.exists(s => filter.inTimeFrame(s.submittedAt)))
      .sortBy(c => (c.site, c.useCase.toString, c.patient.getOrElse(""), c.episodeOfCare.getOrElse(""), c.deletions.headOption.map(_.tan).getOrElse("")))
  }

  /**
   * Episode of care by TAN, for reports without one, derived from their submissions
   * (see [[EpisodeOfCare.of]]). Missing for reports whose submission is missing or unreadable.
   */
  private def derivedEpisodes(reports: Seq[ReportPart]): Map[String, String] =
    reports.groupBy(r => (r.site, r.useCase)).toSeq.flatMap { case ((site, useCase), rs) =>
      val createdAt = rs.map(r => r.submission.tan -> r.submission.report.createdAt).toMap
      createdAt.keys.toSeq.sorted.grouped(TansPerQuery).flatMap { tans =>
        source.documents(site, useCase.toString, tans.toSet, Set(DocumentType.Submission)).flatMap { doc =>
          submission(doc) match {
            case Right(s) =>
              EpisodeOfCare.of(s, createdAt(doc.tan)).map(doc.tan -> _)
            case Left(problem) =>
              log.warn(s"Episode of care of $doc unknown, since it couldn't be read: $problem")
              None
          }
        }
      }
    }
    .toMap

  /** The case with the submissions of its TANs, None (counted) if one of them can't be read */
  private def withSubmissions(c: Case): Option[Case] = {
    val docs =
      source.documents(c.site, c.useCase.toString, c.submissions.map(_.tan).toSet, Set(DocumentType.Submission)).toList
    docs.foreach(countDocument)
    val read     = docs.map(doc => doc -> submission(doc))
    val problems = read.collect { case (doc, Left(problem)) => doc -> problem }

    if (problems.nonEmpty) {
      skippedCaseCount += 1
      failedDocuments  += problems.size
      problems.foreach { case (doc, problem) => log.error(s"Skipping case of $doc: $problem") }
      None
    }
    else {
      caseCount += 1
      val byTan = read.collect { case (doc, Right(s)) => doc.tan -> s }.toMap
      Some(c.copy(submissions = c.submissions.map(s => s.copy(submission = byTan.get(s.tan)))))
    }
  }

  private def submission(doc: BackupDocument): Either[String, SubmissionDocument] =
    doc.content match {
      case encrypted: BackupDocument.Encrypted => decryptJson(encrypted).map(SubmissionDocument)
      case BackupDocument.Invalid(problem)     => Left(problem)
      case other                               => Left(s"unexpected content ${other.getClass.getSimpleName} for type ${doc.documentType}")
    }

  private def toPart(doc: BackupDocument): Either[String, Part] =
    for {
      submittedAt <- Try(LocalDateTime.parse(doc.submittedAt)).toEither.left.map(_ => s"invalid submittedAt '${doc.submittedAt}'")
      useCase     <- UseCase.values.find(_.toString == doc.useCase).toRight(s"unknown use case '${doc.useCase}'")
      part        <-
        (doc.documentType, doc.content) match {
          case (_, BackupDocument.Invalid(problem)) =>
            Left(problem)
          case (DocumentType.ManualDeletion, BackupDocument.Plain(comment)) =>
            Right(ManualPart(doc.site, useCase, Deletion(Deletion.Kind.Manual, doc.tan, submittedAt, Some(comment))))
          case (DocumentType.Deletion, encrypted: BackupDocument.Encrypted) =>
            // the deletion event: patient, tan and dateTime (= submittedAt)
            decryptJson(encrypted).flatMap(json =>
              json.validate[DeletionEvent].asEither.left.map(errs => s"invalid deletion event: ${JsError.toJson(errs)}")
                .map(event => EventPart(doc.site, useCase, event.patient.value, Deletion(Deletion.Kind.Event, doc.tan, submittedAt, None, Some(json))))
            )
          case (DocumentType.Report, encrypted: BackupDocument.Encrypted) =>
            decryptJson(encrypted).flatMap(json =>
              json.validate(BackedUpReport.reads).asEither.left.map(errs => s"invalid report: ${JsError.toJson(errs)}")
                .map(report => ReportPart(doc.site, useCase, CaseSubmission(doc.tan, submittedAt, report, json, None)))
            )
          case (docType, content) =>
            Left(s"unexpected content ${content.getClass.getSimpleName} for type $docType")
        }
    } yield part

  private def decryptJson(encrypted: BackupDocument.Encrypted): Either[String, JsObject] =
    decryptor.decrypt(encrypted) match {
      case Left(failure) =>
        if (failure.keyMismatch) {
          keyMismatches += 1
          if (decrypted == 0 && keyMismatches >= KeyMismatchLimit)
            throw new WrongKeyException(
              s"The first $keyMismatches documents couldn't be decrypted with the given private key, it probably doesn't belong to the public key used for the backups"
            )
        }
        Left(failure.message)
      case Right(bytes) =>
        decrypted += 1
        Try(Json.parse(bytes)).toEither.left.map(_ => "decrypted content is no valid JSON")
          .flatMap(_.validate[JsObject].asEither.left.map(_ => "decrypted content is no JSON object"))
    }
}
