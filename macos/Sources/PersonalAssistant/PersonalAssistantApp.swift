import AppKit
import SwiftUI

final class AssistantAppDelegate: NSObject, NSApplicationDelegate {
    var stopAssistant: (() -> Void)?

    func applicationWillTerminate(_ notification: Notification) {
        stopAssistant?()
    }
}

@main
struct PersonalAssistantApp: App {
    @NSApplicationDelegateAdaptor(AssistantAppDelegate.self) private var appDelegate
    @StateObject private var model = AssistantModel()

    var body: some Scene {
        WindowGroup("Personal Assistant", id: "chat") {
            ChatWindow(model: model)
                .task { await model.start() }
                .onAppear { appDelegate.stopAssistant = { model.stop() } }
        }
        .defaultSize(width: 520, height: 680)
        .windowResizability(.contentSize)

        MenuBarExtra("Assistant", systemImage: "waveform") {
            VoicePopup(model: model)
        }
        .menuBarExtraStyle(.window)
    }
}

struct ChatWindow: View {
    @ObservedObject var model: AssistantModel
    @State private var draft = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        if model.messages.isEmpty {
                            VStack(spacing: 10) {
                                Image(systemName: "sparkles")
                                    .font(.largeTitle)
                                    .foregroundStyle(.secondary)
                                Text("How can I help?")
                                    .font(.headline)
                                Text("Ask a question or use the voice popup in the menu bar.")
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                                    .multilineTextAlignment(.center)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                        }
                        ForEach(model.messages) { message in
                            messageView(message)
                                .id(message.id)
                        }
                        if model.isThinking {
                            HStack(spacing: 8) {
                                ProgressView()
                                    .controlSize(.small)
                                Text("Thinking…")
                                    .foregroundStyle(.secondary)
                            }
                            .padding(.leading, 8)
                            .id("thinking")
                        }
                    }
                    .padding(18)
                }
                .onChange(of: model.messages.count) { _ in
                    if let last = model.messages.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
                .onChange(of: model.isThinking) { thinking in
                    if thinking { withAnimation { proxy.scrollTo("thinking", anchor: .bottom) } }
                }
            }
            Divider()
            composer
        }
        .frame(minWidth: 420, minHeight: 520)
        .alert("Assistant", isPresented: errorPresented) {
            Button("OK", role: .cancel) { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkles")
                .foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 2) {
                Text("Personal Assistant")
                    .font(.headline)
                Text(model.status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let warning = model.startupWarning {
                    Text(warning)
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
            }
            Spacer()
            Button {
                Task { await model.newChat() }
            } label: {
                Image(systemName: "square.and.pencil")
            }
            .help("New chat")
            .disabled(!model.isReady)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Message your assistant…", text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...5)
                .focused($inputFocused)
                .onSubmit(send)
                .padding(10)
                .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
            Button(action: send) {
                Image(systemName: "arrow.up")
                    .font(.headline)
                    .frame(width: 34, height: 34)
            }
            .buttonStyle(.borderedProminent)
            .clipShape(Circle())
            .disabled(!model.isReady || model.isThinking || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(14)
    }

    private func messageView(_ message: ChatMessage) -> some View {
        HStack {
            if message.role == .user { Spacer(minLength: 35) }
            VStack(alignment: .leading, spacing: 5) {
                Text(message.role == .user ? "You" : "Assistant")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                if message.role == .user {
                    Text(message.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    MarkdownMessage(markdown: message.text)
                }
            }
            .padding(12)
            .background(
                message.role == .user ? Color.accentColor.opacity(0.12) : Color.primary.opacity(0.035),
                in: RoundedRectangle(cornerRadius: 12)
            )
            if message.role == .assistant { Spacer(minLength: 20) }
        }
    }

    private var errorPresented: Binding<Bool> {
        Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )
    }

    private func send() {
        let text = draft
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        draft = ""
        inputFocused = false
        Task { await model.send(text) }
    }
}
