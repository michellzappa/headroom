import XCTest
@testable import HeadroomMobile

final class MobileContractTests: XCTestCase {
    func testDecodesRegistryDrivenQuotaPayload() throws {
        let data = Data(
            """
            {
              "updated": "2026-07-28T12:00:00Z",
              "providers": [{
                "id": "codex",
                "title": "Codex",
                "enabled": true,
                "ok": true,
                "accent": "#00aaff",
                "pools": {
                  "session": {
                    "title": "Session",
                    "pct": 42,
                    "pace_pct": 35,
                    "resets_in": "2h",
                    "ring": true
                  }
                }
              }],
              "attention": {"level": "ok", "reasons": []}
            }
            """.utf8
        )

        let snapshot = try JSONDecoder().decode(UsageSnapshot.self, from: data)
        XCTAssertEqual(snapshot.providers?.first?.id, "codex")
        XCTAssertEqual(snapshot.providers?.first?.visiblePools.first?.pool.pct, 42)
        XCTAssertEqual(snapshot.attention?.isWarning, false)
    }

    func testNormalizesBareMacHost() {
        XCTAssertEqual(
            MobileConnection.normalize("studio-mac.local"),
            "http://studio-mac.local:8737/usage"
        )
    }

    func testIdentityLabelPrefersDistinctMachineName() {
        UserDefaults.standard.set(
            "http://studio-mac.local:8737/usage",
            forKey: MobileConnection.endpointKey
        )
        defer {
            UserDefaults.standard.removeObject(forKey: MobileConnection.endpointKey)
        }
        XCTAssertEqual(
            MobileConnection.identityLabel(machineName: "Studio"),
            "Studio · studio-mac.local"
        )
        XCTAssertEqual(
            MobileConnection.identityLabel(machineName: "studio-mac"),
            "studio-mac.local"
        )
        XCTAssertEqual(
            MobileConnection.identityLabel(machineName: nil),
            "studio-mac.local"
        )
    }

    func testPairedComputerMetadataRoundTripsWithoutASecret() throws {
        let computer = PairedComputer(
            id: "studio-1",
            name: "Studio",
            endpoint: "http://studio-mac.local:8737/usage"
        )
        let data = try JSONEncoder().encode(computer)
        let decoded = try JSONDecoder().decode(PairedComputer.self, from: data)
        XCTAssertEqual(decoded, computer)
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("token"))
    }

    func testHostLabelCanDescribeAStoredComputerEndpoint() {
        XCTAssertEqual(
            MobileConnection.hostLabel(for: "http://studio-mac.local:8737/usage"),
            "studio-mac.local"
        )
    }

    /// Attention and Activity are a partition of one feed, not two filters
    /// that happen to agree. Every row lands on exactly one tab, and the
    /// failing ones land on Attention — the tab bar's badge counts the same
    /// call, so a disagreement here is a row nobody can reach.
    func testAttentionAndActivitySplitTheFeedExactlyOnce() throws {
        let data = Data(
            """
            {
              "activity": [
                {"id": "a1", "status": "failure", "subject": "Release"},
                {"id": "a2", "status": "ready", "subject": "Deploy"},
                {"id": "a3", "status": "pushed", "subject": "Push"},
                {"id": "a4", "status": "error", "subject": "Tests"},
                {"id": "a5", "status": "assigned", "subject": "Year-old issue",
                 "needs_attention": false}
              ]
            }
            """.utf8
        )
        let snapshot = try JSONDecoder().decode(UsageSnapshot.self, from: data)
        let failing = AttentionScreen.failures(in: snapshot)
        let rest = (snapshot.activity ?? []).filter { !$0.needsAttention }
        // a5 is an aged inbox row: `assigned` still, but the host says it no
        // longer pages. Both sides read the same verdict, so the partition
        // holds and the row lands in the feed instead of the queue.
        XCTAssertEqual(failing.map(\.id), ["a1", "a4"])
        XCTAssertEqual(failing.count + rest.count, snapshot.activity?.count)
        XCTAssertTrue(Set(failing.map(\.id)).isDisjoint(with: rest.map(\.id)))
    }

    /// Tab order is the reading order the split exists for: what is going on,
    /// what wants you, what happened.
    func testTabsRunSummaryThenQueueThenLog() {
        XCTAssertEqual(
            MobileTab.allCases.map(\.rawValue),
            ["overview", "attention", "activity"]
        )
    }

    func testHeadroomCopyMatchesGlossaryTerms() {
        XCTAssertEqual(HeadroomCopy.dailyBurn, "Daily burn")
        XCTAssertEqual(HeadroomCopy.overallBurndown, "Overall burndown")
        XCTAssertEqual(HeadroomCopy.activity, "Activity")
        XCTAssertEqual(HeadroomCopy.attention, "Attention")
        XCTAssertEqual(HeadroomCopy.dismissAll, "Dismiss all")
        XCTAssertEqual(HeadroomCopy.recentActivity, "Recent")
        XCTAssertEqual(HeadroomCopy.activityGroupTitle(for: "github"), "GitHub Actions")
        XCTAssertEqual(HeadroomCopy.activityGroupTitle(for: "deployment"), "Vercel deployments")
        XCTAssertEqual(HeadroomCopy.activityGroupTitle(for: "commit"), "Git commits")
        XCTAssertEqual(HeadroomCopy.activityGroupTitle(for: "supabase"), "Supabase")
        XCTAssertEqual(HeadroomCopy.activityGroupTitle(for: "unknown"), "Other activity")
        XCTAssertEqual(HeadroomCopy.services, "Services")
        XCTAssertEqual(HeadroomCopy.allClear, "All clear")
        XCTAssertEqual(HeadroomCopy.connected, "Connected")
        XCTAssertEqual(HeadroomCopy.macUnavailable, "Mac unavailable")
        XCTAssertEqual(HeadroomCopy.noHistoryYet, "No history yet")
        XCTAssertEqual(HeadroomCopy.clearAttention, HeadroomCopy.dismissAll)
        XCTAssertEqual(HeadroomCopy.githubActions, "GitHub Actions")
    }
}
