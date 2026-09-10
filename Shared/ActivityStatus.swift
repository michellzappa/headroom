import SwiftUI

/// One vocabulary for every row in the merged Activity feed.
///
/// Status arrives from the host as a bare string (`failure`, `ready`,
/// `pushed`, …) and each surface used to decide on its own what that meant:
/// the Mac card painted everything except failures grey, so a production
/// deploy and an unpushed local commit looked identical, while iOS called a
/// pushed commit green — the same colour it gave a healthy deploy. Both now
/// resolve here, so green means "finished well" on every surface and nothing
/// but a real problem is red.
///
/// Colour is never the only channel: every state also carries a glyph and a
/// word, which is what makes the feed readable in greyscale and to anyone who
/// doesn't separate red from grey. Soft amber is in flight; orange is yours
/// to act on; red is broken.
struct ActivityStatusStyle {
    /// How loudly a row reads. The feed groups on `.attention`, so what is
    /// broken sits above what merely happened.
    enum Weight {
        /// Broken, and yours to fix.
        case attention
        /// In flight. Nothing to do but wait.
        case active
        /// Finished, and finished well.
        case good
        /// Routine bookkeeping — the bulk of the feed on a good day.
        case quiet
    }

    let label: String
    let symbol: String
    let tint: Color
    let weight: Weight

    var needsAttention: Bool { weight == .attention }

    static func resolve(_ status: String?) -> ActivityStatusStyle {
        // Host status strings are lowercased today; tolerate drift so a raw
        // Vercel `ERROR` / `BLOCKED` still lands on Attention rather than
        // looking like an unexplained quiet row.
        switch status?.lowercased() {
        case "error", "failure", "blocked":
            ActivityStatusStyle(
                label: HeadroomCopy.activityFailed,
                symbol: "exclamationmark.triangle.fill",
                tint: HeadroomPalette.red,
                weight: .attention
            )
        case "building", "initializing":
            ActivityStatusStyle(
                label: HeadroomCopy.activityBuilding,
                symbol: "hammer.fill",
                tint: HeadroomPalette.amber,
                weight: .active
            )
        case "running":
            ActivityStatusStyle(
                label: HeadroomCopy.activityRunning,
                symbol: "arrow.triangle.2.circlepath",
                tint: HeadroomPalette.amber,
                weight: .active
            )
        case "review_request":
            ActivityStatusStyle(
                label: HeadroomCopy.activityReviewRequest,
                symbol: "eye",
                tint: HeadroomPalette.orange,
                weight: .attention
            )
        case "assigned":
            ActivityStatusStyle(
                label: HeadroomCopy.activityAssigned,
                symbol: "person.crop.circle.badge.checkmark",
                tint: HeadroomPalette.orange,
                weight: .attention
            )
        case "mention":
            ActivityStatusStyle(
                label: HeadroomCopy.activityMention,
                symbol: "at",
                tint: HeadroomPalette.orange,
                weight: .attention
            )
        case "queued", "pending":
            ActivityStatusStyle(
                label: HeadroomCopy.activityQueued,
                symbol: "clock",
                tint: HeadroomPalette.amber,
                weight: .active
            )
        case "ready":
            ActivityStatusStyle(
                label: HeadroomCopy.activityDeployed,
                symbol: "checkmark.circle.fill",
                tint: HeadroomPalette.green,
                weight: .good
            )
        case "success", "completed":
            ActivityStatusStyle(
                label: HeadroomCopy.activityPassed,
                symbol: "checkmark.circle.fill",
                tint: HeadroomPalette.green,
                weight: .good
            )
        // A quota handed back. Green and `good`, because it is the one row in
        // this feed that is unambiguously in your favour — and `quiet` would
        // sort it in with routine bookkeeping, which a week of budget is not.
        case "granted":
            ActivityStatusStyle(
                label: HeadroomCopy.activityReset,
                symbol: "arrow.clockwise.circle.fill",
                tint: HeadroomPalette.green,
                weight: .good
            )
        // Cancelled is not a failure — nobody has to go look at it — so it
        // stays out of the attention group and out of red.
        case "canceled", "cancelled":
            ActivityStatusStyle(
                label: HeadroomCopy.activityCanceled,
                symbol: "slash.circle",
                tint: HeadroomPalette.dim,
                weight: .quiet
            )
        case "pushed":
            ActivityStatusStyle(
                label: HeadroomCopy.activityPushed,
                symbol: "arrow.up.circle",
                tint: HeadroomPalette.dim,
                weight: .quiet
            )
        case "local":
            ActivityStatusStyle(
                label: HeadroomCopy.activityLocal,
                symbol: "circle.dotted",
                tint: HeadroomPalette.dim,
                weight: .quiet
            )
        case "committed":
            ActivityStatusStyle(
                label: HeadroomCopy.activityCommitted,
                symbol: "circle",
                tint: HeadroomPalette.dim,
                weight: .quiet
            )
        // A status this build has never heard of still gets a word rather
        // than an unexplained dot: the host may ship a new one first.
        default:
            ActivityStatusStyle(
                label: humanized(status),
                symbol: "circle",
                tint: HeadroomPalette.dim,
                weight: .quiet
            )
        }
    }

    private static func humanized(_ status: String?) -> String {
        guard let status, !status.isEmpty else {
            return HeadroomCopy.activityCommitted
        }
        return status.prefix(1).uppercased() + status.dropFirst()
    }
}

/// Shared ordering when a surface groups the mixed host activity feed by
/// kind. Activity itself is chronological; contract tests and any kind-grouped
/// view still share this order.
struct ActivityGroup: Identifiable, Sendable {
    let kind: String
    let rows: [ActivityItem]

    var id: String { kind }
    var title: String { HeadroomCopy.activityGroupTitle(for: kind) }
}

enum ActivityGrouping {
    static let kindOrder = [
        "github", "deployment", "commit", "sentry", "datadog", "axiom",
        "supabase", "reset", "claude-status",
    ]

    static func groups(from rows: [ActivityItem]) -> [ActivityGroup] {
        let grouped = Dictionary(grouping: rows) { $0.kind ?? "" }
        let known = kindOrder.compactMap { kind -> ActivityGroup? in
            guard let rows = grouped[kind], !rows.isEmpty else { return nil }
            return ActivityGroup(kind: kind, rows: rows)
        }
        let unknown = grouped.keys
            .filter { !kindOrder.contains($0) }
            .sorted()
            .flatMap { grouped[$0] ?? [] }
        guard !unknown.isEmpty else { return known }
        return known + [ActivityGroup(kind: "other", rows: unknown)]
    }
}

extension ActivityItem {
    /// Does this row belong on Attention?
    ///
    /// Status decides it for everything the host can describe in one word.
    /// Where it cannot — an assignment that is still `assigned` but a year
    /// old — the host sends its own verdict and that wins. Every surface
    /// asks here, so the Attention queue and the "everything else" feed can
    /// never both drop the same row.
    var needsAttention: Bool {
        hostNeedsAttention ?? ActivityStatusStyle.resolve(status).needsAttention
    }

    /// "Review · web · @alice · #42" / "Failed · headroom · Release · main ·
    /// 1901f54" — state first, then the coordinates. Mac and iOS both call
    /// this so Attention and Activity stay word-for-word across appearances.
    func caption(label: String) -> String {
        var parts = [label]
        let repoLeaf = Self.leafName(repo)
        if let repoLeaf { parts.append(repoLeaf) }
        if let project, project != repoLeaf { parts.append(project) }
        if let author, !author.isEmpty {
            parts.append(author.hasPrefix("@") ? author : "@\(author)")
        }
        if let number { parts.append("#\(number)") }
        if let branch { parts.append(branch) }
        if let shortSHA { parts.append(shortSHA) }
        if status == "ready" {
            parts.append(target == "production" ? "prod" : "preview")
        }
        return parts.joined(separator: " · ")
    }

    /// `owner/name` → `name`. The owner is the same on every row here.
    static func leafName(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        return raw.split(separator: "/").last.map(String.init)
    }
}
