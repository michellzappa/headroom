# The data contract

`GET /usage` is the only thing four codebases agree on. The menu bar, the
widget, the phone, the Watch and the board all render one document produced by
one Python host, and none of them ship on the same schedule. This file is the
rule for changing that document without breaking a client that is already in
the field.

It is about *shape*. [docs/trust.md](trust.md) covers who may call a route,
[docs/rings.md](rings.md) covers what the numbers mean.

## Why this needs a rule at all

Three of the five clients update independently of the host:

| Client | How it updates | Skew you can get |
|---|---|---|
| Menu bar | ships the host inside `Headroom.app` | bounded — `HostVersion` catches it |
| Widget / Watch | same bundle as the Mac app | none beyond the above |
| iPhone | TestFlight / App Store, on Apple's schedule | **months** |
| ESP32 | a human holding a USB cable | **years** |

`host_version.py` already solves the first row. It fingerprints the shipped
host — a sha256 over the flat `.py` and `VERSION` files, first 12 hex — and
`macos/Sources/HostVersion.swift` computes the same value over the bundled
copy, both pinned to a golden vector. That catches an old LaunchAgent still
running under a new app.

It does nothing for the bottom two rows, and those are the ones that matter.
A phone from six months ago talking to a host from today has no way to know it
is behind, and no way to say so.

## The rule: additive only, and it is load-bearing

**Never remove a key. Never repurpose a key. Never narrow a type.** Add new
keys and let old clients ignore them.

This is not style advice. It is the only thing standing between a schema edit
and a blank screen, because of how the Swift side decodes:

```swift
func fetchUsage() async throws -> UsageSnapshot {
    return try JSONDecoder().decode(UsageSnapshot.self, from: data)
}
```

One decoder, one `try`, whole document. `UsageSnapshot`'s own fields are all
optional, so a *top-level* key going missing degrades gracefully. The nested
structs are where it bites: `HeadroomModels.swift` has **39 non-optional
decoded fields**, and exactly one hand-written `init(from:)` in the file. Nine
of the 39 sit on the `/usage` path — `ActivityItem.id`, `DailyBurnDay.date`,
`GitHubRun.id`, `PlausibleSite.domain`, `PostHogProject.id`, `QuotaProviderInfo.id`,
`SupabaseLint.name`, `SupabaseProject.ref`, `SupabaseService.name`,
`SyncSource.id` — and the other 30 are on the agent-event path, which decodes
separately and fails separately.

Every one of the nine is an identity field, which is what makes this feel safe
and is exactly why it is not: identity fields are the ones a refactor renames.

So:

```jsonc
// by_day rows lose "date" in some future host
{ "by_day": [ { "claude": 12.0 } ] }
```

### Mixed activity history

`activity_history` is additive and optional. It is a mixed-source cadence
series: Claude's local session history is combined with daily burn evidence
from every quota source. The `level` is an evidence ramp, not a conversion
between minutes and quota percentage points. Native details remain on each
sparse day for the app; `levels` is the compact full-window array used by the
ESP32.

```jsonc
{
  "activity_history": {
    "source": "mixed",
    "start": "2026-07-01",
    "levels": [0, 1, 2, 0, 4],
    "days": [
      { "date": "2026-07-02", "level": 1,
        "sources": ["codex"], "burns": { "codex": 1.5 } }
    ]
  }
}
```

`DailyBurnDay.date` is `String`, not `String?`. Synthesized decoding throws.
`fetchUsage()` throws. The user does not lose the burndown chart — they lose
**the entire popover**, with an error that names a date field nobody was
looking at.

Two consequences worth stating plainly:

- **A required key is a permanent commitment.** Adding one is cheap; the host
  starts emitting it and old clients ignore it. Removing one is a breaking
  change to every client ever shipped.
- **When you add a field a client will treat as required, make it optional in
  Swift anyway** and default it at the use site. The decoder cannot tell you
  which of the two you meant, so it assumes the expensive one.

### A worked example: superseding a key without removing it

`burndown.*.forgiven` was the burn a granted reset wiped out — one run, drawn
faint, present only on a window a grant had opened. `burndown.*.history`
replaced it: the same curve, but spanning every window in the last four days
rather than needing a grant to exist, which is what stops a rolled window from
drawing a chart with one point in it.

`forgiven` is still emitted, and by the rule above it always will be. A phone
two versions back and a board nobody has held a cable to still read it and
still get their ghost. New clients read `history` and fall back:

```swift
pool.history ?? pool.forgiven
```

That fallback is the whole cost of the rule, and it is two words. The
alternative — deleting `forgiven` because nothing in *this* checkout reads it —
is a silently degraded chart on every surface that updates on someone else's
schedule.

`burndown.*.boundaries` arrived the same way and is worth reading as a second
example, because the key it supersedes is one nobody thought was carrying the
job. `history` climbs at every window boundary, and clients square that climb
into a vertical riser at the instants named by `resets`. But `resets` is
grants only — a provider handing back a week it had already taken. A Claude
session simply rolling on schedule is not in there, so it had no cut to square
against and came out as a diagonal between whatever two samples survived
thinning: a two-hour ramp where the chart should read as a step.
`boundaries` names every one of them, grants included, and `resets` stays
exactly what it was. Clients read it through `historyRisers`, which falls back
to `resets` for a host that predates the key.

`activity[].needs_attention` is a third shape: a key that exists to overrule
a client-side derivation. Every surface used to decide the Attention queue
from `status` alone, which works while one word can carry the answer. It
cannot for the GitHub inbox — an assignment a year old is still `assigned`,
still belongs in the feed, and stopped being attention months ago. The host
now states the verdict and the clients read
`hostNeedsAttention ?? ActivityStatusStyle.resolve(status).needsAttention`,
so a host that predates the key keeps the old behaviour. Absent means "ask
status", not `false` — which is why the field is optional in Swift and
defaulted at the use site rather than at the decoder.

The board is the exception the deprecation window above describes. Its `gpts`
key had one writer and zero readers, so it was replaced outright by `hist` /
`rsts` rather than carried: there was no shipped firmware to break.

## What is missing: a version the payload states out loud

Today the document carries no schema version. Compatibility is maintained
entirely by the convention above plus two hand-rolled shims:

- `device_view.LEGACY_PROVIDER_IDS` keeps emitting flat `claude` / `codex` /
  `cursor` blocks for boards flashed before `providers[]` existed. ~300 bytes
  to keep an un-reflashed board on the desk.
- Every top-level key on `UsageSnapshot` being optional, so a *newer* client
  against an *older* host draws blanks instead of erroring.

Drawing blanks is the part that should change. A phone that renders empty
cards is indistinguishable, to the person holding it, from a broken Mac.

**The fix is one integer.** `/usage` gains:

```jsonc
{
  "contract": 3,          // bumped when clients must be told
  "host": "1.2.7",        // marketing version, already there via /health
  "build": "a1b2c3d4e5f6" // fingerprint, already there
}
```

and each client pins the lowest `contract` it can render. Older than that, the
client says *"This Mac is running an older Headroom than this app needs"* and
names the version to update to. Newer than the client knows, it renders what it
understands and says nothing, because additive-only guarantees that is safe.

Bump `contract` when, and only when, a client that does not know about the
change would show something **wrong or empty**. Adding a field nobody requires
is not a bump. Changing what `pct` means is.

### Deprecation window

A legacy shim like `LEGACY_PROVIDER_IDS` is not forever, and "forever" is the
default unless a number is written down. The rule:

| Surface | Owed |
|---|---|
| iPhone / iPad | two minor versions, or six months, whichever is longer |
| ESP32 firmware | **until the shim costs more than a kilobyte or blocks a change** |
| Menu bar / widget / Watch | nothing; it ships with its host |

The board gets the loosest rule on purpose. Reflashing needs someone at the
desk with a cable, and `flash-esp32.sh` exists because that operation can brick
the thing. Cheap compatibility is worth more than clean code here.

When a shim is finally dropped, say so in `CHANGELOG.md` under **Removed**,
and name the last version that could talk to it.

## Constants that live in more than one language

This is the other half of the contract, and the one that drifts silently.

| Constant | Home | Mirrored in | Checked by |
|---|---|---|---|
| `MAX_DEPLOYS` / `MAX_COMMITS` / `MAX_SERVERS` / `MAX_SOURCES` | `firmware/src/main.cpp` | `host/device_view.py` | **a comment** |
| `MAX_PROVIDERS` (3), `MAX_POOLS` (3) | `firmware/src/main.cpp` | `host/device_view.py` | **a comment** |
| `MAX_HISTORY_POINTS` (16) / `MAX_GRANT_MARKS` (4) | `host/device_view.py` | firmware `MAX_HIST_PTS` / `MAX_GRANTS`, `scripts/render_esp32_preview.py` | `scripts/check-mirrored-constants.sh` ✅ |
| `FOCUS_LIMIT` (3) | `host/sources_config.py` | firmware `MAX_SLOTS`, menu bar, widget | **a comment** |
| Ring geometry + pace semantics | `Shared/HeadroomRings.swift` | `firmware/src/main.cpp` | [docs/rings.md](rings.md), by hand |
| `historyFraction` (0.15) | `Shared/BurndownChartMath.swift` | firmware `HISTORY_REACH_PCT` | **a comment** — the checker compares Python to C++, and this pair is Swift to C++ |
| Chrome copy | `Shared/HeadroomCopy.swift` | `docs/glossary.md`, `LABEL_*` in firmware | `scripts/check-glossary-copy.sh` ✅ |
| Host build fingerprint | `host/host_version.py` | `macos/Sources/HostVersion.swift` | golden vector, both sides ✅ |
| Boot splash tables | `scripts/render_esp32_boot.py` | `firmware/src/boot_max.h` | **generated** ✅ |

Three rows are enforced. Four are held together by a comment that says
"mirror of", which works exactly until someone changes one side at 1am.

The repo already knows the answer, twice: `boot_max.h` is generated rather than
mirrored, and the host fingerprint is pinned by a golden vector in both
languages. Extend that. A single `contract.json` emitting a firmware header, a
Swift file and a Python module would delete this table's unenforced half —
and it is the same idea as `sources_config.py`, which is the best thing in the
codebase, just carried past Python's edge.

Until that exists, the rule is: **change a mirrored constant and grep for its
name across `firmware/`, `host/`, `Shared/` before you commit.** Every one of
them appears in at least two languages under the same name, on purpose.

## The board's projection is not a second contract

`host/device_view.py` is a *trim* of `/usage`, not a parallel document.
~30KB becomes ~4KB, which is invisible over Wi-Fi and the difference between a
2.6s stall and a responsive UI over USB CDC at 115200 baud.

Two rules keep it a projection rather than a fork:

- **It only ever removes.** No key in the device view means anything different
  from the same key in `/usage`. If the board needs a value computed
  differently, that computation belongs in the host document where every client
  gets it.
- **Nulls are dropped, not passed.** ArduinoJson cannot distinguish an absent
  key from a null one, and the firmware's `.isNull()` guards treat them
  identically. A null that survives into the device view is a field the board
  will silently read as zero.

The two non-usage fields are additive host control state for the board's next
poll, not a second source of usage data. `device_effect` is a command envelope:
older firmware ignores it and flashed firmware consumes each id once. `display`
is the panel settings Mac Settings → Desk display holds — brightness in panel
units, whether resets are celebrated, whether the boot animation plays, and
which source pages BOOT cycles through. The values are *effective*: night
dimming is decided on the host, so a dimmed brightness arrives already dimmed
and the board never learns why. Firmware that predates the block never asks
for the key; firmware that has it mirrors the block to NVS and keeps the last
answer when a host stops sending one. Rings/Pace and the lower pane are not in
it on purpose — they are board-side gestures, and a setting with two owners
snaps back sixty seconds after you change it.

The board also never picks anything. The host chooses which three providers are
in `focus`, in pinned order, enabled only — so the desk, the menu bar and the
widget cannot disagree about which three. **The board is a render target.** Any
feature that requires the firmware to decide something is a feature that
belongs in the host.

## Checklist for changing `/usage`

1. Is this additive? If not, stop and reread the top of this file.
2. If a client would show something wrong or blank without knowing about the
   change, bump `contract` and raise the minimum on the clients that care.
3. Does the board need it? Add it to `device_view.py` and check the caps.
4. Does it touch a mirrored constant? Grep all three languages.
5. Does it add a required Swift field? Make it optional and default it instead.
6. `host/test_contract.py` and `macos/Tests/ContractTests.swift` are the
   acceptance test that the document still has the shape everything expects.
   Run them from both sides — a shape change that only one language notices is
   the exact failure this file exists to prevent.

```bash
cd host && python3 -m unittest discover -p "test_*.py"
```
