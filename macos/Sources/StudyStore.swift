import AppKit
import Foundation

/// State behind the "Your usage" window.
///
/// Separate from `UsageStore` on purpose. That one polls every minute for the
/// life of the app because three bars in the menu bar depend on it. This one
/// runs only while its window is open: the host scan behind `/study` reads the
/// whole session-log tree, and nobody needs it refreshed while the window is
/// closed.
@MainActor
final class StudyStore: ObservableObject {
    @Published private(set) var snapshot: StudySnapshot?
    @Published private(set) var errorMessage: String?
    @Published private(set) var isSavingHandle = false
    /// Set for a couple of seconds after Copy, so the button can say so.
    @Published private(set) var copiedCard = false

    /// The first scan takes a few seconds. Poll quickly until it lands, then
    /// slowly: the host only rescans when the logs changed and five minutes
    /// have passed, so a faster loop would read the same document.
    private static let scanningInterval: Duration = .milliseconds(1500)
    private static let settledInterval: Duration = .seconds(30)

    private var loop: Task<Void, Never>?

    /// `/study` names habits and reads this Mac's logs, so the host serves it
    /// to loopback only. Against a remote host the window says so instead of
    /// showing a 403.
    var isLocalHost: Bool {
        HeadroomClient.isLoopback(HeadroomClient.currentEndpoint)
    }

    func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.load()
                let scanning = self.snapshot?.isScanning ?? false
                let failed = self.errorMessage != nil
                try? await Task.sleep(
                    for: scanning || failed
                        ? Self.scanningInterval : Self.settledInterval)
            }
        }
    }

    func stop() {
        loop?.cancel()
        loop = nil
    }

    func load() async {
        guard isLocalHost else {
            errorMessage = nil
            return
        }
        do {
            snapshot = try await HeadroomClient().fetchStudy()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Returns a message to show when the host refused the name.
    func saveHandle(_ text: String) async -> String? {
        isSavingHandle = true
        defer { isSavingHandle = false }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            _ = try await HeadroomClient().setStudyHandle(
                trimmed.isEmpty ? nil : trimmed)
            await load()
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    func copyCard() {
        guard let text = snapshot?.cardText else { return }
        let board = NSPasteboard.general
        board.clearContents()
        board.setString(text, forType: .string)
        copiedCard = true
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(2))
            self?.copiedCard = false
        }
    }
}
