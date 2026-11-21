import SwiftUI

/// One caption for Settings connection state — hub trailing text and detail
/// Status rows.
///
/// Each integration used to pick its own colour and invent its own phrase for
/// the same three outcomes (healthy, needs attention, host too old to say).
/// Resolve here so orange only means something the reader can act on.
struct SettingsConnectionStatus: Equatable, Sendable {
    /// How loudly the caption reads.
    enum Tone: Equatable, Sendable {
        /// Healthy / expected. Secondary grey on the hub; green only when a
        /// detail pane also carries a glyph (Claude hooks installed).
        case ok
        /// Explicit success with a symbol — detail panes only.
        case good
        /// Something the user can fix. Orange.
        case attention
        /// Host predates the field, or state is unavailable. Secondary, never
        /// orange — colouring "unknown" would warn about nothing actionable.
        case unknown
    }

    let title: String
    let tone: Tone
    /// Optional glyph for detail panes that want more than a word.
    let symbol: String?

    init(_ title: String, tone: Tone, symbol: String? = nil) {
        self.title = title
        self.tone = tone
        self.symbol = symbol
    }

    var foregroundStyle: AnyShapeStyle {
        switch tone {
        case .ok, .unknown: AnyShapeStyle(.secondary)
        case .good: AnyShapeStyle(HeadroomPalette.green)
        case .attention: AnyShapeStyle(HeadroomPalette.orange)
        }
    }

    /// Hub / detail caption. Detail panes pass `showSymbol: true` when the
    /// status carries a glyph; the hub stays text-only so the trailing column
    /// stays a quiet secondary label.
    @ViewBuilder
    func label(showSymbol: Bool = false) -> some View {
        if showSymbol, let symbol {
            Label(title, systemImage: symbol)
                .font(.caption)
                .foregroundStyle(foregroundStyle)
        } else {
            Text(title)
                .foregroundStyle(foregroundStyle)
        }
    }

    // MARK: Factories

    static func connected(_ ok: Bool) -> SettingsConnectionStatus {
        SettingsConnectionStatus(
            ok ? HeadroomCopy.connected : HeadroomCopy.notConnected,
            tone: ok ? .ok : .attention
        )
    }

    /// Token-backed detail panes say where the credential lives, not just
    /// "Connected" — the hub already used that word.
    static func keychain(_ stored: Bool) -> SettingsConnectionStatus {
        SettingsConnectionStatus(
            stored ? HeadroomCopy.inKeychain : HeadroomCopy.notConnected,
            tone: stored ? .ok : .attention
        )
    }

    static func signedIn(_ ok: Bool) -> SettingsConnectionStatus {
        SettingsConnectionStatus(
            ok ? HeadroomCopy.signedIn : HeadroomCopy.notSignedIn,
            tone: ok ? .ok : .attention
        )
    }

    static var unknown: SettingsConnectionStatus {
        SettingsConnectionStatus(HeadroomCopy.statusUnknown, tone: .unknown)
    }

    static var folderMissing: SettingsConnectionStatus {
        SettingsConnectionStatus(HeadroomCopy.folderMissing, tone: .attention)
    }
}
