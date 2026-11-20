import Foundation

extension Notification.Name {
    /// Posted by Settings after mutating sources, order, accents, or titles so
    /// the menu bar icon, popover, and widget update without waiting for the
    /// next background poll.
    static let headroomSnapshotDidUpdate = Notification.Name(
        "headroom.snapshotDidUpdate"
    )
}

@MainActor
final class UsageStore: ObservableObject {
    @Published private(set) var snapshot = UsageSnapshot.empty
    @Published private(set) var isRefreshing = false
    @Published private(set) var stoppingServerID: String?
    @Published private(set) var errorMessage: String?
    @Published private(set) var lastRefresh: Date?
    /// Set when launchd is serving a host older than the one in this .app.
    @Published private(set) var hostSkew: HostSkew?
    /// What the running host calls itself, for the Setup card.
    @Published private(set) var hostVersionLabel: String?
    @Published private(set) var isUpdatingHost = false
    /// Did /health answer on the last check?
    ///
    /// Separate from `errorMessage` on purpose. That one is the bucket every
    /// failed call empties into — a refused server stop, one flaky poll — and
    /// keying "show onboarding" off it swapped the whole dashboard for the
    /// setup sheet over things that had nothing to do with the host being down.
    @Published private(set) var hostReachable = true
    /// Attention rows dismissed in this popover. Matches the iPhone queue:
    /// clearing the list has to drop every place it is counted, including the
    /// mode switcher once that grows a badge. Pruned when the host stops
    /// reporting the row so a returning failure can light Attention again.
    @Published private(set) var dismissedAttentionIDs: Set<String> = []

    var onSnapshotChange: ((UsageSnapshot, Bool) -> Void)?

    /// Failed feed rows still waiting in Attention, minus local dismissals.
    var attentionFailures: [ActivityItem] {
        AttentionList.failures(in: snapshot)
            .filter { !dismissedAttentionIDs.contains($0.id) }
    }

    /// Rollup reasons with no concrete failure row, minus local dismissals.
    var attentionReasons: [AttentionReason] {
        AttentionList.leftoverReasons(in: snapshot)
            .filter { !dismissedAttentionIDs.contains($0.id) }
    }

    /// The popover is closed most of the time, and a closed popover only feeds
    /// three bars in the menu bar. Polling the active interval around the
    /// clock is battery spent on pixels nobody is looking at, so idle backs off
    /// to this and opening the popover refreshes immediately.
    private static let idleInterval: TimeInterval = 300
    private static let idleAfter: TimeInterval = 120
    /// Cadence while someone is actually looking. Hardcoded per
    /// docs/product.md: a poll interval is a tradeoff with a right answer,
    /// not a preference. It was briefly a Settings picker, which the three
    /// rules around it — retry backoff, the floor, the idle escalation —
    /// already overrode in every state but this one.
    private static let activeInterval: TimeInterval = 60
    private var refreshLoop: Task<Void, Never>?
    private var lastInteraction = Date()
    private var cadence = RefreshCadence()

    /// Who answered /health last. launchd hands the same port to whatever host
    /// it just started, so "still 200 on :8737" proves nothing — the build
    /// fingerprint and uptime are what tell us the process changed underneath.
    private var lastHostBuild: String?
    private var lastHostUptime: Int?

    /// Running builds we already tried to replace. If the reinstall doesn't
    /// take — a clone's LaunchAgent that keeps winning the port — this must not
    /// become a bootout/bootstrap loop every minute.
    private var autoUpdatedBuilds: Set<String> = []

    /// The one install in flight, if any. Launch, the poll loop, the setup card
    /// and the skew banner all reach for the same bootout/bootstrap; without a
    /// shared handle two of them tear down each other's host and whoever loses
    /// reads /usage from a process that is mid-restart.
    private var hostInstall: Task<HostController.Readiness, Never>?

    /// Multi-Mac transport, built lazily so a Mac with sync switched off never
    /// touches CloudKit — constructing a `CKContainer` on a machine with no
    /// iCloud account is harmless but pointless, and a CloudKit call on the
    /// launch path is not free.
    private var cloudSync: MachineCloudSync?
    private var cloudLoop: Task<Void, Never>?
    /// How often this Mac republishes and re-reads. The subscription is what
    /// makes a change arrive quickly; this is the floor under it for the case
    /// where the push never lands.
    private static let cloudInterval: TimeInterval = 120

    init() {}

    deinit {
        refreshLoop?.cancel()
        cloudLoop?.cancel()
    }

    private var client: HeadroomClient { HeadroomClient() }

    func start() {
        guard refreshLoop == nil else { return }
        refreshLoop = Task { [weak self] in
            guard let self else { return }
            await self.tick()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(self.nextInterval()))
                guard !Task.isCancelled else { return }
                await self.tick()
            }
        }
        startCloudSync()
    }

    /// Drive the multi-Mac transport, if the host has it switched on.
    ///
    /// Its own loop rather than a step in the poll: this reaches CloudKit,
    /// where a round trip can be slow or stalled, and the menu bar has no
    /// business waiting on another Mac's records. Asking the host each round
    /// whether sync is on keeps the switch in one place — Settings writes it
    /// there, and this notices without needing to be told.
    func startCloudSync() {
        // An unsigned build cannot reach CloudKit, and asking it to would take
        // the process down rather than fail — so it never starts the loop.
        guard MachineCloudSync.isAvailable, cloudLoop == nil else { return }
        cloudLoop = Task { [weak self] in
            while !Task.isCancelled {
                await self?.cloudRound()
                try? await Task.sleep(for: .seconds(Self.cloudInterval))
            }
        }
    }

    private func cloudRound() async {
        guard let config = try? await client.fetchMultiMacConfiguration(),
              config.enabled, config.mode == "cloudkit"
        else { return }
        guard let sync = cloudSync ?? MachineCloudSync(
            endpoint: HeadroomClient.currentEndpoint)
        else { return }
        cloudSync = sync
        await sync.subscribeIfNeeded()
        if case let .success(summary) = await sync.run(), summary.adopted > 0 {
            // Settings arriving from another Mac change what this one polls and
            // how it is painted, so take the document again rather than waiting
            // out the poll interval showing the old one.
            await refresh()
        }
    }

    /// Called when CloudKit says something changed. Cheap and idempotent.
    func cloudSyncNow() {
        Task { await cloudRound() }
    }

    /// One pass of the loop: ask who is answering before believing what it says.
    /// A host that restarted between polls gets a forced sync, so the popover
    /// stops showing the document the previous process left behind.
    private func tick() async {
        let restarted = await checkHostVersion()
        await refresh(forceSync: restarted)
    }

    /// Call when the user actually looks at the data — resumes the fast cadence.
    func noteInteraction() {
        lastInteraction = Date()
    }

    private func nextInterval() -> TimeInterval {
        // Nothing answered last time — the host is mid-restart, or launchd is
        // between agents. Sitting out a full minute (or five, when idle) leaves
        // dead meters long after it is back, so retry fast and back off.
        if let retry = cadence.retryInterval {
            return retry
        }
        let idleFor = Date().timeIntervalSince(lastInteraction)
        return idleFor > Self.idleAfter
            ? max(Self.activeInterval, Self.idleInterval)
            : Self.activeInterval
    }

    /// Apply a decoded snapshot without hitting the network (README exports).
    func applySnapshot(_ value: UsageSnapshot, healthy: Bool = true) {
        snapshot = value
        pruneDismissedAttention()
        lastRefresh = Date()
        errorMessage = healthy ? nil : "fixture"
        onSnapshotChange?(value, healthy)
    }

    /// - Parameter forceSync: Ask the host to re-poll every source before
    ///   reading `/usage`. Used after wake and by callers that want fresh
    ///   numbers, not just the last published document.
    func refresh(forceSync: Bool = false) async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        // Coming back from a failed poll: the host may be up again while its
        // sources are still sitting on pre-outage ages. A plain GET looks like
        // nothing happened — kick a sync so the dashboard actually moves.
        let recovering = errorMessage != nil
        do {
            var value = try await client.fetchUsage()
            snapshot = value
            errorMessage = nil
            // A served document is proof the host is up, without a second GET.
            hostReachable = true
            onSnapshotChange?(value, true)

            if recovering || forceSync {
                try? await client.refresh(sources: nil)
                await client.waitForRefresh(sources: nil)
                value = try await client.fetchUsage()
                snapshot = value
                onSnapshotChange?(value, true)
            }

            pruneDismissedAttention()
            lastRefresh = Date()
            cadence.noteSuccess()
            // Written once per successful pass, after any forced re-sync, so
            // the widget never picks up the pre-sync document. The Mac is the
            // source here — this cache is current, unlike the phone's.
            HeadroomWidgetCache.save(snapshot)
        } catch {
            cadence.noteFailure()
            errorMessage = error.localizedDescription
            onSnapshotChange?(snapshot, false)
        }
    }

    /// Same bulk action as iPhone **Dismiss all**: hide every Attention row
    /// here and ack the rollup so the menu-bar pip goes out with the list.
    func dismissAllAttention() async {
        var next = dismissedAttentionIDs
        next.formUnion(AttentionList.failures(in: snapshot).map(\.id))
        next.formUnion((snapshot.attention?.reasons ?? []).map(\.id))
        // Reassign so `@Published` fires — in-place Set mutation does not.
        dismissedAttentionIDs = next
        await acknowledgeAttention()
    }

    func acknowledgeAttention() async {
        guard let attention = snapshot.attention, attention.isWarning else {
            return
        }
        do {
            try await client.acknowledgeAttention(attention.fingerprint)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Drop dismissals for rows the host no longer reports, so a failure that
    /// comes back can come back.
    private func pruneDismissedAttention() {
        let live = Set(AttentionList.failures(in: snapshot).map(\.id))
            .union((snapshot.attention?.reasons ?? []).map(\.id))
        dismissedAttentionIDs = dismissedAttentionIDs.intersection(live)
    }

    /// Ask /health which host is actually answering; true when that is a
    /// different process than the one we last talked to.
    ///
    /// On the refresh path on purpose. An update landing underneath a running
    /// app — a reinstalled LaunchAgent, a Release .app replacing a clone's
    /// host — used to sit unnoticed until the next launch or wake, which is the
    /// whole window in which the popover renders fields the running host never
    /// learned to emit. It costs one loopback GET against a cached document.
    ///
    /// - Parameter autoUpdate: false from `updateHost()`, which is already the
    ///   update and must not stack a second install on the same skew.
    @discardableResult
    func checkHostVersion(autoUpdate: Bool = true) async -> Bool {
        guard let report = try? await client.health() else {
            hostReachable = false
            return false
        }
        hostReachable = true

        let restarted = (lastHostBuild != nil && report.build != lastHostBuild)
            || (lastHostUptime.map { (report.uptimeS ?? 0) < $0 } ?? false)
        lastHostBuild = report.build
        lastHostUptime = report.uptimeS

        hostSkew = HostController.skew(against: report)
        if let version = report.version {
            hostVersionLabel = report.build.map { "Host \(version) (\($0))" }
                ?? "Host \(version)"
        } else {
            hostVersionLabel = "Host predates version reporting"
        }
        if autoUpdate, let hostSkew {
            await installBundledHost(replacing: hostSkew)
        }
        return restarted
    }

    /// This .app ships a host and launchd is serving a different one. Replace it
    /// on sight rather than parking an offer behind a button: until someone
    /// clicks, every number on screen came out of a host this app can't fully
    /// decode. Once per running build — see `autoUpdatedBuilds`.
    ///
    /// Except when the running host is the newer half. Then this .app is what's
    /// behind, installing its copy would be a downgrade, and the banner says so
    /// instead.
    private func installBundledHost(replacing skew: HostSkew) async {
        guard !skew.hostIsNewer else { return }
        let key = skew.runningBuild ?? "pre-1.0"
        guard !autoUpdatedBuilds.contains(key) else { return }
        autoUpdatedBuilds.insert(key)
        await updateHost()
    }

    /// Make the host bundled in this .app the one that is running, under
    /// whichever `HostLifecycle` is selected. Same call as first-run setup.
    /// Under `.launchAgent` that is a launchctl bootout/bootstrap, which
    /// replaces whatever job was there, including one installed from a clone.
    /// Under `.appOwned` it removes that job and spawns a child instead.
    ///
    /// Serialized: a second caller awaits the install already running rather
    /// than starting its own or, worse, returning early and reading /usage
    /// from a host the first one is still restarting.
    @discardableResult
    func updateHost() async -> HostController.Readiness {
        if let hostInstall { return await hostInstall.value }
        let install = Task { await performHostInstall() }
        hostInstall = install
        let readiness = await install.value
        hostInstall = nil
        return readiness
    }

    private func performHostInstall() async -> HostController.Readiness {
        isUpdatingHost = true
        defer { isUpdatingHost = false }
        // Both lifecycles route through the coordinator, so every existing
        // caller — first-run setup, the skew banner, Settings → Start host —
        // keeps working without knowing which supervisor is in use, and cannot
        // interleave with the Settings toggle.
        let outcome = await HostLifecycleCoordinator.shared.apply(HostLifecycle.current)
        switch outcome.readiness {
        case .ready, .foreign:
            await checkHostVersion(autoUpdate: false)
            // The host it replaced published a document; a plain GET would hand
            // us that one back with its pre-restart ages.
            await refresh(forceSync: true)
        case .silent:
            hostReachable = false
            if let message = outcome.errorMessage { errorMessage = message }
        }
        return outcome.readiness
    }

    func stopServer(_ server: LocalServer) async {
        guard let pid = server.pid, let port = server.port,
              stoppingServerID == nil else { return }
        stoppingServerID = server.id
        defer { stoppingServerID = nil }

        do {
            try await client.stopServer(pid: pid, port: port)
            try? await Task.sleep(for: .milliseconds(300))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
