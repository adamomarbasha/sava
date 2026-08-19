import SwiftUI

@main
struct SavaApp: App {
    init() {
        #if DEBUG
        CaptureDiagnostics.runSelectorSelfCheck()
        CaptureDiagnostics.runAuthLifetimeSelfCheck()
        #endif
    }

    @StateObject private var session = SessionStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .tint(SavaColors.accent)
        }
    }
}
