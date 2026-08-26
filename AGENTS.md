# Working on Headroom

Conventions and traps for anyone — human or agent — changing this repo.
Several agents often work here at once, which is what most of this is about.

## Build

XcodeGen owns the project. Never hand-edit `macos/Headroom.xcodeproj`; edit
`macos/project.yml` and regenerate. Always go through the script, which syncs
the embedded host first (bare `xcodegen generate` fails on a fresh clone):

```bash
./scripts/gen-project.sh
```

Anything touching watchOS — including the iPhone app, which embeds the watch
app — needs the beta toolchain on this Mac. The default Xcode reports a watchOS
SDK and even has it on disk, but resolves every destination to *"watchOS 26.5
is not installed"*:

```bash
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
```

Do not pass `-sdk iphonesimulator` to the iOS build under the beta. It
overrides the SDK for the embedded watch complication too, which then fails
with *"'accessoryCorner' is unavailable in iOS"* — a red herring.
`-destination 'generic/platform=iOS Simulator'` alone builds clean.

The green gate, all four targets:

```bash
xcodebuild test -project macos/Headroom.xcodeproj -scheme Headroom -configuration Debug -derivedDataPath macos/.build CODE_SIGNING_ALLOWED=NO
```

```bash
xcodebuild build -project macos/Headroom.xcodeproj -scheme HeadroomMobile -destination 'generic/platform=iOS Simulator' -derivedDataPath macos/.build-ios CODE_SIGNING_ALLOWED=NO
```

```bash
xcodebuild build -project macos/Headroom.xcodeproj -scheme HeadroomWatch -destination 'generic/platform=watchOS Simulator' -derivedDataPath macos/.build-watch CODE_SIGNING_ALLOWED=NO
```

```bash
./scripts/check-glossary-copy.sh
```

**A green suite here can be red on CI, and the gap is your own machine's
data.** Parts of `/usage` only exist when the local state behind them does:
`history` needs `~/.claude` session logs, Supabase and Plausible need keys. A
runner has none of it, so a contract assertion that passes against *your*
served document can fail against a bare one. This has already shipped a red
main once — six `history` keys looked served on a Mac with 400 days of them
and were absent everywhere else.

`docs/demo_usage.json` is the fix and the reason it exists: it keeps the floor
the same on every machine. When a test's coverage depends on a section that
local state can remove, add that section to the fixture and check your work
**against the fixture alone**, which is all CI gets:

```bash
cd host && python3 -c "import test_contract as t; print(sorted(t._demo_doc()))"
```

When several agents share the tree, verify in a worktree at your own commit
rather than in the shared checkout — otherwise you are building someone else's
half-finished work and cannot tell whose failure you are looking at:

```bash
git worktree add --detach /tmp/verify HEAD
```

### Signing overrides, and the export that used to eat them

`macos/Version.xcconfig` is generated. It writes the defaults, then
`#include? "Local.xcconfig"` last, so a contributor's gitignored overrides win
at the moment Xcode reads them. `DEVELOPMENT_TEAM` is pinned *after* that
include in exactly one case — when the caller set `$HEADROOM_TEAM_ID` — which
is how CI and the release scripts outrank a personal override.

That test has to survive a process boundary, and for one release it did not.
`version-env.sh` **exports** `HEADROOM_TEAM_ID` already defaulted to the
maintainer's team, and `sync-embedded-host.sh` sources the script and then runs
it again as a child to write the xcconfig. The child inherited the export and
could not tell our own default from a team someone asked for, so it pinned
every time — meaning on `gen-project.sh`, the one path a contributor takes,
`Local.xcconfig` could never change the team.

Nothing looked wrong, because the pinned value equalled the default. Provenance
now travels separately in `HEADROOM_TEAM_EXPLICIT`, where set-but-empty means
asked-and-nobody-set-it. **If you touch the resolution order, test through
`gen-project.sh`, not by invoking `version-env.sh` directly** — the direct call
is the path that always worked and will happily agree with you:

```bash
./scripts/gen-project.sh && tail -3 macos/Version.xcconfig
```

The include must be the last line. A `DEVELOPMENT_TEAM` below it, on a run
where you set no env var, is the bug back.

## The host LaunchAgent is not yours to repoint

**Read the mode first.** Settings → General → Host has a "Keep the host running
when Headroom is closed" toggle ([docs/host.md](docs/host.md)). It is on by
default and everything below assumes it. When it is off there is no plist and
no job: `Headroom.app` spawns the host as a child, and quitting the app is what
stops it. `launchctl bootout` on a label that does not exist is not a fix, and
neither is writing the plist back.


`~/Library/LaunchAgents/com.centaur-labs.headroom.plist` runs the host MZ
actually uses. It was repointed at agent scratchpad paths five times in one
day, and every time the host died — because the scratchpad had been cleaned up,
or the build in it was mid-edit. A dead host is not cosmetic: the board stops
getting data, the phone stops getting rows, and every Claude session sharing
this Mac loses its Headroom integration at once.

**Verify against your own copy, not the shared service.** The host takes a
port, so nothing has to touch the plist:

```bash
HEADROOM_USB_PORT=/dev/null python3 host/headroom_server.py --port 8738
```

The port keeps you off :8737 and the `HEADROOM_USB_PORT` override keeps you off
`/dev/cu.usbmodem*`, so the board stays with whoever owns it. Kill it when you
are done.

If you genuinely must repoint the LaunchAgent — you are testing the packaged
app, say — point it at a path that will still exist tomorrow, and put it back:

```bash
./scripts/install-host.sh --app=/Applications/Headroom.app
```

Two traps that cost real time when the plist does need touching:

- **`install-host.sh` races its own `bootout`.** It unloads the old job and
  bootstraps the new one immediately, and launchd is asynchronous, so
  bootstrap fails with `5: Input/output error` and leaves nothing running. The
  script reports the failure and exits; the host is simply down. Re-run
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.centaur-labs.headroom.plist`
  after a couple of seconds and it works.
- **The LaunchAgent runs Xcode's Python 3.9**, not whatever `python3` is on
  your PATH. `python3 -m unittest` passing under 3.14 says nothing about
  whether the host will start. Anything newer than 3.9 syntax fails at launch,
  not in the tests.

## Provider credentials Headroom owns a copy of

Headroom does not read a provider's credential store on every poll. It imports
the blob once into `~/.headroom/oauth/`, and `_read_creds_blob` prefers that
copy over the Keychain from then on. Two failures live in that one decision,
and both of them shipped: the copy going stale, in 2.0.0, and the copy being
replaced under it, in every build up to 2.0.8.

**A copy is retired by evidence, not by a date.** Headroom's copy states its
own `refreshTokenExpiresAt`, and dropping the copy when that date passes
catches only the grants that ran out. `claude /login` does not wait for the
date — it rotates the grant, so the copy Headroom holds goes on stating an
expiry days away while the server has already stopped honouring it. Through
2.0.8 that pinned the daemon to a replaced login: every refresh came back
`invalid_grant`, the fresh Keychain item sat one branch away, and the source
read **Needs sign-in** for as long as the stale date lasted. Only the token
endpoint can tell a live grant from a rotated one, so `_bury_grant` writes its
answer down — the copy is expired on disk, and the rejected refresh token is
remembered so the same grant is not imported straight back out of the Keychain
that still holds it.

**Clearing memory is not clearing the pin.** The arm that handled a dead grant
called `_invalidate_oauth_mem()` and said the next poll would re-read. It did
re-read — the same file, which is what the pin is. Anything that means "try
the other stores again" has to change what is on disk.

The user-visible shape of this is one source stuck on **Needs sign-in** with an
age that keeps growing while the provider's own CLI is signed in. Read the
error before anything else; the host reports what the server said:

```bash
curl -s localhost:8737/usage | python3 -c 'import json,sys; print([p for p in json.load(sys.stdin)["providers"] if p["id"]=="claude"])'
```

Do not delete a user's `~/.headroom/oauth/` file to unstick it. That hides the
bug and takes the reproduction with it.

## The board

There is one ESP32 on one desk and it takes one owner at a time. Before you
touch it, **check that nothing else is using it, and stop if something is.**
Do not kill the holder to get your turn.

Known Headroom hardware: the 466×466 Waveshare 1.75″ round unit is
`a4:cb:8f:d6:55:4c` and uses `esp32-s3-175-round`. Its pre-Headroom 16 MB flash
backup is `/Users/mz/Dev/esp32-backups/waveshare-175-a4cb8fd6554c-pre-headroom-20260817.bin`.

```bash
./scripts/flash-esp32.sh
```

That does the check and refuses to race. Use it instead of `pio run -t upload`.

`Headroom.app` serves `/usage` over Wi-Fi by default. Its USB-CDC bridge is
opt-in (`HEADROOM_ENABLE_USB=1`) for travel/offline use; when enabled, it
holds `/dev/cu.usbmodem*` open. Flash while that bridge is running and the
two fight for the port — and **esptool does not fail cleanly.** It can write
part of the app partition and then stop responding, which leaves the board
unbootable.

The failure reads as a hardware fault and isn't:

| What you see | What it actually is |
|---|---|
| `The chip stopped responding` mid-write | Something else owns the port |
| `device reports readiness to read but returned no data` | Same, on the retry |
| Board dark, port still enumerates | Partial app partition — it can't boot |

Recovering a half-written board needs hands: hold **BOOT**, tap **RESET** (or
replug USB), release BOOT, then flash again. OTA cannot save you, because OTA
needs a firmware that boots far enough to bring up Wi-Fi.

To check by hand:

```bash
lsof /dev/cu.usbmodem*
```

**Quitting `Headroom.app` is not enough, and neither is `kill`.** The host
server runs from a `KeepAlive` LaunchAgent, so it is detached from the app
(PPID 1) and launchd restarts it within seconds of any kill. You will watch the
PID change and the port stay busy.

Stop the agent, flash, put it back:

```bash
launchctl bootout gui/$(id -u)/com.centaur-labs.headroom
./scripts/flash-esp32.sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.centaur-labs.headroom.plist
```

Always restore it. The board gets its usage data from that process, so leaving
it unloaded looks exactly like a broken board on the next boot.

## Boot splash art is generated

`firmware/src/boot_max.h` is generated — never hand-edit it. The mask, both
copper tables and the previews all come from one script:

```bash
.venv-shots/bin/python scripts/render_esp32_boot.py --emit-header firmware/src/boot_max.h --out /tmp/boot
```

`--out` writes stills plus an animated GIF of the sequence at the real frame
timings, so the splash can be judged without a reflash. The venv is the one
`scripts/generate_screenshots.sh` builds; it only needs Pillow.

The splash plays on cold boot only — `esp_reset_reason()` gates it to
`POWERON`/`BROWNOUT`, and holding BOOT at power-on skips it. An OTA push or a
watchdog reboot goes straight to the amber ROM checklist, so the dev loop
doesn't pay four seconds an iteration.

`connectWifi()` is called **before** the splash in `setup()` on purpose. The
animation runs while the radio associates, which is time the board spends
anyway. Move it back after the splash and the show starts costing real
time-to-first-data.

`scripts/render_esp32_preview.py` and `render_esp32_boot.py` redraw what the
panel draws at the same logical 448x368, with text through
`scripts/gfx_font.py` — the same 5x7 glyphs and `6*size` metrics Arduino_GFX
blits, not a lookalike in a desktop font. Change layout in
`firmware/src/main.cpp` and change it in the renderer too, or the previews
start lying.

## Versioning

**Writing code and shipping it are two different jobs, and by default only
one of them is an agent's.** Land ordinary commits on `main` — build, test,
commit, done. Do not bump `host/VERSION`, do not tag, do not push a commit
that triggers the release workflow. MZ runs the bump-and-ship pass, in a
separate session, on their own schedule, batching up whatever has landed
since the last one — using the `cleanup` skill, or "Chunking and shipping
someone else's work" below, as the playbook once a batch has grown into
several unrelated things rather than one. The one exception is when MZ
explicitly asks an agent to ship in the same message — "ship this," "cut a
release," "build and push" — which authorizes that session for that pass
and no other.

`host/VERSION` is the marketing version, hand-bumped. Apple build numbers come
from `git rev-list --count HEAD` and are not tracked.

**A bump commit carries two files, not three.** `macos/Version.xcconfig` is
generated by `scripts/version-env.sh --write-xcconfig` and gitignored, so it
never enters a commit however much it looks like it should. Write it anyway —
Xcode reads it — then stage `CHANGELOG.md` and `host/VERSION` and nothing else.
Naming the xcconfig as a third path fails the `git add`.

**Each coherent set of changes gets exactly one release.** Increment the patch.
**Minor and patch are single digits only — never `.10`.** When the patch would
pass 9, roll the minor and reset the patch; when the minor would pass 9, roll
the major and reset both:

| From | Change | To |
|---|---|---|
| 1.1.0 | anything shippable | 1.1.1 |
| 1.1.8 | anything shippable | 1.1.9 |
| 1.1.9 | anything shippable | **1.2.0** |
| 1.9.9 | anything shippable | **2.0.0** |

Never write `1.10.0` or `1.0.10`. `1.0.10`, `1.0.11`, and `1.10.0` are
shipped overshoots that stay tagged where they are; the roll they were owed
happens at the next release off them (`1.0.11` → **1.1.0**, `1.10.0` →
**2.0.0**). `scripts/version-env.sh` and `scripts/ship-inventory.sh` enforce
the single-digit rule going forward.

The number is claimed at merge, not at branch. Several branches sitting on
unmerged bumps is the normal state here, and each one was numbered against the
`main` it forked from — so the second one to land is wrong by the time it
lands. Take the number from `main`'s current `host/VERSION`, and expect to
renumber if someone beats you to it. Nothing downstream can undo a version:
tags, the GitHub Release and TestFlight builds only ever move forward, so
shipping a number strands every lower one still in flight.

Do not bundle unrelated work into one version. A release whose notes read as a
list of things that happened is a release nobody can reason about later.

## Releasing

**This section describes the release pass itself — MZ's job by default, per
Versioning above, not something every agent runs at the end of its own set.**
It stays here because an agent explicitly asked to ship still needs it.

**A bump to `host/VERSION` landing on `main` is the release.** The workflow
tags, notarizes, publishes the GitHub Release, and uploads to TestFlight with
nothing running locally. Ordinary commits to `main` publish nothing — the gate
only fires on a version it has not tagged.

That has one consequence worth stating plainly: **you cannot ship a subset of
`main`.** The bump ships whatever is on `main` at that moment, documented or
not.

**Work on `main`. No branches, no pull requests.** Commit your set and version
it in the same pass, then push:

```
commit your set on main  →  bump + push  →  ships  →  next set
```

The bump is still its own `chore: bump to X.Y.Z` commit and still the last
thing before pushing, but it happens as you go rather than waiting for someone
to merge something. Do not open a PR and do not cut a branch to hold a set;
both were the old shape here and neither is used now.

Because there is no branch to hold work back, **whatever else is sitting
unshipped on `main` ships with you.** Check before you bump:

```bash
git log --oneline $(git describe --tags --abbrev=0)..main
```

Anything listed there goes out under your number, so give it a `CHANGELOG.md`
line even when it is not yours. Uncommitted work in the tree is safe — it does
not ship — which is the other reason to stage paths explicitly rather than
`git commit -a`.

`scripts/cut-release.sh` and the workflow both refuse a version with no
`CHANGELOG.md` section, because that section becomes the release notes.

**A release also writes `docs/latest.json`**, the feed installed copies poll to
find it ([docs/updater.md](docs/updater.md)). The `feed` job does it last, only
after the release is published and only when the build was notarized, and it
commits to `main` — so expect one extra commit per release that you did not
write. It cannot recurse: a push made with `GITHUB_TOKEN` does not trigger
workflows. Do not hand-edit that file; `scripts/write-update-feed.sh` owns it.
And do not remove `docs/CNAME` — every shipped build polls the hostname it was
compiled with, forever, and nothing can reach the old ones to change it.

**The tag appears before the release is green.** The workflow tags early and
notarizes afterwards, so `v1.2.9` existed for several minutes while its build
was still running. Waiting for the tag is not the same as waiting for a release
that worked. When you are shipping sets back to back, the tag is enough to
number the next one — a second bump before the previous tag exists leaves the
gate looking at a version it cannot place — but before you *push* that next
bump, check the macOS job actually went green. If it went red you want to stop,
and a tag that already exists means the recovery is a new patch version rather
than a retry.

## Chunking and shipping someone else's work

Splitting one large uncommitted change into several coherent releases —
because it touches sixty or ninety files and reads as three or four things
that happened to land in the same working tree, not one — is a normal task
here, not an edge case. It has its own failure modes, distinct from the ones
above.

**Green build gates do not prove a partition was complete.** A missing icon
asset, a doc file that never got copied into any set, a test file left
behind — none of these fail `xcodebuild`, because nothing references them at
compile time or the gap is in prose nobody type-checks. The gates prove each
set you *did* ship builds and passes; they say nothing about content you
forgot to include in any set. After every set has shipped, diff the original
dirty working tree against `origin/main` file by file — not `git diff
--stat`, an actual per-file byte comparison, since a summary stat can bury a
handful of real gaps in a wall of expected noise from later commits. Whatever
still differs and isn't explained by something that shipped *after* your
snapshot is a gap. Ship it as one more small release; do not fold it in
disguise.

**Host tests must run with an isolated `HOME`.** `unittest discover` reads
`~/.headroom/config.json` if you let it, meaning a test asserting the
*default* value of a setting can pass or fail depending on what this specific
Mac has configured — not on whether the code is correct. Always:

```bash
T=$(mktemp -d); HOME="$T" python3 -m unittest discover -p "test_*.py"
```

A green run without this proves the suite agrees with your machine, not that
it agrees with a clean one.

**A test asserting `date.today()` against code given an explicit `tz=` is a
timer, not a test.** It passes locally right up until the run lands between
that timezone's midnight and the runner's — `Europe/Berlin` is two hours
ahead of the `UTC` GitHub Actions runs on, so any CI run between 22:00 and
midnight UTC hits a day the runner has not reached yet. Assert against
`datetime.now(the same tz the code uses)`, never against the system clock.

**`git apply` on a patch mixing text and binary hunks fails atomically** —
none of it lands, including the text hunks that would have applied cleanly
on their own. Screenshots, icons, and other binary diffs need a direct file
copy into the target worktree, not a patch; keep them out of the `git diff`
you feed to `git apply` and copy them separately.

**When a bare `git cherry-pick` chain needs a bump *between* two commits you
already applied in sequence**, `git reset --hard` back to the earlier
commit, bump there, then re-apply what came after — do not bump after both
land and try to sort it out with two changelog sections on one version.

**Merging someone else's PR into a tree that moved past it** (new sources,
new files, renamed functions) is a hand rebase, not a checkout-and-merge.
Read their diff in full before touching anything: a same-day change on
`main` may have solved the identical problem their PR set out to solve,
sometimes with a weaker fix, sometimes a stronger one — compare the two and
keep whichever is actually correct rather than defaulting to what already
landed. Credit the original author in the commit trailer and the CHANGELOG
line regardless of how much hand-editing the merge took.

## Working alongside other agents

Assume someone else is committing to this repo right now, to `main`, which is
where everyone works.

- **Append only.** Never `--amend`, `rebase`, `reset`, or force-push `main`.
  Re-check `git log -1` immediately before any history rewrite; HEAD may not be
  where you left it. An amend that lands on someone else's commit silently
  replaces their message and folds your changes into their commit.
- **Pull before you push, and expect to.** Everyone shares one branch, so a
  rejected push is the normal case and not a problem. `git pull --rebase` only
  on commits you have not pushed; once yours are public, merge.
- **Stage your own hunks.** A shared working tree accumulates other agents'
  edits in files you also touched. `git commit -a` sweeps them into your commit.
  Check `git status` and stage paths explicitly; for a file with both your work
  and theirs, stage only your hunks and put their working copy back.
- **Regenerate the project after pulling.** A new file under `Shared/` will not
  be in someone else's generated `.xcodeproj`, and the failure reads as a
  missing type rather than a stale project.
- **`CHANGELOG.md` is the hottest file here.** Add your bullets, do not
  reformat around them.

## Copy

User-facing chrome lives in `Shared/HeadroomCopy.swift`, mirrored by
`docs/glossary.md` and by `LABEL_*` in `firmware/src/main.cpp`.
`scripts/check-glossary-copy.sh` fails the build on banned phrasings and on
alarm colour in the quota and burndown views. Add new surfaces to its search
path when you create them.

Ring and pace semantics are a cross-platform contract: `docs/rings.md`, with
`Shared/HeadroomRings.swift` as the implementation and the firmware mirroring
its constants. Changing one means changing all of them.

Attention rollup scoring is the same kind of contract: `docs/attention.md`.
Weights and ages stay hardcoded product policy — do not add an Attention
Settings pane. Gateway prefs live under Coding agents; see
`docs/agent-attention.md`.

## Contracts, access, and standing decisions

Five docs exist so these are lookups rather than judgment calls. Read the one
that matches before you change the thing it governs.

**[docs/contract.md](docs/contract.md) — changing `/usage`.** Additive only:
never remove a key, never repurpose one, never narrow a type. This is not
style. `fetchUsage()` decodes the whole document under one `try`, and nine
fields on that path are non-optional, so one missing key blanks the entire
popover rather than the one card it belongs to. The phone
updates on Apple's schedule and the board updates when someone finds a cable,
so both can be a year behind the host. That file also inventories every
constant mirrored across Python, Swift and C++, and which of them are actually
enforced (three) versus held together by a comment (four).

**[docs/trust.md](docs/trust.md) — adding a route.** Four classes: Mac-local
(anything naming credentials or config), ambient read, scoped control, and
agent control. Pick the class first, then write the handler. A route that can
cause code to run does not ship under the same gate as one that changes a
display preference. `SECURITY.md` is the user-facing half; if the two
disagree, fix `SECURITY.md` first.

**[docs/product.md](docs/product.md) — what to build.** What Headroom is, the
line under the agent gateway (it answers requests, it does not originate work),
what earns a Setting versus staying hardcoded, retention as a user asset, the
stdlib-only exit condition, and the one genuinely undecided question: whether a
burndown chart is what a Mac watched or what happened to a pool. Decide that
before implementing the multi-Mac history merge.

**[docs/metering.md](docs/metering.md) — adding a meter.** Seven kinds, of
which four are implemented: a meter declares whether it is a `window`, a
`grant`, an `overage`, a `calendar`, a `balance`, a `rate` or a `seat`, and
that decides what its numbers mean and which mark draws it. Reach for it
before adding another `cost_*` or `_remaining_usd` field to a provider
payload, because that is how the first three forms arrived and each one cost
five hand-written integrations while inheriting no ring, no burndown and no
history. Two rules it is worth knowing without reading: **a kind earns its
place by having a level or a headroom that means something** (attribution has
neither and is deliberately not a meter), and **anything printing a dollar
says whether the figure was observed or estimated**, because nobody audits a
percentage against a card statement and everybody audits a dollar.

**[docs/host-merge.md](docs/host-merge.md) — changing the host or its
lifecycle.** The decision is made: the server moves into `Headroom.app` as a
Swift library, and the Python host and its LaunchAgent go away. Read it before
you invest in the split, and before you argue the ESP32 needs a daemon. It does
not: the app is `LSUIElement` and starts at login in the same session, so
availability is already equal. The reasons for the merge are entitlements, the
version-skew machinery, and one process a user can stop. Ordinary work on
`host/` continues until the phases land, but the wire format is frozen at every
phase boundary, so a port is not an occasion to rename a key.

## Multi-Mac

Settings sync between Macs over CloudKit, one record per machine, written by
its owner and read by everyone else ([docs/multi-mac.md](docs/multi-mac.md)).
Four rules are load-bearing if you touch it:

- **The folder transport cannot live in iCloud Drive.**
  `~/Library/Mobile Documents` is TCC-protected, and the host is a LaunchAgent:
  it can create and write files there and is refused `listdir` on the same
  directory. Every Mac publishes, none can enumerate, nothing errors. This is
  why the transport is CloudKit and why the app owns it — an entitlement is not
  subject to TCC and a daemon cannot hold one. `icloud_dir` still selects the
  folder, which is correct for Dropbox or Syncthing and wrong for iCloud Drive.
- **A machine writes only its own record.** That is the entire reason there are
  no conflicts. Anything that writes a peer's record, or a shared one,
  reintroduces the problem the design exists to avoid.
- **Never construct a `CKContainer` without checking
  `MachineCloudSync.isAvailable` first.** On a binary whose signature lacks the
  entitlement — which is every local build — the initializer raises an
  Objective-C exception that Swift cannot catch, so the app dies at launch
  instead of degrading. The check reads the entitlement off our own signature.
- **The iCloud entitlements are not in `Headroom.entitlements`.** They are
  restricted, `codesign` does not validate them, and an app carrying them with
  no provisioning profile signs, notarizes, downloads and is killed on launch.
  `Headroom-iCloud.entitlements` is merged in by `scripts/build-app.sh` only
  when `HEADROOM_PROVISION_PROFILE` supplies one. No profile, no keys, and the
  artifact matches what shipped before the feature.
- **In CI that profile comes from the `MACOS_PROVISION_PROFILE` secret**, and
  its absence is invisible in a green run. Through 1.2.0 the workflow never set
  the variable, so every published release was notarized and had iCloud off.
  Grep the build log for `multi-Mac CloudKit is off` before believing a release
  can sync. Entitlements are sealed into a signature, so an already-downloaded
  app never gains CloudKit — it takes a new release.
- **The merge stays in Python.** `MachineCloudSync.swift` carries bytes and
  holds no opinion about them. A second implementation of last-writer-wins
  would drift, and the symptom would be settings quietly reverting on one Mac.
- **`app_config.SHARED_CONFIG_KEYS` is a whitelist, not a blocklist.** The file
  it reads holds the host token. A new config key is local until someone adds
  it there on purpose.

Per-machine facts — local servers, git commits, attention events — are
*reported* with an owner and never merged. A merged list would describe a
computer that does not exist.

Testing note: the folder transport is what the suite exercises, because it
needs no entitlement. CloudKit itself is only reachable from a signed build, so
a green `xcodebuild` says the code compiles and nothing about whether the
container answers.

## Layout

| Path | What |
|---|---|
| `host/` | Python host, stdlib only, serves `/usage` |
| `macos/` | Menu bar app + `project.yml` (every target) |
| `ios/` | iPhone companion |
| `watch/` | Watch app + complications ([docs/watch.md](docs/watch.md)) |
| `widget/` | One widget source, built for iOS and macOS |
| `Shared/` | Models, copy, palette, rings, chart math — compiled by several targets |
| `firmware/` | ESP32 ([docs/esp32.md](docs/esp32.md)) |
