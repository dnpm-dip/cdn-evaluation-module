package de.dnpm.ccdn.evaluation.statistics

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.collect.DocumentType
import de.dnpm.ccdn.evaluation.model.Case
import Statistic.{Parameter, Params, Result, Table}


/**
 * Number of submissions and of distinct patients per site, use case, period and submission type,
 * from the reports alone
 */
object SubmissionCounts extends Statistic
{
  override val name = "submission-counts"

  override val description =
    "Number of backed up submissions and of distinct patients per site, use case, period " +
    "(of the report's creation) and submission type. Reads only the reports."

  override val documentTypes = Set(DocumentType.Report)

  private val Period = "period"

  override val parameters =
    Seq(
      Parameter(Period, "Length of the periods to count in", "quarter", Seq("month", "quarter", "year"))
    )

  private def periodOf(dateTime: LocalDateTime, period: String): String =
    period match {
      case "month"   => f"${dateTime.getYear}-${dateTime.getMonthValue}%02d"
      case "quarter" => s"${dateTime.getYear}-Q${(dateTime.getMonthValue - 1) / 3 + 1}"
      case _         => dateTime.getYear.toString
    }

  private final case class Key(site: String, useCase: String, period: String, submissionType: String)

  /** Patients are identified per site, since their IDs are local to the site */
  private final case class Counts(submissions: Int, patients: Set[String])

  override def aggregate(cases: Iterator[Case], params: Params): Result = {
    val period = params(Period)

    val counts =
      cases.flatMap(c => c.submissions.map(c -> _.report)).foldLeft(Map.empty[Key, Counts]) { case (acc, (c, report)) =>
        val key = Key(c.site, c.useCase.toString, periodOf(report.createdAt, period), report.`type`.toString)
        acc.updatedWith(key) { current =>
          val Counts(n, patients) = current.getOrElse(Counts(0, Set.empty))
          Some(Counts(n + 1, patients + report.patient.value))
        }
      }

    Result(
      tables = Seq(
        Table(
          name    = "submissions",
          columns = Seq("site", "useCase", period, "type", "submissions", "patients"),
          rows    =
            counts.toSeq
              .sortBy { case (k, _) => (k.site, k.useCase, k.period, k.submissionType) }
              .map { case (k, c) => Table.row(k.site, k.useCase, k.period, k.submissionType, c.submissions, c.patients.size) }
        )
      ),
      notes = Seq(
        "Only submissions with Model Project consent are backed up, and deleted ones are no longer contained."
      )
    )
  }
}
