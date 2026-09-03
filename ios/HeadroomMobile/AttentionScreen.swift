import SwiftUI

/// The queue: everything that is waiting on a person. Coding agents that asked
/// a question, the rollup's own reasons, and the feed rows that failed.
///
/// Anything that merely happened is one tab over on `ActivityScreen`. That
/// split is the whole point of this screen — a build that went green and a
/// permission request that is blocking a session were the same list until now,
/// and the list was sorted by which arrived last.
struct AttentionScreen: View {
    @ObservedObject var store: MobileUsageStore
    @Binding var focusedEventID: String?
    @Binding var showsSettings: Bool
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    var body: some View {
        // Both halves of the queue are the store's to decide: dismissing a row
        // has to drop it from the tab badge as well as from this list.
        let failures = store.attentionFailures
        let reasons = store.attentionReasons
        let hasNeedsAttention = !reasons.isEmpty || !failures.isEmpty
        // Agent-originated state is attention even when the answer is simply
        // "I saw this". A finished turn is useful context for the person
        // coordinating several sessions, so it stays here until dismissed.
        let events = store.agentAttentionEvents
        let dismissibleEvents = events.filter(\.isDismissOnly)
        let isAllClear = events.isEmpty && !hasNeedsAttention
        // Split only when both halves of the queue have something to show —
        // otherwise a lone column would sit at half width for no reason.
        let splitLayout = WidePhoneLayout.isActive(horizontalSizeClass)
            && !events.isEmpty
            && hasNeedsAttention
        Group {
            if isAllClear {
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
            } else if splitLayout {
                HStack(spacing: 0) {
                    ScrollViewReader { proxy in
                        List {
                            ArchivedDataNotice(store: store)
                            agentsSection(
                                events: events,
                                dismissibleEvents: dismissibleEvents
                            )
                        }
                        .listStyle(.insetGrouped)
                        .attentionFocus(
                            focusedEventID: focusedEventID,
                            events: store.agentAttentionEvents,
                            focus: focus,
                            proxy: proxy
                        )
                    }
                    List {
                        needsAttentionSection(
                            reasons: reasons,
                            failures: failures
                        )
                    }
                    .listStyle(.insetGrouped)
                }
            } else {
                ScrollViewReader { proxy in
                    List {
                        ArchivedDataNotice(store: store)
                        if !events.isEmpty {
                            agentsSection(
                                events: events,
                                dismissibleEvents: dismissibleEvents
                            )
                        }
                        if hasNeedsAttention {
                            needsAttentionSection(
                                reasons: reasons,
                                failures: failures
                            )
                        }
                    }
                    .listStyle(.insetGrouped)
                    .attentionFocus(
                        focusedEventID: focusedEventID,
                        events: store.agentAttentionEvents,
                        focus: focus,
                        proxy: proxy
                    )
                }
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
    private func agentsSection(
        events: [AgentAttentionEvent],
        dismissibleEvents: [AgentAttentionEvent]
    ) -> some View {
        Section {
            ForEach(events) { event in
                agentRow(event)
                    .id(event.id)
                    .swipeActions(
                        edge: .trailing,
                        allowsFullSwipe: true
                    ) {
                        if event.isDismissOnly,
                           store.mobilePermissions.agents,
                           !store.isStale,
                           let dismiss = event.actions.first(
                               where: { $0.id == "dismiss" }
                           ) {
                            Button(dismiss.label) {
                                Task {
                                    await store.answer(
                                        event, with: dismiss)
                                }
                            }
                            .tint(HeadroomPalette.dim)
                        }
                    }
            }
        } header: {
            attentionSectionHeader(
                HeadroomCopy.codingAgents,
                showsDismissAll: !dismissibleEvents.isEmpty
                    && store.mobilePermissions.agents
                    && !store.isStale
            ) {
                Task { await store.dismissAllAgentNotices() }
            }
        }
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

    /// Shared section chrome for Coding agents and Needs attention — same
    /// title + optional dismiss control, no per-section colour or count.
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

    private func agentRow(_ event: AgentAttentionEvent) -> some View {
        let tint = (store.snapshot.providers ?? [])
            .accentTint(forProvider: event.providerIconID)
        let isQuestion = event.kind == "structured_question"
        // Answerable questions already put each option on a button.
        // Read-only ones (notify mode, multi-question, multiSelect) do
        // not — their options only live in `request`, so hiding that
        // block made Claude look like it sent a prompt with no choices.
        let hasChoiceButtons = event.actions.contains {
            $0.id.hasPrefix("choice_")
        }
        return HStack(alignment: .top, spacing: 10) {
            ProviderMark(providerID: event.providerIconID, size: 16)
                .foregroundStyle(tint)
                .frame(width: 16)
                .padding(.top, 2)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 6) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(event.displayTitle)
                            .font(.headline)
                            .foregroundStyle(.primary)
                        if let machineName = event.machineName, !machineName.isEmpty {
                            Text(machineName)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 6)
                    Text(HeadroomCopy.ago(event.age))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.tertiary)
                }
                if isQuestion || event.isDismissOnly {
                    Text(event.summary)
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                }
                if let reasons = event.detail.reasons, !reasons.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(HeadroomCopy.agentWhyAsking)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ForEach(reasons, id: \.self) { reason in
                            Text(reason)
                                .font(.caption)
                                .foregroundStyle(HeadroomPalette.orange)
                        }
                    }
                }
                if !isQuestion || !hasChoiceButtons {
                    AgentRequestView(fields: event.detail.requestFields)
                }
                if event.detail.answerOnMac == true {
                    Text(HeadroomCopy.answerInTheTerminal)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                // Dismiss lives on swipe / Dismiss all — printing it as a button
                // on every idle row is noise next to the answers that matter.
                let answers = event.actions.filter { $0.id != "dismiss" }
                if !answers.isEmpty {
                    FlowingActions(
                        actions: answers,
                        tint: tint,
                        disabled: !store.mobilePermissions.agents
                            || store.isStale
                            || store.respondingAgentEventID != nil,
                        responding: store.respondingAgentEventID == event.id
                    ) { action in
                        Task { await store.answer(event, with: action) }
                    }
                }
                if event.actions.contains(where: { $0.id == "approve_always" }),
                   let rule = event.detail.permissionRule {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(HeadroomCopy.agentWouldSaveRule)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                        Text(rule)
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
            }
        }
        // Leave the List's grouped cell background alone so Coding agents and
        // Needs attention share one system surface in light and dark.
        .padding(.vertical, 4)
    }

    private func focus(_ eventID: String?, using proxy: ScrollViewProxy) {
        guard let eventID,
              store.agentAttentionEvents.contains(where: { $0.id == eventID })
        else { return }
        withAnimation {
            proxy.scrollTo(eventID, anchor: .center)
        }
    }
}

private extension View {
    /// Scroll-to-focus wiring shared by the stacked and side-by-side Attention
    /// lists so a notification tap still lands on the matching agent row.
    func attentionFocus(
        focusedEventID: String?,
        events: [AgentAttentionEvent],
        focus: @escaping (String?, ScrollViewProxy) -> Void,
        proxy: ScrollViewProxy
    ) -> some View {
        self
            .onChange(of: focusedEventID) { _, eventID in
                focus(eventID, proxy)
            }
            .onChange(of: events) { _, _ in
                // The notification route refreshes before assigning focus,
                // but this also handles a slow Mac response safely.
                focus(focusedEventID, proxy)
            }
            .task {
                focus(focusedEventID, proxy)
            }
    }
}
