package de.dnpm.ccdn.evaluation

import java.nio.charset.StandardCharsets.UTF_8
import java.nio.file.{Files, Path, Paths}
import java.time.LocalDateTime
import scala.util.{Try, Using}
import scala.util.Properties.envOrNone
import de.dnpm.ccdn.evaluation.collect.{DataCollector, MongoBackupSource, RsaHybridDecryptor, WrongKeyException}
import de.dnpm.ccdn.evaluation.output.{Evaluation, ResultWriter}
import de.dnpm.ccdn.evaluation.statistics.{Statistic, Statistics}
import de.dnpm.dip.util.Logging
import play.api.libs.json.Json


/**
 * Runs a statistic on the backups of the central-data-node, on demand: collects the backup
 * documents (common to all statistics), then aggregates them as implemented per statistic.
 *
 * Exit codes: 0 success, 1 failure, 2 result written, but cases had to be left out (see the log).
 * Logs go to stderr, so that stdout carries only the result.
 */
object EvaluationApp extends Logging
{
  val MongoUriEnv       = MongoBackupSource.MongoUriEnv
  val PrivateKeyPathEnv = "CCDN_BACKUP_PRIVATE_KEY_PATH"
  /** Same as for backup-extract.sh of the deployment */
  val PassphraseEnv     = "CCDN_BACKUP_KEY_PASSPHRASE"
  val OutputDirEnv      = "CCDN_EVALUATION_OUTPUT_DIR"

  private val Success          = 0
  private val Failure          = 1
  private val IncompleteResult = 2

  lazy val version: String =
    Option(getClass.getPackage.getImplementationVersion).getOrElse("development")

  def main(args: Array[String]): Unit =
    sys.exit(
      CommandLine.parse(args.toSeq) match {
        case Left(error) =>
          System.err.println(s"$error\n\n${CommandLine.usage}")
          Failure
        case Right(CommandLine.Help) =>
          println(CommandLine.usage)
          Success
        case Right(CommandLine.ListStatistics) =>
          println(listing)
          Success
        case Right(run: CommandLine.Run) =>
          Try(execute(run)).recover {
            case e: WrongKeyException =>
              log.error(e.getMessage)
              Failure
            case e =>
              log.error(s"Evaluation failed: ${e.getMessage}", e)
              Failure
          }
          .get
      }
    )

  private def listing: String =
    Statistics.all.map { s =>
      val params =
        s.parameters.map { p =>
          val allowed = if (p.allowed.nonEmpty) s" (${p.allowed.mkString("|")})" else ""
          s"\n    --param ${p.name}=...$allowed, default '${p.default}': ${p.description}"
        }
        .mkString
      s"${s.name}\n    ${s.description}$params"
    }
    .mkString(s"dnpm-ccdn-evaluation $version, available statistics:\n\n", "\n\n", "")

  private def required(value: Option[String], option: String, env: String): Either[String, String] =
    value.orElse(envOrNone(env)).filter(_.nonEmpty)
      .toRight(s"Missing $option (or environment variable $env)")

  /**
   * Passphrase from file, environment, or interactively, in that order. Only called for an encrypted key.
   */
  private def passphrase(file: Option[String]): Option[Array[Char]] =
    file.map(f => Files.readString(Paths.get(f), UTF_8).stripLineEnd.toCharArray)
      .orElse(envOrNone(PassphraseEnv).map(_.toCharArray))
      .orElse(Option(System.console()).map(_.readPassword("Passphrase of the private key: ")))

  private def execute(run: CommandLine.Run): Int = {
    val startedAt = LocalDateTime.now
    val setup =
      for {
        statistic <- Statistics.find(run.statistic)
                       .toRight(s"Unknown statistic '${run.statistic}', available: ${Statistics.all.map(_.name).mkString(", ")}")
        params    <- Statistic.Params.validate(statistic, run.params)
        keyPath   <- required(run.keyPath, "--key", PrivateKeyPathEnv)
        pem       <- Try(Files.readString(Paths.get(keyPath), UTF_8)).toEither.left.map(e => s"Couldn't read private key file: $e")
        decryptor <- RsaHybridDecryptor.fromPem(pem, passphrase(run.passphraseFile))
        mongoUri  <- required(run.mongoUri, "--mongodb-uri", MongoUriEnv)
      } yield (statistic, params, decryptor, mongoUri)

    setup match {
      case Left(error) =>
        log.error(error)
        Failure
      case Right((statistic, params, decryptor, mongoUri)) =>
        val filter = statistic.restrict(run.filter)
        if (filter.isUnsatisfiable)
          log.warn(s"No backups can match the filter $filter, note that statistic '${statistic.name}' may be restricted to certain use cases")

        MongoBackupSource.connect(mongoUri) match {
          case Left(error) =>
            log.error(error)
            Failure
          case Right(source) =>
            Using.resource(source) { source =>
              log.info(s"Running statistic '${statistic.name}' (module version $version) with ${params.toMap} on backups matching $filter")
              val collection = new DataCollector(source, decryptor).collect(filter, statistic.documentTypes)
              val result     = statistic.aggregate(collection.cases, params)
              // drain the cases the statistic didn't consume, so that the summary is complete
              collection.cases.foreach(_ => ())
              val summary    = collection.summary

              val evaluation = Evaluation(statistic, version, startedAt, filter, params, summary, result)
              output(evaluation, run.outputDir.orElse(envOrNone(OutputDirEnv)).filter(_.nonEmpty).map(Paths.get(_)))

              log.info(s"Done: ${summary.cases} cases evaluated from ${summary.documents.values.sum} documents")
              if (summary.skippedCases > 0) {
                log.warn(s"${summary.skippedCases} cases were left out, since ${summary.failedDocuments} of their documents couldn't be read, see the errors above")
                IncompleteResult
              }
              else Success
            }
        }
    }
  }

  private def output(evaluation: Evaluation, dir: Option[Path]): Unit =
    dir match {
      case Some(d) =>
        ResultWriter.write(evaluation, d).foreach(file => log.info(s"Written $file"))
      case None =>
        println(Json.prettyPrint(ResultWriter.toJson(evaluation)))
    }
}
