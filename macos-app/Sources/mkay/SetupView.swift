import SwiftUI

/// The setup window: the two permissions, then sign-in. Shown at first launch and
/// whenever something is missing; the menu's "Set Up…" opens it again.
struct SetupView: View {
    @ObservedObject var connector: Connector
    @StateObject private var checks = Checks()

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Set up m’kay").font(.title2).bold()
                Text("Talk from your phone or any browser to the Claude, ChatGPT and Cursor apps on this Mac.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if !Permissions.runsFromApplications {
                Label("Drag m’kay to your Applications folder and open it from there first; permissions given to the copy on the disk image are lost.",
                      systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Step(number: 1, title: "Accessibility",
                 detail: "Lets m’kay read and operate the agent apps’ windows.",
                 done: checks.accessibility) {
                Button("Open Settings…") { Permissions.requestAccessibility() }
            }

            Step(number: 2, title: "Automation",
                 detail: "Lets m’kay press keys (paste, Return) in the agent apps through System Events.",
                 done: checks.automation == .allowed) {
                Button(checks.automation == .denied ? "Open Settings…" : "Allow…") {
                    if checks.automation == .denied {
                        Permissions.openSettings("Privacy_Automation")
                    } else {
                        Permissions.requestAutomation { checks.automation = $0 }
                    }
                }
            }

            Step(number: 3, title: "Sign in",
                 detail: signInDetail,
                 done: connector.isLinked && !connector.isSigningIn) {
                signInControls
            }

            if let problem = connector.problem {
                Text(problem).font(.callout).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Divider()

            Toggle("Read-only: never send messages or answer questions", isOn: $connector.readOnly)
            Toggle("Start m’kay at login", isOn: Binding(get: { checks.loginItem },
                                                         set: { LoginItem.set($0); checks.refresh() }))

            HStack {
                if connector.isLinked && !connector.isSigningIn {
                    Button("Talk to Your Mac…") { Browser.open(connector.accountPage) }
                }
                Spacer()
                Button("Done") { NSApp.keyWindow?.close() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 460)
    }

    private var signInDetail: String {
        if case .signingIn(let code?, _) = connector.state {
            return "Your code is \(code). Link this Mac only if the page in your browser shows the same code."
        }
        if connector.isSigningIn { return "Asking \(connector.server.host ?? "the server") for a code…" }
        if connector.isLinked { return "This Mac is linked to your account at \(connector.server.host ?? "")." }
        return "Link this Mac to your account at \(connector.server.host ?? "mkay.ai"). You sign in in your browser; your password never reaches this app."
    }

    @ViewBuilder private var signInControls: some View {
        if case .signingIn(let code, let page) = connector.state {
            HStack {
                if code != nil, let page { Button("Open Page Again") { Browser.open(page) } }
                Button("Cancel") { connector.signOut() }
            }
        } else if !connector.isLinked {
            Button("Sign In…") { connector.signIn() }
        }
    }
}

private struct Step<Controls: View>: View {
    let number: Int
    let title: String
    let detail: String
    let done: Bool
    @ViewBuilder let controls: () -> Controls

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: done ? "checkmark.circle.fill" : "\(number).circle")
                .font(.title2)
                .foregroundStyle(done ? Color.green : Color.secondary)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline)
                Text(detail).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if !done { controls() }
            }
        }
    }
}

/// Permission state, polled while the window is open: macOS sends no notice when it changes.
@MainActor
private final class Checks: ObservableObject {
    @Published var accessibility = Permissions.accessibility
    @Published var automation = Permissions.automation(ask: false)
    @Published var loginItem = LoginItem.enabled
    private var timer: Timer?

    init() {
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    deinit { timer?.invalidate() }

    func refresh() {
        accessibility = Permissions.accessibility
        let current = Permissions.automation(ask: false)
        if current != .unknown { automation = current }
        loginItem = LoginItem.enabled
    }
}
