import SwiftUI

/// What just happened, said next to where it happened.
///
/// ── Why this exists ─────────────────────────────────────────────────────
///
/// Several actions in Sava were written as
///
///     Task {
///         _ = try? await service.doTheThing()
///         await reload()
///         Haptics.success()
///     }
///
/// which has three faults and no visible symptom. There is no acknowledgement
/// while it runs; the error is swallowed by `try?`; and the success haptic
/// fires *whether or not it worked*, so a failed action buzzes as though it
/// succeeded. That is worse than silence — it is the app telling the user
/// something untrue.
///
/// This is the one place those states are described, so a screen adds feedback
/// by naming what it is doing rather than by building another banner.
///
/// ── Deliberately small ──────────────────────────────────────────────────
///
/// Four cases, one row, no generic state machine. Anything that needs richer
/// states than these — Ask's streaming, Scroll's playback — has its own model
/// already, because those states are about *that* screen's subject rather than
/// about "an action is in flight".
enum ActionStatus: Equatable {
    /// Running. The string is what the user is told, in product language.
    case working(String)
    /// Finished, and worth confirming. Fades on its own.
    case success(String)
    /// Finished, nothing to celebrate — "No new groups yet". Fades.
    case info(String)
    /// Failed. Stays until acted on; carries a retry when one can help.
    case failure(String)

    var message: String {
        switch self {
        case .working(let m), .success(let m), .info(let m), .failure(let m):
            return m
        }
    }

    var isWorking: Bool {
        if case .working = self { return true }
        return false
    }

    var isFailure: Bool {
        if case .failure = self { return true }
        return false
    }

    /// Results are news and become clutter once read. A failure has something
    /// to do about it, so it waits.
    var autoDismisses: Bool {
        switch self {
        case .success, .info: return true
        case .working, .failure: return false
        }
    }
}

/// One line of status, inline.
///
/// Inline rather than a HUD or a toast: the answer is almost always about the
/// list or the card the user is looking at, and a floating overlay covers the
/// very thing they are waiting to see. Banners are reserved for the case where
/// the whole screen's content is what changed.
struct InlineStatus: View {
    let status: ActionStatus
    /// Shown only on a failure, and only when retrying can actually help.
    var onRetry: (() -> Void)? = nil

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: Space.m) {
            mark
            Text(status.message)
                .font(SavaType.callout)
                .foregroundStyle(status.isFailure ? SavaColor.primary : SavaColor.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .contentTransition(.opacity)
            Spacer(minLength: Space.s)
            if status.isFailure, let onRetry {
                Button("Try again", action: onRetry)
                    .font(SavaType.caption)
                    .foregroundStyle(SavaColor.onAccentTint)
                    .padding(.horizontal, Space.m)
                    .frame(minHeight: 34)
                    .background(SavaColor.accentTint, in: Capsule())
                    .buttonStyle(.plain)
            }
        }
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(SavaColor.surface,
                    in: RoundedRectangle(cornerRadius: Radius.control, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.control, style: .continuous)
            .strokeBorder(status.isFailure ? SavaColor.danger.opacity(0.4)
                                           : SavaColor.hairline, lineWidth: 0.5))
        .transition(.opacity.combined(with: .move(edge: .top)))
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: status)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(status.message)
        // So VoiceOver reads a status that appears without the user moving
        // focus to it — otherwise the only feedback is silent.
        .accessibilityAddTraits(status.isWorking ? .updatesFrequently : [])
    }

    @ViewBuilder private var mark: some View {
        switch status {
        case .working:
            ProgressView()
                .controlSize(.small)
                .tint(SavaColor.secondary)
                .frame(width: 16, height: 16)
        case .success:
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 16))
                // `accentTint`, not `accent`: the fill token inverts to ink in
                // light mode and a 16pt glyph would come out black.
                .foregroundStyle(SavaColor.accentTint)
                .frame(width: 16, height: 16)
        case .info:
            Image(systemName: "sparkle.magnifyingglass")
                .font(.system(size: 15))
                .foregroundStyle(SavaColor.tertiary)
                .frame(width: 16, height: 16)
        case .failure:
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 15))
                .foregroundStyle(SavaColor.danger)
                .frame(width: 16, height: 16)
        }
    }
}

/// Holds the current status and retires it when it has been read.
///
/// A view owns one of these as `@State`. The dismissal rule lives here so no
/// screen has to remember that successes fade and failures do not.
@MainActor
@Observable
final class ActionReporter {
    private(set) var status: ActionStatus?
    private var dismissal: Task<Void, Never>?

    /// How long a result stays before it becomes clutter.
    private let linger: Duration = .seconds(3)

    func report(_ status: ActionStatus) {
        dismissal?.cancel()
        self.status = status
        guard status.autoDismisses else { return }
        dismissal = Task { [weak self] in
            try? await Task.sleep(for: self?.linger ?? .seconds(3))
            guard !Task.isCancelled else { return }
            self?.status = nil
        }
    }

    func clear() {
        dismissal?.cancel()
        status = nil
    }

    /// Run an action, reporting all three of its states.
    ///
    /// The success haptic fires only on the success path — the bug this whole
    /// file exists to prevent was a `Haptics.success()` sitting after a
    /// swallowed error, buzzing on failure.
    func run(working: String, success: String,
             failure: @autoclosure @escaping () -> String = "Something went wrong.",
             action: @escaping () async throws -> Void) async {
        report(.working(working))
        do {
            try await action()
            report(.success(success))
            Haptics.success()
        } catch {
            report(.failure((error as? APIError)?.userMessage ?? failure()))
            Haptics.error()
        }
    }
}
