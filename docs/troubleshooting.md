# Troubleshooting

| Symptom | Fix |
|---|---|
| Welcome / host isn’t running | Tap **Start host & keep at login** in the popover |
| Host unhealthy | `tail -f ~/.headroom/logs/headroom.err` (owner-only; it names repos and ports, so read before pasting into an issue) |
| Empty provider | Sign into that app/CLI; enable under Settings → Providers |
| Claude says **Needs sign-in** and macOS keeps asking for the keychain password | The Claude login is dead, so the Keychain item holds a rejected token. Run `claude /login` in Terminal, then refresh Headroom. When macOS asks, choose **Always Allow**. **Allow** grants one read only. Headroom does not read an unchanged item that holds a dead login again, so the prompts stop until the item changes |
| A source stopped updating and you want to know why | `grep -E "FAILING\|recovered\|fix:\|oauth" ~/.headroom/logs/headroom.log`. Each failing source writes one line when it breaks or its error changes, and one line when it recovers. The popover shows the same **fix** under the error |
| Empty integration | Paste its key under Settings → Integrations, then enable the row |
| Extra account missing | Settings → Providers → Library → **Add account** — [setup.md](setup.md#extra-accounts) |
| Gemini flips to “Not updating” after ~1h | OAuth client not found (custom npm prefix / bundled CLI). Install `gemini-cli` where the host looks, or set `gemini_oauth_client_id` / `_secret` in `config.json` — [host.md](host.md) |
| iPhone won’t pair | Confirm **mobile token** (not host token); Local Network allowed — [ios-companion.md](ios-companion.md) |
| Agent requests never reach the phone | Settings → Agents (hooks / gateway on); iPhone grant **Answer coding agents** — [agent-attention.md](agent-attention.md) |
| ESP32 says **NO HOST** | [esp32.md](esp32.md) — the panel names the failing half; `pio device monitor` prints the same |
| Gatekeeper blocks `.app` | Prefer a [notarized Release](https://github.com/michellzappa/headroom/releases); otherwise right-click → Open. Signing: [releasing.md](releasing.md) |
| Restart host | `launchctl kickstart -k gui/$(id -u)/com.centaur-labs.headroom` |
| Build a fresh `.app` | `./scripts/build-app.sh` → `dist/Headroom.app` — [macos/README.md](../macos/README.md) |
| No `.xcodeproj` in the clone | Expected — it is generated. `./scripts/gen-project.sh`, then open it |
| Xcode: **not a valid property list** | Project was regenerated while Xcode held it open. Quit Xcode and reopen; `plutil -lint` the file to confirm |
| Xcode: **watchOS 26.5 is not installed** | Stable Xcode; reopen with `/Applications/Xcode-beta.app` — [ios-companion.md](ios-companion.md) |
| Widget stuck on placeholder | Ad-hoc Mac builds lack a team for the app group — run from Xcode with your team, or a notarized release |
| Board flash fights USB | Host LaunchAgent holds the port — [esp32.md](esp32.md) |
