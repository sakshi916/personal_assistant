import AppKit
import Darwin
import Foundation

struct SessionResponse: Decodable {
    let session_id: String
}

struct MessageResponse: Decodable {
    let response: String
}

struct ErrorResponse: Decodable {
    let error: String
}

@MainActor
final class AssistantBackend {
    private var backendApplication: NSRunningApplication?
    private var isStarting = false
    private let appSupport = FileManager.default.urls(
        for: .applicationSupportDirectory,
        in: .userDomainMask
    )[0].appendingPathComponent("PersonalAssistant", isDirectory: true)

    private var socketURL: URL {
        appSupport.appendingPathComponent("assistant.sock")
    }

    func start() async throws {
        guard !isStarting, backendApplication == nil else {
            try await waitUntilReady()
            return
        }
        isStarting = true
        defer { isStarting = false }

        let environment = ProcessInfo.processInfo.environment
        let helperURL = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Library/LoginItems/AssistantBackend.app")
        guard FileManager.default.fileExists(atPath: helperURL.path) else {
            throw BackendError.message("The bundled assistant backend app is missing.")
        }

        try FileManager.default.createDirectory(
            at: appSupport,
            withIntermediateDirectories: true
        )
        let logDirectory = FileManager.default.urls(
            for: .libraryDirectory,
            in: .userDomainMask
        )[0].appendingPathComponent("Logs/PersonalAssistant", isDirectory: true)
        try FileManager.default.createDirectory(
            at: logDirectory,
            withIntermediateDirectories: true
        )
        try await stopLegacyNetworkHelpers()
        if (try? await healthCheck()) != nil {
            return
        }

        let helperEnvironment = environment.merging(
            [
                "PERSONAL_ASSISTANT_API_SOCKET": socketURL.path,
                "PERSONAL_ASSISTANT_DATA_DIR": appSupport.path,
                "PERSONAL_ASSISTANT_LOG_DIR": logDirectory.path
            ],
            uniquingKeysWith: { _, new in new }
        )

        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.environment = helperEnvironment
        do {
            backendApplication = try await NSWorkspace.shared.openApplication(
                at: helperURL,
                configuration: configuration
            )
        } catch {
            let detail = error as NSError
            let message = "Could not open the bundled assistant helper with Launch Services (\(detail.domain) \(detail.code)): \(detail.localizedDescription)"
            throw BackendError.message(message)
        }
        do {
            try await waitUntilReady()
        } catch {
            stop()
            throw error
        }
    }

    func stop() {
        let semaphore = DispatchSemaphore(value: 0)
        let socketPath = socketURL.path
        Task.detached {
            _ = try? UnixDomainHTTPClient.request(
                path: "/shutdown",
                method: "POST",
                body: Data("{}".utf8),
                socketPath: socketPath
            )
            semaphore.signal()
        }
        _ = semaphore.wait(timeout: .now() + 2)
        backendApplication = nil
    }

    func createSession() async throws -> String {
        let response: SessionResponse = try await request(
            path: "/sessions",
            method: "POST",
            body: Data("{}".utf8)
        )
        return response.session_id
    }

    func send(_ text: String, sessionID: String) async throws -> String {
        let response: MessageResponse = try await request(
            path: "/sessions/\(sessionID)/messages",
            method: "POST",
            body: try JSONEncoder().encode(UserMessageRequest(text: text))
        )
        return response.response
    }

    private func waitUntilReady() async throws {
        for _ in 0..<40 {
            if backendApplication?.isTerminated == true {
                throw BackendError.message(
                    "The assistant backend app quit during startup."
                )
            }
            do {
                let _: HealthResponse = try await request(
                    path: "/health",
                    method: "GET"
                )
                return
            } catch {
                try await Task.sleep(for: .milliseconds(250))
            }
        }
        stop()
        throw BackendError.message(
            "The assistant backend did not respond on its local Unix socket."
        )
    }

    private func healthCheck() async throws -> HealthResponse {
        try await request(path: "/health", method: "GET")
    }

    private func stopLegacyNetworkHelpers() async throws {
        let legacyBundleIdentifiers = [
            "com.sakshi.personal-assistant.backend",
            "com.sakshi.personal-assistant.backend.v2",
            "com.sakshi.personal-assistant.backend.v3"
        ]
        let legacyHelpers = legacyBundleIdentifiers.flatMap {
            NSRunningApplication.runningApplications(withBundleIdentifier: $0)
        }
        for helper in legacyHelpers {
            helper.terminate()
        }
        guard !legacyHelpers.isEmpty else { return }

        try await Task.sleep(for: .milliseconds(300))
        for helper in legacyHelpers where !helper.isTerminated {
            helper.forceTerminate()
        }
    }

    private func request<Response: Decodable>(
        path: String,
        method: String,
        body: Data? = nil
    ) async throws -> Response {
        let socketPath = socketURL.path
        let data = try await Task.detached {
            try UnixDomainHTTPClient.request(
                path: path,
                method: method,
                body: body,
                socketPath: socketPath
            )
        }.value
        return try JSONDecoder().decode(Response.self, from: data)
    }
}

private enum UnixDomainHTTPClient {
    static func request(
        path: String,
        method: String,
        body: Data?,
        socketPath: String
    ) throws -> Data {
        guard socketPath.utf8.count < MemoryLayout.size(ofValue: sockaddr_un().sun_path) else {
            throw BackendError.message("The assistant socket path is too long.")
        }

        let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else {
            throw BackendError.message("Could not create the assistant IPC socket.")
        }
        defer { Darwin.close(descriptor) }

        var noSignal: Int32 = 1
        _ = setsockopt(
            descriptor,
            SOL_SOCKET,
            SO_NOSIGPIPE,
            &noSignal,
            socklen_t(MemoryLayout<Int32>.size)
        )

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let socketCapacity = MemoryLayout.size(ofValue: address.sun_path)
        withUnsafeMutablePointer(to: &address.sun_path) {
            $0.withMemoryRebound(to: CChar.self, capacity: socketCapacity) {
                _ = strlcpy($0, socketPath, socketCapacity)
            }
        }
        let connectResult = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(
                    descriptor,
                    $0,
                    socklen_t(MemoryLayout<sockaddr_un>.size)
                )
            }
        }
        guard connectResult == 0 else {
            throw BackendError.message(
                "Could not connect to the assistant helper. Check that it is running."
            )
        }

        let body = body ?? Data()
        var request = Data(
            "\(method) \(path) HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nContent-Length: \(body.count)\r\n".utf8
        )
        if !body.isEmpty {
            request.append(Data("Content-Type: application/json\r\n".utf8))
        }
        request.append(Data("\r\n".utf8))
        request.append(body)

        try request.withUnsafeBytes { bytes in
            guard let baseAddress = bytes.baseAddress else { return }
            var sent = 0
            while sent < bytes.count {
                let result = Darwin.write(
                    descriptor,
                    baseAddress.advanced(by: sent),
                    bytes.count - sent
                )
                guard result > 0 else {
                    throw BackendError.message("Could not send a request to the assistant helper.")
                }
                sent += result
            }
        }

        var response = Data()
        var buffer = [UInt8](repeating: 0, count: 16_384)
        while true {
            let count = Darwin.read(descriptor, &buffer, buffer.count)
            if count == 0 { break }
            guard count > 0 else {
                throw BackendError.message("Could not read the assistant helper response.")
            }
            response.append(contentsOf: buffer.prefix(count))
        }

        guard let separator = response.range(of: Data("\r\n\r\n".utf8)),
              let header = String(data: response[..<separator.lowerBound], encoding: .utf8),
              let statusLine = header.components(separatedBy: "\r\n").first,
              let statusPart = statusLine.split(separator: " ").dropFirst().first,
              let status = Int(statusPart)
        else {
            throw BackendError.message("The assistant helper returned an invalid response.")
        }

        let payload = response[separator.upperBound...]
        guard (200..<300).contains(status) else {
            if let error = try? JSONDecoder().decode(ErrorResponse.self, from: payload) {
                throw BackendError.message(error.error)
            }
            throw BackendError.message("The assistant helper returned HTTP \(status).")
        }
        return Data(payload)
    }
}

private struct UserMessageRequest: Encodable {
    let text: String
}

private struct HealthResponse: Decodable {
    let status: String
}

enum BackendError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let message):
            return message
        }
    }
}
