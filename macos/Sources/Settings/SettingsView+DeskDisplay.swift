import SwiftUI

/// Settings → Desk display: the ESP32 board's panel, as host-owned settings.
///
/// Nothing here talks to the board. The host keeps the answers in
/// `~/.headroom/config.json`, ships effective values in the device projection
/// (`/usage?view=device` → `display`), and the board applies them on its next
/// poll and mirrors them to NVS. Rings/Pace and the lower pane stay on-board
/// gestures on purpose: no setting has two owners (docs/esp32.md).
extension SettingsView {
    /// The three pages Settings can take out of the BOOT cycle, in the order
    /// the board cycles them. Slots are the host's `focus` and are not here.
    static let deskDisplayPageRows: [(id: String, title: String)] = [
        ("vercel", "Vercel"),
        ("git", "Git"),
        ("local", HeadroomCopy.localServers),
    ]

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
                    Text(deskDisplayAge(board.ageS))
                        .foregroundStyle(.secondary)
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
                HeadroomCopy.deskDisplayDimAtNight,
                isOn: deskDisplayBinding(\.dimAtNight) { .dimAtNight($0) }
            )
            .disabled(!deskDisplayEditable || savingDeskDisplay)
            if deskDisplay.dimAtNight, deskDisplay.dimmedNow {
                LabeledContent(HeadroomCopy.deskDisplayDimmedNow) {
                    Text("\(deskDisplay.nightBrightnessPct)%")
                        .foregroundStyle(.secondary)
                }
            }
        } header: {
            Text(HeadroomCopy.deskDisplayPanel)
        } footer: {
            Text(HeadroomCopy.deskDisplayDimHint(
                from: deskDisplay.nightStartHour,
                to: deskDisplay.nightEndHour,
                pct: deskDisplay.nightBrightnessPct))
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
            ForEach(Self.deskDisplayPageRows, id: \.id) { row in
                Toggle(
                    row.title,
                    isOn: Binding(
                        get: { deskDisplay.pages[row.id] ?? true },
                        set: { shown in
                            deskDisplay.pages[row.id] = shown
                            Task { await saveDeskDisplay(.page(row.id, shown: shown)) }
                        }
                    )
                )
                .disabled(!deskDisplayEditable || savingDeskDisplay)
            }
        } header: {
            Text(HeadroomCopy.deskDisplayPages)
        } footer: {
            Text(HeadroomCopy.deskDisplayPagesHint)
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

    func deskDisplayAge(_ ageS: Int?) -> String {
        guard let ageS else { return HeadroomCopy.hostNotAvailable }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(
            fromTimeInterval: -TimeInterval(ageS))
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
