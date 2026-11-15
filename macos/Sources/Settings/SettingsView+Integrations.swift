import HouseKit
import SwiftUI

extension SettingsView {
    /// One catalog of watched things — order, enable, status, open leaf.
    ///
    /// Flat list (not group sections): the pin is the layout, and Activity
    /// follows the same order for ids that paint blocks. Claude Code / Codex
    /// are under Coding agents, not here.
    var integrationsHub: some View {
        Form {
            activityRowLimitSection

            Section {
                ForEach(integrationsOrder, id: \.self) { id in
                    catalogRow(id)
                }
            } footer: {
                Text(HeadroomCopy.integrationsOrderHint)
            }
        }
        .formStyle(.grouped)
    }

    @ViewBuilder
    private func catalogRow(_ id: String) -> some View {
        let watch = IntegrationWatch(rawValue: id)
        let title = watch?.title ?? id
        let sourceID = watch?.sourceID ?? id
        let enabled = sources.first(where: { $0.id == sourceID })?.enabled ?? true
        let kind = watch?.settingsIntegration
        HStack(spacing: 8) {
            Image(systemName: "line.3.horizontal")
                .foregroundStyle(.tertiary)
                .font(.caption)
                .help("Drag to reorder")
            // Mark + title + chevron open the leaf; drag handle and toggle
            // stay their own targets (same split as Coding agents rows).
            if let kind {
                SettingsLeafLink(integrationLeaf(kind)) {
                    catalogIdentity(watch, title: title)
                }
                .help("Open")
            } else {
                catalogIdentity(watch, title: title)
                Spacer(minLength: 8)
            }
            Toggle(
                "Enabled",
                isOn: Binding(
                    get: { enabled },
                    set: { on in
                        Task { await setSourceRows([sourceID], enabled: on) }
                    }
                )
            )
            .labelsHidden()
            .disabled(togglingSourceID == sourceID)
        }
        .padding(.vertical, 2)
        .opacity(enabled ? 1 : 0.55)
        .background(
            dropTargetID == id
                ? HeadroomPalette.green.opacity(0.12)
                : Color.clear
        )
        .modifier(DragReorder(
            enabled: true,
            id: id,
            onTargeted: { targeted in
                dropTargetID = targeted ? id : nil
            },
            onDrop: { dragged in
                dropTargetID = nil
                Task { await moveServicePanel(dragged, before: id) }
            }
        ))
        .accessibilityAction(named: "Move up") {
            Task { await nudgeServicePanel(id, by: -1) }
        }
        .accessibilityAction(named: "Move down") {
            Task { await nudgeServicePanel(id, by: 1) }
        }
    }

    @ViewBuilder
    private func catalogIdentity(
        _ watch: IntegrationWatch?, title: String
    ) -> some View {
        if let watch {
            ProviderMark(
                providerID: watch.sourceID == "local" ? "local" : watch.rawValue,
                size: 16,
                fallbackSystemImage: watch.symbol
            )
        }
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
            Text(catalogSubtitle(watch))
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private func catalogSubtitle(_ watch: IntegrationWatch?) -> String {
        guard let watch else { return "" }
        switch watch {
        case .servers, .builds:
            if let detail = sources.first(where: { $0.id == "local" })?.detail,
               !detail.isEmpty {
                return detail
            }
            return integrationStatus(.local).title
        default:
            guard let kind = watch.settingsIntegration else { return "" }
            return integrationStatus(kind).title
        }
    }

    /// One hub row. Shared with the Coding agents pane, which links to the two
    /// agent leaves rather than keeping a second copy of their settings.
    ///
    /// `LabeledContent` + `Label` keeps Form's icon column aligned across
    /// symbols of different widths — a bare `Label`/`Spacer`/`Text` stack
    /// opts that out and leaves titles drifting row to row.
    func integrationRow(_ kind: SettingsIntegration) -> some View {
        SettingsLeafLink(integrationLeaf(kind)) {
            LabeledContent {
                integrationStatus(kind).label()
            } label: {
                Label {
                    Text(kind.title)
                } icon: {
                    ProviderMark(
                        providerID: kind.rawValue,
                        size: 16,
                        fallbackSystemImage: kind.symbol
                    )
                }
            }
        }
    }

    /// "Connected" is the wrong word for the two that need no credential, so
    /// they say what is actually true of them instead.
    ///
    /// Token-backed rows (Supabase / Plausible / GitHub) used to say Connected
    /// from Keychain alone. That hid a rejected PAT behind a green caption —
    /// the live `sources[]` detail is what actually answered.
    func integrationStatus(_ kind: SettingsIntegration) -> SettingsConnectionStatus {
        switch kind {
        case .claudeCode:
            let ok = claudeHooks?.installed == true
            return SettingsConnectionStatus(
                ok ? HeadroomCopy.hooksInstalled : HeadroomCopy.hooksOff,
                tone: ok ? .ok : .attention
            )
        case .codex:
            return SettingsConnectionStatus(
                agentGatewayEnabled
                    ? HeadroomCopy.gatewayOn : HeadroomCopy.gatewayOff,
                tone: agentGatewayEnabled ? .ok : .attention
            )
        case .git:
            guard gitEditable else { return .unknown }
            guard gitConfig.devRootExists else { return .folderMissing }
            let count = gitConfig.repos.count
            let title = count == 1 ? "1 repo" : "\(count) repos"
            return SettingsConnectionStatus(
                title,
                tone: count > 0 ? .ok : .attention
            )
        case .vercel:
            guard vercelEditable else { return .unknown }
            return .signedIn(vercelConfig.signedIn)
        case .github:
            return tokenBackedStatus(
                stored: githubTokenStored, sourceID: "github")
        case .supabase:
            return tokenBackedStatus(
                stored: tokenStored, sourceID: "supabase")
        case .plausible:
            return tokenBackedStatus(
                stored: plausibleTokenStored, sourceID: "plausible")
        case .posthog:
            return tokenBackedStatus(
                stored: posthogTokenStored, sourceID: "posthog")
        case .sentry:
            return tokenBackedStatus(
                stored: sentryTokenStored, sourceID: "sentry")
        case .datadog:
            return tokenBackedStatus(
                stored: datadogKeysStored, sourceID: "datadog")
        case .axiom:
            return tokenBackedStatus(
                stored: axiomTokenStored, sourceID: "axiom")
        case .openrouter:
            return tokenBackedStatus(
                stored: openrouterTokenStored, sourceID: "openrouter")
        case .aiGateway:
            return tokenBackedStatus(
                stored: aiGatewayTokenStored, sourceID: "ai-gateway")
        case .local:
            let on = sources.first(where: { $0.id == "local" })?.enabled ?? true
            return SettingsConnectionStatus(
                on ? HeadroomCopy.on : HeadroomCopy.off,
                tone: on ? .ok : .attention
            )
        }
    }

    /// Key present + whether the last poll accepted it. Hub and detail share
    /// this so "Connected" never outranks "token rejected".
    func tokenBackedStatus(
        stored: Bool, sourceID: String
    ) -> SettingsConnectionStatus {
        if !stored {
            return .connected(false)
        }
        guard let source = sources.first(where: { $0.id == sourceID }) else {
            return SettingsConnectionStatus(
                HeadroomCopy.inKeychain,
                tone: .ok,
                symbol: "key.fill"
            )
        }
        if source.ok == true {
            return SettingsConnectionStatus(
                source.detail ?? HeadroomCopy.connected,
                tone: .good,
                symbol: "checkmark.circle.fill"
            )
        }
        if let error = source.error, !error.isEmpty {
            return SettingsConnectionStatus(
                error,
                tone: .attention,
                symbol: "exclamationmark.triangle.fill"
            )
        }
        if let detail = source.detail, !detail.isEmpty {
            return SettingsConnectionStatus(
                detail,
                tone: .attention,
                symbol: "exclamationmark.triangle.fill"
            )
        }
        return SettingsConnectionStatus(
            HeadroomCopy.inKeychain,
            tone: .attention,
            symbol: "key.fill"
        )
    }

    @ViewBuilder
    func integrationPane(_ kind: SettingsIntegration) -> some View {
        Form {
            visibilitySection(kind)
            if kind.sharesActivityRowLimit {
                activityRowLimitSection
            }
            switch kind {
            case .claudeCode: claudeCodeSections
            case .codex: codexSections
            case .git: gitSections
            case .github: githubSections
            case .vercel: vercelSections
            case .supabase: supabaseSections
            case .plausible: plausibleSections
            case .posthog: posthogSections
            case .sentry: sentrySections
            case .datadog: datadogSections
            case .axiom: axiomSections
            case .openrouter: openrouterSections
            case .aiGateway: aiGatewaySections
            case .local: localWatchSections
            }
        }
        .formStyle(.grouped)
    }

    /// Shared by the Integrations hub and every leaf that feeds Recent.
    /// One `@AppStorage` — changing it on Vercel changes it on Git too.
    @ViewBuilder
    var activityRowLimitSection: some View {
        Section {
            Stepper(
                "\(HeadroomCopy.activity) rows: \(activityRowLimit)",
                value: $activityRowLimit,
                in: Self.activityRowLimitRange
            )
        } footer: {
            Text(HeadroomCopy.activityRowsHint)
        }
    }

    private var localWatchSections: some View {
        Section {
            if let local = sources.first(where: { $0.id == "local" }) {
                Toggle(
                    HeadroomCopy.showInHeadroom,
                    isOn: Binding(
                        get: { local.enabled ?? true },
                        set: { on in
                            Task {
                                await setSourceRows([local.id], enabled: on)
                            }
                        }
                    )
                )
                .disabled(togglingSourceID == local.id)
            }
            Stepper(
                "\(HeadroomCopy.localServers): \(serverRowLimit)",
                value: $serverRowLimit,
                in: Self.serverRowLimitRange
            )
            Toggle("Confirm before stopping servers", isOn: $confirmServerStops)
        } footer: {
            Text("Listening ports and Xcode builds on this Mac. Off stops both; the same toggle lives on the Integrations list.")
        }
    }

    /// Leaf on/off for a keyed dev tool. AI-group integrations keep their
    /// switch on Sources; Local has Show in Headroom on its own leaf (and the
    /// same on/off on the catalog rows for servers/builds).
    @ViewBuilder
    private func visibilitySection(_ kind: SettingsIntegration) -> some View {
        if kind == .local { EmptyView() }
        else if let source = sources.first(where: { $0.id == kind.rawValue }),
                source.sourceGroup == .devtools {
            Section {
                Toggle(HeadroomCopy.showInHeadroom, isOn: Binding(
                    get: { source.enabled ?? true },
                    set: { on in
                        Task { await setSourceRows([source.id], enabled: on) }
                    }
                ))
                .disabled(togglingSourceID == source.id)
            } footer: {
                Text("Off stops polling and hides its rows. The key stays in the Keychain — \(HeadroomCopy.settingsDisconnect) is what forgets it.")
            }
        }
    }

    /// The house About pane. The publisher shares the credit line; the
    /// open-source note stays as the last footer.
    var aboutPane: some View {
        AboutSettingsPane(
            appName: HeadroomCopy.product,
            credit: "\(HeadroomCopy.createdBy)\n\(HeadroomCopy.publisher)",
            repository: "michellzappa/headroom",
            links: [AboutLink(HeadroomCopy.aboutCommunityPulse, url: HeadroomTelemetry.communityURL)]
        ) {
            Section {
            } footer: {
                Text(HeadroomCopy.aboutOpenSourceFooter)
            }
        }
    }
}
