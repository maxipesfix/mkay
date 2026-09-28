import Foundation

/// Runs the public connector.py with the bundled Python and follows its --events output.
///
/// The connector does the work (sign-in, the outbound WebSocket, agent_mcp.py, the
/// read-only guard); this class starts and stops it, restarts it if it crashes, and turns
/// its JSON lines into state for the menu and the setup window.
@MainActor
final class Connector: ObservableObject {
    enum State: Equatable {
        case notSignedIn
        case signingIn(code: String?, page: URL?)
        case connecting
        case connected(server: String)
        case reconnecting(seconds: Int)
        case paused
    }

    struct ToolCall: Identifiable {
        let id = UUID()
        let time: Date
        let name: String
        let refused: Bool
    }

    @Published private(set) var state: State = .notSignedIn
    /// Why the connector last stopped by itself (unlinked, expired code, unreachable server).
    @Published private(set) var problem: String?
    @Published private(set) var recent: [ToolCall] = []
    @Published var readOnly: Bool {
        didSet {
            UserDefaults.standard.set(readOnly, forKey: "readOnly")
            if readOnly != oldValue, process != nil, !isSigningIn { restart() }
        }
    }

    /// The service's address; `defaults write ai.mkay.mac server http://localhost:7870` for development.
    let server: URL
    var accountPage: URL { server.appendingPathComponent("account") }
    var isLinked: Bool { FileManager.default.fileExists(atPath: Paths.token.path) }
    var isSigningIn: Bool { if case .signingIn = state { return true } else { return false } }

    private var process: Process?
    private var stopping = false
    private var stoppedReason: String?
    private var crashes = 0
    private var buffer = Data()

    init() {
        let configured = UserDefaults.standard.string(forKey: "server").flatMap(URL.init(string:))
        server = configured ?? URL(string: "https://app.mkay.ai")!
        readOnly = UserDefaults.standard.bool(forKey: "readOnly")
    }

    /// Connect if this Mac is linked and the user has not paused.
    func startIfLinked() {
        guard isLinked else { state = .notSignedIn; return }
        if UserDefaults.standard.bool(forKey: "paused") { state = .paused; return }
        launch(login: false)
    }

    func signIn() {
        problem = nil
        stop(then: { self.launch(login: true) })
    }

    func pause() {
        UserDefaults.standard.set(true, forKey: "paused")
        stop(then: { self.state = .paused })
    }

    func resume() {
        UserDefaults.standard.set(false, forKey: "paused")
        startIfLinked()
    }

    /// Forget this Mac's device token here. The account page's Unlink revokes it on the server.
    func signOut() {
        stop(then: {
            for file in [Paths.token, Paths.connectorURL] { try? FileManager.default.removeItem(at: file) }
            self.recent = []
            self.state = .notSignedIn
        })
    }

    func restart() {
        stop(then: { self.startIfLinked() })
    }

    /// Stop before the app quits: SIGINT lets the connector close agent_mcp.py cleanly.
    func stopForQuit() {
        guard let process, process.isRunning else { return }
        stopping = true
        process.interrupt()
        let deadline = Date().addingTimeInterval(3)
        while process.isRunning && Date() < deadline { usleep(50_000) }
        if process.isRunning { process.terminate() }
    }

    // MARK: - Process

    private func launch(login: Bool) {
        guard process == nil else { return }
        guard Permissions.runsFromApplications else {
            // A copy on the disk image would lose its permissions, and would compete with
            // the installed copy for this account's connection.
            problem = "Open m’kay from your Applications folder, not from the disk image."
            state = isLinked ? .paused : .notSignedIn
            return
        }
        let python = Paths.python
        var arguments = [Paths.connectorScript.path, "--events", "--python", python.path,
                         "--token-file", Paths.token.path, "--url", connectorEndpoint]
        if login { arguments += ["--login", server.absoluteString, "--no-browser"] }
        if readOnly { arguments.append("--read-only") }

        let process = Process()
        process.executableURL = python
        process.arguments = arguments
        process.environment = Self.environment()
        let output = Pipe(), errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        process.standardInput = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            Task { @MainActor in self?.received(data) }
        }
        errors.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor in Log.write(text.trimmingCharacters(in: .newlines)) }
        }
        process.terminationHandler = { [weak self] finished in
            let status = finished.terminationStatus
            Task { @MainActor in self?.exited(status) }
        }
        stopping = false
        stoppedReason = nil
        buffer = Data()
        state = login ? .signingIn(code: nil, page: nil) : .connecting
        do {
            try process.run()
            self.process = process
            Log.write("Started the connector" + (login ? " to sign in" : "") + (readOnly ? " (read-only)" : "") + ".")
        } catch {
            problem = "Cannot start the connector: \(error.localizedDescription)"
            Log.write(problem!)
            state = isLinked ? .paused : .notSignedIn
        }
    }

    private var pendingStop: (() -> Void)?

    private func stop(then next: @escaping () -> Void) {
        guard let process, process.isRunning else { self.process = nil; next(); return }
        stopping = true
        pendingStop = next
        process.interrupt()
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak process] in
            if let process, process.isRunning { process.terminate() }
        }
    }

    private func exited(_ status: Int32) {
        process = nil
        if stopping {
            stopping = false
            let next = pendingStop
            pendingStop = nil
            next?()
            return
        }
        Log.write("The connector exited with status \(status).")
        switch stoppedReason {
        case "token_refused", "unlinked":
            // Retrying cannot help: this Mac has to be linked again.
            for file in [Paths.token, Paths.connectorURL] { try? FileManager.default.removeItem(at: file) }
            state = .notSignedIn
        case "expired", "unreachable", "usage":
            state = isLinked ? .paused : .notSignedIn
        case "replaced":
            // Another Mac or copy of this app took over; Resume takes the connection back.
            UserDefaults.standard.set(true, forKey: "paused")
            state = .paused
        default:
            // A crash or an unexpected exit: start again, waiting longer each time.
            guard isLinked else { state = .notSignedIn; return }
            crashes += 1
            let delay = min(60, 2 << min(crashes, 5))
            state = .reconnecting(seconds: delay)
            DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(delay)) { [weak self] in
                guard let self, self.process == nil, case .reconnecting = self.state else { return }
                self.startIfLinked()
            }
        }
    }

    private func received(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<newline]
            buffer.removeSubrange(buffer.startIndex...newline)
            guard let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else {
                if let text = String(data: line, encoding: .utf8), !text.isEmpty { Log.write(text) }
                continue
            }
            handle(event)
        }
    }

    private func handle(_ event: [String: Any]) {
        let message = event["message"] as? String ?? ""
        switch event["event"] as? String {
        case "code":
            let page = (event["verification_uri"] as? String).flatMap(URL.init(string:))
            state = .signingIn(code: event["user_code"] as? String, page: page)
            if let page { Browser.open(page) }
        case "linked":
            state = .connecting
        case "connected":
            crashes = 0
            problem = nil
            state = .connected(server: event["server"] as? String ?? server.host ?? "")
        case "disconnected":
            state = .reconnecting(seconds: event["retry_in"] as? Int ?? 0)
        case "tool":
            let call = ToolCall(time: Date(), name: event["name"] as? String ?? "?",
                                refused: event["refused"] as? Bool ?? false)
            recent.insert(call, at: 0)
            if recent.count > 15 { recent.removeLast() }
            // Tool names only: arguments can hold message text, which stays out of the log file.
            Log.write("Tool: \(call.name)" + (call.refused ? " (refused, read-only)" : ""))
            return
        case "stopped":
            stoppedReason = event["reason"] as? String
            if stoppedReason != "interrupted" { problem = message }
        default:
            break
        }
        if !message.isEmpty { Log.write(message) }
    }

    private var connectorEndpoint: String {
        var parts = URLComponents(url: server, resolvingAgainstBaseURL: false)!
        parts.scheme = parts.scheme == "https" ? "wss" : "ws"
        parts.path = "/connector"
        return parts.string!
    }

    private static func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"] { env.removeValue(forKey: key) }
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        env["LANG"] = env["LANG"] ?? "en_US.UTF-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"  // the bundle is signed: never write into it
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        return env
    }
}

enum Paths {
    static var resources: URL { Bundle.main.resourceURL! }
    static var python: URL { resources.appendingPathComponent("python/bin/python3") }
    static var connectorScript: URL { resources.appendingPathComponent("mkay/multi-agent-mcp/connector.py") }

    /// The app's own token, separate from a connector run in a terminal (~/.config/agent-mcp).
    static var support: URL {
        let url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("mkay", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
        return url
    }
    static var token: URL { support.appendingPathComponent("connector-token") }
    static var connectorURL: URL { support.appendingPathComponent("connector-url") }
    static var logFile: URL {
        let url = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Logs/mkay", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url.appendingPathComponent("connector.log")
    }
}

/// ~/Library/Logs/mkay/connector.log, kept under about 2 MB (one older file is kept).
enum Log {
    private static let formatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter
    }()

    @MainActor static func write(_ text: String) {
        let url = Paths.logFile
        if let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size > 2_000_000 {
            let old = url.appendingPathExtension("1")
            try? FileManager.default.removeItem(at: old)
            try? FileManager.default.moveItem(at: url, to: old)
        }
        let line = Data("[\(formatter.string(from: Date()))] \(text)\n".utf8)
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(line)
            try? handle.close()
        } else {
            try? line.write(to: url)
        }
    }
}
