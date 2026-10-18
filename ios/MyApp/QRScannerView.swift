import AVFoundation
import SwiftUI
import UIKit

/// Full-screen QR scanner. Calls `onPairingURL` with the first valid `pdcompanion://pair` link it sees.
struct QRScannerView: View {
    let onPairingURL: (URL) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var cameraAllowed: Bool?
    @State private var hint: String?

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                switch cameraAllowed {
                case true?:
                    CameraPreview(onCode: handle)
                        .ignoresSafeArea()
                    VStack {
                        Spacer()
                        Text(hint ?? "Point the camera at the QR code on the website.")
                            .font(.callout)
                            .multilineTextAlignment(.center)
                            .padding()
                            .background(.ultraThinMaterial, in: .rect(cornerRadius: 12))
                            .padding()
                    }
                case false?:
                    ContentUnavailableView {
                        Label("Camera access is off", systemImage: "camera.fill")
                    } description: {
                        Text("Allow camera access in Settings to scan the pairing QR code, or scan it with the iPhone Camera app.")
                    } actions: {
                        Button("Open Settings") {
                            if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        }
                    }
                    .foregroundStyle(.white)
                case nil:
                    ProgressView()
                }
            }
            .navigationTitle("Scan QR code")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .task {
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized:
                cameraAllowed = true
            case .notDetermined:
                cameraAllowed = await AVCaptureDevice.requestAccess(for: .video)
            default:
                cameraAllowed = false
            }
        }
    }

    private func handle(_ code: String) {
        guard let url = URL(string: code), (try? Pairing.parse(url)) != nil else {
            hint = "That QR code isn't a pairing code. Scan the one shown on the website."
            return
        }
        onPairingURL(url)
        dismiss()
    }
}

/// Camera preview that reports scanned QR code strings using AVCaptureMetadataOutput.
private struct CameraPreview: UIViewControllerRepresentable {
    let onCode: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerViewController {
        let controller = ScannerViewController()
        controller.onCode = onCode
        return controller
    }

    func updateUIViewController(_ controller: ScannerViewController, context: Context) {
        controller.onCode = onCode
    }
}

final class ScannerViewController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onCode: ((String) -> Void)?

    private let session = AVCaptureSession()
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var lastCode: String?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black

        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input)
        else { return }
        session.addInput(input)

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]

        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        previewLayer = layer
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        let session = session
        // startRunning blocks, so keep it off the main thread.
        DispatchQueue.global(qos: .userInitiated).async { session.startRunning() }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        let session = session
        DispatchQueue.global(qos: .userInitiated).async { session.stopRunning() }
    }

    nonisolated func metadataOutput(
        _ output: AVCaptureMetadataOutput,
        didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        let code = metadataObjects.compactMap { ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }.first
        // The delegate queue is .main.
        MainActor.assumeIsolated {
            // The same code is reported many times per second; only react when it changes.
            guard let code, code != lastCode else { return }
            lastCode = code
            onCode?(code)
        }
    }
}
