// What each file can become on iPhone/iPad, and the conversions themselves.
// Uses only Apple's frameworks (ImageIO, AVFoundation, UIKit), so there's nothing extra to install.
//
//   photos  -> JPG, PNG, HEIC, TIFF, GIF, BMP, PDF
//   videos  -> MP4, MOV, animated GIF, or just the sound as M4A / WAV
//   audio   -> M4A, WAV, AIFF

import AVFoundation
import ImageIO
import UIKit
import UniformTypeIdentifiers

struct Format: Hashable {
    let ext: String
    let label: String
    let caption: String

    init(_ ext: String, _ caption: String = "") {
        self.ext = ext
        self.label = ext.uppercased()
        self.caption = caption
    }
}

enum MediaKind { case image, video, audio }

enum ConvertError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let text): return text }
    }
}

enum Formats {
    static let aliases = ["jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "heif": "heic", "aif": "aiff", "m4v": "mp4"]

    static func canonical(_ ext: String) -> String { aliases[ext.lowercased()] ?? ext.lowercased() }

    static func kind(of url: URL) -> MediaKind? {
        guard let type = UTType(filenameExtension: url.pathExtension.lowercased()) else { return nil }
        if type.conforms(to: .image) { return .image }
        if type.conforms(to: .movie) || type.conforms(to: .video) { return .video }
        if type.conforms(to: .audio) { return .audio }
        return nil
    }

    static func targets(for url: URL) -> [Format] {
        let list: [Format]
        switch kind(of: url) {
        case .image: list = ["jpg", "png", "heic", "tiff", "gif", "bmp", "pdf"].map { Format($0) }
        case .video: list = [Format("mp4"), Format("mov"), Format("gif", "animated"), Format("m4a", "audio"), Format("wav", "audio")]
        case .audio: list = [Format("m4a"), Format("wav"), Format("aiff")]
        case nil: list = []
        }
        let own = canonical(url.pathExtension)
        return list.filter { $0.ext != own }
    }

    /// Formats that work for every selected file (a file already in that format is skipped).
    static func common(_ urls: [URL]) -> [Format] {
        let owns = urls.map { canonical($0.pathExtension) }
        let lists = urls.map { targets(for: $0) }
        if lists.isEmpty || lists.contains(where: { $0.isEmpty }) { return [] }
        var seen = Set<String>(), result = [Format]()
        for format in lists.flatMap({ $0 }) where !seen.contains(format.ext) {
            seen.insert(format.ext)
            let fits = zip(owns, lists).allSatisfy { own, list in own == format.ext || list.contains { $0.ext == format.ext } }
            if fits && owns.contains(where: { $0 != format.ext }) { result.append(format) }
        }
        return result
    }
}

enum Converter {
    static func outputURL(for src: URL, ext: String) -> URL {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("Converted", isDirectory: true)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder.appendingPathComponent(src.deletingPathExtension().lastPathComponent + "." + ext)
    }

    /// Convert one file. progress gets 0...1 (or nil when unknown) on any thread.
    static func run(_ src: URL, to ext: String, progress: @escaping (Double?) -> Void) async throws -> URL {
        let dst = outputURL(for: src, ext: ext)
        progress(nil)
        switch (Formats.kind(of: src), ext) {
        case (.image, _):
            try await background { try image(src, ext, dst) }
        case (.video, "gif"):
            try await background { try gif(src, dst, progress: progress) }
        case (_, "wav"), (_, "aiff"):
            try await background { try pcm(src, dst, aiff: ext == "aiff", progress: progress) }
        case (_, "m4a"):
            try await export(src, dst, preset: AVAssetExportPresetAppleM4A, type: .m4a, progress: progress)
        case (.video, "mp4"):
            try await export(src, dst, preset: AVAssetExportPresetHighestQuality, type: .mp4, progress: progress)
        case (.video, "mov"):
            try await export(src, dst, preset: AVAssetExportPresetHighestQuality, type: .mov, progress: progress)
        default:
            throw ConvertError.message("Can't make \(ext.uppercased()) from this file")
        }
        progress(1)
        return dst
    }

    static func background(_ work: @escaping () throws -> Void) async throws {
        try await Task.detached(priority: .userInitiated) { try work() }.value
    }

    // MARK: photos

    static func image(_ src: URL, _ ext: String, _ dst: URL) throws {
        guard let source = CGImageSourceCreateWithURL(src as CFURL, nil), CGImageSourceGetCount(source) > 0 else {
            throw ConvertError.message("This photo couldn't be opened")
        }
        let count = CGImageSourceGetCount(source)
        if ext == "pdf" {
            let pages: [UIImage] = count == 1
                ? [UIImage(contentsOfFile: src.path)].compactMap { $0 }
                : (0..<count).compactMap { CGImageSourceCreateImageAtIndex(source, $0, nil) }.map { UIImage(cgImage: $0) }
            guard let first = pages.first else { throw ConvertError.message("This photo couldn't be opened") }
            let renderer = UIGraphicsPDFRenderer(bounds: CGRect(origin: .zero, size: first.size))
            try renderer.writePDF(to: dst) { context in
                for page in pages {
                    let rect = CGRect(origin: .zero, size: page.size)
                    context.beginPage(withBounds: rect, pageInfo: [:])
                    page.draw(in: rect)
                }
            }
            return
        }
        let types: [String: UTType] = ["jpg": .jpeg, "png": .png, "heic": .heic, "tiff": .tiff, "gif": .gif, "bmp": .bmp]
        guard let type = types[ext] else { throw ConvertError.message("Can't make \(ext.uppercased())") }
        let frames = (ext == "gif" || ext == "tiff") ? count : 1
        guard let dest = CGImageDestinationCreateWithURL(dst as CFURL, type.identifier as CFString, frames, nil) else {
            throw ConvertError.message("This iPhone can't write \(ext.uppercased())")
        }
        for i in 0..<frames {
            var props: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: 0.92]
            if ext == "jpg" || ext == "bmp", let flat = flattened(source, i) {
                // no transparency in JPG/BMP: put it on white, keep the photo's rotation
                if let meta = CGImageSourceCopyPropertiesAtIndex(source, i, nil) as? [CFString: Any],
                   let orientation = meta[kCGImagePropertyOrientation] {
                    props[kCGImagePropertyOrientation] = orientation
                }
                CGImageDestinationAddImage(dest, flat, props as CFDictionary)
            } else {
                CGImageDestinationAddImageFromSource(dest, source, i, props as CFDictionary)
            }
        }
        guard CGImageDestinationFinalize(dest) else { throw ConvertError.message("Saving the \(ext.uppercased()) failed") }
    }

    static func flattened(_ source: CGImageSource, _ index: Int) -> CGImage? {
        guard let image = CGImageSourceCreateImageAtIndex(source, index, nil) else { return nil }
        switch image.alphaInfo {
        case .none, .noneSkipFirst, .noneSkipLast: return image
        default: break
        }
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                                  bytesPerRow: 0, space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
        else { return image }
        let rect = CGRect(x: 0, y: 0, width: image.width, height: image.height)
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(rect)
        ctx.draw(image, in: rect)
        return ctx.makeImage()
    }

    // MARK: video and audio

    static func export(_ src: URL, _ dst: URL, preset: String, type: AVFileType,
                       progress: @escaping (Double?) -> Void) async throws {
        let asset = AVURLAsset(url: src)
        guard let session = AVAssetExportSession(asset: asset, presetName: preset) else {
            throw ConvertError.message("This file can't be converted to \(dst.pathExtension.uppercased())")
        }
        session.outputURL = dst
        session.outputFileType = type
        session.shouldOptimizeForNetworkUse = true
        let ticker = Task.detached {
            while !Task.isCancelled {
                progress(Double(session.progress))
                try? await Task.sleep(nanoseconds: 150_000_000)
            }
        }
        await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
            session.exportAsynchronously { done.resume() }
        }
        ticker.cancel()
        if session.status != .completed {
            throw ConvertError.message(session.error?.localizedDescription ?? "The conversion didn't finish")
        }
    }

    static func pcm(_ src: URL, _ dst: URL, aiff: Bool, progress: @escaping (Double?) -> Void) throws {
        let asset = AVURLAsset(url: src)
        guard let track = asset.tracks(withMediaType: .audio).first else {
            throw ConvertError.message("There's no sound in this file")
        }
        let duration = CMTimeGetSeconds(asset.duration)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 44100, AVNumberOfChannelsKey: 2,
            AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: aiff,
            AVLinearPCMIsNonInterleaved: false,
        ]
        let reader = try AVAssetReader(asset: asset)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
        reader.add(output)
        let writer = try AVAssetWriter(outputURL: dst, fileType: aiff ? .aiff : .wav)
        let input = AVAssetWriterInput(mediaType: .audio, outputSettings: settings)
        input.expectsMediaDataInRealTime = false
        writer.add(input)
        guard reader.startReading(), writer.startWriting() else {
            throw ConvertError.message(reader.error?.localizedDescription ?? writer.error?.localizedDescription ?? "Couldn't read the sound")
        }
        writer.startSession(atSourceTime: .zero)
        while let buffer = output.copyNextSampleBuffer() {
            while !input.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.005) }
            if !input.append(buffer) {
                throw ConvertError.message(writer.error?.localizedDescription ?? "Writing the sound failed")
            }
            let t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(buffer))
            if duration > 0 { progress(min(1, t / duration)) }
        }
        input.markAsFinished()
        let finished = DispatchSemaphore(value: 0)
        writer.finishWriting { finished.signal() }
        finished.wait()
        if writer.status != .completed {
            throw ConvertError.message(writer.error?.localizedDescription ?? "Writing the sound failed")
        }
    }

    static func gif(_ src: URL, _ dst: URL, progress: @escaping (Double?) -> Void) throws {
        let asset = AVURLAsset(url: src)
        let seconds = min(CMTimeGetSeconds(asset.duration), 15)          // GIFs get huge: first 15 s
        let fps = 10.0
        let count = max(1, Int(seconds * fps))
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 480, height: 480)
        let tolerance = CMTime(seconds: 0.05, preferredTimescale: 600)
        generator.requestedTimeToleranceBefore = tolerance
        generator.requestedTimeToleranceAfter = tolerance
        guard let dest = CGImageDestinationCreateWithURL(dst as CFURL, UTType.gif.identifier as CFString, count, nil) else {
            throw ConvertError.message("Couldn't create the GIF")
        }
        CGImageDestinationSetProperties(dest, [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary)
        let frameProps = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: 1 / fps]] as CFDictionary
        var last: CGImage?
        for i in 0..<count {
            let time = CMTime(seconds: Double(i) / fps, preferredTimescale: 600)
            if let frame = try? generator.copyCGImage(at: time, actualTime: nil) { last = frame }
            guard let frame = last else { continue }
            CGImageDestinationAddImage(dest, frame, frameProps)
            progress(Double(i + 1) / Double(count))
        }
        guard last != nil, CGImageDestinationFinalize(dest) else { throw ConvertError.message("Couldn't read the video") }
    }
}
