package de.dnpm.ccdn.evaluation.statistics

import de.dnpm.ccdn.evaluation.collect.{BackupFilter, DocumentType}
import de.dnpm.ccdn.evaluation.model.Case
import play.api.libs.json.{JsValue, Json}


/**
 * A statistic requested by stakeholders: the aggregation step on top of the common data collection.
 *
 * To add one, implement this trait as an object in this package and register it in
 * [[Statistics.all]]. Once released, it can be run (and rerun with other filters and
 * parameters) by its [[name]], see the README.
 */
trait Statistic
{
  /** Identifies the statistic on the command line, in kebab-case, e.g. "submission-counts". Never change it once released. */
  def name: String

  /** What it shows and for whom, in a few sentences */
  def description: String

  /**
   * The backup document types to collect. Only these are fetched and decrypted, so keep it minimal:
   * reports are small, while submissions may be several megabytes each.
   */
  def documentTypes: Set[DocumentType.Value]

  /** Parameters specific to this statistic, set with `--param name=value` */
  def parameters: Seq[Statistic.Parameter] = Seq.empty

  /** Narrows the filter given on the command line, e.g. to the use case this statistic applies to */
  def restrict(filter: BackupFilter): BackupFilter = filter

  /**
   * Computes the statistic from the collected cases. The cases are streamed: they can be
   * traversed only once, and shouldn't be held in memory all at once, so aggregate
   * them while traversing (e.g. with foldLeft).
   */
  def aggregate(cases: Iterator[Case], params: Statistic.Params): Statistic.Result
}


object Statistic
{
  /**
   * @param allowed if non-empty, the only values accepted
   */
  final case class Parameter(
    name: String,
    description: String,
    default: String,
    allowed: Seq[String] = Seq.empty
  )

  /** Values of the [[Statistic.parameters]], validated, with defaults for those not given */
  final class Params private (values: Map[String, String])
  {
    /** The value of a declared parameter (throws for an undeclared one, i.e. a programming error) */
    def apply(name: String): String =
      values.getOrElse(name, throw new NoSuchElementException(s"Undeclared parameter '$name'"))

    def toMap: Map[String, String] = values
  }

  object Params
  {
    def validate(statistic: Statistic, values: Map[String, String]): Either[String, Params] = {
      val declared = statistic.parameters.map(p => p.name -> p).toMap
      val errors =
        values.toSeq.sortBy(_._1).flatMap { case (name, value) =>
          declared.get(name) match {
            case None =>
              Some(s"Unknown parameter '$name' for statistic '${statistic.name}'")
            case Some(p) if p.allowed.nonEmpty && !p.allowed.contains(value) =>
              Some(s"Invalid value '$value' of parameter '$name', allowed: ${p.allowed.mkString(", ")}")
            case _ =>
              None
          }
        }
      Either.cond(
        errors.isEmpty,
        new Params(statistic.parameters.map(p => p.name -> values.getOrElse(p.name, p.default)).toMap),
        errors.mkString("\n")
      )
    }
  }


  /**
   * @param columns names of the columns
   * @param rows    each with one value per column
   */
  final case class Table(
    name: String,
    columns: Seq[String],
    rows: Seq[Seq[JsValue]]
  )
  {
    require(rows.forall(_.size == columns.size), s"Table '$name': each row needs ${columns.size} values")
  }

  object Table
  {
    /** A row of values of any type with a play-json Writes, e.g. `Table.row("UKT", 42)` */
    def row(values: Json.JsValueWrapper*): Seq[JsValue] =
      Json.arr(values: _*).value.toSeq
  }

  /**
   * @param notes remarks needed to interpret the tables, e.g. on cases left out
   */
  final case class Result(
    tables: Seq[Table],
    notes: Seq[String] = Seq.empty
  )
}
