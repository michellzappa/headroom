import SwiftUI

/// The queue: everything that is waiting on a person — the rollup's own
/// reasons and the feed rows that failed.
///
/// Anything that merely happened is one tab over on `ActivityScreen`.
struct AttentionScreen: View {
    @ObservedObject var store: MobileUsageStore
    @Binding var showsSettings: Bool

    var body: some View {
        // Both halves of the queue are the store's to decide: dismissing a row
        // has to drop it from the tab badge as well as from this list.
        let failures = store.attentionFailures
        let reasons = store.attentionReasons
        let hasNeedsAttention = !reasons.isEmpty || !failures.isEmpty
        Group {
            if !hasNeedsAttention {
                // ScrollView so pull-to-refresh still works with no rows.
                ScrollView {
                    VStack(spacing: 0) {
                        if store.isStale {
                            ArchivedDataNotice(store: store)
                                .padding(.horizontal, MobileHomeChrome.pageInset)
                                .padding(.top, 8)
                        }
                        PageEmptyState(
                            systemImage: "eye",
                            title: HeadroomCopy.allClear
                        )
                        .containerRelativeFrame(.vertical) { height, _ in
                            max(height - (store.isStale ? 80 : 0), 280)
                        }
                    }
                }
                .background(Color(.systemGroupedBackground))
            } else {
                List {
                    ArchivedDataNotice(store: store)
                    needsAttentionSection(
                        reasons: reasons,
                        failures: failures
                    )
                }
                .listStyle(.insetGrouped)
            }
        }
        .navigationTitle(HeadroomCopy.attention)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button(HeadroomCopy.settings, systemImage: "gearshape") {
                    showsSettings = true
                }
                .labelStyle(.iconOnly)
            }
        }
        .refreshable { await store.refresh(forceServerSync: true) }
    }

    /// The feed rows this screen claims off `ActivityScreen`. One definition,
    /// read by both tabs and by the tab bar's badge, so a row can never be on
    /// both screens or on neither.
    /// `nonisolated` because it is a rule about data, not about a view: the
    /// tab bar's badge and the tests both ask it off the main actor.
    nonisolated static func failures(in snapshot: UsageSnapshot) -> [ActivityItem] {
        AttentionList.failures(in: snapshot)
    }

    @ViewBuilder
    private func needsAttentionSection(
        reasons: [AttentionReason],
        failures: [ActivityItem]
    ) -> some View {
        Section {
            ForEach(reasons) { reason in
                AttentionReasonRow(
                    reason: reason,
                    permalink: AttentionList.permalink(
                        for: reason, in: store.snapshot)
                )
                    .swipeActions(
                        edge: .trailing,
                        allowsFullSwipe: true
                    ) {
                        Button(HeadroomCopy.dismiss) {
                            store.dismissAttention(id: reason.id)
                        }
                        .tint(HeadroomPalette.dim)
                    }
            }
            ForEach(failures) { failure in
                ActivityFeedRow(item: failure)
                    .swipeActions(
                        edge: .trailing,
                        allowsFullSwipe: true
                    ) {
                        Button(HeadroomCopy.dismiss) {
                            store.dismissAttention(id: failure.id)
                        }
                        .tint(HeadroomPalette.dim)
                    }
            }
        } header: {
            attentionSectionHeader(
                HeadroomCopy.needsAttention,
                showsDismissAll: true
            ) {
                Task { await store.dismissAllAttention() }
            }
        }
    }

    /// Section chrome for Needs attention — title + optional dismiss control,
    /// no per-section colour or count.
    private func attentionSectionHeader(
        _ title: String,
        showsDismissAll: Bool,
        dismiss: @escaping () -> Void
    ) -> some View {
        HStack {
            Text(title)
            Spacer()
            if showsDismissAll {
                Button(HeadroomCopy.dismissAll, action: dismiss)
                    .font(.caption.weight(.semibold))
                    .textCase(nil)
            }
        }
    }
}
