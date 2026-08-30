import SwiftUI

/// The small, fixed library onboarding is allowed to show.
///
/// ── Why fixtures, and why drawn rather than shipped ─────────────────────
///
/// First run happens before there is anything to look at. Sava cannot show the
/// user their own library because they have not saved anything yet, and it must
/// not pretend to: an invented "recent save" is a lie the moment they reach the
/// real Library tab.
///
/// So this is openly a demonstration — believable content, clearly Sava's own,
/// and identical on every device and every launch. That last property is what
/// makes the search demo in Stage 3 a demonstration rather than a gamble: the
/// query resolves to a known item because the corpus is known.
///
/// The artwork is **drawn**, not bundled and not fetched:
///
///   * Fetching would make first run depend on the network and on a CDN, and
///     onboarding is exactly when a cold, slow, or captive connection is most
///     likely. A tour that shows grey boxes is worse than no tour.
///   * Bundling photographs would mean either licensing stock (which looks like
///     stock) or shipping megabytes of JPEG for four screens someone sees once.
///   * Screenshotting real TikToks would be copying other people's content and
///     other apps' interfaces, which is not ours to ship.
///
/// `PosterArt` composes each one from gradients and shapes at draw time. They
/// are a few hundred bytes of code, they scale to any size without artefacts,
/// they respond to the colour scheme, and they are unmistakably illustrations
/// rather than claims about real posts.
struct DemoItem: Identifiable, Equatable {
    let id: String
    let platform: Platform
    /// The label on the card — "TikTok", "Reel", "Short". Kept separate from
    /// `platform` so a YouTube Short can say "Short" without inventing a
    /// platform that the rest of the app does not have.
    let kindLabel: String
    let title: String
    let creator: String
    let poster: PosterArt.Scene
    /// Roughly how long, shown on the card. Cosmetic.
    let duration: String

    /// Everything the search demo is allowed to match against.
    var searchableText: String { "\(title) \(creator) \(kindLabel)" }
}

enum DemoLibrary {

    /// Ordered for the Stage 1 constellation: the eye should land on something
    /// warm and food-shaped first, then a face, then the quieter ones.
    static let all: [DemoItem] = [
        DemoItem(id: "demo.pasta", platform: .tiktok, kindLabel: "TikTok",
                 title: "Three-ingredient pasta, ten minutes",
                 creator: "cookwithme", poster: .pasta, duration: "0:41"),

        DemoItem(id: "demo.speed", platform: .tiktok, kindLabel: "TikTok",
                 title: "Speed was convinced it looked like GTA 5 but it's GTA 6",
                 creator: "clipsdaily", poster: .nightCreator, duration: "0:28"),

        DemoItem(id: "demo.berlin", platform: .instagram, kindLabel: "Reel",
                 title: "Berlin, in October",
                 creator: "faye.travels", poster: .cityDusk, duration: "0:33"),

        DemoItem(id: "demo.kyoto", platform: .instagram, kindLabel: "Reel",
                 title: "Five quiet places in Kyoto",
                 creator: "onthewayto", poster: .torii, duration: "1:02"),

        DemoItem(id: "demo.compilers", platform: .youtube, kindLabel: "YouTube",
                 title: "How compilers actually work",
                 creator: "handmade", poster: .codeDesk, duration: "18:24"),

        DemoItem(id: "demo.espresso", platform: .youtube, kindLabel: "Short",
                 title: "Why your espresso tastes sour",
                 creator: "the.bean", poster: .espresso, duration: "0:52"),

        DemoItem(id: "demo.slow", platform: .other, kindLabel: "Article",
                 title: "The case for slow software",
                 creator: "essays.dev", poster: .editorial, duration: "6 min read"),
    ]

    /// The item the Stage 3 demo is built to find, and the query that finds it.
    static var searchTarget: DemoItem { all[1] }
    static let searchQuery = "that Speed GTA clip"

    /// The answer Ask gives in the demo.
    ///
    /// Written out rather than generated. Stage 3 must never call the real Ask
    /// endpoint: it would spend the user's Ask allowance before they have
    /// agreed to anything, it would need a signed-in session and a warm
    /// backend, and it could answer differently — or fail — on the one screen
    /// whose entire job is to show what Ask does.
    static let askQuestion = "What was happening in this video?"
    /// Two lines, not three. On an iPhone 17 Pro the third line of this answer
    /// fell behind the pinned Continue button — and the answer is the payoff of
    /// the entire screen, so it is the last thing that may be cut.
    static let askAnswer = "He's reacting to the GTA 6 trailer and insisting "
        + "the graphics look like GTA 5."

    /// Matches the way the real library search behaves closely enough to teach
    /// it: every word must appear somewhere in the item's text.
    ///
    /// Deliberately not fuzzy. The demo types one fixed query, so cleverness
    /// here would buy nothing and could only make the outcome less certain.
    static func matches(_ query: String, in items: [DemoItem]) -> [DemoItem] {
        let words = query.lowercased()
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map(String.init)
            .filter { $0.count > 1 && !stopWords.contains($0) }
        guard !words.isEmpty else { return items }
        return items.filter { item in
            let haystack = item.searchableText.lowercased()
            return words.allSatisfy { haystack.contains($0) }
        }
    }

    /// "that Speed GTA clip" contains two words that carry meaning and two that
    /// do not. Dropping them is what lets the demo query read like something a
    /// person would actually type.
    private static let stopWords: Set<String> = [
        "that", "the", "this", "a", "an", "my", "clip", "video", "one", "of", "about",
    ]
}
