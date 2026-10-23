package de.dnpm.ccdn.evaluation.model

import java.time.LocalDateTime
import de.dnpm.dip.mtb.model.MTBPatientRecord
import de.dnpm.dip.rd.model.RDPatientRecord
import de.dnpm.dip.service.mvh.{Submission, UseCase}
import de.dnpm.dip.service.mvh.Submission.Report
import play.api.libs.json.{JsObject, JsResult, Reads}


/**
 * All backed up documents of one data submission, identified by its transfer TAN, site and use case.
 * This is what the data collection step hands over to the aggregation of a statistic.
 *
 * Only the document types a statistic requests are collected, so e.g. `submission` is always
 * None for statistics that only request reports. The report and the submission of a case are
 * removed from the backup once a deletion of it is received, so a case has either
 * report and submission, or deletions.
 *
 * @param submittedAt "submittedAt" of the report or submission (the creation time of the report),
 *                    None if neither of them is collected
 */
final case class Case(
  tan: String,
  site: String,
  useCase: UseCase.Value,
  submittedAt: Option[LocalDateTime],
  report: Option[Report],
  submission: Option[SubmissionDocument],
  deletions: Seq[Deletion]
)


/**
 * A decrypted submission, kept as raw JSON, since reading it into the typed model (see [[as]])
 * depends on the version of the DIP node that sent it. Typically, statistics read only the parts
 * they need, e.g. `(json \ "diagnoses").validate[List[MTBDiagnosis]]`, which tolerates
 * deviations elsewhere in the record.
 *
 * The JSON is the [[Submission]] as served by the DIP node: the fields of the patient record at the
 * top level, plus "metadata" and "submittedAt".
 */
final case class SubmissionDocument(json: JsObject)
{
  def as[T: Reads]: JsResult[T] =
    json.validate[T]

  def mtb: JsResult[Submission[MTBPatientRecord]] =
    as(Submission.tolerantReads.submission[MTBPatientRecord])

  def rd: JsResult[Submission[RDPatientRecord]] =
    as(Submission.tolerantReads.submission[RDPatientRecord])

  /** The submission's type (initial, followup, ...), if readable */
  def submissionType: Option[Submission.Type.Value] =
    (json \ "metadata" \ "type").asOpt[Submission.Type.Value]
}


/**
 * A deletion of the case's report and submission
 *
 * @param at      time of the deletion
 * @param comment the comment of a manual deletion
 */
final case class Deletion(
  kind: Deletion.Kind.Value,
  at: LocalDateTime,
  comment: Option[String]
)

object Deletion
{
  object Kind extends Enumeration
  {
    /** Deletion event fetched from the DIP node, e.g. after consent revocation */
    val Event: Value  = Value("event")
    /** Deletion notice of a manual deletion via the deployment's backup-delete.sh */
    val Manual: Value = Value("manual")
  }
}
