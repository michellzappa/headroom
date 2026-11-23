import HouseKitSparkle

/// Headroom's updater: Sparkle through HouseKit (`SparkleUpdater`), like the
/// other Mac apps. The feed is the appcast on the latest GitHub Release
/// (Info.plist `SUFeedURL`), signed with Headroom's EdDSA key by
/// `.github/workflows/release.yml`. Sparkle checks once a day, verifies the
/// EdDSA signature and Apple's code signature, installs and relaunches.
///
/// Sparkle can replace the bundle while the host runs, because the host runs
/// from a staged copy outside the bundle (`HostController.stagedServer`). See
/// docs/updater.md.
///
/// Off for copies outside /Applications: a build from Xcode, a worktree or the
/// mini never overwrites the installed app.
@MainActor
enum Updates {
    static let shared = SparkleUpdater(appName: "Headroom")
}
