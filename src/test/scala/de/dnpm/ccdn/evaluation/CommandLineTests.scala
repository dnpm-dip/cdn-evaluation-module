package de.dnpm.ccdn.evaluation

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.CommandLine._
import de.dnpm.ccdn.evaluation.collect.BackupFilter
import de.dnpm.ccdn.evaluation.output.{Evaluation, ResultWriter}
import de.dnpm.ccdn.evaluation.collect.{CollectionSummary, DocumentType}
import de.dnpm.ccdn.evaluation.statistics.{Statistic, SubmissionCounts}
import de.dnpm.dip.service.mvh.UseCase
import org.scalatest.EitherValues
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import play.api.libs.json.{JsNull, Json}


class CommandLineTests extends AnyFlatSpec with Matchers with EitherValues
{

  "CommandLine" should "parse the commands" in {
    parse(Seq()).value shouldBe Help
    parse(Seq("help")).value shouldBe Help
    parse(Seq("list")).value shouldBe ListStatistics
    parse(Seq("run", "x")).value shouldBe Run("x")
    parse(Seq("run")).left.value should include ("Missing name")
    parse(Seq("foo")).left.value should include ("Unknown command")
  }

  it should "parse filters, parameters and options" in {
    parse(Seq(
      "run", "submission-counts",
      "--site", "UKT,UKFR", "--site=UKM",
      "--usecase", "mtb",
      "--tan", "a",
      "--from", "2026-01-01", "--until=2026-04-01T12:30",
      "--param", "period=month", "--param", "x=a=b",
      "--key", "/k.pem", "--passphrase-file", "/p", "--mongodb-uri", "mongodb://m/ccdn", "--output", "/out"
    )).value shouldBe
      Run(
        statistic      = "submission-counts",
        filter         = BackupFilter(
          sites    = Set("UKT", "UKFR", "UKM"),
          useCases = Set(UseCase.MTB),
          tans     = Set("a"),
          from     = Some(LocalDateTime.of(2026, 1, 1, 0, 0)),
          until    = Some(LocalDateTime.of(2026, 4, 1, 12, 30))
        ),
        params         = Map("period" -> "month", "x" -> "a=b"),
        keyPath        = Some("/k.pem"),
        passphraseFile = Some("/p"),
        mongoUri       = Some("mongodb://m/ccdn"),
        outputDir      = Some("/out")
      )
  }

  it should "default to all use cases" in {
    parse(Seq("run", "x", "--site", "UKT")).value.asInstanceOf[Run].filter.useCases shouldBe UseCase.values
  }

  it should "reject invalid input" in {
    parse(Seq("run", "x", "--usecase", "XYZ")).left.value should include ("Unknown use case")
    parse(Seq("run", "x", "--from", "01.01.2026")).left.value should include ("Invalid date")
    parse(Seq("run", "x", "--param", "noequals")).left.value should include ("NAME=VALUE")
    parse(Seq("run", "x", "--site")).left.value should include ("Missing value")
    parse(Seq("run", "x", "--bogus", "1")).left.value should include ("Unknown option")
  }

  "BackupFilter.format" should "be comparable with the stored submittedAt strings" in {
    BackupFilter.format(LocalDateTime.of(2026, 1, 1, 0, 0)) shouldBe "2026-01-01T00:00:00"
    // as stored by the central-data-node: LocalDateTime.format(ISO_LOCAL_DATE_TIME)
    BackupFilter.format(LocalDateTime.of(2026, 1, 1, 0, 0)) should be < "2026-01-01T00:00:00.123"
  }

  "ResultWriter" should "write CSV with escaping" in {
    val table = Statistic.Table("t", Seq("a", "b,c"), Seq(Statistic.Table.row("x\"y", 1.5), Seq(JsNull, Json.toJson(2))))
    ResultWriter.toCsv(table) shouldBe "a,\"b,c\"\r\n\"x\"\"y\",1.5\r\n,2\r\n"
  }

  it should "include filter, parameters and collection summary in the JSON" in {
    val evaluation =
      Evaluation(
        SubmissionCounts, "1.0.0", LocalDateTime.of(2026, 10, 1, 12, 0),
        BackupFilter(sites = Set("UKT")),
        Statistic.Params.validate(SubmissionCounts, Map.empty).value,
        CollectionSummary(Map(DocumentType.Report -> 3), 3, 0, 0),
        Statistic.Result(Seq(Statistic.Table("t", Seq("site", "n"), Seq(Statistic.Table.row("UKT", 3)))))
      )
    val json = ResultWriter.toJson(evaluation)
    (json \ "statistic").as[String] shouldBe "submission-counts"
    (json \ "filter" \ "sites").as[Seq[String]] shouldBe Seq("UKT")
    (json \ "filter" \ "useCases").as[Seq[String]] shouldBe Seq("MTB", "RD")
    (json \ "parameters" \ "period").as[String] shouldBe "quarter"
    (json \ "collection" \ "documents" \ "report").as[Int] shouldBe 3
    (json \ "tables" \ 0 \ "rows" \ 0).get shouldBe Json.obj("site" -> "UKT", "n" -> 3)
  }
}
