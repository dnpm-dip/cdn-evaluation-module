package de.dnpm.ccdn.evaluation

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.collect.{BackupDocument, BackupFilter, Collection, DataCollector, DocumentType, WrongKeyException}
import de.dnpm.ccdn.evaluation.model.{Deletion, EpisodeOfCare, SubmissionDocument}
import de.dnpm.dip.service.mvh.UseCase
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers
import play.api.libs.json.{JsObject, Json}


class DataCollectorTests extends AnyFlatSpec with Matchers
{
  import Fixtures._

  private val allTypes = DocumentType.values.toSet

  private def collect(docs: BackupDocument*)(types: Set[DocumentType.Value] = allTypes, filter: BackupFilter = BackupFilter()) =
    new DataCollector(new InMemorySource(docs), TestCrypto.defaultDecryptor).collect(filter, types)

  private def at(month: Int, day: Int = 1) = LocalDateTime.of(2026, month, day, 10, 0)

  "DataCollector" should "group the submissions of a patient's episode of care into one case" in {
    val r1 = report("tan-1", createdAt = at(1))
    val r2 = report("tan-2", createdAt = at(3), episodeOfCare = Some("episode-1"))
    val r3 = report("tan-3", createdAt = at(2), episodeOfCare = Some("episode-2"))
    val r4 = report("tan-4", site = "UKFR", useCase = UseCase.RD)
    val collection =
      collect(
        reportDocument(r2), submissionDocument(r2, mtbSubmission("tan-2")),
        reportDocument(r1), submissionDocument(r1, mtbSubmission("tan-1")),
        reportDocument(r3), reportDocument(r4)
      )()

    val cases = collection.cases.toList
    cases.map(c => (c.site, c.useCase, c.patient, c.episodeOfCare, c.submissions.map(_.tan))) shouldBe List(
      ("UKFR", UseCase.RD, Some("patient-1"), Some("episode-1"), Seq("tan-4")),
      ("UKT", UseCase.MTB, Some("patient-1"), Some("episode-1"), Seq("tan-1", "tan-2")),
      ("UKT", UseCase.MTB, Some("patient-1"), Some("episode-2"), Seq("tan-3"))
    )
    cases(1).submissions.map(_.report) shouldBe Seq(r1, r2)
    cases(1).submissions.map(_.submittedAt) shouldBe Seq(r1.createdAt, r2.createdAt)
    cases(1).submissions.flatMap(_.submission).map(_.json) shouldBe Seq(mtbSubmission("tan-1"), mtbSubmission("tan-2"))
    cases(2).submissions.map(_.submission) shouldBe Seq(None)

    collection.summary.cases shouldBe 3
    collection.summary.documents shouldBe Map(DocumentType.Report -> 4, DocumentType.Submission -> 2)
    collection.summary.skippedCases shouldBe 0
  }

  it should "keep patients of different sites apart" in {
    val cases = collect(reportDocument(report("tan-1", site = "UKT")), reportDocument(report("tan-2", site = "UKFR")))().cases.toList
    cases.map(_.site) shouldBe List("UKFR", "UKT")
  }

  it should "keep the reports as stored" in {
    val r = report("tan-1")
    val stored = collect(reportDocument(r, status = "confirmed"))().cases.toList.flatMap(_.submissions.map(_.reportJson))
    stored.map(json => (json \ "status").as[String]) shouldBe List("confirmed")
  }

  it should "read reports with the central-data-node's processing statuses" in {
    val statuses = Seq("unsubmitted", "submissionbackedup", "reportbackedup", "submitted", "confirmed")
    val docs = statuses.map(s => reportDocument(report(s"tan-$s"), status = s))
    val reports = collect(docs: _*)().cases.flatMap(_.submissions.map(_.report)).toList
    reports.map(r => r.id.value -> r.status.toString) should contain theSameElementsAs Seq(
      "tan-unsubmitted"        -> "unsubmitted",
      "tan-submissionbackedup" -> "unsubmitted",
      "tan-reportbackedup"     -> "unsubmitted",
      "tan-submitted"          -> "submitted",
      "tan-confirmed"          -> "submitted"
    )
  }

  it should "only collect the requested document types, but always the reports" in {
    val r = report("tan-1")
    val docs = Seq(reportDocument(r), submissionDocument(r, mtbSubmission("tan-1")), deletionDocument("tan-0", "patient-1", at(1)))
    collect(docs: _*)(Set(DocumentType.Report)).cases.toList.map(c => (c.submissions.map(_.submission), c.deletions)) shouldBe
      List(Seq(None) -> Seq.empty)
    collect(docs: _*)(Set(DocumentType.Submission)).cases.toList.map(_.submissions.flatMap(_.submission)) shouldBe
      List(Seq(SubmissionDocument(mtbSubmission("tan-1"))))
  }

  it should "derive the episode of care of older reports from their submission" in {
    val episodes = Seq(("e1", "2025-01-01", Some("2025-12-31")), ("e2", "2026-02-01", None))
    val r1 = report("tan-1", createdAt = at(3), episodeOfCare = None)
    val r2 = report("tan-2", createdAt = at(4), episodeOfCare = Some("e2"))
    val r3 = report("tan-3", createdAt = LocalDateTime.of(2025, 6, 1, 0, 0), episodeOfCare = None)
    val r4 = report("tan-4", createdAt = at(5), episodeOfCare = None)  // without submission
    val cases =
      collect(
        reportDocument(r1), submissionDocument(r1, mtbSubmission("tan-1", episodesOfCare = episodes)),
        reportDocument(r2),
        reportDocument(r3), submissionDocument(r3, mtbSubmission("tan-3", episodesOfCare = episodes)),
        reportDocument(r4)
      )(Set(DocumentType.Report)).cases.toList

    cases.map(c => c.episodeOfCare -> c.submissions.map(_.tan)) shouldBe List(
      None       -> Seq("tan-4"),
      Some("e1") -> Seq("tan-3"),
      Some("e2") -> Seq("tan-1", "tan-2")
    )
  }

  "EpisodeOfCare.of" should "prefer the metadata, else take the episode containing the report's creation" in {
    val episodes = Seq(("e2", "2026-02-01", Some("2026-03-01")), ("e1", "2025-01-01", Some("2025-12-31")))
    def episode(createdAt: LocalDateTime, json: JsObject = mtbSubmission("t", episodesOfCare = episodes)) =
      EpisodeOfCare.of(SubmissionDocument(json), createdAt)

    episode(LocalDateTime.of(2025, 5, 1, 0, 0)) shouldBe Some("e1")
    episode(at(2, 15)) shouldBe Some("e2")
    // none containing it: the latest one started before, else the first
    episode(at(6)) shouldBe Some("e2")
    episode(LocalDateTime.of(2024, 1, 1, 0, 0)) shouldBe Some("e1")
    val json = mtbSubmission("t", episodesOfCare = episodes)
    episode(at(2, 15), json ++ Json.obj("metadata" -> ((json \ "metadata").as[JsObject] + ("episodeOfCare" -> Json.obj("id" -> "e1"))))) shouldBe Some("e1")
    episode(at(1), mtbSubmission("t")) shouldBe None
  }

  "DataCollector" should "attach deletion events to all cases of their patient" in {
    val r1 = report("tan-1", episodeOfCare = Some("e1"))
    val r2 = report("tan-2", episodeOfCare = Some("e2"))
    val cases =
      collect(
        reportDocument(r1), reportDocument(r2),
        deletionDocument("tan-0", "patient-1", at(3)),
        deletionDocument("tan-9", "patient-2", at(4)),
        manualDeletionDocument("tan-8", at(5), "withdrawn")
      )().cases.toList

    cases.map(c => (c.patient, c.episodeOfCare, c.submissions.map(_.tan), c.deletions)) shouldBe List(
      (None, None, Seq(), Seq(Deletion(Deletion.Kind.Manual, "tan-8", at(5), Some("withdrawn")))),
      (Some("patient-1"), Some("e1"), Seq("tan-1"), Seq(Deletion(Deletion.Kind.Event, "tan-0", at(3), None, Some(deletionEvent("tan-0", "patient-1", at(3)))))),
      (Some("patient-1"), Some("e2"), Seq("tan-2"), Seq(Deletion(Deletion.Kind.Event, "tan-0", at(3), None, Some(deletionEvent("tan-0", "patient-1", at(3)))))),
      (Some("patient-2"), None, Seq(), Seq(Deletion(Deletion.Kind.Event, "tan-9", at(4), None, Some(deletionEvent("tan-9", "patient-2", at(4))))))
    )
  }

  it should "select the cases with a submission in the time frame" in {
    val docs = Seq(
      reportDocument(report("tan-1", createdAt = at(1), episodeOfCare = Some("e1"))),
      reportDocument(report("tan-2", createdAt = at(4), episodeOfCare = Some("e1"))),
      reportDocument(report("tan-3", createdAt = at(1), episodeOfCare = Some("e2"))),
      reportDocument(report("tan-4", createdAt = at(6), episodeOfCare = Some("e3"))),
      deletionDocument("tan-0", "patient-2", at(4))
    )
    val cases = collect(docs: _*)(filter = BackupFilter(from = Some(at(3)), until = Some(at(6)))).cases.toList
    cases.map(c => c.episodeOfCare -> c.submissions.map(_.tan)) shouldBe List(Some("e1") -> Seq("tan-1", "tan-2"))
  }

  it should "leave out an unreadable report, and count it" in {
    val r1 = report("tan-1")
    val r2 = report("tan-2", patient = "patient-2")
    val broken = reportDocument(r2).copy(content = BackupDocument.Invalid("ciphertext part missing"))
    val collection = collect(reportDocument(r1), broken, submissionDocument(r2, mtbSubmission("tan-2")))()

    collection.cases.map(_.submissions.map(_.tan)).toList shouldBe List(Seq("tan-1"))
    collection.summary.skippedCases shouldBe 0
    collection.summary.failedDocuments shouldBe 1
  }

  it should "leave out a case with an unreadable submission, and count it" in {
    val r1 = report("tan-1", episodeOfCare = Some("e1"))
    val r2 = report("tan-2", episodeOfCare = Some("e2"))
    val broken = submissionDocument(r2, mtbSubmission("tan-2")).copy(content = BackupDocument.Invalid("ciphertext part missing"))
    val collection = collect(reportDocument(r1), reportDocument(r2), broken)()

    collection.cases.map(_.episodeOfCare).toList shouldBe List(Some("e1"))
    collection.summary.skippedCases shouldBe 1
    collection.summary.failedDocuments shouldBe 1
  }

  it should "abort if the first documents can't be decrypted with the key" in {
    val otherKeys = TestCrypto.keyPair()
    val docs =
      (1 to Collection.KeyMismatchLimit).map { i =>
        reportDocument(report(s"tan-$i")).copy(content = TestCrypto.encrypt(Json.obj(), otherKeys))
      }
    a [WrongKeyException] should be thrownBy collect(docs: _*)().cases.toList
  }

  it should "not abort on single undecryptable documents after successful ones" in {
    val otherKeys = TestCrypto.keyPair()
    val good = reportDocument(report("tan-0"))
    val bad =
      (1 to Collection.KeyMismatchLimit).map { i =>
        reportDocument(report(s"tan-$i")).copy(content = TestCrypto.encrypt(Json.obj(), otherKeys))
      }
    val collection = collect(good +: bad: _*)()
    collection.cases.map(_.submissions.map(_.tan)).toList shouldBe List(Seq("tan-0"))
    collection.summary.failedDocuments shouldBe Collection.KeyMismatchLimit
  }
}
