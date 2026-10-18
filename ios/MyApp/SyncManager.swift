import Foundation
import HealthKit
import Observation
import UIKit

@Observable
final class SyncManager {
    static let shared = SyncManager()

    private(set) var isPaired = Pairing.load() != nil
    private(set) var isSyncing = false
    private(set) var lastSyncDate: Date?
    private(set) var lastSyncMessage: String?
    private(set) var lastSyncFailed = false
    /// Shown on the "not paired" screen, e.g. after a 401 or an invalid QR code.
    private(set) var notice: String?
    private(set) var todaySteps: Double?
    private(set) var todayRestingHR: Double?

    private let health = HealthStore.shared
    private let defaults = UserDefaults.standard
    private var currentSync: Task<Void, Never>?
    private var observers: [HKSampleType: HKObserverQuery] = [:]

    private static let backfillDays = 30
    private static let regularDays = 3
    private static let batchSize = 500

    private enum Key {
        static let didBackfill = "sync.didBackfill"
        static let lastDate = "sync.lastDate"
        static let lastMessage = "sync.lastMessage"
        static let lastFailed = "sync.lastFailed"
    }

    private init() {
        lastSyncDate = defaults.object(forKey: Key.lastDate) as? Date
        lastSyncMessage = defaults.string(forKey: Key.lastMessage)
        lastSyncFailed = defaults.bool(forKey: Key.lastFailed)
    }

    // MARK: Pairing

    /// Handles a `pdcompanion://pair?...` link from the camera, the in-app scanner or `.onOpenURL`.
    func pair(with url: URL) async {
        let info: PairingInfo
        do {
            info = try Pairing.parse(url)
        } catch {
            notice = error.localizedDescription
            return
        }

        // Let any sync using the old pairing finish first, then start fresh with a 30-day backfill.
        await currentSync?.value
        Pairing.save(info)
        defaults.set(false, forKey: Key.didBackfill)
        setResult(date: nil, message: nil, failed: false)
        notice = nil
        isPaired = true

        do {
            try await health.requestAuthorization()
        } catch {
            setResult(date: .now, message: error.localizedDescription, failed: true)
            return
        }
        // Observers registered before authorization may have failed; make sure they're running.
        startBackgroundObservers()
        await sync()
    }

    func disconnect() {
        Pairing.clear()
        defaults.set(false, forKey: Key.didBackfill)
        setResult(date: nil, message: nil, failed: false)
        isPaired = false
        notice = nil
        todaySteps = nil
        todayRestingHR = nil
    }

    // MARK: Sync

    /// The single sync entry point. If a sync is already running, this waits for it
    /// instead of starting a second one.
    func sync() async {
        if let running = currentSync {
            await running.value
            return
        }
        let task = Task { await performSync() }
        currentSync = task
        await task.value
        currentSync = nil
    }

    private func performSync() async {
        guard let pairing = Pairing.load() else {
            isPaired = false
            return
        }
        isSyncing = true
        defer { isSyncing = false }

        // If the permission sheet was never shown (e.g. dismissed mid-pairing), show it when we can.
        if UIApplication.shared.applicationState == .active, await health.needsAuthorization() {
            try? await health.requestAuthorization()
            startBackgroundObservers()
        }

        let backfill = !defaults.bool(forKey: Key.didBackfill)
        do {
            let samples = try await health.dailySamples(days: backfill ? Self.backfillDays : Self.regularDays)
            updateTodayValues(from: samples)

            // An empty list is still sent: it marks the device as synced.
            var batches = stride(from: 0, to: samples.count, by: Self.batchSize).map {
                Array(samples[$0..<min($0 + Self.batchSize, samples.count)])
            }
            if batches.isEmpty { batches = [[]] }

            var rejected = 0
            for batch in batches {
                let response = try await APIClient.sendMetrics(batch, to: pairing)
                rejected += response.rejected.count
            }

            defaults.set(true, forKey: Key.didBackfill)
            setResult(date: .now, message: Self.summary(sent: samples.count - rejected, rejected: rejected), failed: false)
        } catch APIError.unauthorized {
            Pairing.clear()
            defaults.set(false, forKey: Key.didBackfill)
            setResult(date: nil, message: nil, failed: false)
            isPaired = false
            notice = APIError.unauthorized.localizedDescription
        } catch {
            setResult(date: .now, message: error.localizedDescription, failed: true)
        }
    }

    private static func summary(sent: Int, rejected: Int) -> String {
        var text = sent == 1 ? "Sent 1 value" : "Sent \(sent) values"
        if rejected > 0 {
            text += rejected == 1 ? " (1 looked wrong and was skipped)" : " (\(rejected) looked wrong and were skipped)"
        }
        return text
    }

    private func updateTodayValues(from samples: [MetricSample]) {
        let today = HealthStore.dayStamp(.now)
        todaySteps = samples.first { $0.type == "steps" && $0.recordedAt == today }?.value
        todayRestingHR = samples.first { $0.type == "resting_hr" && $0.recordedAt == today }?.value
    }

    /// Persisted so results of background syncs show up the next time the app opens.
    private func setResult(date: Date?, message: String?, failed: Bool) {
        lastSyncDate = date
        lastSyncMessage = message
        lastSyncFailed = failed
        defaults.set(date, forKey: Key.lastDate)
        defaults.set(message, forKey: Key.lastMessage)
        defaults.set(failed, forKey: Key.lastFailed)
    }

    // MARK: Background delivery

    /// Called at app launch (including background launches by HealthKit) and after authorization.
    /// Safe to call repeatedly: only missing observers are added.
    func startBackgroundObservers() {
        guard health.isAvailable else { return }
        for type in HealthStore.backgroundTypes where observers[type] == nil {
            let query = HKObserverQuery(sampleType: type, predicate: nil) { _, completionHandler, error in
                Task { @MainActor in
                    if error != nil {
                        // Usually means authorization isn't determined yet; it's re-added after pairing.
                        SyncManager.shared.removeObserver(for: type)
                    } else if SyncManager.shared.isPaired {
                        await SyncManager.shared.sync()
                    }
                    // Tell HealthKit we're done only after the upload has finished.
                    completionHandler()
                }
            }
            observers[type] = query
            health.store.execute(query)

            Task {
                try? await health.store.enableBackgroundDelivery(for: type, frequency: .hourly)
            }
        }
    }

    private func removeObserver(for type: HKSampleType) {
        if let query = observers.removeValue(forKey: type) {
            health.store.stop(query)
        }
    }
}
