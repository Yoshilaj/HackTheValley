import SwiftUI

@main struct MyApp: App {
    @Environment(\.scenePhase) private var scenePhase

    init() {
        // Registered at launch so HealthKit background deliveries are handled even when
        // the app is woken in the background without showing any UI.
        SyncManager.shared.startBackgroundObservers()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .onOpenURL { url in
                    Task { await SyncManager.shared.pair(with: url) }
                }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await SyncManager.shared.sync() }
            }
        }
    }
}
