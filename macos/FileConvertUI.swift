// FileConvertUI: the press-and-hold branch menu and progress pill for FileConvert on macOS.
//
// Two ways to run (the installer builds it with `xcrun swiftc -O -parse-as-library`):
//
//   FileConvertUI --agent <targets.json> <converter command...>
//       Background helper started at login (LaunchAgent). Hold the mouse still on a file in Finder:
//       a ring fills, then curvy glass branches grow out of the file, one per format, each ending
//       in a glass label with a soft reflection. Slide onto a format and let go to convert it
//       (or let go anywhere, then click a format; click elsewhere to close). Needs Accessibility
//       permission (to see the hold) and asks Finder which file you're holding.
//
//   FileConvertUI '<json: files, options[{ext,label,caption}], legacy>'
//       Used by the right-click Quick Action: the same branches open at the pointer.
//       stdout: "PICK <ext>" or "CANCEL"; stdin: PROGRESS / STATUS / DONE / FAIL / QUIT lines.
//
// macOS 26 or later: real Liquid Glass (.glassEffect). macOS 12–15: frosted vibrancy glass.

import AppKit
import ApplicationServices
import SwiftUI

// MARK: - Data

struct Option: Codable, Hashable {
    let ext: String
    let label: String
    let caption: String
}

struct Config: Decodable {
    let files: [String]
    let options: [Option]
    let legacy: Bool?
}

let aliases = ["jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "heif": "heic", "aif": "aiff", "oga": "ogg", "mpeg": "mpg"]
func canonical(_ ext: String) -> String { aliases[ext.lowercased()] ?? ext.lowercased() }

/// Formats every selected file can become (a file already in that format is simply skipped).
func commonOptions(files: [String], table: [String: [Option]]) -> [Option] {
    let raw = files.map { URL(fileURLWithPath: $0).pathExtension.lowercased() }
    let lists = raw.map { table[$0] ?? [] }
    if lists.isEmpty || lists.contains(where: { $0.isEmpty }) { return [] }
    let exts = raw.map(canonical)
    var seen = Set<String>(), result = [Option]()
    for option in lists.flatMap({ $0 }) where !seen.contains(option.ext) {
        seen.insert(option.ext)
        let fits = zip(exts, lists).allSatisfy { src, list in src == option.ext || list.contains { $0.ext == option.ext } }
        if fits && exts.contains(where: { $0 != option.ext }) { result.append(option) }
    }
    return result
}

// MARK: - Branch layout (same as fileconvert/tree.py)

struct Leaf {
    let option: Option
    let tip: CGPoint          // label centre, relative to the held point (y down)
    let path: [CGPoint]       // cubic Bézier from the icon edge to the label
}

struct Room { var left: CGFloat, right: CGFloat, up: CGFloat, down: CGFloat }

enum Layout {
    static let labelW: CGFloat = 58, labelH: CGFloat = 30, startR: CGFloat = 36

    static func fanArc(_ room: Room, _ n: Int) -> (Double, Double) {
        let want = n < 2 ? 0 : min(168.0, 27.0 * Double(n - 1))
        let reach: CGFloat = n > 4 ? 196 : 150
        func fits(_ deg: Double) -> Bool {
            let tx = CGFloat(cos(deg * .pi / 180)) * (reach + labelW * 0.42)
            let ty = CGFloat(sin(deg * .pi / 180)) * (reach + labelH * 0.75)
            return labelW / 2 - tx <= room.left - 6 && tx + labelW / 2 <= room.right - 6
                && labelH / 2 - ty <= room.up - 6 && ty + labelH / 2 + 22 <= room.down - 6
        }
        let ok = stride(from: -180, to: 180, by: 3).map { fits(Double($0)) }
        if !ok.contains(false) { return (15, want) }
        var best = (0, 0), start: Int? = nil
        for (i, good) in (ok + ok).enumerated() {
            if good {
                if start == nil { start = i }
                let length = i - start! + 1
                if length > best.1 && length <= ok.count { best = (start!, length) }
            } else { start = nil }
        }
        if best.1 == 0 { return (15, want) }
        let lo = -180.0 + Double(best.0 * 3), width = Double((best.1 - 1) * 3)
        let centre = width >= want ? min(max(15, lo + want / 2), lo + width - want / 2) : lo + width / 2
        return (centre, min(want, width))
    }

    static func make(_ options: [Option], room: Room) -> [Leaf] {
        let n = options.count
        let (centre, span) = fanArc(room, n)
        return options.enumerated().map { i, option in
            let a = (centre - span / 2 + (n > 1 ? span * Double(i) / Double(n - 1) : 0)) * .pi / 180
            let r: CGFloat = n <= 4 ? 150 : (i % 2 == 0 ? 128 : 196)
            let d = CGPoint(x: cos(a), y: sin(a)), p = CGPoint(x: -d.y, y: d.x)
            let s = CGPoint(x: d.x * startR, y: d.y * startR), e = CGPoint(x: d.x * r, y: d.y * r)
            let bend = CGFloat(i % 2 == 1 ? 1 : -1) * 0.20 * r
            let c1 = CGPoint(x: s.x + (e.x - s.x) * 0.35 + p.x * bend, y: s.y + (e.y - s.y) * 0.35 + p.y * bend)
            let c2 = CGPoint(x: s.x + (e.x - s.x) * 0.72 - p.x * bend * 0.45, y: s.y + (e.y - s.y) * 0.72 - p.y * bend * 0.45)
            let tip = CGPoint(x: d.x * (r + labelW * 0.42), y: d.y * (r + labelH * 0.75))
            return Leaf(option: option, tip: tip, path: [s, c1, c2, e])
        }
    }

    static func hit(_ leaves: [Leaf], _ p: CGPoint) -> Int? {
        var best: (CGFloat, Int)? = nil
        for (i, leaf) in leaves.enumerated()
        where abs(p.x - leaf.tip.x) <= labelW / 2 + 6 && abs(p.y - leaf.tip.y) <= labelH / 2 + 6 {
            let dist = hypot(p.x - leaf.tip.x, p.y - leaf.tip.y)
            if best == nil || dist < best!.0 { best = (dist, i) }
        }
        return best?.1
    }
}

// MARK: - Easing (same curves as the Windows renderer)

func clamp01(_ x: Double) -> Double { min(1, max(0, x)) }
func easeOut(_ t: Double) -> Double { 1 - pow(1 - clamp01(t), 3) }
func easeInOut(_ t: Double) -> Double { let t = clamp01(t); return t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2 }
func springCurve(_ t: Double, _ response: Double, _ damping: Double) -> Double {
    if t <= 0 { return 0 }
    let w0 = 2 * Double.pi / response, wd = w0 * sqrt(1 - damping * damping)
    return 1 - exp(-damping * w0 * t) * (cos(wd * t) + damping * w0 / wd * sin(wd * t))
}

// MARK: - Model

enum Phase { case charging, branches, working, done, failed }

final class Model: ObservableObject {
    static let canvas = CGSize(width: 600, height: 600)
    let legacy: Bool
    @Published var phase: Phase = .branches
    @Published var root = CGPoint(x: 300, y: 300)
    @Published var leaves: [Leaf] = []
    @Published var hover: Int?
    @Published var chosen: Int?
    @Published var visible = true
    @Published var progress: Double?
    @Published var status = "Starting…"
    @Published var message = ""
    @Published var outputPath: String?
    @Published var shake: CGFloat = 0
    var chargeStart = Date(), chargeLength = 0.35
    var openedAt: Date?, choseAt: Date?, cancelAt: Date?
    var showRing = false
    var onTap: (() -> Void)?
    var onPick: ((Int) -> Void)?

    init(legacy: Bool) { self.legacy = legacy }

    var chosenLabel: String {
        guard let c = chosen, leaves.indices.contains(c) else { return "" }
        return leaves[c].option.label
    }

    var pillCenter: CGPoint {
        CGPoint(x: min(max(root.x, 175), Model.canvas.width - 175), y: min(max(root.y, 40), Model.canvas.height - 40))
    }

    func ring(_ now: Date) -> (progress: Double, alpha: Double, scale: Double)? {
        guard showRing else { return nil }
        guard let open = openedAt else {
            let t = now.timeIntervalSince(chargeStart)
            return (clamp01(t / chargeLength), easeOut(t / 0.12), 1)
        }
        let ot = now.timeIntervalSince(open)
        return ot < 0.2 ? (1, 1 - easeOut(ot / 0.2), 1 + 0.25 * easeOut(ot / 0.2)) : nil
    }

    func leaf(_ i: Int, _ now: Date) -> (grow: Double, pop: Double, position: CGPoint) {
        let tip = CGPoint(x: root.x + leaves[i].tip.x, y: root.y + leaves[i].tip.y)
        guard let open = openedAt else { return (0, 0, tip) }
        let ot = now.timeIntervalSince(open), b0 = 0.02 + Double(i) * 0.035
        var grow = easeOut((ot - b0) / 0.26)
        var pop = springCurve(ot - b0 - 0.14, 0.40, 0.60)
        var pos = tip
        if let c = choseAt {
            let ct = now.timeIntervalSince(c)
            if i == chosen {
                let k = easeInOut(ct / 0.42)
                pos = CGPoint(x: tip.x + (root.x - tip.x) * k, y: tip.y + (root.y - tip.y) * k)
                grow *= 1 - k
                pop *= 1 - 0.5 * k
                if ct > 0.3 { pop *= 1 - clamp01((ct - 0.3) / 0.15) }
            } else {
                grow *= 1 - easeOut(ct / 0.26)
                pop *= 1 - easeOut(ct / 0.2)
            }
        }
        if let c = cancelAt {
            let k = easeOut(now.timeIntervalSince(c) / 0.26)
            grow *= 1 - k
            pop *= 1 - k
        }
        return (grow, max(0, pop), pos)
    }
}

// MARK: - Glass

struct VisualEffect: NSViewRepresentable {
    let radius: CGFloat

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .popover
        view.blendingMode = .behindWindow
        view.state = .active
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.maskImage = VisualEffect.mask(radius)
    }

    static func mask(_ r: CGFloat) -> NSImage {
        let edge = r * 2 + 1
        let image = NSImage(size: NSSize(width: edge, height: edge), flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(roundedRect: rect, xRadius: r, yRadius: r).fill()
            return true
        }
        image.capInsets = NSEdgeInsets(top: r, left: r, bottom: r, right: r)
        image.resizingMode = .stretch
        return image
    }
}

struct GlassPanel: ViewModifier {
    let radius: CGFloat
    let legacy: Bool
    var shadow: CGFloat = 20

    func body(content: Content) -> some View {
        GlassPanel.apply(content, radius, legacy, shadow)
    }

    static func apply(_ content: Content, _ r: CGFloat, _ legacy: Bool, _ shadow: CGFloat) -> AnyView {
        let shape = RoundedRectangle(cornerRadius: r, style: .continuous)
        #if compiler(>=6.2)
        if #available(macOS 26.0, *), !legacy {
            return AnyView(content.glassEffect(.regular, in: shape))
        }
        #endif
        return AnyView(
            content
                .background(VisualEffect(radius: r))
                .overlay(shape.strokeBorder(
                    LinearGradient(colors: [Color.white.opacity(0.7), Color.white.opacity(0.1)],
                                   startPoint: .top, endPoint: .bottom),
                    lineWidth: 1))
                .shadow(color: Color.black.opacity(0.22), radius: shadow, x: 0, y: shadow / 2.4))
    }
}

// MARK: - Branch menu views

struct BranchShape: Shape {
    let points: [CGPoint]
    let root: CGPoint

    func path(in rect: CGRect) -> Path {
        let q = points.map { CGPoint(x: root.x + $0.x, y: root.y + $0.y) }
        var p = Path()
        p.move(to: q[0])
        p.addCurve(to: q[3], control1: q[1], control2: q[2])
        return p
    }
}

struct LeafLabel: View {
    let option: Option
    let lit: Bool
    let legacy: Bool

    var height: CGFloat { option.caption.isEmpty ? 30 : 38 }

    func text(_ white: Bool) -> some View {
        VStack(spacing: 0) {
            Text(option.label).font(.system(size: 12.5, weight: .semibold, design: .rounded))
            if !option.caption.isEmpty {
                Text(option.caption).font(.system(size: 9, weight: .medium)).opacity(0.75)
            }
        }
        .foregroundColor(white ? .white : .primary)
        .frame(width: Layout.labelW, height: height)
    }

    var body: some View {
        text(lit)
            .background(Capsule().fill(lit ? Color.accentColor : Color.clear))
            .modifier(GlassPanel(radius: height / 2, legacy: legacy, shadow: 6))
            .overlay(alignment: .bottom) {
                // mirror reflection under the label
                text(lit)
                    .background(Capsule().fill(lit ? Color.accentColor : Color.white.opacity(0.55)))
                    .mask(LinearGradient(colors: [.clear, .white], startPoint: .top, endPoint: .bottom))
                    .opacity(0.32)
                    .scaleEffect(x: 1, y: -0.55, anchor: .bottom)
                    .offset(y: 2)
                    .allowsHitTesting(false)
            }
    }
}

struct BranchMenuView: View {
    @ObservedObject var model: Model

    var body: some View {
        TimelineView(.animation) { context in
            ZStack(alignment: .topLeading) {
                if let ring = model.ring(context.date) {
                    Circle()
                        .trim(from: 0, to: ring.progress)
                        .stroke(Color.white, style: StrokeStyle(lineWidth: 3.4, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                        .frame(width: 80, height: 80)
                        .scaleEffect(ring.scale)
                        .opacity(ring.alpha)
                        .shadow(color: .white.opacity(0.6), radius: 4)
                        .position(model.root)
                }
                ForEach(Array(model.leaves.indices), id: \.self) { i in
                    BranchShape(points: model.leaves[i].path, root: model.root)
                        .trim(from: 0, to: model.leaf(i, context.date).grow)
                        .stroke(i == model.hover ? Color.accentColor : Color.white,
                                style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .shadow(color: (i == model.hover ? Color.accentColor : Color.white).opacity(0.7), radius: 4)
                }
                ForEach(Array(model.leaves.indices), id: \.self) { i in
                    LeafLabel(option: model.leaves[i].option, lit: i == model.hover, legacy: model.legacy)
                        .onHover { inside in if inside && model.choseAt == nil { model.hover = i } }
                        .onTapGesture { model.onPick?(i) }
                        .scaleEffect((i == model.hover ? 1.18 : 1.0) * model.leaf(i, context.date).pop)
                        .animation(.spring(response: 0.28, dampingFraction: 0.6), value: model.hover)
                        .opacity(min(1, model.leaf(i, context.date).pop * 1.4))
                        .position(model.leaf(i, context.date).position)
                }
            }
            .frame(width: Model.canvas.width, height: Model.canvas.height, alignment: .topLeading)
        }
    }
}

struct MenuRootView: View {
    @ObservedObject var model: Model

    var body: some View {
        ZStack(alignment: .topLeading) {
            if model.phase == .charging || model.phase == .branches {
                BranchMenuView(model: model)
            } else {
                ProgressCard(model: model)
                    .modifier(GlassPanel(radius: 30, legacy: model.legacy))
                    .modifier(Shake(animatableData: model.shake))
                    .position(model.pillCenter)
                    .transition(.scale(scale: 0.6).combined(with: .opacity))
            }
        }
        .frame(width: Model.canvas.width, height: Model.canvas.height, alignment: .topLeading)
        .opacity(model.visible ? 1 : 0)
        .scaleEffect(model.visible ? 1 : 0.9)
    }
}

// MARK: - Progress pill

struct ProgressCard: View {
    @ObservedObject var model: Model

    var headline: String {
        switch model.phase {
        case .done: return model.message
        case .failed: return "Couldn't convert"
        default: return "Converting to \(model.chosenLabel)"
        }
    }

    var subline: String {
        switch model.phase {
        case .done: return model.outputPath == nil ? "" : "Click to show in Finder"
        case .failed: return model.message
        default: return model.status
        }
    }

    var body: some View {
        HStack(spacing: 13) {
            RingView(phase: model.phase, progress: model.progress).frame(width: 36, height: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(headline).font(.system(size: 13, weight: .semibold)).lineLimit(1).truncationMode(.middle)
                if !subline.isEmpty {
                    Text(subline).font(.system(size: 11)).foregroundColor(.secondary)
                        .lineLimit(2).truncationMode(.middle)
                }
            }
            .id(model.phase)
            .transition(.opacity)
            Spacer(minLength: 6)
            if model.phase == .working, let p = model.progress {
                Text("\(Int((p * 100).rounded()))%")
                    .font(.system(size: 12, weight: .semibold, design: .rounded).monospacedDigit())
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(width: 320)
        .overlay(Group {
            if model.phase == .working {
                Shine().clipShape(RoundedRectangle(cornerRadius: 30, style: .continuous))
            }
        })
        .contentShape(Rectangle())
        .onTapGesture { model.onTap?() }
    }
}

let ringGradient = AngularGradient(
    gradient: Gradient(colors: [
        Color(red: 0.39, green: 0.82, blue: 1.00), Color(red: 0.04, green: 0.52, blue: 1.00),
        Color(red: 0.75, green: 0.35, blue: 0.95), Color(red: 1.00, green: 0.22, blue: 0.37),
        Color(red: 0.39, green: 0.82, blue: 1.00),
    ]),
    center: .center)

struct RingView: View {
    let phase: Phase
    let progress: Double?

    var body: some View {
        ZStack {
            switch phase {
            case .done:
                Badge(color: Color(red: 0.20, green: 0.78, blue: 0.35), mark: .check)
                    .transition(.scale(scale: 0.3).combined(with: .opacity))
            case .failed:
                Badge(color: Color(red: 1.00, green: 0.27, blue: 0.23), mark: .cross)
                    .transition(.scale(scale: 0.3).combined(with: .opacity))
            default:
                Circle().stroke(Color.primary.opacity(0.13), lineWidth: 3.6)
                if let p = progress {
                    Circle()
                        .trim(from: 0, to: max(0.015, p))
                        .stroke(ringGradient, style: StrokeStyle(lineWidth: 3.6, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                        .animation(.easeOut(duration: 0.3), value: p)
                } else {
                    Spinner()
                }
            }
        }
        .animation(.spring(response: 0.42, dampingFraction: 0.55), value: phase)
    }
}

struct Spinner: View {
    @State private var spin = false

    var body: some View {
        Circle()
            .trim(from: 0, to: 0.3)
            .stroke(ringGradient, style: StrokeStyle(lineWidth: 3.6, lineCap: .round))
            .rotationEffect(.degrees(spin ? 360 : 0))
            .animation(.linear(duration: 0.85).repeatForever(autoreverses: false), value: spin)
            .onAppear { spin = true }
    }
}

enum Mark { case check, cross }

struct MarkShape: Shape {
    let mark: Mark

    func path(in r: CGRect) -> Path {
        func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: r.minX + r.width * x, y: r.minY + r.height * y)
        }
        var p = Path()
        switch mark {
        case .check:
            p.move(to: pt(0.29, 0.52)); p.addLine(to: pt(0.44, 0.67)); p.addLine(to: pt(0.72, 0.36))
        case .cross:
            p.move(to: pt(0.35, 0.35)); p.addLine(to: pt(0.65, 0.65))
            p.move(to: pt(0.65, 0.35)); p.addLine(to: pt(0.35, 0.65))
        }
        return p
    }
}

struct Badge: View {
    let color: Color
    let mark: Mark
    @State private var drawn: CGFloat = 0

    var body: some View {
        ZStack {
            Circle().fill(color)
            MarkShape(mark: mark)
                .trim(from: 0, to: drawn)
                .stroke(Color.white, style: StrokeStyle(lineWidth: 3.4, lineCap: .round, lineJoin: .round))
        }
        .onAppear { withAnimation(.easeOut(duration: 0.34).delay(0.12)) { drawn = 1 } }
    }
}

struct Shine: View {
    @State private var start = Date()

    var body: some View {
        TimelineView(.animation) { context in
            GeometryReader { geo in
                let t = context.date.timeIntervalSince(start).truncatingRemainder(dividingBy: 2.8)
                LinearGradient(colors: [.clear, Color.white.opacity(0.30), .clear],
                               startPoint: .leading, endPoint: .trailing)
                    .frame(width: 70, height: geo.size.height * 2)
                    .rotationEffect(.degrees(18))
                    .position(x: -60 + (geo.size.width + 120) * easeInOut(t / 1.1), y: geo.size.height / 2)
                    .opacity(t < 1.1 ? 1 : 0)
            }
        }
        .allowsHitTesting(false)
    }
}

struct Shake: GeometryEffect {
    var animatableData: CGFloat

    func effectValue(size: CGSize) -> ProjectionTransform {
        ProjectionTransform(CGAffineTransform(translationX: 8 * sin(animatableData * .pi * 6), y: 0))
    }
}

// MARK: - One menu on screen (used by the agent and by the Quick Action)

final class Panel: NSPanel {
    override var canBecomeKey: Bool { true }
}

@MainActor
final class MenuSession {
    let model: Model
    let panel: Panel
    var files: [String] = []
    var finished = false
    var onClosed: (@MainActor () -> Void)?
    var onConvert: (@MainActor (Option) -> Void)?

    /// rootCocoa: the held point in screen coordinates (bottom-left origin).
    init(rootCocoa: NSPoint, legacy: Bool, interactive: Bool) {
        let model = Model(legacy: legacy)
        let size = Model.canvas
        let screen = NSScreen.screens.first { NSMouseInRect(rootCocoa, $0.frame, false) } ?? NSScreen.main
        let vf = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let ox = min(max(rootCocoa.x - size.width / 2, vf.minX), vf.maxX - size.width)
        let oy = min(max(rootCocoa.y - size.height / 2, vf.minY), vf.maxY - size.height)
        model.root = CGPoint(x: rootCocoa.x - ox, y: oy + size.height - rootCocoa.y)

        let panel = Panel(contentRect: NSRect(x: ox, y: oy, width: size.width, height: size.height),
                      styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .popUpMenu
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = !interactive
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let host = NSHostingView(rootView: MenuRootView(model: model))
        host.frame = NSRect(origin: .zero, size: size)
        panel.contentView = host
        self.model = model
        self.panel = panel
        let me = self
        model.onPick = { i in Task { @MainActor in me.choose(i) } }
        model.onTap = { Task { @MainActor in me.tapped() } }
        panel.orderFrontRegardless()
    }

    var room: Room {
        Room(left: model.root.x, right: Model.canvas.width - model.root.x,
             up: model.root.y, down: Model.canvas.height - model.root.y)
    }

    func charge(seconds: Double) {
        model.phase = .charging
        model.showRing = true
        model.chargeStart = Date()
        model.chargeLength = seconds
    }

    func open(_ options: [Option], files: [String]) {
        self.files = files
        model.leaves = Layout.make(options, room: room)
        model.openedAt = Date()
        model.phase = .branches
    }

    /// Convert a global CoreGraphics point (top-left origin) to a point relative to the held spot.
    func relative(_ cg: CGPoint) -> CGPoint {
        let primary = NSScreen.screens.first?.frame.height ?? 0
        let cocoa = NSPoint(x: cg.x, y: primary - cg.y)
        let f = panel.frame
        let local = CGPoint(x: cocoa.x - f.minX, y: f.maxY - cocoa.y)
        return CGPoint(x: local.x - model.root.x, y: local.y - model.root.y)
    }

    func hover(_ cg: CGPoint) {
        guard model.phase == .branches, model.choseAt == nil else { return }
        let i = Layout.hit(model.leaves, relative(cg))
        if i != model.hover { model.hover = i }
    }

    /// Let go after holding: convert if over a format. Returns true if a format was chosen.
    func release(_ cg: CGPoint) -> Bool {
        guard model.phase == .branches, let i = Layout.hit(model.leaves, relative(cg)) else { return false }
        choose(i)
        return true
    }

    func click(_ cg: CGPoint) {
        if model.phase == .branches, let i = Layout.hit(model.leaves, relative(cg)) { choose(i) } else { cancel() }
    }

    func choose(_ i: Int) {
        guard model.choseAt == nil, model.cancelAt == nil, model.leaves.indices.contains(i) else { return }
        model.chosen = i
        model.hover = i
        model.choseAt = Date()
        onConvert?(model.leaves[i].option)
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 450_000_000)
            withAnimation(.spring(response: 0.45, dampingFraction: 0.75)) { self.model.phase = .working }
            self.panel.ignoresMouseEvents = false               // the pill can be clicked
        }
    }

    func cancel() {
        guard model.cancelAt == nil, model.choseAt == nil else { return }
        if model.openedAt == nil { model.openedAt = Date() }
        model.cancelAt = Date()
        closeLater(0.36)
    }

    func handle(_ line: String) {
        let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
        guard let command = parts.first else { return }
        let argument = parts.count > 1 ? parts[1] : ""
        switch command {
        case "PROGRESS":
            model.progress = Double(argument).map { min(1, max(0, $0)) }
        case "STATUS":
            model.status = argument
        case "DONE":
            let bits = argument.components(separatedBy: "\t")
            withAnimation(.spring(response: 0.45, dampingFraction: 0.7)) {
                model.message = bits[0]
                model.outputPath = bits.count > 1 && !bits[1].isEmpty ? bits[1] : nil
                model.progress = 1
                model.phase = .done
            }
            closeLater(2.3)
        case "FAIL":
            withAnimation(.spring(response: 0.45, dampingFraction: 0.7)) {
                model.message = argument
                model.phase = .failed
            }
            withAnimation(.linear(duration: 0.5)) { model.shake += 1 }
            closeLater(4.5)
        case "QUIT":
            close()
        default:
            break
        }
    }

    func tapped() {
        if model.phase == .done, let path = model.outputPath {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        }
        if model.phase == .done || model.phase == .failed { close() }
    }

    func closeLater(_ seconds: Double) {
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            self.close()
        }
    }

    func close() {
        guard !finished else { return }
        finished = true
        withAnimation(.easeIn(duration: 0.2)) { model.visible = false }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 250_000_000)
            self.panel.orderOut(nil)
            self.onClosed?()
        }
    }
}

// MARK: - Quick Action mode (driven by Python over stdin/stdout)

@MainActor
final class PickController {
    let config: Config
    var session: MenuSession?
    var monitors: [Any] = []

    init(config: Config) { self.config = config }

    func start() {
        let session = MenuSession(rootCocoa: NSEvent.mouseLocation, legacy: config.legacy ?? false, interactive: true)
        self.session = session
        session.onConvert = { option in print("PICK \(option.ext)"); fflush(stdout) }
        session.onClosed = {
            if session.model.choseAt == nil { print("CANCEL"); fflush(stdout) }
            NSApp.terminate(nil)
        }
        session.open(config.options, files: config.files)
        session.panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        if let m = NSEvent.addLocalMonitorForEvents(matching: .keyDown, handler: { event in
            if event.keyCode == 53 { session.cancel(); return nil }      // esc
            return event
        }) { monitors.append(m) }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown], handler: { _ in
            Task { @MainActor in if session.model.choseAt == nil { session.cancel() } }
        }) { monitors.append(m) }

        let me = self
        Thread.detachNewThread {
            while let line = readLine() { Task { @MainActor in me.session?.handle(line) } }
            Task { @MainActor in
                if let s = me.session, s.model.phase == .working { s.handle("FAIL Stopped unexpectedly") }
            }
        }
    }
}

// MARK: - Press-and-hold agent

let injectedMark: Int64 = 0xFC0FC0

/// Watches the left mouse button through an event tap (not main-actor: called from the tap).
final class HoldDetector {
    var tap: CFMachPort?
    var downAt: CGPoint?
    var pressing = false        // held still on something, waiting for the hold to complete
    var capturing = false       // hold completed: this press belongs to the menu until mouse-up
    var menuOpen = false        // branches open after release: clicks go to the menu
    var onDown: ((CGPoint) -> Void)?
    var onCancel: (() -> Void)?
    var onMove: ((CGPoint) -> Void)?
    var onRelease: ((CGPoint) -> Void)?
    var onClick: ((CGPoint) -> Void)?

    func handle(_ type: CGEventType, _ event: CGEvent) -> Unmanaged<CGEvent>? {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap = tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return Unmanaged.passUnretained(event)
        }
        if event.getIntegerValueField(.eventSourceUserData) == injectedMark {
            return Unmanaged.passUnretained(event)
        }
        let loc = event.location
        switch type {
        case .leftMouseDown:
            if menuOpen && !capturing {
                onClick?(loc)
                return nil                                   // the click belongs to the menu
            }
            pressing = true
            downAt = loc
            onDown?(loc)
        case .leftMouseDragged:
            if capturing {
                event.type = .mouseMoved                     // Finder sees a move, never a drag
                onMove?(loc)
            } else if pressing, let d = downAt, hypot(loc.x - d.x, loc.y - d.y) > 6 {
                pressing = false
                onCancel?()
            }
        case .leftMouseUp:
            if capturing {
                capturing = false
                onRelease?(loc)
                return nil                                   // Finder already got its click
            }
            if pressing {
                pressing = false
                onCancel?()
            }
        case .mouseMoved:
            if menuOpen { onMove?(loc) }
        case .rightMouseDown:
            if pressing { pressing = false; onCancel?() }
        default:
            break
        }
        return Unmanaged.passUnretained(event)
    }

    /// The hold completed: finish Finder's click (so it just selects) and keep the rest of the press.
    func beginCapture() -> Bool {
        guard pressing, let at = downAt else { return false }
        pressing = false
        capturing = true
        let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: at, mouseButton: .left)
        up?.setIntegerValueField(.eventSourceUserData, value: injectedMark)
        up?.post(tap: .cgSessionEventTap)
        return true
    }
}

func tapCallback(proxy: CGEventTapProxy, type: CGEventType, event: CGEvent,
                 refcon: UnsafeMutableRawPointer?) -> Unmanaged<CGEvent>? {
    guard let refcon = refcon else { return Unmanaged.passUnretained(event) }
    return Unmanaged<HoldDetector>.fromOpaque(refcon).takeUnretainedValue().handle(type, event)
}

@MainActor
final class Agent {
    let detector = HoldDetector()
    let tablePath: String
    let converter: [String]
    let holdSeconds: Double
    var table: [String: [Option]] = [:]
    var tableDate: Date?
    var session: MenuSession?
    var generation = 0

    init(tablePath: String, converter: [String], holdSeconds: Double) {
        self.tablePath = tablePath
        self.converter = converter
        self.holdSeconds = holdSeconds
    }

    func loadTable() {
        let url = URL(fileURLWithPath: tablePath)
        let modified = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        if tableDate != nil && modified == tableDate { return }
        if let data = try? Data(contentsOf: url),
           let decoded = try? JSONDecoder().decode([String: [Option]].self, from: data) {
            table = decoded
            tableDate = modified
        }
    }

    func start() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)              // shows the permission prompt once
        let me = self
        detector.onDown = { p in Task { @MainActor in me.down(p) } }
        detector.onCancel = { Task { @MainActor in me.cancelHold() } }
        detector.onMove = { p in Task { @MainActor in me.session?.hover(p) } }
        detector.onRelease = { p in Task { @MainActor in me.release(p) } }
        detector.onClick = { p in Task { @MainActor in me.click(p) } }
        installTap()
    }

    func installTap() {
        let types: [CGEventType] = [.leftMouseDown, .leftMouseUp, .leftMouseDragged, .mouseMoved, .rightMouseDown]
        let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << CGEventMask($1.rawValue)) }
        guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                                          options: .defaultTap, eventsOfInterest: mask, callback: tapCallback,
                                          userInfo: Unmanaged.passUnretained(detector).toOpaque()) else {
            // No Accessibility permission yet: try again shortly.
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                self.installTap()
            }
            return
        }
        detector.tap = tap
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    var finderInFront: Bool {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.finder"
    }

    func cocoa(_ cg: CGPoint) -> NSPoint {
        NSPoint(x: cg.x, y: (NSScreen.screens.first?.frame.height ?? 0) - cg.y)
    }

    func down(_ p: CGPoint) {
        guard session == nil || session!.finished else { return }
        generation += 1
        let gen = generation
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 200_000_000)
            guard gen == self.generation, self.detector.pressing, self.finderInFront else { return }
            let s = MenuSession(rootCocoa: self.cocoa(p), legacy: false, interactive: false)
            s.charge(seconds: max(0.05, self.holdSeconds - 0.2))
            s.onClosed = { [weak s] in
                if let s = s, self.session === s { self.session = nil; self.detector.menuOpen = false }
            }
            s.onConvert = { [weak s] option in
                if let s = s { self.convert(option, files: s.files, session: s) }
            }
            self.session = s
            try? await Task.sleep(nanoseconds: UInt64(max(0.05, self.holdSeconds - 0.2) * 1_000_000_000))
            guard gen == self.generation, self.finderInFront, self.detector.beginCapture() else { return }
            self.loadTable()
            let files = self.finderSelection()
            let options = commonOptions(files: files, table: self.table)
            if options.isEmpty { s.cancel(); return }
            s.open(options, files: files)
        }
    }

    func cancelHold() {
        generation += 1
        if let s = session, s.model.openedAt == nil { s.cancel() }
    }

    func release(_ p: CGPoint) {
        guard let s = session else { return }
        if !s.release(p) && s.model.phase == .branches && s.model.cancelAt == nil {
            detector.menuOpen = true                             // stay open: click a format
        }
    }

    func click(_ p: CGPoint) {
        detector.menuOpen = false
        session?.click(p)
    }

    func finderSelection() -> [String] {
        let source = """
        tell application "Finder"
            set out to {}
            repeat with i in (get selection)
                try
                    set end of out to POSIX path of (i as alias)
                end try
            end repeat
            set AppleScript's text item delimiters to linefeed
            return out as text
        end tell
        """
        var error: NSDictionary?
        guard let text = NSAppleScript(source: source)?.executeAndReturnError(&error).stringValue else { return [] }
        return text.split(separator: "\n").map(String.init).filter { !$0.hasSuffix("/") }
    }

    func convert(_ option: Option, files: [String], session: MenuSession) {
        detector.menuOpen = false
        guard let exe = converter.first else { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exe)
        process.arguments = Array(converter.dropFirst()) + ["run", "--to", option.ext, "--report"] + files
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        let buffer = LineBuffer()
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty { handle.readabilityHandler = nil; return }
            for line in buffer.append(chunk) {
                Task { @MainActor in session.handle(line) }
            }
        }
        process.terminationHandler = { _ in
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 300_000_000)
                if session.model.phase == .working { session.handle("FAIL Stopped unexpectedly") }
            }
        }
        do { try process.run() } catch { session.handle("FAIL Couldn't start the converter") }
    }
}

/// Splits a byte stream into lines (used from the pipe's background queue only).
final class LineBuffer {
    private var data = Data()

    func append(_ chunk: Data) -> [String] {
        data.append(chunk)
        var lines = [String]()
        while let nl = data.firstIndex(of: 0x0A) {
            lines.append(String(decoding: data[data.startIndex..<nl], as: UTF8.self))
            data.removeSubrange(data.startIndex...nl)
        }
        return lines
    }
}

// MARK: - Entry point

@main
enum FileConvertUI {
    @MainActor
    static func main() {
        let args = CommandLine.arguments
        setvbuf(stdout, nil, _IOLBF, 0)
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)

        if args.count > 3 && args[1] == "--agent" {
            let holdMs = Double(ProcessInfo.processInfo.environment["FILECONVERT_HOLD_MS"] ?? "") ?? 550
            let agent = Agent(tablePath: args[2], converter: Array(args.dropFirst(3)), holdSeconds: holdMs / 1000)
            agent.start()
            withExtendedLifetime(agent) { app.run() }
            return
        }

        guard args.count > 1, let data = args[1].data(using: .utf8),
              let config = try? JSONDecoder().decode(Config.self, from: data),
              !config.options.isEmpty
        else {
            FileHandle.standardError.write("usage: FileConvertUI '<json>' | --agent <targets.json> <command...>\n".data(using: .utf8)!)
            exit(2)
        }
        let controller = PickController(config: config)
        controller.start()
        withExtendedLifetime(controller) { app.run() }
    }
}
