import Foundation

/// Who owns the host process.
///
/// Both modes run the same `host/headroom_server.py` on the same port. The
/// only difference is the supervisor, and what a quit means.
enum HostLifecycle: String, CaseIterable, Sendable {
    /// launchd owns it. It starts at login, survives quitting Headroom, and
    /// comes back on a crash. The default, and what every install before this
    /// setting existed already had.
    case launchAgent
    /// This app owns it. It starts with the app and dies with the app.
    ///
    /// Quitting Headroom takes the board, the phone and the watch with it.
    /// That is the point of the mode, not a defect of it.
    case appOwned

    static let defaultsKey = "hostLifecycle"

    /// Absent and unrecognised both resolve to the default.
    ///
    /// A mode written by a newer build must not strand an older one on a
    /// lifecycle it has no code for. It would stop supervising the host
    /// entirely and the failure would read as a dead board.
    static func resolve(_ raw: String?) -> HostLifecycle {
        guard let raw, let mode = HostLifecycle(rawValue: raw) else {
            return .launchAgent
        }
        return mode
    }

    static var current: HostLifecycle {
        resolve(UserDefaults.standard.string(forKey: defaultsKey))
    }

    static func store(_ mode: HostLifecycle) {
        UserDefaults.standard.set(mode.rawValue, forKey: defaultsKey)
    }
}

/// The host as a child process of this app, for `HostLifecycle.appOwned`.
///
/// Two mechanisms stop this child, and both are needed. `applicationWillTerminate`
/// sends SIGTERM, which is immediate and clean. `--exit-with-pid` is the one
/// that catches a crash or a force quit, where no termination handler runs at
/// all — see `host/parent_watch.py` for why an orphan is worse than a leak.
final class HostProcess: @unchecked Sendable {
    static let shared = HostProcess()

    /// Restarts allowed before this gives up and reports.
    ///
    /// 1.9.3 is the reason there is a budget at all. It exited non-zero at
    /// startup, and `KeepAlive` returned it every `ThrottleInterval` until the
    /// user uninstalled the app, each respawn rescanning a week of logs.
    /// Supervision that never gives up converts one bug into that loop.
    static let maxRestarts = 4
    /// A child that ran this long before dying was working, so the next death
    /// starts a fresh budget rather than counting toward an old streak.
    static let settledAfterS: TimeInterval = 60

    private let lock = NSLock()
    private let queue = DispatchQueue(label: "com.centaur-labs.headroom.host-process")
    private var child: Process?
    private var startedAt: Date?
    private var restarts = 0
    private var stopping = false
    private var port = HostController.defaultPort
    /// The staged server the child runs. A restart reuses it rather than
    /// staging again from a bundle that Sparkle may be replacing.
    private var server: URL?
    private var failure: String?

    var isRunning: Bool { lock.withLock { child?.isRunning ?? false } }

    /// Why the child is not coming back, or nil while it is healthy.
    var lastFailure: String? { lock.withLock { failure } }

    // MARK: - Start and stop

    @discardableResult
    func start(port: Int = HostController.defaultPort) throws -> Int32 {
        let server = try HostController.stagedServer()
        lock.lock()
        if let child, child.isRunning {
            lock.unlock()
            return child.processIdentifier
        }
        stopping = false
        restarts = 0
        failure = nil
        self.port = port
        self.server = server
        lock.unlock()
        let pid = try spawn(server: server, port: port)
        HostController.pruneStagedHosts(keeping: server)
        return pid
    }

    /// SIGTERM and return. Does not wait, because the caller is usually
    /// `applicationWillTerminate` and the app is about to go away regardless.
    func stop() {
        lock.lock()
        stopping = true
        let running = child
        child = nil
        startedAt = nil
        lock.unlock()
        guard let running, running.isRunning else { return }
        // The host handles SIGTERM by skipping finalize, so this is clean even
        // with daemon threads mid-print.
        running.terminate()
    }

    /// Stop, then wait for the process to actually go.
    ///
    /// Switching back to the LaunchAgent needs this. launchd's host binds
    /// :8737 on start, and ours exits 0 on purpose when the port is taken. A
    /// handover that does not wait leaves nothing running, and `KeepAlive` with
    /// `SuccessfulExit: false` will not bring the loser back.
    func stopAndWait(timeout: TimeInterval = 5) async {
        stop()
        let deadline = Date().addingTimeInterval(timeout)
        while isRunning, Date() < deadline {
            try? await Task.sleep(for: .milliseconds(100))
        }
    }

    // MARK: - Supervision

    private func spawn(server: URL, port: Int) throws -> Int32 {
        HostController.seedConfigIfNeeded()
        let headroom = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".headroom", isDirectory: true)
        let logs = headroom.appendingPathComponent("logs", isDirectory: true)
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)

        let process = Process()
        process.executableURL = HostController.pythonURL
        process.arguments = [
            server.path,
            "--port", "\(port)",
            "--exit-with-pid", "\(ProcessInfo.processInfo.processIdentifier)",
        ]
        process.currentDirectoryURL = headroom
        // Inherit, then pin PATH to what the LaunchAgent plist supplies. The
        // scrapers shell out to git, gh and lsof, and an app launched from
        // Finder does not get a login shell's PATH.
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment[HeadroomUSB.environmentKey] = HeadroomUSB.isEnabled ? "1" : "0"
        process.environment = environment
        // The same two files launchd redirects to, so docs/troubleshooting.md
        // stays true in both modes.
        process.standardOutput = Self.appendHandle(logs.appendingPathComponent("headroom.log"))
        process.standardError = Self.appendHandle(logs.appendingPathComponent("headroom.err"))
        process.terminationHandler = { [weak self] finished in
            self?.childExited(
                status: finished.terminationStatus, reason: finished.terminationReason)
        }

        try process.run()
        lock.withLock {
            child = process
            startedAt = Date()
        }
        return process.processIdentifier
    }

    private func childExited(status: Int32, reason: Process.TerminationReason) {
        lock.lock()
        let deliberate = stopping
        let lived = startedAt.map { Date().timeIntervalSince($0) } ?? 0
        child = nil
        startedAt = nil

        if deliberate {
            lock.unlock()
            return
        }

        // A clean exit is never restarted, for two reasons that both want the
        // same answer. The host exits 0 on purpose when a foreign process
        // already owns :8737, and restarting into a taken port is the loop
        // described above. And a user who runs `kill` on the host asked for it
        // to stop, which is the whole reason this mode exists.
        if reason == .exit, status == 0 {
            failure = HeadroomCopy.hostStoppedCleanly
            lock.unlock()
            return
        }

        if lived > Self.settledAfterS { restarts = 0 }
        guard restarts < Self.maxRestarts else {
            failure = HeadroomCopy.hostGaveUp
            lock.unlock()
            return
        }
        restarts += 1
        // 1s, 2s, 4s, 8s.
        let delay = pow(2.0, Double(restarts - 1))
        lock.unlock()

        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.restartAfterFailure()
        }
    }

    private func restartAfterFailure() {
        lock.lock()
        let abandoned = stopping
        let port = self.port
        let server = self.server
        lock.unlock()
        guard !abandoned, let server else { return }
        _ = try? spawn(server: server, port: port)
    }

    private static func appendHandle(_ url: URL) -> FileHandle {
        let fm = FileManager.default
        if !fm.fileExists(atPath: url.path) {
            fm.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else {
            return FileHandle.nullDevice
        }
        _ = try? handle.seekToEnd()
        return handle
    }
}

/// One lifecycle change at a time, whoever asks for it.
///
/// Settings can flip the toggle while the poll loop's version check is already
/// reinstalling the host. Both reach for the same port and the same plist, and
/// two of them interleaved is how you end up with no host at all. The second
/// caller awaits the first instead of starting its own.
actor HostLifecycleCoordinator {
    static let shared = HostLifecycleCoordinator()

    struct Outcome: Sendable {
        var readiness: HostController.Readiness
        var errorMessage: String?
    }

    private var inFlight: Task<Outcome, Never>?

    func apply(
        _ mode: HostLifecycle, port: Int = HostController.defaultPort
    ) async -> Outcome {
        if let inFlight { return await inFlight.value }
        let task = Task { await Self.run(mode, port: port) }
        inFlight = task
        let outcome = await task.value
        inFlight = nil
        return outcome
    }

    private static func run(_ mode: HostLifecycle, port: Int) async -> Outcome {
        do {
            try await HostController.applyLifecycle(mode, port: port)
        } catch {
            return Outcome(readiness: .silent, errorMessage: error.localizedDescription)
        }
        // Wait for the host we just started, not for anything that answers.
        let readiness = await HostController.waitUntilReady(
            port: port, expecting: HostController.bundledBuild)
        return Outcome(
            readiness: readiness, errorMessage: HostProcess.shared.lastFailure)
    }
}

// MARK: - Switching owners

extension HostController {
    /// Make the current lifecycle the one that is actually running.
    ///
    /// Always stops the other owner first and waits for :8737 to go quiet. Both
    /// directions have the same race: the incoming host binds the port on
    /// start, and whichever one loses exits 0 rather than fighting. AGENTS.md
    /// documents the launchd half of this as `install-host.sh` racing its own
    /// `bootout`.
    static func applyLifecycle(
        _ mode: HostLifecycle, port: Int = defaultPort
    ) async throws {
        switch mode {
        case .launchAgent:
            await HostProcess.shared.stopAndWait()
            await waitUntilPortIsFree(port: port)
            try installAndStart(port: port)
        case .appOwned:
            uninstall()
            await waitUntilPortIsFree(port: port)
            try HostProcess.shared.start(port: port)
        }
    }

    /// Wait for the outgoing host to stop answering, up to `attempts` polls.
    ///
    /// Returns either way. A port still busy at the end is a foreign host, and
    /// `waitUntilReady(expecting:)` is what reports that to the UI.
    static func waitUntilPortIsFree(port: Int = defaultPort, attempts: Int = 20) async {
        for _ in 0..<attempts {
            if await !isReachable(port: port) { return }
            try? await Task.sleep(for: .milliseconds(250))
        }
    }
}
