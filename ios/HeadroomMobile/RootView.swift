import SwiftUI

/// Three tabs, split by what they ask of you rather than by where the data
/// came from: a summary, a queue, and a log. Services stopped being a tab of
/// its own in that split — a Supabase project and a deploy are both "what the
/// Mac is doing", and neither is waiting for an answer.
enum MobileTab: String, CaseIterable, Hashable {
    case overview
    case attention
    case activity
}

struct RootView: View {
    @ObservedObject var store: MobileUsageStore
    /// When set, cycle tabs and drop `.ios-shot-ready-<tab>` markers for the
    /// screenshot script (see `scripts/generate_screenshots.sh`).
    var exportDirectory: String? = nil

    @State private var showsConnection = false
    @State private var showsSettings = false
    @State private var selectedTab: MobileTab = .overview

    var body: some View {
        Group {
            if store.isConfigured {
                TabView(selection: $selectedTab) {
                    NavigationStack {
                        OverviewScreen(store: store)
                            .settingsToolbar($showsSettings)
                    }
                    .tabItem { Label(HeadroomCopy.usage, systemImage: "gauge.with.needle") }
                    .tag(MobileTab.overview)

                    NavigationStack {
                        AttentionScreen(
                            store: store,
                            showsSettings: $showsSettings
                        )
                    }
                    .tabItem {
                        Label(HeadroomCopy.attention,
                              systemImage: "eye")
                    }
                    .badge(waitingCount)
                    .tag(MobileTab.attention)

                    NavigationStack {
                        ActivityScreen(store: store)
                        .settingsToolbar($showsSettings)
                    }
                    .tabItem { Label(HeadroomCopy.activity, systemImage: "list.bullet.rectangle.fill") }
                    .tag(MobileTab.activity)
                }
            } else {
                NavigationStack {
                    PairingView(store: store)
                }
            }
        }
        .sheet(isPresented: $showsSettings) {
            // The pairing sheet hangs off the settings sheet, not the root:
            // two sheets presented from the same view cancel each other, and
            // "Change connection" only ever opens from inside Settings.
            MobileSettingsScreen(store: store, showsConnection: $showsConnection)
                .sheet(isPresented: $showsConnection) {
                    NavigationStack {
                        PairingView(store: store, isEditing: true)
                }
            }
        }
        .task(id: exportDirectory) {
            guard let exportDirectory else { return }
            await runScreenshotExport(to: exportDirectory)
        }
    }

    /// What the Attention tab is holding, for the badge. Rollup reasons are
    /// deliberately not added in: several of them are a reading of the same
    /// failed rows, so counting both reports one broken build twice.
    private var waitingCount: Int {
        store.attentionFailures.count
    }

    /// Signal each tab after Charts settle so `simctl io screenshot` can grab it.
    @MainActor
    private func runScreenshotExport(to directory: String) async {
        let root = URL(fileURLWithPath: directory)
        for tab in MobileTab.allCases {
            selectedTab = tab
            // Layout + chart settle.
            try? await Task.sleep(for: .milliseconds(1100))
            let ready = root.appendingPathComponent(".ios-shot-ready-\(tab.rawValue)")
            try? Data().write(to: ready)
            fputs("ios fixture ready \(tab.rawValue)\n", stderr)
            // Wait until the script removes the marker (or timeout).
            for _ in 0..<80 {
                if !FileManager.default.fileExists(atPath: ready.path) { break }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
        // Legacy marker so older scripts still unblock on overview alone.
        let legacy = root.appendingPathComponent(".ios-shot-ready")
        try? Data().write(to: legacy)
    }
}

private struct OverviewScreen: View {
    @ObservedObject var store: MobileUsageStore
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    /// Wide iPhone: status + quotas on the left, burndown charts on the right.
    private var splitLayout: Bool {
        WidePhoneLayout.isActive(horizontalSizeClass)
    }

    var body: some View {
        ScrollView {
            Group {
                if splitLayout {
                    HStack(alignment: .top, spacing: 16) {
                        VStack(spacing: 16) { leftColumn }
                            .frame(maxWidth: .infinity, alignment: .top)
                        VStack(spacing: 16) { rightColumn }
                            .frame(maxWidth: .infinity, alignment: .top)
                    }
                } else {
                    LazyVStack(spacing: 16) {
                        leftColumn
                        rightColumn
                    }
                }
            }
            .padding(MobileHomeChrome.pageInset)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle(HeadroomCopy.summary)
        .refreshable {
            await store.refresh(forceServerSync: true)
        }
        .task {
            // Archived content still counts as "nothing fetched yet".
            if store.capturedAt == nil || store.isShowingArchive {
                await store.refresh()
            }
        }
    }

    @ViewBuilder private var leftColumn: some View {
        MobileStatusCard(store: store)
        QuotaOverviewCard(
            snapshot: store.snapshot
        )
    }

    @ViewBuilder private var rightColumn: some View {
        OverallBurndownChart(
            providers: store.visibleProviders,
            snapshot: store.snapshot
        )
        DailyBurnChart(
            days: store.snapshot.byDay ?? [],
            providers: store.visibleProviders
        )
        ActivityHistoryCard(
            history: store.snapshot.activityHistory
        )
    }
}

struct MobileStatusCard: View {
    @ObservedObject var store: MobileUsageStore

    var body: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(statusColor)
                .frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 3) {
                Text(statusTitle)
                    .font(.headline)
                Text(statusSubtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if store.isLoading {
                ProgressView()
            }
        }
        .headroomCard()
    }

    private var statusTitle: String {
        if store.isLoading, store.isStale {
            return HeadroomCopy.reconnecting
        }
        if store.isStale {
            return store.hasSnapshot
                ? HeadroomCopy.recentHistory
                : HeadroomCopy.macUnavailable
        }
        if store.isLoading {
            return HeadroomCopy.refreshing
        }
        if store.snapshot.attention?.isWarning == true {
            return HeadroomCopy.needsAttention
        }
        return HeadroomCopy.connected
    }

    private var statusSubtitle: String {
        let identity = MobileConnection.identityLabel(
            machineName: store.snapshot.currentMachine?.name
        )
        if let error = store.errorMessage {
            guard let age = store.age else { return "\(identity) · \(error)" }
            return "\(identity) · \(HeadroomCopy.ago(age)) · \(error)"
        }
        // Archived and not yet contradicted: the fetch is still in flight.
        if store.isShowingArchive, let age = store.age {
            return "\(identity) · \(HeadroomCopy.ago(age))"
        }
        if let date = store.capturedAt {
            let updated = date.formatted(date: .omitted, time: .shortened)
            return "\(identity) · Updated \(updated)"
        }
        return identity
    }

    private var statusColor: Color {
        HeadroomPalette.status(
            level: store.snapshot.attention?.level,
            isStale: store.isStale
        )
    }
}

/// The one-line "this is saved, not live" marker for the screens that have no
/// status card of their own. Every screen that draws numbers needs to say when
/// they stopped being current, or the Quotas tab quietly lies for a day.
struct ArchivedDataNotice: View {
    @ObservedObject var store: MobileUsageStore

    var body: some View {
        if store.isStale, let age = store.age {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(HeadroomCopy.recentHistory(age: age))
                        .font(.footnote.weight(.medium))
                    Text(HeadroomCopy.recentHistoryHint)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } icon: {
                Image(systemName: "clock.arrow.circlepath")
                    .foregroundStyle(HeadroomPalette.amber)
            }
        }
    }
}

extension View {
    /// The gear every tab carries, top left. Settings is a place you visit,
    /// not a peer of the three screens that show numbers.
    func settingsToolbar(_ isPresented: Binding<Bool>) -> some View {
        toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button(HeadroomCopy.settings, systemImage: "gearshape") {
                    isPresented.wrappedValue = true
                }
                .labelStyle(.iconOnly)
            }
        }
    }

    func headroomCard() -> some View {
        self
            .padding(16)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}
