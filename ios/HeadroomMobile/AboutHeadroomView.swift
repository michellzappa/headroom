import SwiftUI

#if canImport(AppKit)
import AppKit
#endif
#if canImport(UIKit)
import UIKit
#endif

/// Compact About block for Settings on Mac and iPhone: icon, name, version,
/// who made it, and a light open-source / community touch (GitHub source +
/// live star count). Keeps the credit line in one place so the two surfaces
/// cannot drift.
struct AboutHeadroomView: View {
    @State private var starCount: Int?
    @State private var showingChangelog = false

    private static let githubURL = URL(
        string: "https://github.com/michellzappa/headroom"
    )!
    private static let communityURL = URL(
        string: "https://headroom-telemetry.mz-508.workers.dev/community"
    )!
    private static let starsAPIURL = URL(
        string: "https://api.github.com/repos/michellzappa/headroom"
    )!

    var body: some View {
        VStack(spacing: 6) {
            appIcon
                .resizable()
                .interpolation(.high)
                .scaledToFit()
                .frame(width: 64, height: 64)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .accessibilityHidden(true)

            Text(HeadroomCopy.product)
                .font(.headline)

            Text(versionLabel)
                .font(.caption)
                .foregroundStyle(.secondary)

            Text(HeadroomCopy.createdBy)
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Text(HeadroomCopy.publisher)
                .font(.caption)
                .foregroundStyle(.tertiary)

            VStack(spacing: 4) {
                Button(HeadroomCopy.changelog) {
                    showingChangelog = true
                }
                .font(.caption.weight(.medium))
                .buttonStyle(.plain)
                .foregroundStyle(.tint)

                Link(destination: Self.githubURL) {
                    Text(HeadroomCopy.aboutSourceOnGitHub)
                        .font(.caption.weight(.medium))
                }
                if let starCount {
                    Text(HeadroomCopy.aboutGitHubStars(starCount))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .accessibilityLabel(HeadroomCopy.aboutGitHubStars(starCount))
                }
                Link(destination: Self.communityURL) {
                    Text(HeadroomCopy.aboutCommunityPulse)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .padding(.top, 4)
            }
            .padding(.top, 6)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
        .task { await loadStarCount() }
        .sheet(isPresented: $showingChangelog) {
            NavigationStack {
                ChangelogView()
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button(HeadroomCopy.done) {
                                showingChangelog = false
                            }
                        }
                    }
            }
            #if os(macOS)
            .frame(minWidth: 480, minHeight: 420)
            #endif
        }
    }

    private var versionLabel: String {
        let short =
            Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString")
            as? String ?? "?"
        let build =
            Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion")
            as? String ?? "?"
        return "Version \(short) (\(build))"
    }

    private var appIcon: Image {
        // AppIcon.appiconset is not loadable as a named image, and on an
        // LSUIElement menu-bar app `applicationIconImage` is the generic
        // "no icon" placeholder. AboutAppIcon is the same artwork kept for
        // in-app display on both Mac and iPhone.
        #if os(macOS)
        if let image = NSImage(named: "AboutAppIcon") {
            Image(nsImage: image)
        } else {
            Image(systemName: "app.fill")
        }
        #elseif os(iOS)
        if let image = UIImage(named: "AboutAppIcon") {
            Image(uiImage: image)
        } else {
            Image(systemName: "app.fill")
        }
        #else
        Image(systemName: "app.fill")
        #endif
    }

    private func loadStarCount() async {
        var request = URLRequest(url: Self.starsAPIURL)
        request.timeoutInterval = 8
        // Not returnCacheDataElseLoad: that ignores expiry and freezes the
        // first count forever (Mac + iPhone About both stuck on a stale
        // number). Protocol policy honours GitHub's max-age=60.
        request.cachePolicy = .useProtocolCachePolicy
        request.setValue(
            "application/vnd.github+json",
            forHTTPHeaderField: "Accept"
        )
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  (200..<300).contains(http.statusCode),
                  let payload = try? JSONDecoder().decode(
                    GitHubRepoStars.self, from: data
                  )
            else { return }
            starCount = payload.stargazersCount
        } catch {
            // About stays useful offline; stars are a niceness, not a requirement.
        }
    }
}

private struct GitHubRepoStars: Decodable {
    let stargazersCount: Int

    enum CodingKeys: String, CodingKey {
        case stargazersCount = "stargazers_count"
    }
}
