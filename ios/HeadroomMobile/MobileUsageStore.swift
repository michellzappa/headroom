import Foundation
import LocalAuthentication

@MainActor
final class MobileUsageStore: ObservableObject {
    @Published private(set) var snapshot = UsageSnapshot.empty
    @Published private(set) var isLoading = false
    @Published private(set) var stoppingServerID: String?
    @Published private(set) var changingSourceID: String?
    @Published private(set) var mobilePermissions = MobilePermissions.allDisabled
    @Published private(set) var agentAttentionEvents: [AgentAttentionEvent] = []
    @Published private(set) var respondingAgentEventID: String?
    @Published private(set) var errorMessage: String?
    /// When the Mac handed us the snapshot on screen — live this session, or
    /// read back from the archive on a cold launch.
    @Published private(set) var capturedAt: Date?
    /// The snapshot on screen came off disk; nothing has reached the Mac yet.
    @Published private(set) var isShowingArchive = false
    /// Attention rows swiped away on this phone. A queue only works if clearing
    /// a row clears it everywhere it is counted, so this lives here rather than
    /// in the screen's `@State` — the tab badge reads it too.
    @Published private(set) var dismissedAttentionIDs: Set<String> = []

    /// Foreground poll. The Mac restarts its host on its own schedule — an
    /// update reinstalling the LaunchAgent, a wake, a launchctl kickstart — and
    /// an app that only fetches when it comes to the front keeps drawing the
    /// pre-restart document until someone pulls to refresh.
    private var liveLoop: Task<Void, Never>?
    private var agentLiveLoop: Task<Void, Never>?
    private var cadence = RefreshCadence()

    private static let liveInterval: TimeInterval = 60
    /// Agent questions are time-sensitive; keep this separate from the full
    /// usage poll so answering from the phone does not make every quota source
    /// run once per few seconds.
    private static let agentLiveInterval: TimeInterval = 5

    init() {
        if let events = MobileAgentAttentionArchive.load() {
            agentAttentionEvents = events
        }
        guard let entry = MobileSnapshotArchive.load() else { return }
        snapshot = entry.snapshot
        capturedAt = entry.capturedAt
        isShowingArchive = true
    }

    var isConfigured: Bool { MobileConnection.isConfigured }

    var visibleProviders: [QuotaProviderInfo] {
        snapshot.codingQuotaProviders
    }

    /// Feed rows that failed, minus anything already swiped away here.
    var attentionFailures: [ActivityItem] {
        AttentionScreen.failures(in: snapshot)
            .filter { !dismissedAttentionIDs.contains($0.id) }
    }

    /// The rollup's own reasons. Once concrete failure rows exist those rows
    /// are the list, and the summary is intentionally omitted rather than
    /// reporting one broken build twice.
    var attentionReasons: [AttentionReason] {
        AttentionList.leftoverReasons(in: snapshot)
            .filter { !dismissedAttentionIDs.contains($0.id) }
    }

    func dismissAttention(id: String) {
        dismissedAttentionIDs.insert(id)
    }

    /// The section header's bulk action. Clears both kinds of row and tells the
    /// Mac, so the pip there goes out with the list here.
    func dismissAllAttention() async {
        dismissedAttentionIDs.formUnion(
            AttentionScreen.failures(in: snapshot).map(\.id))
        dismissedAttentionIDs.formUnion(
            (snapshot.attention?.reasons ?? []).map(\.id))
        await acknowledgeAttention()
    }

    /// Forget dismissals for rows the Mac no longer reports. Keeps the set from
    /// growing forever, and lets a failure that comes back come back.
    private func pruneDismissedAttention() {
        let live = Set(AttentionScreen.failures(in: snapshot).map(\.id))
            .union((snapshot.attention?.reasons ?? []).map(\.id))
        dismissedAttentionIDs.formIntersection(live)
    }

    /// True once we are drawing numbers the Mac has not confirmed this session,
    /// whether that is a cold launch with the Mac asleep or a refresh that
    /// failed after one succeeded.
    var isStale: Bool { isShowingArchive || errorMessage != nil }

    /// Whether there is anything real on screen, live or archived.
    var hasSnapshot: Bool { capturedAt != nil }

    /// How old the numbers on screen are, for the copy that says so.
    var age: TimeInterval? {
        capturedAt.map { Date().timeIntervalSince($0) }
    }

    /// Poll while the app is on screen. Idempotent — scenePhase can hand us
    /// `.active` more than once for the same visit.
    func startLiveUpdates() {
        guard liveLoop == nil, isConfigured else { return }
        liveLoop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                try? await Task.sleep(for: .seconds(self.nextInterval()))
                guard !Task.isCancelled else { return }
                await self.refresh()
            }
        }
        agentLiveLoop = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(
                    for: .seconds(Self.agentLiveInterval))
                guard !Task.isCancelled else { return }
                await self?.refreshAgentAttention()
            }
        }
    }

    func stopLiveUpdates() {
        liveLoop?.cancel()
        liveLoop = nil
        agentLiveLoop?.cancel()
        agentLiveLoop = nil
    }

    /// Fast foreground-only path for a question or approval. This is kept
    /// independent of `/usage`: a broken quota provider must not delay a
    /// response to an agent, and the full snapshot does not need five-second
    /// polling.
    private func refreshAgentAttention() async {
        guard isConfigured, mobilePermissions.agents else { return }
        let client = MobileHeadroomClient(
            endpoint: MobileConnection.endpoint,
            token: MobileTokenStore.read() ?? ""
        )
        guard let events = try? await client.fetchAgentAttentionEvents()
        else { return }
        applyAgentAttentionEvents(events)
        await MobileNotifications.notifyIfNeeded(events)
    }

    /// Keep memory and the on-disk archive in lockstep so a cold launch does
    /// not resurrect a row the person already answered this session.
    private func applyAgentAttentionEvents(_ events: [AgentAttentionEvent]) {
        agentAttentionEvents = events
        MobileAgentAttentionArchive.save(events)
    }

    private func nextInterval() -> TimeInterval {
        // Lost the Mac: most often it is restarting its host, which takes
        // seconds. Come back on that scale rather than a minute, then back off
        // so a phone left open on a sleeping Mac isn't retrying all evening.
        cadence.retryInterval ?? Self.liveInterval
    }

    func refresh(forceServerSync: Bool = false) async {
        guard !isLoading, isConfigured else { return }
        isLoading = true
        defer { isLoading = false }

        // Stale / archived / errored: a plain GET can succeed with the same
        // pre-outage document and look like nothing happened. Force sources
        // when recovering so Connected lands with fresh meters.
        let recovering = isStale
        let client = MobileHeadroomClient(
            endpoint: MobileConnection.endpoint,
            token: MobileTokenStore.read() ?? ""
        )
        do {
            if let permissions = try? await client.fetchMobilePermissions() {
                mobilePermissions = permissions
            }
            if mobilePermissions.read,
               let events = try? await client.fetchAgentAttentionEvents() {
                applyAgentAttentionEvents(events)
                await MobileNotifications.notifyIfNeeded(events)
            }
            if forceServerSync || recovering {
                if mobilePermissions.refresh {
                    try await client.requestRefresh()
                    await client.waitForRefresh()
                }
            }
            snapshot = try await client.fetchAndArchiveUsage()
            pruneDismissedAttention()
            errorMessage = nil
            capturedAt = Date()
            isShowingArchive = false
            cadence.noteSuccess()
            // Same bytes to both caches: the phone's widget group, and — over
            // WatchConnectivity — the watch's, which no other surface can fill.
            WatchBridge.shared.push(HeadroomWidgetCache.save(snapshot))
            await MobileNotifications.notifyIfNeeded(snapshot.attention)
            await MobileNotifications.notifyIfNeeded(resets: snapshot)
        } catch {
            // Keep whatever is on screen. Losing a week of burndown because the
            // Mac went to sleep is worse than showing it with its age attached.
            cadence.noteFailure()
            errorMessage = error.localizedDescription
        }
    }

    func configured() async {
        objectWillChange.send()
        // Pairing is the first moment `startLiveUpdates` has an endpoint to
        // poll; the one at launch bailed on `isConfigured`.
        startLiveUpdates()
        await refresh()
    }

    func answer(
        _ event: AgentAttentionEvent,
        with action: AgentAttentionAction
    ) async {
        guard mobilePermissions.agents,
              !isStale,
              respondingAgentEventID == nil else { return }
        respondingAgentEventID = event.id
        defer { respondingAgentEventID = nil }
        do {
            if action.requiresBiometric == true {
                let context = LAContext()
                try await context.evaluatePolicy(
                    .deviceOwnerAuthentication,
                    localizedReason: "\(action.label): \(event.summary)"
                )
            }
            let client = MobileHeadroomClient(
                endpoint: MobileConnection.endpoint,
                token: MobileTokenStore.read() ?? ""
            )
            let updated = try await client.respond(
                to: event,
                action: action,
                idempotencyKey: UUID().uuidString
            )
            applyAgentAttentionEvents(
                agentAttentionEvents.filter { $0.id != updated.id })
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            if let events = try? await MobileHeadroomClient(
                endpoint: MobileConnection.endpoint,
                token: MobileTokenStore.read() ?? ""
            ).fetchAgentAttentionEvents() {
                applyAgentAttentionEvents(events)
            }
        }
    }

    /// Clears only passive agent notices. Requests with a real answer remain
    /// untouched, even when the user chooses the bulk action.
    func dismissAllAgentNotices() async {
        let notices = agentAttentionEvents.filter(\.isDismissOnly)
        for event in notices {
            guard let dismiss = event.actions.first(where: { $0.id == "dismiss" })
            else { continue }
            await answer(event, with: dismiss)
        }
    }

    /// Fixture path for README / marketing screenshots (no network).
    func applySnapshot(_ value: UsageSnapshot) {
        snapshot = value
        errorMessage = nil
        capturedAt = Date()
        isShowingArchive = false
        mobilePermissions = .allEnabled
        isLoading = false
    }

    /// Called when the connection is re-pointed. The archive describes the Mac
    /// we just stopped talking to, so it goes with it.
    func forgetArchive() {
        MobileSnapshotArchive.clear()
        MobileAgentAttentionArchive.clear()
        snapshot = .empty
        agentAttentionEvents = []
        capturedAt = nil
        isShowingArchive = false
    }

    func setSource(_ id: String, enabled: Bool) async {
        guard changingSourceID == nil, mobilePermissions.sources else { return }
        changingSourceID = id
        defer { changingSourceID = nil }
        do {
            let client = mobileClient
            _ = try await client.setSources([id: enabled])
            await refresh(forceServerSync: true)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setServicesOrder(_ order: [String]) async {
        guard mobilePermissions.sources else { return }
        do {
            _ = try await mobileClient.setServicesOrder(order)
            await refresh(forceServerSync: true)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func acknowledgeAttention() async {
        guard let attention = snapshot.attention, attention.isWarning,
              mobilePermissions.read else { return }
        do {
            try await mobileClient.acknowledgeAttention(attention.fingerprint)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func stopServer(_ server: LocalServer) async {
        guard let pid = server.pid, let port = server.port,
              stoppingServerID == nil, mobilePermissions.servers else { return }
        stoppingServerID = server.id
        defer { stoppingServerID = nil }
        do {
            try await mobileClient.stopServer(pid: pid, port: port)
            try? await Task.sleep(for: .milliseconds(300))
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private var mobileClient: MobileHeadroomClient {
        MobileHeadroomClient(
            endpoint: MobileConnection.endpoint,
            token: MobileTokenStore.read() ?? ""
        )
    }
}
