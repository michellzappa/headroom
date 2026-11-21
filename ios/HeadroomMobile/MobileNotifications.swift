import Foundation
import UserNotifications

enum MobileNotifications {
    static func installDelegate() {
        UNUserNotificationCenter.current().delegate = NotificationDelegate.shared
    }

    static func requestAuthorization() async {
        _ = try? await UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .badge, .sound])
    }

    static func notifyIfNeeded(_ attention: Attention?) async {
        guard UserDefaults.standard.bool(forKey: "notificationsEnabled"),
              let attention,
              attention.isWarning else { return }
        let key = "lastNotifiedAttentionFingerprint"
        guard UserDefaults.standard.string(forKey: key) != attention.fingerprint else {
            return
        }

        let content = UNMutableNotificationContent()
        content.title = attention.isCritical ? "Headroom needs attention" : "Headroom warning"
        content.body = attention.summary ?? "Open Headroom for details."
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "headroom-attention-\(attention.fingerprint.hashValue)",
            content: content,
            trigger: nil
        )
        if (try? await UNUserNotificationCenter.current().add(request)) != nil {
            UserDefaults.standard.set(attention.fingerprint, forKey: key)
        }
    }

    /// A quota pool that came back early — on Codex, usually a reset credit the
    /// reader spent. Shares the "is this new" rule with the Mac
    /// (`ResetAnnouncer`) so the two devices agree on which grants are news.
    ///
    /// Rides the same `notificationsEnabled` toggle as attention: a second
    /// switch for one event a week is a settings screen nobody can hold in
    /// their head.
    static func notifyIfNeeded(
        resets snapshot: UsageSnapshot
    ) async {
        guard UserDefaults.standard.bool(forKey: "notificationsEnabled") else {
            return
        }
        let defaults = UserDefaults.standard
        let titles = Dictionary(
            uniqueKeysWithValues: (snapshot.providers ?? []).map {
                ($0.id, $0.title ?? $0.id.capitalized)
            }
        )
        let pending = ResetAnnouncer.pending(
            burndown: snapshot.burndown,
            providerTitles: titles,
            seen: { defaults.bool(forKey: $0) }
        )
        for grant in pending {
            let content = UNMutableNotificationContent()
            content.title = ResetAnnouncer.title(grant)
            content.body = ResetAnnouncer.body(grant)
            content.sound = .default
            let request = UNNotificationRequest(
                identifier: grant.key, content: content, trigger: nil
            )
            if (try? await UNUserNotificationCenter.current().add(request))
                != nil {
                defaults.set(true, forKey: grant.key)
            }
        }
    }

    // The singleton has no mutable state. NSObject protocol conformances are
    // not annotated Sendable, so state this invariant explicitly for Swift 6's
    // global-actor checking.
    private final class NotificationDelegate: NSObject,
        UNUserNotificationCenterDelegate, @unchecked Sendable {
        static let shared = NotificationDelegate()

        func userNotificationCenter(
            _ center: UNUserNotificationCenter,
            willPresent notification: UNNotification,
            withCompletionHandler completionHandler:
                @escaping (UNNotificationPresentationOptions) -> Void
        ) {
            completionHandler([.banner, .sound])
        }
    }
}
