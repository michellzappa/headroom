import AppKit
import Foundation
import UniformTypeIdentifiers

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
    /// The outcome of the last friend action, in words. Refusals from the host
    /// arrive here verbatim ("that is your own card").
    @Published private(set) var friendMessage: String?
    @Published private(set) var macsMessage: String?

    /// A shard is counts for months of sessions. Far above a real one, far
    /// below anything worth reading into memory.
    private static let maxShardBytes = 4 * 1024 * 1024

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

    // MARK: Friends

    /// True when the card was accepted, so a caller can clear its field.
    @discardableResult
    func addFriend(_ text: String) async -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        do {
            let result = try await HeadroomClient().addStudyFriend(card: trimmed)
            let name = result.friend?.name ?? "friend"
            friendMessage = result.updated
                ? "Updated \(name)." : "Added \(name)."
            await load()
            return true
        } catch {
            friendMessage = error.localizedDescription
            return false
        }
    }

    /// One click for the usual case: the card is on the clipboard.
    func addFriendFromClipboard() async {
        guard let text = NSPasteboard.general.string(forType: .string),
              !text.isEmpty
        else {
            friendMessage = HeadroomCopy.studyClipboardEmpty
            return
        }
        await addFriend(text)
    }

    /// Returns a message when the host refused the name.
    func setAlias(_ id: String, _ alias: String) async -> String? {
        let trimmed = alias.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            try await HeadroomClient().setStudyAlias(
                id: id, alias: trimmed.isEmpty ? nil : trimmed)
            await load()
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    func removeFriend(_ id: String) async {
        do {
            try await HeadroomClient().removeStudyFriend(id: id)
            friendMessage = nil
            await load()
        } catch {
            friendMessage = error.localizedDescription
        }
    }

    // MARK: Your other Macs

    /// Save this Mac's counts as a file to carry to another Mac.
    func exportCounts() async {
        let mac = snapshot?.machines.first(where: \.thisMac)?.name ?? "Mac"
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.json]
        panel.nameFieldStringValue = "headroom-counts-"
            + mac.filter { $0.isLetter || $0.isNumber || $0 == "-" } + ".json"
        panel.message = HeadroomCopy.studyExportPanelMessage
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let data = try await HeadroomClient().exportStudyShard()
            try data.write(to: url, options: .atomic)
            // Exact counts: keep the file private to this account.
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600], ofItemAtPath: url.path)
            macsMessage = "Saved \(url.lastPathComponent)."
        } catch {
            macsMessage = error.localizedDescription
        }
    }

    /// Add the counts another of your Macs saved.
    func importCounts() async {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        panel.allowsMultipleSelection = false
        panel.message = HeadroomCopy.studyImportPanelMessage
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let size = (try FileManager.default.attributesOfItem(
                atPath: url.path)[.size] as? Int) ?? 0
            guard size <= Self.maxShardBytes else {
                macsMessage = HeadroomCopy.studyFileTooLarge
                return
            }
            _ = try await HeadroomClient().importStudyShard(
                Data(contentsOf: url))
            macsMessage = HeadroomCopy.studyMacAdded
            await load()
        } catch {
            macsMessage = error.localizedDescription
        }
    }

    func removeMachine(_ id: String) async {
        do {
            try await HeadroomClient().removeStudyMachine(id: id)
            macsMessage = nil
            await load()
        } catch {
            macsMessage = error.localizedDescription
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
