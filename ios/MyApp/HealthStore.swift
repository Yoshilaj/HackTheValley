import Foundation
import HealthKit

/// A HealthKit quantity type mapped to one of our backend metric types.
struct QuantityMetric {
    let type: String
    let identifier: HKQuantityTypeIdentifier
    let unit: HKUnit
    /// Daily sum for cumulative types, daily average for discrete ones.
    let cumulative: Bool
    /// Multiplier applied before sending, e.g. 100 to send 97 instead of 0.97.
    var scale: Double = 1
    let decimals: Int

    var quantityType: HKQuantityType { HKQuantityType(identifier) }
}

enum HealthStoreError: LocalizedError {
    case unavailable
    case locked

    var errorDescription: String? {
        switch self {
        case .unavailable:
            return "Apple Health isn't available on this device."
        case .locked:
            return "Health data can't be read while your iPhone is locked. It will sync after you unlock it."
        }
    }
}

final class HealthStore {
    static let shared = HealthStore()

    let store = HKHealthStore()

    private static let perMinute = HKUnit.count().unitDivided(by: .minute())

    static let quantityMetrics: [QuantityMetric] = [
        QuantityMetric(type: "steps", identifier: .stepCount, unit: .count(), cumulative: true, decimals: 0),
        QuantityMetric(type: "distance_m", identifier: .distanceWalkingRunning, unit: .meter(), cumulative: true, decimals: 0),
        QuantityMetric(type: "active_energy_kcal", identifier: .activeEnergyBurned, unit: .kilocalorie(), cumulative: true, decimals: 0),
        QuantityMetric(type: "exercise_minutes", identifier: .appleExerciseTime, unit: .minute(), cumulative: true, decimals: 0),
        QuantityMetric(type: "resting_hr", identifier: .restingHeartRate, unit: perMinute, cumulative: false, decimals: 0),
        QuantityMetric(type: "walking_hr_avg", identifier: .walkingHeartRateAverage, unit: perMinute, cumulative: false, decimals: 0),
        QuantityMetric(type: "hrv_sdnn_ms", identifier: .heartRateVariabilitySDNN, unit: .secondUnit(with: .milli), cumulative: false, decimals: 1),
        QuantityMetric(type: "vo2max", identifier: .vo2Max, unit: HKUnit(from: "ml/kg*min"), cumulative: false, decimals: 1),
        QuantityMetric(type: "spo2_pct", identifier: .oxygenSaturation, unit: .percent(), cumulative: false, scale: 100, decimals: 1),
        QuantityMetric(type: "respiratory_rate", identifier: .respiratoryRate, unit: perMinute, cumulative: false, decimals: 1),
        QuantityMetric(type: "walking_speed_mps", identifier: .walkingSpeed, unit: .meter().unitDivided(by: .second()), cumulative: false, decimals: 2),
        QuantityMetric(type: "step_length_m", identifier: .walkingStepLength, unit: .meter(), cumulative: false, decimals: 2),
        QuantityMetric(type: "double_support_pct", identifier: .walkingDoubleSupportPercentage, unit: .percent(), cumulative: false, scale: 100, decimals: 1),
        QuantityMetric(type: "walking_asymmetry_pct", identifier: .walkingAsymmetryPercentage, unit: .percent(), cumulative: false, scale: 100, decimals: 1),
        QuantityMetric(type: "walking_steadiness", identifier: .appleWalkingSteadiness, unit: .percent(), cumulative: false, scale: 100, decimals: 1),
        QuantityMetric(type: "weight_kg", identifier: .bodyMass, unit: .gramUnit(with: .kilo), cumulative: false, decimals: 1),
    ]

    static let sleepType = HKCategoryType(.sleepAnalysis)

    /// Read-only access: we never write to Health.
    static var readTypes: Set<HKObjectType> {
        Set(quantityMetrics.map(\.quantityType)).union([sleepType])
    }

    /// Types that wake the app in the background when they change.
    static let backgroundTypes: [HKSampleType] = [
        HKQuantityType(.stepCount),
        HKQuantityType(.restingHeartRate),
        HKQuantityType(.walkingSpeed),
        sleepType,
    ]

    var isAvailable: Bool { HKHealthStore.isHealthDataAvailable() }

    // MARK: Authorization

    func requestAuthorization() async throws {
        guard isAvailable else { throw HealthStoreError.unavailable }
        try await store.requestAuthorization(toShare: [], read: Self.readTypes)
    }

    /// True if the permission sheet has never been shown for our types.
    func needsAuthorization() async -> Bool {
        guard isAvailable else { return false }
        let status = try? await store.statusForAuthorizationRequest(toShare: [], read: Self.readTypes)
        return status == .shouldRequest
    }

    // MARK: Daily samples

    /// Builds one sample per metric per day for the last `days` calendar days (including today).
    /// Days without data are skipped.
    func dailySamples(days: Int) async throws -> [MetricSample] {
        guard isAvailable else { throw HealthStoreError.unavailable }
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        guard let firstDay = calendar.date(byAdding: .day, value: -(days - 1), to: today) else { return [] }

        var samples: [MetricSample] = []
        for metric in Self.quantityMetrics {
            samples += try await quantitySamples(for: metric, from: firstDay, anchor: today)
        }
        samples += try await sleepSamples(from: firstDay, through: today)
        return samples
    }

    private func quantitySamples(for metric: QuantityMetric, from start: Date, anchor: Date) async throws -> [MetricSample] {
        let end = Date.now
        let descriptor = HKStatisticsCollectionQueryDescriptor(
            predicate: .quantitySample(
                type: metric.quantityType,
                predicate: HKQuery.predicateForSamples(withStart: start, end: end)
            ),
            options: metric.cumulative ? .cumulativeSum : .discreteAverage,
            anchorDate: anchor, // local midnight, so each interval is one local calendar day
            intervalComponents: DateComponents(day: 1)
        )

        let collection: HKStatisticsCollection
        do {
            collection = try await descriptor.result(for: store)
        } catch {
            try rethrowIfLocked(error)
            return [] // No data or not authorized for this type: skip it.
        }

        var samples: [MetricSample] = []
        collection.enumerateStatistics(from: start, to: end) { statistics, _ in
            let quantity = metric.cumulative ? statistics.sumQuantity() : statistics.averageQuantity()
            guard let quantity else { return }
            let value = Self.rounded(quantity.doubleValue(for: metric.unit) * metric.scale, decimals: metric.decimals)
            samples.append(MetricSample(type: metric.type, value: value, recordedAt: Self.dayStamp(statistics.startDate)))
        }
        return samples
    }

    // MARK: Sleep

    /// Each night is attributed to its wake-up date D, using samples between 18:00 on D-1 and 12:00 on D.
    private func sleepSamples(from firstDay: Date, through lastDay: Date) async throws -> [MetricSample] {
        let calendar = Calendar.current
        guard let queryStart = Self.sleepWindow(for: firstDay, calendar: calendar)?.start,
              let queryEnd = Self.sleepWindow(for: lastDay, calendar: calendar)?.end
        else { return [] }

        let descriptor = HKSampleQueryDescriptor(
            predicates: [.categorySample(
                type: Self.sleepType,
                predicate: HKQuery.predicateForSamples(withStart: queryStart, end: queryEnd)
            )],
            sortDescriptors: [SortDescriptor(\.startDate)]
        )

        let all: [HKCategorySample]
        do {
            all = try await descriptor.result(for: store)
        } catch {
            try rethrowIfLocked(error)
            return []
        }

        var samples: [MetricSample] = []
        var day = firstDay
        while day <= lastDay {
            if let window = Self.sleepWindow(for: day, calendar: calendar) {
                samples += Self.sleepMetrics(from: all, in: window, wakeDate: day)
            }
            guard let next = calendar.date(byAdding: .day, value: 1, to: day) else { break }
            day = next
        }
        return samples
    }

    private static func sleepWindow(for wakeDate: Date, calendar: Calendar) -> DateInterval? {
        guard let previousDay = calendar.date(byAdding: .day, value: -1, to: wakeDate),
              let start = calendar.date(bySettingHour: 18, minute: 0, second: 0, of: previousDay),
              let end = calendar.date(bySettingHour: 12, minute: 0, second: 0, of: wakeDate)
        else { return nil }
        return DateInterval(start: start, end: end)
    }

    private static func sleepMetrics(from all: [HKCategorySample], in window: DateInterval, wakeDate: Date) -> [MetricSample] {
        // Clip every sample to the window and group the intervals by sleep stage.
        var intervals: [HKCategoryValueSleepAnalysis: [DateInterval]] = [:]
        for sample in all {
            guard let stage = HKCategoryValueSleepAnalysis(rawValue: sample.value) else { continue }
            let start = max(sample.startDate, window.start)
            let end = min(sample.endDate, window.end)
            guard end > start else { continue }
            intervals[stage, default: []].append(DateInterval(start: start, end: end))
        }

        // Merging overlapping intervals means the Watch and iPhone don't double-count the same time.
        func minutes(_ stages: [HKCategoryValueSleepAnalysis]) -> Double {
            let merged = mergeIntervals(stages.flatMap { intervals[$0] ?? [] })
            return merged.reduce(0) { $0 + $1.duration } / 60
        }

        let stamp = dayStamp(wakeDate)
        var samples: [MetricSample] = []
        func add(_ type: String, _ value: Double) {
            // Never send 0 for missing data.
            guard value > 0 else { return }
            samples.append(MetricSample(type: type, value: rounded(value, decimals: 0), recordedAt: stamp))
        }

        add("sleep_asleep_minutes", minutes([.asleepCore, .asleepDeep, .asleepREM, .asleepUnspecified]))
        add("sleep_inbed_minutes", minutes([.inBed]))
        add("sleep_core_minutes", minutes([.asleepCore]))
        add("sleep_deep_minutes", minutes([.asleepDeep]))
        add("sleep_rem_minutes", minutes([.asleepREM]))

        // Awake segments are only recorded alongside sleep stages, so a night with stages
        // and no awake segments is a real 0. Without stage data the count is unknown and skipped.
        let hasStages = [.asleepCore, .asleepDeep, .asleepREM].contains { intervals[$0]?.isEmpty == false }
        let awakeSegments = mergeIntervals(intervals[.awake] ?? []).count
        if hasStages || awakeSegments > 0 {
            samples.append(MetricSample(type: "sleep_awake_count", value: Double(awakeSegments), recordedAt: stamp))
        }
        return samples
    }

    /// Merges overlapping or touching intervals into a sorted list of disjoint intervals.
    static func mergeIntervals(_ intervals: [DateInterval]) -> [DateInterval] {
        var merged: [DateInterval] = []
        for interval in intervals.sorted(by: { $0.start < $1.start }) {
            if let last = merged.last, interval.start <= last.end {
                merged[merged.count - 1] = DateInterval(start: last.start, end: max(last.end, interval.end))
            } else {
                merged.append(interval)
            }
        }
        return merged
    }

    // MARK: Helpers

    private func rethrowIfLocked(_ error: Error) throws {
        if let hkError = error as? HKError, hkError.code == .errorDatabaseInaccessible {
            throw HealthStoreError.locked
        }
    }

    private static func rounded(_ value: Double, decimals: Int) -> Double {
        let factor = pow(10, Double(decimals))
        return (value * factor).rounded() / factor
    }

    /// Local midnight of `date` with its UTC offset, e.g. "2026-10-17T00:00:00-04:00".
    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ssxxx"
        return formatter
    }()

    static func dayStamp(_ date: Date) -> String {
        dayFormatter.timeZone = .current
        return dayFormatter.string(from: Calendar.current.startOfDay(for: date))
    }
}
