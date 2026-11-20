import AppKit

enum OllamaLauncher {
    static func openApplication() async throws {
        let runningApps = NSRunningApplication.runningApplications(
            withBundleIdentifier: "com.electron.ollama"
        )
        if runningApps.contains(where: { !$0.isTerminated }) {
            return
        }

        guard let applicationURL = NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: "com.electron.ollama"
        ) else {
            throw BackendError.message(
                "Ollama.app was not found. Install Ollama, then launch Personal Assistant again."
            )
        }

        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Void, Error>) in
            NSWorkspace.shared.openApplication(
                at: applicationURL,
                configuration: configuration
            ) { _, error in
                if let error {
                    continuation.resume(throwing: BackendError.message(
                        "Could not open Ollama.app: \(error.localizedDescription)"
                    ))
                } else {
                    continuation.resume()
                }
            }
        }
    }
}
