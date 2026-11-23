# Headroom for the menu bar

Native macOS 14+ companion. The Release `.app` **bundles the Python host** —
first launch’s Welcome sheet starts it automatically and installs a login item.

- Status item: thin remaining-quota meters for **enabled** providers + attention pip
- Popover modes: **Usage** (rings, burn, spend), **Attention**, **Activity**
- Notification Center widget: the same extension the iPhone runs, rings on the
  small size and the combined burndown on the medium one
- Welcome / setup sheet when the host is down or on first open, asking about
  **Providers** and **Integrations** separately
- Settings: General, Providers, Integrations, Sync, Telemetry; host /
  mobile tokens and integration keys on their leaves

Product install overview: [../README.md](../README.md). First-run sources and
tokens: [../docs/setup.md](../docs/setup.md).

## Easiest path

Download `Headroom-macOS.zip` from GitHub Releases, open the app, confirm
providers on Welcome. Notarized builds open without Gatekeeper workarounds —
see [docs/releasing.md](../docs/releasing.md).

**Updating.** The app updates itself with Sparkle, from the appcast on the
latest GitHub Release. The host runs from a staged copy in
`~/Library/Application Support/Headroom/host`, so Sparkle can replace the app
while the host runs. See [docs/updater.md](../docs/updater.md).
[`scripts/update-app.sh`](../scripts/update-app.sh) still replaces an
installed `Headroom.app` by hand:

```bash
./scripts/update-app.sh --check
./scripts/update-app.sh
```

## Build from source

```sh
./scripts/build-app.sh          # embeds host → dist/Headroom.app
open dist/Headroom.app
```

Version is `host/VERSION` + git commit count (`./scripts/version-env.sh`).

**The widget needs a team on the signature.** App and extension share their
cache through an app group, and on macOS a group id carries the team id — so a
plain ad-hoc build is denied the container and the widget draws its empty state
forever. Notarizing is not what fixes that, the signature is:

```sh
export HEADROOM_SIGN_IDENTITY='Developer ID Application: You (TEAMID)'
./scripts/build-app.sh --release --sign     # signed, not notarized
./scripts/install-local-release.sh          # → /Applications, host repointed
```

Running from Xcode with automatic signing works too. `install-local-release.sh`
takes `dist/Headroom.app` whenever it carries a team and never re-signs it —
ad-hoc re-signing an installed copy is the same bug arriving later, and it looks
like a broken widget rather than a lost entitlement.

### Debug build (host from clone)

```bash
./scripts/install-host.sh
./scripts/gen-project.sh          # embeds host + writes Headroom.xcodeproj
cd macos
xcodebuild -project Headroom.xcodeproj -scheme Headroom \
  -configuration Debug -derivedDataPath .build build
open .build/Build/Products/Debug/Headroom.app
```

**Foreground try** (no login item): `./scripts/install-host.sh --foreground`  
**Uninstall LaunchAgent:** `./scripts/uninstall-host.sh` (`--purge` wipes `~/.headroom`)

## Opening the project in Xcode

A fresh clone contains no `.xcodeproj`: `macos/project.yml` is the source of
truth and XcodeGen writes the project from it, so `macos/Headroom.xcodeproj`
is gitignored. On a new Mac:

```bash
brew install xcodegen
./scripts/gen-project.sh
open macos/Headroom.xcodeproj
```

Go through the script rather than calling `xcodegen generate` yourself.
`project.yml` lists `macos/host` as a source directory, and that folder is a
gitignored copy of the host made by `sync-embedded-host.sh` — on a fresh clone
bare xcodegen stops at a missing source directory, which does not point at the
real cause. The script syncs first.

Anything touching the watch app needs the beta toolchain, and that includes the
iPhone app, which embeds it. Stable Xcode reports a watchOS SDK and still
resolves every watch destination to *"watchOS 26.5 is not installed"*:

```bash
open -a /Applications/Xcode-beta.app macos/Headroom.xcodeproj
```

Regenerate after any pull that adds files. A new file under `Shared/` will not
be in last week’s project, and the failure reads as a missing type rather than
a stale project. **Quit Xcode before regenerating** — replacing the project
underneath a running Xcode produces *"The project “Headroom” is not a valid
property list"* on a file that is perfectly valid.

## Signing as yourself

Only needed when Xcode actually signs — running on your own iPhone or Watch,
notarizing, TestFlight. Unsigned builds (`build-app.sh`, the test commands)
skip signing entirely.

Create `macos/Local.xcconfig` — gitignored, survives pulls and regeneration:

```xcconfig
DEVELOPMENT_TEAM = ABCDE12345

// Optional — only iOS / watchOS device builds need it, because Apple will
// not register the maintainer's com.centaur-labs.* App IDs to your team:
// HEADROOM_BUNDLE_PREFIX = com.example.you
```

Put overrides there and nowhere else — `macos/Version.xcconfig` is regenerated
on every build. Device-build details: [docs/ios-companion.md](../docs/ios-companion.md).
Contributor overview: [CONTRIBUTING.md](../CONTRIBUTING.md).

## Screenshots

Regenerate ESP32 / macOS / iOS assets from `docs/demo_usage.json`:

```sh
./scripts/generate_screenshots.sh
```
