import Foundation

/// Builds the Attention queue as concrete, tappable rows.
///
/// The host rollup speaks in summaries ("2 failed deploys"). The Activity
/// feed — and, when that is quiet, the source payloads behind the reasons —
/// speak in events with URLs. Attention always prefers the latter so every
/// warning can drill into a detail page or open its permalink.
enum AttentionList {
    /// Feed rows that belong on Attention (failed deploys, inbox, red CI, …).
    static func failures(in snapshot: UsageSnapshot) -> [ActivityItem] {
        let flagged = (snapshot.activity ?? []).filter(\.needsAttention)
        if !flagged.isEmpty { return flagged }

        // Rollup lit up but nothing in the feed was marked — expand each
        // reason onto matching source rows so the list stays tappable.
        var seen = Set<String>()
        var expanded: [ActivityItem] = []
        for reason in snapshot.attention?.reasons ?? [] {
            for item in expand(reason, in: snapshot)
            where seen.insert(item.id).inserted {
                expanded.append(item)
            }
        }
        return expanded
    }

    /// Rollup reasons that have no concrete row to show. Once `failures`
    /// returns anything these stay empty — one broken build is not listed
    /// twice.
    static func leftoverReasons(in snapshot: UsageSnapshot) -> [AttentionReason] {
        guard failures(in: snapshot).isEmpty else { return [] }
        return snapshot.attention?.reasons ?? []
    }

    /// Best permalink for a leftover reason (source dashboard / first event).
    static func permalink(
        for reason: AttentionReason,
        in snapshot: UsageSnapshot
    ) -> URL? {
        if let item = expand(reason, in: snapshot).first {
            return Permalink.activity(item)
        }
        switch reason.kind {
        case "github", "github-inbox":
            return Permalink.url(from: "https://github.com/notifications")
        case "vercel":
            if let team = snapshot.vercel?.team, !team.isEmpty {
                return Permalink.url(from: "https://vercel.com/\(team)")
            }
            return Permalink.url(from: "https://vercel.com/dashboard")
        case "supabase", "supabase-security":
            return (snapshot.supabase?.projects ?? [])
                .compactMap { Permalink.url(from: $0.dashboardURL) }
                .first
        case "sentry":
            return (snapshot.activity ?? [])
                .first { $0.kind == "sentry" }
                .flatMap { Permalink.activity($0) }
        case "datadog":
            return (snapshot.activity ?? [])
                .first { $0.kind == "datadog" }
                .flatMap { Permalink.activity($0) }
        case "axiom":
            return (snapshot.activity ?? [])
                .first { $0.kind == "axiom" }
                .flatMap { Permalink.activity($0) }
        case "claude-status":
            return Permalink.url(from: "https://status.anthropic.com")
        default:
            return nil
        }
    }

    // MARK: - Expand

    private static func expand(
        _ reason: AttentionReason,
        in snapshot: UsageSnapshot
    ) -> [ActivityItem] {
        let feed = snapshot.activity ?? []
        switch reason.kind {
        case "vercel":
            let fromFeed = feed.filter {
                $0.kind == "deployment" && isProblem($0.status)
            }
            if !fromFeed.isEmpty { return fromFeed }
            return (snapshot.vercel?.deployments ?? [])
                .filter(isFailedDeploy)
                .map(activityItem(from:))
        case "github":
            let fromFeed = feed.filter {
                $0.kind == "github" && $0.status == "failure"
            }
            if !fromFeed.isEmpty { return fromFeed }
            return (snapshot.github?.runs ?? [])
                .filter { ($0.status ?? "") == "failure" }
                .map(activityItem(from:))
        case "github-inbox":
            let fromFeed = feed.filter {
                $0.kind == "github" && isInboxStatus($0.status)
                    && $0.needsAttention
            }
            if !fromFeed.isEmpty { return fromFeed }
            return (snapshot.github?.inbox ?? [])
                .map(activityItem(from:))
                .filter(\.needsAttention)
        case "supabase", "supabase-security":
            return feed.filter {
                $0.kind == "supabase" && isProblem($0.status)
            }
        case "sentry", "datadog", "axiom", "claude-status":
            return feed.filter {
                $0.kind == reason.kind && isProblem($0.status)
            }
        default:
            return []
        }
    }

    private static func isProblem(_ status: String?) -> Bool {
        ActivityStatusStyle.resolve(status).needsAttention
    }

    private static func isInboxStatus(_ status: String?) -> Bool {
        switch status {
        case "assigned", "review_request", "mention": true
        default: false
        }
    }

    private static func isFailedDeploy(_ deployment: Deployment) -> Bool {
        let status = (deployment.status ?? "").lowercased()
        let state = (deployment.state ?? "").uppercased()
        return status == "error" || status == "failure"
            || state == "ERROR" || state == "FAILED" || state == "BLOCKED"
    }

    private static func activityItem(from deployment: Deployment) -> ActivityItem {
        ActivityItem(
            id: deployment.id,
            kind: "deployment",
            status: deployment.status ?? "error",
            subject: deployment.commitMessage
                ?? deployment.project
                ?? "Deployment",
            repo: deployment.repo ?? deployment.project,
            project: deployment.project,
            branch: deployment.branch,
            sha: deployment.sha,
            shortSHA: deployment.shortSHA,
            target: deployment.target,
            ago: deployment.ago,
            errorMessage: deployment.errorMessage,
            url: deployment.url,
            inspectorURL: deployment.inspectorURL
        )
    }

    private static func activityItem(from item: GitHubInboxItem) -> ActivityItem {
        ActivityItem(
            id: "github-inbox:\(item.id)",
            kind: "github",
            status: item.reason ?? "assigned",
            subject: item.title ?? "GitHub",
            repo: item.repo,
            author: item.author,
            number: item.number,
            ago: item.ago,
            url: item.url,
            inspectorURL: item.url,
            hostNeedsAttention: item.hostNeedsAttention
        )
    }

    private static func activityItem(from run: GitHubRun) -> ActivityItem {
        ActivityItem(
            id: "github:\(run.id)",
            kind: "github",
            status: run.status ?? "failure",
            subject: run.displayTitle ?? run.name ?? "Workflow",
            repo: run.repo,
            project: run.name,
            branch: run.branch,
            sha: run.sha,
            shortSHA: run.shortSHA,
            ago: run.ago,
            url: run.url,
            inspectorURL: run.url
        )
    }
}
