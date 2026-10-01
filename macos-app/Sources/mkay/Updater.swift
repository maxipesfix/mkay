import AppKit
import Sparkle

/// Updates through Sparkle: the feed is appcast.xml on the latest GitHub release (SUFeedURL),
/// checked daily; each update is the notarized disk image, signed with the EdDSA key whose
/// public half is SUPublicEDKey. release.sh publishes both.
///
/// A menu-bar app has no window to show an update in front of, so Sparkle may open its
/// alert behind other apps; the menu then also offers the update until the user sees it.
@MainActor
final class Updater: NSObject, SPUStandardUserDriverDelegate {
    private var controller: SPUStandardUpdaterController!
    /// The version a scheduled check found and the user has not looked at yet.
    private(set) var pending: String?

    override init() {
        super.init()
        controller = SPUStandardUpdaterController(startingUpdater: Self.enabled, updaterDelegate: nil,
                                                  userDriverDelegate: self)
    }

    /// Only an installed, Developer ID build can replace itself: not one on the disk image,
    /// and not an ad-hoc build (Sparkle refuses an update signed differently).
    static let enabled = Permissions.runsFromApplications && hasTeamSignature

    var canCheck: Bool { Self.enabled && controller.updater.canCheckForUpdates }

    func check() { controller.checkForUpdates(nil) }

    // MARK: - SPUStandardUserDriverDelegate

    nonisolated var supportsGentleScheduledUpdateReminders: Bool { true }

    nonisolated func standardUserDriverWillHandleShowingUpdate(_ handleShowingUpdate: Bool,
                                                               forUpdate update: SUAppcastItem,
                                                               state: SPUUserUpdateState) {
        let version = update.displayVersionString
        MainActor.assumeIsolated {
            guard !state.userInitiated else { return }
            pending = version
            Log.write("Update available: \(version)")
        }
    }

    nonisolated func standardUserDriverDidReceiveUserAttention(forUpdate update: SUAppcastItem) {
        MainActor.assumeIsolated { pending = nil }
    }

    nonisolated func standardUserDriverWillFinishUpdateSession() {
        MainActor.assumeIsolated { pending = nil }
    }
}

private extension Updater {
    /// Whether this copy carries a Developer ID signature (a team), as Sparkle requires.
    static var hasTeamSignature: Bool {
        var code: SecStaticCode?
        var info: CFDictionary?
        guard SecStaticCodeCreateWithPath(Bundle.main.bundleURL as CFURL, [], &code) == errSecSuccess, let code,
              SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess
        else { return false }
        return (info as? [String: Any])?[kSecCodeInfoTeamIdentifier as String] != nil
    }
}
