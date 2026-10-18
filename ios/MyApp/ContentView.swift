import SwiftUI

struct ContentView: View {
    private let sync = SyncManager.shared
    @State private var showingScanner = false

    var body: some View {
        NavigationStack {
            Group {
                if sync.isPaired {
                    pairedView
                } else {
                    unpairedView
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .navigationTitle("Health Companion")
        }
        .fullScreenCover(isPresented: $showingScanner) {
            QRScannerView { url in
                Task { await sync.pair(with: url) }
            }
        }
    }

    // MARK: Not paired

    private var unpairedView: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "heart.text.square.fill")
                .font(.system(size: 64))
                .foregroundStyle(.red)
            Text("Sends your daily Apple Health values to your health dashboard.")
                .font(.title3)
                .multilineTextAlignment(.center)

            if let notice = sync.notice {
                Label(notice, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
            }

            Button {
                showingScanner = true
            } label: {
                Label("Scan QR code", systemImage: "qrcode.viewfinder")
                    .font(.title3.bold())
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)

            Text("Open your account on the website → Data → Apple Watch → Connect with the iPhone app")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Spacer()
        }
        .padding()
    }

    // MARK: Paired

    private var pairedView: some View {
        List {
            Section {
                Label("Connected", systemImage: "checkmark.circle.fill")
                    .font(.headline)
                    .foregroundStyle(.green)
            }

            Section("Last sync") {
                if sync.isSyncing {
                    HStack {
                        ProgressView()
                        Text("Syncing… the first sync can take up to a minute.")
                    }
                } else if let date = sync.lastSyncDate {
                    LabeledContent("Time", value: date.formatted(date: .abbreviated, time: .shortened))
                    if let message = sync.lastSyncMessage {
                        Text(message)
                            .foregroundStyle(sync.lastSyncFailed ? .red : .primary)
                    }
                } else {
                    Text("Not synced yet")
                        .foregroundStyle(.secondary)
                }

                Button("Sync now") {
                    Task { await sync.sync() }
                }
                .disabled(sync.isSyncing)
            }

            Section("Today") {
                LabeledContent("Steps", value: sync.todaySteps.map { $0.formatted(.number.precision(.fractionLength(0))) } ?? "—")
                LabeledContent("Resting heart rate", value: sync.todayRestingHR.map { "\(Int($0)) bpm" } ?? "—")
            }

            Section {
                Button("Disconnect", role: .destructive) {
                    sync.disconnect()
                }
            }
        }
    }
}

#Preview {
    ContentView()
}
