import SwiftUI

/// One source-id → mark contract for every Apple surface. Account ids keep
/// their base provider before `:`, so e.g. `codex:work` uses the Codex mark.
///
/// Activity feed `kind` and Attention reason `kind` are not always the same
/// string as the host source id (`deployment` → Vercel, `github-inbox` →
/// GitHub). Resolve those through `sourceID(forKind:)` before looking up an
/// asset.
enum ProviderIcon {
    static func assetName(for id: String) -> String? {
        let providerID = String(id.split(separator: ":", maxSplits: 1).first ?? "")
        return switch providerID {
        case "claude", "claude-status": "ProviderClaude"
        case "codex": "ProviderCodex"
        case "cursor": "ProviderCursor"
        case "copilot": "ProviderCopilot"
        case "gemini": "ProviderGemini"
        case "antigravity": "ProviderAntigravity"
        case "windsurf": "ProviderWindsurf"
        case "jetbrains": "ProviderJetBrains"
        case "zed": "ProviderZed"
        case "openrouter": "ProviderOpenRouter"
        case "ai-gateway": "ProviderAIGateway"
        case "git", "commit": "ProviderGit"
        case "github", "github-inbox": "ProviderGitHub"
        case "vercel", "deployment": "ProviderVercel"
        case "supabase", "supabase-security": "ProviderSupabase"
        case "plausible": "ProviderPlausible"
        case "posthog": "ProviderPostHog"
        case "sentry": nil
        case "datadog": nil
        case "axiom": nil
        default: nil
        }
    }

    /// Activity / Attention kind → source id that owns the brand mark.
    /// Kinds with no brand (`stale`, `signin`, `reset`) return nil so the
    /// caller can keep a status glyph.
    static func sourceID(forKind kind: String?) -> String? {
        guard let kind, !kind.isEmpty else { return nil }
        switch kind {
        case "deployment": return "vercel"
        case "commit": return "git"
        case "github-inbox": return "github"
        case "supabase-security": return "supabase"
        case "claude-status": return "claude"
        case "sentry", "datadog", "axiom": return kind
        case "stale", "signin", "reset", "other": return nil
        default:
            return assetName(for: kind) != nil ? kind : nil
        }
    }
}

/// Monochrome by design: every SVG is an asset-catalog template and inherits
/// the surrounding primary/secondary foreground style on both macOS and iOS.
/// Brand colour belongs to AI providers (accent swatches, rings, charts) —
/// never paint a service mark with status red/amber; leave that for the
/// caption and for the fallback glyph when `sourceID(forKind:)` is nil.
///
/// Pass `fallbackSystemImage` when a missing brand should still occupy the
/// icon column (Settings Integrations, Attention reasons with no source).
struct ProviderMark: View {
    let providerID: String
    var size: CGFloat
    var fallbackSystemImage: String? = nil

    var body: some View {
        if let assetName = ProviderIcon.assetName(for: providerID) {
            Image(assetName)
                .resizable()
                .renderingMode(.template)
                .scaledToFit()
                .frame(width: size, height: size)
                .accessibilityHidden(true)
        } else if let fallbackSystemImage {
            Image(systemName: fallbackSystemImage)
                .resizable()
                .scaledToFit()
                .frame(width: size, height: size)
                .accessibilityHidden(true)
        }
    }

    /// Resolve an activity / attention kind onto a mark, with an optional
    /// status-symbol fallback when the kind has no brand.
    static func forKind(
        _ kind: String?,
        size: CGFloat,
        fallbackSystemImage: String? = nil
    ) -> some View {
        let sourceID = ProviderIcon.sourceID(forKind: kind) ?? ""
        return ProviderMark(
            providerID: sourceID,
            size: size,
            fallbackSystemImage: fallbackSystemImage
        )
    }
}
