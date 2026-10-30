package de.dnpm.ccdn.evaluation

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.collect.BackupFilter
import de.dnpm.ccdn.evaluation.model.Deletion
import de.dnpm.ccdn.evaluation.statistics.{Export, MtbDiagnoses, Statistic, Statistics, SubmissionCounts}
import de.dnpm.dip.service.mvh.{Submission, UseCase}
import org.scalatest.EitherValues
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import play.api.libs.json.{JsValue, Json}


class StatisticsTests extends AnyFlatSpec with Matchers with EitherValues
{
  import Fixtures._

  private def params(statistic: Statistic, values: (String, String)*): Statistic.Params =
    Statistic.Params.validate(statistic, values.toMap).value

  private def rows(result: Statistic.Result): Seq[Seq[JsValue]] =
    result.tables.head.rows

  "The registry" should "contain statistics with unique kebab-case names" in {
    val names = Statistics.all.map(_.name)
    names.distinct shouldBe names
    all (names) should fullyMatch regex "[a-z0-9]+(-[a-z0-9]+)*"
  }

  it should "declare valid defaults for all parameters" in {
    for (s <- Statistics.all; p <- s.parameters if p.allowed.nonEmpty)
      p.allowed should contain (p.default)
  }

  "Params" should "fill in defaults and reject unknown parameters or values" in {
    params(SubmissionCounts).toMap shouldBe Map("period" -> "quarter")
    Statistic.Params.validate(SubmissionCounts, Map("period" -> "week")).left.value should include ("allowed: month, quarter, year")
    Statistic.Params.validate(SubmissionCounts, Map("foo" -> "bar")).left.value should include ("Unknown parameter 'foo'")
  }

  "submission-counts" should "count submissions and distinct patients per site, use case, period and type" in {
    val q1 = LocalDateTime.of(2026, 2, 1, 0, 0)
    val q2 = LocalDateTime.of(2026, 4, 1, 0, 0)
    val cases = Seq(
      // two submissions of one episode
      caseOf(report("t1", createdAt = q1, patient = "p1") -> None, report("t2", createdAt = q1, patient = "p1") -> None),
      caseOf(report("t3", createdAt = q1, patient = "p2")),
      caseOf(report("t4", createdAt = q2, patient = "p1", submissionType = Submission.Type.FollowUp)),
      caseOf(report("t5", createdAt = q1, patient = "p1", site = "UKFR", useCase = UseCase.RD))
    )
    rows(SubmissionCounts.aggregate(cases.iterator, params(SubmissionCounts))) shouldBe Seq(
      Statistic.Table.row("UKFR", "RD", "2026-Q1", "initial", 1, 1),
      Statistic.Table.row("UKT", "MTB", "2026-Q1", "initial", 3, 2),
      Statistic.Table.row("UKT", "MTB", "2026-Q2", "followup", 1, 1)
    )

    rows(SubmissionCounts.aggregate(cases.iterator, params(SubmissionCounts, "period" -> "month"))).map(_(2)) shouldBe
      Seq("2026-02", "2026-02", "2026-04").map(Json.toJson(_))
  }

  "mtb-diagnoses" should "be restricted to MTB" in {
    MtbDiagnoses.restrict(BackupFilter()).useCases shouldBe Set(UseCase.MTB)
    MtbDiagnoses.restrict(BackupFilter(useCases = Set(UseCase.RD))).isUnsatisfiable shouldBe true
  }

  it should "count patients per diagnosis from their latest submission, leaving out test submissions" in {
    val cases = Seq(
      caseOf(report("t1", patient = "p1", createdAt = LocalDateTime.of(2026, 1, 1, 0, 0)), mtbSubmission("t1", "p1", Seq("C34.1"))),
      // later submission of p1 replaces the earlier one
      caseOf(report("t2", patient = "p1", createdAt = LocalDateTime.of(2026, 2, 1, 0, 0)), mtbSubmission("t2", "p1", Seq("C50.9", "C34.2"))),
      caseOf(report("t3", patient = "p2"), mtbSubmission("t3", "p2", Seq("C50.1"))),
      // same patient ID at another site is another patient
      caseOf(report("t4", patient = "p1", site = "UKFR"), mtbSubmission("t4", "p1", Seq("C50.1"))),
      caseOf(report("t5", patient = "p3"), mtbSubmission("t5", "p3", Seq("C61"), Submission.Type.Test)),
      caseOf(report("t6", patient = "p4"), mtbSubmission("t6", "p4") - "diagnoses")
    )

    val result = MtbDiagnoses.aggregate(cases.iterator, params(MtbDiagnoses))
    rows(result) shouldBe Seq(
      Statistic.Table.row("UKFR", "C50", 1),
      Statistic.Table.row("UKT", "C34", 1),
      Statistic.Table.row("UKT", "C50", 2)
    )
    result.notes.mkString(" ") should (include ("1 test submissions") and include ("1 submissions left out"))

    rows(MtbDiagnoses.aggregate(cases.iterator, params(MtbDiagnoses, "level" -> "code"))).map(_(1)) shouldBe
      Seq("C50.1", "C34.2", "C50.1", "C50.9").map(Json.toJson(_))
  }

  "export" should "export all documents, each deletion once" in {
    val r1 = report("t1", patient = "p1")
    val r2 = report("t2", patient = "p1", episodeOfCare = Some("episode-2"))
    val event = Deletion(Deletion.Kind.Event, "t0", LocalDateTime.of(2026, 3, 1, 0, 0), None, Some(deletionEvent("t0", "p1", LocalDateTime.of(2026, 3, 1, 0, 0))))
    val manual = Deletion(Deletion.Kind.Manual, "t9", LocalDateTime.of(2026, 4, 1, 0, 0), Some("withdrawn"))
    val cases = Seq(
      caseOf(r1, mtbSubmission("t1", "p1")).copy(deletions = Seq(event)),
      caseOf(r2).copy(deletions = Seq(event)),
      caseOf(r1).copy(patient = None, episodeOfCare = None, submissions = Seq.empty, deletions = Seq(manual))
    )
    val result = rows(Export.aggregate(cases.iterator, params(Export)))
    result.map(_.take(6)) shouldBe Seq(
      Statistic.Table.row("UKT", "MTB", "p1", "episode-1", "t1", "report"),
      Statistic.Table.row("UKT", "MTB", "p1", "episode-1", "t1", "submission"),
      Statistic.Table.row("UKT", "MTB", "p1", Option.empty[String], "t0", "deletion"),
      Statistic.Table.row("UKT", "MTB", "p1", "episode-2", "t2", "report"),
      Statistic.Table.row("UKT", "MTB", Option.empty[String], Option.empty[String], "t9", "manual-deletion")
    )
    result.map(_(7)) shouldBe Seq(Json.toJson(r1), mtbSubmission("t1", "p1"), deletionEvent("t0", "p1", event.at), Json.toJson(r2), Json.toJson("withdrawn"))
  }
}
