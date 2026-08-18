import SwiftUI

/// A consistent motion language. Physics-based springs for interaction,
/// tuned curves for transitions. Prefer these over ad-hoc durations.
enum SavaMotion {
    /// Fast tactile response for taps/toggles (~120–180ms perceived).
    static let tap = Animation.spring(response: 0.28, dampingFraction: 0.72)

    /// Standard element movement / appearance.
    static let standard = Animation.spring(response: 0.42, dampingFraction: 0.82)

    /// Softer, for larger surfaces and screen-level transitions.
    static let smooth = Animation.spring(response: 0.55, dampingFraction: 0.86)

    /// Snappy but bouncy — success confirmations.
    static let bounce = Animation.spring(response: 0.4, dampingFraction: 0.6)

    /// Gentle, continuous ambient motion (liquid background).
    static let ambient = Animation.easeInOut(duration: 6).repeatForever(autoreverses: true)

    /// Resolve an animation, collapsing to a near-instant fade when the user
    /// has Reduce Motion enabled.
    static func respectingReduceMotion(_ animation: Animation, reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: 0.12) : animation
    }
}
