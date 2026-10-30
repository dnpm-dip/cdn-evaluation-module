package de.dnpm.ccdn.evaluation.statistics


/**
 * Registry of all statistics that can be run. Register new statistics here.
 */
object Statistics
{
  val all: Seq[Statistic] =
    Seq(
      SubmissionCounts,
      MtbDiagnoses,
      Export,
      ExportDatenkranz
    )

  def find(name: String): Option[Statistic] =
    all.find(_.name == name)
}
