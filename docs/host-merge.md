# Merging the host into the app

A decision and a plan. It replaces `docs/swift-host-study.md`, which asked a
narrower question and answered it against a host a third of the present size.

**The decision: the server moves into `Headroom.app`, and the Python host goes
away.** The LaunchAgent retires with it.

## Why

The study ruled the merge out in its first paragraph, on the grounds that the
ESP32 and the iPhone poll the host whether or not the app runs. That reason
does not hold.

`Headroom.app` sets `INFOPLIST_KEY_LSUIElement`
([project.yml:53](../macos/project.yml#L53)) and registers as a login item
through `SMAppService.mainApp`
(HouseKit's `LaunchAtLogin`). It runs in the
same `gui/$uid` session as the LaunchAgent. Both start at login. Both stop at
logout. **Availability is already equal.** The app is quit only by a deliberate
act or by a crash.

The board is not the reason either. Few installs have one, but the board keeps
working after the merge, because the app runs whenever the Mac runs. Do not
scope this work around board rarity, and do not reduce board support.

Three facts carry the decision.

**1. A server inside the app can hold entitlements. A LaunchAgent cannot.**
[multi-mac.md](multi-mac.md) records what this already cost: the folder
transport cannot live in iCloud Drive, because `~/Library/Mobile Documents` is
TCC-protected and a daemon is refused `listdir` there with no error. CloudKit
and app ownership were the workaround. The same asymmetry applies to `dev_root`
under `~/Documents`, where an app prompts and a daemon is denied in silence.

**2. The version-skew machinery exists only because launchd outlives the app.**
`host_version.py`, the `/health` `version` and `build` handshake,
[HostVersion.swift](../macos/Sources/HostVersion.swift), the "Update host"
button, and the bundle-stamp cache in
[HostController.swift](../macos/Sources/HostController.swift) all serve one
problem. One binary deletes the problem and all of it.

**3. The user sees one process and can stop it.** Today `kill` does nothing,
because `KeepAlive` returns the host within `ThrottleInterval`. A background
service that cannot be stopped and does not appear in System Settings is the
complaint, not the language it is written in.

## What this costs

| Loss | Effect |
|---|---|
| launchd restart after a crash | A source crash stops the menu bar and the board until someone opens the app. |
| Hot patch of a `.py` scraper | A provider format change needs a release. |
| `python3 host/headroom_server.py --port 8738` | [AGENTS.md](../AGENTS.md) tells every agent to verify this way. |

The first loss needs a decision before Phase 1. The third is answered by the
architecture below, and must not be left to Phase 5 to notice.

Note what supervision is worth in practice. 1.9.3 exited non-zero at startup,
and `KeepAlive` with `SuccessfulExit: false` returned it every five seconds
until the user uninstalled the app. Supervision converted one undefined name
into a crash loop. It did not contain it.

## Architecture

Three units, not one.

| Unit | Job |
|---|---|
| `HeadroomHostKit` | Library. HTTP, routing, auth, sources, aggregation, config. |
| `headroom-host` | Headless executable. Runs the library on a port. |
| `Headroom.app` | Runs the library in-process. Owns the lifecycle. |

The headless target is load-bearing, not a convenience. It keeps the AGENTS.md
verify path alive, it gives CI something to run without a GUI session, and it
lets Phase 1 replace the Python LaunchAgent one binary for another before the
in-process move. Two risky changes stay apart.

## Verified constraints

Checked on 2026-08-07, because the plan depends on both.

**The app is not sandboxed.** [Headroom.entitlements](../macos/Headroom.entitlements)
carries `com.apple.security.application-groups` and nothing else, and states
the reason. The in-process server may read `~/.claude`, walk `dev_root`, and
run `git`, `gh`, and `lsof`. The comment in that file names the bundled Python
host and becomes wrong at Phase 5.

Any future move into the App Sandbox ends this design. Treat sandboxing and
this plan as mutually exclusive.

**Installed Claude Code hooks call HTTP, not Python.**
[claude_hooks.py:53](../host/claude_hooks.py#L53) writes `"type": "http"` with
a `http://127.0.0.1:<port>/agents/hooks/claude/<event>` URL. Hooks already
written into `~/.claude/settings.json` keep working after the port, provided
the port stays 8737. Only the installer is Python, and it moves with the rest.

## Decisions to settle before Phase 1

**1. Crash restart.** Choose one: accept manual relaunch, or install a minimal
launchd job whose only job is to relaunch the app. Record the choice in
[product.md](product.md). Do not leave it implicit, and do not rebuild
`KeepAlive` around a process that also draws a menu.

**2. HTTP layer.** Hand-rolled on `Network.framework`, or SwiftNIO. The repo is
deliberately zero-dependency, and [trust.md](trust.md) makes this the only
LAN-exposed surface in the product. Hand-rolled code owns request line and
header parsing, `Content-Length` bodies, keep-alive, read timeouts, connection
caps, and 413/414 limits. Python's `http.server` supplies all of it today.

**3. Keychain identity.** This is the highest user-visible risk in the plan,
above the HTTP layer. Items today carry an ACL that trusts `/usr/bin/python3`.
A signed app is a different code identity, so macOS prompts or denies. Commit
141b814 shows this area already produced `SecurityAgent` loops on background
reads. Define how items transfer before any source is ported, and fail closed.

**4. Contract authority after Python.** [test_contract.py](../host/test_contract.py)
is the present gate and it is written in the language being removed. Promote
[demo_usage.json](demo_usage.json) to the authority and assert the Swift output
against the same fixture. The additive-only rules in [contract.md](contract.md)
stay in force through every phase, because the firmware and older iPhone builds
decode this document for years.

## Phases

**Phase 0. Freeze the contract.** Extend `demo_usage.json` to cover every
section a test asserts. Add the Swift contract test that reads it. Both hosts
satisfy one fixture.

**Phase 1. Library skeleton and headless target, no sources.** Build HTTP,
routing, auth, Bonjour, config read, the byte cache, and `/health`. Every
source proxies to the Python host on a second port. Install the headless
target as the LaunchAgent. The app does not change.
*Gate:* the four AGENTS.md builds pass, and `/usage` matches the Python host.

**Phase 2. Port sources.** One source per commit, each behind a switch. Add
`scripts/diff-hosts.sh` to pull `/usage` from both hosts on one machine and
diff the JSON. Start with `local_servers` and `git_activity`. Finish with
`cursor_usage` and `oauth_usage`.
*Gate:* an empty diff for every ported source.

**Phase 3. Port aggregation.** Port `quota_samples`, `burndown`,
`claude_history`, `daily_burn`, and `pricing`. Copy the Python fixtures without
change.
*Gate:* the Swift tests assert the same numbers as the Python tests.

**Phase 4. Move in-process.** The app links the library and serves 8737. Remove
the LaunchAgent. Remove the bundled Python. Apply the crash-restart decision.
Call `ProcessInfo.beginActivity` so App Nap cannot throttle the poll tick, which
is a silent failure in an `LSUIElement` app that is never frontmost. Migrate
Keychain items.
*Gate:* a clean install and an upgrade over an existing LaunchAgent install both
serve `/usage`, and the board polls without a reflash.

**Phase 5. Remove Python.** Delete `host/`. Rewrite the hook installer in Swift.
Update [AGENTS.md](../AGENTS.md), [host.md](host.md), [setup.md](setup.md), and
[troubleshooting.md](troubleshooting.md). Correct the comment in
`Headroom.entitlements`. Delete the Python row from the support floor in
[product.md](product.md).

The one rule from the study still holds: never sit in a half-and-half state for
long. Two runtimes where both are required is worse than either endpoint.

## Rules that change

- "The merge stays in Python" in [multi-mac.md](multi-mac.md) becomes "the merge
  stays in `HeadroomHostKit`". One implementation, same intent.
- The Python 3.9 floor in [product.md](product.md) disappears. The macOS 14
  floor stays.
- `install-host.sh` and `uninstall-host.sh` lose their reason to exist at
  Phase 4, and with them the `bootout` race in AGENTS.md.
- The stdlib-only exit condition in [product.md](product.md) applies to the
  Swift library instead. The HTTP layer decision is the first test of it.

## What does not change

The wire format, on every phase boundary. The board is a render target and the
phone updates on Apple's schedule, so both can be a year behind the host. A
port is not an occasion to rename a key.
