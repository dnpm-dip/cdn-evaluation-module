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
      dependencies.mtb_generators,
      dependencies.rd_generators,
      dependencies.icd10gm,
      dependencies.icdo3,
      dependencies.icd_catalogs,
      dependencies.atc_impl,
      dependencies.atc_catalogs,
      dependencies.hgnc_geneset,
      dependencies.hpo,
      dependencies.alpha_id_se,
      dependencies.orphanet,
      dependencies.logback,
      dependencies.service_base,
      dependencies.mtb_dto_model,
      dependencies.rd_dto_model,
      dependencies.bfarm_onco,
      dependencies.bfarm_rd,
      dependencies.mongodb_driver,
      dependencies.jsr305
    ),
    assembly / assemblyJarName := "dnpm-ccdn-evaluation.jar",
    assembly / mainClass       := Some("de.dnpm.ccdn.evaluation.EvaluationApp")
  )


//-----------------------------------------------------------------------------
// DEPENDENCIES
//-----------------------------------------------------------------------------

// Kept in line with the versions of the central-data-node, whose backups are evaluated, except for
// service-base, mtb-dto-model and rd-dto-model, raised to the versions bfarm-schema-dto-model requires
lazy val dependencies =
  new {
    val scalatest      = "org.scalatest"     %% "scalatest"               % "3.2.18" % Test
    // Test-only: random patient records for the Datenkranz export (rd-dto-generators 1.2.2 isn't resolvable),
    // and the code systems they draw from, as in the tests of bfarm-schema-dto-model
    val mtb_generators = "de.dnpm.dip"       %% "mtb-dto-generators"      % "1.2.4" % Test
    val rd_generators  = "de.dnpm.dip"       %% "rd-dto-generators"       % "1.2.1" % Test
    val icd10gm        = "de.dnpm.dip"       %% "icd10gm-impl"            % "1.1.5" % Test
    val icdo3          = "de.dnpm.dip"       %% "icdo3-impl"              % "1.1.5" % Test
    val icd_catalogs   = "de.dnpm.dip"       %% "icd-claml-packaged"      % "1.1.5" % Test
    val atc_impl       = "de.dnpm.dip"       %% "atc-impl"                % "1.1.4" % Test
    val atc_catalogs   = "de.dnpm.dip"       %% "atc-catalogs-packaged"   % "1.1.4" % Test
    val hgnc_geneset   = "de.dnpm.dip"       %% "hgnc-gene-set-impl"      % "1.1.4" % Test
    val hpo            = "de.dnpm.dip"       %% "hp-ontology"             % "1.2.2" % Test
    val alpha_id_se    = "de.dnpm.dip"       %% "alpha-id-se"             % "1.2.2" % Test
    val orphanet       = "de.dnpm.dip"       %% "orphanet-ordo"           % "1.2.2" % Test
    val logback        = "ch.qos.logback"    %  "logback-classic"         % "1.5.18"
    val service_base   = "de.dnpm.dip"       %% "service-base"            % "1.5.4"
    val mtb_dto_model  = "de.dnpm.dip"       %% "mtb-dto-model"           % "1.2.4"
    val rd_dto_model   = "de.dnpm.dip"       %% "rd-dto-model"            % "1.2.2"
    // Mappings to the BfArM "Datenkranz" schemata, see https://github.com/dnpm-dip/bfarm-schema-dto-model
    val bfarm_onco     = "de.dnpm"           %% "dnpm-bfarm-model-oncology"     % "1.0.7"
    val bfarm_rd       = "de.dnpm"           %% "dnpm-bfarm-model-rare-disease" % "1.0.6"
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
