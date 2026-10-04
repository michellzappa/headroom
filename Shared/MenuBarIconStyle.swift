import Foundation

/// How the macOS menu-bar glyph (and later the ESP32 glance) reads focus
/// providers. Remaining is fuel left; Pace is over/under even spend.
enum MenuBarIconStyle: String, CaseIterable, Sendable {
    case remaining
    case pace

    static let defaultsKey = "menuBarIconStyle"
    /// Flips Remaining (left ↔ used) and Pace (over ↔ under) without
    /// changing which of the two styles is active.
    static let invertDefaultsKey = "menuBarIconInvert"

    /// Softness of the pace curve. A delta of this many points maps near
    /// halfway to the edge (`tanh(1) ≈ 0.76`); small gaps stay readable and
    /// large ones asymptote instead of clipping.
    static let paceScale: Double = 8

    static var current: MenuBarIconStyle {
        MenuBarIconStyle(
            rawValue: UserDefaults.standard.string(forKey: defaultsKey) ?? ""
        ) ?? .remaining
    }

    static var invert: Bool {
        UserDefaults.standard.bool(forKey: invertDefaultsKey)
    }

    /// Draw the dark house tile (MenuBarPlate) behind the marks. Off by
    /// default: without it the glyph is a template image and follows the
    /// menu bar's own light or dark ink like every other status item.
    static let tileDefaultsKey = "menuBarIconTile"

    static var tile: Bool {
        UserDefaults.standard.bool(forKey: tileDefaultsKey)
    }

    /// Hide the attention dot on the icon. Stored as "hide" so the default,
    /// an unset key, keeps the dot. The popover and the tooltip still say
    /// what needs attention; this only stops the icon from showing it.
    static let hideAttentionDotDefaultsKey = "menuBarIconHideAttentionDot"

    static var hideAttentionDot: Bool {
        UserDefaults.standard.bool(forKey: hideAttentionDotDefaultsKey)
    }

    /// Maps `used% − pace%` onto (−1, +1) for vertical placement.
    /// `invert` flips the sign so over-pace sits below the rail instead.
    static func paceOffset(
        used: Double,
        pace: Double,
        scale: Double = paceScale,
        invert: Bool = false
    ) -> Double {
        guard scale > 0 else { return 0 }
        let t = tanh((used - pace) / scale)
        return invert ? -t : t
    }
}
