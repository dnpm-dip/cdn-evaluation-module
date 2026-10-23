package de.dnpm.ccdn.evaluation.model

import de.dnpm.dip.service.mvh.Submission.Report
import play.api.libs.json.{JsObject, JsString, Reads}


/**
 * Reads the reports backed up by the central-data-node as [[de.dnpm.dip.service.mvh.Submission.Report]].
 *
 * The central-data-node's copy of that class only extends the processing statuses ("status"),
 * by intermediate states of its own processing. These are mapped onto the two statuses of the
 * library class, according to whether the report was submitted to the BfArM. The status at
 * backup time says nothing about the data, and shouldn't be used by statistics.
 */
object BackedUpReport
{

  private val submittedToBfarm = Set("submitted", "confirmed")

  implicit val reads: Reads[Report] =
    Reads {
      case obj: JsObject =>
        val status =
          (obj \ "status").asOpt[String].map(s =>
            if (submittedToBfarm(s)) Report.Status.Submitted else Report.Status.Unsubmitted
          )
        Report.format.reads(status.fold(obj)(s => obj + ("status" -> JsString(s.toString))))
      case other =>
        Report.format.reads(other)
    }
}
