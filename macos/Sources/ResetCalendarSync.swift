import AppKit
import EventKit

/// Writes the reset calendar into a "Headroom" calendar through EventKit, so
/// it reaches the iPhone. See docs/calendar.md.
///
/// The host decides what the events are (`GET /calendar.json`, built by
/// `host/reset_calendar.py`, the same code as the .ics feed). This type only
/// carries them into Calendar: it creates, moves and removes its own events
/// and holds no opinion about what they should be.
///
/// **Why EventKit and not the feed.** A subscription stored in iCloud is
/// fetched by Apple's servers, which cannot reach a feed on this Mac. A
/// calendar the app writes into an iCloud account syncs like any other.
@MainActor
final class ResetCalendarSync: ObservableObject {
    static let shared = ResetCalendarSync()

    static let enabledKey = "resetCalendarWritesToCalendar"
    private static let calendarIDKey = "resetCalendarIdentifier"
    static let calendarTitle = "Headroom"
    /// Marks an event as ours. Matching by this, not by title, means a
    /// person's own events are never touched, and a moved reset updates the
    /// event it already made instead of adding a second one.
    private static let urlScheme = "headroom-calendar"
    /// Resets move with use and credits expire; this keeps the calendar about
    /// as current as the .ics feed Calendar.app polls.
    private static let interval: Duration = .seconds(300)

    /// What Settings shows under the toggle. Nil when there is nothing to say.
    @Published private(set) var status: String?
    @Published private(set) var accessDenied = false

    private let store = EKEventStore()
    private var loop: Task<Void, Never>?

    var isEnabled: Bool {
        UserDefaults.standard.bool(forKey: Self.enabledKey)
    }

    /// Called at launch. Does nothing until the person turns it on.
    func start() {
        guard isEnabled, loop == nil else { return }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                await self?.syncOnce()
                try? await Task.sleep(for: Self.interval)
            }
        }
    }

    func stop() {
        loop?.cancel()
        loop = nil
    }

    /// Turn writing on or off. On asks for calendar access first; off leaves
    /// the calendar and its events where they are, because deleting someone's
    /// calendar is not what "stop updating it" means.
    func setEnabled(_ enabled: Bool) async {
        guard enabled else {
            UserDefaults.standard.set(false, forKey: Self.enabledKey)
            stop()
            status = HeadroomCopy.calendarWriteStopped
            return
        }
        let granted = (try? await store.requestFullAccessToEvents()) ?? false
        accessDenied = !granted
        guard granted else {
            UserDefaults.standard.set(false, forKey: Self.enabledKey)
            status = HeadroomCopy.calendarWriteDenied
            return
        }
        UserDefaults.standard.set(true, forKey: Self.enabledKey)
        stop()
        start()
    }

    func openPrivacySettings() {
        if let url = URL(string:
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars") {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: One round

    private func syncOnce() async {
        guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else {
            accessDenied = true
            status = HeadroomCopy.calendarWriteDenied
            return
        }
        accessDenied = false
        do {
            let feed = try await HeadroomClient().fetchCalendarEvents()
            let calendar = try headroomCalendar()
            let changed = try apply(feed, to: calendar)
            status = HeadroomCopy.calendarWriteStatus(
                calendar: calendar.source.title, changed: changed)
        } catch {
            status = error.localizedDescription
        }
    }

    /// The calendar to write into: the one used last time, else an existing
    /// "Headroom" calendar in iCloud (another Mac may have made it), else a
    /// new one in iCloud, else wherever new calendars go on this Mac.
    private func headroomCalendar() throws -> EKCalendar {
        let defaults = UserDefaults.standard
        if let id = defaults.string(forKey: Self.calendarIDKey),
           let known = store.calendar(withIdentifier: id) {
            return known
        }
        let iCloud = store.sources.first {
            $0.sourceType == .calDAV && $0.title.localizedCaseInsensitiveContains("iCloud")
        }
        let existing = store.calendars(for: .event).first {
            $0.title == Self.calendarTitle
                && (iCloud == nil || $0.source.sourceIdentifier == iCloud?.sourceIdentifier)
        }
        if let existing {
            defaults.set(existing.calendarIdentifier, forKey: Self.calendarIDKey)
            return existing
        }
        guard let source = iCloud
            ?? store.defaultCalendarForNewEvents?.source
            ?? store.sources.first(where: { $0.sourceType == .local })
        else { throw SyncError.noSource }
        let calendar = EKCalendar(for: .event, eventStore: store)
        calendar.title = Self.calendarTitle
        calendar.source = source
        calendar.cgColor = NSColor(HeadroomPalette.claude).cgColor
        try store.saveCalendar(calendar, commit: true)
        defaults.set(calendar.calendarIdentifier, forKey: Self.calendarIDKey)
        return calendar
    }

    /// Make the calendar match the feed. Returns how many events changed.
    ///
    /// Only future events are removed: a reset that already happened stays
    /// in the calendar as a record, the way a past appointment does.
    private func apply(_ feed: [CalendarFeedEvent], to calendar: EKCalendar)
        throws -> Int {
        let now = Date()
        let window = store.predicateForEvents(
            withStart: now.addingTimeInterval(-2 * 86_400),
            end: now.addingTimeInterval(400 * 86_400),
            calendars: [calendar])
        var ours: [String: EKEvent] = [:]
        for event in store.events(matching: window) {
            guard let uid = Self.uid(of: event) else { continue }
            if ours[uid] != nil {
                // A duplicate, from two Macs racing on the first round.
                try store.remove(event, span: .thisEvent, commit: false)
            } else {
                ours[uid] = event
            }
        }

        var changed = 0
        for item in feed {
            let event = ours.removeValue(forKey: item.uid)
                ?? EKEvent(eventStore: store)
            if Self.write(item, into: event, calendar: calendar) {
                try store.save(event, span: .thisEvent, commit: false)
                changed += 1
            }
        }
        for (_, stale) in ours where stale.startDate > now {
            try store.remove(stale, span: .thisEvent, commit: false)
            changed += 1
        }
        if changed > 0 { try store.commit() }
        return changed
    }

    /// Copy one feed event onto a Calendar event. False when nothing changed,
    /// so an idle round writes nothing to iCloud.
    private static func write(_ item: CalendarFeedEvent, into event: EKEvent,
                              calendar: EKCalendar) -> Bool {
        let start = Date(timeIntervalSince1970: item.start)
        let end = Date(timeIntervalSince1970: item.end)
        let alarms = item.alertMin.map {
            [EKAlarm(relativeOffset: -Double($0) * 60)]
        } ?? []
        let url = URL(string: "\(urlScheme):\(item.uid)")
        let same = event.calendar == calendar
            && event.title == item.title
            && event.notes == item.description
            && event.startDate == start
            && event.endDate == end
            && event.url == url
            && (event.alarms ?? []).map(\.relativeOffset)
                == alarms.map(\.relativeOffset)
        guard !same else { return false }
        event.calendar = calendar
        event.title = item.title
        event.notes = item.description
        event.startDate = start
        event.endDate = end
        event.url = url
        event.availability = .free
        event.alarms = alarms
        return true
    }

    private static func uid(of event: EKEvent) -> String? {
        guard let url = event.url, url.scheme == urlScheme else { return nil }
        let text = url.absoluteString
        return String(text.dropFirst(urlScheme.count + 1))
    }

    enum SyncError: LocalizedError {
        case noSource
        var errorDescription: String? {
            HeadroomCopy.calendarWriteNoSource
        }
    }
}
