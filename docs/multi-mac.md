# Two Macs

Headroom on a second Mac should feel like the same app, not a fresh install.
This is how that works, and — more usefully — what it deliberately does not
try to do.

## Most of it was already shared

Quota is account-scoped. Two Macs signed into the same Claude, Codex or Cursor
login read the same percentages off the provider, because the provider is
counting the account and not the machine. Same for Vercel, GitHub, Supabase,
Plausible and PostHog. So the headline numbers on a second Mac were always right,
and none
of them are synced by anything here.

What did not travel was everything around those numbers.

## Three tiers

**Already shared.** Every `pct`, every `resets_in`, every deploy and CI run.
Nothing to do. The only prerequisite on the second Mac is being signed into the
CLIs there, which you do anyway.

**Synced by this feature.** The settings that are the same person's answer on
any machine: which sources are enabled, the order you pinned providers in, the
colours you gave them, and the non-secret half of `config.json` — git authors,
Vercel teams, GitHub org filters, Plausible sites, PostHog projects.

**Never merged, but reported.** Local servers, git commits, the Claude token
log, agent attention events, whether the ESP32 is on this desk. These are
genuinely different per machine and a merged list of them would describe a
computer that does not exist. Each Mac publishes a summary of its own and the
others show it with an owner and an age.

## iPhone connections

The iPhone can keep more than one Mac paired. Each saved computer has its own
endpoint and token; the endpoint is ordinary metadata, while the token stays
in that iPhone's device-only Keychain. Settings → Connection shows the saved
computers and lets the user switch which one supplies live data. The iPhone's
Local servers section names that selected Mac, so a port is never presented as
an unexplained machine-local fact.

This first step is deliberately a switcher, not a merged live feed. Combining
attention events or server rows from several Macs comes next, once each row can
retain its owning endpoint for replies and controls.

## Turning it on

**Settings → Other Macs → Share settings between my Macs**, on each Mac. The
toggle runs a sync round before it answers, so the peer list under it is real
rather than a promise about the next minute.

Off by default. Not because it is risky, but because it writes data to a folder
that leaves the machine, and an app that starts doing that on upgrade — for
every user, including the ones with one Mac and no use for it — has made a
decision that was theirs. It is one switch, and the second Mac is the moment
you go looking for it.

The switch is `icloud_sync` in `~/.headroom/config.json` if you would rather
edit it directly. Leave `icloud_dir` empty for CloudKit; set it to a path to
use the folder transport instead.

Turning it **off** stops this Mac publishing. It does not delete the record:
the other Macs are still reading it, and reaching into iCloud to remove
something on their behalf is not what a local toggle should mean.

**It needs a build signed with the iCloud profile.** CloudKit answers
entitlements, and entitlements need a provisioning profile to authorize them,
so a local `CODE_SIGNING_ALLOWED=NO` build compiles the code but cannot reach
the container. Neither can a notarized release built without the profile, which
is a different problem with the same symptom. Settings distinguishes them:
`MachineCloudSync.isDeveloperIDSigned` separates "you built this yourself" from
"this release shipped without the profile", because only the first is fixed by
downloading a release.

## Turning it on for the first time, once

Five manual steps, none of which anything here can do for you:

1. **Create the CloudKit container** `iCloud.com.centaur-labs.headroom` in the
   Apple Developer portal, under the same team that signs the app.
2. **Create a Developer ID provisioning profile** for
   `com.centaur-labs.headroom.macos` with the iCloud capability, and download
   it.
3. **Point the build at it**: `HEADROOM_PROVISION_PROFILE=/path/to.profile`.
   `scripts/build-app.sh` then copies it to `Contents/embedded.provisionprofile`
   and merges `Headroom-iCloud.entitlements` into the signature.
4. **For releases, set the `MACOS_PROVISION_PROFILE` secret** to the base64 of
   that same file. `scripts/setup-release-secrets.sh --provision-profile` does
   it. The release workflow decodes it, checks its team against the signing
   certificate, and exports `HEADROOM_PROVISION_PROFILE` before
   `scripts/build-app.sh`.
5. **Deploy the CloudKit schema to Production.** Import
   [`macos/Headroom-CloudKit.ckdb`](../macos/Headroom-CloudKit.ckdb) in the
   CloudKit Console (**Import Schema…**), then **Deploy Schema Changes…**
   from Development to Production.

Step 5 looks optional and is not. A Developer ID build is pinned to the
**Production** environment by its profile
(`com.apple.developer.icloud-container-environment`), and CloudKit only
auto-creates record types in **Development**. A container whose Production
schema has no `Machine` type fails every save and every query — which is
exactly what 1.2.1 shipped into. iCloud was correctly entitled, the amber
banner was gone, both Macs said "No other Macs yet", and nothing anywhere said
why. The schema file is the source of truth for what the container holds:
changing what `MachineCloudSync` writes means changing it and redeploying, or
the write fails in Production while a development build still works.

Step 4 is not optional in practice and was missing until 1.2.0, which is why
every release up to and including that one was notarized, installable, and had
iCloud off. Nothing was red. `codesign -d --entitlements -` on the downloaded
app showed the app group and nothing else, and the build log said
`no HEADROOM_PROVISION_PROFILE`. **A previously downloaded copy never gains
CloudKit** — entitlements are sealed into a signature, so it takes a new
release.

To check any copy of the app:

```bash
codesign -d --entitlements - --xml /Applications/Headroom.app | plutil -convert xml1 -o - -
```

That output must carry **five** keys, not three. Alongside the two iCloud keys
and the app group it needs `com.apple.application-identifier`,
`com.apple.developer.team-identifier` and
`com.apple.developer.icloud-container-environment`. Xcode copies those out of
the profile when it signs; `codesign` does not, because it stamps exactly what
the entitlements plist holds. `scripts/build-app.sh` reads them off the
embedded profile instead, so they can never disagree with the profile that
authorizes them.

Without them the container has no application identity to bind to, and
CloudKit fails with **"Trying to initialize a container without an application
ID"**. 1.2.2 shipped in that state: profile embedded, iCloud entitlements
present, `MachineCloudSync.isAvailable` true, Settings showing no warning, and
not one record ever written. Three keys present is the failure that looks like
success.

Without step 3 the build signs exactly as it did before multi-Mac existed. That
is deliberate — see below.

## The trap this is arranged around

`com.apple.developer.*` entitlements are the restricted family, and `codesign`
does not validate them against anything. An app that carries them with no
provisioning profile to authorize them **signs cleanly, notarizes, downloads,
and is killed the moment it launches.**

So the iCloud keys live in their own file and are merged in only when a profile
is supplied. A build with no profile is byte-for-byte the build that shipped
before this feature, minus the feature.

There is a matching trap on the app's side: `CKContainer(identifier:)` on a
binary whose signature lacks the entitlement raises an Objective-C exception,
which Swift cannot catch. Not a failed call that degrades — the process dies.
`MachineCloudSync.isAvailable` reads the entitlement off our own signature with
`SecTaskCopyValueForEntitlement` and is checked before a container is ever
constructed. Removing that check turns every development build into one that
crashes on launch.

## Why CloudKit, and not a folder in iCloud Drive

A folder looks like the simpler answer and is not, for a reason that is
invisible until you try it.

**`~/Library/Mobile Documents` is TCC-protected.** The Python host runs as a
LaunchAgent, and a daemon in that position can create and write files inside
iCloud Drive quite happily while being refused `listdir` on the same directory.
So every Mac publishes its record successfully, none of them can enumerate the
folder, and all of them report no peers. The publish half working is what makes
it so convincing: nothing errors anywhere.

```
$ python3 -c "import os; os.listdir(os.path.expanduser(
    '~/Library/Mobile Documents/com~apple~CloudDocs/Headroom'))"
PermissionError: [Errno 1] Operation not permitted
$ # ...while creating a file in that same folder succeeds.
```

Full Disk Access would fix it and is not shippable: granting it to
`/usr/bin/python3` grants it to every Python script on the machine.

CloudKit is reached through an **entitlement**, and entitlements are not subject
to TCC. That is the whole argument. The app is the only half of Headroom that
can hold one, so the app owns the transport. Everything else follows:

- No Full Disk Access prompt, and no folder permissions at all.
- Push instead of polling. A colour changed on the laptop lands on the desktop
  in seconds rather than at the next interval.
- The same container reaches iPhone and Watch, which a Mac-local folder never
  could.
- No `.icloud` placeholders, no eviction, no conflict copies.

The cost is a **Developer ID provisioning profile** embedded in the .app, and a
CloudKit container. A local build with `CODE_SIGNING_ALLOWED=NO` compiles
without one; CloudKit only answers a properly signed copy.

`NSUbiquitousKeyValueStore` was never an option: documented as App Store
distribution only, and Headroom ships notarized off GitHub Releases.

## The folder transport still exists

Set `icloud_dir` and the folder path is used instead. That is the right answer
for a directory that is **not** TCC-protected — Dropbox, Syncthing, a mounted
share — and it is what the tests exercise, because it needs no entitlement.

Pointing `icloud_dir` at something inside iCloud Drive is the one combination
that will disappoint, and `probe()` detects it and says so rather than letting
it look fine.

## Why it never conflicts

**A machine writes only its own record and only ever reads the others.** One
CloudKit record per machine, `recordName` = machine id, in the private
database; or one file per machine in folder mode. Same shape either way.

No record has two writers, so there is nothing to make a conflict copy of.
There is no shared document to reconcile, no merge on the write path, and no
ordering problem. Each Mac derives its own view at read time and is allowed to
reach a different one — which is also the answer to a Mac that has been asleep
for a week. It is not "behind", it just knows less, and it says so with a
timestamp instead of pretending otherwise.

## Where the line is drawn

The Swift side carries bytes and holds no opinion about them. It fetches
records, posts them to `POST /machines/sync`, and saves back the one record the
host says to publish. The record stays a raw JSON string in Swift — not a
decoded dictionary — because that states the contract the type system can
enforce.

Everything that decides *what* travels and *who wins* stays in Python, where it
is tested. Two implementations of last-writer-wins would eventually disagree,
and the bug would surface as settings quietly reverting on one Mac, which is
about the worst failure this feature could have.

## How settings pick a winner

Per setting, not per file, so pinning Codex above Claude on the laptop does not
drag the laptop's colours along with it. `shared_prefs.py` flattens both stores
into one keyspace:

| Key | Value |
|---|---|
| `sources.enabled.<id>` | bool |
| `sources.accent.<id>` | `#RRGGBB`, or null for the registry colour |
| `sources.order` | pinned provider ids |
| `sources.integrations_order` | pinned Integrations catalog ids |
| `config.<key>` | whitelisted config.json keys |

Every key is always present, with an explicit null for "unset" — a missing key
and a cleared one have to be told apart, or clearing an accent on one Mac would
never travel.

Each key carries the wall-clock time it last changed, and the newest stamp
wins. Ties keep the local value, so a round that adopts nothing writes nothing.
Clock skew between two Macs on the same iCloud account is seconds; these
settings are changed by hand, minutes or months apart.

**The first round is a bootstrap, not a merge.** This is the part worth
understanding, because plain last-writer-wins gets it exactly backwards: on a
brand new Mac every setting is equally unstamped, so a merge would leave both
machines sitting on their own defaults. Instead:

- Folder already has machines in it → this Mac is **joining**, and adopts
  wholesale. That is what opening Headroom on a second Mac is for.
- Folder is empty → this Mac is the **origin**. Its config is stamped now, so
  it wins over the next Mac's untouched defaults.

After that first round it is ordinary last-writer-wins.

Local edits are detected *before* anything is applied. That ordering is the
whole loop-prevention argument: a value this Mac normalizes on the way in — a
pinned order naming a provider that only exists on the other Mac — is not
mistaken for a fresh local edit next round and bounced back. `test_icloud_sync.py`
asserts settling in one round and staying settled.

## What never travels in the record

Credentials, of any kind. `shared_prefs` reaches `config.json` only through
`SHARED_CONFIG_KEYS` in `app_config.py`, so the host token cannot be written to
the shared folder even by accident, and there is a test that greps the produced
files for one.

That rule is absolute and stays absolute, because `icloud_dir` can point the
same code at Dropbox or Syncthing. A secret in the record is a secret in a
plaintext file in a third party's folder.

Also never synced: anything describing one machine's disk or moment —
`dev_root`, `codex_binary`, extra-account credential roots, the mobile pairing
token, local servers, git commits, the Claude token log, attention events.

## Usage counts travel, and are added up

Each record also carries `study`: this Mac's usage-study shard, the counts
from its own Claude Code session logs (`host/study_service.py`). Two Macs run
disjoint sessions, so the honest merge is a sum, and "Your usage" shows one
person instead of one machine without anyone moving a file.

- The shard is stamped with its scan time, not the round's clock, so the
  record only changes when the counts do. A round rescans the logs at most
  once an hour (`SYNC_RESCAN_S`).
- It is never in the beacon. The beacon is also this Mac's row in `/usage`,
  which the phone and the board read; `_peer_view` copies a whitelist, so a
  peer's shard never reaches `/usage` either.
- A shard is tens of KB for a year of sessions, so `/machines/sync` takes
  bodies up to 2 MB rather than the 128 KB hook ceiling.
- The counts are exact. They go only where the record goes: the CloudKit
  private database, or the folder the person chose.

The file export and import in "Your usage" stay as the path for a Mac with
sync off.

## PATs travel, by a different road

Retyping a GitHub PAT on the second Mac is the chore this feature exists to
remove, so the three tokens Headroom owns are stored with
`kSecAttrSynchronizable` and travel through **iCloud Keychain** — end-to-end
encrypted with the user's own keys, never through the record above:

| Service | Synced |
|---|---|
| `com.centaur-labs.headroom.github` | yes |
| `com.centaur-labs.headroom.plausible` | yes |
| `com.centaur-labs.headroom.posthog` | yes |
| `com.centaur-labs.headroom.sentry` | yes |
| `com.centaur-labs.headroom.datadog` (`api-key` / `app-key`) | yes |
| `com.centaur-labs.headroom.axiom` | yes |
| `com.centaur-labs.headroom.supabase` | yes |
| `com.centaur-labs.headroom.host` | **no** — authorizes one Mac's host, and the phone pairs to one Mac |

Not the Claude OAuth credentials either. `Claude Code-credentials` belongs to
the CLI, not to us, and its refresh token rotates — two Macs refreshing one
rotating token independently can invalidate each other.

**Synchronizable items are a separate keyspace, not a flag on a row.** A query
that omits `kSecAttrSynchronizable` defaults to `false` and returns *item not
found* for an item that is plainly in Keychain Access. This is the whole trap:

- Reads use `kSecAttrSynchronizableAny`, which spans both halves. A Mac that
  stored a token before this shipped has a local-only copy that must keep
  working. `KeychainScope` in `macos/Sources/Keychain.swift` names the three
  cases; `keychain.read_token()` is the host's side.
- `/usr/bin/security find-generic-password` is the legacy SecKeychain API and
  cannot be relied on to see synced items. Every host credential read —
  source pollers, first-run `detect_sources` probes, and Zed's internet
  password — goes through `host/keychain.py` (`SecItemCopyMatching`). Probes
  pass `allow_ui=False` so seeding never pops SecurityAgent.
- `TokenStore.adoptSync()` migrates an old local-only token at launch, ordered
  **add-then-delete**. The local copy is the only copy until the synced one is
  confirmed written. Reversing that order loses a token the user pasted in
  months ago and cannot re-derive.
- `TokenStore.save()` tries the synced write first and **falls back to local**
  when it fails. Ad-hoc / unsigned builds (no Team ID) and iCloud Keychain
  being off both refuse `kSecAttrSynchronizable`; without the fallback,
  Settings → Supabase/GitHub/Plausible/PostHog Connect hard-fails with
  "Could not save … token" even though a this-Mac-only item would work.
  Each half is update-or-add on its own; the other half is dropped only
  after the write that should win has succeeded. Deleting both first was
  how a refused sync write used to erase a working local copy before the
  fallback could run.
- The accessible class cannot be a `…ThisDeviceOnly` variant; those are refused
  outright for a synchronizable item.

The unentitled Python host reaching a synchronizable item is not a given, and
was checked on macOS 15 before this shipped: plain `SecItemCopyMatching` finds
them, with no `kSecUseDataProtectionKeychain` needed. Worth re-checking if a
future macOS moves generic passwords further into the data-protection keychain,
because the symptom would be every PAT source going *not connected* at once
while Keychain Access shows the tokens sitting right there.

## Layout

| File | Job |
|---|---|
| `host/machine_identity.py` | stable id, live display name |
| `host/shared_prefs.py` | the flat keyspace, and applying it back |
| `host/icloud_sync.py` | merge, peer list, folder transport |
| `macos/Sources/MachineCloudSync.swift` | CloudKit transport, nothing else |
| `Shared/HeadroomModels.swift` | `MachineSummary` off `machines[]` |
| `macos/Sources/MachinesSection.swift` | the popover rows |

Two endpoints, both loopback-only:

| Route | Job |
|---|---|
| `GET`/`POST /machines/config` | the switch, the mode, who is out there |
| `POST /machines/sync` | peer records in, this Mac's record out |

`/machines/sync` bodies clear the handler's usual 4 KB POST cap — one machine
record is already a few KB of prefs and stamps — so it shares the 128 KB
ceiling with the hook payloads.

`machines[]` is always in `/usage` with at least this Mac's own row, so a
single-Mac install has the same shape as a synced one and no client needs a
special case for sync being off.

## Not done yet

**Chart history.** `quota_samples.jsonl` is a log of what *this Mac watched
happen*, so a laptop that slept all week has a correct `week_pct` and a sparse
burndown chart. The merge is easy — thinned curves, local samples win, remote
fills gaps, max-pct within a bucket since usage only rises inside a window —
but it is a separate change from settings.

**Cross-machine control.** Answering an agent or stopping a server on the other
Mac. The attention ledger is already compare-and-swap guarded for it, but the
transport here is a folder that syncs on its own schedule, which is the wrong
shape for something you expect to happen now.

**The phone.** iPhone and Watch pair to one Mac, and that Mac now knows about
the others, so `machines[]` is already in the payload they fetch. Only the UI
is missing.
