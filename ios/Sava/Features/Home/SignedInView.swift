import SwiftUI

/// Placeholder home shown after authentication. It confirms a real session
/// (email comes from `GET /auth/me`) and previews what the library will hold.
/// This is intentionally minimal — the full bookmark library is the next phase.
struct SignedInView: View {
    let user: User
    @EnvironmentObject private var session: SessionStore

    var body: some View {
        ZStack {
            SavaColors.background.ignoresSafeArea()

            VStack(spacing: Spacing.lg) {
                Spacer()

                SavaMark(size: 72)

                VStack(spacing: Spacing.xs) {
                    Text("You're in")
                        .font(SavaFont.title)
                        .foregroundStyle(SavaColors.textPrimary)
                    Text(user.email)
                        .font(SavaFont.callout)
                        .foregroundStyle(SavaColors.textSecondary)
                }

                Text("Your library lives here. Saved videos from TikTok, YouTube, and Instagram will appear next.")
                    .font(SavaFont.footnote)
                    .foregroundStyle(SavaColors.textTertiary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Spacing.xl)

                Spacer()

                Button {
                    Haptics.press()
                    session.signOut()
                } label: {
                    Text("Sign out")
                        .font(SavaFont.headline)
                        .foregroundStyle(SavaColors.textPrimary)
                        .frame(maxWidth: .infinity)
                        .frame(height: 52)
                        .background(
                            RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                .fill(SavaColors.surfaceMuted)
                        )
                }
                .buttonStyle(.pressable)
                .padding(.horizontal, Spacing.lg)
                .padding(.bottom, Spacing.lg)
            }
        }
    }
}
