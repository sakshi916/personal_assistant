import Combine
import Foundation

struct ChatMessage: Identifiable {
    enum Role {
        case user
        case assistant
    }

    let id = UUID()
    let role: Role
    let text: String
}

@MainActor
final class AssistantModel: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var status = "Starting assistant…"
    @Published private(set) var isReady = false
    @Published private(set) var isThinking = false
    @Published var errorMessage: String?
    @Published private(set) var startupWarning: String?

    private let backend = AssistantBackend()
    private var sessionID: String?

    func start() async {
        do {
            try await OllamaLauncher.openApplication()
        } catch {
            startupWarning = "Could not open Ollama automatically: \(error.localizedDescription)"
        }
        do {
            try await backend.start()
            try await createSession()
            isReady = true
            status = startupWarning == nil ? "Ready" : "Ready · Ollama not opened"
        } catch {
            isReady = false
            status = "Assistant unavailable"
            errorMessage = error.localizedDescription
        }
    }

    func stop() {
        backend.stop()
    }

    func newChat() async {
        messages = []
        isReady = false
        status = "Starting a new chat…"
        do {
            try await createSession()
            isReady = true
            status = "Ready"
        } catch {
            status = "Assistant unavailable"
            errorMessage = error.localizedDescription
        }
    }

    @discardableResult
    func send(_ text: String) async -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let sessionID else { return nil }
        messages.append(ChatMessage(role: .user, text: trimmed))
        isThinking = true
        defer { isThinking = false }
        do {
            let answer = try await backend.send(trimmed, sessionID: sessionID)
            messages.append(ChatMessage(role: .assistant, text: answer))
            return answer
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    private func createSession() async throws {
        sessionID = try await backend.createSession()
    }
}
