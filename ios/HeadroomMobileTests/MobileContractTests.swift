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

    /// The phone used to decode four fields of a request and drop the rest,
    /// which made an Edit approval read as "Use Edit". Pin the whole shape.
    func testDecodesWholeAgentRequestNotJustTheCommand() throws {
        let data = Data(
            """
            {
              "ok": true,
              "events": [{
                "id": "evt_1",
                "provider": "claude-code",
                "adapter": "claude-http-hooks",
                "machine_id": "mac-studio-1",
                "machine_name": "Studio",
                "session_id": "s1",
                "kind": "permission_approval",
                "state": "pending",
                "revision": 1,
                "title": "Claude needs permission in acme",
                "summary": "Edit /tmp/acme/app.ts",
                "detail": {
                  "cwd": "/tmp/acme",
                  "tool_name": "Edit",
                  "reasons": ["Destructive operation"],
                  "request": [
                    {"key": "file_path", "label": "File", "kind": "path",
                     "value": "/tmp/acme/app.ts", "truncated": false},
                    {"key": "old_string", "label": "Replacing", "kind": "code",
                     "value": "const port = 3000", "truncated": false},
                    {"key": "new_string", "label": "With", "kind": "code",
                     "value": "const port = 8080", "truncated": true,
                     "full_chars": 9000, "omitted_fields": 2}
                  ]
                },
                "actions": [{"id": "decline", "label": "Deny", "risk": "safe"}],
                "created_at_ms": 1,
                "updated_at_ms": 2
              }]
            }
            """.utf8
        )

        let response = try JSONDecoder().decode(
            AgentAttentionEventsResponse.self, from: data)
        let detail = try XCTUnwrap(response.events.first).detail
        XCTAssertEqual(response.events.first?.machineID, "mac-studio-1")
        XCTAssertEqual(response.events.first?.machineName, "Studio")
        XCTAssertEqual(response.events.first?.displayTitle, "acme")
        XCTAssertEqual(
            response.events.first?.notificationTitle, "acme • Claude • Studio")
        XCTAssertEqual(detail.toolName, "Edit")
        XCTAssertEqual(detail.reasons, ["Destructive operation"])
        XCTAssertEqual(detail.requestFields.count, 3)
        XCTAssertEqual(detail.requestFields[1].value, "const port = 3000")
        XCTAssertEqual(detail.requestFields[2].kind, "code")
        XCTAssertTrue(detail.requestFields[2].wasTruncated)
        XCTAssertEqual(detail.requestFields[2].fullChars, 9000)
        XCTAssertEqual(detail.requestFields[2].omittedFields, 2)
    }

    /// The row shows how long the agent has been waiting, in the same words
    /// an activity row uses.
    func testAgentEventAgeReadsFromCreatedAt() throws {
        let sixMinutesAgo = Int64((Date().timeIntervalSince1970 - 360) * 1000)
        let data = Data(
            """
            {"ok": true, "events": [{
              "id": "evt_1", "provider": "claude-code",
              "adapter": "claude-http-hooks", "session_id": "s1",
              "kind": "permission_approval", "state": "pending", "revision": 1,
              "title": "t", "summary": "s", "detail": {}, "actions": [],
              "created_at_ms": \(sixMinutesAgo), "updated_at_ms": \(sixMinutesAgo)
            }]}
            """.utf8
        )
        let event = try XCTUnwrap(
            try JSONDecoder().decode(
                AgentAttentionEventsResponse.self, from: data).events.first)
        XCTAssertEqual(event.age, 360, accuracy: 5)
        XCTAssertEqual(HeadroomCopy.ago(event.age), "6 min ago")
    }

    /// Codex still sends a bare `command`; it must render through the same
    /// accessor so views never branch on provider.
    func testBareCommandDetailStillProducesARequestField() throws {
        let data = Data(#"{"command": "npm test", "cwd": "/tmp"}"#.utf8)
        let detail = try JSONDecoder().decode(
            AgentAttentionDetail.self, from: data)
        XCTAssertEqual(detail.requestFields.count, 1)
        XCTAssertEqual(detail.requestFields.first?.kind, "command")
        XCTAssertEqual(detail.requestFields.first?.value, "npm test")
    }

    func testDismissOnlyAgentNoticeIsNotActionableAttention() throws {
        let data = Data(
            """
            {"ok": true, "events": [{
              "id": "evt_notice", "provider": "claude-code",
              "adapter": "claude-http-hooks", "session_id": "s1",
              "kind": "agent_waiting", "state": "pending", "revision": 1,
              "title": "acme", "summary": "Ready for your next instruction",
              "detail": {},
              "actions": [{"id": "dismiss", "label": "Dismiss", "risk": "safe"}],
              "created_at_ms": 1, "updated_at_ms": 1
            }]}
            """.utf8
        )
        let event = try XCTUnwrap(
            try JSONDecoder().decode(
                AgentAttentionEventsResponse.self, from: data).events.first)
        XCTAssertTrue(event.isDismissOnly)
        XCTAssertFalse(event.isActionable)
    }

    func testAgentNotificationCarriesOnlyTheEventRoute() {
        XCTAssertEqual(
            MobileNotifications.agentEventID(
                from: ["agent_event_id": "evt_123"]
            ),
            "evt_123"
        )
        XCTAssertNil(
            MobileNotifications.agentEventID(from: ["other": "value"])
        )
    }

    func testAgentEventWithoutMachineMetadataStillDecodes() throws {
        let data = Data(
            #"{"ok":true,"events":[{"id":"evt_old","provider":"codex","adapter":"test","session_id":"s1","kind":"command_approval","state":"pending","revision":1,"title":"repo","summary":"Run tests","detail":{},"actions":[],"created_at_ms":1,"updated_at_ms":1}]}"#.utf8
        )
        let event = try XCTUnwrap(
            try JSONDecoder().decode(
                AgentAttentionEventsResponse.self, from: data).events.first)
        XCTAssertNil(event.machineID)
        XCTAssertNil(event.machineName)
        XCTAssertEqual(event.notificationTitle, "repo • Codex")
    }

    func testRepoTitleRepairsLegacyClaudeTitleFromCwd() throws {
        let data = Data(
            #"{"ok":true,"events":[{"id":"evt_old","provider":"claude-code","adapter":"claude-http-hooks","session_id":"s1","kind":"agent_waiting","state":"pending","revision":1,"title":"Claude finished responding in headroom","summary":"Ready for your next instruction","detail":{"cwd":"/Users/mz/Dev/headroom"},"actions":[{"id":"dismiss","label":"Dismiss","risk":"safe"}],"created_at_ms":1,"updated_at_ms":1}]}"#.utf8
        )
        let event = try XCTUnwrap(
            try JSONDecoder().decode(
                AgentAttentionEventsResponse.self, from: data).events.first)
        XCTAssertEqual(event.displayTitle, "headroom")
        // Older hosts omit machine_name; the push still names the agent.
        XCTAssertEqual(event.notificationTitle, "headroom • Claude")
    }

    func testNotificationTitleNamesCodexAndComputer() throws {
        let data = Data(
            #"{"ok":true,"events":[{"id":"evt_1","provider":"codex","adapter":"test","machine_name":"Studio","session_id":"s1","kind":"command_approval","state":"pending","revision":1,"title":"repo","summary":"Run tests","detail":{"cwd":"/tmp/acme"},"actions":[],"created_at_ms":1,"updated_at_ms":1}]}"#.utf8
        )
        let event = try XCTUnwrap(
            try JSONDecoder().decode(
                AgentAttentionEventsResponse.self, from: data).events.first)
        XCTAssertEqual(event.notificationTitle, "acme • Codex • Studio")
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
