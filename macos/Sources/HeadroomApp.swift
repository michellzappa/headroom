import AppKit
import Security
import SwiftUI

@main
struct HeadroomApp: App {
    init() {
        TelemetryCoordinator.shared.start()
    }

    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        Settings {
            SettingsView()
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var store: UsageStore?
    private var statusController: StatusItemController?
    private var welcomeController: WelcomeWindowController?
    private var studyController: StudyWindowController?
    private var wakeObserver: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)
        let store = UsageStore()
        self.store = store
        statusController = StatusItemController(store: store)

        if let exportDir = Self.argValue("--export-screenshots") {
            // Keep the process alive until export finishes; accessory apps can
            // otherwise exit before a detached Task runs.
            DispatchQueue.main.async {
                Task { @MainActor in
                    await Self.exportScreenshots(
                        to: exportDir,
                        fixture: Self.argValue("--fixture"),
                        store: store
                    )
                    NSApp.terminate(nil)
                }
            }
            return
        }

        store.start()
        // Tokens stored before iCloud Keychain sync shipped are local-only, and
        // a local-only item is invisible to the synced query the host now runs.
        TokenStore.adoptSyncForStoredTokens()
        // After sleep the poll loop may still be on the idle cadence, and the
        // host's own poller is cold — force a source sync so bars move again.
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification,
            object: nil,
            queue: .main
        ) { [weak store] _ in
            Task { @MainActor in
                store?.noteInteraction()
                await store?.refresh(forceSync: true)
            }
        }
        Task { @MainActor in
            await Self.ensureHostRunning(store: store)
        }
        // Runs whether or not Settings is ever opened — an update nobody hears
        // about is the problem this exists to solve. The loop sleeps before its
        // first check, so launch never waits on the network.
        Task { @MainActor in
            await UpdateChecker.shared.runPeriodically()
        }

        let welcome = WelcomeWindowController(
            store: store,
            statusItemFrame: { [weak self] in self?.statusController?.buttonScreenFrame },
            onFinish: { [weak self] in
                // The window is mid-close, and closing it drops the app back to
                // `.accessory`. Showing the popover in the same turn races that
                // and it opens behind everything; next turn it lands.
                DispatchQueue.main.async {
                    self?.statusController?.openPopover()
                }
            }
        )
        welcomeController = welcome
        // Second and later launches return immediately.
        welcome.showIfPending()

        NotificationCenter.default.addObserver(
            forName: .headroomShowWelcome,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.welcomeController?.show() }
        }

        let study = StudyWindowController()
        studyController = study
        NotificationCenter.default.addObserver(
            forName: .headroomShowStudy,
            object: nil,
            queue: .main
        ) { _ in
            Task { @MainActor in study.show() }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let wakeObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(wakeObserver)
        }
        // Only does anything under HostLifecycle.appOwned. Deliberately not
        // waited on: the child also watches this pid, so a quit this handler
        // misses still stops the host within a couple of seconds.
        HostProcess.shared.stop()
    }

    /// If nothing answers on :8737 and this .app has a bundled host, install
    /// the LaunchAgent and wait for /health so first open isn't an error card.
    private static func ensureHostRunning(store: UsageStore) async {
        // Under the app-owned lifecycle a reachable host is not enough. It may
        // be a LaunchAgent from before the switch, or one started by hand. This
        // app is the supervisor now, so it starts its own child and
        // applyLifecycle retires whatever else was there.
        if HostLifecycle.current == .appOwned, !HostProcess.shared.isRunning {
            guard HostController.isBundled else { return }
            await store.updateHost()
            return
        }
        if await HostController.isReachable() {
            await store.refresh()
            // launchd kept an older host alive across this app's update? Say so
            // rather than rendering its stale document as if it were current.
            await store.checkHostVersion()
            return
        }
        guard HostController.isBundled else { return }
        // Through the store: `store.start()` is already ticking, and its own
        // version check can reach for the same bootout/bootstrap. One install,
        // both callers awaiting it. Failures land in SetupView, not a crash.
        await store.updateHost()
    }

    private static func argValue(_ flag: String) -> String? {
        let args = CommandLine.arguments
        guard let idx = args.firstIndex(of: flag), args.indices.contains(idx + 1)
        else { return nil }
        return args[idx + 1]
    }

    private static func exportScreenshots(
        to directory: String,
        fixture: String?,
        store: UsageStore
    ) async {
        let out = URL(fileURLWithPath: directory, isDirectory: true)
        do {
            try FileManager.default.createDirectory(
                at: out, withIntermediateDirectories: true)
        } catch {
            fputs("mkdir failed: \(error)\n", stderr)
            return
        }

        AttentionAck.dismissedFingerprint = nil
        UserDefaults.standard.set("overview", forKey: "selectedDashboard")
        // Usage · Attention · Activity — pin Usage so a leftover Attention
        // selection from a previous launch does not ship in the README shot.
        UserDefaults.standard.set(
            DashboardMode.overview.rawValue,
            forKey: "selectedDashboardMode")
        // Belt and braces: this path returns before the welcome window is even
        // built, but a shipped screenshot must never be onboarding.
        UserDefaults.standard.set(
            WelcomeWindowController.currentVersion,
            forKey: WelcomeWindowController.shownVersionKey)

        if let fixture {
            do {
                let data = try Data(contentsOf: URL(fileURLWithPath: fixture))
                let snapshot = try JSONDecoder().decode(
                    UsageSnapshot.self, from: data)
                store.applySnapshot(snapshot, healthy: true)
            } catch {
                fputs("fixture decode failed: \(error)\n", stderr)
                return
            }
        } else {
            await store.refresh()
        }

        // Let SwiftUI settle bindings before rasterizing.
        try? await Task.sleep(for: .milliseconds(500))

        let dashboard = DashboardView(store: store)
            .environment(\.colorScheme, .light)
        if let image = renderHosting(dashboard, size: NSSize(width: 390, height: 620)) {
            writePNG(image, to: out.appendingPathComponent("macos-popover.png"))
        } else {
            fputs("dashboard render returned nil\n", stderr)
        }

        let attention = store.snapshot.attention
        let showPip = attention?.isWarning == true
        let icon = MeterIconRenderer.render(
            snapshot: store.snapshot,
            healthy: store.errorMessage == nil,
            attentionLevel: showPip ? attention?.level : nil
        )
        if let rep = scaleIconRep(icon, pixels: 72),
           let png = rep.representation(using: .png, properties: [:]) {
            let iconURL = out.appendingPathComponent("macos-menubar-icon.png")
            do {
                try png.write(to: iconURL)
                fputs("wrote \(iconURL.path)\n", stderr)
            } catch {
                fputs("icon write failed: \(error)\n", stderr)
            }
        } else {
            fputs("icon scale failed\n", stderr)
        }
        fputs("exported screenshots to \(directory)\n", stderr)
    }

    private static func scaleIconRep(_ icon: NSImage, pixels: Int) -> NSBitmapImageRep? {
        guard let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: pixels,
            pixelsHigh: pixels,
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ) else { return nil }
        rep.size = NSSize(width: pixels, height: pixels)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        NSGraphicsContext.current?.imageInterpolation = .none
        NSColor.clear.setFill()
        NSRect(x: 0, y: 0, width: pixels, height: pixels).fill()
        // Template icons draw black; force a concrete appearance so bars are
        // visible when composited onto a light menubar strip.
        icon.isTemplate = false
        icon.draw(
            in: NSRect(x: 0, y: 0, width: pixels, height: pixels),
            from: .zero,
            operation: .sourceOver,
            fraction: 1
        )
        NSGraphicsContext.restoreGraphicsState()
        return rep
    }

    private static func renderHosting<V: View>(
        _ view: V,
        size: NSSize
    ) -> NSImage? {
        let host = NSHostingView(
            rootView: view.frame(width: size.width, height: size.height))
        host.frame = NSRect(origin: .zero, size: size)
        host.appearance = NSAppearance(named: .aqua)
        host.layoutSubtreeIfNeeded()

        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds)
        else { return nil }
        host.cacheDisplay(in: host.bounds, to: rep)
        let image = NSImage(size: size)
        image.addRepresentation(rep)
        return image
    }

    private static func writePNG(_ image: NSImage, to url: URL) {
        guard let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:])
        else {
            fputs("failed to encode \(url.lastPathComponent)\n", stderr)
            return
        }
        do {
            try png.write(to: url)
            fputs("wrote \(url.path)\n", stderr)
        } catch {
            fputs("write failed \(url.path): \(error)\n", stderr)
        }
    }
}
