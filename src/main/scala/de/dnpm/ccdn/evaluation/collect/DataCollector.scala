package de.dnpm.ccdn.evaluation.collect

import java.time.LocalDateTime
import scala.util.Try
import de.dnpm.ccdn.evaluation.model.{BackedUpReport, Case, Deletion, SubmissionDocument}
import de.dnpm.dip.service.mvh.Submission.Report
import de.dnpm.dip.service.mvh.UseCase
import de.dnpm.dip.util.Logging
import play.api.libs.json.{JsError, JsObject, Json}


/**
 * Counts of the data collection, to be reported along with a statistic, so that its
 * completeness can be judged
 *
 * @param documents     number of documents read, by type
 * @param cases         number of cases handed over to the aggregation
 * @param skippedCases  number of cases left out, since one of their documents couldn't be read
 * @param failedDocuments number of documents that couldn't be read (decrypted, parsed)
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
   * The cases are read lazily while the returned iterator is consumed, the summary
   * is complete once the iterator is exhausted.
   */
  def collect(filter: BackupFilter, types: Set[DocumentType.Value]): Collection =
    new Collection(source.documents(filter, types), decryptor)
}


object Collection
{
  /**
   * If this many decryptions fail because of the key, without any succeeding before,
   * the wrong private key was most likely given, so collection is aborted
   */
  private[evaluation] val KeyMismatchLimit = 3

  /** A readable document of a case */
  private sealed trait Part
  private final case class ReportPart(report: Report, submittedAt: LocalDateTime) extends Part
  private final case class SubmissionPart(submission: SubmissionDocument, submittedAt: LocalDateTime) extends Part
  private final case class DeletionPart(deletion: Deletion) extends Part

  /** Groups consecutive elements with the same key, without reading ahead more than one group */
  private[evaluation] def groupConsecutive[A, K](it: Iterator[A])(key: A => K): Iterator[(K, Seq[A])] = {
    val input = it.buffered
    new Iterator[(K, Seq[A])] {
      override def hasNext: Boolean = input.hasNext
      override def next(): (K, Seq[A]) = {
        val k     = key(input.head)
        val group = Seq.newBuilder[A]
        while (input.hasNext && key(input.head) == k) group += input.next()
        (k, group.result())
      }
    }
  }
}


final class Collection private[collect] (
  documents: Iterator[BackupDocument],
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

  /** Each case is read at most once, so the iterator can be traversed only once */
  val cases: Iterator[Case] =
    groupConsecutive(documents)(_.tan)
      .flatMap { case (_, docs) =>
        // backups of the same TAN from different sites or use cases are different cases
        docs.groupBy(d => (d.site, d.useCase)).toSeq.sortBy(_._1).map(_._2)
      }
      .flatMap { docs =>
        docs.foreach(d => documentCounts = documentCounts.updatedWith(d.documentType)(n => Some(n.getOrElse(0) + 1)))
        toCase(docs) match {
          case Right(c) =>
            caseCount += 1
            Some(c)
          case Left(problems) =>
            skippedCaseCount += 1
            failedDocuments  += problems.size
            problems.foreach { case (doc, problem) => log.error(s"Skipping case of $doc: $problem") }
            None
        }
      }

  /** `docs` are non-empty and all of the same TAN, site and use case */
  private def toCase(docs: Seq[BackupDocument]): Either[Seq[(BackupDocument, String)], Case] = {
    val head = docs.head
    val parts = docs.map(doc => toPart(doc).left.map(doc -> _))
    val problems =
      parts.collect { case Left(p) => p } ++
      Option.when(UseCase.values.forall(_.toString != head.useCase))(head -> s"unknown use case '${head.useCase}'")

    if (problems.nonEmpty) Left(problems)
    else {
      val valid = parts.collect { case Right(p) => p }
      val report = valid.collectFirst { case p: ReportPart => p }
      val submission = valid.collectFirst { case p: SubmissionPart => p }
      Right(
        Case(
          tan         = head.tan,
          site        = head.site,
          useCase     = UseCase.withName(head.useCase),
          submittedAt = report.map(_.submittedAt).orElse(submission.map(_.submittedAt)),
          report      = report.map(_.report),
          submission  = submission.map(_.submission),
          deletions   = valid.collect { case DeletionPart(d) => d }.sortBy(_.at)
        )
      )
    }
  }

  private def toPart(doc: BackupDocument): Either[String, Part] =
    Try(LocalDateTime.parse(doc.submittedAt)).toEither.left.map(_ => s"invalid submittedAt '${doc.submittedAt}'")
      .flatMap { submittedAt =>
        (doc.documentType, doc.content) match {
          case (_, BackupDocument.Invalid(problem)) =>
            Left(problem)
          case (DocumentType.ManualDeletion, BackupDocument.Plain(comment)) =>
            Right(DeletionPart(Deletion(Deletion.Kind.Manual, submittedAt, Some(comment))))
          case (DocumentType.Deletion, _: BackupDocument.Encrypted) =>
            // the encrypted deletion event holds nothing beyond the plain fields: patient, tan and dateTime (= submittedAt)
            Right(DeletionPart(Deletion(Deletion.Kind.Event, submittedAt, None)))
          case (DocumentType.Report, encrypted: BackupDocument.Encrypted) =>
            decryptJson(encrypted).flatMap(
              _.validate(BackedUpReport.reads).asEither.left.map(errs => s"invalid report: ${JsError.toJson(errs)}")
            )
            .map(ReportPart(_, submittedAt))
          case (DocumentType.Submission, encrypted: BackupDocument.Encrypted) =>
            decryptJson(encrypted).map(json => SubmissionPart(SubmissionDocument(json), submittedAt))
          case (docType, content) =>
            Left(s"unexpected content ${content.getClass.getSimpleName} for type $docType")
        }
      }

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
