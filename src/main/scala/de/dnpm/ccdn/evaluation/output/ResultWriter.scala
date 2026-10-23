package de.dnpm.ccdn.evaluation.output

import java.nio.charset.StandardCharsets.UTF_8
import java.nio.file.{Files, Path}
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import de.dnpm.ccdn.evaluation.collect.{BackupFilter, CollectionSummary}
import de.dnpm.ccdn.evaluation.statistics.Statistic
import play.api.libs.json.{JsNull, JsNumber, JsObject, JsString, JsValue, Json}


/**
 * A statistic's result with everything needed to reproduce and interpret it
 */
final case class Evaluation(
  statistic: Statistic,
  moduleVersion: String,
  startedAt: LocalDateTime,
  filter: BackupFilter,
  params: Statistic.Params,
  collection: CollectionSummary,
  result: Statistic.Result
)


object ResultWriter
{

  def toJson(evaluation: Evaluation): JsObject = {
    import evaluation._
    Json.obj(
      "statistic"     -> statistic.name,
      "description"   -> statistic.description,
      "moduleVersion" -> moduleVersion,
      "startedAt"     -> startedAt,
      "filter"        -> Json.obj(
        "sites"    -> filter.sites.toSeq.sorted,
        "useCases" -> filter.useCases.toSeq.map(_.toString).sorted,
        "tans"     -> filter.tans.toSeq.sorted,
        "from"     -> filter.from,
        "until"    -> filter.until
      ),
      "parameters"    -> params.toMap,
      "collection"    -> Json.obj(
        "documents"       -> JsObject(collection.documents.toSeq.sortBy(_._1).map { case (t, n) => t.toString -> JsNumber(n) }),
        "cases"           -> collection.cases,
        "skippedCases"    -> collection.skippedCases,
        "failedDocuments" -> collection.failedDocuments
      ),
      "notes"         -> result.notes,
      "tables"        -> result.tables.map(table =>
        Json.obj(
          "name"    -> table.name,
          "columns" -> table.columns,
          "rows"    -> table.rows.map(row => JsObject(table.columns.zip(row)))
        )
      )
    )
  }

  /** RFC 4180 CSV with header line */
  def toCsv(table: Statistic.Table): String = {
    def cell(value: JsValue): String = {
      val text =
        value match {
          case JsString(s) => s
          case JsNumber(n) => n.bigDecimal.toPlainString
          case JsNull      => ""
          case other       => Json.stringify(other)
        }
      if (text.exists(c => c == ',' || c == '"' || c == '\n' || c == '\r')) "\"" + text.replace("\"", "\"\"") + "\""
      else text
    }
    (table.columns.map(c => cell(JsString(c))) +: table.rows.map(_.map(cell)))
      .map(_.mkString(",") + "\r\n")
      .mkString
  }

  /**
   * Writes the evaluation into `dir` as `<statistic>_<startedAt>.json` and one
   * `<statistic>_<startedAt>_<table>.csv` per table
   *
   * @return the written files
   */
  def write(evaluation: Evaluation, dir: Path): Seq[Path] = {
    Files.createDirectories(dir)
    val base =
      s"${evaluation.statistic.name}_${evaluation.startedAt.format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"))}"
    val json = dir.resolve(s"$base.json")
    Files.writeString(json, Json.prettyPrint(toJson(evaluation)), UTF_8)
    json +: evaluation.result.tables.map { table =>
      val csv = dir.resolve(s"${base}_${safeName(table.name)}.csv")
      Files.writeString(csv, toCsv(table), UTF_8)
    }
  }

  private def safeName(s: String): String =
    s.replaceAll("[^A-Za-z0-9._-]", "_")
}
