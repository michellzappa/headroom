import Foundation

/// Parsed `CHANGELOG.md` — the same file release tagging and App Store
/// What's New already use. Bundled so About stays readable offline.
struct ChangelogDocument: Equatable, Sendable {
    var versions: [ChangelogVersion]
}

struct ChangelogVersion: Equatable, Identifiable, Sendable {
    /// Marketing version, e.g. `1.9.5`.
    var version: String
    /// ISO date from the heading when present, e.g. `2026-08-07`.
    var date: String?
    var sections: [ChangelogSection]

    var id: String { version }

    var title: String {
        if let date, !date.isEmpty {
            return "\(version) — \(date)"
        }
        return version
    }
}

struct ChangelogSection: Equatable, Sendable {
    var title: String
    var items: [String]
}

enum Changelog {
    /// `CHANGELOG.md` shipped in the app bundle.
    static func load(from bundle: Bundle = .main) -> ChangelogDocument? {
        guard let url = bundle.url(forResource: "CHANGELOG", withExtension: "md"),
              let text = try? String(contentsOf: url, encoding: .utf8)
        else { return nil }
        return parse(text)
    }

    /// Turn Keep a Changelog-shaped markdown into version blocks.
    ///
    /// Soft-wrapped list lines (the house style in `CHANGELOG.md`) are joined
    /// so the reader sees one sentence per bullet, not a ragged line break
    /// mid-clause.
    static func parse(_ markdown: String) -> ChangelogDocument {
        var versions: [ChangelogVersion] = []
        var current: ChangelogVersion?
        var section: ChangelogSection?
        var pendingItem: String?

        func flushItem() {
            guard var item = pendingItem else { return }
            item = item.trimmingCharacters(in: .whitespacesAndNewlines)
            if !item.isEmpty {
                if section == nil {
                    section = ChangelogSection(title: "", items: [])
                }
                section?.items.append(item)
            }
            pendingItem = nil
        }

        func flushSection() {
            flushItem()
            guard let done = section else { return }
            if !done.items.isEmpty || !done.title.isEmpty {
                current?.sections.append(done)
            }
            section = nil
        }

        func flushVersion() {
            flushSection()
            if let done = current, !done.version.isEmpty {
                versions.append(done)
            }
            current = nil
        }

        for rawLine in markdown.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(rawLine)
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.hasPrefix("## ") {
                flushVersion()
                let heading = String(trimmed.dropFirst(3))
                    .trimmingCharacters(in: .whitespaces)
                let parts = heading.split(separator: "—", maxSplits: 1)
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                let version = parts.first ?? heading
                let date = parts.count > 1 ? parts[1] : nil
                current = ChangelogVersion(
                    version: version, date: date, sections: []
                )
                continue
            }

            // Preamble above the first version heading is release process,
            // not user-facing notes.
            guard current != nil else { continue }

            if trimmed.hasPrefix("### ") {
                flushSection()
                section = ChangelogSection(
                    title: String(trimmed.dropFirst(4))
                        .trimmingCharacters(in: .whitespaces),
                    items: []
                )
                continue
            }

            if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") {
                flushItem()
                pendingItem = String(trimmed.dropFirst(2))
                continue
            }

            if trimmed.isEmpty {
                flushItem()
                continue
            }

            // Soft wrap: continue the open bullet.
            if pendingItem != nil {
                pendingItem = (pendingItem ?? "") + " " + trimmed
                continue
            }

            // Rare prose under a version with no bullet — keep it.
            if section == nil {
                section = ChangelogSection(title: "", items: [])
            }
            flushItem()
            pendingItem = trimmed
        }

        flushVersion()
        return ChangelogDocument(versions: versions)
    }
}
