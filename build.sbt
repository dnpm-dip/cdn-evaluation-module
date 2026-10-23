import scala.util.Properties.envOrElse


name := "dnpm-ccdn-evaluation"  // Statistical evaluations on the backups of the Central Clinical Data Node
ThisBuild / organization := "de.dnpm"
ThisBuild / scalaVersion := "2.13.18"
ThisBuild / version      := envOrElse("VERSION","0.1.0")

val ownerRepo  = envOrElse("REPOSITORY","dnpm-dip/cdn-evaluation-module").split("/")
ThisBuild / githubOwner      := ownerRepo(0)
ThisBuild / githubRepository := ownerRepo(1)


ThisBuild / assemblyMergeStrategy := {
  case PathList("META-INF", "services", xs @ _*) => MergeStrategy.first
  case PathList("META-INF", xs @ _*)             => MergeStrategy.discard
  case "reference.conf"                          => MergeStrategy.concat
  case _                                         => MergeStrategy.last
}


//-----------------------------------------------------------------------------
// PROJECT
//-----------------------------------------------------------------------------

lazy val evaluation = project
  .in(file("."))
  .settings(
    settings,
    libraryDependencies ++= Seq(
      dependencies.scalatest,
      dependencies.logback,
      dependencies.service_base,
      dependencies.mtb_dto_model,
      dependencies.rd_dto_model,
      dependencies.mongodb_driver,
      dependencies.jsr305
    ),
    assembly / assemblyJarName := "dnpm-ccdn-evaluation.jar",
    assembly / mainClass       := Some("de.dnpm.ccdn.evaluation.EvaluationApp")
  )


//-----------------------------------------------------------------------------
// DEPENDENCIES
//-----------------------------------------------------------------------------

// Kept in line with the versions of the central-data-node, whose backups are evaluated
lazy val dependencies =
  new {
    val scalatest      = "org.scalatest"     %% "scalatest"               % "3.2.18" % Test
    val logback        = "ch.qos.logback"    %  "logback-classic"         % "1.5.18"
    val service_base   = "de.dnpm.dip"       %% "service-base"            % "1.5.0"
    val mtb_dto_model  = "de.dnpm.dip"       %% "mtb-dto-model"           % "1.2.3"
    val rd_dto_model   = "de.dnpm.dip"       %% "rd-dto-model"            % "1.2.1"
    val mongodb_driver = "org.mongodb" % "mongodb-driver-sync" % "5.3.0"
    // Compile-only: resolves JSR-305 annotations used by the MongoDB driver (e.g. com.mongodb.lang.Nullable), which results in a compiler warning
    val jsr305         = "com.google.code.findbugs" % "jsr305" % "3.0.2" % Provided
  }


//-----------------------------------------------------------------------------
// SETTINGS
//-----------------------------------------------------------------------------

lazy val settings = commonSettings

// Compiler options from: https://alexn.org/blog/2020/05/26/scala-fatal-warnings/
lazy val compilerOptions = Seq(
  // Feature options
  "-encoding", "utf-8",
  "-explaintypes",
  "-feature",
  "-language:existentials",
  "-language:experimental.macros",
  "-language:higherKinds",
  "-language:implicitConversions",
  "-language:postfixOps",
  "-Ymacro-annotations",

  // Warnings as errors!
  "-Xfatal-warnings",

  // Linting options
  "-unchecked",
  "-Xcheckinit",
  "-Xlint:adapted-args",
  "-Xlint:constant",
  "-Xlint:delayedinit-select",
  "-Xlint:deprecation",
  "-Xlint:doc-detached",
  "-Xlint:inaccessible",
  "-Xlint:infer-any",
  "-Xlint:missing-interpolator",
  "-Xlint:nullary-unit",
  "-Xlint:option-implicit",
  "-Xlint:package-object-classes",
  "-Xlint:poly-implicit-overload",
  "-Xlint:private-shadow",
  "-Xlint:stars-align",
  "-Xlint:type-parameter-shadow",
  "-Wdead-code",
  "-Wextra-implicit",
  "-Wnumeric-widen",
  "-Wunused:imports",
  "-Wunused:locals",
  "-Wunused:patvars",
  "-Wunused:privates",
  "-Wunused:implicits",
  "-Wvalue-discard",
)


lazy val commonSettings = Seq(
  scalacOptions ++= compilerOptions,
  resolvers ++= Seq(
    "Local Maven Repository" at "file://" + Path.userHome.absolutePath + "/.m2/repository",
    Resolver.githubPackages("dnpm-dip"),
    Resolver.githubPackages("KohlbacherLab"),
    Resolver.sonatypeCentralSnapshots
  )
)
