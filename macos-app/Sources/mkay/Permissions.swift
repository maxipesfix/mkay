import AppKit
import ApplicationServices
import ServiceManagement

/// The two permissions the agent apps' automation needs. Both belong to the app: the
/// connector and the CLI run as its child processes, and macOS attributes them to it.
enum Permissions {
    enum Automation { case allowed, denied, notAsked, unknown }

    /// Accessibility: reading and operating the agent apps' windows (ax_native.py).
    static var accessibility: Bool { AXIsProcessTrusted() }

    /// Shows the system prompt (once) and lists mkay in Settings, switched off.
    static func requestAccessibility() {
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
        openSettings("Privacy_Accessibility")
    }

    /// Automation of System Events: the CLI presses keys (paste, Return) through osascript.
    /// macOS answers only while System Events runs, and it quits when idle; then the last
    /// answer is used, so the menu does not ask to finish a setup that is complete.
    static func automation(ask: Bool) -> Automation {
        let target = NSAppleEventDescriptor(bundleIdentifier: "com.apple.systemevents")
        guard let desc = target.aeDesc else { return .unknown }
        let result: Automation
        switch AEDeterminePermissionToAutomateTarget(desc, typeWildCard, typeWildCard, ask) {
        case noErr: result = .allowed
        case OSStatus(errAEEventNotPermitted): result = .denied
        case OSStatus(errAEEventWouldRequireUserConsent): result = .notAsked
        default:  // System Events is not running (procNotFound)
            return UserDefaults.standard.bool(forKey: automationKey) ? .allowed : .unknown
        }
        UserDefaults.standard.set(result == .allowed, forKey: automationKey)
        return result
    }

    private static let automationKey = "automationAllowed"

    /// Asks for Automation now, while the user is at the Mac, rather than during a session.
    static func requestAutomation(completion: @escaping @MainActor (Automation) -> Void) {
        let events = URL(fileURLWithPath: "/System/Library/CoreServices/System Events.app")
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        NSWorkspace.shared.openApplication(at: events, configuration: configuration) { _, _ in
            // The consent prompt blocks until answered: keep it off the main thread.
            DispatchQueue.global().async {
                var result = automation(ask: true)
                if result == .denied { DispatchQueue.main.async { openSettings("Privacy_Automation") } }
                if result == .unknown { result = automation(ask: false) }
                DispatchQueue.main.async { completion(result) }
            }
        }
    }

    static func openSettings(_ pane: String) {
        Browser.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)")!)
    }

    /// Where the app runs from: permissions granted to a copy on the disk image are lost.
    static var runsFromApplications: Bool { isInstalled(Bundle.main.bundlePath) }

    static func isInstalled(_ path: String) -> Bool {
        !path.hasPrefix("/Volumes/") && !path.contains("/AppTranslocation/")
    }
}

enum LoginItem {
    static var enabled: Bool { SMAppService.mainApp.status == .enabled }

    static func set(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            Task { @MainActor in Log.write("Start at login: \(error.localizedDescription)") }
        }
    }
}

enum Browser {
    static func open(_ url: URL) { NSWorkspace.shared.open(url) }
}
