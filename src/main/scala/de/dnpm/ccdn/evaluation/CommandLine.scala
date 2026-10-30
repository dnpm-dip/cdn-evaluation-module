package de.dnpm.ccdn.evaluation

import java.time.{LocalDate, LocalDateTime}
import scala.annotation.tailrec
import scala.util.Try
import de.dnpm.ccdn.evaluation.collect.BackupFilter
import de.dnpm.dip.service.mvh.UseCase


object CommandLine
{
  sealed trait Command
  case object Help extends Command
  case object ListStatistics extends Command

  /**
   * Values not given here may be configured via environment variables, see [[EvaluationApp]]
   */
  final case class Run(
    statistic: String,
    filter: BackupFilter = BackupFilter(),
    params: Map[String, String] = Map.empty,
    keyPath: Option[String] = None,
    passphraseFile: Option[String] = None,
    mongoUri: Option[String] = None,
    outputDir: Option[String] = None
  ) extends Command


  val usage: String =
    s"""Usage:
       |  run <statistic> [options]   Runs a statistic on the backups
       |  list                        Lists the available statistics and their parameters
       |  help                        Shows this help
       |
       |Filters (combined with AND; lists are comma-separated or the option repeated):
       |  --site CODE[,CODE...]       Site code(s), e.g. UKT,UKFR
       |  --usecase UC[,UC...]        ${UseCase.values.mkString(" and/or ")}
       |  --from DATE                 Cases with a submission at or after DATE (ISO, e.g. 2026-01-01 or 2026-01-01T12:00)
       |  --until DATE                Cases with a submission before DATE (exclusive); with --from, in between
       |
       |Options:
       |  --param NAME=VALUE          Parameter of the statistic (repeatable), see "list"
       |  --key FILE                  RSA private key (PKCS#8 PEM) for decrypting the backups,
       |                              default: $$${EvaluationApp.PrivateKeyPathEnv}
       |  --passphrase-file FILE      File containing the key's passphrase; otherwise taken from
       |                              $$${EvaluationApp.PassphraseEnv} or prompted for
       |  --mongodb-uri URI           default: $$${EvaluationApp.MongoUriEnv}
       |  --output DIR                Writes the result as JSON and CSV files into DIR, instead of
       |                              JSON to stdout; default: $$${EvaluationApp.OutputDirEnv}
       |""".stripMargin


  private def list(value: String): Seq[String] =
    value.split(",").map(_.trim).filter(_.nonEmpty).toSeq

  private[evaluation] def parseDateTime(value: String): Either[String, LocalDateTime] =
    Try(LocalDateTime.parse(value))
      .orElse(Try(LocalDate.parse(value).atStartOfDay))
      .toEither.left.map(_ => s"Invalid date '$value', expected e.g. 2026-01-01 or 2026-01-01T12:00")

  private def parseUseCases(value: String): Either[String, Set[UseCase.Value]] =
    list(value).foldLeft[Either[String, Set[UseCase.Value]]](Right(Set.empty)) { (acc, uc) =>
      acc.flatMap(set =>
        UseCase.values.find(_.toString.equalsIgnoreCase(uc))
          .toRight(s"Unknown use case '$uc', expected ${UseCase.values.mkString(" or ")}")
          .map(set + _)
      )
    }

  private def parseParam(value: String): Either[String, (String, String)] =
    value.split("=", 2) match {
      case Array(k, v) if k.trim.nonEmpty => Right(k.trim -> v.trim)
      case _                              => Left(s"Invalid parameter '$value', expected NAME=VALUE")
    }

  def parse(args: Seq[String]): Either[String, Command] =
    args match {
      case Seq() | Seq("help" | "--help" | "-h", _*) => Right(Help)
      case Seq("list")                               => Right(ListStatistics)
      case Seq("run", statistic, options @ _*) if !statistic.startsWith("-") =>
        parseRunOptions(splitAssignments(options), Run(statistic), Set.empty)
      case Seq("run", _*)                            => Left("Missing name of the statistic to run")
      case Seq(other, _*)                            => Left(s"Unknown command '$other'")
    }

  /** Allows "--option=value" besides "--option value" */
  private def splitAssignments(options: Seq[String]): List[String] =
    options.toList.flatMap {
      case opt if opt.startsWith("--") && opt.contains("=") =>
        val Array(name, value) = opt.split("=", 2)
        List(name, value)
      case other => List(other)
    }

  @tailrec
  private def parseRunOptions(options: List[String], run: Run, useCasesGiven: Set[UseCase.Value]): Either[String, Run] = {
    val f = run.filter
    options match {
      case Nil =>
        Right(if (useCasesGiven.isEmpty) run else run.copy(filter = f.copy(useCases = useCasesGiven)))
      case option :: Nil =>
        Left(s"Missing value of option '$option'")
      case option :: value :: rest =>
        val next: Either[String, (Run, Set[UseCase.Value])] =
          option match {
            case "--site"            => Right(run.copy(filter = f.copy(sites = f.sites ++ list(value))) -> useCasesGiven)
            case "--usecase"         => parseUseCases(value).map(ucs => run -> (useCasesGiven ++ ucs))
            case "--from"            => parseDateTime(value).map(d => run.copy(filter = f.copy(from = Some(d))) -> useCasesGiven)
            case "--until"           => parseDateTime(value).map(d => run.copy(filter = f.copy(until = Some(d))) -> useCasesGiven)
            case "--param"           => parseParam(value).map(p => run.copy(params = run.params + p) -> useCasesGiven)
            case "--key"             => Right(run.copy(keyPath = Some(value)) -> useCasesGiven)
            case "--passphrase-file" => Right(run.copy(passphraseFile = Some(value)) -> useCasesGiven)
            case "--mongodb-uri"     => Right(run.copy(mongoUri = Some(value)) -> useCasesGiven)
            case "--output"          => Right(run.copy(outputDir = Some(value)) -> useCasesGiven)
            case other               => Left(s"Unknown option '$other'")
          }
        next match {
          case Right((r, ucs)) => parseRunOptions(rest, r, ucs)
          case Left(err)       => Left(err)
        }
    }
  }
}
