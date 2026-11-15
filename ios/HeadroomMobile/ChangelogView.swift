import SwiftUI

/// Scrollable release notes from the bundled `CHANGELOG.md`.
///
/// Opened from Settings → About on Mac and iPhone. Same document the
/// release workflow and App Store What's New already copy from.
struct ChangelogView: View {
    private let document: ChangelogDocument?
    private let githubURL = URL(
        string: "https://github.com/michellzappa/headroom/blob/main/CHANGELOG.md"
    )!

    init(document: ChangelogDocument? = Changelog.load()) {
        self.document = document
    }

    var body: some View {
        Group {
            if let document, !document.versions.isEmpty {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 20) {
                        ForEach(document.versions) { version in
                            versionBlock(version)
                        }
                    }
                    .padding()
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                ContentUnavailableView {
                    Label(
                        HeadroomCopy.changelogUnavailable,
                        systemImage: "doc.text"
                    )
                } description: {
                    Text(HeadroomCopy.changelogUnavailableHint)
                } actions: {
                    Link(HeadroomCopy.changelogOnGitHub, destination: githubURL)
                }
            }
        }
        .navigationTitle(HeadroomCopy.changelog)
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }

    @ViewBuilder
    private func versionBlock(_ version: ChangelogVersion) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(version.title)
                .font(.title3.weight(.semibold))
                .accessibilityAddTraits(.isHeader)

            ForEach(Array(version.sections.enumerated()), id: \.offset) { _, section in
                VStack(alignment: .leading, spacing: 6) {
                    if !section.title.isEmpty {
                        Text(section.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .textCase(.uppercase)
                            .padding(.top, 2)
                    }
                    ForEach(Array(section.items.enumerated()), id: \.offset) { _, item in
                        HStack(alignment: .top, spacing: 8) {
                            Text("•")
                                .foregroundStyle(.secondary)
                            markdownText(item)
                                .font(.body)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }
        }
        .accessibilityElement(children: .contain)
    }

    private func markdownText(_ source: String) -> Text {
        if let attributed = try? AttributedString(
            markdown: source,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .inlineOnlyPreservingWhitespace
            )
        ) {
            return Text(attributed)
        }
        return Text(source)
    }
}
