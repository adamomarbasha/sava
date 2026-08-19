import AppIntents
import Foundation
import UIKit

/// Safely gets Shortcuts' temporary screenshot into Sava.
///
/// Shortcuts hands the intent an `IntentFile` backed by a file in
/// `.../WorkflowKit.BackgroundShortcutRunner/Image.png`. Reading that URL
/// directly can fail with
///
///     _INIssueSandboxExtensionWithTokenGeneratorBlock
///     Could not create sandbox read extension ... Operation not permitted
///
/// because the sandbox extension for the donor file is not always issued to
/// us. So: take the in-memory bytes if they are already there, otherwise open
/// the URL under a security-scoped access claim, and either way **copy into
/// Sava-owned storage immediately** while access is still valid. Everything
/// afterwards operates on our own copy.
///
/// The result is always validated by decoding it — a byte count alone does not
/// prove the handoff worked, and a truncated or empty PNG would otherwise
/// travel all the way to the server before failing.
enum ScreenshotTransport {

    struct Result {
        let data: Data
        let source: String          // "inline" | "security-scoped" | "direct-read"
        let byteCount: Int
        let pixelSize: CGSize
        var isValid: Bool { byteCount > 0 && pixelSize != .zero }
    }

    enum TransportError: LocalizedError {
        case noBytes(String)
        case notAnImage(Int)

        var errorDescription: String? {
            switch self {
            case .noBytes(let detail):
                return "Sava couldn't read the screenshot (\(detail))."
            case .notAnImage(let count):
                return "The screenshot didn't arrive intact (\(count) bytes, not a readable image)."
            }
        }
    }

    /// Pull the bytes out of an `IntentFile`, trying each supported route.
    static func materialize(_ file: IntentFile) throws -> Result {
        var attempts: [String] = []

        // 1. Bytes already resident. `IntentFile.data` is the documented
        //    accessor and usually carries the payload inline for small files.
        let inline = file.data
        if !inline.isEmpty, let size = pixelSize(of: inline) {
            log("inline bytes ok", inline.count)
            return Result(data: inline, source: "inline",
                          byteCount: inline.count, pixelSize: size)
        }
        attempts.append("inline=\(inline.count)B")

        // 2. Security-scoped read of the donor URL, copied out immediately.
        if let url = file.fileURL {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            do {
                let bytes = try Data(contentsOf: url, options: [.mappedIfSafe])
                if !bytes.isEmpty, let size = pixelSize(of: bytes) {
                    log("security-scoped read ok (scoped=\(scoped))", bytes.count)
                    // Copy is implicit: `bytes` is ours now, the donor file can go.
                    return Result(data: Data(bytes), source: "security-scoped",
                                  byteCount: bytes.count, pixelSize: size)
                }
                attempts.append("scoped=\(bytes.count)B")
            } catch {
                attempts.append("scoped-failed=\(error.localizedDescription)")
            }

            // 3. Plain read, in case the scope claim was unnecessary.
            if let bytes = try? Data(contentsOf: url), !bytes.isEmpty,
               let size = pixelSize(of: bytes) {
                log("direct read ok", bytes.count)
                return Result(data: Data(bytes), source: "direct-read",
                              byteCount: bytes.count, pixelSize: size)
            }
            attempts.append("direct-failed")
        } else {
            attempts.append("no-fileURL")
        }

        // Bytes arrived but do not decode — report that specifically rather
        // than shipping a broken payload to the server.
        if !inline.isEmpty {
            throw TransportError.notAnImage(inline.count)
        }
        throw TransportError.noBytes(attempts.joined(separator: ", "))
    }

    /// Decode just enough to confirm the payload is a real image and get its
    /// dimensions, without fully rasterising it.
    private static func pixelSize(of data: Data) -> CGSize? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil)
                as? [CFString: Any],
              let w = props[kCGImagePropertyPixelWidth] as? NSNumber,
              let h = props[kCGImagePropertyPixelHeight] as? NSNumber,
              w.intValue > 0, h.intValue > 0
        else { return nil }
        return CGSize(width: w.intValue, height: h.intValue)
    }

    private static func log(_ what: String, _ bytes: Int) {
        #if DEBUG
        NSLog("[Sava screenshot] %@ (%d KB)", what, bytes / 1024)
        #endif
    }
}
