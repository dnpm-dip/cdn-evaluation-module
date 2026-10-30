package de.dnpm.ccdn.evaluation.statistics

import de.dnpm.ccdn.evaluation.collect.DocumentType
import de.dnpm.ccdn.evaluation.model.{Case, Deletion}
import play.api.libs.json.{JsNull, JsString, JsValue, Json}
import Statistic.{Params, Result, Table}


/**
 * All backed up reports, submissions and deletions as stored, with their content decrypted,
 * e.g. for evaluations not (yet) implemented as statistic
 */
object Export extends Statistic
{
  override val name = "export"

  override val description =
    "All backed up reports, submissions and deletions as stored in the central-data-node, with their " +
    "content decrypted, one row per document, along with the patient and episode of care of its case. " +
    "Reads all documents, and holds them in memory: restrict large exports with the filters."

  override val documentTypes = DocumentType.values.toSet

  private final case class DeletionKey(site: String, useCase: String, tan: String, kind: Deletion.Kind.Value)

  override def aggregate(cases: Iterator[Case], params: Params): Result = {
    // a deletion event is attached to all cases of its patient, but exported once
    val (rows, _) =
      cases.foldLeft((Vector.empty[Seq[JsValue]], Set.empty[DeletionKey])) { case ((rows, exported), c) =>
        def row(tan: String, documentType: DocumentType.Value, episode: Option[String], submittedAt: JsValue, content: JsValue) =
          Table.row(c.site, c.useCase.toString, c.patient, episode, tan, documentType.toString, submittedAt, content)

        val submissionRows =
          c.submissions.flatMap { s =>
            row(s.tan, DocumentType.Report, c.episodeOfCare, Json.toJson(s.submittedAt), s.reportJson) +:
            s.submission.toSeq.map(sub => row(s.tan, DocumentType.Submission, c.episodeOfCare, Json.toJson(s.submittedAt), sub.json))
          }

        val deletions =
          c.deletions.filterNot(d => exported(DeletionKey(c.site, c.useCase.toString, d.tan, d.kind)))

        // the episode of a deletion is unknown, see Case
        val deletionRows =
          deletions.map { d =>
            d.kind match {
              case Deletion.Kind.Event  => row(d.tan, DocumentType.Deletion, None, Json.toJson(d.at), d.event.getOrElse(JsNull))
              case Deletion.Kind.Manual => row(d.tan, DocumentType.ManualDeletion, None, Json.toJson(d.at), d.comment.fold[JsValue](JsNull)(JsString))
            }
          }

        (rows ++ submissionRows ++ deletionRows, exported ++ deletions.map(d => DeletionKey(c.site, c.useCase.toString, d.tan, d.kind)))
      }

    Result(
      tables = Seq(
        Table(
          name    = "documents",
          columns = Seq("site", "useCase", "patient", "episodeOfCare", "tan", "type", "submittedAt", "content"),
          rows    = rows
        )
      ),
      notes = Seq(
        "\"submittedAt\" is the creation time of the report for reports and submissions, the time of deletion for deletions.",
        "\"content\" is the decrypted content as stored; reports carry the central-data-node's own processing status.",
        "Deletions have no episode of care, since their report and submission are no longer contained; manual deletions have no patient.",
        "Documents that couldn't be read are not contained, see \"collection\"."
      )
    )
  }
}
