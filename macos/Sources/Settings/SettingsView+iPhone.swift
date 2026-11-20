import AppKit
import SwiftUI

extension SettingsView {
    var iPhonePane: some View {
        Form {
            iPhoneSection
        }
        .formStyle(.grouped)
    }

    @ViewBuilder
    var iPhoneSection: some View {
            Section {
                Link(
                    HeadroomCopy.openTestFlightInvite,
                    destination: HeadroomCopy.testFlightInvite
                )
            } footer: {
                Text("Install the iPhone app from TestFlight. The Apple Watch app installs with it.")
            }
            Section {
                LabeledContent("Discovery") {
                    Text("Automatic on local Wi‑Fi")
                        .foregroundStyle(.secondary)
                }
                HStack {
                    Button("Copy mobile token") {
                        copyMobileToken()
                    }
                    Spacer()
                    if let mobileTokenMessage {
                        Text(mobileTokenMessage)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                ForEach(MobilePermission.visibleCases, id: \.rawValue) { permission in
                    HStack {
                        Text(permission.title)
                        Spacer()
                        if changingMobilePermission == permission {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Toggle(
                                permission.title,
                                isOn: Binding(
                                    get: { mobilePermissions[permission] },
                                    set: { enabled in
                                        Task {
                                            await setMobilePermission(
                                                permission,
                                                enabled: enabled
                                            )
                                        }
                                    }
                                )
                            )
                            .labelsHidden()
                        }
                    }
                }
            } header: {
                Text(HeadroomCopy.settingsPermissions)
            } footer: {
                Text("Copy mobile token (~/.headroom/mobile-token), open Headroom on iPhone, tap this Mac, paste once. Do not use the host token (that’s for the ESP32). Tailscale names remain available as a fallback.")
            }
    }

    func reloadMobilePermissions() async {
        if let permissions = try? await client.fetchMobilePermissions() {
            mobilePermissions = permissions
        }
    }

    func setMobilePermission(
        _ permission: MobilePermission,
        enabled: Bool
    ) async {
        guard changingMobilePermission == nil else { return }
        changingMobilePermission = permission
        defer { changingMobilePermission = nil }
        var updated = mobilePermissions
        updated[permission] = enabled
        do {
            mobilePermissions = try await client.setMobilePermissions(updated)
        } catch {
            mobileTokenMessage = error.localizedDescription
        }
    }

    func copyMobileToken() {
        guard let value = HostController.mobileToken else {
            mobileTokenMessage = "Start the host first"
            return
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
        mobileTokenMessage = "Copied"
    }
}
