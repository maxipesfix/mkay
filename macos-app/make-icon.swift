// Draws the app icon (a big prime-shaped apostrophe, which cannot pass for a comma, in black on a white rounded square) into an .iconset folder;
// build.sh turns it into AppIcon.icns with iconutil. Usage: swift make-icon.swift DIR
import AppKit

let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

/// The prime-shaped mark (as in m′kay): a wide flat top, slanting to a narrower flat foot.
/// Corners as fractions of its box, y up; the same outline as the menu-bar icon (AppDelegate).
let primeCorners: [(CGFloat, CGFloat)] = [(0.22, 1), (1, 1), (0.815, 0.52), (0.43, 0), (0, 0), (0.04, 0.55)]
let primeAspect: CGFloat = 0.52  // width / height

func prime(in box: NSRect) -> NSBezierPath {
    let path = NSBezierPath()
    for (index, (x, y)) in primeCorners.enumerated() {
        let point = NSPoint(x: box.minX + x * box.width, y: box.minY + y * box.height)
        if index == 0 { path.move(to: point) } else { path.line(to: point) }
    }
    path.close()
    return path
}

func draw(_ pixels: Int) -> Data {
    let size = CGFloat(pixels)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    // Apple's icon grid: an 824/1024 rounded square, centered.
    let inset = size * 100 / 1024
    let square = NSRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset)
    let shape = NSBezierPath(roundedRect: square, xRadius: square.width * 0.225, yRadius: square.width * 0.225)
    // A faint shadow gives the white tile its edge on light backgrounds, as macOS icons have.
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor(white: 0, alpha: 0.3)
    shadow.shadowBlurRadius = size * 0.02
    shadow.shadowOffset = NSSize(width: 0, height: -size * 0.008)
    shadow.set()
    NSColor.white.setFill()
    shape.fill()
    NSGraphicsContext.restoreGraphicsState()
    // The mark: a prime-shaped wedge, 62% of the square's height, centered.
    let height = square.height * 0.62
    let width = height * primeAspect
    NSColor.black.setFill()
    prime(in: NSRect(x: (size - width) / 2, y: (size - height) / 2, width: width, height: height)).fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for points in [16, 32, 128, 256, 512] {
    try draw(points).write(to: folder.appendingPathComponent("icon_\(points)x\(points).png"))
    try draw(points * 2).write(to: folder.appendingPathComponent("icon_\(points)x\(points)@2x.png"))
}
