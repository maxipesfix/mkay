import AppKit
import Combine
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let connector = Connector()
    private var statusItem: NSStatusItem!
    private var setupWindow: NSWindow?
    private var observation: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if anotherCopyRuns() {
            // Like opening an app that is already open: the running copy shows its window.
            DistributedNotificationCenter.default().postNotificationName(Self.showSetupNotice, object: nil,
                                                                         deliverImmediately: true)
            NSApp.terminate(nil)
            return
        }
        DistributedNotificationCenter.default().addObserver(forName: Self.showSetupNotice, object: nil,
                                                            queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.showSetup() }
        }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        observation = connector.objectWillChange.sink { [weak self] in
            DispatchQueue.main.async { self?.updateIcon() }
        }
        updateIcon()
        connector.startIfLinked()
        if setupNeeded { showSetup() }
    }

    func applicationWillTerminate(_ notification: Notification) {
        connector.stopForQuit()
    }

    /// Opening the app again (from Finder or Spotlight) shows the setup window.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSetup()
        return false
    }

    private static let showSetupNotice = Notification.Name("ai.mkay.mac.showSetup")

    /// One copy at a time: two would take the account's connection from each other and drop
    /// every voice session. A copy started while another runs quits. The exception is a
    /// copy running from the disk image, which cannot connect: one in Applications replaces it.
    private func anotherCopyRuns() -> Bool {
        let me = NSRunningApplication.current
        var others = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier!)
            .filter { $0 != me }
        if Permissions.runsFromApplications {
            let stranded = others.filter { !Permissions.isInstalled($0.bundleURL?.path ?? "") }
            stranded.forEach { $0.terminate() }
            others.removeAll { stranded.contains($0) }
        }
        // Two copies started at the same moment: the older one stays.
        let mine = me.launchDate ?? Date()
        return others.contains { other in
            let theirs = other.launchDate ?? .distantPast
            return theirs < mine || (theirs == mine && other.processIdentifier < me.processIdentifier)
        }
    }

    private var setupNeeded: Bool {
        !connector.isLinked || !Permissions.accessibility || Permissions.automation(ask: false) != .allowed
    }

    // MARK: - Status item

    private func updateIcon() {
        let connected: Bool
        if case .connected = connector.state { connected = true } else { connected = false }
        statusItem.button?.image = Self.apostrophe
        statusItem.button?.appearsDisabled = !connected
        statusItem.button?.toolTip = "m’kay: " + statusText
    }

    /// The menu-bar icon: the prime-shaped apostrophe of the app icon (make-icon.swift draws
    /// the same outline), a template image (it follows the menu bar's colors), pale while not
    /// connected.
    private static let apostrophe: NSImage = {
        let corners: [(CGFloat, CGFloat)] = [(0.22, 1), (1, 1), (0.815, 0.52), (0.43, 0), (0, 0), (0.04, 0.55)]
        let image = NSImage(size: NSSize(width: 12, height: 18), flipped: false) { rect in
            let box = NSRect(x: (rect.width - 7.3) / 2, y: 2, width: 7.3, height: 14)
            let path = NSBezierPath()
            for (index, (x, y)) in corners.enumerated() {
                let point = NSPoint(x: box.minX + x * box.width, y: box.minY + y * box.height)
                if index == 0 { path.move(to: point) } else { path.line(to: point) }
            }
            path.close()
            NSColor.black.setFill()
            path.fill()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "m’kay"
        return image
    }()

    private var statusText: String {
        switch connector.state {
        case .notSignedIn: return "Not signed in"
        case .signingIn(let code?, _): return "Signing in: code \(code)"
        case .signingIn: return "Signing in…"
        case .connecting: return "Connecting…"
        case .connected(let server): return "Connected to \(server)" + (connector.readOnly ? " (read-only)" : "")
        case .reconnecting(let seconds): return seconds > 0 ? "Offline; retrying in \(seconds) s" : "Reconnecting…"
        case .paused: return "Paused"
        }
    }

    // MARK: - Menu (rebuilt each time it opens, so permissions and state are current)

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        menu.addItem(disabled("m’kay: " + statusText))
        if let problem = connector.problem, !connector.isSigningIn {
            menu.addItem(disabled(problem))
        }
        if case .signingIn(_, let page?) = connector.state {
            menu.addItem(item("Open Sign-In Page Again") { Browser.open(page) })
            menu.addItem(item("Cancel Sign-In") { [connector] in connector.signOut() })
        }
        if setupNeeded {
            menu.addItem(item("⚠︎ Finish Setup…") { [weak self] in self?.showSetup() })
        }
        if connector.isLinked {
            menu.addItem(item("Talk to Your Mac…") { [connector] in Browser.open(connector.accountPage) })
        }

        menu.addItem(.separator())
        let activity = NSMenuItem(title: "Recent Activity", action: nil, keyEquivalent: "")
        let submenu = NSMenu()
        let time = DateFormatter()
        time.timeStyle = .short
        for call in connector.recent {
            submenu.addItem(disabled("\(time.string(from: call.time))  \(call.name)" + (call.refused ? " (refused)" : "")))
        }
        if connector.recent.isEmpty { submenu.addItem(disabled("Nothing yet")) }
        activity.submenu = submenu
        menu.addItem(activity)

        let readOnly = item("Read-Only (Never Send)") { [connector] in connector.readOnly.toggle() }
        readOnly.state = connector.readOnly ? .on : .off
        menu.addItem(readOnly)
        if connector.isLinked {
            if case .paused = connector.state {
                menu.addItem(item("Resume") { [connector] in connector.resume() })
            } else {
                menu.addItem(item("Pause") { [connector] in connector.pause() })
            }
        }

        menu.addItem(.separator())
        menu.addItem(item("Set Up…") { [weak self] in self?.showSetup() })
        let login = item("Start at Login") { LoginItem.set(!LoginItem.enabled) }
        login.state = LoginItem.enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(item("Open Log") { NSWorkspace.shared.open(Paths.logFile) })
        if connector.isLinked {
            menu.addItem(item("Sign Out") { [connector] in connector.signOut() })
        } else if !connector.isSigningIn {
            menu.addItem(item("Sign In…") { [weak self, connector] in connector.signIn(); self?.showSetup() })
        }

        menu.addItem(.separator())
        menu.addItem(item("About m’kay") {
            NSApp.activate(ignoringOtherApps: true)
            NSApp.orderFrontStandardAboutPanel(nil)
        })
        menu.addItem(NSMenuItem(title: "Quit m’kay", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }

    private func showSetup() {
        if setupWindow == nil {
            let window = NSWindow(contentViewController: NSHostingController(rootView: SetupView(connector: connector)))
            window.title = "m’kay"
            window.styleMask = [.titled, .closable]
            window.isReleasedWhenClosed = false
            window.center()
            setupWindow = window
        }
        NSApp.activate(ignoringOtherApps: true)
        setupWindow?.makeKeyAndOrderFront(nil)
    }

    private func disabled(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    private func item(_ title: String, _ action: @escaping @MainActor () -> Void) -> NSMenuItem {
        ActionItem(title: title, action: action)
    }
}

/// A menu item that runs a closure.
private final class ActionItem: NSMenuItem {
    private let handler: @MainActor () -> Void

    init(title: String, action: @escaping @MainActor () -> Void) {
        handler = action
        super.init(title: title, action: #selector(fire), keyEquivalent: "")
        target = self
    }

    required init(coder: NSCoder) { fatalError("not used") }

    @objc private func fire() { MainActor.assumeIsolated { handler() } }
}
