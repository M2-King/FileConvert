// The FileConvert app: explains the Share-sheet action and lets you pick files to convert here too.

import SwiftUI
import UniformTypeIdentifiers

@main
struct FileConvertApp: App {
    var body: some Scene {
        WindowGroup { HomeView() }
    }
}

struct Selection: Identifiable {
    let id = UUID()
    let files: [URL]
}

struct HomeView: View {
    @State private var importing = false
    @State private var selection: Selection?

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.98, green: 0.62, blue: 0.42), Color(red: 0.42, green: 0.3, blue: 0.85),
                                    Color(red: 0.1, green: 0.55, blue: 0.68)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
                .ignoresSafeArea()
            ScrollView {
                VStack(spacing: 22) {
                    Image(systemName: "arrow.triangle.2.circlepath")
                        .font(.system(size: 34, weight: .semibold)).foregroundColor(.white)
                        .frame(width: 76, height: 76).modifier(GlassBackground(radius: 22))
                        .padding(.top, 40)
                    Text("FileConvert").font(.system(size: 32, weight: .bold, design: .rounded)).foregroundColor(.white)

                    VStack(alignment: .leading, spacing: 14) {
                        Text("Convert from anywhere").font(.headline)
                        step(1, "In the Files app (or Photos), press and hold a file.")
                        step(2, "Tap Share, then \u{201C}Convert with FileConvert\u{201D}. If you don't see it, scroll the list of actions to the end, tap Edit Actions and add it.")
                        step(3, "Glass branches grow out of your file. Slide onto a format and let go.")
                        step(4, "Tap Save to Files or Share.")
                    }
                    .padding(20)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .modifier(GlassBackground(radius: 24))

                    Button {
                        importing = true
                    } label: {
                        Label("Choose files to convert", systemImage: "plus.circle.fill")
                            .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)

                    Text("Photos: JPG, PNG, HEIC, TIFF, GIF, BMP, PDF\nVideos: MP4, MOV, animated GIF, or just the sound (M4A, WAV)\nAudio: M4A, WAV, AIFF")
                        .font(.footnote).foregroundColor(.white.opacity(0.9)).multilineTextAlignment(.center)
                }
                .padding(.horizontal, 22)
                .padding(.bottom, 40)
            }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.image, .movie, .audio],
                      allowsMultipleSelection: true) { result in
            guard case .success(let urls) = result else { return }
            let copies = urls.compactMap(copyIn)
            if !copies.isEmpty { selection = Selection(files: copies) }
        }
        .fullScreenCover(item: $selection) { picked in
            ConvertScreen(files: picked.files) { selection = nil }
        }
    }

    func step(_ number: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(number)").font(.system(size: 13, weight: .bold, design: .rounded)).foregroundColor(.white)
                .frame(width: 24, height: 24).background(Circle().fill(Color.accentColor))
            Text(text).font(.subheadline)
        }
    }

    /// Files picked from other apps are only readable for a moment: work on a private copy.
    func copyIn(_ url: URL) -> URL? {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("Incoming/\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let copy = folder.appendingPathComponent(url.lastPathComponent)
        do { try FileManager.default.copyItem(at: url, to: copy) } catch { return nil }
        return copy
    }
}
