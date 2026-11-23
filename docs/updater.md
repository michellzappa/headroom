# Updating the app

How a running `Headroom.app` learns that a newer one exists and replaces
itself.

## The decision

**Sparkle, through HouseKit, like every other Mac app.** Michell decided on
2026-11-17 that Sparkle is the one updater for all Mac apps. Headroom was the
recorded exception until 2.3.4. The exception existed for one reason: the host
ran from inside the bundle (`Contents/Resources/host`) under a KeepAlive
LaunchAgent, and Sparkle would replace the bundle under the running host.

2.3.4 removed that reason. The host now runs from a staged copy outside the
bundle, so Sparkle can replace `Headroom.app` at any time.

## The staged host

`HostController.stagedServer()` copies `Contents/Resources/host` to
`~/Library/Application Support/Headroom/host/<build>`, where `<build>` is the
host fingerprint (`HostVersion.build`). Both lifecycles run that copy: the
LaunchAgent plist points at it, and the app-owned child is spawned from it.
After the new host starts, `pruneStagedHosts` removes the other copies.

What happens on an update:

1. Sparkle downloads the zip, checks the EdDSA signature and Apple's code
   signature, quits the app, replaces the bundle and opens the app again.
2. The host keeps running from its staged copy through the swap.
3. The new app reads `/health`, sees a host build different from the one it
   bundles, and restarts the host from a new staged copy
   (`UsageStore.checkHostVersion`). That was already the skew path; nothing
   new.

**Migration.** Builds before 2.3.4 pointed the LaunchAgent into the bundle. At
launch, `ensureHostRunning` checks `launchAgentRunsFromBundle` and reinstalls
the agent against the staged copy. An agent that runs a host from a clone
(`scripts/install-host.sh`) is not touched.

## The feed

`SUFeedURL` is the appcast on the latest GitHub Release:

    https://github.com/michellzappa/headroom/releases/latest/download/appcast.xml

`.github/workflows/release.yml` signs `Headroom-macOS.zip` with the
`SPARKLE_PRIVATE_ED_KEY` secret, checks the signature against the
`SUPublicEDKey` compiled into the build, writes `appcast.xml` and attaches it
to the release. Only a notarized build gets an appcast.

The key pair came from `housekit/scripts/sparkle-key.sh michellzappa/headroom`.
The private key is in the secret and in the login keychain (service
`https://sparkle-project.org`, account `michellzappa/headroom`). A lost private
key means every installed copy stops updating, so keep the keychain item. The
public key is `SPARKLE_PUBLIC_ED_KEY` in `macos/project.yml`.

Headroom keeps its own `release.yml` and does not use HouseKit's shared
`release-mac.yml`. Its workflow also does the `gate` job (release on a
`host/VERSION` bump), TestFlight, and the old feed below.

## The old feed, for builds before 2.3.4

Builds up to 2.3.3 poll `https://updates.centaur-labs.io/latest.json` (GitHub
Pages on `main:/docs`) and install with the `update-app.sh` in their own
bundle. That path must keep working until no such build is left, because it is
the only way those copies reach a Sparkle build. So:

- The `feed` job still writes `docs/latest.json` on every notarized release.
- `scripts/write-update-feed.sh` and `scripts/update-app.sh` stay in the repo.
  `update-app.sh` also stays useful by hand (`--check`, `--version`).
- Do not remove `docs/CNAME`. Every old build polls the hostname it was
  compiled with.

`update-app.sh` installs the new app and bootstraps the LaunchAgent that still
points into the bundle. The new app then moves the agent to the staged copy at
its first launch (see Migration above).

## Trust

Sparkle refuses an update unless the EdDSA signature matches the public key in
the running app and the new bundle's code signature matches the old one. The
old path's guarantee was the Team ID pin in `update-app.sh`; that still guards
builds before 2.3.4.

## What Sparkle does not cover

- **Copies outside /Applications** do not update themselves. `SparkleUpdater`
  turns itself off there, so a build from Xcode or the mini never offers to
  overwrite the installed app.
- **Ad-hoc builds** get no appcast.
- **The host merge** ([host-merge.md](host-merge.md)) deletes the staged copy
  with the rest of the Python host. Sparkle does not change.
