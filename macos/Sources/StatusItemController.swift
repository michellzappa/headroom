import AppKit
import SwiftUI

@MainActor
final class StatusItemController: NSObject {
    private let statusItem: NSStatusItem
    private let popover = NSPopover()
    private let store: UsageStore
    private var eventMonitor: Any?
    private var preferencesObserver: NSObjectProtocol?

    init(store: UsageStore) {
        self.store = store
        self.statusItem = NSStatusBar.system.statusItem(
            withLength: NSStatusItem.squareLength
        )
        super.init()

        if let button = statusItem.button {
            // Empty snapshot, healthy mark: three empty tanks so the slot is
            // never blank while the first poll is still out.
            button.image = MeterIconRenderer.render(
                snapshot: .empty,
                healthy: true,
                attentionLevel: nil
            )
            button.imagePosition = .imageOnly
            button.toolTip = "Headroom"
            button.target = self
            button.action = #selector(togglePopover)
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        popover.behavior = .transient
        popover.animates = true
        popover.contentSize = NSSize(width: 390, height: 620)
        popover.contentViewController = NSHostingController(
            rootView: DashboardView(store: store)
        )

        store.onSnapshotChange = { [weak self] snapshot, healthy in
            self?.update(snapshot: snapshot, healthy: healthy)
            // Only off a healthy document: a failed poll replays the last good
            // one, and re-announcing a grant off a stale copy is how a reset
            // gets celebrated twice.
            if healthy {
                Task { await ResetNotifications.announce(snapshot) }
            }
        }
        preferencesObserver = NotificationCenter.default.addObserver(
            forName: UserDefaults.didChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.update(
                    snapshot: self.store.snapshot,
                    healthy: self.store.errorMessage == nil
                )
            }
        }
    }

    /// Where the icon sits on screen, for the welcome window's coach mark.
    /// Read on demand rather than cached: the icon moves when another app
    /// claims a slot, when it's dragged, and when a display comes or goes.
    var buttonScreenFrame: NSRect? {
        guard let button = statusItem.button, let window = button.window
        else { return nil }
        return window.convertToScreen(button.convert(button.bounds, to: nil))
    }

    /// Open the dashboard from outside. The welcome window's last step uses
    /// this so "it lives up there" ends on the thing itself.
    func openPopover() {
        guard !popover.isShown else { return }
        togglePopover()
    }

    private func update(snapshot: UsageSnapshot, healthy: Bool) {
        let attention = snapshot.attention
        let showPip = attention?.isWarning == true
        let style = MenuBarIconStyle.current
        let invert = MenuBarIconStyle.invert
        statusItem.button?.image = MeterIconRenderer.render(
            snapshot: snapshot,
            healthy: healthy,
            attentionLevel: showPip ? attention?.level : nil,
            style: style,
            invert: invert
        )
        if !healthy {
            // "Backend" is not a word this product uses anywhere else, and
            // "unavailable" was doing four different jobs. Settings already
            // calls this thing the host.
            statusItem.button?.toolTip = "\(HeadroomCopy.product) — host not answering"
        } else if showPip, let attention {
            statusItem.button?.toolTip =
                "\(HeadroomCopy.product) — \(attention.summary ?? HeadroomCopy.needsAttention)"
        } else {
            let parts = snapshot.codingQuotaProviders.map { provider in
                let meter = snapshot.meter(for: provider)
                let window = meter.menuBarWindow
                switch style {
                case .remaining:
                    guard let used = window.percent else {
                        return "\(provider.displayTitle) —"
                    }
                    let value = invert ? used : (100 - used)
                    let pct = Int(max(0, min(value, 100)).rounded())
                    let unit = invert ? "used" : "left"
                    return "\(provider.displayTitle) \(pct)% \(unit)"
                case .pace:
                    return Self.paceTooltip(
                        title: provider.displayTitle,
                        used: window.percent,
                        pace: window.pacePercent,
                        invert: invert
                    )
                }
            }
            statusItem.button?.toolTip = parts.isEmpty
                ? "Headroom"
                : "Headroom — \(parts.joined(separator: ", "))"
        }
    }

    /// Raw delta in the tooltip — the glyph compresses; the hover does not.
    /// `invert` swaps over/under so the words match the flipped glyph.
    private static func paceTooltip(
        title: String,
        used: Double?,
        pace: Double?,
        invert: Bool = false
    ) -> String {
        guard let used, let pace else { return "\(title) —" }
        let delta = used - pace
        let pts = Int(abs(delta).rounded())
        if pts < 2 { return "\(title) on pace" }
        let over = invert ? delta < 0 : delta > 0
        if over { return "\(title) \(pts)% over" }
        return "\(title) \(pts)% under"
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            closePopover()
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
            // Someone is looking — refresh now and hold the fast cadence.
            store.noteInteraction()
            Task { await store.refresh() }
            eventMonitor = NSEvent.addGlobalMonitorForEvents(
                matching: [.leftMouseDown, .rightMouseDown]
            ) { [weak self] _ in
                Task { @MainActor in self?.closePopover() }
            }
        }
    }

    private func closePopover() {
        popover.performClose(nil)
        if let eventMonitor {
            NSEvent.removeMonitor(eventMonitor)
            self.eventMonitor = nil
        }
    }
}

enum MeterIconRenderer {
    private static let outputScale: CGFloat = 2
    private static let canvasPixels = 36

    private struct PixelRect {
        let x: Int
        let y: Int
        let width: Int
        let height: Int

        var rect: CGRect {
            CGRect(
                x: CGFloat(x) / outputScale,
                y: CGFloat(y) / outputScale,
                width: CGFloat(width) / outputScale,
                height: CGFloat(height) / outputScale
            )
        }
    }

    static func render(
        snapshot: UsageSnapshot,
        healthy: Bool,
        attentionLevel: String? = nil,
        style: MenuBarIconStyle = .current,
        invert: Bool = MenuBarIconStyle.invert
    ) -> NSImage {
        // Settings subset only — never invent Claude/Codex/Cursor when
        // every quota source is off. The host picks which 3 (pinned
        // order, enabled only); icon geometry is sized for that same
        // hard limit.
        let windows = snapshot.focusProviders().map {
            snapshot.meter(for: $0).menuBarWindow
        }
        return render(
            windows: windows,
            healthy: healthy,
            attentionLevel: attentionLevel,
            style: style,
            invert: invert,
            accessibilityDescription: accessibilityLabel(
                snapshot: snapshot,
                style: style,
                invert: invert
            )
        )
    }

    /// Draw from prepared long-window tanks rather than a document. Settings
    /// draws its preview strip through here so the picker shows the real
    /// glyph, with sample numbers on a Mac whose sources are all off.
    static func render(
        windows: [MeterWindow],
        healthy: Bool,
        attentionLevel: String? = nil,
        style: MenuBarIconStyle = .current,
        invert: Bool = MenuBarIconStyle.invert,
        accessibilityDescription: String? = nil
    ) -> NSImage {
        let size = NSSize(width: 18, height: 18)
        let warning = attentionLevel == "warn" || attentionLevel == "critical"
        let image = NSImage(size: size, flipped: false) { _ in
            // While the first poll is still out (or nothing is enabled),
            // draw three empty slots so the icon is never blank.
            let barCount = windows.isEmpty ? 3 : windows.count
            let barWidthPixels = 6
            let barHeightPixels = 30
            let gapPixels = 5
            let groupWidth =
                barCount * barWidthPixels
                + max(0, barCount - 1) * gapPixels
            let groupX = (canvasPixels - groupWidth) / 2
            let barY = (canvasPixels - barHeightPixels) / 2
            let slotRect: (Int) -> PixelRect = { index in
                PixelRect(
                    x: groupX + index * (barWidthPixels + gapPixels),
                    y: barY,
                    width: barWidthPixels,
                    height: barHeightPixels
                )
            }

            let slots: [(PixelRect, MeterWindow?)] = {
                if windows.isEmpty {
                    return (0..<barCount).map { (slotRect($0), nil) }
                }
                return windows.enumerated().map { (slotRect($0.offset), $0.element) }
            }()

            switch style {
            case .remaining:
                for (rect, window) in slots {
                    drawVerticalBar(
                        rect: rect,
                        used: window?.percent,
                        healthy: healthy,
                        unavailable: window?.percent == nil,
                        invert: invert
                    )
                }
            case .pace:
                drawPaceGlyph(slots: slots, healthy: healthy, invert: invert)
            }

            if warning {
                let pip = PixelRect(x: 26, y: 26, width: 8, height: 8)
                let color = HeadroomPalette.nsAttention(attentionLevel)
                color.setFill()
                NSBezierPath(ovalIn: pip.rect).fill()
            }
            return true
        }
        // Template icons can't show the colored warning pip.
        image.isTemplate = !warning
        image.accessibilityDescription = accessibilityDescription
        return image
    }

    private static func accessibilityLabel(
        snapshot: UsageSnapshot,
        style: MenuBarIconStyle,
        invert: Bool
    ) -> String {
        let active = snapshot.codingQuotaProviders
        if active.isEmpty {
            return "Headroom — no coding providers enabled"
        }
        let labels = active.map(\.displayTitle).joined(separator: ", ")
        switch style {
        case .remaining:
            let reading = invert ? "used" : "remaining"
            return "\(labels) long-window quota \(reading)"
        case .pace:
            return "\(labels) long-window pace"
        }
    }

    private static func drawPaceGlyph(
        slots: [(PixelRect, MeterWindow?)],
        healthy: Bool,
        invert: Bool
    ) {
        guard let first = slots.first, let last = slots.last else { return }
        for (rect, window) in slots {
            drawSlotTrack(
                rect: rect,
                healthy: healthy,
                unavailable: window?.percent == nil || window?.pacePercent == nil
            )
        }

        // One rail across the group — the even-spend target.
        let midY = first.0.y + first.0.height / 2
        let rail = PixelRect(
            x: first.0.x,
            y: midY,
            width: last.0.x + last.0.width - first.0.x,
            height: 2
        )
        let railAlpha: CGFloat = healthy ? 0.40 : 0.25
        NSColor.labelColor.withAlphaComponent(railAlpha).setFill()
        NSBezierPath(rect: rail.rect).fill()

        let dotPixels = 4
        let padPixels = 3
        for (rect, window) in slots {
            guard let used = window?.percent, let pace = window?.pacePercent
            else { continue }
            let t = MenuBarIconStyle.paceOffset(
                used: used, pace: pace, invert: invert)
            let halfTravel = max(
                0,
                (rect.height / 2) - padPixels - (dotPixels / 2)
            )
            let centerY = midY + Int((CGFloat(t) * CGFloat(halfTravel)).rounded())
            let centerX = rect.x + rect.width / 2
            let dot = PixelRect(
                x: centerX - dotPixels / 2,
                y: centerY - dotPixels / 2,
                width: dotPixels,
                height: dotPixels
            )
            let fillAlpha: CGFloat = healthy ? 1 : 0.55
            NSColor.labelColor.withAlphaComponent(fillAlpha).setFill()
            NSBezierPath(ovalIn: dot.rect).fill()
        }
    }

    private static func drawSlotTrack(
        rect pixelRect: PixelRect,
        healthy: Bool,
        unavailable: Bool
    ) {
        let base = NSColor.labelColor
        let alpha: CGFloat = unavailable ? 0.45 : 1
        let trackFillAlpha: CGFloat = healthy ? 0.18 : 0.12
        let trackStrokeAlpha: CGFloat = healthy ? 0.36 : 0.22
        let frame = pixelRect.rect
        let radius = CGFloat(pixelRect.width / 2) / outputScale
        let track = NSBezierPath(
            roundedRect: frame,
            xRadius: radius,
            yRadius: radius
        )
        base.withAlphaComponent(trackFillAlpha * alpha).setFill()
        track.fill()

        let strokePixels = 2
        let insetPixels = strokePixels / 2
        let strokeRect = PixelRect(
            x: pixelRect.x + insetPixels,
            y: pixelRect.y + insetPixels,
            width: pixelRect.width - insetPixels * 2,
            height: pixelRect.height - insetPixels * 2
        )
        let stroke = NSBezierPath(
            roundedRect: strokeRect.rect,
            xRadius: CGFloat(max(0, pixelRect.width / 2 - insetPixels))
                / outputScale,
            yRadius: CGFloat(max(0, pixelRect.width / 2 - insetPixels))
                / outputScale
        )
        stroke.lineWidth = CGFloat(strokePixels) / outputScale
        base.withAlphaComponent(trackStrokeAlpha * alpha).setStroke()
        stroke.stroke()
    }

    private static func drawVerticalBar(
        rect pixelRect: PixelRect,
        used: Double?,
        healthy: Bool,
        unavailable: Bool = false,
        invert: Bool = false
    ) {
        let base = NSColor.labelColor
        let alpha: CGFloat = unavailable ? 0.45 : 1
        let trackFillAlpha: CGFloat = healthy ? 0.28 : 0.18
        let trackStrokeAlpha: CGFloat = healthy ? 0.44 : 0.28
        let fillAlpha: CGFloat = healthy ? 1 : 0.55
        let frame = pixelRect.rect
        let radius = CGFloat(pixelRect.width / 2) / outputScale
        let track = NSBezierPath(
            roundedRect: frame,
            xRadius: radius,
            yRadius: radius
        )
        base.withAlphaComponent(trackFillAlpha * alpha).setFill()
        track.fill()

        let strokePixels = 2
        let insetPixels = strokePixels / 2
        let strokeRect = PixelRect(
            x: pixelRect.x + insetPixels,
            y: pixelRect.y + insetPixels,
            width: pixelRect.width - insetPixels * 2,
            height: pixelRect.height - insetPixels * 2
        )
        let stroke = NSBezierPath(
            roundedRect: strokeRect.rect,
            xRadius: CGFloat(max(0, pixelRect.width / 2 - insetPixels))
                / outputScale,
            yRadius: CGFloat(max(0, pixelRect.width / 2 - insetPixels))
                / outputScale
        )
        stroke.lineWidth = CGFloat(strokePixels) / outputScale
        base.withAlphaComponent(trackStrokeAlpha * alpha).setStroke()
        stroke.stroke()

        guard let used else { return }
        let fraction = max(0, min(used / 100, 1))
        // Default Remaining = fuel left; Invert = used (the other reading).
        let fillFraction = invert ? fraction : (1 - fraction)
        let fillPixels = Int(
            (CGFloat(pixelRect.height) * CGFloat(fillFraction)).rounded()
        )
        guard fillPixels > 0 else { return }

        NSGraphicsContext.current?.cgContext.saveGState()
        track.addClip()
        base.withAlphaComponent(fillAlpha * alpha).setFill()
        NSBezierPath(
            rect: PixelRect(
                x: pixelRect.x,
                y: pixelRect.y,
                width: pixelRect.width,
                height: min(pixelRect.height, fillPixels)
            ).rect
        ).fill()
        NSGraphicsContext.current?.cgContext.restoreGState()
    }
}
