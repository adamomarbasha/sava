import Foundation

/// Saves handed over by the share extension, waiting for the app to confirm them.
///
/// A share extension has seconds to live and no guarantee of network. The
/// requirement is that sharing *feels* instant and never loses anything, and
/// those two goals conflict unless the write and the upload are separated:
///
///   1. The extension writes the URL here and dismisses. This is a file write to
///      a shared container — sub-millisecond, works offline, works in airplane
///      mode, works when the API is down.
///   2. It then *tries* the network with a short budget. If that succeeds the
///      entry is removed and nothing more happens.
///   3. Anything still here when the app next opens is retried by the app,
///      which has no time limit and a real network stack.
///
/// The alternative — spin in the extension until the upload finishes — is how
/// share sheets end up feeling slow and how saves get lost when iOS terminates
/// the extension mid-request.
///
/// Storage is a JSON file in the App Group container rather than
/// `UserDefaults(suiteName:)`. Both are shared, but a queue is a list with
/// append and remove semantics, and defaults are a plist that must be rewritten
/// whole — with two processes writing, that is a lost-update race.
struct PendingSave: Codable, Identifiable, Equatable {
    let id: UUID
    let url: String
    let createdAt: Date
    /// How many times the app has tried and failed. Used to give up eventually
    /// rather than retry a permanently broken URL on every launch.
    var attempts: Int

    init(url: String, id: UUID = UUID(), createdAt: Date = Date(), attempts: Int = 0) {
        self.id = id
        self.url = url
        self.createdAt = createdAt
        self.attempts = attempts
    }
}

enum PendingSaveQueue {
    /// Must match the App Group in both targets' entitlements.
    static let appGroup = "group.com.sava.mobile"

    static let maxAttempts = 5

    private static let fileName = "pending-saves.json"

    private static var containerURL: URL? {
        FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: appGroup)
    }

    private static var fileURL: URL? {
        containerURL?.appendingPathComponent(fileName)
    }

    /// Serialises access within a process. Across processes the extension and
    /// the app are not usually live at once — and the app drains on foreground,
    /// by which point the extension is gone.
    private static let lock = NSLock()

    static func load() -> [PendingSave] {
        lock.lock(); defer { lock.unlock() }
        guard let fileURL, let data = try? Data(contentsOf: fileURL) else { return [] }
        return (try? JSONDecoder().decode([PendingSave].self, from: data)) ?? []
    }

    @discardableResult
    static func append(_ save: PendingSave) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard let fileURL else { return false }
        var all = (try? Data(contentsOf: fileURL))
            .flatMap { try? JSONDecoder().decode([PendingSave].self, from: $0) } ?? []
        // Same link shared twice in a row is a slip, not two saves.
        guard !all.contains(where: { $0.url == save.url }) else { return true }
        all.append(save)
        return write(all, to: fileURL)
    }

    static func remove(_ id: UUID) {
        mutate { $0.removeAll { $0.id == id } }
    }

    /// Record a failed attempt, dropping the entry once it is clearly hopeless.
    static func recordFailure(_ id: UUID) {
        mutate { queue in
            guard let index = queue.firstIndex(where: { $0.id == id }) else { return }
            queue[index].attempts += 1
            if queue[index].attempts >= maxAttempts {
                queue.remove(at: index)
            }
        }
    }

    static func clear() {
        mutate { $0.removeAll() }
    }

    private static func mutate(_ change: (inout [PendingSave]) -> Void) {
        lock.lock(); defer { lock.unlock() }
        guard let fileURL else { return }
        var all = (try? Data(contentsOf: fileURL))
            .flatMap { try? JSONDecoder().decode([PendingSave].self, from: $0) } ?? []
        change(&all)
        _ = write(all, to: fileURL)
    }

    private static func write(_ queue: [PendingSave], to url: URL) -> Bool {
        guard let data = try? JSONEncoder().encode(queue) else { return false }
        // Atomic: a share extension can be killed mid-write, and a truncated
        // JSON file would lose every queued save rather than one.
        do {
            try data.write(to: url, options: .atomic)
            return true
        } catch {
            return false
        }
    }

    /// True when the App Group is provisioned. Callers degrade rather than crash.
    static var isAvailable: Bool { containerURL != nil }
}
