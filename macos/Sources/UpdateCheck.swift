import Foundation
import HouseKit
import Observation

/// Learning that a newer Headroom.app exists. See docs/updater.md.
///
/// This half only decides *whether* to offer one. The install is
/// `scripts/update-app.sh`, spawned detached, because the host runs from
/// inside the bundle under a KeepAlive LaunchAgent and the swap has to
/// bracket itself with bootout/bootstrap — an app cannot do that to itself
/// while it is the thing being replaced.
///
/// Everything that decides is a pure static below, so it can be tested
/// without a network or a bundle.

// MARK: - The feed document

/// `docs/latest.json`, as served from the update feed.
///
/// **Every field is optional and that is not laziness.** This decodes under
/// one `try`, so a single non-optional key missing fails the whole document —
/// the `fetchUsage()` trap, except here the cost is permanent: a build that
/// cannot read the feed is a build that can never be told about an update
/// again, and no later release can reach it to fix that. Decode loosely,
/// validate in `evaluate`.
///
/// Unknown keys are ignored by `Decodable`, which is what lets the feed grow.
struct UpdateFeed: Decodable, Equatable, Sendable {
    var schema: Int?
    var version: String?
    var published: String?
    var url: String?
    var sha256: String?
    var size: Int?
    var minMacOS: String?
    var notesMD: String?

    enum CodingKeys: String, CodingKey {
        case schema, version, published, url, sha256, size
        case minMacOS = "min_macos"
        case notesMD = "notes_md"
    }
}

/// A feed entry that survived validation and is worth showing someone.
struct AvailableUpdate: Equatable, Sendable {
    var version: String
    var url: URL
    var sha256: String?
    var notesMD: String?
}

// MARK: - The decisions

enum UpdateCheck {
    /// Where the app looks for a newer one.
    ///
    /// The hostname is ours and CNAMEs to GitHub Pages today, which is the
    /// point: every shipped build polls whatever URL it was compiled with,
    /// forever, and no later release can reach the old ones to change it. Only
    /// DNS can move this.
    static let defaultFeed = "https://updates.centaur-labs.io/latest.json"

    /// Overridable through `UserDefaults`, the same way `HeadroomClient` takes
    /// `usageEndpoint`, so a local build can be pointed at a test feed without
    /// editing Swift:
    ///
    ///     defaults write com.centaur-labs.headroom.macos updateFeedURL …
    ///
    /// Safe to leave writable because the feed is not what makes an update
    /// trustworthy: `update-app.sh` refuses anything not notarized under the
    /// Team ID it was compiled with, whatever URL pointed at it.
    static var feedURL: URL? {
        let raw = UserDefaults.standard.string(forKey: "updateFeedURL")
            ?? defaultFeed
        return URL(string: raw)
    }

    static var installedVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString")
            as? String ?? "0.0.0"
    }

    /// Whether this copy is allowed to replace itself.
    ///
    /// Only an app in `/Applications` may. A build running from a worktree,
    /// DerivedData or a Homebrew prefix must not: at best it offers to
    /// overwrite a copy that is not the one you are testing, at worst two
    /// update mechanisms fight over the same bundle. Homebrew installs update
    /// through `brew upgrade`, which is the same split CodexBar draws.
    static var canSelfUpdate: Bool {
        Bundle.main.bundleURL
            .resolvingSymlinksInPath()
            .deletingLastPathComponent()
            .standardizedFileURL.path == "/Applications"
    }

    static var currentOSVersion: String {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        return "\(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"
    }

    static func decode(_ data: Data) throws -> UpdateFeed {
        try JSONDecoder().decode(UpdateFeed.self, from: data)
    }

    /// The feed entry worth offering, or nil.
    ///
    /// Conservative at every branch. Declining to offer an update is a bad
    /// day; offering one that cannot install is a bad week, because it repeats
    /// until the next release.
    static func evaluate(
        feed: UpdateFeed,
        installed: String,
        osVersion: String
    ) -> AvailableUpdate? {
        guard let version = feed.version, !version.isEmpty,
              let raw = feed.url, let url = URL(string: raw)
        else { return nil }

        // The feed is not a trusted document — update-app.sh's notarization
        // and Team ID checks are what make it safe to follow. But https is
        // free to insist on, and a feed that has started naming http URLs is
        // one something has gone wrong with.
        guard url.scheme == "https" else { return nil }

        // nil from isNewer means one side was not dotted-numeric, which is not
        // the same as older. Only a definite yes offers.
        guard HostVersion.isNewer(version, than: installed) == true else {
            return nil
        }

        // An unparseable floor is treated as satisfied. The alternative is an
        // app that silently stops offering updates forever because one string
        // in the feed was malformed, and the OS refuses to launch a bundle it
        // is too old for anyway — this gate exists to be polite, not to be the
        // thing standing between the user and a broken install.
        if let floor = feed.minMacOS,
           HostVersion.isNewer(floor, than: osVersion) == true {
            return nil
        }

        return AvailableUpdate(
            version: version,
            url: url,
            sha256: feed.sha256,
            notesMD: feed.notesMD
        )
    }
}

// MARK: - Handing off to the script

/// Starts `update-app.sh` and steps out of its way.
///
/// The app does not download, verify or swap anything. It cannot: replacing a
/// bundle out from under mapped images is how you get an app that half-runs,
/// and the LaunchAgent would restart the old host from the old bundle anyway.
/// The script closes the app, waits for it to go, boots the agent out, copies,
/// restores the previous copy if the copy fails, and starts everything back
/// up. All this side does is name a version and a URL.
enum UpdateInstaller {
    enum Failure: LocalizedError {
        case scriptMissing

        var errorDescription: String? {
            switch self {
            case .scriptMissing:
                return "This build does not carry the updater."
            }
        }
    }

    /// `Contents/Resources/update-app.sh`, put there by `macos/project.yml`.
    static var scriptURL: URL? {
        Bundle.main.url(forResource: "update-app", withExtension: "sh")
    }

    /// Everything the script says goes here, because the app it is talking to
    /// will not be running to read it. Next to the host's own logs.
    static var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".headroom/logs/update.log")
    }

    static func install(_ update: AvailableUpdate) throws {
        guard let script = scriptURL else { throw Failure.scriptMissing }

        let logDirectory = logURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(
            at: logDirectory, withIntermediateDirectories: true)

        // Through `nohup … &` so the script outlives the app that started it.
        // A plain child would be fine on macOS, but this is a process whose
        // entire job happens *after* we quit, and inheriting our stdio means
        // its first write after we go lands on a closed pipe.
        //
        // /bin/bash by path rather than executing the script, so nothing
        // depends on the executable bit surviving the resource copy.
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = [
            "-c",
            #"nohup /bin/bash "$0" --yes --version "$1" --url "$2" >> "$3" 2>&1 &"#,
            script.path,
            update.version,
            update.url.absoluteString,
            logURL.path,
        ]
        try process.run()
    }
}

// MARK: - The poller

/// Fetches the feed and holds what it found.
///
/// Weekly, plus whenever someone asks. There is nothing to gain from checking
/// more often: releases are days apart at best, and this is a menu bar app
/// that should not be spending anyone's battery on a JSON file.
@MainActor
@Observable
final class UpdateChecker {
    /// One per app. The poll loop runs from `AppDelegate` whether or not
    /// Settings is open, and Settings has to show what that loop found rather
    /// than starting a second one that checks again on every visit.
    @ObservationIgnored static let shared = UpdateChecker()

    /// Off by default is wrong here — an update nobody hears about is the
    /// problem this exists to solve — but it stays a preference, because a Mac
    /// that manages its own software should be allowed to say so.
    @ObservationIgnored static let automaticKey = "automaticUpdateChecks"

    private(set) var available: AvailableUpdate?
    /// Version string from the last successful feed fetch, whether or not it
    /// is newer than this copy. Settings always shows it next to Current;
    /// `available` is only set when that version is worth installing.
    private(set) var latestVersion: String?
    private(set) var lastChecked: Date?
    private(set) var lastError: String?
    private(set) var isChecking = false

    @ObservationIgnored private static let interval: TimeInterval = 7 * 24 * 60 * 60

    @ObservationIgnored private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    /// Stored, not read from defaults on each access, so the Settings toggle
    /// redraws when it changes. The defaults key is the same one older
    /// builds wrote through `@AppStorage`.
    var automaticChecksEnabled: Bool =
        UserDefaults.standard.object(forKey: UpdateChecker.automaticKey) as? Bool ?? true {
        didSet {
            UserDefaults.standard.set(automaticChecksEnabled, forKey: Self.automaticKey)
        }
    }

    /// Poll loop. Sleeps first so launch is never slowed by a network call
    /// nobody asked for.
    func runPeriodically() async {
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(60))
            guard !Task.isCancelled else { return }
            guard automaticChecksEnabled, UpdateCheck.canSelfUpdate else {
                try? await Task.sleep(for: .seconds(Self.interval))
                continue
            }
            if let last = lastChecked, Date().timeIntervalSince(last) < Self.interval {
                try? await Task.sleep(for: .seconds(3600))
                continue
            }
            await check()
            try? await Task.sleep(for: .seconds(Self.interval))
        }
    }

    /// A manual check runs even when this copy cannot install what it finds —
    /// telling someone on a Homebrew build that 1.5.0 exists is useful; doing
    /// it silently behind their back is not.
    @discardableResult
    func check() async -> AvailableUpdate? {
        guard let url = UpdateCheck.feedURL else {
            lastError = "This build has no update feed configured."
            return nil
        }
        isChecking = true
        defer { isChecking = false }

        do {
            var request = URLRequest(url: url)
            // The feed is small and Pages caches for minutes; a manual check
            // that returns a cached answer looks broken.
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 20

            let (data, response) = try await session.data(for: request)
            if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                lastError = "The update feed answered \(http.statusCode)."
                return nil
            }

            let feed = try UpdateCheck.decode(data)
            lastChecked = Date()
            lastError = nil
            // Keep whatever the feed named even when evaluate declines to
            // offer it — Settings lists Current and Latest side by side, and
            // "Up to date" alone used to hide both numbers.
            if let version = feed.version, !version.isEmpty {
                latestVersion = version
            }
            available = UpdateCheck.evaluate(
                feed: feed,
                installed: UpdateCheck.installedVersion,
                osVersion: UpdateCheck.currentOSVersion
            )
            return available
        } catch {
            lastError = error.localizedDescription
            return nil
        }
    }
}

// MARK: - Settings

/// The house Updates section (HouseKit `UpdatesSection`) reads Headroom's
/// checker through this.
extension UpdateChecker: UpdateChecking {
    var installedVersion: String { UpdateCheck.installedVersion }
    var availableVersion: String? { available?.version }
    var automaticChecks: Bool {
        get { automaticChecksEnabled }
        set { automaticChecksEnabled = newValue }
    }
    var canInstall: Bool { UpdateCheck.canSelfUpdate }
    var policyNote: String {
        "Headroom looks weekly for a newer notarized build, and asks before installing one."
    }
    func checkForUpdates() async { await check() }
    func installUpdate() throws {
        guard let available else { return }
        try UpdateInstaller.install(available)
    }
}
