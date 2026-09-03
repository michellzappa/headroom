import XCTest
@testable import Headroom

/// Decodes the same fixture the Python side checks, through the real models.
/// A renamed host key shows up here as a nil field rather than as a blank row
/// someone notices weeks later. The Python half is host/test_contract.py.
final class ContractTests: XCTestCase {

    func testProviderIconsCoverEveryQuotaSourceAndNamedAccounts() {
        let expectedAssets = [
            "claude": "ProviderClaude",
            "codex": "ProviderCodex",
            "cursor": "ProviderCursor",
            "copilot": "ProviderCopilot",
            "gemini": "ProviderGemini",
            "windsurf": "ProviderWindsurf",
            "jetbrains": "ProviderJetBrains",
            "zed": "ProviderZed",
            "openrouter": "ProviderOpenRouter",
            "ai-gateway": "ProviderAIGateway",
            "git": "ProviderGit",
            "github": "ProviderGitHub",
            "vercel": "ProviderVercel",
            "supabase": "ProviderSupabase",
            "plausible": "ProviderPlausible",
            "posthog": "ProviderPostHog",
        ]

        for (providerID, assetName) in expectedAssets {
            XCTAssertEqual(ProviderIcon.assetName(for: providerID), assetName)
            XCTAssertEqual(
                ProviderIcon.assetName(for: "\(providerID):work"),
                assetName
            )
        }
        // Activity / Attention kinds that alias a source.
        XCTAssertEqual(ProviderIcon.assetName(for: "deployment"), "ProviderVercel")
        XCTAssertEqual(ProviderIcon.assetName(for: "commit"), "ProviderGit")
        XCTAssertEqual(ProviderIcon.assetName(for: "github-inbox"), "ProviderGitHub")
        XCTAssertEqual(ProviderIcon.assetName(for: "supabase-security"), "ProviderSupabase")
        XCTAssertEqual(ProviderIcon.assetName(for: "claude-status"), "ProviderClaude")
        XCTAssertEqual(ProviderIcon.sourceID(forKind: "deployment"), "vercel")
        XCTAssertNil(ProviderIcon.sourceID(forKind: "stale"))
        XCTAssertNil(ProviderIcon.assetName(for: "unknown"))
    }

    /// docs/demo_usage.json, located relative to this source file so the
    /// fixture doesn't have to be copied into the test bundle.
    private func demoFixtureURL() throws -> URL {
        let thisFile = URL(fileURLWithPath: #filePath)
        let repoRoot = thisFile
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // macos
            .deletingLastPathComponent()   // repo root
        let url = repoRoot
            .appendingPathComponent("docs")
            .appendingPathComponent("demo_usage.json")
        try XCTSkipUnless(
            FileManager.default.fileExists(atPath: url.path),
            "docs/demo_usage.json not found at \(url.path)")
        return url
    }

    private func decodeDemo() throws -> UsageSnapshot {
        let data = try Data(contentsOf: try demoFixtureURL())
        return try JSONDecoder().decode(UsageSnapshot.self, from: data)
    }

    func testDemoFixtureDecodesEveryTopLevelSection() throws {
        let snapshot = try decodeDemo()

        XCTAssertNotNil(snapshot.updated, "updated")
        XCTAssertNotNil(snapshot.plan, "plan")
        XCTAssertEqual(snapshot.quotaOK, true)
        XCTAssertNotNil(snapshot.sessionPct, "session_pct")
        XCTAssertNotNil(snapshot.weekPct, "week_pct")
        XCTAssertNotNil(snapshot.today, "today")
        XCTAssertNotNil(snapshot.byDay, "by_day")
        XCTAssertNotNil(snapshot.providers, "providers")
        XCTAssertGreaterThanOrEqual(snapshot.providers?.count ?? 0, 1)
        XCTAssertTrue(
            Set(snapshot.activeQuotaProviders.map(\.rawValue))
                .isSubset(of: Set(snapshot.providers?.map(\.id) ?? [])))
        XCTAssertEqual(
            snapshot.activeQuotaProviders.map(\.rawValue),
            ["claude", "codex", "cursor"])
        XCTAssertTrue(
            Set(["claude", "codex", "cursor", "openrouter", "ai-gateway"])
                .isSubset(of: Set(snapshot.visibleQuotaProviders.map(\.id))))
        XCTAssertEqual(
            Set(snapshot.codingQuotaProviders.map(\.id)),
            Set(["claude", "codex", "cursor"]))
        XCTAssertEqual(
            Set(snapshot.balanceProviders.map(\.id)),
            Set(["openrouter", "ai-gateway"]))
        let openrouter = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "openrouter" })
        XCTAssertEqual(openrouter.primaryBalance?.meterKind, "balance")
        XCTAssertEqual(openrouter.primaryBalance?.headroom?.unit, "usd")
        XCTAssertEqual(openrouter.primaryBalance?.headroom?.value, 62.5)
        XCTAssertTrue(openrouter.isBalanceOnly)
        let spend = try XCTUnwrap(openrouter.spend)
        XCTAssertEqual(spend.todayUSD, 2.4)
        XCTAssertEqual(spend.runwayDays, 50.0)
        XCTAssertEqual(spend.byModel?.first?.id, "anthropic/claude-sonnet-4")
        XCTAssertEqual(spend.byKey?.first?.name, "headroom-dev")
        let gateway = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "ai-gateway" })
        XCTAssertEqual(gateway.spend?.periodUSD, 4.5)
        XCTAssertNotNil(snapshot.codex, "codex")
        XCTAssertNotNil(snapshot.cursor, "cursor")
        XCTAssertNotNil(snapshot.vercel, "vercel")
        XCTAssertNotNil(snapshot.git, "git")
        XCTAssertNotNil(snapshot.local, "local")
        XCTAssertNotNil(snapshot.plausible, "plausible")
        XCTAssertEqual(snapshot.plausible?.sites?.count, 2)
        XCTAssertNotNil(snapshot.posthog, "posthog")
        XCTAssertEqual(snapshot.posthog?.projects?.count, 2)
        XCTAssertNotNil(snapshot.sources, "sources")
        XCTAssertNotNil(snapshot.attention, "attention")
    }

    func testMixedActivityHistoryDecodesItsSparseDetails() throws {
        let snapshot = try decodeDemo()
        let activity = try XCTUnwrap(snapshot.activityHistory)
        XCTAssertEqual(activity.source, "mixed")
        XCTAssertEqual(activity.levels?.count, 14)
        XCTAssertEqual(activity.activeDays, 11)
        let busiest = try XCTUnwrap(activity.day(for: "2026-07-21"))
        XCTAssertEqual(busiest.level, 4)
        XCTAssertEqual(busiest.activeMinutes, 212)
        XCTAssertEqual(busiest.sources, ["claude", "codex", "cursor"])
    }

    func testCurrencyLabelsUseThousandsSeparators() {
        XCTAssertEqual(12_475.0.dollarLabel, "$12,475")
        XCTAssertEqual(1_234.5.dollarLabel, "$1,235")
        XCTAssertEqual(
            HeadroomFormat.usd(1_234.5, maximumFractionDigits: 2),
            "$1,234.50"
        )
    }

    func testEveryProviderMeterResolves() throws {
        let snapshot = try decodeDemo()
        for provider in snapshot.activeQuotaProviders {
            let meter = snapshot.meter(for: provider)
            XCTAssertTrue(meter.ok, "\(provider.title) should be ok in the fixture")
            XCTAssertNotNil(
                meter.headline.percent,
                "\(provider.title) headline percent decoded as nil — the host "
                + "key it reads was probably renamed")
        }
    }

    func testCodexResetCreditsSurfaceOnTheMeter() throws {
        let snapshot = try decodeDemo()
        XCTAssertEqual(snapshot.codex?.resetCreditsAvailable, 2)
        XCTAssertEqual(
            snapshot.codex?.resetCreditsExpiries,
            ["6d 5h", "18d 3h"]
        )
        XCTAssertEqual(
            snapshot.codex?.resetCreditsExpireAt,
            [1785330000, 1786359600]
        )
        let meter = snapshot.meter(for: .codex)
        XCTAssertEqual(meter.resetCreditsLabel, "2 reset credits")
        XCTAssertEqual(meter.resetCreditsExpiryLabel, "6d 5h · 18d 3h")
    }

    func testSourceRowsCarryTheBrandAccentSettingsPaintsWith() throws {
        let snapshot = try decodeDemo()
        let sources = try XCTUnwrap(snapshot.sources)
        let claude = try XCTUnwrap(sources.first { $0.id == "claude" })
        XCTAssertEqual(claude.accent, "#D97757")
        // Same hex the rings and the firmware palette use — one source.
        XCTAssertEqual(
            claude.accent,
            snapshot.providers?.first { $0.id == "claude" }?.accent)
        // Rows with no brand fall back to the status color, so nil is fine.
        XCTAssertNil(sources.first { $0.id == "git" }?.accent)
    }

    func testProviderCarriesSubscriptionPricingCatalog() throws {
        let snapshot = try decodeDemo()
        let claude = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "claude" })
        let pricing = try XCTUnwrap(claude.subscriptionPricing)
        XCTAssertEqual(pricing.currency, "USD")
        XCTAssertEqual(pricing.checked, "2026-08-01")
        XCTAssertEqual(pricing.url, "https://www.anthropic.com/pricing")
        let pro = try XCTUnwrap(pricing.plans?.first { $0.id == "pro" })
        XCTAssertEqual(pro.monthlyUSD, 20)
        XCTAssertEqual(pro.annualUSD, 200)
        XCTAssertEqual(pro.compactPrice, "$20 / user / mo · $200 / user / yr")
        XCTAssertEqual(
            pricing.currentPrice(for: "Max 5x")?.compactPrice,
            "$100 / user / mo · $1,200 / user / yr")
        XCTAssertNil(pricing.currentPrice(for: "Team"))

        let codex = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "codex" })
        XCTAssertEqual(
            codex.subscriptionPricing?.currentPrice(for: codex.plan)?.compactPrice,
            "$25 / user / mo · $240 / user / yr")
    }

    /// A malformed plan row costs that row, never the provider carrying it.
    /// Before plans went lossy, one bad row threw out of subscription_pricing
    /// and the row-lossy providers[] decode silently dropped the whole
    /// provider — ring, meter, burndown and Activity leaf all gone over one
    /// registry typo.
    func testBadSubscriptionPlanRowKeepsTheProvider() throws {
        let json = """
        {
          "providers": [
            {
              "id": "claude",
              "title": "Claude",
              "plan": "Pro",
              "subscription_pricing": {
                "currency": "USD",
                "plans": [
                  {"id": 42, "title": ["not", "a", "string"]},
                  {"title": "Untitled tier", "monthly_usd": 5},
                  {"id": "pro", "title": "Pro", "monthly_usd": 20}
                ]
              }
            },
            {
              "id": "codex",
              "subscription_pricing": {"currency": "USD"}
            }
          ]
        }
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))

        let claude = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "claude" })
        let pricing = try XCTUnwrap(claude.subscriptionPricing)
        // The unparseable row is dropped; the id-less one survives.
        XCTAssertEqual(pricing.plans?.count, 2)
        XCTAssertEqual(
            pricing.currentPrice(for: "Pro")?.monthlyUSD, 20)
        XCTAssertNil(pricing.currentPrice(for: "Enterprise"))

        // A catalog with no plans key at all keeps its provider too.
        let codex = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "codex" })
        XCTAssertNil(codex.subscriptionPricing?.plans)
        XCTAssertNil(codex.subscriptionPricing?.currentPrice(for: "Plus"))
    }

    func testFocusPicksTheProvidersTheHostChose() throws {
        var snapshot = try decodeDemo()
        XCTAssertEqual(snapshot.focus, ["claude", "codex", "cursor"])
        XCTAssertEqual(
            snapshot.focusProviders().map(\.id), ["claude", "codex", "cursor"])
        XCTAssertEqual(snapshot.providers?.first?.rank, 0)

        snapshot.focus = ["cursor", "claude"]
        XCTAssertEqual(snapshot.focusProviders().map(\.id), ["cursor", "claude"])

        // Never more than the compact surfaces can draw.
        snapshot.focus = ["cursor", "claude", "codex", "claude"]
        XCTAssertEqual(snapshot.focusProviders().count, 3)
    }

    func testFocusFallsBackWhenTheHostIsOlderOrTheIDsAreStale() throws {
        var snapshot = try decodeDemo()
        snapshot.focus = nil
        XCTAssertEqual(
            snapshot.focusProviders().map(\.id), ["claude", "codex", "cursor"])

        // Every focus id unresolvable between polls — show something.
        snapshot.focus = ["nope", "gone"]
        XCTAssertFalse(snapshot.focusProviders().isEmpty)
    }

    func testSourcesCarryTheFieldsSettingsRenders() throws {
        let snapshot = try decodeDemo()
        let sources = try XCTUnwrap(snapshot.sources)
        XCTAssertFalse(sources.isEmpty)
        for source in sources {
            XCTAssertFalse(source.id.isEmpty)
            XCTAssertNotNil(source.title, "\(source.id) title")
            XCTAssertNotNil(source.kind, "\(source.id) kind")
        }
        XCTAssertEqual(
            sources.filter { $0.kind == "quota" }.map(\.id),
            ["claude", "codex", "cursor", "openrouter", "ai-gateway"])
        let github = try XCTUnwrap(sources.first { $0.id == "github" })
        XCTAssertEqual(github.title, HeadroomCopy.githubActions)
    }

    func testSourcesSplitIntoAIAndDevToolSections() throws {
        let snapshot = try decodeDemo()
        let sources = try XCTUnwrap(snapshot.sources)
        let grouped = sources.groupedBySourceGroup()
        XCTAssertEqual(grouped.map(\.group), [.ai, .devtools])
        XCTAssertEqual(
            grouped.first { $0.group == .ai }?.sources.map(\.id),
            ["claude", "codex", "cursor", "openrouter", "ai-gateway", "claude-status"])
        let ai = try XCTUnwrap(grouped.first { $0.group == .ai })
        XCTAssertEqual(
            ai.sources.first { $0.id == "claude-status" }?.kind, "activity")
        let devtools = try XCTUnwrap(grouped.first { $0.group == .devtools })
        XCTAssertTrue(devtools.sources.contains { $0.id == "plausible" })
        XCTAssertTrue(devtools.sources.contains { $0.id == "posthog" })
        XCTAssertFalse(devtools.sources.contains { $0.kind == "quota" })
    }

    func testSourceGroupFallsBackToKindOnOlderHosts() {
        // Hosts before the split only sent `kind`; quota meant a coding tool.
        XCTAssertEqual(SourceGroup(group: nil, kind: "quota"), .ai)
        XCTAssertEqual(SourceGroup(group: nil, kind: "activity"), .devtools)
        XCTAssertEqual(SourceGroup(group: "devtools", kind: "quota"), .devtools)
    }

    func testHeadroomCopyMatchesGlossaryTerms() {
        XCTAssertEqual(HeadroomCopy.dailyBurn, "Daily burn")
        XCTAssertEqual(HeadroomCopy.overallBurndown, "Overall burndown")
        XCTAssertEqual(HeadroomCopy.burndown, "Burndown")
        XCTAssertEqual(HeadroomCopy.activity, "Activity")
        XCTAssertEqual(HeadroomCopy.services, "Services")
        XCTAssertEqual(HeadroomCopy.codingQuotas, "Coding quotas")
        XCTAssertEqual(HeadroomCopy.allClear, "All clear")
        XCTAssertEqual(HeadroomCopy.connected, "Connected")
        XCTAssertEqual(HeadroomCopy.notConnected, "Not connected")
        XCTAssertEqual(HeadroomCopy.inKeychain, "Keychain")
        XCTAssertEqual(HeadroomCopy.settingsKeySavedPrompt, "••••••••••••")
        XCTAssertEqual(HeadroomCopy.macUnavailable, "Mac unavailable")
        XCTAssertEqual(HeadroomCopy.hooksInstalled, "Hooks installed")
        XCTAssertEqual(HeadroomCopy.gatewayOn, "Gateway on")
        XCTAssertEqual(HeadroomCopy.settingsStatus, "Status")
        XCTAssertEqual(HeadroomCopy.on, "On")
        XCTAssertEqual(HeadroomCopy.off, "Off")
        XCTAssertEqual(HeadroomCopy.collectingHistory, "Collecting history")
        XCTAssertEqual(
            HeadroomCopy.overallBurndownSubtitle, "7 days around today")
        XCTAssertEqual(HeadroomCopy.overallBurndownSubtitleShort, "±3.5d")
        XCTAssertEqual(HeadroomCopy.windowFrame, "This window")
        XCTAssertEqual(HeadroomCopy.windowSliceFrame, "7 days of this window")
        XCTAssertEqual(HeadroomCopy.noHistoryYet, "No history yet")
        XCTAssertEqual(HeadroomCopy.noCodingSources, "No coding sources")
        XCTAssertEqual(HeadroomCopy.dismissAll, "Dismiss all")
        XCTAssertEqual(HeadroomCopy.clearAttention, HeadroomCopy.dismissAll)
        XCTAssertEqual(HeadroomCopy.githubActions, "GitHub Actions")
        XCTAssertEqual(HeadroomCopy.openAtLogin, "Open at Login")
        XCTAssertEqual(HeadroomCopy.poolBurndown("Weekly"), "Weekly burndown")
        XCTAssertEqual(HeadroomCopy.resets("3d"), "Resets 3d")
    }

    /// Percent is the only unit Headroom claims. Every provider bills in a
    /// real one of its own — points, credits, premium requests — so a figure
    /// labelled "pts" here reads as a number from somewhere else.
    func testQuotaFiguresAreLabelledInPercent() {
        XCTAssertEqual(HeadroomCopy.dailyBurnUnit, "% / day")
        XCTAssertEqual(HeadroomCopy.resetGranted(forgivenPct: 42),
                       "Reset granted · 42% back")
        XCTAssertFalse(HeadroomCopy.dailyBurnUnit.contains("pts"))
    }

    /// Service health answers the same question source health does: wait, or
    /// go and do something. "Unavailable" answered neither.
    func testServiceHealthNamesTheFixableCase() {
        XCTAssertEqual(HeadroomCopy.serviceStatus("Supabase", configured: false),
                       "Supabase needs a key")
        XCTAssertEqual(HeadroomCopy.serviceStatus("Supabase", configured: true),
                       "Supabase not reporting")
        XCTAssertEqual(HeadroomCopy.serviceStatus("Plausible", configured: nil),
                       "Plausible not reporting")
    }

    func testDisabledQuotaProviderIsHidden() throws {
        var snapshot = try decodeDemo()
        snapshot.sources = snapshot.sources?.map { source in
            var row = source
            if row.id == "cursor" { row.enabled = false }
            return row
        }
        snapshot.providers = snapshot.providers?.map { row in
            var provider = row
            if provider.id == "cursor" { provider.enabled = false }
            return provider
        }
        // UsageProvider is the fixed three-case legacy enum; openrouter and
        // ai-gateway are source-registry providers and never appear here.
        XCTAssertEqual(
            snapshot.activeQuotaProviders.map(\.rawValue),
            ["claude", "codex"])
        XCTAssertEqual(
            snapshot.visibleQuotaProviders.map(\.id),
            ["claude", "codex", "openrouter", "ai-gateway"])
        XCTAssertEqual(
            snapshot.codingQuotaProviders.map(\.id),
            ["claude", "codex"])
        XCTAssertEqual(
            snapshot.balanceProviders.map(\.id),
            ["openrouter", "ai-gateway"])
    }

    func testEmptyQuotaSourcesYieldNoActiveProviders() throws {
        var snapshot = try decodeDemo()
        snapshot.sources = snapshot.sources?.map { source in
            var row = source
            if row.kind == "quota" { row.enabled = false }
            return row
        }
        // Even if providers[] still advertise enabled (stale), sources win.
        XCTAssertTrue(snapshot.activeQuotaProviders.isEmpty)
        XCTAssertTrue(snapshot.visibleQuotaProviders.isEmpty)

        snapshot.providers = snapshot.providers?.map { row in
            var provider = row
            provider.enabled = false
            return provider
        }
        XCTAssertTrue(snapshot.visibleQuotaProviders.isEmpty)
    }

    func testProvidersOnlyPayloadUsesEnabledFlags() throws {
        let json = """
        {
          "providers": [
            {"id": "claude", "kind": "quota", "enabled": true},
            {"id": "codex", "kind": "quota", "enabled": false},
            {"id": "gemini", "title": "Gemini", "kind": "quota", "enabled": true,
             "ok": true, "headline": "week",
             "pools": {
               "week": {"title": "Weekly", "pct": 20.0, "ring": true}
             }}
          ]
        }
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        XCTAssertEqual(
            snapshot.visibleQuotaProviders.map(\.id),
            ["claude", "gemini"])
        // Known-enum helper still lists only branded ids.
        XCTAssertEqual(
            snapshot.activeQuotaProviders.map(\.rawValue),
            ["claude"])
        // Mac Usage tabs / rings use codingQuotaProviders — unknown ids still
        // meter when they are window quotas.
        let gemini = snapshot.meter(forProviderID: "gemini")
        XCTAssertEqual(gemini.title, "Gemini")
        XCTAssertEqual(gemini.primary.percent, 20)
        XCTAssertEqual(
            DashboardSelection.tabs(for: snapshot.codingQuotaProviders),
            ["overview", "claude", "gemini"])
    }

    func testDashboardModesMatchTheMobileInformationArchitecture() {
        XCTAssertEqual(
            DashboardMode.allCases,
            [.overview, .attention, .activity])
        XCTAssertEqual(
            DashboardMode.allCases.map(\.title),
            [HeadroomCopy.usage, HeadroomCopy.attention, HeadroomCopy.activity])
    }

    func testActivityGroupsUseSharedFunctionOrder() throws {
        let rows = try JSONDecoder().decode(
            [ActivityItem].self,
            from: Data(
                """
                [
                  {"id":"commit", "kind":"commit", "subject":"Commit"},
                  {"id":"unknown", "kind":"new-source", "subject":"New"},
                  {"id":"github", "kind":"github", "subject":"Actions"}
                ]
                """.utf8
            )
        )
        let groups = ActivityGrouping.groups(from: rows)
        XCTAssertEqual(
            groups.map(\.title),
            [HeadroomCopy.githubActions, HeadroomCopy.gitCommits,
             HeadroomCopy.otherActivity])
        XCTAssertEqual(groups[0].rows.first?.subject, "Actions")
        XCTAssertEqual(groups[2].rows.first?.subject, "New")
    }

    func testInboxActivityCaptionNamesRepoAuthorAndNumber() throws {
        let row = try JSONDecoder().decode(
            ActivityItem.self,
            from: Data(
                """
                {
                  "id":"github-inbox:pr_1",
                  "kind":"github",
                  "status":"review_request",
                  "subject":"Tighten the menu bar glyph",
                  "repo":"acme/web",
                  "author":"alice",
                  "number":42,
                  "ago":"12m"
                }
                """.utf8
            )
        )
        let style = ActivityStatusStyle.resolve(row.status)
        XCTAssertEqual(
            row.caption(label: style.label),
            "\(HeadroomCopy.activityReviewRequest) · web · @alice · #42")
        // No verdict on the wire: status decides, same as every older host.
        XCTAssertTrue(row.needsAttention)
    }

    func testAnAgedInboxRowKeepsItsWordAndLeavesAttention() throws {
        // A year-old assignment is still `assigned` — the word is right and
        // the row belongs in the feed. Only the host knows it stopped being
        // attention, so it says so per row.
        let row = try JSONDecoder().decode(
            ActivityItem.self,
            from: Data("""
                {
                  "id":"github-inbox:100",
                  "kind":"github",
                  "status":"assigned",
                  "subject":"Limit headline to 200 chars",
                  "repo":"acme/core",
                  "needs_attention":false,
                  "ago":"371d"
                }
                """.utf8
            )
        )
        XCTAssertFalse(row.needsAttention)
        XCTAssertTrue(ActivityStatusStyle.resolve(row.status).needsAttention)
        XCTAssertEqual(
            row.caption(label: ActivityStatusStyle.resolve(row.status).label),
            "\(HeadroomCopy.activityAssigned) · core")
    }

    func testARateLimitedProviderSaysPausedNotNotUpdating() throws {
        // A 429 is the host backing off on purpose. Amber "Not updating" plus
        // the raw HTTP error is what made Refresh feel like the fix.
        let json = """
        {
          "providers": [
            {"id": "claude", "title": "Claude", "kind": "quota",
             "enabled": true, "ok": true, "stale": true,
             "stale_cause": "rate_limited", "retry_in_s": 300,
             "stale_for_s": 120,
             "error": "HTTP Error 429: Too Many Requests, retrying in 5m",
             "headline": "week",
             "pools": {
               "week": {"title": "Weekly", "pct": 35.0, "ring": true}
             }}
          ]
        }
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        let claude = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "claude" })
        XCTAssertTrue(claude.isRateLimited)
        XCTAssertFalse(claude.statusAlarming)
        XCTAssertEqual(claude.statusNote, "Paused · retries in 5m")
        XCTAssertNil(claude.displayError)
        let meter = snapshot.meter(for: claude)
        XCTAssertEqual(meter.statusNote, claude.statusNote)
        XCTAssertFalse(meter.statusAlarming)
        XCTAssertNil(meter.displayError)
    }

    func testAReplayedProviderSaysSoDespiteBeingOK() throws {
        // The host keeps ok=true on a replay — the numbers were real once —
        // so `stale` is the only thing standing between a frozen meter and a
        // surface that draws it as live.
        let json = """
        {
          "providers": [
            {"id": "claude", "title": "Claude", "kind": "quota",
             "enabled": true, "ok": true, "stale": true,
             "stale_for_s": 7200, "headline": "week",
             "pools": {
               "week": {"title": "Weekly", "pct": 35.0, "ring": true}
             }},
            {"id": "codex", "title": "Codex", "kind": "quota",
             "enabled": true, "ok": true,
             "pools": {
               "week": {"title": "Weekly", "pct": 12.0, "ring": true}
             }}
          ]
        }
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        let claude = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "claude" })
        XCTAssertEqual(claude.ok, true)
        XCTAssertTrue(claude.isStale)
        XCTAssertFalse(claude.needsSignIn)
        XCTAssertEqual(claude.statusNote, "Not updating · 2 hours ago")
        XCTAssertEqual(snapshot.meter(for: claude).statusNote, claude.statusNote)

        let codex = try XCTUnwrap(
            snapshot.providers?.first { $0.id == "codex" })
        XCTAssertFalse(codex.isStale)
        XCTAssertNil(codex.statusNote)
        XCTAssertNil(snapshot.meter(for: codex).statusNote)
    }

    func testADeadLoginSaysSignInRatherThanNotUpdating() throws {
        // The failure that sent us here: `ok` true, bars replayed, and the one
        // string naming the fix suppressed because nothing looked wrong. Both
        // flags are set on the wire, and the sign-in wording has to win —
        // "Not updating" reads as a hiccup to wait out, and this one never
        // clears on its own.
        let json = """
        {
          "providers": [
            {"id": "claude", "title": "Claude", "kind": "quota",
             "enabled": true, "ok": true, "stale": true,
             "auth_required": true, "stale_for_s": 41896,
             "error": "keychain has no claudeAiOauth.accessToken",
             "headline": "week",
             "pools": {
               "week": {"title": "Weekly", "pct": 53.0, "ring": true}
             }}
          ],
          "sources": [
            {"id": "claude", "title": "Claude", "kind": "quota",
             "enabled": true, "ok": true, "stale": true,
             "auth_required": true}
          ]
        }
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        let claude = try XCTUnwrap(snapshot.providers?.first)
        XCTAssertEqual(claude.ok, true)
        XCTAssertTrue(claude.isStale)
        XCTAssertTrue(claude.needsSignIn)
        XCTAssertTrue(claude.readingSuspect)
        XCTAssertEqual(claude.statusNote, "Needs sign-in · 12 hours ago")

        let meter = snapshot.meter(for: claude)
        XCTAssertTrue(meter.needsSignIn)
        XCTAssertTrue(meter.statusAlarming)
        XCTAssertEqual(meter.statusNote, claude.statusNote)
        // The card draws this whenever it is non-nil now, so the reason
        // reaches the reader while `ok` is still true.
        XCTAssertEqual(meter.displayError, "keychain has no claudeAiOauth.accessToken")

        let row = try XCTUnwrap(snapshot.sources?.first)
        XCTAssertTrue(row.needsSignIn)
    }

    func testAProviderThatNeverFetchedStillAsksForSignIn() throws {
        // No snapshot to replay, so nothing is stale and there is no age to
        // report — the card is empty and still owes a reason.
        let json = """
        {"providers": [{"id": "claude", "kind": "quota", "ok": false,
                        "auth_required": true}]}
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        let claude = try XCTUnwrap(snapshot.providers?.first)
        XCTAssertFalse(claude.isStale)
        XCTAssertTrue(claude.needsSignIn)
        XCTAssertTrue(claude.readingSuspect)
        XCTAssertEqual(claude.statusNote, "Needs sign-in")
    }

    func testAHostWithoutTheStaleFieldReadsAsFetching() throws {
        // Older hosts never send it; absent must not mean frozen.
        let json = """
        {"providers": [{"id": "claude", "kind": "quota", "ok": true}]}
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        let claude = try XCTUnwrap(snapshot.providers?.first)
        XCTAssertFalse(claude.isStale)
        XCTAssertFalse(claude.needsSignIn)
        XCTAssertNil(claude.statusNote)
    }

    func testMissingProvidersAndSourcesYieldEmptyActiveSet() throws {
        let snapshot = UsageSnapshot.empty
        XCTAssertTrue(snapshot.visibleQuotaProviders.isEmpty)
        XCTAssertTrue(snapshot.activeQuotaProviders.isEmpty)
    }

    func testHealthReportDecodesTheHostShape() throws {
        let json = """
        {
          "ok": true,
          "uptime_s": 128,
          "updated": "2026-07-25T14:32:00+0200",
          "built_age_s": 3,
          "sources": {
            "claude": {"ok": true, "stale": false, "enabled": true,
                       "age_s": 2, "error": null, "detail": "Max 5x · week 63%"},
            "github": {"ok": false, "stale": false, "enabled": true,
                       "age_s": null, "error": "not connected", "detail": null}
          }
        }
        """
        let report = try JSONDecoder().decode(
            HealthReport.self, from: Data(json.utf8))
        XCTAssertEqual(report.ok, true)
        XCTAssertEqual(report.uptimeS, 128)
        XCTAssertEqual(report.sources["claude"]?.ageS, 2)
        XCTAssertEqual(report.sources["github"]?.error, "not connected")
        XCTAssertNil(report.sources["github"]?.ageS)
    }

    func testClientDerivesEndpointsFromTheUsageURL() {
        let client = HeadroomClient(
            endpoint: "http://mz-mbp.local:8737/usage", token: "abc")
        XCTAssertEqual(client.token, "abc")
        XCTAssertEqual(client.endpoint, "http://mz-mbp.local:8737/usage")
    }

    func testLoopbackSkipsHostKeychain() {
        XCTAssertTrue(HeadroomClient.isLoopback("http://127.0.0.1:8737/usage"))
        XCTAssertTrue(HeadroomClient.isLoopback("http://localhost:8737/usage"))
        XCTAssertFalse(HeadroomClient.isLoopback("http://mz-mbp.local:8737/usage"))
        // Must not call TokenStore.host.read() — a wedged keychain freezes UI.
        let client = HeadroomClient(endpoint: "http://127.0.0.1:8737/usage")
        XCTAssertNil(client.token)
    }

    // MARK: - Contract handshake and lossy rows (docs/contract.md)

    func testContractDecodesAndOlderHostsStillCount() throws {
        let stated = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(#"{"contract": 7}"#.utf8))
        XCTAssertEqual(stated.contract, 7)
        XCTAssertTrue(stated.contractSatisfied)

        // A host predating the field speaks contract 1 by definition. Treating
        // nil as failure would have blanked every desk the day this shipped.
        let silent = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(#"{"plan": "max"}"#.utf8))
        XCTAssertNil(silent.contract)
        XCTAssertTrue(silent.contractSatisfied)
    }

    func testTheDemoFixtureSatisfiesThisBuildsFloor() throws {
        XCTAssertTrue(try decodeDemo().contractSatisfied)
    }

    func testOneMalformedRowCostsTheRowNotTheDocument() throws {
        // by_day rows require `date`. Before lossy decoding, a single row
        // missing it threw past the chart and blanked the whole popover.
        let json = """
        {
          "plan": "max",
          "by_day": [
            {"date": "2026-07-30", "total": 12.0},
            {"total": 9.0},
            {"date": "2026-07-31", "total": 3.0}
          ]
        }
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))

        XCTAssertEqual(snapshot.plan, "max", "the document survived")
        XCTAssertEqual(snapshot.byDay?.count, 2, "only the bad row was lost")
        XCTAssertEqual(
            snapshot.byDay?.map(\.date), ["2026-07-30", "2026-07-31"])
    }

    func testLossyRowsApplyToNestedListsToo() throws {
        // SupabaseProject.ref, SupabaseLint.name and GitHubRun.id are all
        // required, and all sit inside a parent that is itself optional.
        let json = """
        {
          "github": {"ok": true, "runs": [{"id": "1"}, {"repo": "no-id"}]},
          "supabase": {
            "ok": true,
            "projects": [
              {"ref": "abc", "lints": [{"name": "rls"}, {"title": "nameless"}]},
              {"name": "refless"}
            ]
          }
        }
        """
        let snapshot = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))

        XCTAssertEqual(snapshot.github?.runs?.count, 1)
        XCTAssertEqual(snapshot.supabase?.projects?.count, 1)
        XCTAssertEqual(snapshot.supabase?.projects?.first?.lints?.count, 1)
        XCTAssertEqual(snapshot.supabase?.projects?.first?.ref, "abc")
    }

    func testAnEmptyListStaysEmptyAndAMissingListStaysNil() throws {
        // Lossy decoding must not invent a list where the host sent none —
        // "no activity yet" and "this host is too old to say" are different
        // states and several surfaces branch on the difference.
        let empty = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(#"{"activity": []}"#.utf8))
        XCTAssertEqual(empty.activity?.count, 0)

        let absent = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(#"{}"#.utf8))
        XCTAssertNil(absent.activity)
    }
}

/// The widget/watch cache, which outlives the build that wrote it.
///
/// This is the one payload with no error path at all: `WatchSnapshotCache`
/// and `HeadroomWidgetSnapshot.cached()` both decode with `try?`, so anything
/// that throws is indistinguishable from "nothing cached yet" — a placeholder
/// face, forever, with nothing on screen to say why. These pin the tolerance
/// that keeps a cache from a different build readable.
final class WidgetSnapshotSkewTests: XCTestCase {
    private func decode(_ json: String) throws -> HeadroomWidgetSnapshot {
        try JSONDecoder().decode(
            HeadroomWidgetSnapshot.self, from: Data(json.utf8))
    }

    func testAnEmptyObjectStillDecodes() throws {
        // The floor: whatever a future build adds, the envelope survives.
        let snapshot = try decode("{}")
        XCTAssertTrue(snapshot.providers.isEmpty)
        XCTAssertTrue(snapshot.isStale)
    }

    func testAMissingTimestampReadsAsStaleRatherThanFailing() throws {
        // distantPast is the honest answer to "when was this written" when
        // the payload does not say, and `isStale` already knows how to
        // report it.
        let snapshot = try decode(#"{"providers": []}"#)
        XCTAssertTrue(snapshot.isStale)
    }

    func testOneMalformedProviderCostsThatProviderNotTheFace() throws {
        // The row without an id is the unusable one; the rest must survive.
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "claude", "title": "Claude", "percent": 40},
            {"title": "no id here", "percent": 10},
            {"id": "codex", "title": "Codex", "percent": 20}
        ]}
        """)
        XCTAssertEqual(snapshot.providers.map(\.id), ["claude", "codex"])
    }

    func testAProviderFallsBackRatherThanDroppingOut() throws {
        // Only `id` is identity. A build that stopped writing `title` or
        // `percent` should cost a label, not the provider.
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [{"id": "claude"}]}
        """)
        let provider = try XCTUnwrap(snapshot.providers.first)
        XCTAssertEqual(provider.title, "claude")
        XCTAssertEqual(provider.percent, 0)
    }

    func testAnAbsentBurndownCurveIsEmptyNotAFailure() throws {
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "claude", "title": "Claude", "percent": 40,
             "burndown": {"windowEnd": 12}}
        ]}
        """)
        let series = try XCTUnwrap(snapshot.providers.first?.burndown)
        XCTAssertTrue(series.actual.isEmpty)
        XCTAssertTrue(series.projected.isEmpty)
        XCTAssertEqual(series.windowEnd, 12)
    }

    func testTheNoCacheStateCarriesNoInventedNumbers() {
        // What a home screen or watch face shows before the app has ever
        // synced. It must not be the gallery's demo data: a widget added on
        // day one otherwise states that Claude is at 42% with nothing behind
        // it, which is worse than showing nothing at all.
        XCTAssertTrue(HeadroomWidgetSnapshot.awaitingFirstSync.providers.isEmpty)
        XCTAssertNotNil(
            HeadroomWidgetSnapshot.awaitingFirstSync.attentionSummary,
            "the empty state has to explain itself")
    }

    func testTheGalleryPlaceholderStillHasSomethingToShow() {
        // The other half: the widget picker is the one place invented
        // numbers are correct, so this must not be emptied by accident.
        XCTAssertFalse(HeadroomWidgetSnapshot.placeholder.providers.isEmpty)
        XCTAssertFalse(HeadroomWidgetSnapshot.placeholder.isStale)
    }

    func testABurndownKeyWithNoCurveIsNotSomethingToChart() throws {
        // What made the wide widget a blank box: `charted` asked whether the
        // provider had a `burndown` key, not whether that key held a stroke.
        // A cache from an older build, or one a lossy decode emptied, sent the
        // widget down the chart branch — which then had nothing to draw and no
        // words to fall back to.
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "empty", "title": "Empty", "percent": 10,
             "burndown": {"windowEnd": 12}},
            {"id": "onesample", "title": "One", "percent": 20,
             "burndown": {"actual": [[1, 90]]}},
            {"id": "claude", "title": "Claude", "percent": 30,
             "burndown": {"actual": [[1, 90], [2, 70]]}}
        ]}
        """)
        XCTAssertEqual(snapshot.charted.map(\.id), ["claude"])
        XCTAssertEqual(snapshot.providers.count, 3, "none of them is dropped")
    }

    func testAShortRowCostsItsSampleRatherThanTheWidget() throws {
        // A widget extension has no error path: an index out of range is not a
        // wrong number on screen, it is an empty tile that never says why. Row
        // width is not validated on the way into the cache, so it is validated
        // on the way out.
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "claude", "title": "Claude", "percent": 30,
             "burndown": {"actual": [[1, 90], [], [3, 70], [4]]}}
        ]}
        """)
        let series = try XCTUnwrap(snapshot.providers.first?.burndown)
        XCTAssertEqual(series.latestSampleTime, 3)
        XCTAssertTrue(series.isDrawable)
        XCTAssertNil(OverallBurndownChartMath.latestSampleTime([[], [7]]))
        XCTAssertNil(OverallBurndownChartMath.latestSampleTime(nil))
    }

    func testTheBindingProviderIsTheOneClosestToRunningOut() throws {
        // A new widget starts on this provider, and the watch names it when it
        // can only name one. Emptying first outranks most spent: a pool at 90%
        // that renews tomorrow changes nothing about today.
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "spent", "title": "Spent", "percent": 90},
            {"id": "emptying", "title": "Emptying", "percent": 40,
             "burndown": {"actual": [[1, 60], [2, 40]],
                          "projected": [[2, 40], [3, 0]]}}
        ]}
        """)
        XCTAssertEqual(snapshot.bindingProvider?.id, "emptying")

        // With nothing running dry it is the most spent, which is what the
        // meters lead with everywhere else.
        let steady = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "claude", "title": "Claude", "percent": 20},
            {"id": "codex", "title": "Codex", "percent": 70}
        ]}
        """)
        XCTAssertEqual(steady.bindingProvider?.id, "codex")
    }

    func testATileDrawsTheProviderItWasConfiguredFor() throws {
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "claude", "title": "Claude", "percent": 30},
            {"id": "codex", "title": "Codex", "percent": 60}
        ]}
        """)
        XCTAssertEqual(snapshot.showing("codex").providers.map(\.id), ["codex"])
        // The default, and every widget placed before the picker existed.
        XCTAssertEqual(
            snapshot.showing(nil).providers.map(\.id), ["claude", "codex"])
    }

    func testAConfiguredProviderThatLeftDrawsTheRestRatherThanNothing() throws {
        // Turning a provider off, or reordering it out of the top 3, must not
        // turn a tile into an empty box. Every figure a widget draws is
        // labelled with whose it is, so more than was asked for still reads.
        let snapshot = try decode("""
        {"updatedAt": 0, "providers": [
            {"id": "claude", "title": "Claude", "percent": 30}
        ]}
        """)
        XCTAssertEqual(
            snapshot.showing("cursor").providers.map(\.id), ["claude"])
    }

    func testWhatThisBuildWritesIsWhatThisBuildReads() throws {
        // The tolerance above must not have cost the round trip.
        let written = HeadroomWidgetSnapshot(
            updatedAt: Date(timeIntervalSince1970: 1_700_000_000),
            attentionLevel: "warn",
            attentionSummary: "one build failed",
            providers: [
                HeadroomWidgetSnapshot.Provider(
                    id: "claude",
                    title: "Claude",
                    name: "Claude · Work",
                    percent: 42,
                    accent: "#D97757",
                    layers: [.init(id: "week", name: "Week", percent: 42)],
                    burndown: .init(
                        actual: [[1, 100], [2, 80]],
                        projected: [[2, 80], [3, 60]])
                ),
            ]
        )
        let read = try JSONDecoder().decode(
            HeadroomWidgetSnapshot.self,
            from: try JSONEncoder().encode(written))
        XCTAssertEqual(read.updatedAt, written.updatedAt)
        XCTAssertEqual(read.attentionSummary, "one build failed")
        XCTAssertEqual(read.providers.first?.name, "Claude · Work")
        XCTAssertEqual(read.providers.first?.percent, 42)
        XCTAssertEqual(read.providers.first?.layers?.first?.name, "Week")
        XCTAssertEqual(read.providers.first?.burndown?.actual.count, 2)
    }
}
