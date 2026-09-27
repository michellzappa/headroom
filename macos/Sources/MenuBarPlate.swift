import AppKit

/// House menu bar icon shared by Cargo, Tessellate and Headroom — keep the three
/// copies of this file identical. 18pt canvas, 16pt dark gradient plate with a
/// hairline rim, white marks drawn by the caller inside `MenuBarPlate.field`.
enum MenuBarPlate {
    static let canvas = NSSize(width: 18, height: 18)
    /// Plate bounds inside the canvas.
    static let frame = NSRect(x: 1, y: 1, width: 16, height: 16)
    /// Where marks go: plate inset by 3pt → 10pt square (20px @2x).
    static let field = NSRect(x: 4, y: 4, width: 10, height: 10)
    static let cornerRadius: CGFloat = 3.6
    static let markRadius: CGFloat = 0.75

    static func image(marks: @escaping (CGContext) -> Void) -> NSImage {
        let image = NSImage(size: canvas, flipped: false) { _ in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            drawPlate(ctx)
            marks(ctx)
            return true
        }
        image.isTemplate = false
        return image
    }

    static func drawPlate(_ ctx: CGContext) {
        let path = NSBezierPath(roundedRect: frame, xRadius: cornerRadius, yRadius: cornerRadius)
        ctx.saveGState()
        path.addClip()
        let colors = [
            NSColor(calibratedWhite: 0.28, alpha: 1).cgColor,
            NSColor(calibratedWhite: 0.13, alpha: 1).cgColor
        ] as CFArray
        if let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 1]) {
            ctx.drawLinearGradient(
                gradient,
                start: CGPoint(x: frame.minX, y: frame.maxY),
                end: CGPoint(x: frame.maxX, y: frame.minY),
                options: []
            )
        }
        ctx.restoreGState()
        let rim = NSBezierPath(
            roundedRect: frame.insetBy(dx: 0.25, dy: 0.25),
            xRadius: cornerRadius - 0.25,
            yRadius: cornerRadius - 0.25
        )
        rim.lineWidth = 0.5
        NSColor(calibratedWhite: 1, alpha: 0.16).setStroke()
        rim.stroke()
    }

    /// White mark with the shared corner radius. Coordinates are in points, snapped to @2x pixels.
    static func mark(_ rect: NSRect, alpha: CGFloat) {
        let snapped = NSRect(
            x: (rect.minX * 2).rounded() / 2,
            y: (rect.minY * 2).rounded() / 2,
            width: (rect.width * 2).rounded() / 2,
            height: (rect.height * 2).rounded() / 2
        )
        NSColor(calibratedWhite: 1, alpha: alpha).setFill()
        NSBezierPath(roundedRect: snapped, xRadius: markRadius, yRadius: markRadius).fill()
    }
}
