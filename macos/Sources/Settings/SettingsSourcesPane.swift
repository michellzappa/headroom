import AppKit
import SwiftUI

// Sources, design 2a "Active vs. Library".
//
// Two zones: Active lists only enabled services as rich, reorderable rows
// with live usage; everything switched off shrinks to a chip in the Library.
// One component serves every service — multi-account is a capability flag,
// not a Claude feature — so the old flat 14-row list, the separate "Extra
// accounts" section, and the footnote paragraphs are all gone.
//
// The pane lists AI providers only — things Headroom reads a meter for.
// Dev tools (Git, GitHub, Vercel, Supabase, Plausible, PostHog) live under
// Integrations, which owns both their credentials and their on/off.
//
// Ordering is account-level on the wire: the host's `order` holds
// quota-account ids and dragging a service moves its accounts as a block.
// The ①②③ badges mark menu-bar slots honestly — a service whose accounts
// fill three slots wears ①–③ alone.

/// One row of the Active card / one chip of the Library: a provider with its
/// account-level source rows grouped back together.
struct SourceService: Identifiable {
    /// Provider key — `claude:personal` groups under `claude`.
    let id: String
    let group: SourceGroup
    /// Account-level rows in pinned order. Single-account services hold one.
    let rows: [SyncSource]

    /// The default-login row when present; the first account otherwise.
    var primary: SyncSource { rows.first { !$0.id.contains(":") } ?? rows[0] }

    /// Service name with no account suffix: "Claude", never "Claude · Work".
    var title: String {
        let full = primary.title ?? id.capitalized
        if let sep = full.range(of: " · ") {
            return String(full[..<sep.lowerBound])
        }
        return full
    }

    var enabledRows: [SyncSource] { rows.filter { $0.enabled ?? true } }
    var isActive: Bool { !enabledRows.isEmpty }
    /// Active-list membership. Distinct from `isActive`: a paused service —
    /// every row off, none dismissed — stays listed, dimmed. On hosts
    /// predating `dismissed` the two collapse to the same answer.
    var isListed: Bool { rows.contains { !$0.isDismissed } }
    var accent: String? { primary.accent }
    var accentDefault: String? { primary.accentDefault }
    var titleDefault: String? { primary.titleDefault }

    /// Group account-level sources into services, preserving the pinned
    /// order of first appearance — the same order `order` persists.
    static func services(from sources: [SyncSource]) -> [SourceService] {
        var keys: [String] = []
        var rows: [String: [SyncSource]] = [:]
        for source in sources {
            let key = source.id.split(separator: ":").first.map(String.init)
                ?? source.id
            if rows[key] == nil { keys.append(key) }
            rows[key, default: []].append(source)
        }
        return keys.map { key in
            SourceService(
                id: key,
                group: rows[key]![0].sourceGroup,
                rows: rows[key]!
            )
        }
    }
}

/// Everything the pane reads and every change it can ask for. The pane owns
/// presentation only; state and writes stay in `SettingsView`.
struct SettingsSourcesPane: View {
    let sources: [SyncSource]
    /// Live usage by account id, for the bars — `providers[]` off `/usage`.
    let usage: [String: QuotaProviderInfo]
    /// Claude's public service health, attached to the Claude provider row.
    let claudeStatus: ClaudeStatus?
    /// Multi-account capability + current extra logins, from `/accounts`.
    let accountProviders: [AccountProvider]
    /// Credential detection by source id, from `/setup`. Empty when the
    /// fetch failed — chips then stay enabled rather than dead-ending.
    let detected: [String: Bool]
    let busyID: String?
    let isSyncing: Bool
    let message: String?
    let dropTargetID: String?

    let onToggleRows: ([String], Bool) -> Void
    /// Move a whole service to the Library (all row ids at once).
    let onDismissRows: ([String]) -> Void
    let onRemoveAccount: (String) -> Void
    let onAddAccount: (AccountProvider) -> Void
    let onRefresh: ([String]?) -> Void
    let onMoveService: (String, String) -> Void
    let onNudgeService: (String, Int) -> Void
    let onDropTarget: (String, Bool) -> Void
    let onAccent: ([String], String?) -> Void
    let onTitle: (String, String?) -> Void

    private var services: [SourceService] {
        SourceService.services(from: sources)
    }

    /// Active: AI providers in pinned order. Claude Status is an internal
    /// health check attached to Claude, so it never becomes its own row.
    /// Paused services (configured, switched off, not dismissed) stay in this
    /// list.
    ///
    /// Dev tools are deliberately absent. They used to sit below the AI rows
    /// here *and* have a leaf under Integrations, which made one list read as
    /// a duplicate of the other — every integration is also a source, so the
    /// two pages showed the same nouns without saying they answer different
    /// questions. Sources now answers "what do I watch"; Integrations answers
    /// "how is it connected", and owns the on/off for anything that needs a
    /// credential. See `SettingsIntegration`.
    private var activeServices: [SourceService] {
        services.filter {
            $0.isListed && $0.group == .ai && $0.id != "claude-status"
        }
    }

    private var libraryServices: [SourceService] {
        services.filter {
            !$0.isListed && $0.group == .ai && $0.id != "claude-status"
        }
    }

    /// Menu-bar slots: the first three enabled quota accounts in pinned
    /// order, mapped back to the service that owns each. Mirrors the host's
    /// `focus` pick so a badge never promises a slot the board won't fill.
    private var focusSlots: [String: [Int]] {
        var slots: [String: [Int]] = [:]
        var slot = 0
        for service in activeServices where service.group == .ai {
            for row in service.enabledRows where row.kind == "quota" {
                guard slot < 3 else { return slots }
                slots[service.id, default: []].append(slot)
                slot += 1
            }
        }
        return slots
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                sectionHeader(
                    HeadroomCopy.sourcesActive,
                    hint: HeadroomCopy.sourcesActiveHint)
                activeCard
                sectionHeader(
                    HeadroomCopy.sourcesLibrary,
                    hint: HeadroomCopy.sourcesLibraryHint)
                libraryGroup(.ai, title: HeadroomCopy.aiProvidersGroup)
                addAccountButtons
                footerBar
            }
            .padding(20)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private func sectionHeader(_ title: String, hint: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(.headline)
            Spacer()
            Text(hint)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var activeCard: some View {
        if activeServices.isEmpty {
            Text(message ?? "Waiting for host…")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(cardBackground)
        } else {
            VStack(spacing: 0) {
                ForEach(activeServices) { service in
                    ActiveServiceRow(
                        service: service,
                        usage: usage,
                        claudeStatus: service.id == "claude"
                            ? claudeStatus
                            : nil,
                        badgeSlots: focusSlots[service.id] ?? [],
                        isBusy: isBusy(service),
                        isDropTarget: dropTargetID == service.id,
                        onToggleRows: onToggleRows,
                        onDismiss: {
                            onDismissRows(service.rows.map(\.id))
                        },
                        onRemoveAccount: onRemoveAccount,
                        onRefresh: onRefresh,
                        onNudge: { onNudgeService(service.id, $0) },
                        onAccent: onAccent,
                        onTitle: onTitle
                    )
                    .modifier(DragReorder(
                        enabled: service.group == .ai,
                        id: service.id,
                        onTargeted: { onDropTarget(service.id, $0) },
                        onDrop: { onMoveService($0, service.id) }
                    ))
                    if service.id != activeServices.last?.id {
                        Divider().padding(.leading, 40)
                    }
                }
            }
            .background(cardBackground)
        }
    }

    @ViewBuilder
    private func libraryGroup(_ group: SourceGroup, title: String) -> some View {
        let chips = libraryServices.filter { $0.group == group }
        if !chips.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(title.uppercased())
                    .font(.caption2.weight(.semibold))
                    .kerning(0.5)
                    .foregroundStyle(.secondary)
                LazyVGrid(
                    columns: Array(
                        repeating: GridItem(.flexible(), spacing: 8),
                        count: 3),
                    spacing: 8
                ) {
                    ForEach(chips) { service in
                        LibraryChip(
                            service: service,
                            isDetected: isDetected(service),
                            isBusy: isBusy(service),
                            capability: capability(for: service),
                            onEnable: {
                                onToggleRows(service.rows.map(\.id), true)
                            },
                            onAddAccount: onAddAccount
                        )
                    }
                }
            }
        }
    }

    /// Extra logins for providers already in Active — buttons live under
    /// Library so Active rows stay meters-only. Library chips that are not
    /// detected already open the same sheet, so they stay out of this row.
    @ViewBuilder
    private var addAccountButtons: some View {
        let activeIDs = Set(activeServices.map(\.id))
        let caps = accountProviders.filter {
            !$0.isFull && activeIDs.contains($0.id)
        }
        if !caps.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(HeadroomCopy.addAccountSection.uppercased())
                    .font(.caption2.weight(.semibold))
                    .kerning(0.5)
                    .foregroundStyle(.secondary)
                LazyVGrid(
                    columns: Array(
                        repeating: GridItem(.flexible(), spacing: 8),
                        count: 3),
                    spacing: 8
                ) {
                    ForEach(caps) { provider in
                        AddAccountChip(
                            provider: provider,
                            isBusy: busyID != nil,
                            onAddAccount: onAddAccount
                        )
                    }
                }
            }
        }
    }

    private var footerBar: some View {
        HStack(alignment: .firstTextBaseline) {
            if let message {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            Spacer()
            Button {
                onRefresh(nil)
            } label: {
                if isSyncing {
                    ProgressView().controlSize(.small)
                } else {
                    Text(HeadroomCopy.refreshAll)
                }
            }
            .disabled(isSyncing || sources.isEmpty)
        }
    }

    private var cardBackground: some View {
        RoundedRectangle(cornerRadius: 10)
            .fill(Color(nsColor: .textBackgroundColor))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(Color(nsColor: .separatorColor))
            )
    }

    private func capability(for service: SourceService) -> AccountProvider? {
        accountProviders.first { $0.id == service.id }
    }

    private func isBusy(_ service: SourceService) -> Bool {
        if isSyncing { return true }
        guard let busyID else { return false }
        return busyID == service.id
            || service.rows.contains { $0.id == busyID }
    }

    /// A service counts as detected when any of its rows does, or when it is
    /// already configured — `/setup` only knows about local credentials, and
    /// a source running on a pasted key must not read "not detected".
    private func isDetected(_ service: SourceService) -> Bool {
        guard !detected.isEmpty else { return true }
        return service.rows.contains {
            detected[$0.id] == true || $0.configured == true || $0.ok == true
        }
    }
}

private struct ActiveServiceRow: View {
    let service: SourceService
    let usage: [String: QuotaProviderInfo]
    let claudeStatus: ClaudeStatus?
    let badgeSlots: [Int]
    let isBusy: Bool
    let isDropTarget: Bool
    let onToggleRows: ([String], Bool) -> Void
    let onDismiss: () -> Void
    let onRemoveAccount: (String) -> Void
    let onRefresh: ([String]?) -> Void
    let onNudge: (Int) -> Void
    let onAccent: ([String], String?) -> Void
    let onTitle: (String, String?) -> Void

    @State private var isHovering = false
    @State private var isPickingColor = false
    @State private var isRenaming = false
    @State private var renameDraft = ""
    @FocusState private var renameFocused: Bool
    @FocusState private var refreshFocused: Bool
    @FocusState private var dismissFocused: Bool

    private var enabledIDs: [String] { service.enabledRows.map(\.id) }

    /// Named sub-rows only from two accounts up; a single account keeps its
    /// bar inline in the service row with no name to repeat. Disabled
    /// accounts stay listed — this block is their only controls while a
    /// sibling keeps the service out of the Library.
    private var namedAccounts: [SyncSource]? {
        guard service.group == .ai, service.rows.count >= 2 else {
            return nil
        }
        return service.rows
    }

    private var inlineAccount: SyncSource? {
        guard service.group == .ai, service.rows.count == 1 else {
            return nil
        }
        return service.enabledRows.first
    }

    /// Account rows carry their own accent. Fall back through the source row
    /// for hosts that only sent `/sources`, then to the service tint for old
    /// hosts that had no account-level color at all.
    private func tint(for account: SyncSource) -> Color {
        usage[account.id]?.tint
            ?? HeadroomPalette.color(hex: account.accent)
            ?? tint
    }

    private func accountEmail(_ account: SyncSource) -> String? {
        let fromUsage = usage[account.id]?.email?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let fromUsage, !fromUsage.isEmpty { return fromUsage }
        let fromSource = account.email?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let fromSource, !fromSource.isEmpty { return fromSource }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                if service.group == .ai {
                    Image(systemName: "line.3.horizontal")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .help("Drag to reorder")
                        .accessibilityHidden(true)
                }

                Text(badgeText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(width: 26, alignment: .leading)
                    .accessibilityLabel(badgeSlots.isEmpty
                        ? "" : "Menu bar slot \(badgeText)")

                swatch

                VStack(alignment: .leading, spacing: 4) {
                    titleLine
                    if let claudeStatus {
                        ClaudeStatusLine(status: claudeStatus)
                    }
                    if let account = inlineAccount {
                        if let email = accountEmail(account) {
                            Text(email)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        AccountBar(
                            name: nil,
                            row: account,
                            usage: usage[account.id],
                            tint: tint(for: account))
                    }
                }

                Spacer(minLength: 8)

                trailing
            }

            if namedAccounts != nil {
                accountBlock
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .overlay(alignment: .top) {
            if isDropTarget {
                Rectangle()
                    .fill(HeadroomPalette.green)
                    .frame(height: 2)
            }
        }
        .opacity(service.isActive ? 1 : 0.6)
        .onHover { isHovering = $0 }
        .contextMenu {
            Button(HeadroomCopy.moveToLibrary) { onDismiss() }
        }
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Move up") { onNudge(-1) }
        .accessibilityAction(named: "Move down") { onNudge(1) }
        .accessibilityAction(named: "Refresh") { onRefresh(enabledIDs) }
        .accessibilityAction(named: HeadroomCopy.moveToLibrary) { onDismiss() }
    }

    /// "①", "②③", "①–③" — the menu-bar slots this service's accounts fill.
    private var badgeText: String {
        let glyphs = ["①", "②", "③"]
        let owned = badgeSlots.filter { glyphs.indices.contains($0) }
        guard let first = owned.first else { return "" }
        guard owned.count > 1, let last = owned.last else {
            return glyphs[first]
        }
        return "\(glyphs[first])–\(glyphs[last])"
    }

    private var titleLine: some View {
        HStack(spacing: 4) {
            Group {
                if isRenaming {
                    TextField("Name", text: $renameDraft, onCommit: commitRename)
                        .textFieldStyle(.plain)
                        .focused($renameFocused)
                        .onSubmit { commitRename() }
                } else {
                    Text(service.title)
                        .fontWeight(.semibold)
                        .contentShape(Rectangle())
                        .onTapGesture { beginRename() }
                        .help("Click to rename")
                }
            }
            .font(.system(size: 13))
            Text(subtitle)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .font(.system(size: 13))
        .contextMenu {
            Button("Rename") { beginRename() }
            if service.title != (service.titleDefault ?? service.title) {
                Button("Reset name") { onTitle(service.id, nil) }
            }
        }
    }

    private func beginRename() {
        renameDraft = service.title
        isRenaming = true
        renameFocused = true
    }

    private func commitRename() {
        isRenaming = false
        let trimmed = renameDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        let defaultName = service.titleDefault ?? service.title
        if trimmed.isEmpty || trimmed == defaultName {
            onTitle(service.id, nil)
        } else if trimmed != service.title {
            onTitle(service.id, trimmed)
        }
    }

    /// "· AI provider · Max 5x" — category as metadata, plan when known.
    private var subtitle: String {
        var parts = [service.group == .ai
            ? HeadroomCopy.aiProviderCategory
            : HeadroomCopy.devToolCategory]
        if let plan = enabledIDs.compactMap({ usage[$0]?.plan }).first {
            parts.append(plan)
        } else if service.group == .devtools,
                  let detail = service.primary.detail {
            parts.append(detail)
        }
        return "· " + parts.joined(separator: " · ")
    }

    /// Brand fill with the health ring around it, color-pickable on
    /// providers. Dev tools keep the plain health dot — their dot *is* the
    /// status light, and repainting it would repaint the status.
    private var swatch: some View {
        Button {
            guard canPickColor else { return }
            isPickingColor = true
        } label: {
            // Providers get the design's square color swatch with the brand
            // mark inset; dev tools get the mark tinted by health — their
            // colour *is* the status light. The swatch grows a status-colored
            // ring only when something is wrong, so brand color never swallows
            // an Error the row text has no other place to show.
            if let brandColor {
                RoundedRectangle(cornerRadius: 6)
                    .fill(brandColor)
                    .frame(width: 20, height: 20)
                    .overlay {
                        ProviderMark(providerID: service.id, size: 11)
                            .foregroundStyle(.white)
                    }
                    .overlay {
                        if isUnhealthy {
                            RoundedRectangle(cornerRadius: 8)
                                .strokeBorder(statusColor, lineWidth: 1.5)
                                .padding(-3)
                        }
                    }
                    .contentShape(RoundedRectangle(cornerRadius: 6))
            } else if ProviderIcon.assetName(for: service.id) != nil {
                ProviderMark(providerID: service.id, size: 14)
                    .foregroundStyle(statusColor)
                    .frame(width: 20, height: 20)
                    .contentShape(Rectangle())
            } else {
                Circle()
                    .fill(statusColor)
                    .frame(width: 10, height: 10)
                    .frame(width: 20, height: 20)
                    .contentShape(Circle())
            }
        }
        .buttonStyle(.plain)
        .disabled(!canPickColor)
        .help(canPickColor ? "Change color" : statusLabel)
        .accessibilityLabel(
            canPickColor ? "\(statusLabel). Change color" : statusLabel)
        .popover(isPresented: $isPickingColor, arrowEdge: .bottom) {
            AccentPicker(
                title: service.title,
                defaultHex: service.accentDefault,
                currentHex: service.accent,
                // The service swatch controls the base row. Extra accounts
                // derive their shades from that base instead of receiving
                // the same explicit override and collapsing to one color.
                onPick: { onAccent([service.primary.id], $0) }
            )
        }
    }

    /// Staleness label normally; the refresh button while hovered — the ⟳
    /// per row exists but never as fourteen resting buttons. The button
    /// stays in the hierarchy at zero opacity so Tab can reach it; keyboard
    /// focus reveals it the same way hover does.
    @ViewBuilder
    private var trailing: some View {
        if isBusy {
            ProgressView()
                .controlSize(.small)
        } else if service.isActive {
            let showsRefresh = isHovering || refreshFocused
            ZStack(alignment: .trailing) {
                stalenessLabel
                    .opacity(showsRefresh ? 0 : 1)
                Button {
                    onRefresh(enabledIDs)
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .focused($refreshFocused)
                .opacity(showsRefresh ? 1 : 0)
                .help("Force refresh")
                .accessibilityLabel("Force refresh \(service.title)")
            }
        } else {
            // Paused: configured, listed, not polled. No refresh — the host
            // skips disabled rows, so offering one would be a dead button.
            Text(HeadroomCopy.sourcePaused)
                .font(.caption)
                .foregroundStyle(.secondary)
        }

        Toggle(
            "Enabled",
            isOn: Binding(
                get: { service.isActive },
                set: { onToggleRows(service.rows.map(\.id), $0) }
            )
        )
        .labelsHidden()
        .disabled(isBusy)
        .toggleStyle(.switch)
        .controlSize(.small)

        // The ✕ that files the whole service back in the Library. Same
        // reveal contract as the ⟳: resting rows stay quiet, but the button
        // never leaves the hierarchy, so Tab reaches it.
        Button {
            onDismiss()
        } label: {
            Image(systemName: "xmark")
                .font(.caption2.weight(.semibold))
        }
        .buttonStyle(.borderless)
        .focused($dismissFocused)
        .opacity(isHovering || dismissFocused ? 1 : 0)
        .disabled(isBusy)
        .help("\(HeadroomCopy.moveToLibrary) — stops tracking. Your sign-ins are untouched.")
        .accessibilityLabel("\(HeadroomCopy.moveToLibrary): \(service.title)")
    }

    /// Needs sign-in carries the age too — the bars below may still show
    /// last-known numbers, and the age is what says how old they are. Same
    /// rule as the glossary: Needs sign-in ages like Not updating.
    @ViewBuilder
    private var stalenessLabel: some View {
        if service.enabledRows.contains(where: \.needsSignIn) {
            Text(worstAge.map {
                "\(HeadroomCopy.needsSignIn) · \(HeadroomCopy.agoShort($0))"
            } ?? HeadroomCopy.needsSignIn)
                .font(.caption)
                .foregroundStyle(HeadroomPalette.red)
                .monospacedDigit()
        } else if let age = worstAge {
            Text(isStaleWarning
                ? "\(HeadroomCopy.agoShort(age)) ⚠︎"
                : HeadroomCopy.agoShort(age))
                .font(.caption)
                .foregroundStyle(isStaleWarning
                    ? AnyShapeStyle(HeadroomPalette.amber)
                    : AnyShapeStyle(.secondary))
                .monospacedDigit()
        }
    }

    /// The oldest enabled account — one number that can only under-promise.
    private var worstAge: TimeInterval? {
        let ages = service.enabledRows.compactMap(\.ageS)
        return ages.max().map(TimeInterval.init)
    }

    /// Soft amber once a *stale* source is an hour behind. Age alone is
    /// not the trigger: a provider polled hourly is not a provider in
    /// trouble. Rate-limit holds stay quiet — the host is waiting on purpose.
    /// Needs-sign-in oranges elsewhere; stale stays soft.
    private var isStaleWarning: Bool {
        guard service.enabledRows.contains(where: {
            $0.stale == true && !$0.isRateLimited
        }),
              let age = worstAge else { return false }
        return age >= 3600
    }

    private var accountBlock: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let accounts = namedAccounts {
                ForEach(accounts) { account in
                    AccountRow(
                        account: account,
                        usage: usage[account.id],
                        tint: tint(for: account),
                        isBusy: isBusy,
                        onToggleRows: onToggleRows,
                        onRemoveAccount: onRemoveAccount,
                        onAccent: onAccent)
                }
            }
        }
        .padding(.leading, 74)
    }

    private var statusLabel: String {
        if service.enabledRows.contains(where: \.needsSignIn) {
            return HeadroomCopy.needsSignIn
        }
        if service.enabledRows.contains(where: { $0.ok != true }) {
            return "Error"
        }
        // A rate-limit hold is the host doing its job — not a stale failure.
        if service.enabledRows.contains(where: \.isRateLimited),
           !service.enabledRows.contains(where: {
               $0.stale == true && !$0.isRateLimited
           }) {
            return HeadroomCopy.updatingPaused
        }
        return service.enabledRows.contains { $0.stale == true }
            ? "Stale" : "Healthy"
    }

    /// Anything the status light would not paint green.
    /// Rate-limited rows stay out: they are paused on purpose, and painting
    /// them amber is what made Refresh feel like the fix.
    private var isUnhealthy: Bool {
        service.enabledRows.contains {
            $0.needsSignIn || $0.ok != true
                || ($0.stale == true && !$0.isRateLimited)
        }
    }

    private var statusColor: Color {
        if service.enabledRows.contains(where: \.needsSignIn) {
            return HeadroomPalette.red
        }
        if service.enabledRows.contains(where: { $0.ok != true }) {
            return HeadroomPalette.red
        }
        if service.enabledRows.contains(where: {
            $0.stale == true && !$0.isRateLimited
        }) {
            return HeadroomPalette.amber
        }
        return HeadroomPalette.green
    }

    private var brandColor: Color? {
        HeadroomPalette.color(hex: service.accent)
    }

    private var tint: Color {
        brandColor ?? HeadroomPalette.green
    }

    private var canPickColor: Bool {
        service.accentDefault != nil
    }
}

/// One named account under a service: the bar plus its controls. Extra
/// accounts wear a hover ✕ for Remove — the context menu still has both
/// actions, but a control someone asked "how do I remove one?" about is a
/// control that was too hidden.
private struct AccountRow: View {
    let account: SyncSource
    let usage: QuotaProviderInfo?
    let tint: Color
    let isBusy: Bool
    let onToggleRows: ([String], Bool) -> Void
    let onRemoveAccount: (String) -> Void
    let onAccent: ([String], String?) -> Void

    @State private var isHovering = false
    @State private var isPickingColor = false
    @FocusState private var removeFocused: Bool

    private var isRemovable: Bool { account.id.contains(":") }
    private var canPickColor: Bool { account.accentDefault != nil }

    var body: some View {
        HStack(spacing: 6) {
            if canPickColor {
                accountSwatch
            }
            AccountBar(
                name: accountTitle,
                row: account,
                usage: usage,
                tint: tint)
            if isRemovable {
                Button {
                    onRemoveAccount(account.id)
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption2.weight(.semibold))
                }
                .buttonStyle(.borderless)
                .focused($removeFocused)
                .opacity(isHovering || removeFocused ? 1 : 0)
                .disabled(isBusy)
                .help("Remove this account")
                .accessibilityLabel(
                    "Remove account \(account.label ?? account.id)")
            }
        }
        .onHover { isHovering = $0 }
        .contextMenu {
            if account.enabled ?? true {
                Button("Turn off") {
                    onToggleRows([account.id], false)
                }
            } else {
                Button("Turn on") {
                    onToggleRows([account.id], true)
                }
            }
            if isRemovable {
                Button("Remove…", role: .destructive) {
                    onRemoveAccount(account.id)
                }
            }
            if canPickColor {
                Button("Change color…") { isPickingColor = true }
            }
        }
    }

    private var accountSwatch: some View {
        Button {
            isPickingColor = true
        } label: {
            RoundedRectangle(cornerRadius: 4)
                .fill(tint)
                .frame(width: 14, height: 14)
                .overlay {
                    RoundedRectangle(cornerRadius: 4)
                        .strokeBorder(.primary.opacity(0.12), lineWidth: 0.5)
                }
        }
        .buttonStyle(.plain)
        .help("Change account color")
        .accessibilityLabel("Change color for \(accountTitle)")
        .popover(isPresented: $isPickingColor, arrowEdge: .bottom) {
            AccentPicker(
                title: accountTitle,
                defaultHex: accountDefaultHex,
                currentHex: account.accent,
                onPick: { onAccent([account.id], $0) },
                defaultLabel: account.id.contains(":")
                    ? "Derived shade" : "Default"
            )
        }
    }

    /// Registry default on the provider row; derived shade on extra accounts.
    private var accountDefaultHex: String? {
        if account.id.contains(":") {
            return account.accentDerived ?? account.accent
        }
        return account.accentDefault
    }

    /// Label · email when both exist; email alone; else the user label.
    private var accountTitle: String {
        let label = account.label?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let email = (usage?.email ?? account.email)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let hasLabel = !(label ?? "").isEmpty
        let hasEmail = !(email ?? "").isEmpty
        if hasLabel, hasEmail, label != email {
            return "\(label!) · \(email!)"
        }
        if hasEmail { return email! }
        return label ?? "Default"
    }
}

/// One account's usage: optional name, a thin bar tinted with the service
/// color, and "44% · week". The bar is the plan's primary window — the ring
/// pool with the longest window, which is the number the plan is sold in.
private struct AccountBar: View {
    let name: String?
    let row: SyncSource
    let usage: QuotaProviderInfo?
    let tint: Color

    private var isOff: Bool { row.enabled == false }

    var body: some View {
        HStack(spacing: 8) {
            if let name {
                Text(name)
                    .font(.system(size: 12))
                    .foregroundStyle(isOff ? AnyShapeStyle(.secondary)
                                           : AnyShapeStyle(.primary))
                    .frame(minWidth: 70, maxWidth: 160, alignment: .leading)
                    .lineLimit(1)
            }
            if isOff {
                Text(HeadroomCopy.sourcePaused)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else if row.needsSignIn {
                Text(HeadroomCopy.needsSignIn)
                    .font(.caption)
                    .foregroundStyle(HeadroomPalette.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else if let pool = primaryPool, let pct = pool.pct {
                bar(fraction: pct / 100)
                Text(meterLabel(pct: pct, pool: pool))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                    .lineLimit(1)
                    .frame(minWidth: 74, alignment: .trailing)
            } else {
                Text(row.detail ?? HeadroomCopy.collectingHistory)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func bar(fraction: Double) -> some View {
        GeometryReader { proxy in
            let clamped = min(max(fraction, 0), 1)
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color(nsColor: .quaternaryLabelColor))
                Capsule()
                    .fill(tint)
                    .frame(width: max(5, proxy.size.width * clamped))
            }
        }
        .frame(height: 5)
        .frame(maxWidth: .infinity)
    }

    /// "44% · week" — used percentage over the pool's own name.
    private func meterLabel(pct: Double, pool: QuotaPoolInfo) -> String {
        let period = (pool.title ?? "").lowercased()
        let value = "\(Int(pct.rounded()))%"
        return period.isEmpty ? value : "\(value) · \(period)"
    }

    /// The host's declared headline pool when it sent one; otherwise the
    /// longest ring window, tie-broken by declared pool rank and then id —
    /// `pools` is a dictionary, and Copilot ships two ring pools with the
    /// same window, so a bare `max` would flip between launches.
    private var primaryPool: QuotaPoolInfo? {
        guard let pools = usage?.pools, !pools.isEmpty else { return nil }
        if let headline = usage?.headline, let pool = pools[headline] {
            return pool
        }
        let ringPools = pools.filter { $0.value.ring == true }
        let candidates = ringPools.isEmpty ? pools : ringPools
        return candidates.min { lhs, rhs in
            let lw = lhs.value.windowS ?? 0
            let rw = rhs.value.windowS ?? 0
            if lw != rw { return lw > rw }
            let lr = QuotaProviderInfo.poolRank(id: lhs.key, pool: lhs.value)
            let rr = QuotaProviderInfo.poolRank(id: rhs.key, pool: rhs.value)
            if lr != rr { return lr < rr }
            return lhs.key < rhs.key
        }?.value
    }
}

/// Library chip that only opens the add-account sheet — for providers that
/// already sit in Active and still have room for another login.
private struct AddAccountChip: View {
    let provider: AccountProvider
    let isBusy: Bool
    let onAddAccount: (AccountProvider) -> Void

    var body: some View {
        Button {
            onAddAccount(provider)
        } label: {
            HStack(spacing: 6) {
                if ProviderIcon.assetName(for: provider.id) != nil {
                    ProviderMark(providerID: provider.id, size: 11)
                        .foregroundStyle(Color(nsColor: .secondaryLabelColor))
                } else {
                    Circle()
                        .fill(Color(nsColor: .tertiaryLabelColor))
                        .frame(width: 8, height: 8)
                }
                Text(provider.title)
                    .font(.system(size: 12))
                    .lineLimit(1)
                Spacer(minLength: 0)
                Image(systemName: "plus")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color(nsColor: .textBackgroundColor))
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(Color(nsColor: .separatorColor))
                    )
            )
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(isBusy)
        .help("Add another \(provider.title) account")
        .accessibilityLabel("\(HeadroomCopy.addAccount) \(provider.title)")
    }
}

/// A switched-off source: dot, name, "+". Undetectable sources dim in place
/// with "not detected" instead of growing a toggle that cannot work — unless
/// the service takes accounts, in which case the chip opens the add-account
/// sheet: a missing default credential is exactly when someone needs to point
/// the host at an alternate one.
private struct LibraryChip: View {
    let service: SourceService
    let isDetected: Bool
    let isBusy: Bool
    /// Multi-account capability, when the host reports one.
    let capability: AccountProvider?
    let onEnable: () -> Void
    let onAddAccount: (AccountProvider) -> Void

    /// No credential to import, but the service takes accounts — the tap
    /// becomes "Add account…" instead of a dead end.
    private var addsInstead: Bool {
        !isDetected && capability.map { !$0.isFull } == true
    }

    var body: some View {
        Button {
            if addsInstead, let capability {
                onAddAccount(capability)
            } else {
                onEnable()
            }
        } label: {
            HStack(spacing: 6) {
                if ProviderIcon.assetName(for: service.id) != nil {
                    ProviderMark(providerID: service.id, size: 11)
                        .foregroundStyle(Color(nsColor: .secondaryLabelColor))
                } else {
                    Circle()
                        .fill(Color(nsColor: .tertiaryLabelColor))
                        .frame(width: 8, height: 8)
                }
                Text(service.title)
                    .font(.system(size: 12))
                    .lineLimit(1)
                Spacer(minLength: 0)
                if isDetected {
                    Image(systemName: "plus")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                } else if addsInstead {
                    Text(HeadroomCopy.addAccount)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                } else {
                    Text(HeadroomCopy.notDetected)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color(nsColor: .textBackgroundColor))
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(Color(nsColor: .separatorColor))
                    )
            )
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled((!isDetected && !addsInstead) || isBusy)
        .opacity(isDetected || addsInstead ? 1 : 0.55)
        .help(helpText)
        .accessibilityLabel(accessibilityText)
    }

    private var helpText: String {
        if isDetected {
            return "Turn on — moves up to \(HeadroomCopy.sourcesActive)"
        }
        if addsInstead {
            return "Nothing to import on this Mac — add an account by picking its credential location"
        }
        return "Nothing to import on this Mac"
    }

    private var accessibilityText: String {
        if isDetected { return "Turn on \(service.title)" }
        if addsInstead {
            return "\(service.title), \(HeadroomCopy.notDetected). \(HeadroomCopy.addAccount)"
        }
        return "\(service.title), \(HeadroomCopy.notDetected)"
    }
}

/// The technical prose the list dropped — credential paths, restart notes —
/// lands here, read once at the moment it is needed.
struct AddAccountSheet: View {
    let provider: AccountProvider
    let endpoint: String
    /// Called once the host is back up, so Settings can reload its rows.
    var onDone: () async -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var label = ""
    @State private var message: String?
    @State private var isBusy = false

    private var client: HeadroomClient { HeadroomClient(endpoint: endpoint) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add \(provider.title) account")
                .font(.headline)
            if let hint = provider.hint {
                Text("\(hint). Adding an account restarts the host.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            TextField("Name (Work)", text: $label)
                .textFieldStyle(.roundedBorder)
            if let message {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .disabled(isBusy)
                Button(provider.wantsFolder ? "Choose folder…" : "Choose file…") {
                    choose()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(isBusy
                    || label.trimmingCharacters(in: .whitespaces).isEmpty)
                if isBusy {
                    ProgressView().controlSize(.small)
                }
            }
        }
        .padding(20)
        .frame(width: 380)
    }

    /// A path, chosen the way a person picks one. Typing it would be the
    /// likeliest place to get this wrong, and the host can only tell you
    /// afterwards that the folder isn't there.
    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = provider.wantsFolder
        panel.canChooseFiles = !provider.wantsFolder
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        panel.prompt = "Use"
        panel.message = provider.wantsFolder
            ? "Pick the \(provider.title) config folder for this account"
            : "Pick the \(provider.title) credential store for this account"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await add(root: url.path) }
    }

    private func add(root: String) async {
        let name = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        // launchd hands the same port to whatever host it just started, so
        // "still 200 on :8737" proves nothing. Uptime going backwards is what
        // says the process actually changed underneath.
        let before = try? await client.health().uptimeS
        do {
            _ = try await client.addAccount(
                provider: provider.id, label: name, root: root)
            message = "Restarting host…"
            await Self.waitForRestart(client: client, previousUptime: before)
            await onDone()
            dismiss()
        } catch {
            message = error.localizedDescription
        }
    }

    static func waitForRestart(
        client: HeadroomClient, previousUptime: Int?
    ) async {
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            try? await Task.sleep(for: .milliseconds(400))
            guard let report = try? await client.health() else { continue }
            guard let before = previousUptime, let now = report.uptimeS else {
                return
            }
            if now < before { return }
        }
    }
}
