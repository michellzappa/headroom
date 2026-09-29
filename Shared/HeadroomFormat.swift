import Foundation

/// Number and date shaping shared by macOS, iOS, and the widget.
///
/// These run inside chart bodies, so the formatters are built once and held
/// rather than allocated per bar.
enum HeadroomFormat {
    /// `en_US`, not POSIX: POSIX skips grouping and puts a NBSP after `$`,
    /// which is the opposite of what the labels want.
    private static let usdWhole: NumberFormatter = {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        formatter.currencySymbol = "$"
        formatter.usesGroupingSeparator = true
        formatter.minimumFractionDigits = 0
        formatter.maximumFractionDigits = 0
        return formatter
    }()

    private static let usdCents: NumberFormatter = {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        formatter.currencySymbol = "$"
        formatter.usesGroupingSeparator = true
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 2
        return formatter
    }()

    /// USD with grouping, keeping the app's existing whole-dollar display by
    /// default: `$12,475` rather than `$12475`.
    static func usd(_ value: Double, maximumFractionDigits: Int = 0) -> String {
        let formatter = maximumFractionDigits > 0 ? usdCents : usdWhole
        return formatter.string(from: NSNumber(value: value))
            ?? String(format: maximumFractionDigits > 0 ? "$%.2f" : "$%.0f", value)
    }

    /// "1.2k" / "3.4M" / "19.6B". Chart axes and traffic counts, where the
    /// exact figure is not the point and the column is narrow.
    static func compact(_ value: Int) -> String {
        if value >= 1_000_000_000 {
            return String(format: "%.1fB", Double(value) / 1_000_000_000)
        }
        if value >= 1_000_000 {
            return String(format: "%.1fM", Double(value) / 1_000_000)
        }
        if value >= 1_000 {
            return String(format: "%.1fk", Double(value) / 1_000)
        }
        return "\(value)"
    }

    private static let isoDay: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let weekday: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "EEE"
        return formatter
    }()

    /// "2026-07-28" → "Tue". Falls back to the bare "MM-DD" tail so an
    /// unparseable date still labels its bar instead of blanking it.
    static func shortWeekday(isoDate: String) -> String {
        guard let date = isoDay.date(from: isoDate) else {
            return String(isoDate.suffix(5))
        }
        return weekday.string(from: date)
    }

    /// "Fri 31 Jul, 11:05" — a past event that needs naming to the minute.
    ///
    /// Localised rather than POSIX, unlike the chart formatters above: this one
    /// is read as a sentence rather than matched against a key, and the day
    /// order is the reader's own.
    private static let eventMoment: DateFormatter = {
        let formatter = DateFormatter()
        formatter.setLocalizedDateFormatFromTemplate("EEE d MMM HH:mm")
        return formatter
    }()

    static func eventMoment(_ date: Date) -> String {
        eventMoment.string(from: date)
    }

    /// "Today 8%" / "Today 8.5%" — daily burn for one provider or the stack.
    /// Whole numbers drop the decimal so the board and the Mac agree.
    static func todayBurn(_ value: Double) -> String {
        let rounded = (value * 10).rounded() / 10
        if rounded == rounded.rounded() {
            return "Today \(Int(rounded))%"
        }
        return "Today \(rounded)%"
    }
}
