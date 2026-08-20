import SwiftUI

@main
struct SavaApp: App {
    init() {
        SavaAppearance.apply()
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
                .tint(SavaColor.primary)
        }
    }
}
