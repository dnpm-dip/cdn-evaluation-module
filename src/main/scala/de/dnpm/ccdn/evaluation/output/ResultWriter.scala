package de.dnpm.ccdn.evaluation.output

import java.io.{BufferedWriter, OutputStreamWriter, StringWriter, Writer}
import java.nio.charset.StandardCharsets.UTF_8
import java.nio.file.{Files, Path}
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import scala.util.Using
import de.dnpm.ccdn.evaluation.collect.{BackupFilter, CollectionSummary}
import de.dnpm.ccdn.evaluation.statistics.Statistic
import play.api.libs.json.{JsNull, JsNumber, JsObject, JsString, JsValue, Json}


/**
 * A statistic's result with everything needed to reproduce and interpret it, except for the
 * collection summary, which is complete only once the (possibly streamed) tables are written
 */
final case class Evaluation(
  statistic: Statistic,
  moduleVersion: String,
  startedAt: LocalDateTime,
  filter: BackupFilter,
  params: Statistic.Params,
  result: Statistic.Result
)


/**
 * Writes evaluations as JSON (and CSV), streaming the rows of the tables, so that a table is
 * never held in memory as a whole, see [[Statistic.Table.rows]]. Hence the notes and the
 * collection summary follow the tables in the JSON.
 */
object ResultWriter
{

  /** Writes the evaluation as pretty-printed JSON to stdout */
  def print(evaluation: Evaluation, collection: => CollectionSummary): Unit = {
    val out = new BufferedWriter(new OutputStreamWriter(System.out, UTF_8))
    writeJson(evaluation, collection, out, _ => None)
    out.write("\n")
    out.flush()
  }

  /**
   * Writes the evaluation into `dir` as `<statistic>_<startedAt>.json` and one
   * `<statistic>_<startedAt>_<table>.csv` per table
   *
   * @return the written files
   */
  def write(evaluation: Evaluation, collection: => CollectionSummary, dir: Path): Seq[Path] = {
    Files.createDirectories(dir)
    val base =
      s"${evaluation.statistic.name}_${evaluation.startedAt.format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"))}"
    val json = dir.resolve(s"$base.json")
    val csvs = evaluation.result.tables.map(table => table.name -> dir.resolve(s"${base}_${safeName(table.name)}.csv"))

    Using.resource(Files.newBufferedWriter(json, UTF_8)) { out =>
      writeJson(evaluation, collection, out,
        table => Some(Files.newBufferedWriter(csvs.find(_._1 == table.name).get._2, UTF_8)))
    }
    json +: csvs.map(_._2)
  }

  /** The evaluation as JSON object, e.g. for tests */
  def toJson(evaluation: Evaluation, collection: CollectionSummary): JsObject = {
    val out = new StringWriter
    writeJson(evaluation, collection, out, _ => None)
    Json.parse(out.toString).as[JsObject]
  }

  /** RFC 4180 CSV with header line */
  def toCsv(table: Statistic.Table): String = {
    val out = new StringWriter
    val csv = new CsvWriter(table, out)
    table.rows.iterator.foreach(csv.write)
    out.toString
  }

  /**
   * @param csv the writer to also write a table as CSV to, if any; closed once the table is written
   */
  private def writeJson(
    evaluation: Evaluation,
    collection: => CollectionSummary,
    out: Writer,
    csv: Statistic.Table => Option[Writer]
  ): Unit = {
    import evaluation._

    // values are pretty-printed one by one, indented to their depth: JSON strings contain no
    // raw line breaks, so all line breaks of the pretty-printed values are between tokens
    def value(v: JsValue, depth: Int): Unit =
      out.write(Json.prettyPrint(v).replace("\n", "\n" + "  " * depth))

    def field(name: String, v: JsValue, last: Boolean = false): Unit = {
      out.write(s"  ${Json.stringify(JsString(name))} : ")
      value(v, 1)
      out.write(if (last) "\n" else ",\n")
    }

    out.write("{\n")
    field("statistic", JsString(statistic.name))
    field("description", JsString(statistic.description))
    field("moduleVersion", JsString(moduleVersion))
    field("startedAt", Json.toJson(startedAt))
    field("filter",
      Json.obj(
        "sites"    -> filter.sites.toSeq.sorted,
        "useCases" -> filter.useCases.toSeq.map(_.toString).sorted,
        "from"     -> filter.from,
        "until"    -> filter.until
      )
    )
    field("parameters", Json.toJson(params.toMap))

    out.write("  \"tables\" : [")
    result.tables.zipWithIndex.foreach { case (table, i) =>
      out.write(if (i == 0) "\n" else ",\n")
      out.write(s"    {\n      \"name\" : ${Json.stringify(JsString(table.name))},\n      \"columns\" : ")
      value(Json.toJson(table.columns), 3)
      out.write(",\n      \"rows\" : [")
      val csvOut = csv(table)
      try {
        val csvWriter = csvOut.map(new CsvWriter(table, _))
        var first = true
        table.rows.iterator.foreach { row =>
          checkRow(table, row)
          out.write(if (first) "\n        " else ",\n        ")
          first = false
          value(JsObject(table.columns.zip(row)), 4)
          csvWriter.foreach(_.write(row))
        }
        out.write(if (first) "]\n    }" else "\n      ]\n    }")
      }
      finally csvOut.foreach(_.close())
    }
    out.write(if (result.tables.isEmpty) "],\n" else "\n  ],\n")

    // known only now that the tables are written
    field("notes", Json.toJson(result.notes))
    field("collection",
      Json.obj(
        "documents"       -> JsObject(collection.documents.toSeq.sortBy(_._1).map { case (t, n) => t.toString -> JsNumber(n) }),
        "cases"           -> collection.cases,
        "skippedCases"    -> collection.skippedCases,
        "failedDocuments" -> collection.failedDocuments
      ),
      last = true
    )
    out.write("}")
  }

  private def checkRow(table: Statistic.Table, row: Seq[JsValue]): Unit =
    require(row.size == table.columns.size, s"Table '${table.name}': each row needs ${table.columns.size} values")

  /** RFC 4180 CSV with header line, row by row */
  private final class CsvWriter(table: Statistic.Table, out: Writer)
  {
    private def cell(value: JsValue): String = {
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

    private def line(cells: Seq[String]): Unit = {
      out.write(cells.mkString(","))
      out.write("\r\n")
    }

    line(table.columns.map(c => cell(JsString(c))))

    def write(row: Seq[JsValue]): Unit = {
      checkRow(table, row)
      line(row.map(cell))
    }
  }

  private def safeName(s: String): String =
    s.replaceAll("[^A-Za-z0-9._-]", "_")
}
