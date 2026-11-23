# Releasing Headroom

Marketing version lives in [`host/VERSION`](../host/VERSION), hand-bumped.
Apple build numbers are `git rev-list --count HEAD` via [`scripts/version-env.sh`](../scripts/version-env.sh).

**Each coherent set of changes gets one release. Minor and patch never pass 9.**
Increment the patch; at `X.Y.9` roll to `X.(Y+1).0`; at `X.9.9` roll to
`(X+1).0.0`. So 1.1.9 → 1.2.0 and **1.9.9 → 2.0.0** — never `1.10.0`.
`v1.0.10`, `v1.0.11`, and `v1.10.0` are tagged overshoots that stay; the next
release after them takes the roll they were owed.

Because the bump ships whatever is on `main`, one-set-per-release means one
branch per set, merged and bumped one at a time — see
[`AGENTS.md`](../AGENTS.md) for the full working agreement, including what to
check before bumping when several agents share the repo.

Tag releases as `v` + that version (e.g. `1.0.0` → `v1.0.0`). The release
workflow refuses a tag that does not match `host/VERSION`.

Public download URLs (macOS Releases + iOS TestFlight) live in
[`install-links.md`](install-links.md) and are embedded in GitHub Release notes.

## Checklist (first release)

1. **GitHub Actions secrets** — run
   [`scripts/setup-release-secrets.sh`](../scripts/setup-release-secrets.sh)
   (or set the table below in the repo Settings UI). Without these, tags still
   publish an **ad-hoc** `Headroom-macOS.zip` (Gatekeeper warns).
2. **App Store Connect app** — `com.centaur-labs.headroom` (id `6795549853`).
   Set GitHub secret `ASC_APP_ID` to that id. Optional: `ASC_TESTFLIGHT_GROUP`
   (default `Internal`).
3. **Smoke upload** — locally, from a clean checkout at the tag:
   ```bash
   ./scripts/ship-ios.sh --dry-run
   ```
   Exporting a real IPA proves the certificate and profiles before a build
   number is spent. Drop `--dry-run` to upload. Do not export `APPLE_API_*` for
   this: a Developer-role key takes provisioning away from the Xcode account
   and reproduces CI's export failure on your Mac.
4. **Public TestFlight link** — ASC → TestFlight → Public Link → paste into
   [`install-links.md`](install-links.md) → commit.
5. **Cut the tag** — `./scripts/cut-release.sh`

## Cut a release

**A bump to `host/VERSION` on `main` releases itself.** Push a commit that
changes it, and the [Release workflow](../.github/workflows/release.yml) tags,
builds, notarizes and publishes the macOS half with nothing running on your
Mac. That is the whole procedure:

```bash
# 1. bump host/VERSION
# 2. add a "## <version> — YYYY-MM-DD" section to CHANGELOG.md
# 3. commit both and push to main
```

The `gate` job decides what a push means:

| Push to `main` | Result |
|---|---|
| `host/VERSION` already tagged | Nothing. Ordinary commits never publish. |
| New version, changelog section present | Tags `v<version>` and publishes. |
| New version, **no** changelog section | **Fails.** Nothing ships undocumented. |

That last row is the one that matters, because on this path nobody is watching
a terminal. The section also becomes the "What changed" body of the GitHub
Release, so it is read by users rather than filed away.

The tag is created by the publish step rather than pushed from CI on purpose: a
tag pushed with `GITHUB_TOKEN` does not trigger workflows, so a tag-then-build
design would tag and then silently never build.

Hand-cutting still works when you want it, and runs the same changelog guard
through the same script:

```bash
./scripts/cut-release.sh
```

Manual **Actions → Release → Run workflow** builds artifacts without publishing.

## The update feeds

From 2.3.4 the app updates with Sparkle. The macOS job signs the zip and
attaches `appcast.xml` to the release; `SUFeedURL` reads it from
`releases/latest/download/appcast.xml` ([docs/updater.md](updater.md)).

Builds up to 2.3.3 poll `https://updates.centaur-labs.io/latest.json` to learn
a newer one exists, and that is their only way to the first Sparkle build.
That file is
`docs/latest.json`, served by GitHub Pages from `main:/docs`, and the `feed`
job writes it as the **last** step of a release.

Two properties are worth knowing before changing anything near it:

- **It publishes only after the release is green, and only when notarized.**
  A run that fails leaves the feed pointing at the last version that actually
  exists. An ad-hoc signed build is skipped with a `::warning::`, because
  `update-app.sh` checks `spctl` and would refuse it — advertising one offers
  every user an update that cannot succeed, repeatedly, until the next
  release.
- **The commit it makes does not trigger another release.** Pushes made with
  `GITHUB_TOKEN` do not start workflows, the same guarantee the tag path
  already relies on.

So a release that goes green but shows `not publishing the update feed` in the
`feed` job shipped fine and told nobody. Same failure shape as the CloudKit
entitlement gap: grep the log rather than trusting the green tick.

To write the feed by hand — recovering from a red `feed` job, or repointing at
a zip that moved:

```bash
./scripts/write-update-feed.sh
```

It downloads the zip rather than hashing a local one, so it doubles as a check
that the URL a shipped app will fetch actually resolves.

## TestFlight

The iPhone half needs the **Apple Distribution** certificate in
`IOS_DISTRIBUTION_P12`. An App Store Connect key cannot substitute for it: a key
mints *development* certificates only, so without the p12 the export asks for a
distribution identity the runner has never had and fails with `Cloud signing
permission error`. Upgrading the key's role does not help — that was tried on
2026-07-29 with an App Manager key and failed identically. Each such run also
leaves a "Created via API" development certificate behind, against the team's
cap.

With that secret set, a release publishes to TestFlight on its own and the rest
of this section is only a fallback for when CI is unavailable.

Run this from the Mac once the Release has published:

```bash
git worktree add --detach /tmp/ship v1.1.5
```

```bash
cd /tmp/ship && ./scripts/ship-ios.sh
```

Signing comes from the Xcode account on that Mac, which holds the **Apple
Distribution** certificate CI cannot create, so
[`ship-ios.sh`](../scripts/ship-ios.sh) keeps the ASC key out of the archive
and lets `asc` upload under its own stored credential.

It refuses to run unless the tree is clean and `HEAD` is the tag matching
`host/VERSION`. Both numbers Apple sees come from the commit —
`CFBundleShortVersionString` from `host/VERSION`, `CFBundleVersion` from
`git rev-list --count HEAD` — so one extra commit moves the build number and
puts something on TestFlight that no GitHub Release accounts for. Nothing
downstream can withdraw it. Hence the worktree at the tag.

`--dry-run` exports `dist/Headroom-iOS.ipa` and stops, which is the way to
check signing without spending a build number.

**This path is currently blocked**, and not by signing. The iPhone app embeds
the watch app, which on this Mac only archives under `Xcode-beta`
([`AGENTS.md`](../AGENTS.md)), and App Store Connect rejects beta-SDK builds
with `ITMS-90534: Unsupported SDK or Xcode version` — betas may be uploaded,
only Release Candidates are accepted. Stable Xcode refuses the archive outright
(*"watchOS 26.5 must be installed"*) until its watchOS platform is installed:

```bash
xcodebuild -downloadPlatform watchOS
```

CI is unaffected: its runner archives against a release SDK. Which is the other
reason to keep the workflow, rather than the Mac, as the way builds ship.

What the [Release workflow](../.github/workflows/release.yml) does on a `v*` tag:

| Step | Behavior |
|---|---|
| macOS zip | Always. Notarized when signing + ASC secrets exist; else ad-hoc. |
| GitHub Release | Attaches `Headroom-macOS.zip` + notes (incl. TestFlight URL if set). |
| TestFlight | Soft-attempt: `asc publish testflight` → Internal group when ASC secrets + `ASC_APP_ID` exist. |
| IPA asset | Attached to the same Release when the upload ran. |

Manual **Actions → Release → Run workflow** can force notarize / TestFlight
without a tag (artifacts only — no GitHub Release).

Local notarized build (same secrets as env vars):

```bash
export HEADROOM_SIGN_IDENTITY='Developer ID Application: Your Name (TEAMID)'
export APPLE_API_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_XXXXXX.p8
export APPLE_API_KEY_ID=XXXXXX
export APPLE_API_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
./scripts/build-app.sh --release --notarize
```

## One-time Apple setup

### 1. Developer ID (macOS notarization)

1. [Apple Developer](https://developer.apple.com/account/resources/certificates/list) →
   create **Developer ID Application** (needs a CSR from Keychain Access).
2. Install the cert in Keychain, then export as `.p12`.
3. Base64 for GitHub (or let `setup-release-secrets.sh` do it):

```bash
base64 -i DeveloperID.p12 | pbcopy
```

### 2. App Store Connect API key (notarize + TestFlight)

1. [Users and Access → Integrations → Team Key](https://appstoreconnect.apple.com/access/integrations/api)
2. Create a key with **App Manager** (or Admin) access; download the `.p8` once.
3. Note Key ID + Issuer ID.

The key's role is not what fixes iOS signing, and chasing it wastes a day. No
key of any role can mint an **Apple Distribution** certificate — a key creates
*development* certificates and nothing else. So when the runner has no
distribution identity, export fails with `Cloud signing permission error` /
`No profiles for 'com.centaur-labs.headroom' were found` no matter how
privileged the key is; an App Manager key was tried on 2026-07-29 and failed
identically to the Developer one.

Worse: passing the key into an **Automatic**-signing *archive* makes Xcode
mint a fresh `Created via API` development certificate on every run. That is
what walks the team to its certificate cap, after which the archive itself
fails with `Choose a certificate to revoke` and looks for **iOS App
Development** profiles that CI never installed. CI therefore archives with
`--manual-signing` (Distribution identity + named App Store profiles, no API
key on `xcodebuild`) and only uses the key for `asc` upload.

`IOS_DISTRIBUTION_P12` is still required for the Distribution identity. Leftover
`Created via API` development certificates are safe to revoke under
[Certificates](https://developer.apple.com/account/resources/certificates/list)
if the team is already at its cap.

### 3. GitHub Actions secrets

| Secret | Value |
|---|---|
| `MACOS_CERTIFICATE_P12` | base64 of the **Developer ID Application** `.p12` (macOS notarization) |
| `MACOS_CERTIFICATE_PASSWORD` | password used when exporting that `.p12` |
| `IOS_DISTRIBUTION_P12` | base64 of the **Apple Distribution** `.p12` (iOS export) |
| `IOS_DISTRIBUTION_PASSWORD` | password used when exporting that `.p12` |
| `KEYCHAIN_PASSWORD` | any random string (CI temp keychain) |
| `APPLE_API_KEY` | raw `.p8` PEM contents |
| `APPLE_API_KEY_ID` | Key ID |
| `APPLE_API_ISSUER_ID` | Issuer ID |
| `ASC_APP_ID` | App Store Connect app id (`6795549853`) |
| `ASC_TESTFLIGHT_GROUP` | Optional; default `Internal` |
| `MACOS_PROVISION_PROFILE` | Optional; base64 of the **Developer ID** `.provisionprofile` for `com.centaur-labs.headroom.macos` with iCloud. Turns multi-Mac CloudKit on. |

`MACOS_PROVISION_PROFILE` is the one secret whose absence is invisible in a
green run. The release notarizes and publishes exactly as it always did, and
the app it ships reports multi-Mac over iCloud as unavailable on every Mac that
opens it. That is what happened through 1.2.0. The tell is in the build log:

```
note: no HEADROOM_PROVISION_PROFILE — multi-Mac CloudKit is off in this build
```

With the secret set, the same line reads `note: embedded … — multi-Mac CloudKit
is on`. It is deliberately a warning and not a failure, because the alternative
is worse: stamping the restricted iCloud entitlements on with no profile to
authorize them produces an app that signs, notarizes, downloads, and is killed
the moment it launches. See [multi-mac.md](multi-mac.md).

Helper (needs a `gh` token that can write Actions secrets):

```bash
./scripts/setup-release-secrets.sh \
  --p12 ~/Desktop/DeveloperID.p12 \
  --p12-password '…' \
  --api-key ~/.appstoreconnect/private_keys/AuthKey_XXXX.p8 \
  --api-key-id XXXX \
  --api-issuer-id xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### 4. TestFlight public link

1. Create an iOS app in App Store Connect with bundle id `com.centaur-labs.headroom`.
2. Upload a build (`./scripts/build-ios.sh --upload` or a tagged Release).
3. Enable TestFlight; add yourself as an internal tester.
4. Create a **Public Link** group and copy `https://testflight.apple.com/join/…`
   into [`install-links.md`](install-links.md).
5. Ensure an **Apple Distribution** certificate exists for the team (Xcode →
   Settings → Accounts → Manage Certificates).

Missing ASC setup never blocks the macOS zip on a tag — the iOS job soft-skips.

## App Store listing

Canonical copy lives in [`appstore.md`](appstore.md) (name, subtitle, description,
keywords, promo, what’s new, review notes, screenshot plan). Privacy policy:
[`privacy.md`](privacy.md).

Push metadata (same parser as TinySuite):

```bash
python3 ../tiny/scripts/push-metadata.py \
  --bundle-id com.centaur-labs.headroom \
  --metadata-file docs/appstore.md \
  --dry-run
```

## Version map

| Surface | Marketing | Build |
|---|---|---|
| Host `/health` | `host/VERSION` | content fingerprint of shipped `.py` |
| Headroom + widget | same | commit count |
| HeadroomMobile + widget | same | commit count |
| ESP32 firmware | n/a | local `firmware/.build_number` (per flash machine) |
