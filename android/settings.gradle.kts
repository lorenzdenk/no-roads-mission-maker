pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal(); maven("https://chaquo.com/maven") }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories { google(); mavenCentral() }
}
rootProject.name = "NoRoadsMissionMaker"
include(":app")
