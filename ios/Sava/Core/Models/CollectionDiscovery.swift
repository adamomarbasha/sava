import Foundation

/// What a run of "Look for new groupings" actually did.
///
/// The old call returned raw `Data` and the caller discarded it with
/// `_ = try? await …`, so there was nothing to render and nothing to report:
/// success, "found nothing", and a server error were indistinguishable, and all
/// three looked like the button doing nothing.
struct CollectionDiscovery: Decodable {
    /// `ok`, `empty_library`, `not_enough_content`.
    let status: String
    let savesConsidered: Int
    /// Groupings the algorithm proposed before reconciliation.
    let proposed: Int
    let created: Int
    let updated: Int
    let removed: Int
    /// The minimum number of saves a grouping can be made from.
    let minimum: Int?
    let collections: [Group]

    struct Group: Decodable, Identifiable {
        let id: Int
        let name: String
        let size: Int
    }

    enum CodingKeys: String, CodingKey {
        case status, proposed, created, updated, removed, minimum, collections
        case savesConsidered = "saves_considered"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = (try? c.decode(String.self, forKey: .status)) ?? "ok"
        savesConsidered = (try? c.decode(Int.self, forKey: .savesConsidered)) ?? 0
        proposed = (try? c.decode(Int.self, forKey: .proposed)) ?? 0
        created = (try? c.decode(Int.self, forKey: .created)) ?? 0
        updated = (try? c.decode(Int.self, forKey: .updated)) ?? 0
        removed = (try? c.decode(Int.self, forKey: .removed)) ?? 0
        minimum = try? c.decode(Int.self, forKey: .minimum)
        collections = (try? c.decode([Group].self, forKey: .collections)) ?? []
    }

    /// Nothing moved. Not a failure — the library simply has no new patterns in
    /// it, which is the honest and common answer.
    var foundNothingNew: Bool {
        status == "ok" && created == 0 && updated == 0 && removed == 0
    }
}

/// Where a discovery run is, from the user's point of view.
///
/// Named for what the user is told rather than for what the code is doing:
/// every case here is a sentence somebody reads, and the button must never sit
/// in a state that has no sentence.
enum DiscoveryPhase: Equatable {
    case idle
    case starting
    case analyzing
    case grouping
    case saving
    case complete(created: Int, updated: Int)
    case noNewGroups
    case notEnoughContent(minimum: Int)
    case failed(String)

    var isRunning: Bool {
        switch self {
        case .starting, .analyzing, .grouping, .saving: return true
        default: return false
        }
    }

    /// The line shown beside the indicator. Product language, not internals.
    var message: String? {
        switch self {
        case .idle: return nil
        case .starting:   return "Looking across your saves…"
        case .analyzing:  return "Looking across your saves…"
        case .grouping:   return "Finding things that belong together…"
        case .saving:     return "Saving what it found…"
        case .complete(let created, let updated):
            if created > 0 && updated > 0 {
                return "\(created) new \(created == 1 ? "group" : "groups"), "
                    + "\(updated) updated"
            }
            if created > 0 {
                return created == 1 ? "1 new group" : "\(created) new groups"
            }
            return updated == 1 ? "1 group updated" : "\(updated) groups updated"
        case .noNewGroups:
            return "No new groups yet"
        case .notEnoughContent:
            return "Save a few more things and Sava can find patterns"
        case .failed(let why):
            return why
        }
    }

    var isTerminal: Bool {
        switch self {
        case .complete, .noNewGroups, .notEnoughContent, .failed: return true
        default: return false
        }
    }

    var isFailure: Bool {
        if case .failed = self { return true }
        return false
    }
}
