import BackgroundTasks

enum MobileBackgroundRefresh {
    static let identifier = "com.centaur-labs.headroom.refresh"

    static func register() {
        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: identifier,
            using: nil
        ) { task in
            guard let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            handle(refreshTask)
        }
    }

    static func schedule() {
        guard UserDefaults.standard.object(forKey: "backgroundRefreshEnabled") == nil
                || UserDefaults.standard.bool(forKey: "backgroundRefreshEnabled")
        else { return }
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(_ task: BGAppRefreshTask) {
        schedule()
        let boxedTask = SendableRefreshTask(task)
        let work = Task {
            do {
                // Before the fetches, so WCSession can finish activating while
                // we wait on the Mac — same reason the foreground path
                // activates before `store.refresh()`. Without this, a cold
                // background wake never forwards and the wrist stays on
                // whatever the last open of the phone app pushed.
                await MainActor.run { WatchBridge.shared.activate() }
                let client = MobileHeadroomClient(
                    endpoint: MobileConnection.endpoint,
                    token: MobileTokenStore.read() ?? ""
                )
                let snapshot = try await client.fetchAndArchiveUsage()
                let widgetSnapshot = HeadroomWidgetCache.save(snapshot)
                await MainActor.run { WatchBridge.shared.push(widgetSnapshot) }
                await MobileNotifications.notifyIfNeeded(snapshot.attention)
                await MobileNotifications.notifyIfNeeded(resets: snapshot)
                boxedTask.value.setTaskCompleted(success: true)
            } catch {
                boxedTask.value.setTaskCompleted(success: false)
            }
        }
        task.expirationHandler = {
            work.cancel()
        }
    }
}

private final class SendableRefreshTask: @unchecked Sendable {
    let value: BGAppRefreshTask

    init(_ value: BGAppRefreshTask) {
        self.value = value
    }
}
