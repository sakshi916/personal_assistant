import AppKit
import AVFoundation
import Combine
import SwiftUI
import WhisperKit

@MainActor
final class VoiceAssistant: NSObject, ObservableObject, AVSpeechSynthesizerDelegate {
    @Published private(set) var isListening = false
    @Published private(set) var isSpeaking = false
    @Published private(set) var transcript = ""
    @Published private(set) var status = "Tap to speak"
    @Published var errorMessage: String?

    private let synthesizer = AVSpeechSynthesizer()
    private var recorder: AVAudioRecorder?
    private var recordingURL: URL?
    private var whisperKit: WhisperKit?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func toggle(model: AssistantModel) {
        if isListening {
            Task { await finishListening(model: model) }
        } else {
            Task { await beginListening() }
        }
    }

    private func beginListening() async {
        do {
            errorMessage = nil
            try await requestMicrophonePermission()
            status = "Preparing offline speech recognition…"
            try await prepareWhisper()

            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("personal-assistant-\(UUID().uuidString)")
                .appendingPathExtension("wav")
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: 16_000,
                AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false
            ]
            let recorder = try AVAudioRecorder(url: url, settings: settings)
            guard recorder.prepareToRecord(), recorder.record() else {
                throw VoiceError.recordingFailed
            }
            self.recorder = recorder
            recordingURL = url
            transcript = ""
            status = "Listening… tap to finish"
            isListening = true
        } catch {
            isListening = false
            status = "Tap to speak"
            errorMessage = error.localizedDescription
        }
    }

    private func finishListening(model: AssistantModel) async {
        isListening = false
        status = "Processing speech…"
        recorder?.stop()
        recorder = nil
        guard let recordingURL else {
            status = "Tap to speak"
            errorMessage = VoiceError.recordingFailed.localizedDescription
            return
        }
        self.recordingURL = nil
        defer { try? FileManager.default.removeItem(at: recordingURL) }

        do {
            guard let whisperKit else { throw VoiceError.modelUnavailable }
            let results = try await whisperKit.transcribe(audioPath: recordingURL.path)
            transcript = results.map(\.text).joined(separator: " ")
        } catch {
            status = "Tap to speak"
            errorMessage = error.localizedDescription
            return
        }

        let spokenText = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !spokenText.isEmpty else {
            status = "Tap to speak"
            return
        }
        status = "Thinking…"
        if let answer = await model.send(spokenText) {
            isSpeaking = true
            status = "Speaking…"
            let utterance = AVSpeechUtterance(string: answer)
            synthesizer.speak(utterance)
        } else {
            status = "Tap to speak"
        }
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didFinish utterance: AVSpeechUtterance
    ) {
        Task { @MainActor in
            isSpeaking = false
            status = "Tap to speak"
        }
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        didCancel utterance: AVSpeechUtterance
    ) {
        Task { @MainActor in
            isSpeaking = false
            status = "Tap to speak"
        }
    }

    private func prepareWhisper() async throws {
        guard whisperKit == nil else { return }
        let supportDirectory = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let modelDirectory = supportDirectory
            .appendingPathComponent("PersonalAssistant", isDirectory: true)
            .appendingPathComponent("WhisperKit", isDirectory: true)
        try FileManager.default.createDirectory(
            at: modelDirectory,
            withIntermediateDirectories: true
        )
        let config = WhisperKitConfig(
            model: "base",
            downloadBase: modelDirectory,
            verbose: false,
            load: true
        )
        whisperKit = try await WhisperKit(config)
    }

    private func requestMicrophonePermission() async throws {
        let microphoneAuthorized = await AVCaptureDevice.requestAccess(for: .audio)
        guard microphoneAuthorized else {
            throw VoiceError.microphonePermission
        }
    }
}

private enum VoiceError: LocalizedError {
    case microphonePermission
    case recordingFailed
    case modelUnavailable

    var errorDescription: String? {
        switch self {
        case .microphonePermission:
            "Allow microphone access in System Settings to use voice mode."
        case .recordingFailed:
            "Could not start or access the microphone recording."
        case .modelUnavailable:
            "The local Whisper model is not ready. Try voice mode again."
        }
    }
}

struct VoicePopup: View {
    @ObservedObject var model: AssistantModel
    @StateObject private var voice = VoiceAssistant()
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: voice.isListening ? "waveform.circle.fill" : "waveform.circle")
                .font(.system(size: 38))
                .foregroundStyle(voice.isListening ? Color.red : Color.accentColor)
            Text(voice.transcript.isEmpty ? voice.status : voice.transcript)
                .font(.callout)
                .multilineTextAlignment(.center)
                .lineLimit(4)
                .frame(minHeight: 40)
            Button {
                voice.toggle(model: model)
            } label: {
                Label(
                    voice.isListening ? "Finish speaking" : "Start speaking",
                    systemImage: voice.isListening ? "stop.fill" : "mic.fill"
                )
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled((!model.isReady && !voice.isListening) || voice.isSpeaking)
            Text(model.status)
                .font(.caption)
                .foregroundStyle(.secondary)
            if let message = voice.errorMessage ?? model.errorMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            HStack {
                Button("Open chat") {
                    openWindow(id: "chat")
                    NSApp.activate(ignoringOtherApps: true)
                }
                Spacer()
                Button("Quit") {
                    NSApp.terminate(nil)
                }
            }
        }
        .padding(18)
        .frame(width: 280)
    }
}
