import SwiftUI

extension SettingsView {
    var sourcesPane: some View {
        VStack(spacing: 0) {
            quotaResetProviderBar
            SettingsSourcesPane(
                sources: sources,
                usage: usageProviders,
                accountProviders: accountProviders,
                detected: detectedSources,
                busyID: togglingSourceID,
                isSyncing: isSyncing,
                message: sourcesMessage,
                dropTargetID: dropTargetID,
                onToggleRows: { ids, enabled in
                    Task { await setSourceRows(ids, enabled: enabled) }
                },
                onDismissRows: { ids in
                    Task { await dismissSourceRows(ids) }
                },
                onRemoveAccount: { id in
                    Task { await removeAccount(id) }
                },
                onAddAccount: { provider in
                    addingAccountProvider = provider
                },
                onRefresh: { ids in
                    Task { await refreshSources(ids) }
                },
                onMoveService: { dragged, target in
                    dropTargetID = nil
                    Task { await moveService(dragged, before: target) }
                },
                onNudgeService: { id, offset in
                    Task { await nudgeService(id, by: offset) }
                },
                onDropTarget: { id, targeted in
                    dropTargetID = targeted ? id : nil
                },
                onAccent: { ids, hex in
                    Task { await setAccents(ids, hex: hex) }
                },
                onTitle: { id, name in
                    Task { await setTitle(id, name: name) }
                }
            )
        }
        .sheet(item: $addingAccountProvider) { provider in
            AddAccountSheet(provider: provider, endpoint: endpoint) {
                await reloadSources()
            }
        }
    }

    var quotaResetProviderBar: some View {
        HStack(spacing: 12) {
            Toggle(
                HeadroomCopy.notifyOnQuotaReset,
                isOn: $notifyOnQuotaReset
            )
            .onChange(of: notifyOnQuotaReset) { _, enabled in
                if enabled {
                    Task { await ResetNotifications.requestAuthorization() }
                }
            }
            Spacer()
            Text("Ask macOS before showing a reset notification.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay(alignment: .bottom) { Divider() }
    }

    /// Mirrors `sources_config.FOCUS_LIMIT`.
    var focusLimit: Int { 3 }

    /// AI services in pinned order, each carrying its account ids as a block.
    /// The wire order stays account-level; the pane reorders services.
    var aiServiceBlocks: [(id: String, rowIDs: [String])] {
        SourceService.services(from: sources)
            .filter { $0.group == .ai }
            .map { ($0.id, $0.rows.map(\.id)) }
    }

    /// Drop `dragged` into `target`'s slot, moving the service's accounts as
    /// one block. The list sent is the whole AI group including disabled
    /// rows — a service you turned off keeps its place rather than sinking.
    func moveService(_ dragged: String, before target: String) async {
        guard dragged != target else { return }
        var blocks = aiServiceBlocks
        guard let from = blocks.firstIndex(where: { $0.id == dragged }) else {
            return
        }
        let moved = blocks.remove(at: from)
        guard let to = blocks.firstIndex(where: { $0.id == target }) else {
            return
        }
        blocks.insert(moved, at: to)
        await commitOrder(blocks.flatMap(\.rowIDs), movedID: dragged)
    }

    /// Keyboard / VoiceOver path to the same reorder, so pinning isn't
    /// drag-only.
    func nudgeService(_ id: String, by offset: Int) async {
        var blocks = aiServiceBlocks
        guard let from = blocks.firstIndex(where: { $0.id == id }) else {
            return
        }
        let to = from + offset
        guard blocks.indices.contains(to) else { return }
        blocks.swapAt(from, to)
        await commitOrder(blocks.flatMap(\.rowIDs), movedID: id)
    }

    /// Store an accent for each requested source row, then reload. The
    /// Sources pane sends only the base row for a multi-account service;
    /// account shades are derived host-side, while explicit account colors
    /// remain independent.
    func setAccents(_ ids: [String], hex: String?) async {
        togglingSourceID = ids.first
        defer { togglingSourceID = nil }
        do {
            for id in ids {
                _ = try await client.setSourceAccent(id, hex: hex)
            }
            // Colors are presentation only — the host republished the cached
            // document, so re-reading it is the whole update.
            await reloadSources()
            sourcesMessage = hex == nil
                ? "Restored the default color."
                : "Color updated — menu bar, rings and iPhone follow."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    func setTitle(_ id: String, name: String?) async {
        togglingSourceID = id
        defer { togglingSourceID = nil }
        do {
            _ = try await client.setSourceTitle(id, name: name)
            await reloadSources()
            sourcesMessage = name == nil
                ? "Restored the default name."
                : "Renamed — menu bar, rings and iPhone follow."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    /// Remove an extra login. The host re-execs to rebuild its registry, so
    /// this waits for the restart the same way the add sheet does.
    func removeAccount(_ id: String) async {
        togglingSourceID = id
        defer { togglingSourceID = nil }
        let before = try? await client.health().uptimeS
        do {
            _ = try await client.removeAccount(id)
            sourcesMessage = "Removed \(id). Restarting host…"
            await AddAccountSheet.waitForRestart(
                client: client, previousUptime: before)
            await reloadSources()
            sourcesMessage = "Removed \(id)."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    func commitOrder(_ order: [String], movedID: String) async {
        togglingSourceID = movedID
        defer { togglingSourceID = nil }
        // Reordering is local bookkeeping — nothing to refetch.
        do {
            _ = try await client.setSourceOrder(order)
            await reloadSources()
            sourcesMessage = "Reordered — top \(focusLimit) drive the menu bar."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    /// Toggle a whole service or a single account — same path, different id
    /// lists. One POST either way, so three Claude accounts flip together
    /// instead of racing three writes.
    func setSourceRows(_ ids: [String], enabled: Bool) async {
        guard let first = ids.first else { return }
        togglingSourceID = first
        defer { togglingSourceID = nil }
        do {
            var map = Dictionary(
                uniqueKeysWithValues: sources.map { ($0.id, $0.enabled ?? true) })
            for id in ids { map[id] = enabled }
            // Turning on also un-dismisses: a Library chip tap and an Active
            // toggle are the same write, and both must land the row in
            // Active. Turning off sends no dismissed key — that's a pause,
            // and the row stays put.
            _ = try await client.setSources(
                map,
                dismissed: enabled
                    ? Dictionary(uniqueKeysWithValues: ids.map { ($0, false) })
                    : nil)
            // Toggling on kicks a refresh host-side; wait for it to land rather
            // than guessing how long it takes.
            if enabled {
                await client.waitForRefresh(sources: ids)
            }
            await reloadSources()
            let names = ids.joined(separator: ", ")
            sourcesMessage = enabled
                ? "Enabled \(names) — ESP32 will show it on next poll."
                : "Paused \(names) — stays listed, stops fetching; ESP32 will hide that page."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    /// The row's ✕: back to the Library. The host flips `dismissed` and
    /// disables the rows in the same write.
    func dismissSourceRows(_ ids: [String]) async {
        guard let first = ids.first else { return }
        togglingSourceID = first
        defer { togglingSourceID = nil }
        do {
            _ = try await client.setSources(
                [:],
                dismissed: Dictionary(
                    uniqueKeysWithValues: ids.map { ($0, true) }))
            await reloadSources()
            sourcesMessage =
                "Moved \(ids.joined(separator: ", ")) to the Library."
        } catch {
            sourcesMessage = error.localizedDescription
        }
    }

    func refreshSources(_ ids: [String]?) async {
        isSyncing = true
        defer { isSyncing = false }
        do {
            try await client.refresh(sources: ids)
            // /sync/refresh answers 202 and works in the background.
            await client.waitForRefresh(sources: ids)
            await reloadSources()
            if ids == ["supabase"] {
                supabaseMessage = sources
                    .first(where: { $0.id == "supabase" })?
                    .detail ?? "Supabase refreshed"
            }
            if ids == ["plausible"] {
                plausibleMessage = sources
                    .first(where: { $0.id == "plausible" })?
                    .detail ?? "Plausible refreshed"
            }
            if ids == ["posthog"] {
                posthogMessage = sources
                    .first(where: { $0.id == "posthog" })?
                    .detail ?? "PostHog refreshed"
            }
            if ids == ["openrouter"] {
                openrouterMessage = sources
                    .first(where: { $0.id == "openrouter" })?
                    .detail ?? "OpenRouter refreshed"
            }
            if ids == ["ai-gateway"] {
                aiGatewayMessage = sources
                    .first(where: { $0.id == "ai-gateway" })?
                    .detail ?? "AI Gateway refreshed"
            }
            sourcesMessage = "Synced."
        } catch {
            sourcesMessage = error.localizedDescription
        }
        tokenStored = TokenStore.supabase.exists()
        plausibleTokenStored = TokenStore.plausible.exists()
        posthogTokenStored = TokenStore.posthog.exists()
        githubTokenStored = TokenStore.github.exists()
        openrouterTokenStored = TokenStore.openrouter.exists()
        aiGatewayTokenStored = TokenStore.aiGateway.exists()
    }

    func reloadSources() async {
        do {
            let snapshot = try await client.fetchUsage()
            sources = snapshot.sources ?? []
            menuBarPreviewSnapshot = snapshot
            usageProviders = Dictionary(
                (snapshot.providers ?? []).map { ($0.id, $0) },
                uniquingKeysWith: { first, _ in first })
            if let range = snapshot.plausible?.range {
                plausibleRange = range
            }
            if let range = snapshot.posthog?.range {
                posthogRange = range
            }
            servicesOrder = IntegrationWatch.activityBlocks(
                from: snapshot.integrationsOrder ?? snapshot.servicesOrder
            ).map(\.rawValue)
            integrationsOrder = IntegrationWatch.ordered(
                from: snapshot.integrationsOrder ?? snapshot.servicesOrder
            ).map(\.rawValue)
            if sources.isEmpty {
                sourcesMessage = "Host has no sources payload — restart com.centaur-labs.headroom."
            }
        } catch {
            sourcesMessage = error.localizedDescription
        }
        // Capability and detection are additive context: a host predating
        // either endpoint just means no "Add account…" links and no dimmed
        // chips, never an error in the pane.
        if let accounts = try? await client.fetchAccounts() {
            accountProviders = accounts.providers
        }
        if let setup = try? await client.fetchSetup() {
            detectedSources = Dictionary(
                setup.sources.map { ($0.id, $0.detected) },
                uniquingKeysWith: { first, _ in first })
        }
    }
}
