package de.dnpm.ccdn.evaluation.collect

import java.time.LocalDateTime
import java.time.format.DateTimeFormatter.ISO_LOCAL_DATE_TIME
import de.dnpm.dip.service.mvh.UseCase


/**
 * Types of documents in the "backup" collection of the central-data-node, stored as
 * the value's name in field "type"
 */
object DocumentType extends Enumeration
{
  val Report: Value         = Value("report")
  val Submission: Value     = Value("submission")
  /** Deletion event fetched from a DIP node; its encrypted content only repeats the plain fields */
  val Deletion: Value       = Value("deletion")
  /** Deletion notice inserted by backup-delete.sh of the deployment; its content is a plain comment */
  val ManualDeletion: Value = Value("manual-deletion")
}


/**
 * A document of the "backup" collection as stored: "tan", "site", "usecase", "type" and
 * "submittedAt" in plain text, and the (in general) encrypted "content".
 * For reports and submissions, "submittedAt" is the creation time of the report,
 * for deletions the time of deletion.
 */
final case class BackupDocument(
  tan: String,
  site: String,
  useCase: String,
  documentType: DocumentType.Value,
  submittedAt: String,
  content: BackupDocument.Content
)
{
  /** Identifies the document in log messages, without revealing any of its content */
  override def toString: String =
    s"$documentType $tan ($site/$useCase, submitted $submittedAt)"
}

object BackupDocument
{
  sealed trait Content

  /**
   * Content encrypted by the central-data-node's RsaHybridEncryptor, all binary fields base64-encoded.
   * A ciphertext stored in parts (collection "largeBackupParts") is already reassembled.
   */
  final case class Encrypted(
    algorithm: String,
    encryptedKey: String,
    iv: String,
    ciphertext: String
  ) extends Content

  /** Unencrypted content, i.e. the comment of a manual deletion notice */
  final case class Plain(text: String) extends Content

  /** Content that can't be read as stored, e.g. a missing ciphertext part */
  final case class Invalid(problem: String) extends Content
}


/**
 * Filter on the plain fields of backup documents.
 *
 * @param sites    site codes to match, empty matches all sites
 * @param useCases use cases to match (all by default), empty matches nothing
 * @param tans     transfer TANs to match, empty matches all
 * @param from     "submittedAt" at or after this point in time
 * @param until    "submittedAt" strictly before this point in time
 */
final case class BackupFilter(
  sites: Set[String] = Set.empty,
  useCases: Set[UseCase.Value] = UseCase.values,
  tans: Set[String] = Set.empty,
  from: Option[LocalDateTime] = None,
  until: Option[LocalDateTime] = None
)
{
  /** Narrows the use cases to `allowed`, e.g. for statistics that only apply to one use case */
  def restrictUseCases(allowed: Set[UseCase.Value]): BackupFilter =
    copy(useCases = useCases intersect allowed)

  /** True if no document can match, e.g. after restricting to disjoint use cases */
  def isUnsatisfiable: Boolean =
    useCases.isEmpty || from.zip(until).exists { case (f, u) => !f.isBefore(u) }

  override def toString: String =
    (
      Seq(s"use cases ${useCases.mkString(",")}") ++
      Option.when(sites.nonEmpty)(s"sites ${sites.toSeq.sorted.mkString(",")}") ++
      Option.when(tans.nonEmpty)(s"${tans.size} TAN(s)") ++
      from.map(f => s"from ${BackupFilter.format(f)}") ++
      until.map(u => s"until ${BackupFilter.format(u)}")
    )
    .mkString("[", "; ", "]")
}

object BackupFilter
{
  /**
   * "submittedAt" is stored as ISO local date time string (LocalDateTime.format(ISO_LOCAL_DATE_TIME)),
   * so formatted the same way, lexicographic comparison of the strings is chronological.
   */
  def format(dateTime: LocalDateTime): String =
    dateTime.format(ISO_LOCAL_DATE_TIME)
}
