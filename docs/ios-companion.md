# Headroom for iPhone

The iOS companion is a second application target in `macos/Headroom.xcodeproj`.
It reads the same registry-driven `/usage` document as the Mac menu bar app and
ESP32 display. No cloud service or second database is involved.

## Install

1. Prefer the public [TestFlight link](install-links.md) when published.
2. Otherwise build from source (below) or wait for an internal TestFlight invite.

## Pair

1. Mac host must be running (menu bar **Welcome** / LaunchAgent).
2. On iPhone: allow **Local Network**, open Headroom, pick your Mac under
   **Nearby Macs** (or paste a Tailscale / LAN URL).
3. On Mac: **Settings → iPhone pairing → Copy mobile token**.
4. Paste that **mobile token** on the phone → **Connect**.

Do **not** paste the **host token** (`~/.headroom/token`) — that is for the
ESP32 / generic LAN clients. The phone always uses
`~/.headroom/mobile-token`.

## Apple Watch

The watch app rides inside this one and installs with it. It cannot reach the
Mac on its own — the phone forwards what it fetched. See
[docs/watch.md](watch.md).

## Features

- Automatic discovery of nearby Headroom Macs over Bonjour.
- One-tap endpoint selection and the **mobile token** from Mac Settings →
  iPhone pairing. A `.local` hostname, LAN IP, or Tailscale MagicDNS name
  remains available as a fallback.
- Token stored in the iOS Keychain.
- Three tabs: **Usage** (quotas, burndown, daily burn), **Attention** (queue
  and agent answers), **Activity** (Recent feed plus service panels).
- Activity: deploys, commits, Actions, resets; Supabase / Plausible / PostHog
  panels; local servers and Xcode builds — deep links where the source has one.
- Source toggles in Settings: **Providers** and **Integrations** (same catalog
  as Mac — Vercel, Git, Actions, Supabase, Plausible, PostHog, Sentry, Datadog,
  Axiom, local servers, builds). Face ID before stopping a local server.
  Credentials stay in the Mac Keychain.
- Attention summary, agent approvals when granted, and local notifications.
- Home Screen widgets backed by an App Group cache: rings on the small size,
  the combined burndown on the medium one. The Mac runs the same extension in
  Notification Center — one source file, `widget/HeadroomWidget.swift`, built
  for both platforms. What differs is the group id, which macOS prefixes with
  the team, and the freshness: the phone's cache is written after a background
  refresh, the Mac's after every successful poll of its own host.
  - **Every family names every provider it has, in words, before it draws a
    chart.** The wide family drew a canvas and a legend of names, so a cache
    holding a `burndown` key with no curve in it rendered as an empty tile
    with nothing to say why. The reading comes first now and the chart is
    added to it; `charted` tests for a stroke rather than for the key.
  - **A Provider widget can be edited to pick the provider**
    (`widget/HeadroomWidgetIntent.swift`). The original **All providers**
    widget remains a static definition so tiles placed before the picker was
    introduced keep receiving timelines. WidgetKit stores a tile's
    configuration system with its kind and cannot migrate that tile in place,
    so the editable widget deliberately has a new kind.
    The Providers pane still decides which providers exist and in what order,
    and the host still serves the top 3; the tile decides which of those it
    spends its space on, which is a question two tiles on one screen answer
    differently. A new tile starts on the provider closest to running out —
    the one every compact surface leads with — resolved once when the widget
    is added and stored with it, so a tile never wanders to a different
    provider on its own. "All providers" is a choice in the Provider widget
    and is also what the compatibility widget keeps doing. A provider that
    leaves the top 3 leaves the tile drawing the rest, never an empty box.
    App Intents strings are literals in that file on purpose — the metadata
    extractor reads them out of the source at build time, so a `HeadroomCopy`
    constant would reach the picker as nothing.
    - **One intermediate cohort cannot migrate in place.** Versions 2.0.6–2.1.0
      briefly shipped the editable configuration under the original static
      kind. A widget added in that window may freeze again when the static
      definition is restored; remove it and add **Headroom — Provider** to
      retain provider selection. There is no WidgetKit representation that
      preserves both configuration systems under the same kind.
- Best-effort iOS background refresh.
- Pull-to-refresh, including the existing LAN-safe `POST /sync/refresh`.
- iPhone and iPad layouts from one target.

Every mobile operation requires the **mobile token**, a private/Tailscale client
address, the `X-Headroom-Client: ios` header, and its matching Mac-owned
permission: `read`, `refresh`, `sources`, or `servers`. Change the four grants
under Mac Settings → iPhone pairing. Provider credentials and permission
changes remain Mac-only.

## Build

Versions match macOS (`host/VERSION` + git commit count). For a Release IPA /
TestFlight upload see [releasing.md](releasing.md).

**Simulator (no Apple account needed).** Start here if you just want it to
compile:

```bash
./scripts/gen-project.sh
cd macos
xcodebuild -project Headroom.xcodeproj -scheme HeadroomMobile \
  -destination 'generic/platform=iOS Simulator' \
  -configuration Debug CODE_SIGNING_ALLOWED=NO build
```

Two things about that command, neither of which the error messages tell you.
**It builds the watch app too** — `HeadroomMobile` embeds it — so
the toolchain needs a watchOS SDK that resolves, not just one it reports. An
Xcode that fails with *"watchOS 26.5 is not installed"* on a watch destination
fails here as well; point `DEVELOPER_DIR` at one that works:

```bash
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
```

And **do not add `-sdk iphonesimulator`.** It overrides the SDK for the embedded
watch complication too, which then fails with *"'accessoryCorner' is unavailable
in iOS"* — a red herring that has nothing to do with the complication code. The
`-destination` above is sufficient on its own.

**Physical phone.** Unlike the Mac app, this cannot be built unsigned, and the
repo defaults to the maintainer's team and bundle ids. A fork needs both of
these before the device build resolves:

1. `HEADROOM_BUNDLE_PREFIX = com.example.you` in the gitignored
   `macos/Local.xcconfig` ([macos/README.md](../macos/README.md#signing-as-yourself)) → your own
   reverse-DNS prefix on every target. `com.centaur-labs.*` is already
   registered to someone else's team, so Apple will not mint you a profile for
   it. Change the `group.com.centaur-labs.headroom` App Group to match (the
   `.entitlements` files and `Shared/WidgetSnapshot.swift`).
2. `DEVELOPMENT_TEAM = ABCDE12345` (your Team ID) in the same file —
   or `export HEADROOM_TEAM_ID=ABCDE12345`, which wins over it.

Then let Xcode register the ids and mint profiles on first build:

```bash
./scripts/gen-project.sh
cd macos
xcodebuild -project Headroom.xcodeproj -scheme HeadroomMobile \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -configuration Debug -allowProvisioningUpdates build
```

Without `-allowProvisioningUpdates` automatic signing is disabled and the build
stops at `No profiles for 'com.centaur-labs.headroom' were found`. Signing as
the maintainer's team is the only path that works with the defaults unchanged.

A Release IPA / TestFlight upload is `./scripts/build-ios.sh` and needs the
same two settings. Run the phone on the same network as the Mac, and allow
local network access on first use.

Normally the Mac appears automatically under **Nearby Macs**. For Tailscale or
manual fallback, use a connection such as:

```text
http://your-mac.local:8737/usage
```

Copy the **mobile token** from the Mac (Settings → **Copy mobile token**), or:

```bash
cat ~/.headroom/mobile-token
```

The iOS and macOS apps compile from the same `Shared/HeadroomModels.swift`
contract. They remain two platform artifacts in the same Xcode project/release;
an iOS app cannot be embedded inside a macOS `.app`.
