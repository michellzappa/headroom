import AppKit
import SwiftUI
import XCTest
@testable import Headroom

final class ModelsTests: XCTestCase {
    func testDecodesBackendShape() throws {
        let json = """
        {
          "updated": "2026-07-23T10:00:00+0200",
          "plan": "Max",
          "quota_ok": true,
          "session_pct": 22.0,
          "session_pace_pct": 18.0,
          "session_resets_in": "1h",
          "week_pct": 31.0,
          "week_pace_pct": 26.0,
          "week_resets_in": "4d",
          "today": {"total": 1234, "cost_usd": 4.25},
          "by_day": [
            {"date": "2026-07-22", "claude": 2.0, "codex": 1.5, "cursor": 0.5, "total": 4.0},
            {"date": "2026-07-23", "claude": 3.5, "codex": 2.0, "cursor": 0.25, "total": 5.75}
          ],
          "codex": {
            "ok": true,
            "plan": "Team",
            "session_pct": 41.0,
            "session_pace_pct": 20.0,
            "session_resets_in": "2h",
            "week_pct": 72.0,
            "week_pace_pct": 55.0,
            "week_resets_in": "3d",
            "reset_credits_available": 2,
            "reset_credits_expiries": ["6d 5h", "18d 3h"],
            "reset_credits_expire_at": [1785330000, 1786359600],
            "cost_usd": 120.5,
            "cost_limit_usd": 500.0,
            "cost_label": "$120 / $500"
          },
          "cursor": {
            "ok": true,
            "plan": "Pro",
            "total_pct": 4.0,
            "total_pace_pct": 28.0,
            "auto_pct": 0.0,
            "auto_pace_pct": 28.0,
            "api_pct": 34.0,
            "api_pace_pct": 28.0,
            "resets_in": "7d 43m",
            "cost_usd": 15.15,
            "cost_limit_usd": 20.0,
            "cost_label": "$15 / $20",
            "on_demand_label": "$30 / $30 on-demand"
          },
          "attention": {
            "level": "warn",
            "score": 25,
            "summary": "1 Supabase alert",
            "reasons": [
              {"level": "warn", "kind": "supabase", "summary": "1 Supabase alert"}
            ]
          },
          "vercel": {
            "ok": true,
            "team": "ev-io",
            "deployments": [
              {"project": "signals", "status": "ready", "ago": "2m"}
            ]
          },
          "git": {"ok": true, "commits": []},
          "activity": [{
            "id": "dpl_1",
            "kind": "deployment",
            "status": "error",
            "subject": "Fix checkout",
            "repo": "store",
            "project": "store-web",
            "branch": "main",
            "short_sha": "abc1234",
            "ago": "2m",
            "error_message": "Build failed",
            "inspector_url": "https://vercel.com/example"
          }],
          "supabase": {
            "ok": true,
            "configured": true,
            "project_count": 2,
            "healthy_count": 1,
            "alert_count": 1,
            "projects": [{
              "ref": "project-ref",
              "name": "Production DB",
              "organization_id": "acme",
              "region": "eu-west-1",
              "status": "ACTIVE_HEALTHY",
              "healthy": false,
              "created_at": "2024-03-29T16:32:59Z",
              "database": {
                "host": "db.project-ref.supabase.co",
                "version": "15.8.1.034",
                "postgres_engine": "15",
                "release_channel": "ga"
              },
              "unhealthy_services": ["storage"],
              "services": [{
                "name": "storage",
                "status": "unhealthy",
                "healthy": false
              }],
              "dashboard_url": "https://supabase.com/dashboard/project/project-ref"
            }]
          },
          "plausible": {
            "ok": true,
            "configured": true,
            "range": "24h",
            "range_label": "24h",
            "site_count": 1,
            "visitors_today": 98,
            "realtime": 3,
            "sites": [{
              "domain": "acme.dev",
              "range": "24h",
              "range_label": "24h",
              "visitors_today": 98,
              "pageviews_today": 210,
              "visitors_7d": 1200,
              "pageviews_7d": 3400,
              "bounce_rate_7d": 41.5,
              "visit_duration_7d": 142,
              "realtime": 3,
              "by_day": [
                { "day": "2026-08-06 12", "visitors": 8 },
                { "day": "2026-08-06 13", "visitors": 14 }
              ],
              "dashboard_url": "https://plausible.io/acme.dev"
            }]
          },
          "posthog": {
            "ok": true,
            "configured": true,
            "range": "24h",
            "range_label": "24h",
            "project_count": 1,
            "events_today": 3100,
            "users_today": 220,
            "realtime": 5,
            "projects": [{
              "id": "12345",
              "name": "Acme Web",
              "range": "24h",
              "range_label": "24h",
              "events_today": 3100,
              "users_today": 220,
              "events_7d": 18000,
              "users_7d": 1400,
              "realtime": 5,
              "dashboard_url": "https://us.posthog.com/project/12345"
            }]
          },
          "local": {
            "ok": true,
            "host": "mac",
            "servers": [{
              "name": "web",
              "port": 3000,
              "pid": 4242,
              "reachable": true,
              "latency_ms": 2
            }],
            "builds": [{
              "name": "Headroom",
              "kind": "xcodebuild",
              "action": "test",
              "scheme": "Headroom",
              "target": "Headroom.xcodeproj",
              "pid": 5252,
              "cmd": "xcodebuild",
              "cwd": "/Users/mz/Dev/headroom/macos",
              "age_s": 38
            }]
          }
        }
        """.data(using: .utf8)!

        let value = try JSONDecoder().decode(UsageSnapshot.self, from: json)

        XCTAssertEqual(value.codex?.sessionPct, 41)
        XCTAssertEqual(value.codex?.weekPct, 72)
        XCTAssertEqual(value.meter(for: .claude).primary.percent, 22)
        XCTAssertEqual(value.meter(for: .claude).primary.reset, "1h")
        XCTAssertEqual(value.meter(for: .claude).secondary.percent, 31)
        XCTAssertEqual(value.meter(for: .claude).secondary.reset, "4d")
        XCTAssertEqual(value.meter(for: .claude).headline.percent, 31)
        XCTAssertEqual(value.meter(for: .codex).primary.title, "Session")
        XCTAssertEqual(value.meter(for: .codex).secondary.reset, "3d")
        XCTAssertEqual(value.meter(for: .codex).headline.percent, 72)
        XCTAssertEqual(value.meter(for: .cursor).primary.percent, 4)
        XCTAssertEqual(value.meter(for: .cursor).primary.title, "Total")
        XCTAssertEqual(value.meter(for: .cursor).primary.reset, "7d 43m")
        XCTAssertEqual(value.meter(for: .cursor).secondary.title, "API")
        XCTAssertEqual(value.meter(for: .cursor).secondary.percent, 34)
        XCTAssertNil(value.meter(for: .cursor).tertiary)
        XCTAssertEqual(value.meter(for: .cursor).headline.title, "Total")
        XCTAssertEqual(value.meter(for: .cursor).headline.percent, 4)
        XCTAssertEqual(value.vercel?.deployments?.first?.project, "signals")
        XCTAssertEqual(value.local?.servers?.first?.port, 3000)
        XCTAssertEqual(value.local?.servers?.first?.pid, 4242)
        XCTAssertEqual(value.local?.servers?.first?.latencyMS, 2)
        XCTAssertEqual(value.local?.builds?.first?.name, "Headroom")
        XCTAssertEqual(value.local?.builds?.first?.kind, "xcodebuild")
        XCTAssertEqual(value.local?.builds?.first?.ageS, 38)
        XCTAssertEqual(value.activity?.first?.status, "error")
        XCTAssertEqual(value.activity?.first?.shortSHA, "abc1234")
        XCTAssertEqual(value.supabase?.alertCount, 1)
        XCTAssertEqual(value.supabase?.projects?.first?.ref, "project-ref")
        XCTAssertEqual(
            value.supabase?.projects?.first?.organizationID, "acme")
        XCTAssertEqual(
            value.supabase?.projects?.first?.createdAt,
            "2024-03-29T16:32:59Z")
        XCTAssertEqual(
            value.supabase?.projects?.first?.database?.host,
            "db.project-ref.supabase.co")
        XCTAssertEqual(
            value.supabase?.projects?.first?.database?.postgresEngine, "15")
        XCTAssertEqual(
            value.supabase?.projects?.first?.unhealthyServices,
            ["storage"]
        )
        XCTAssertEqual(value.plausible?.realtime, 3)
        XCTAssertEqual(value.plausible?.range, "24h")
        XCTAssertEqual(value.plausible?.windowLabel, "24h")
        XCTAssertEqual(value.plausible?.sites?.first?.domain, "acme.dev")
        XCTAssertEqual(value.plausible?.sites?.first?.visitorsToday, 98)
        XCTAssertEqual(value.plausible?.sites?.first?.byDay?.count, 2)
        XCTAssertEqual(value.plausible?.sites?.first?.byDay?.last?.visitors, 14)
        XCTAssertEqual(value.posthog?.realtime, 5)
        XCTAssertEqual(value.posthog?.range, "24h")
        XCTAssertEqual(value.posthog?.windowLabel, "24h")
        XCTAssertEqual(value.posthog?.projects?.first?.id, "12345")
        XCTAssertEqual(value.posthog?.projects?.first?.eventsToday, 3100)
        XCTAssertEqual(value.today?.costUSD, 4.25)
        XCTAssertEqual(value.byDay?.count, 2)
        XCTAssertEqual(value.byDay?.last?.total, 5.75)
        XCTAssertEqual(value.byDay?.last?.burn(for: .claude), 3.5)
        XCTAssertEqual(value.codex?.costLabel, "$120 / $500")
        XCTAssertEqual(value.codex?.resetCreditsAvailable, 2)
        XCTAssertEqual(value.codex?.resetCreditsExpiries, ["6d 5h", "18d 3h"])
        XCTAssertEqual(
            value.codex?.resetCreditsExpireAt,
            [1785330000, 1786359600]
        )
        XCTAssertEqual(value.meter(for: .codex).resetCreditsLabel, "2 reset credits")
        XCTAssertEqual(
            value.meter(for: .codex).resetCreditsExpiryLabel,
            "6d 5h · 18d 3h"
        )
        XCTAssertEqual(value.cursor?.costLabel, "$15 / $20")
        XCTAssertEqual(
            value.meter(for: .cursor).costLabel,
            "$15 / $20 · $30 / $30 on-demand"
        )
        XCTAssertEqual(
            value.meter(for: .cursor).displayableWindows.map(\.title),
            ["Total", "API"]
        )
        XCTAssertEqual(value.meter(for: .claude).costLabel, "$4 today")
        XCTAssertNil(value.meter(for: .claude).resetCreditsLabel)
        XCTAssertNil(value.meter(for: .cursor).resetCreditsLabel)
        XCTAssertEqual(value.attention?.level, "warn")
        XCTAssertEqual(value.attention?.summary, "1 Supabase alert")
        XCTAssertTrue(value.attention?.isWarning == true)
        XCTAssertEqual(
            value.attention?.fingerprint,
            "warn|supabase|1 Supabase alert"
        )
        XCTAssertTrue(AttentionAck.shouldShowPip(
            for: value.attention,
            dismissedFingerprint: nil
        ))
        XCTAssertFalse(AttentionAck.shouldShowPip(
            for: value.attention,
            dismissedFingerprint: value.attention?.fingerprint
        ))
    }

    func testProviderTintPrefersHostAccent() throws {
        let json = """
        {
          "providers": [
            {"id": "claude", "accent": "#D97757"},
            {"id": "codex", "accent": "#10A37F"},
            {"id": "cursor", "accent": "#789BC8"}
          ]
        }
        """
        let value = try JSONDecoder().decode(
            UsageSnapshot.self,
            from: Data(json.utf8)
        )
        XCTAssertNotNil(HeadroomPalette.color(hex: "#D97757"))
        XCTAssertNotNil(HeadroomPalette.color(hex: "10A37F"))
        XCTAssertNil(HeadroomPalette.color(hex: "nope"))

        func rgb(_ color: Color) -> (CGFloat, CGFloat, CGFloat) {
            let ns = NSColor(color).usingColorSpace(.deviceRGB)!
            return (ns.redComponent, ns.greenComponent, ns.blueComponent)
        }
        func assertClose(_ a: Color, _ b: Color, file: StaticString = #filePath,
                         line: UInt = #line) {
            let lhs = rgb(a)
            let rhs = rgb(b)
            XCTAssertEqual(lhs.0, rhs.0, accuracy: 0.002, file: file, line: line)
            XCTAssertEqual(lhs.1, rhs.1, accuracy: 0.002, file: file, line: line)
            XCTAssertEqual(lhs.2, rhs.2, accuracy: 0.002, file: file, line: line)
        }

        // Host accents resolve to the same RGB as the firmware fallbacks.
        assertClose(value.tint(for: .claude), UsageProvider.claude.tint)
        assertClose(value.tint(for: .codex), UsageProvider.codex.tint)
        assertClose(value.tint(for: .cursor), UsageProvider.cursor.tint)

        // Custom host accent wins over the fallback.
        let customJSON = """
        {"providers":[{"id":"claude","accent":"#112233"}]}
        """
        let custom = try JSONDecoder().decode(
            UsageSnapshot.self,
            from: Data(customJSON.utf8)
        )
        assertClose(custom.tint(for: .claude), HeadroomPalette.color(hex: "#112233")!)
    }

    func testMeterPrefersRegistryPoolsOverLegacyFields() throws {
        let json = """
        {
          "session_pct": 99.0,
          "week_pct": 98.0,
          "quota_ok": true,
          "plan": "legacy-ignored",
          "providers": [{
            "id": "claude",
            "ok": true,
            "plan": "Max 5x",
            "headline": "week",
            "pools": {
              "session": {"title": "Session", "pct": 12.0, "pace_pct": 10.0,
                          "resets_in": "1h", "ring": true},
              "week": {"title": "Weekly", "pct": 45.0, "pace_pct": 40.0,
                       "resets_in": "4d", "ring": true}
            }
          }]
        }
        """
        let value = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        let meter = value.meter(for: .claude)
        XCTAssertEqual(meter.plan, "Max 5x")
        XCTAssertEqual(meter.primary.percent, 45)
        XCTAssertEqual(meter.secondary.percent, 12)
        XCTAssertEqual(meter.headline.percent, 45)
        XCTAssertEqual(meter.menuBarWindow.percent, 45)
        XCTAssertEqual(meter.primary.id, "week")
        XCTAssertEqual(meter.secondary.id, "session")
    }

    func testMeterFallsBackWhenProvidersOmitPools() throws {
        let json = """
        {
          "session_pct": 22.0,
          "week_pct": 31.0,
          "quota_ok": true,
          "providers": [{"id": "claude", "ok": true, "plan": "Max"}]
        }
        """
        let value = try JSONDecoder().decode(
            UsageSnapshot.self, from: Data(json.utf8))
        let meter = value.meter(for: .claude)
        XCTAssertEqual(meter.primary.percent, 22)
        XCTAssertEqual(meter.secondary.percent, 31)
    }

    func testCropProjectionStopsAtResetAndFloor() {
        let pairs: [[Double]] = [
            [100, 40],
            [200, 20],
            [400, -10],
        ]
        let cropped = Burndown.cropProjection(pairs, windowEnd: 300)
        XCTAssertEqual(cropped.count, 3)
        XCTAssertEqual(cropped[2][0], 300, accuracy: 0.01)
        // -10 clamps to 0 before interpolate → remaining at reset is 10.
        XCTAssertEqual(cropped[2][1], 10, accuracy: 0.01)

        let exhaust: [[Double]] = [[10, 50], [20, 0], [30, 0]]
        let stopped = Burndown.cropProjection(exhaust, windowEnd: 40)
        XCTAssertEqual(stopped.count, 2)
        XCTAssertEqual(stopped.last?[1], 0)
    }

    func testOverallDomainIsFixedSevenDays() {
        // today−3.5d … today+3.5d always — upcoming resets must not stretch
        // the axis.
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let now = Date(timeIntervalSince1970: 1_785_246_900) // 2026-07-28 13:55 UTC
        let today = calendar.startOfDay(for: now)
        let expectedStart = calendar.date(byAdding: .day, value: -3, to: today)!
            .addingTimeInterval(12 * 60 * 60 * -1)
        let expectedEnd = calendar.date(byAdding: .day, value: 7, to: expectedStart)!
        let farReset = now.addingTimeInterval(10 * 24 * 60 * 60)
        let nearReset = now.addingTimeInterval(2 * 24 * 60 * 60)

        let domain = OverallBurndownChartMath.domain(
            now: now,
            calendar: calendar
        )
        XCTAssertEqual(domain.start, expectedStart)
        XCTAssertEqual(domain.end, expectedEnd)
        XCTAssertEqual(domain.now, now)
        // Sanity: near resets fall inside; far ones stay off-canvas.
        XCTAssertGreaterThan(nearReset, domain.start)
        XCTAssertLessThan(nearReset, domain.end)
        XCTAssertGreaterThan(farReset, domain.end)

        let clipped = OverallBurndownChartMath.preparedProjection(
            [
                [now.timeIntervalSince1970, 72],
                [farReset.timeIntervalSince1970, 10],
            ],
            windowEnd: farReset.timeIntervalSince1970,
            domain: domain
        )
        XCTAssertGreaterThanOrEqual(clipped.count, 2)
        XCTAssertEqual(
            clipped.last?[0] ?? 0,
            domain.endEpoch,
            accuracy: 1,
            "forecast clipped at the fixed week edge, not stretched to the reset"
        )
    }

    func testFrameLabelsTellTheTwoChartRulesApart() {
        // The overview is anchored to the clock, a provider chart to its
        // window, and a monthly provider chart to neither — it is a slice
        // inside one. Three frames, three sentences; a surface that draws the
        // wrong words is the bug this whole labelling exists to prevent.
        let now = Date(timeIntervalSince1970: 1_785_246_900)
        XCTAssertEqual(
            OverallBurndownChartMath.domain(now: now).frameLabel,
            HeadroomCopy.overallBurndownSubtitle
        )

        // Weekly pool: the plot spans the whole window, history stub and all.
        let weekStart = now.timeIntervalSince1970 - 2 * 24 * 3600
        let weekly = BurndownChartAxis.domain(
            windowStart: weekStart,
            windowEnd: weekStart + 7 * 24 * 3600,
            now: now.timeIntervalSince1970,
            historyStart: weekStart - 5 * 24 * 3600
        )
        XCTAssertEqual(weekly?.showsWholeWindow, true)
        XCTAssertEqual(weekly?.frameLabel, HeadroomCopy.windowFrame)

        // Monthly pool: seven days clipped out of thirty. Saying "this window"
        // here would describe an axis four times the one on screen.
        let monthStart = now.timeIntervalSince1970 - 10 * 24 * 3600
        let monthly = BurndownChartAxis.domain(
            windowStart: monthStart,
            windowEnd: monthStart + 30 * 24 * 3600,
            now: now.timeIntervalSince1970
        )
        XCTAssertEqual(monthly?.showsWholeWindow, false)
        XCTAssertEqual(monthly?.frameLabel, HeadroomCopy.windowSliceFrame)
        XCTAssertEqual(
            (monthly?.endEpoch ?? 0) - (monthly?.startEpoch ?? 0),
            7 * 24 * 3600,
            accuracy: 1
        )
    }

    func testOverviewLookbackIsSymmetricAboutToday() {
        // The subtitle claims three and a half days either side of today. If
        // the domain
        // ever stops covering exactly that, the copy is lying — so assert the
        // shape the words describe, not just the span.
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let now = Date(timeIntervalSince1970: 1_785_246_900)
        let today = calendar.startOfDay(for: now)
        let domain = OverallBurndownChartMath.domain(now: now, calendar: calendar)

        XCTAssertEqual(
            today.timeIntervalSince(domain.start),
            Double(OverallBurndownChartMath.lookbackDays) * 24 * 60 * 60
                + OverallBurndownChartMath.extraHistorySeconds,
            accuracy: 1
        )
        XCTAssertEqual(
            domain.end.timeIntervalSince(domain.start),
            Double(OverallBurndownChartMath.spanDays) * 24 * 60 * 60,
            accuracy: 1
        )
    }

    func testMonthlySliceKeepsItsOwnLookback() {
        // The two lookbacks are intentionally separate, so changing the
        // overview's centring cannot silently move every monthly chart —
        // assert they are read from different places, not that they differ.
        let monthStart = 1_785_246_900.0 - 10 * 24 * 3600
        let domain = BurndownChartAxis.domain(
            windowStart: monthStart,
            windowEnd: monthStart + 30 * 24 * 3600,
            now: 1_785_246_900
        )
        XCTAssertEqual(
            domain?.startEpoch ?? 0,
            1_785_246_900
                - Double(BurndownChartAxis.sliceLookbackDays) * 24 * 3600,
            accuracy: 1
        )
    }

    func testUpcomingEventsAppearOnlyOnceTheyEnterReach() {
        let domain = OverallBurndownChartMath.Domain(
            start: Date(timeIntervalSince1970: 100),
            end: Date(timeIntervalSince1970: 800),
            now: Date(timeIntervalSince1970: 400)
        )
        XCTAssertEqual(
            OverallBurndownChartMath.preparedUpcomingEvents(
                [900, 500, 300, 800], domain: domain
            ),
            [500, 800]
        )
    }

    func testProviderAxisCapsWeekdayMarksAtSeven() {
        // Sun afternoon → next Sun afternoon used to produce 8 midnights and a
        // clipped leftover label. Cap at seven weekday columns.
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let start = Date(timeIntervalSince1970: 1_752_235_200) // 2025-07-13 14:00 UTC Sun
        let end = Date(timeIntervalSince1970: 1_752_840_000)   // 2025-07-20 14:00 UTC Sun
        let columns = BurndownChartAxis.dayColumns(
            start: start, end: end, calendar: calendar
        )
        XCTAssertEqual(columns.count, 7)
        // Bands tile the domain edge to edge and stay inside it: Swift Charts
        // drops an out-of-domain value, which is how iOS ended up one column
        // short of the Mac canvas.
        XCTAssertEqual(columns.first?.start, start)
        XCTAssertEqual(columns.last?.end, end)
        for (index, column) in columns.enumerated() {
            XCTAssertLessThan(column.start, column.end)
            XCTAssertGreaterThan(column.mid, column.start)
            XCTAssertLessThan(column.mid, column.end)
            if index + 1 < columns.count {
                XCTAssertEqual(column.end, columns[index + 1].start)
            }
        }
        // Every band names the day its midpoint falls in, so no two adjacent
        // columns can carry the same weekday.
        let names = columns.map { column -> Int in
            calendar.component(.weekday, from: column.mid)
        }
        XCTAssertEqual(Set(names).count, 7)

        // A window that resets at midnight gets the same seven, not eight.
        let midnightStart = Date(timeIntervalSince1970: 1_752_192_000)
        let midnightColumns = BurndownChartAxis.dayColumns(
            start: midnightStart,
            end: midnightStart.addingTimeInterval(7 * 24 * 60 * 60),
            calendar: calendar
        )
        XCTAssertEqual(midnightColumns.count, 7)
        XCTAssertEqual(midnightColumns.first?.start, midnightStart)

        // A pool resetting in the small hours: the 2h sliver folds into the
        // previous band rather than costing a name or adding an eighth.
        let smallHoursStart = start.addingTimeInterval(12 * 60 * 60)
        let smallHours = BurndownChartAxis.dayColumns(
            start: smallHoursStart,
            end: smallHoursStart.addingTimeInterval(7 * 24 * 60 * 60),
            calendar: calendar
        )
        XCTAssertEqual(smallHours.count, 7)
        XCTAssertEqual(
            Set(smallHours.map { calendar.component(.weekday, from: $0.mid) })
                .count,
            7
        )

        let monthStart = Date(timeIntervalSince1970: 1_751_328_000) // ~Jul 1 2025
        let monthEnd = Date(timeIntervalSince1970: 1_753_920_000)   // ~Aug 1 2025
        let nowInMonth = Date(timeIntervalSince1970: 1_752_580_800) // ~Jul 17
        let domain = BurndownChartAxis.domain(
            windowStart: monthStart.timeIntervalSince1970,
            windowEnd: monthEnd.timeIntervalSince1970,
            now: nowInMonth.timeIntervalSince1970
        )!
        XCTAssertLessThanOrEqual(
            domain.end.timeIntervalSince(domain.start),
            7 * 24 * 60 * 60 + 1
        )
        let monthColumns = BurndownChartAxis.dayColumns(
            start: domain.start, end: domain.end, calendar: calendar
        )
        XCTAssertEqual(monthColumns.count, 7)
    }

    func testProviderSessionAxisUsesHoursNotDays() {
        let start = Date(timeIntervalSince1970: 1_785_246_900)
        let end = start.addingTimeInterval(5 * 60 * 60)
        let domain = BurndownChartAxis.domain(
            windowStart: start.timeIntervalSince1970,
            windowEnd: end.timeIntervalSince1970,
            now: start.timeIntervalSince1970 + 3600
        )!
        XCTAssertFalse(domain.showsDayAxis)
        XCTAssertTrue(
            BurndownChartAxis.dayColumns(start: domain.start, end: domain.end)
                .isEmpty
        )
        XCTAssertFalse(
            BurndownChartAxis.hourMarks(start: domain.start, end: domain.end)
                .isEmpty
        )
    }

    func testClipPolylineInterpolatesAtDomainEdge() {
        let clipped = OverallBurndownChartMath.clipPolyline(
            [[0, 100], [100, 0]],
            start: 25,
            end: 75
        )
        XCTAssertEqual(clipped.count, 2)
        XCTAssertEqual(clipped[0][0], 25, accuracy: 0.01)
        XCTAssertEqual(clipped[0][1], 75, accuracy: 0.01)
        XCTAssertEqual(clipped[1][0], 75, accuracy: 0.01)
        XCTAssertEqual(clipped[1][1], 25, accuracy: 0.01)
    }

    func testHistoryPolylineMakesResetVisible() {
        let reset = OverallBurndownChartMath.historyPolyline(
            [[100, 72], [200, 48]],
            risersAt: [150]
        )

        XCTAssertEqual(reset, [[100, 72], [150, 72], [150, 48], [200, 48]])
    }

    func testAccountMarkTitlePrefersTheUserLabel() throws {
        let labeled = try JSONDecoder().decode(
            QuotaProviderInfo.self,
            from: Data("""
            {"id":"claude:work","title":"Claude · Work","label":"Work"}
            """.utf8)
        )
        XCTAssertEqual(labeled.displayTitle, "Claude · Work")
        XCTAssertEqual(labeled.markTitle, "Work")

        // Older hosts only shipped the combined title.
        let legacy = try JSONDecoder().decode(
            QuotaProviderInfo.self,
            from: Data("""
            {"id":"claude:work","title":"Claude · Work"}
            """.utf8)
        )
        XCTAssertEqual(legacy.markTitle, "Work")

        let bare = try JSONDecoder().decode(
            QuotaProviderInfo.self,
            from: Data("""
            {"id":"claude","title":"Claude"}
            """.utf8)
        )
        XCTAssertEqual(bare.markTitle, "Claude")
        XCTAssertNil(bare.label)
    }
}
