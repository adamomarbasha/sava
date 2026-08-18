import SwiftUI

@main
struct SavaApp: App {
    @StateObject private var session = SessionStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .tint(SavaColors.accent)
        }
    }
}
