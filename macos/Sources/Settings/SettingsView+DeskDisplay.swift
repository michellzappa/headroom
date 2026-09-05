import SwiftUI

/// Settings → Desk display: the ESP32 board's panel, as host-owned settings.
///
/// Nothing here talks to the board. The host keeps the answers in
/// `~/.headroom/config.json`, ships effective values in the device projection
/// (`/usage?view=device` → `display`), and the board applies them on its next
/// poll and mirrors them to NVS. Rings/Pace and the lower pane stay on-board
/// gestures on purpose: no setting has two owners (docs/esp32.md).
extension SettingsView {
    /// Board page order for the BOOT cycle. The ids themselves come from the
    /// host's payload; this only sorts them the way the board cycles.
    static let deskDisplayPageOrder = ["vercel", "git", "local"]

    /// The pages the board can draw, joined with the host's source list so
    /// each row carries the source's own title and on/off state. A page whose
    /// source is not in the list at all is not offered.
    var deskDisplayPageRows: [(id: String, title: String, sourceOn: Bool)] {
        let bySource = Dictionary(uniqueKeysWithValues: sources.map { ($0.id, $0) })
        return Self.deskDisplayPageOrder
            .filter { deskDisplay.pages[$0] != nil }
            .compactMap { id -> (id: String, title: String, sourceOn: Bool)? in
                guard let source = bySource[id] else { return nil }
                return (id, source.title ?? id, source.enabled ?? true)
            }
    }

    var deskDisplayPane: some View {
        Form {
            deskDisplayBoardSection
            deskDisplayPanelSection
            deskDisplayEffectsSection
            deskDisplayPagesSection
        }
        .task { await reloadDeskDisplay() }
    }

    var deskDisplayBoardSection: some View {
        Section {
            if let board = deskDisplay.board, let firmware = board.firmware {
                LabeledContent(HeadroomCopy.deskDisplayFirmware) {
                    Text(firmware)
                        .font(.body.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                LabeledContent(HeadroomCopy.deskDisplayConnection) {
                    Text(deskDisplayTransportName(board.via))
                        .foregroundStyle(.secondary)
                }
                LabeledContent(HeadroomCopy.deskDisplayLastSeen) {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(deskDisplayLivenessColor(board.liveness))
                            .frame(width: 8, height: 8)
                            .accessibilityLabel(deskDisplayLivenessLabel(board.liveness))
                        Text(deskDisplayAge(board.ageS, pollS: board.pollS))
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                Text(HeadroomCopy.deskDisplayNoBoard)
                    .foregroundStyle(.secondary)
            }
            HStack {
                Button(HeadroomCopy.settingsRefresh) {
                    Task { await reloadDeskDisplay() }
                }
                Spacer()
                if savingDeskDisplay {
                    ProgressView().controlSize(.small)
                }
            }
            if let deskDisplayMessage {
                Text(deskDisplayMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text(HeadroomCopy.deskDisplayBoard)
        } footer: {
            Text(deskDisplayEditable
                 ? HeadroomCopy.deskDisplayBoardHint
                 : HeadroomCopy.deskDisplayReadOnly)
        }
        // Last seen is a clock; keep it moving while the pane is open. One
        // loopback GET every 15 s, cancelled with the view.
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(15))
                guard !Task.isCancelled, !savingDeskDisplay else { continue }
                await reloadDeskDisplay()
            }
        }
    }

    var deskDisplayPanelSection: some View {
        Section {
            Picker(
                HeadroomCopy.deskDisplayBrightness,
                selection: Binding(
                    get: { deskDisplay.brightnessPct },
                    set: { pct in
                        deskDisplay.brightnessPct = pct
                        Task { await saveDeskDisplay(.brightnessPct(pct)) }
                    }
                )
            ) {
                ForEach(deskDisplay.brightnessSteps, id: \.self) { step in
                    Text("\(step)%").tag(step)
                }
            }
            .pickerStyle(.segmented)
            .disabled(!deskDisplayEditable || savingDeskDisplay)

            Toggle(
                HeadroomCopy.deskDisplayDim,
                isOn: deskDisplayBinding(\.dimAtNight) { .dimAtNight($0) }
            )
            .disabled(!deskDisplayEditable || savingDeskDisplay)
            if deskDisplay.dimAtNight {
                deskDisplayHourPicker(
                    HeadroomCopy.deskDisplayDimFrom,
                    hour: deskDisplay.dimStartHour
                ) { .dimStartHour($0) }
                deskDisplayHourPicker(
                    HeadroomCopy.deskDisplayDimUntil,
                    hour: deskDisplay.dimEndHour
                ) { .dimEndHour($0) }
                if deskDisplay.brightnessNowPct != deskDisplay.brightnessPct {
                    LabeledContent(HeadroomCopy.deskDisplayBrightnessNow) {
                        Text("\(deskDisplay.brightnessNowPct)%")
                            .foregroundStyle(.secondary)
                    }
                }
            }
        } header: {
            Text(HeadroomCopy.deskDisplayPanel)
        } footer: {
            Text(HeadroomCopy.deskDisplayDimHint(
                pct: deskDisplay.dimBrightnessPct,
                rampMinutes: deskDisplay.dimRampMinutes))
        }
    }

    var deskDisplayEffectsSection: some View {
        Section {
            Toggle(isOn: deskDisplayBinding(\.celebrateResets) { .celebrateResets($0) }) {
                Text(HeadroomCopy.deskDisplayCelebrateResets)
                Text(HeadroomCopy.deskDisplayCelebrateResetsHint)
            }
            .disabled(!deskDisplayEditable || savingDeskDisplay)
            Toggle(isOn: deskDisplayBinding(\.bootSplash) { .bootSplash($0) }) {
                Text(HeadroomCopy.deskDisplayBootSplash)
                Text(HeadroomCopy.deskDisplayBootSplashHint)
            }
            .disabled(!deskDisplayEditable || savingDeskDisplay)
        } header: {
            Text(HeadroomCopy.deskDisplayEffects)
        }
    }

    var deskDisplayPagesSection: some View {
        Section {
            let rows = deskDisplayPageRows
            if rows.isEmpty {
                Text(HeadroomCopy.deskDisplayPagesNone)
                    .foregroundStyle(.secondary)
            }
            ForEach(rows, id: \.id) { row in
                Toggle(
                    isOn: Binding(
                        get: { row.sourceOn && (deskDisplay.pages[row.id] ?? true) },
                        set: { shown in
                            deskDisplay.pages[row.id] = shown
                            Task { await saveDeskDisplay(.page(row.id, shown: shown)) }
                        }
                    )
                ) {
                    Text(row.title)
                    if !row.sourceOn {
                        Text(HeadroomCopy.deskDisplayPageSourceOff)
                    }
                }
                .disabled(!deskDisplayEditable || savingDeskDisplay || !row.sourceOn)
            }
        } header: {
            Text(HeadroomCopy.deskDisplayPages)
        } footer: {
            Text(HeadroomCopy.deskDisplayPagesHint)
        }
    }

    /// One hour of the day, as a menu of 24. Saves on change like the toggles.
    func deskDisplayHourPicker(
        _ title: String,
        hour: Int,
        change: @escaping (Int) -> DeskDisplayChange
    ) -> some View {
        Picker(
            title,
            selection: Binding(
                get: { hour },
                set: { picked in Task { await saveDeskDisplay(change(picked)) } }
            )
        ) {
            ForEach(0..<24, id: \.self) { h in
                Text(String(format: "%02d:00", h)).tag(h)
            }
        }
        .disabled(!deskDisplayEditable || savingDeskDisplay)
    }

    func deskDisplayLivenessColor(_ liveness: DeskDisplayBoard.Liveness) -> Color {
        switch liveness {
        case .live: return .green
        case .late: return .orange
        case .lost: return .red
        }
    }

    func deskDisplayLivenessLabel(_ liveness: DeskDisplayBoard.Liveness) -> String {
        switch liveness {
        case .live: return "Polling"
        case .late: return "Late"
        case .lost: return "Not polling"
        }
    }

    /// A toggle that writes through to the host and, on failure, snaps back
    /// to what the host actually holds.
    func deskDisplayBinding(
        _ keyPath: WritableKeyPath<DeskDisplayConfiguration, Bool>,
        change: @escaping (Bool) -> DeskDisplayChange
    ) -> Binding<Bool> {
        Binding(
            get: { deskDisplay[keyPath: keyPath] },
            set: { value in
                deskDisplay[keyPath: keyPath] = value
                Task { await saveDeskDisplay(change(value)) }
            }
        )
    }

    func deskDisplayTransportName(_ via: String?) -> String {
        switch via {
        case "usb": return HeadroomCopy.deskDisplayUSB
        case "wifi": return HeadroomCopy.deskDisplayWiFi
        case let other?: return other
        case nil: return HeadroomCopy.hostNotAvailable
        }
    }

    /// "12 sec. ago · every 60 s" — the age against the cadence it is judged
    /// by, so a red dot explains itself.
    func deskDisplayAge(_ ageS: Int?, pollS: Int?) -> String {
        guard let ageS else { return HeadroomCopy.hostNotAvailable }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        let age = formatter.localizedString(fromTimeInterval: -TimeInterval(ageS))
        guard let pollS else { return age }
        return "\(age) · every \(pollS) s"
    }

    func reloadDeskDisplay() async {
        do {
            deskDisplay = try await client.fetchDeskDisplayConfiguration()
            deskDisplayEditable = true
            deskDisplayMessage = nil
        } catch {
            // A host without the route answers 404; a host that is down
            // answers nothing. Either way there is nothing here to edit.
            deskDisplayEditable = false
        }
    }

    func saveDeskDisplay(_ change: DeskDisplayChange) async {
        savingDeskDisplay = true
        defer { savingDeskDisplay = false }
        do {
            deskDisplay = try await client.setDeskDisplayConfiguration(change)
            deskDisplayMessage = nil
        } catch {
            deskDisplayMessage = error.localizedDescription
            await reloadDeskDisplay()
        }
    }
}
