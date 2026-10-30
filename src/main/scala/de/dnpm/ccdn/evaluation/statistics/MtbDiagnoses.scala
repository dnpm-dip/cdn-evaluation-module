package de.dnpm.ccdn.evaluation.statistics

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.collect.{BackupFilter, DocumentType}
import de.dnpm.ccdn.evaluation.model.Case
import de.dnpm.dip.mtb.model.MTBDiagnosis
import de.dnpm.dip.service.mvh.{Submission, UseCase}
import Statistic.{Parameter, Params, Result, Table}


/**
 * Number of MTB patients per site and ICD-10-GM code of their diagnoses, from each
 * patient's latest submission
 */
object MtbDiagnoses extends Statistic
{
  override val name = "mtb-diagnoses"

  override val description =
    "Number of MTB patients per site and ICD-10-GM diagnosis code (or category), taking the " +
    "diagnoses of each patient's latest submission. Test submissions are left out. Reads the submissions."

  override val documentTypes = Set(DocumentType.Submission)

  override def restrict(filter: BackupFilter): BackupFilter =
    filter.restrictUseCases(Set(UseCase.MTB))

  private val Level = "level"

  override val parameters =
    Seq(
      Parameter(Level, "'category' counts by 3-character category (e.g. C34), 'code' by full code (e.g. C34.1)", "category", Seq("category", "code"))
    )

  /** Patient IDs are only unique within their site */
  private final case class PatientKey(site: String, patient: String)

  private final case class Latest(submittedAt: LocalDateTime, codes: Set[String])

  private final case class State(latest: Map[PatientKey, Latest], unreadable: Int, tests: Int)

  override def aggregate(cases: Iterator[Case], params: Params): Result = {
    val byCategory = params(Level) == "category"

    def code(diagnosis: MTBDiagnosis): String = {
      val c = diagnosis.code.code.value
      if (byCategory) c.takeWhile(_ != '.') else c
    }

    val state =
      cases.flatMap(c => c.submissions.map(c -> _)).foldLeft(State(Map.empty, 0, 0)) { case (state, (c, s)) =>
        s.submission match {
          case Some(submission) =>
            if (submission.submissionType.contains(Submission.Type.Test))
              state.copy(tests = state.tests + 1)
            else {
              // only the parts needed, which tolerates deviations in the rest of the record
              val content =
                for {
                  patient   <- (submission.json \ "patient" \ "id").validate[String]
                  diagnoses <- (submission.json \ "diagnoses").validate[List[MTBDiagnosis]]
                } yield (patient, diagnoses)

              content.asOpt match {
                case Some((patient, diagnoses)) =>
                  val key = PatientKey(c.site, patient)
                  if (state.latest.get(key).exists(_.submittedAt.isAfter(s.submittedAt))) state
                  else state.copy(latest = state.latest.updated(key, Latest(s.submittedAt, diagnoses.map(code).toSet)))
                case None =>
                  state.copy(unreadable = state.unreadable + 1)
              }
            }
          case None => state
        }
      }

    val counts =
      state.latest.toSeq
        .flatMap { case (key, latest) => latest.codes.map(key.site -> _) }
        .groupMapReduce(identity)(_ => 1)(_ + _)

    Result(
      tables = Seq(
        Table(
          name    = "diagnoses",
          columns = Seq("site", if (byCategory) "icd10Category" else "icd10Code", "patients"),
          rows    =
            counts.toSeq.sortBy(_._1).map { case ((site, code), n) => Table.row(site, code, n) }
        )
      ),
      notes =
        Seq(
          s"${state.latest.size} patients counted; a patient with several diagnoses is counted once per distinct code.",
          s"${state.tests} test submissions left out."
        ) ++
        Option.when(state.unreadable > 0)(
          s"${state.unreadable} submissions left out, since their patient ID or diagnoses couldn't be read."
        )
    )
  }
}
