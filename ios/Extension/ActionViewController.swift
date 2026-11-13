// "Convert with FileConvert" in the Share sheet: long-press a file in Files (or Photos), tap Share,
// then this action. It copies the shared files and shows the glass branch menu.

import SwiftUI
import UIKit
import UniformTypeIdentifiers

final class ActionViewController: UIViewController {
    private let spinner = UIActivityIndicatorView(style: .large)

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        spinner.color = .white
        spinner.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(spinner)
        NSLayoutConstraint.activate([spinner.centerXAnchor.constraint(equalTo: view.centerXAnchor),
                                     spinner.centerYAnchor.constraint(equalTo: view.centerYAnchor)])
        spinner.startAnimating()
        loadSharedFiles { [weak self] files in self?.show(files) }
    }

    private func show(_ files: [URL]) {
        spinner.stopAnimating()
        let screen = ConvertScreen(files: files) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
        }
        let host = UIHostingController(rootView: screen)
        host.view.backgroundColor = .clear
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)
    }

    /// Shared files are only readable inside the callback, so each one is copied somewhere private.
    private func loadSharedFiles(_ done: @escaping ([URL]) -> Void) {
        let items = extensionContext?.inputItems as? [NSExtensionItem] ?? []
        let providers = items.flatMap { $0.attachments ?? [] }
        let group = DispatchGroup()
        let found = Found()
        for (index, provider) in providers.enumerated() {
            let types = provider.registeredTypeIdentifiers
            let media = types.first { id in
                guard let t = UTType(id) else { return false }
                return t.conforms(to: .image) || t.conforms(to: .movie) || t.conforms(to: .audio)
            }
            guard let type = media ?? types.first else { continue }
            group.enter()
            provider.loadFileRepresentation(forTypeIdentifier: type) { url, _ in
                defer { group.leave() }
                guard let url = url else { return }
                let folder = FileManager.default.temporaryDirectory
                    .appendingPathComponent("Incoming/\(UUID().uuidString)", isDirectory: true)
                try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                var name = url.lastPathComponent
                if URL(fileURLWithPath: name).pathExtension.isEmpty, let ext = UTType(type)?.preferredFilenameExtension {
                    name += "." + ext
                }
                let copy = folder.appendingPathComponent(name)
                if (try? FileManager.default.copyItem(at: url, to: copy)) != nil {
                    found.add(index, copy)
                }
            }
        }
        group.notify(queue: .main) {
            done(found.sorted())
        }
    }
}

/// Collects copied files from the background callbacks (thread-safe).
final class Found: @unchecked Sendable {
    private let lock = NSLock()
    private var items = [(Int, URL)]()

    func add(_ index: Int, _ url: URL) {
        lock.lock()
        items.append((index, url))
        lock.unlock()
    }

    func sorted() -> [URL] {
        lock.lock()
        defer { lock.unlock() }
        return items.sorted { $0.0 < $1.0 }.map { $0.1 }
    }
}
