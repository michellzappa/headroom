# Updating the app

How a running `Headroom.app` learns a newer one exists, and replaces itself
with it. Written as a build plan; phases 1 and 2 are inert on their own, so
they can land and be verified before any Swift is written.

## The decision

**No Sparkle.** Not because it is bad — CodexBar uses it well — but because
the two things Sparkle would buy us are already bought, and the one thing it
does badly here is the thing this app needs most.

Sparkle's installer replaces the bundle and relaunches the app on its own
schedule. Headroom's host runs from *inside* the bundle
(`Contents/Resources/host/headroom_server.py`) under a KeepAlive LaunchAgent,
so a bundle swap that does not bracket itself with `bootout`/`bootstrap`
leaves launchd holding the old code and restarting it within seconds. Wiring
that into `SPUUpdaterDelegate` means fighting a framework's lifecycle to
re-implement choreography that [scripts/update-app.sh](../scripts/update-app.sh)
already performs correctly, backup-and-restore included.

The second argument is the trust model. Sparkle's EdDSA appcast signature
exists so you can trust a feed served from a host you do not control. We get
the same property for free: the script pins `TEAM_ID=992N457T8D` and runs
`spctl -a -t exec` plus `codesign --verify --deep --strict` before touching
anything on disk. A compromised feed host cannot produce a bundle notarized
under our team. **The feed does not have to be trusted**, which is what lets
it sit on any dumb static origin — and means we do not have a second private
key to keep safe.

What we give up: delta updates, staged rollouts, install-on-quit, and a UI
users recognise. Acceptable at this size. Revisit if the zip gets large.

Also relevant: `macos/project.yml` has no SPM packages at all today. Sparkle
would be the first, in a repo whose host is deliberately stdlib-only.

## Sparkle review, 2026-11-17

Michell decided that Sparkle is the one updater for all Mac apps. The other
apps now release through the shared workflow in michellzappa/housekit
(`release-mac.yml`) and update through HouseKit's `SparkleUpdater`.
Headroom is the **recorded exception**: it stays on `latest.json` and
`update-app.sh`. The reasons above were checked again on that date:

| Reason | Still true? |
|---|---|
| The host runs from inside the bundle under a KeepAlive LaunchAgent, so a bundle swap must bracket `bootout`/`bootstrap` | Yes. `HostController.swift` writes the agent with `KeepAlive`; the host is `Contents/Resources/host`. Sparkle's installer would swap the bundle under a running agent. |
| No second private key: the team pin in `update-app.sh` makes the feed untrusted by design | Yes. Sparkle would add a per-app EdDSA key to keep safe. |
| No SPM packages | No longer true: Headroom depends on HouseKit by URL. Moving that pin to HouseKit 0.2.0 or later makes Xcode resolve Sparkle's package too, but Headroom does not link `HouseKitSparkle`, so nothing ships. |

Two of three still hold, and the first is the one that decides. Headroom's
`release.yml` also does more than the shared one (the `gate` job that
releases on a `host/VERSION` bump, TestFlight, the feed commit), so it
keeps its own workflow too.

`updates.centaur-labs.io` stays Headroom's GitHub Pages site. The other apps
do not use it: their feeds are the `appcast.xml` on each app's latest GitHub
Release (`housekit/docs/releasing.md` says why).

To migrate later: implement `SPUUpdaterDelegate` so the install is handed to
`update-app.sh` (Sparkle downloads and verifies, the script does the swap and
the agent choreography), or move the host out of the bundle. Either is a
decision for Michell.

## The shape

**The app is a notifier and a launcher. The script does the work.**

`update-app.sh` already downloads, verifies, quits the running app, waits for
it to exit, stops the LaunchAgent, swaps the bundle with `ditto`, restores the
previous copy if the copy fails, strips quarantine, restarts the agent, and
reopens the app. It solves the replace-your-own-bundle problem by not being
the app. The Swift side never needs to.

So the app: polls a feed, compares versions, shows an affordance, and on click
spawns the script detached and gets out of the way.

## What already exists

| Piece | State |
|---|---|
| Verified swap, agent choreography | [scripts/update-app.sh](../scripts/update-app.sh), shipped |
| GitHub Pages on `main:/docs` | enabled, Jekyll off via `docs/.nojekyll` |
| Feed hostname | `updates.centaur-labs.io` → `michellzappa.github.io` (CNAME at Gandi), `docs/CNAME` set |
| Notarized zip per release | [release.yml](../.github/workflows/release.yml) |

The custom domain serves `docs/` at the **root**, not under `/headroom/`. So
`docs/latest.json` answers at `https://updates.centaur-labs.io/latest.json`.

## Phase 1 — the feed

`docs/latest.json`:

```json
{
  "schema": 1,
  "version": "1.4.2",
  "published": "2026-08-01T12:00:00Z",
  "url": "https://github.com/michellzappa/headroom/releases/download/v1.4.2/Headroom-macOS.zip",
  "sha256": "…",
  "size": 12345678,
  "min_macos": "14.0",
  "notes_md": "…"
}
```

Written by [write-update-feed.sh](../scripts/write-update-feed.sh), which is
the only thing that writes it. It **downloads the zip rather than hashing the
one in `dist/`** — that proves the URL a shipped app will fetch actually
resolves, and that what answers is the artifact we hashed. A feed advertising
a 404 is worse than no feed, because the app stops asking.

**The expected Team ID is deliberately not a field.** The app's only real
defence is that `update-app.sh` refuses any bundle not notarized under a team
it was *compiled* with. Putting that team in the feed invites someone to read
it from there one day, which hands the check to whoever controls the feed. For
the same reason `sha256` is an integrity check on the download, not an
authenticity check on the release — a feed that can name the URL can name its
hash too.

Two rules, both borrowed from [contract.md](contract.md) because the failure
mode is identical:

- **Additive only.** Never remove a key, never repurpose one, never narrow a
  type. A build from two years ago still parses whatever CI writes today, and
  unlike `/usage` there is no way to update it out of the problem — a build
  that cannot read the feed is a build that can never be updated again.
- **Every field optional in the decoder**, validated after decoding. This is
  the `fetchUsage()` trap: decoding a document under one `try` with
  non-optional fields means one missing key fails the whole thing. Here that
  costs the update path permanently.

`url` being a field and not a constructed path is the point of the exercise.
Move the zip off GitHub and only this string changes; every shipped build
follows without an update.

## Phase 2 — CI writes it

The `feed` job in [release.yml](../.github/workflows/release.yml), last and
gated on `needs.macos-app.result == 'success'`.

- **Ordering is the fail-safe.** Written after notarization and publication,
  so a red release leaves the feed advertising the last version that actually
  exists rather than a zip that was never uploaded.
- **An ad-hoc signed release must never reach the feed.** `update-app.sh` runs
  `spctl -a -t exec` before touching anything, so advertising a build that was
  not notarized offers every user an update that *cannot* succeed, and goes on
  offering it until the next release. The job checks
  `needs.macos-app.outputs.notarized` and emits a `::warning::` when it skips
  — the same class of invisible failure as the CloudKit entitlement gap in
  [AGENTS.md](../AGENTS.md), where a green run said nothing about whether the
  feature shipped.
- **The self-commit does not recurse.** A push made with `GITHUB_TOKEN` does
  not trigger workflows — the same guarantee the `gate` job already relies on
  for tags. The step looks like a loop and is not.
- **The push retries.** `main` is shared and moves during a release, so a
  rejected push is the normal case; the job rebases and tries again three
  times before failing.
- `permissions: contents: write` is already set at workflow level.

Verify by hand before phase 3 exists: `curl https://updates.centaur-labs.io/latest.json`.

## Phase 3 — the checker

New `macos/Sources/UpdateCheck.swift`.

- **Feed URL from `Info.plist`, not a literal.** The target sets
  `GENERATE_INFOPLIST_FILE: YES`, so add
  `INFOPLIST_KEY_HeadroomUpdateFeedURL` under the `Headroom` target's
  `settings.base` in `macos/project.yml`. A local build can then be pointed at
  a test feed without editing Swift.
- **Version comparison needs numeric-component semantics**, matching the
  script's `sort -V`. A string compare puts `1.4.10` below `1.4.9`, and the
  release history has already been through `1.0.10` and `1.0.11`. Small
  comparator, unit-testable, no dependency.
- Weekly background check plus an explicit "Check for Updates…".
- **Skip the check unless the bundle is at `/Applications`.** This is the
  Homebrew-and-dev-build guard; it stops a second update mechanism fighting
  the first, and stops a build in a worktree offering to overwrite itself.
- A pref to turn automatic checks off. Manual check stays available.

UI home: `appVersionLabel` in [SetupView.swift:143](../macos/Sources/SetupView.swift#L143)
already renders the version, and Settings → About (HouseKit's `AboutSettingsPane`) reads it too. The app is `LSUIElement`, so there is no app menu to hang
"Check for Updates…" from — it belongs next to the version string.

## Phase 4 — the handoff

- `macos/project.yml`: ship `update-app.sh` into `Contents/Resources/`. The
  existing `host` folder reference is synced by `sync-embedded-host.sh`, so do
  not drop it in there; add its own resource entry.
- [build-app.sh](../scripts/build-app.sh) already sanity-checks the bundled
  host around lines 90 and 339. Add the script to that check, so a bundle that
  shipped without it fails the build rather than shipping an app that can
  never update.
- `update-app.sh` gains **`--url <zip>`**. `--version` already exists;
  today both still reconstruct a GitHub path, and taking the URL from the feed
  is what completes the decoupling. Few lines.
- The app spawns `update-app.sh --yes --version X --url Y` detached, then does
  nothing. The script quits the app itself.

Keep one source of truth. The CLI path and the in-app path run the same file;
a second copy in Swift would drift, and the symptom would be an update that
works from the terminal and bricks the LaunchAgent from the UI.

## Phase 5 — tests and docs

The swap cannot run in CI. What can:

- The version comparator, including `1.4.10` vs `1.4.9`.
- Feed decoding against a malformed fixture, a fixture missing every optional
  key, and one carrying unknown future keys. All three must survive.

Then a section in [releasing.md](releasing.md) and a line in
[AGENTS.md](../AGENTS.md) recording that the feed is CI-written after
notarization and that `docs/CNAME` must not be removed.

## Gotchas

| Thing | Why it bites |
|---|---|
| `docs/CNAME` before DNS | Pages 301s `michellzappa.github.io/headroom` to a hostname that does not resolve. Site down at both URLs. DNS first. |
| Jekyll on | One unparseable file fails the build and the feed silently stops updating, with no error naming the file. `docs/.nojekyll` exists for this. |
| Custom domain path | Serves `docs/` at the root, not `/headroom/`. Any hardcoded `/headroom/` path breaks the day the domain lands. |
| Non-optional feed fields | Permanently unupdatable builds. See phase 1. |
| Feed written before notarization | Advertises a zip that does not exist. |
| No `/Applications` guard | Two updaters, or a worktree build overwriting the real app. |
| String version compare | `1.4.10` loses to `1.4.9`. |

## Trust model

Ordered as `update-app.sh` performs them, all *before* anything on disk is
touched:

1. `spctl -a -t exec` — notarized, or refuse.
2. `codesign --verify --deep --strict` — signature intact, or refuse.
3. `TeamIdentifier == 992N457T8D` — ours, or refuse. Someone else's notarized
   app is still notarized; it is just not ours.
4. Bundle `CFBundleShortVersionString` equals the tag's version, or refuse.
5. *(to add)* sha256 equals the feed's, or refuse.

Feed integrity is not part of this. A compromised feed can point at an old
version or a URL that 404s; it cannot cause anything unsigned to be installed.
That is the whole reason the feed can live on static hosting we do not
operate, and the reason there is no second signing key.
