import AppKit
import SwiftUI

/// Upcoming resets and grant expiries as a calendar Calendar.app subscribes
/// to. The host builds the feed (`host/reset_calendar.py`); this section only
/// sets its options and hands Calendar the link. See docs/calendar.md.
extension SettingsView {
    /// Values the host accepts for an alert, in the order the menu shows them.
    static let calendarAlertChoices: [Int?] = [nil, 0, 15, 60, 1440]

    /// `http://127.0.0.1:8737/calendar.ics`, from the endpoint this app
    /// talks to. Nil against a remote host: the feed is loopback only.
    ///
    /// Not `webcal://`. Calendar turns a webcal link into HTTPS and never
    /// falls back, and the host has no certificate. Given `http://` it tries
    /// TLS, then fetches over plain HTTP, which works.
    var calendarFeedURL: URL? {
        guard !endpointIsRemote,
              var parts = URLComponents(string: endpoint)
        else { return nil }
        parts.scheme = "http"
        parts.path = "/calendar.ics"
        parts.query = nil
        parts.fragment = nil
        return parts.url
    }

    var calendarSection: some View {
        Section {
            if let config = calendarConfig {
                Toggle(HeadroomCopy.calendarEnabled, isOn: calendarBinding(
                    config.enabled, key: "enabled"))
                if config.enabled {
                    Toggle(HeadroomCopy.calendarResets, isOn: calendarBinding(
                        config.resets, key: "resets"))
                    Toggle(HeadroomCopy.calendarShortWindows, isOn: calendarBinding(
                        config.shortWindows, key: "short_windows"))
                        .disabled(!config.resets)
                    Picker(HeadroomCopy.calendarResetAlert, selection: alertBinding(
                        config.resetAlertMin, key: "reset_alert_min")) {
                        alertChoices
                    }
                    .disabled(!config.resets)
                    Toggle(HeadroomCopy.calendarExpiries, isOn: calendarBinding(
                        config.expiries, key: "expiries"))
                    Picker(HeadroomCopy.calendarExpiryAlert, selection: alertBinding(
                        config.expiryAlertMin, key: "expiry_alert_min")) {
                        alertChoices
                    }
                    .disabled(!config.expiries)
                    if let url = calendarFeedURL {
                        HStack {
                            // Opening an http link would go to the browser,
                            // and a webcal link fails (see calendarFeedURL).
                            // So copy the link and bring Calendar forward.
                            Button(HeadroomCopy.calendarSubscribe) {
                                copyCalendarLink(url)
                                NSWorkspace.shared.open(URL(
                                    fileURLWithPath: "/System/Applications/Calendar.app"))
                                calendarMessage = HeadroomCopy.calendarSubscribeSteps
                            }
                            Button(HeadroomCopy.calendarCopyLink) {
                                copyCalendarLink(url)
                                calendarMessage = HeadroomCopy.calendarCopied
                            }
                            Spacer()
                        }
                    }
                }
            } else if endpointIsRemote {
                Text(HeadroomCopy.calendarRemoteHost)
                    .foregroundStyle(.secondary)
            } else {
                Text(HeadroomCopy.calendarUnavailable)
                    .foregroundStyle(.secondary)
            }
            if let calendarMessage {
                Text(calendarMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text(HeadroomCopy.calendarTitle)
        } footer: {
            Text(HeadroomCopy.calendarHint)
        }
        .task { await reloadCalendar() }
    }

    @ViewBuilder
    private var alertChoices: some View {
        ForEach(Self.calendarAlertChoices, id: \.self) { minutes in
            Text(HeadroomCopy.calendarAlertLabel(minutes)).tag(minutes)
        }
    }

    private func calendarBinding(_ value: Bool, key: String) -> Binding<Bool> {
        Binding(
            get: { value },
            set: { new in Task { await saveCalendar([key: new]) } })
    }

    private func alertBinding(_ value: Int?, key: String) -> Binding<Int?> {
        Binding(
            get: { value },
            set: { new in
                Task { await saveCalendar([key: new.map { $0 as Any } ?? NSNull()]) }
            })
    }

    private func copyCalendarLink(_ url: URL) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url.absoluteString, forType: .string)
    }

    func reloadCalendar() async {
        guard !endpointIsRemote else { return }
        calendarConfig = try? await client.fetchCalendarConfiguration()
    }

    func saveCalendar(_ updates: [String: Any]) async {
        do {
            let body = try JSONSerialization.data(withJSONObject: updates)
            calendarConfig = try await client.setCalendarConfiguration(body)
            calendarMessage = nil
        } catch {
            calendarMessage = error.localizedDescription
        }
    }
}
