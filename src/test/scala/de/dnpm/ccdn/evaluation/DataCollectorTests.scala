package de.dnpm.ccdn.evaluation

import java.time.LocalDateTime
import de.dnpm.ccdn.evaluation.collect.{BackupDocument, BackupFilter, Collection, DataCollector, DocumentType, WrongKeyException}
import de.dnpm.ccdn.evaluation.model.Deletion
import de.dnpm.dip.service.mvh.UseCase
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers


class DataCollectorTests extends AnyFlatSpec with Matchers
{
  import Fixtures._

  private val allTypes = DocumentType.values.toSet

  private def collect(docs: BackupDocument*)(types: Set[DocumentType.Value] = allTypes) =
    new DataCollector(new InMemorySource(docs), TestCrypto.defaultDecryptor).collect(BackupFilter(), types)

  "groupConsecutive" should "group runs of equal keys, keeping order" in {
    Collection.groupConsecutive(Iterator(1, 1, 2, 3, 3, 3, 1))(identity).toList shouldBe
      List(1 -> Seq(1, 1), 2 -> Seq(2), 3 -> Seq(3, 3, 3), 1 -> Seq(1))
    Collection.groupConsecutive(Iterator.empty[Int])(identity).hasNext shouldBe false
  }

  "DataCollector" should "combine report and submission of a TAN into one case" in {
    val r1 = report("tan-1")
    val r2 = report("tan-2", site = "UKFR", useCase = UseCase.RD)
    val collection =
      collect(reportDocument(r1), submissionDocument(r1, mtbSubmission("tan-1")), reportDocument(r2))()

    val cases = collection.cases.toList
    cases.map(c => (c.tan, c.site, c.useCase)) shouldBe List(("tan-1", "UKT", UseCase.MTB), ("tan-2", "UKFR", UseCase.RD))
    cases.head.report shouldBe Some(r1)
    cases.head.submittedAt shouldBe Some(r1.createdAt)
    cases.head.submission.map(_.json) shouldBe Some(mtbSubmission("tan-1"))
    cases(1).submission shouldBe None

    collection.summary.cases shouldBe 2
    collection.summary.documents shouldBe Map(DocumentType.Report -> 2, DocumentType.Submission -> 1)
    collection.summary.skippedCases shouldBe 0
  }

  it should "read reports with the central-data-node's processing statuses" in {
    val statuses = Seq("unsubmitted", "submissionbackedup", "reportbackedup", "submitted", "confirmed")
    val docs = statuses.map(s => reportDocument(report(s"tan-$s"), status = s))
    val reports = collect(docs: _*)().cases.flatMap(_.report).toList
    reports.map(r => r.id.value -> r.status.toString) should contain theSameElementsAs Seq(
      "tan-unsubmitted"        -> "unsubmitted",
      "tan-submissionbackedup" -> "unsubmitted",
      "tan-reportbackedup"     -> "unsubmitted",
      "tan-submitted"          -> "submitted",
      "tan-confirmed"          -> "submitted"
    )
  }

  it should "keep the same TAN from different sites as different cases" in {
    val cases = collect(reportDocument(report("tan-1", site = "UKT")), reportDocument(report("tan-1", site = "UKFR")))().cases.toList
    cases.map(_.site) shouldBe List("UKFR", "UKT")
  }

  it should "only collect the requested document types" in {
    val r = report("tan-1")
    val cases = collect(reportDocument(r), submissionDocument(r, mtbSubmission("tan-1")))(Set(DocumentType.Report)).cases.toList
    cases.map(_.submission) shouldBe List(None)
  }

  it should "read deletions without decrypting them" in {
    val deletedAt = LocalDateTime.of(2026, 3, 1, 8, 0)
    val docs = Seq(
      BackupDocument("tan-1", "UKT", "MTB", DocumentType.Deletion, BackupFilter.format(deletedAt),
        BackupDocument.Encrypted("whatever", "", "", "")),
      BackupDocument("tan-1", "UKT", "MTB", DocumentType.ManualDeletion, BackupFilter.format(deletedAt.plusDays(1)),
        BackupDocument.Plain("withdrawn"))
    )
    val cases = collect(docs: _*)().cases.toList
    cases.map(_.deletions) shouldBe List(Seq(
      Deletion(Deletion.Kind.Event, deletedAt, None),
      Deletion(Deletion.Kind.Manual, deletedAt.plusDays(1), Some("withdrawn"))
    ))
    cases.head.submittedAt shouldBe None
  }

  it should "leave out a case with an unreadable document, and count it" in {
    val r1 = report("tan-1")
    val r2 = report("tan-2")
    val broken = reportDocument(r2).copy(content = BackupDocument.Invalid("ciphertext part missing"))
    val collection = collect(reportDocument(r1), broken, submissionDocument(r2, mtbSubmission("tan-2")))()

    collection.cases.map(_.tan).toList shouldBe List("tan-1")
    collection.summary.skippedCases shouldBe 1
    collection.summary.failedDocuments shouldBe 1
  }

  it should "abort if the first documents can't be decrypted with the key" in {
    val otherKeys = TestCrypto.keyPair()
    val docs =
      (1 to Collection.KeyMismatchLimit).map { i =>
        reportDocument(report(s"tan-$i")).copy(content = TestCrypto.encrypt(play.api.libs.json.Json.obj(), otherKeys))
      }
    a [WrongKeyException] should be thrownBy collect(docs: _*)().cases.toList
  }

  it should "not abort on single undecryptable documents after successful ones" in {
    val otherKeys = TestCrypto.keyPair()
    val good = reportDocument(report("tan-0"))
    val bad =
      (1 to Collection.KeyMismatchLimit).map { i =>
        reportDocument(report(s"tan-$i")).copy(content = TestCrypto.encrypt(play.api.libs.json.Json.obj(), otherKeys))
      }
    val collection = collect(good +: bad: _*)()
    collection.cases.map(_.tan).toList shouldBe List("tan-0")
    collection.summary.skippedCases shouldBe Collection.KeyMismatchLimit
  }
}
