import SwiftUI
import UIKit

/// The one place Sava overrides UIKit's defaults.
///
/// Almost all of this app is deliberately the system's — system controls, system
/// tab bar, system sheets — because that is what "native" means and what keeps it
/// out of the uncanny valley. The exception is the masthead.
///
/// A large title is the single most repeated piece of type in an iOS app: it is
/// the first thing on Library, Collections, Search and Ask. Setting it a little
/// larger and a little tighter than the stock face, and matching it to the
/// wordmark on the sign-in screen, is enough to make a Sava screenshot
/// recognisable — without a logo in the corner, a coloured bar, or any of the
/// decoration that would read as branding-by-sticker.
enum SavaAppearance {
    static func apply() {
        let standard = UINavigationBarAppearance()
        standard.configureWithTransparentBackground()
        standard.largeTitleTextAttributes = [
            .font: UIFont.systemFont(ofSize: 33, weight: .bold),
            // Tight, the way the wordmark is set. Stock large titles are looser.
            .kern: -0.9,
            .foregroundColor: UIColor.label,
        ]
        standard.titleTextAttributes = [
            .font: UIFont.systemFont(ofSize: 16, weight: .semibold),
            .kern: -0.2,
            .foregroundColor: UIColor.label,
        ]

        // Scrolled state keeps the system's material so content passing under the
        // bar still reads correctly; only the type is ours.
        let scrolled = UINavigationBarAppearance()
        scrolled.configureWithDefaultBackground()
        scrolled.largeTitleTextAttributes = standard.largeTitleTextAttributes
        scrolled.titleTextAttributes = standard.titleTextAttributes

        let bar = UINavigationBar.appearance()
        bar.standardAppearance = scrolled
        bar.scrollEdgeAppearance = standard
        bar.compactAppearance = scrolled
    }
}
