package de.dnpm.ccdn.evaluation.collect

import com.mongodb.ConnectionString
import com.mongodb.client.{MongoClient, MongoClients, MongoDatabase}
import com.mongodb.client.model.Filters
import de.dnpm.dip.util.Logging
import org.bson.Document
import org.bson.conversions.Bson
import org.bson.types.ObjectId

import scala.jdk.CollectionConverters._
import scala.util.Try


/**
 * Source of the documents of the central-data-node's "backup" collection
 */
trait BackupSource extends AutoCloseable
{
  /**
   * The documents of the given types of the filter's sites and use cases. The filter's time frame
   * is not applied, since it selects cases, not documents. The iterator is only valid until the
   * source is closed.
   */
  def documents(filter: BackupFilter, types: Set[DocumentType.Value]): Iterator[BackupDocument]

  /**
   * The documents of the given types with one of the TANs, of the site and use case.
   * The iterator is only valid until the source is closed.
   */
  def documents(site: String, useCase: String, tans: Set[String], types: Set[DocumentType.Value]): Iterator[BackupDocument]
}


object MongoBackupSource
{
  /** Same configuration as for the central-data-node itself */
  val MongoUriEnv = "CCDN_MONGODB_URI"

  /** Database of the central-data-node, unless the URI names another one */
  private val DefaultDatabase = "ccdn"

  private val BackupCollection           = "backup"
  private val LargeBackupPartsCollection = "largeBackupParts"

  /**
   * Documents are fetched in small batches, since a submission may be up to 16 MiB large, and
   * processing a batch must not exceed the server's cursor timeout (10 min) between two fetches
   */
  private val BatchSize = 16

  /**
   * Connects and checks that the database is reachable, which fails after the client's
   * server selection timeout (30 s by default) if it isn't
   */
  def connect(uri: String): Either[String, MongoBackupSource] =
    Try(new ConnectionString(uri)).toEither.left.map(e => s"Invalid MongoDB URI: ${e.getMessage}")
      .flatMap { connectionString =>
        val client = MongoClients.create(connectionString)
        val dbName = Option(connectionString.getDatabase).getOrElse(DefaultDatabase)
        Try {
          val _ = client.getDatabase(dbName).runCommand(new Document("ping", 1))
          new MongoBackupSource(client, dbName)
        }
        .toEither
        .left.map { e =>
          client.close()
          s"Couldn't connect to MongoDB: ${e.getMessage}"
        }
      }

  private[collect] def toBson(filter: BackupFilter, types: Set[DocumentType.Value]): Bson =
    Filters.and(
      (
        Seq(
          Filters.in("type", types.map(_.toString).asJava),
          Filters.in("usecase", filter.useCases.map(_.toString).asJava)
        ) ++
        Option.when(filter.sites.nonEmpty)(Filters.in("site", filter.sites.asJava))
      ): _*
    )
}


final class MongoBackupSource private (
  client: MongoClient,
  dbName: String
) extends BackupSource with Logging
{
  import MongoBackupSource._

  private val db: MongoDatabase = client.getDatabase(dbName)

  override def documents(filter: BackupFilter, types: Set[DocumentType.Value]): Iterator[BackupDocument] =
    if (types.isEmpty || filter.isUnsatisfiable) Iterator.empty
    else find(toBson(filter, types))

  override def documents(site: String, useCase: String, tans: Set[String], types: Set[DocumentType.Value]): Iterator[BackupDocument] =
    if (types.isEmpty || tans.isEmpty) Iterator.empty
    else
      find(
        Filters.and(
          // served by the unique index the central-data-node ensures on the backup collection
          Filters.in("tan", tans.asJava),
          Filters.in("type", types.map(_.toString).asJava),
          Filters.eq("site", site),
          Filters.eq("usecase", useCase)
        )
      )

  private def find(filter: Bson): Iterator[BackupDocument] =
    db.getCollection(BackupCollection)
      .find(filter)
      .batchSize(BatchSize)
      .iterator()
      .asScala
      .flatMap(toBackupDocument)

  /**
   * None (logged) for documents lacking a plain field, which the central-data-node always sets
   */
  private def toBackupDocument(doc: Document): Option[BackupDocument] = {
    val plain =
      for {
        tan         <- Option(doc.getString("tan"))
        site        <- Option(doc.getString("site"))
        useCase     <- Option(doc.getString("usecase"))
        typeName    <- Option(doc.getString("type"))
        docType     <- DocumentType.values.find(_.toString == typeName)
        submittedAt <- Option(doc.getString("submittedAt"))
      } yield (tan, site, useCase, docType, submittedAt)

    plain match {
      case None =>
        log.error(s"Skipping backup document ${doc.getObjectId("_id")}: missing or invalid plain fields")
        None
      case Some((tan, site, useCase, docType, submittedAt)) =>
        val content =
          if (docType == DocumentType.ManualDeletion)
            Option(doc.getString("content")).fold[BackupDocument.Content](BackupDocument.Invalid("no comment"))(BackupDocument.Plain)
          else
            encryptedContent(doc)
        Some(BackupDocument(tan, site, useCase, docType, submittedAt, content))
    }
  }

  /**
   * The encrypted content, with a ciphertext stored in parts reassembled: then "content" holds
   * instead of "ciphertext" the "_id"s of the parts in "ciphertextParts", in order (see
   * MongodbLongTermStore.splitEncrypted in the central-data-node)
   */
  private def encryptedContent(doc: Document): BackupDocument.Content =
    Try(doc.get("content", classOf[Document])).toOption.flatMap(Option(_)) match {
      case None => BackupDocument.Invalid("no content")
      case Some(content) =>
        val ciphertext: Either[String, String] =
          Option(content.getString("ciphertext")) match {
            case Some(ciphertext) => Right(ciphertext)
            case None =>
              Option(content.getList("ciphertextParts", classOf[ObjectId]))
                .map(_.asScala.toSeq)
                .filter(_.nonEmpty)
                .toRight("neither ciphertext nor ciphertextParts in content")
                .flatMap(reassemble(doc, _))
          }
        val encrypted =
          for {
            ct           <- ciphertext
            algorithm    <- Option(content.getString("algorithm")).toRight("no algorithm")
            encryptedKey <- Option(content.getString("encryptedKey")).toRight("no encryptedKey")
            iv           <- Option(content.getString("iv")).toRight("no iv")
          } yield BackupDocument.Encrypted(algorithm, encryptedKey, iv, ct)

        encrypted.fold[BackupDocument.Content](BackupDocument.Invalid, identity)
    }

  private def reassemble(doc: Document, ids: Seq[ObjectId]): Either[String, String] = {
    val parts =
      db.getCollection(LargeBackupPartsCollection)
        .find(Filters.in("_id", ids.asJava))
        .asScala
        .map(part => part.getObjectId("_id") -> part)
        .toMap

    val identityFields = Seq("tan", "site", "usecase", "type")
    ids.foldLeft[Either[String, StringBuilder]](Right(new StringBuilder)) {
      (acc, id) =>
        acc.flatMap { sb =>
          parts.get(id) match {
            case None =>
              Left(s"ciphertext part ${id.toHexString} is missing")
            case Some(part) if identityFields.exists(f => part.getString(f) != doc.getString(f)) =>
              Left(s"ciphertext part ${id.toHexString} belongs to another backup")
            case Some(part) =>
              Option(part.getString("ciphertext"))
                .toRight(s"ciphertext part ${id.toHexString} has no ciphertext")
                .map(sb.append)
          }
        }
    }
    .map(_.toString)
  }

  override def close(): Unit =
    client.close()
}
