package de.dnpm.ccdn.evaluation.model

import java.time.{LocalDate, LocalDateTime}
import scala.util.Try
import de.dnpm.dip.mtb.model.MTBPatientRecord
import de.dnpm.dip.rd.model.RDPatientRecord
import de.dnpm.dip.service.mvh.{Submission, UseCase}
import de.dnpm.dip.service.mvh.Submission.Report
import play.api.libs.json.{JsObject, JsResult, Reads}


/**
 * All backed up documents of one episode of care of a patient: the submissions with their reports
 * (one report per submission, identified by its transfer TAN), and the patient's deletions.
 * This is what the data collection step hands over to the aggregation of a statistic.
 *
 * Reports are always collected, since they define the cases. Submissions and deletions are only
 * collected if the statistic requests them, so e.g. [[CaseSubmission.submission]] is always None
 * for statistics that only request reports.
 *
 * Deletions remove the report and submission of their TAN from the backup, so their episode
 * is unknown: a deletion event is attached to all cases of its patient, or forms a case of its own
 * (without submissions and episode) if none of the patient's reports are left. A manual deletion
 * notice identifies nothing but its TAN, so it always forms a case of its own, without patient.
 *
 * @param patient       None only for the case of a manual deletion notice
 * @param episodeOfCare ID of the episode of care, as given by the reports, or for older reports
 *                      without it, derived from their submission (see [[EpisodeOfCare.of]]);
 *                      None if not determinable, then grouping all such submissions of the patient
 * @param submissions   ordered by "submittedAt"
 * @param deletions     ordered by time of deletion
 */
final case class Case(
  site: String,
  useCase: UseCase.Value,
  patient: Option[String],
  episodeOfCare: Option[String],
  submissions: Seq[CaseSubmission],
  deletions: Seq[Deletion]
)


/**
 * A submission of a case, identified by its transfer TAN
 *
 * @param submittedAt "submittedAt" of the report and submission (the creation time of the report)
 * @param report      the report, with the central-data-node's processing status mapped (see [[BackedUpReport]])
 * @param reportJson  the report as stored (decrypted), with the central-data-node's own processing status
 * @param submission  None if not requested by the statistic, or not backed up
 */
final case class CaseSubmission(
  tan: String,
  submittedAt: LocalDateTime,
  report: Report,
  reportJson: JsObject,
  submission: Option[SubmissionDocument]
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


object EpisodeOfCare
{
  private final case class Episode(id: String, start: LocalDate, end: Option[LocalDate])

  /**
   * The episode of care a submission pertains to, for reports without "episodeOfCare" (sent by
   * older DIP nodes): the one referenced in the submission's metadata, else, like the DIP node
   * does (BaseMVHService), the one of the patient record in whose period the report was created.
   * If there is none, the latest one started before, else the chronologically first one.
   */
  def of(submission: SubmissionDocument, reportCreatedAt: LocalDateTime): Option[String] =
    (submission.json \ "metadata" \ "episodeOfCare" \ "id").asOpt[String].orElse {
      val date = reportCreatedAt.toLocalDate
      val episodes =
        (submission.json \ "episodesOfCare").asOpt[Seq[JsObject]].getOrElse(Seq.empty).flatMap { eoc =>
          for {
            id    <- (eoc \ "id").asOpt[String]
            start <- (eoc \ "period" \ "start").asOpt[String].flatMap(s => Try(LocalDate.parse(s)).toOption)
          } yield Episode(id, start, (eoc \ "period" \ "end").asOpt[String].flatMap(s => Try(LocalDate.parse(s)).toOption))
        }
        .sortBy(_.start.toEpochDay)

      episodes.find(e => !e.start.isAfter(date) && e.end.forall(!_.isBefore(date)))
        .orElse(episodes.filterNot(_.start.isAfter(date)).lastOption)
        .orElse(episodes.headOption)
        .map(_.id)
    }
}


/**
 * A deletion of a report and submission
 *
 * @param tan     TAN of the deleted report and submission
 * @param at      time of the deletion
 * @param comment the comment of a manual deletion
 * @param event   the deletion event as stored (decrypted): patient, tan and dateTime; None for a manual deletion
 */
final case class Deletion(
  kind: Deletion.Kind.Value,
  tan: String,
  at: LocalDateTime,
  comment: Option[String],
  event: Option[JsObject] = None
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
