package de.dnpm.ccdn.evaluation.statistics

import scala.util.Try
import de.dnpm.bfarm.model.onco.MTBMappings
import de.dnpm.bfarm.model.rd.RDMappings
import de.dnpm.ccdn.evaluation.collect.DocumentType
import de.dnpm.ccdn.evaluation.model.{Case, SubmissionDocument}
import de.dnpm.dip.coding.Coding
import de.dnpm.dip.model.{Patient, Site}
import de.dnpm.dip.service.mvh.UseCase
import play.api.libs.json.{JsError, JsResult, JsValue, Json, Writes}
import Statistic.{Params, Result, Table}


/**
 * All backed up submissions, converted into the BfArM "Datenkranz" schema (of oncology or rare
 * diseases) by the mappings of https://github.com/dnpm-dip/bfarm-schema-dto-model
 */
object ExportDatenkranz extends Statistic
{
  override val name = "export-datenkranz"

  override val description =
    "All backed up submissions, converted into the BfArM \"Datenkranz\" schema (oncology for MTB, rare " +
    "diseases for RD), one row per submission, along with the patient and episode of care of its case. " +
    "Submissions that can't be read or converted are listed with the error instead. Reads the submissions, " +
    "and holds them in memory: restrict large exports with the filters."

  override val documentTypes = Set(DocumentType.Submission)

  /**
   * The submission in the Datenkranz schema of its use case, or the reason it couldn't be converted.
   * The mappings take the site from the patient's managing site, else from the local site of the
   * running DIP node (Site.local), which is undefined here, so the case's site is filled in.
   */
  private def datenkranz(c: Case, submission: SubmissionDocument): Either[String, JsValue] = {
    def convert[T, S: Writes](read: JsResult[T])(mapping: T => S): Either[String, JsValue] =
      read.asEither.left.map(errs => s"unreadable submission: ${JsError.toJson(errs)}")
        .flatMap(s => Try(Json.toJson(mapping(s))).toEither.left.map(t => s"conversion failed: $t"))

    def withSite(patient: Patient): Patient =
      patient.copy(managingSite = patient.managingSite.orElse(Some(Coding[Site](c.site))))

    c.useCase match {
      case UseCase.MTB =>
        convert(submission.mtb.map(s => s.copy(record = s.record.copy(patient = withSite(s.record.patient)))))(MTBMappings.useCaseSubmission)
      case UseCase.RD =>
        convert(submission.rd.map(s => s.copy(record = s.record.copy(patient = withSite(s.record.patient)))))(RDMappings.useCaseSubmission)
    }
  }

  override def aggregate(cases: Iterator[Case], params: Params): Result = {
    val (rows, failures) =
      cases.foldLeft((Vector.empty[Seq[JsValue]], Vector.empty[Seq[JsValue]])) { case ((rows, failures), c) =>
        c.submissions.foldLeft((rows, failures)) { case ((rows, failures), s) =>
          s.submission match {
            case Some(submission) =>
              datenkranz(c, submission) match {
                case Right(content) =>
                  (rows :+ Table.row(c.site, c.useCase.toString, c.patient, c.episodeOfCare, s.tan, s.submittedAt, content), failures)
                case Left(error) =>
                  (rows, failures :+ Table.row(c.site, c.useCase.toString, c.patient, c.episodeOfCare, s.tan, s.submittedAt, error))
              }
            case None =>
              (rows, failures)
          }
        }
      }

    Result(
      tables = Seq(
        Table(
          name    = "datenkranz",
          columns = Seq("site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt", "content"),
          rows    = rows
        ),
        Table(
          name    = "failures",
          columns = Seq("site", "useCase", "patient", "episodeOfCare", "tan", "submittedAt", "error"),
          rows    = failures
        )
      ),
      notes = Seq(
        s"${rows.size} submissions converted, ${failures.size} couldn't be read or converted, see \"failures\".",
        "\"content\" is the submission in the BfArM Datenkranz schema, as the mappings produce it; it isn't validated against the schema.",
        "The submitter and data node IDs come from the mappings' config, set with the environment variable " +
          "CCDN_BFARM_MAPPINGS_CONFIG_FILE, else its defaults; submissions of sites not contained there fail.",
        "Test submissions are contained, see their type in the metadata.",
        "\"submittedAt\" is the creation time of the report; submissions that weren't backed up are not contained."
      )
    )
  }
}
