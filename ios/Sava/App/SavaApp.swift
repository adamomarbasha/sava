import SwiftUI

@main
struct SavaApp: App {
    init() {
        SavaAppearance.apply()
        // So the share extension talks to the same backend this build does.
        AppConfig.publishOriginForExtension()
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
