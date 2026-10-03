import AppKit
import SwiftUI

extension Notification.Name {
    /// The popover asks for the "Your usage" window; the app delegate owns it.
    static let headroomShowStudy = Notification.Name("headroom.showStudy")
}

/// "Your usage" in a real window, for the same reason the welcome is one: the
/// popover is `.transient` and a click anywhere else closes it, which is a bad
/// home for a screen you read slowly.
@MainActor
final class StudyWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private let store = StudyStore()
    /// Read for the Claude source's colour, which the model shades derive from.
    private let usage: UsageStore

    init(usage: UsageStore) {
        self.usage = usage
    }

    func show() {
        if let window {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
            return
        }
        let window = NSWindow(
            contentRect: NSRect(origin: .zero, size: StudyView.windowSize),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = HeadroomCopy.studyTitle
        window.contentView = NSHostingView(rootView: StudyView(store: store, usage: usage))
        window.delegate = self
        window.center()
        window.isReleasedWhenClosed = false
        self.window = window

        // Same flip as the welcome window: an `.accessory` app has no menu bar,
        // so Cmd-W and Cmd-Q would do nothing. `windowWillClose` undoes it.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        // Poll only while someone can see it. The host scan reads the whole
        // session-log tree.
        store.start()
    }

    func windowWillClose(_ notification: Notification) {
        store.stop()
        window = nil
        NSApp.setActivationPolicy(.accessory)
    }
}
