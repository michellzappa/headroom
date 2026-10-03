import AppKit
import SwiftUI

/// The eight first-run panes. Presented by `WelcomeWindowController`, never in
/// the popover — see that file for why.
///
/// Nothing here gates anything. Every pane is skippable and the app works if
/// the window is closed on the first one; the sources pane is the only one that
/// writes, and Settings can do the same job later.
///
/// Chrome is Liquid Glass on macOS 26 and later, with a material fallback
/// below it. `WelcomeGlass` owns every availability gate.
struct WelcomePane: Identifiable, Hashable {
    let id: String
    let railTitle: String
    let symbol: String

    static let what = WelcomePane(
        id: "what", railTitle: "What it is", symbol: "gauge.with.needle")
    static let find = WelcomePane(
        id: "find", railTitle: "Where it lives", symbol: "menubar.arrow.up.rectangle")
    static let helper = WelcomePane(
        id: "helper",
        railTitle: "Background helper",
        symbol: SettingsDestination.host.symbol)
    static let privacy = WelcomePane(
        id: "privacy", railTitle: "Your data", symbol: "lock")
    static let sources = WelcomePane(
        id: "sources",
        railTitle: HeadroomCopy.welcomeWhatToWatch,
        symbol: SettingsDestination.sources.symbol)
    static let phone = WelcomePane(
        id: "phone",
        railTitle: HeadroomCopy.welcomeOnYourPhone,
        symbol: SettingsDestination.iPhone.symbol)
    static let board = WelcomePane(
        id: "board", railTitle: "On your desk", symbol: "square.split.bottomrightquarter")
    static let done = WelcomePane(
        id: "done", railTitle: "Ready", symbol: "checkmark.seal")

    static let all: [WelcomePane] = [
        .what, .find, .helper, .privacy, .sources, .phone, .board, .done,
    ]
}

private enum WelcomeLink {
    static let privacy = URL(
        string: "https://github.com/michellzappa/headroom/blob/main/docs/privacy.md")!
    static let board = URL(
        string: "https://github.com/michellzappa/headroom/blob/main/README.md")!
}

struct WelcomeView: View {
    @ObservedObject var store: UsageStore
    var onPaneChange: @MainActor (WelcomePane) -> Void
    var onFinish: @MainActor () -> Void

    static let windowSize = CGSize(width: 720, height: 560)

    @State private var index = 0
    @State private var sourceRows: [SetupSourceRow] = []
    @State private var sourcesError: String?
    @State private var sourcesLoaded = false
    @State private var mobileToken: String?
    @State private var mobileTokenCopied = false
    @Namespace private var railNamespace

    private var pane: WelcomePane { WelcomePane.all[index] }
    private var isLast: Bool { index == WelcomePane.all.count - 1 }

    var body: some View {
        ZStack {
            WelcomeBackdrop()
            HStack(spacing: 0) {
                rail
                VStack(spacing: 0) {
                    ScrollView {
                        content
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 34)
                            .padding(.top, 34)
                            .padding(.bottom, 12)
                            .id(pane.id)
                            .transition(
                                .asymmetric(
                                    insertion: .opacity.combined(with: .offset(y: 12)),
                                    removal: .opacity))
                    }
                    .scrollIndicators(.never)
                    footer
                }
            }
        }
        .frame(width: Self.windowSize.width, height: Self.windowSize.height)
        .animation(.smooth(duration: 0.32), value: index)
        .task { onPaneChange(pane) }
        .onChange(of: index) { _, _ in onPaneChange(pane) }
    }

    // MARK: Chrome

    private var rail: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                if let icon = NSImage(named: "AboutAppIcon") {
                    // AboutAppIcon is the full-bleed artwork; the corner is ours
                    // to apply here, the same way AboutHeadroomView does.
                    Image(nsImage: icon)
                        .resizable()
                        .frame(width: 36, height: 36)
                        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(HeadroomCopy.product)
                        .font(.headline)
                    Text(versionLabel)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 22)

            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(WelcomePane.all.enumerated()), id: \.element.id) {
                    position, item in
                    railRow(position: position, item: item)
                }
            }
            .glassGroup(spacing: 14)
            .padding(.horizontal, 10)

            Spacer()
        }
        .padding(.top, 38)
        .frame(width: 208, alignment: .leading)
    }

    private func railRow(position: Int, item: WelcomePane) -> some View {
        let isCurrent = position == index
        let isPast = position < index
        return Button {
            index = position
        } label: {
            HStack(spacing: 10) {
                Image(systemName: isPast ? "checkmark.circle.fill" : item.symbol)
                    .font(.system(size: 12.5))
                    .frame(width: 17)
                    .foregroundStyle(
                        isPast
                            ? AnyShapeStyle(HeadroomPalette.green)
                            : AnyShapeStyle(isCurrent ? .primary : .secondary))
                Text(item.railTitle)
                    .font(.callout.weight(isCurrent ? .semibold : .regular))
                    .foregroundStyle(isCurrent ? .primary : .secondary)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
            .glassSelection(isCurrent, namespace: railNamespace)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isCurrent ? .isSelected : [])
    }

    private var footer: some View {
        HStack(spacing: 12) {
            Button("Back") { index -= 1 }
                .glassButton()
                .disabled(index == 0)
                .opacity(index == 0 ? 0 : 1)
            Spacer()
            if !isLast {
                Button("Skip") { finish() }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
            }
            Button(isLast ? HeadroomCopy.welcomeFinish : "Continue") {
                if isLast {
                    finish()
                } else {
                    advance()
                }
            }
            .glassButton(prominent: true)
            .keyboardShortcut(.defaultAction)
        }
        .controlSize(.large)
        .padding(.horizontal, 30)
        .padding(.top, 8)
        .padding(.bottom, 24)
    }

    private var versionLabel: String {
        let short =
            Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString")
            as? String ?? "?"
        return "Version \(short)"
    }

    // MARK: Panes

    @ViewBuilder
    private var content: some View {
        switch pane.id {
        case "what": whatPane
        case "find": findPane
        case "helper": helperPane
        case "privacy": privacyPane
        case "sources": sourcesPane
        case "phone": phonePane
        case "board": boardPane
        default: donePane
        }
    }

    private var whatPane: some View {
        paneBody(
            title: HeadroomCopy.welcomeTitle,
            lead: """
                Headroom watches how much of your AI coding plans you have left, \
                and tells you before you run out.
                """
        ) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 16) {
                    menuBarIconPreview
                    VStack(alignment: .leading, spacing: 4) {
                        Text("One tank per coding tool")
                            .font(.callout.weight(.semibold))
                        Text(
                            """
                            Each tank drains as you spend that plan. A glance \
                            at the menu bar answers the only question that \
                            matters mid-task: can I keep going?
                            """
                        )
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(18)
                .glassPanel()

                Text(
                    """
                    It watches the rest of your desk too: deploys, commits, \
                    GitHub Actions, and the dev servers running on this Mac. \
                    Open the dashboard for the full picture.
                    """
                )
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var findPane: some View {
        paneBody(
            title: "It lives in the menu bar",
            lead: """
                Headroom has no Dock icon and no main window. Everything is \
                behind one icon at the top of your screen.
                """
        ) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 13) {
                    // The icon itself, from the real renderer, rather than an
                    // arrow pointing off the window at it. What someone needs
                    // here is to know what they are looking for; the coach mark
                    // over the menu bar does the pointing.
                    Image(
                        nsImage: MeterIconRenderer.render(
                            snapshot: store.snapshot, healthy: true))
                    Text("This is the icon. It sits at the top right of your screen.")
                        .font(.callout.weight(.medium))
                    Spacer()
                }
                .padding(18)
                .glassPanel(tint: HeadroomPalette.green)

                bullets([
                    "Click the icon for quotas, activity, and local servers.",
                    """
                    Closing this window does not quit Headroom. It keeps \
                    running up there.
                    """,
                    """
                    To quit for real, open the dashboard and use the power \
                    button in its footer.
                    """,
                ])
            }
        }
    }

    private var helperPane: some View {
        paneBody(
            title: "A small helper runs in the background",
            lead: """
                So your numbers are current the moment you look, Headroom keeps \
                a local server running instead of waking up when you click.
                """
        ) {
            VStack(alignment: .leading, spacing: 16) {
                bullets([
                    """
                    It starts at login and listens on 127.0.0.1:8737, which \
                    only this Mac can reach.
                    """,
                    """
                    It is the same process your iPhone and the desk display \
                    read from, if you set those up later.
                    """,
                    """
                    It is Python from the standard library. No installer, no \
                    dependencies, nothing added to your PATH.
                    """,
                ])

                VStack(alignment: .leading, spacing: 7) {
                    if HostLifecycle.current == .appOwned {
                        detailRow("Runs", "With Headroom")
                    } else {
                        detailRow("Login item", HostController.label)
                    }
                    detailRow("Logs", "~/.headroom/logs")
                }
                .padding(16)
                .glassPanel(cornerRadius: 13)

                // Nothing to reveal in app-owned mode: there is no plist, and
                // pointing Finder at a path that does not exist is worse than
                // saying nothing.
                if HostLifecycle.current != .appOwned {
                    Button("Show the login item in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([
                            HostController.launchAgentURL
                        ])
                    }
                    .glassButton()
                    .controlSize(.small)
                }
            }
        }
    }

    private var privacyPane: some View {
        paneBody(
            title: "Nothing leaves this Mac",
            lead: """
                Headroom reads tools you are already signed into here. There is \
                no Headroom account and no server of ours in the path.
                """
        ) {
            VStack(alignment: .leading, spacing: 16) {
                bullets([
                    """
                    Usage data is read locally and stays local. It is never \
                    uploaded anywhere.
                    """,
                    """
                    Keys and tokens stay in the macOS Keychain. Headroom reads \
                    them to call the provider and never copies them out.
                    """,
                    "No analytics, no advertising, no crash reporters.",
                    """
                    The only outbound calls are to the providers you turn on in \
                    the next step, using credentials you already had.
                    """,
                ])
                Link("Read the full privacy policy", destination: WelcomeLink.privacy)
                    .font(.callout)
            }
        }
    }

    private var sourcesPane: some View {
        paneBody(
            title: "What should it watch?",
            lead: """
                Everything here was found on this Mac. Change it any time in \
                Settings → \(HeadroomCopy.settingsSources).
                """
        ) {
            VStack(alignment: .leading, spacing: 14) {
                if let sourcesError {
                    Text(sourcesError)
                        .font(.callout)
                        .foregroundStyle(HeadroomPalette.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if sourceRows.isEmpty {
                    HStack(spacing: 9) {
                        if !sourcesLoaded {
                            ProgressView().controlSize(.small)
                        }
                        Text(
                            sourcesLoaded
                                ? "Nothing detected yet. Settings has the full list."
                                : "Looking for signed-in tools…"
                        )
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 14) {
                        SetupSourcesList(rows: $sourceRows, enabled: true)
                    }
                    .padding(18)
                    .glassPanel()
                }
            }
            .task {
                guard !sourcesLoaded else { return }
                await loadSources()
            }
        }
    }

    private var phonePane: some View {
        paneBody(
            title: "Carry it on your phone",
            lead: """
                The iPhone app reads the same numbers from this Mac, and brings \
                notifications when something needs attention. The Apple Watch \
                app installs with it.
                """
        ) {
            VStack(alignment: .leading, spacing: 16) {
                numbered([
                    "Install the iPhone app from TestFlight.",
                    "On the phone, pick this Mac under Nearby Macs.",
                    "Paste the mobile token below when it asks for one.",
                ])

                Link(HeadroomCopy.openTestFlightInvite, destination: HeadroomCopy.testFlightInvite)
                    .font(.callout)

                mobileTokenPanel

                calloutBox(
                    symbol: "info.circle",
                    text: """
                        iOS will ask to allow Local Network. That is how the \
                        phone finds this Mac, and it is the only way the two \
                        talk. Nothing goes over the internet.
                        """
                )
            }
            .task { loadMobileToken() }
        }
    }

    /// The token itself, rather than directions to Settings. It is the one
    /// thing this pane exists to hand over, and the host has usually written it
    /// by the time anyone gets here.
    private var mobileTokenPanel: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("Mobile token")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 0)
                if let mobileToken {
                    Button(mobileTokenCopied ? "Copied" : "Copy") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(mobileToken, forType: .string)
                        mobileTokenCopied = true
                    }
                    .glassButton()
                    .controlSize(.small)
                } else {
                    Button("Check again") { loadMobileToken() }
                        .glassButton()
                        .controlSize(.small)
                }
            }

            if let mobileToken {
                Text(mobileToken)
                    .font(.system(.callout, design: .monospaced))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Text(
                    """
                    This is the phone's token. The host token in \
                    ~/.headroom/token is for the desk display, and the phone \
                    refuses it. Settings → \(HeadroomCopy.settingsiPhone) has \
                    this again later.
                    """
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(
                    """
                    The background helper writes the token the first time it \
                    runs. Give it a moment, then check again — or find it later \
                    in Settings → \(HeadroomCopy.settingsiPhone).
                    """
                )
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .glassPanel(cornerRadius: 13)
    }

    private var boardPane: some View {
        paneBody(
            title: "Optional: a display on your desk",
            lead: """
                Headroom can drive a small ESP32 screen that shows the same \
                rings all day without you opening anything.
                """
        ) {
            VStack(alignment: .leading, spacing: 16) {
                Image("ESP32Glance")
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: 320)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .padding(8)
                    .glassPanel(cornerRadius: 18)

                Text(
                    """
                    This is hardware you build yourself, and it is entirely \
                    optional. Skip it and nothing is missing: the Mac and the \
                    phone are the whole product.
                    """
                )
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

                Link("See the build guide", destination: WelcomeLink.board)
                    .font(.callout)
            }
        }
    }

    private var donePane: some View {
        paneBody(
            title: "You are set",
            lead: "One last thing about reading the icon."
        ) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 16) {
                    menuBarIconPreview
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Full means plenty left. The fill drops as you spend.")
                            .font(.callout)
                        Text("A coloured dot appears when something needs attention.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(18)
                .glassPanel()

                calloutBox(
                    symbol: "clock",
                    text: """
                        Burndown and daily burn need a few days of history \
                        before they can draw anything. Empty charts on day one \
                        are expected, not a fault.
                        """
                )

                Text("You can reopen this window any time from Settings.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Pieces

    private func paneBody(
        title: String,
        lead: String,
        @ViewBuilder body: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 22) {
            VStack(alignment: .leading, spacing: 9) {
                Text(title)
                    .font(.system(size: 25, weight: .semibold))
                Text(lead)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            body()
        }
    }

    private var menuBarIconPreview: some View {
        // The real renderer, not a drawing of it, so this pane cannot drift
        // from what is actually up there.
        Image(nsImage: MeterIconRenderer.render(snapshot: store.snapshot, healthy: true))
            .frame(width: 40, height: 40)
            .glassPanel(cornerRadius: 11)
    }

    private func bullets(_ items: [String]) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            ForEach(items, id: \.self) { item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: "circle.fill")
                        .font(.system(size: 4))
                        .foregroundStyle(.tertiary)
                    Text(item)
                        .font(.callout)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private func numbered(_ items: [String]) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            ForEach(Array(items.enumerated()), id: \.offset) { position, item in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(position + 1)")
                        .font(.caption.weight(.semibold).monospacedDigit())
                        .foregroundStyle(.secondary)
                        .frame(width: 20, height: 20)
                        .glassPanel(cornerRadius: 10)
                    Text(item)
                        .font(.callout)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 74, alignment: .leading)
            Text(value)
                .font(.caption.monospaced())
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }

    private func calloutBox(symbol: String, text: String) -> some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: symbol)
                .font(.callout)
                .foregroundStyle(.secondary)
            Text(text)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(15)
        .glassPanel(cornerRadius: 13)
    }

    // MARK: Actions

    private func advance() {
        // Selections are written when leaving the pane that made them, so
        // closing the window later never silently discards them.
        if pane.id == "sources" {
            Task { await saveSources() }
        }
        index = min(index + 1, WelcomePane.all.count - 1)
    }

    private func finish() {
        Task {
            await saveSources()
            onFinish()
        }
    }

    private func loadMobileToken() {
        mobileToken = HostController.mobileToken
        mobileTokenCopied = false
    }

    private func loadSources() async {
        do {
            sourceRows = try await HeadroomClient().fetchSetup().sources
            sourcesError = nil
        } catch {
            sourcesError = error.localizedDescription
        }
        sourcesLoaded = true
    }

    private func saveSources() async {
        guard !sourceRows.isEmpty else { return }
        let map = Dictionary(uniqueKeysWithValues: sourceRows.map { ($0.id, $0.enabled) })
        do {
            _ = try await HeadroomClient().setSources(map)
            await store.refresh()
        } catch {
            sourcesError = error.localizedDescription
        }
    }
}
