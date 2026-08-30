import Foundation
import UIKit

/// Whether this iPhone has an Action Button, and what Sava may do about it.
///
/// ── There is no API for either question ─────────────────────────────────
///
/// iOS publishes **no** way to ask "does this device have an Action Button",
/// and **no** supported URL that opens Settings → Action Button. `App-prefs:`
/// deep links into Settings subpages are private, have been rejected in review,
/// and break between releases. `UIApplication.openSettingsURLString` is the
/// only supported entry point, and it opens *Sava's own* Settings page — from
/// which the user walks back out to the Settings root. That is the honest
/// ceiling, so that is what Sava does, with the path written out beside it.
///
/// ── Detection, and which way it fails ───────────────────────────────────
///
/// The model identifier is public (`uname`), and the Action Button's history is
/// simple: it arrived with iPhone 15 Pro — `iPhone16,1` — and every iPhone
/// generation since has shipped it across the whole line.
///
/// So the rule is `major >= 16`, and the important part is the direction it
/// fails in. An unknown identifier — a phone released after this build, an
/// unrecognised string, the Simulator — is treated as **supported**. A stale
/// allow-list that hides the feature on hardware that has it would be a silent,
/// permanent bug for every new iPhone; showing an extra setup card to somebody
/// on an iPhone 13 is a paragraph they can ignore. Only devices positively
/// identified as older are told the truth about their hardware.
enum ActionButtonSupport {

    /// Does this device have an Action Button?
    static var isAvailable: Bool { isAvailable(identifier: modelIdentifier) }

    /// Testable seam.
    static func isAvailable(identifier: String) -> Bool {
        guard identifier.hasPrefix("iPhone") else {
            // iPad, Simulator without a model hint, or something unrecognised.
            // Fail open — see the note above.
            return true
        }
        let digits = identifier.dropFirst("iPhone".count)
        guard let major = Int(digits.prefix(while: { $0.isNumber })), major > 0 else {
            return true
        }
        return major >= 16
    }

    /// e.g. `iPhone17,1`. On the Simulator the host's own identifier is not
    /// useful, so the value Xcode sets is preferred when present.
    static var modelIdentifier: String {
        if let simulated = ProcessInfo.processInfo
            .environment["SIMULATOR_MODEL_IDENTIFIER"], !simulated.isEmpty {
            return simulated
        }
        var info = utsname()
        uname(&info)
        return withUnsafePointer(to: &info.machine) { pointer in
            pointer.withMemoryRebound(to: CChar.self,
                                      capacity: MemoryLayout.size(ofValue: pointer)) {
                String(validatingUTF8: $0) ?? ""
            }
        }
    }

    /// The only Settings URL Apple supports. Opens Sava's own Settings page.
    ///
    /// Named to say what it actually does, so no call site can come to believe
    /// it lands on the Action Button screen.
    static var appSettingsURL: URL? { URL(string: UIApplication.openSettingsURLString) }

    /// The path the user has to walk once Settings opens. Rendered as a visual
    /// trail rather than a sentence, because that is what people follow.
    static let settingsPath = ["Settings", "Action Button", "Shortcut",
                               AppConfig.officialSaveShortcutName]
}
