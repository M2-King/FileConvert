// The glass branch menu on iPhone/iPad: the file sits on a blurred copy of itself with a mirror
// reflection, curvy glass branches grow out of it (one per format), and you slide onto a format
// and let go (or tap it). Then a glass pill shows progress, a check mark, and "Save to Files".
// Same layout and timing as the desktop versions (fileconvert/tree.py, FileConvertUI.swift).

import QuickLookThumbnailing
import SwiftUI
import UIKit

// MARK: - Easing

func clamp01(_ x: Double) -> Double { min(1, max(0, x)) }
func easeOut(_ t: Double) -> Double { 1 - pow(1 - clamp01(t), 3) }
func easeInOut(_ t: Double) -> Double { let t = clamp01(t); return t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2 }
func springCurve(_ t: Double, _ response: Double, _ damping: Double) -> Double {
    if t <= 0 { return 0 }
    let w0 = 2 * Double.pi / response, wd = w0 * sqrt(1 - damping * damping)
    return 1 - exp(-damping * w0 * t) * (cos(wd * t) + damping * w0 / wd * sin(wd * t))
}

// MARK: - Layout

struct Leaf {
    let format: Format
    let tip: CGPoint
    let path: [CGPoint]
}

struct Room { var left: CGFloat, right: CGFloat, up: CGFloat, down: CGFloat }

enum Layout {
    static let labelW: CGFloat = 58, labelH: CGFloat = 30, startR: CGFloat = 46

    static func fanArc(_ room: Room, _ n: Int, reach: CGFloat) -> (Double, Double) {
        let want = n < 2 ? 0 : min(168.0, 27.0 * Double(n - 1))
        func fits(_ deg: Double) -> Bool {
            let tx = CGFloat(cos(deg * .pi / 180)) * (reach + labelW * 0.42)
            let ty = CGFloat(sin(deg * .pi / 180)) * (reach + labelH * 0.75)
            return labelW / 2 - tx <= room.left - 6 && tx + labelW / 2 <= room.right - 6
                && labelH / 2 - ty <= room.up - 6 && ty + labelH / 2 + 22 <= room.down - 6
        }
        let ok = stride(from: -180, to: 180, by: 3).map { fits(Double($0)) }
        if !ok.contains(false) { return (0, want) }
        var best = (0, 0), start: Int? = nil
        for (i, good) in (ok + ok).enumerated() {
            if good {
                if start == nil { start = i }
                let length = i - start! + 1
                if length > best.1 && length <= ok.count { best = (start!, length) }
            } else { start = nil }
        }
        if best.1 == 0 { return (0, want) }
        let lo = -180.0 + Double(best.0 * 3), width = Double((best.1 - 1) * 3)
        let centre = width >= want ? min(max(0, lo + want / 2), lo + width - want / 2) : lo + width / 2
        return (centre, min(want, width))
    }

    static func make(_ formats: [Format], room: Room) -> [Leaf] {
        let n = formats.count
        // shorter branches on small screens
        let long: CGFloat = min(196, max(120, room.right - labelW - 20))
        let short: CGFloat = max(104, long * 0.66)
        let reach = n > 4 ? long : (long + short) / 2
        let (centre, span) = fanArc(room, n, reach: reach)
        return formats.enumerated().map { i, format in
            let a = (centre - span / 2 + (n > 1 ? span * Double(i) / Double(n - 1) : 0)) * .pi / 180
            let r: CGFloat = n <= 4 ? reach : (i % 2 == 0 ? short : long)
            let d = CGPoint(x: cos(a), y: sin(a)), p = CGPoint(x: -d.y, y: d.x)
            let s = CGPoint(x: d.x * startR, y: d.y * startR), e = CGPoint(x: d.x * r, y: d.y * r)
            let bend = CGFloat(i % 2 == 1 ? 1 : -1) * 0.20 * r
            let c1 = CGPoint(x: s.x + (e.x - s.x) * 0.35 + p.x * bend, y: s.y + (e.y - s.y) * 0.35 + p.y * bend)
            let c2 = CGPoint(x: s.x + (e.x - s.x) * 0.72 - p.x * bend * 0.45, y: s.y + (e.y - s.y) * 0.72 - p.y * bend * 0.45)
            let tip = CGPoint(x: d.x * (r + labelW * 0.42), y: d.y * (r + labelH * 0.75))
            return Leaf(format: format, tip: tip, path: [s, c1, c2, e])
        }
    }

    /// Fingers are bigger than mouse pointers: a generous touch area around each label.
    static func hit(_ leaves: [Leaf], _ p: CGPoint) -> Int? {
        var best: (CGFloat, Int)? = nil
        for (i, leaf) in leaves.enumerated()
        where abs(p.x - leaf.tip.x) <= labelW / 2 + 12 && abs(p.y - leaf.tip.y) <= labelH / 2 + 12 {
            let dist = hypot(p.x - leaf.tip.x, p.y - leaf.tip.y)
            if best == nil || dist < best!.0 { best = (dist, i) }
        }
        return best?.1
    }
}

// MARK: - Glass

struct GlassBackground: ViewModifier {
    let radius: CGFloat

    func body(content: Content) -> some View {
        GlassBackground.apply(content, radius)
    }

    static func apply(_ content: Content, _ r: CGFloat) -> AnyView {
        let shape = RoundedRectangle(cornerRadius: r, style: .continuous)
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            return AnyView(content.glassEffect(.regular.interactive(), in: shape))
        }
        #endif
        return AnyView(
            content
                .background(.ultraThinMaterial, in: shape)
                .overlay(shape.strokeBorder(
                    LinearGradient(colors: [Color.white.opacity(0.7), Color.white.opacity(0.1)],
                                   startPoint: .top, endPoint: .bottom), lineWidth: 1))
                .shadow(color: Color.black.opacity(0.25), radius: 8, x: 0, y: 4))
    }
}

// MARK: - State

@MainActor
final class ConvertFlow: ObservableObject {
    enum Phase { case branches, working, done, failed }

    let files: [URL]
    let formats: [Format]
    @Published var phase: Phase = .branches
    @Published var leaves: [Leaf] = []
    @Published var hover: Int?
    @Published var chosen: Int?
    @Published var progress: Double?
    @Published var status = ""
    @Published var message = ""
    @Published var outputs: [URL] = []
    @Published var shake: CGFloat = 0
    var openedAt: Date?
    var choseAt: Date?
    private let ticks = UISelectionFeedbackGenerator()

    init(files: [URL]) {
        self.files = files
        self.formats = Formats.common(files)
    }

    var chosenLabel: String { chosen.map { leaves[$0].format.label } ?? "" }

    func grow(root: CGPoint, size: CGSize) {
        guard leaves.isEmpty, !formats.isEmpty else { return }
        let room = Room(left: root.x, right: size.width - root.x, up: root.y - 70, down: size.height - root.y - 90)
        leaves = Layout.make(formats, room: room)
        openedAt = Date()
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }

    func setHover(_ i: Int?) {
        guard choseAt == nil, i != hover else { return }
        hover = i
        if i != nil { ticks.selectionChanged() }
    }

    func leaf(_ i: Int, root: CGPoint, now: Date) -> (grow: Double, pop: Double, position: CGPoint) {
        let tip = CGPoint(x: root.x + leaves[i].tip.x, y: root.y + leaves[i].tip.y)
        guard let open = openedAt else { return (0, 0, tip) }
        let ot = now.timeIntervalSince(open), b0 = 0.05 + Double(i) * 0.035
        var grow = easeOut((ot - b0) / 0.28)
        var pop = springCurve(ot - b0 - 0.15, 0.40, 0.60)
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
        return (grow, max(0, pop), pos)
    }

    func choose(_ i: Int) {
        guard choseAt == nil, leaves.indices.contains(i) else { return }
        chosen = i
        hover = i
        choseAt = Date()
        UIImpactFeedbackGenerator(style: .rigid).impactOccurred()
        let format = leaves[i].format
        Task {
            try? await Task.sleep(nanoseconds: 450_000_000)
            withAnimation(.spring(response: 0.45, dampingFraction: 0.78)) { self.phase = .working }
            await self.convert(to: format)
        }
    }

    func convert(to format: Format) async {
        let todo = files.filter { Formats.canonical($0.pathExtension) != format.ext }
        let n = Double(max(1, todo.count))
        for (k, file) in todo.enumerated() {
            status = todo.count == 1 ? file.lastPathComponent : "\(k + 1) of \(todo.count)  ·  \(file.lastPathComponent)"
            do {
                let out = try await Converter.run(file, to: format.ext) { @Sendable p in
                    Task { @MainActor in self.progress = p.map { (Double(k) + $0) / n } }
                }
                outputs.append(out)
            } catch {
                message = "\(file.lastPathComponent): \(error.localizedDescription)"
                withAnimation(.spring(response: 0.45, dampingFraction: 0.7)) { phase = .failed }
                withAnimation(.linear(duration: 0.5)) { shake += 1 }
                UINotificationFeedbackGenerator().notificationOccurred(.error)
                return
            }
        }
        message = outputs.count == 1 ? "Converted to \(outputs[0].lastPathComponent)" : "Converted \(outputs.count) files"
        withAnimation(.spring(response: 0.45, dampingFraction: 0.7)) {
            progress = 1
            phase = .done
        }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }
}

// MARK: - Pieces

final class Thumbnail: ObservableObject {
    @Published var image: UIImage?

    func load(_ url: URL?) {
        guard let url = url, image == nil else { return }
        let request = QLThumbnailGenerator.Request(fileAt: url, size: CGSize(width: 600, height: 600),
                                                   scale: 2, representationTypes: .all)
        QLThumbnailGenerator.shared.generateBestRepresentation(for: request) { rep, _ in
            let image = rep?.uiImage
            DispatchQueue.main.async { self.image = image }
        }
    }
}

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
    let format: Format
    let lit: Bool

    var height: CGFloat { format.caption.isEmpty ? 30 : 38 }

    func text(_ white: Bool) -> some View {
        VStack(spacing: 0) {
            Text(format.label).font(.system(size: 13, weight: .semibold, design: .rounded))
            if !format.caption.isEmpty {
                Text(format.caption).font(.system(size: 9, weight: .medium)).opacity(0.75)
            }
        }
        .foregroundColor(white ? .white : .primary)
        .frame(width: Layout.labelW, height: height)
    }

    var body: some View {
        text(lit)
            .background(Capsule().fill(lit ? Color.accentColor : Color.clear))
            .modifier(GlassBackground(radius: height / 2))
            .overlay(alignment: .bottom) {
                text(lit)                                  // mirror reflection
                    .background(Capsule().fill(lit ? Color.accentColor : Color.white.opacity(0.5)))
                    .mask(LinearGradient(colors: [.clear, .white], startPoint: .top, endPoint: .bottom))
                    .opacity(0.3)
                    .scaleEffect(x: 1, y: -0.55, anchor: .bottom)
                    .offset(y: 2)
            }
    }
}

struct FileTile: View {
    let image: UIImage?
    let count: Int

    var tile: some View {
        ZStack {
            if let image = image {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                LinearGradient(colors: [Color(red: 0.55, green: 0.35, blue: 0.95), Color(red: 0.25, green: 0.5, blue: 1)],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
                Image(systemName: "doc.fill").font(.system(size: 28)).foregroundColor(.white)
            }
        }
        .frame(width: 80, height: 80)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    var body: some View {
        tile
            .shadow(color: .black.opacity(0.35), radius: 10, x: 0, y: 6)
            .overlay(alignment: .topTrailing) {
                if count > 1 {
                    Text("\(count)").font(.system(size: 12, weight: .bold, design: .rounded)).foregroundColor(.white)
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Capsule().fill(Color.accentColor)).offset(x: 8, y: -8)
                }
            }
            .overlay(alignment: .bottom) {
                tile                                       // reflection on the glass floor
                    .mask(LinearGradient(colors: [.clear, .white], startPoint: .top, endPoint: .bottom))
                    .opacity(0.28)
                    .scaleEffect(x: 1, y: -0.6, anchor: .bottom)
                    .offset(y: 4)
                    .allowsHitTesting(false)
            }
    }
}

let ringGradient = AngularGradient(
    gradient: Gradient(colors: [
        Color(red: 0.39, green: 0.82, blue: 1.00), Color(red: 0.04, green: 0.52, blue: 1.00),
        Color(red: 0.75, green: 0.35, blue: 0.95), Color(red: 1.00, green: 0.22, blue: 0.37),
        Color(red: 0.39, green: 0.82, blue: 1.00),
    ]),
    center: .center)

struct Spinner: View {
    @State private var spin = false

    var body: some View {
        Circle().trim(from: 0, to: 0.3)
            .stroke(ringGradient, style: StrokeStyle(lineWidth: 3.6, lineCap: .round))
            .rotationEffect(.degrees(spin ? 360 : 0))
            .animation(.linear(duration: 0.85).repeatForever(autoreverses: false), value: spin)
            .onAppear { spin = true }
    }
}

struct MarkShape: Shape {
    let check: Bool

    func path(in r: CGRect) -> Path {
        func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: r.minX + r.width * x, y: r.minY + r.height * y) }
        var p = Path()
        if check {
            p.move(to: pt(0.29, 0.52)); p.addLine(to: pt(0.44, 0.67)); p.addLine(to: pt(0.72, 0.36))
        } else {
            p.move(to: pt(0.35, 0.35)); p.addLine(to: pt(0.65, 0.65))
            p.move(to: pt(0.65, 0.35)); p.addLine(to: pt(0.35, 0.65))
        }
        return p
    }
}

struct Badge: View {
    let check: Bool
    @State private var drawn: CGFloat = 0

    var body: some View {
        ZStack {
            Circle().fill(check ? Color(red: 0.2, green: 0.78, blue: 0.35) : Color(red: 1, green: 0.27, blue: 0.23))
            MarkShape(check: check).trim(from: 0, to: drawn)
                .stroke(Color.white, style: StrokeStyle(lineWidth: 3.4, lineCap: .round, lineJoin: .round))
        }
        .onAppear { withAnimation(.easeOut(duration: 0.34).delay(0.12)) { drawn = 1 } }
    }
}

struct Shake: GeometryEffect {
    var animatableData: CGFloat

    func effectValue(size: CGSize) -> ProjectionTransform {
        ProjectionTransform(CGAffineTransform(translationX: 8 * sin(animatableData * .pi * 6), y: 0))
    }
}

struct ProgressPill: View {
    @ObservedObject var flow: ConvertFlow
    let onSave: () -> Void
    let onClose: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            HStack(spacing: 13) {
                ZStack {
                    switch flow.phase {
                    case .done: Badge(check: true).transition(.scale(scale: 0.3).combined(with: .opacity))
                    case .failed: Badge(check: false).transition(.scale(scale: 0.3).combined(with: .opacity))
                    default:
                        Circle().stroke(Color.primary.opacity(0.13), lineWidth: 3.6)
                        if let p = flow.progress {
                            Circle().trim(from: 0, to: max(0.015, p))
                                .stroke(ringGradient, style: StrokeStyle(lineWidth: 3.6, lineCap: .round))
                                .rotationEffect(.degrees(-90))
                                .animation(.easeOut(duration: 0.3), value: p)
                        } else {
                            Spinner()
                        }
                    }
                }
                .frame(width: 36, height: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text(headline).font(.system(size: 14, weight: .semibold)).lineLimit(1).truncationMode(.middle)
                    Text(subline).font(.system(size: 12)).foregroundColor(.secondary).lineLimit(2)
                }
                Spacer(minLength: 4)
                if flow.phase == .working, let p = flow.progress {
                    Text("\(Int((p * 100).rounded()))%")
                        .font(.system(size: 13, weight: .semibold, design: .rounded).monospacedDigit())
                        .foregroundColor(.secondary)
                }
            }
            if flow.phase == .done {
                HStack(spacing: 10) {
                    Button(action: onSave) {
                        Label("Save to Files", systemImage: "folder").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    ShareLink(items: flow.outputs) {
                        Label("Share", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
                .controlSize(.large)
                .transition(.opacity.combined(with: .move(edge: .top)))
            } else if flow.phase == .failed {
                Button("Close", action: onClose).buttonStyle(.bordered).controlSize(.large)
            }
        }
        .padding(16)
        .frame(width: 330)
        .modifier(GlassBackground(radius: 28))
        .modifier(Shake(animatableData: flow.shake))
    }

    var headline: String {
        switch flow.phase {
        case .done: return "Done"
        case .failed: return "Couldn't convert"
        default: return "Converting to \(flow.chosenLabel)"
        }
    }

    var subline: String {
        switch flow.phase {
        case .done, .failed: return flow.message
        default: return flow.status
        }
    }
}

// MARK: - The screen

struct ConvertScreen: View {
    @StateObject private var flow: ConvertFlow
    @StateObject private var thumb = Thumbnail()
    @State private var saving = false
    let onClose: () -> Void

    init(files: [URL], onClose: @escaping () -> Void) {
        _flow = StateObject(wrappedValue: ConvertFlow(files: files))
        self.onClose = onClose
    }

    var body: some View {
        GeometryReader { geo in
            let root = CGPoint(x: max(74, geo.size.width * 0.21), y: geo.size.height * 0.5)
            ZStack {
                backdrop(geo.size)
                if flow.formats.isEmpty {
                    VStack(spacing: 16) {
                        Text("This kind of file can't be converted on iPhone yet.")
                            .font(.headline).multilineTextAlignment(.center).foregroundColor(.white)
                        Button("Close", action: onClose).buttonStyle(.borderedProminent)
                    }
                    .padding(32)
                } else {
                    FileTile(image: thumb.image, count: flow.files.count)
                        .scaleEffect(flow.phase == .branches ? 1 : 0.8)
                        .opacity(flow.phase == .branches ? 1 : 0)
                        .position(root)
                    if flow.phase == .branches {
                        branches(root: root)
                    } else {
                        ProgressPill(flow: flow, onSave: { saving = true }, onClose: onClose)
                            .position(x: geo.size.width / 2, y: root.y)
                            .transition(.scale(scale: 0.6).combined(with: .opacity))
                    }
                }
            }
            .contentShape(Rectangle())
            .gesture(
                drag(root: root),
                including: flow.phase == .branches ? .all : .subviews)    // buttons work after picking
            .overlay(alignment: .top) {
                if flow.phase == .branches && !flow.formats.isEmpty {
                    Text(flow.files.count == 1 ? flow.files[0].lastPathComponent : "\(flow.files.count) files")
                        .font(.system(size: 15, weight: .semibold)).foregroundColor(.white).lineLimit(1)
                        .padding(.horizontal, 16).padding(.vertical, 8)
                        .modifier(GlassBackground(radius: 18))
                        .padding(.top, geo.safeAreaInsets.top + 16)
                }
            }
            .overlay(alignment: .bottom) {
                if flow.phase == .branches && !flow.formats.isEmpty {
                    Text("Slide to a format and let go, or tap one")
                        .font(.system(size: 13)).foregroundColor(.white.opacity(0.85))
                        .padding(.bottom, geo.safeAreaInsets.bottom + 24)
                }
            }
            .overlay(alignment: .topTrailing) {
                if flow.phase != .working {
                    Button(action: onClose) {
                        Image(systemName: "xmark").font(.system(size: 15, weight: .semibold)).foregroundColor(.white)
                            .frame(width: 38, height: 38).modifier(GlassBackground(radius: 19))
                    }
                    .padding(.top, geo.safeAreaInsets.top + 12).padding(.trailing, 16)
                }
            }
            .onAppear {
                thumb.load(flow.files.first)
                flow.grow(root: root, size: geo.size)
            }
        }
        .ignoresSafeArea()
        .fileMover(isPresented: $saving, files: flow.outputs) { result in
            if case .success = result { onClose() }
        }
    }

    func drag(root: CGPoint) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard flow.phase == .branches else { return }
                let p = CGPoint(x: value.location.x - root.x, y: value.location.y - root.y)
                flow.setHover(Layout.hit(flow.leaves, p))
            }
            .onEnded { value in
                guard flow.phase == .branches else { return }
                let p = CGPoint(x: value.location.x - root.x, y: value.location.y - root.y)
                if let i = Layout.hit(flow.leaves, p) { flow.choose(i) } else { flow.setHover(nil) }
            }
    }

    func backdrop(_ size: CGSize) -> some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.16, green: 0.12, blue: 0.32), Color(red: 0.05, green: 0.2, blue: 0.3)],
                           startPoint: .top, endPoint: .bottom)
            if let image = thumb.image {
                Image(uiImage: image).resizable().scaledToFill()
                    .frame(width: size.width, height: size.height).clipped()
                    .blur(radius: 40).opacity(0.85)
            }
            Color.black.opacity(0.28)
        }
        .frame(width: size.width, height: size.height)
    }

    func branches(root: CGPoint) -> some View {
        TimelineView(.animation) { context in
            ZStack {
                ForEach(Array(flow.leaves.indices), id: \.self) { i in
                    BranchShape(points: flow.leaves[i].path, root: root)
                        .trim(from: 0, to: flow.leaf(i, root: root, now: context.date).grow)
                        .stroke(i == flow.hover ? Color.accentColor : Color.white,
                                style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        .shadow(color: (i == flow.hover ? Color.accentColor : Color.white).opacity(0.7), radius: 4)
                }
                ForEach(Array(flow.leaves.indices), id: \.self) { i in
                    LeafLabel(format: flow.leaves[i].format, lit: i == flow.hover)
                        .scaleEffect((i == flow.hover ? 1.2 : 1.0) * flow.leaf(i, root: root, now: context.date).pop)
                        .animation(.spring(response: 0.28, dampingFraction: 0.6), value: flow.hover)
                        .opacity(min(1, flow.leaf(i, root: root, now: context.date).pop * 1.4))
                        .position(flow.leaf(i, root: root, now: context.date).position)
                }
            }
            .allowsHitTesting(false)
        }
    }
}
