# Backlog

What is queued, roughly in the order it earns its keep. None of it blocks a
release.

Last reviewed 2026-08-26, against 2.1.0. Every line count and every claim
below was re-measured on that pass. Two items were finished; their outcomes
are recorded under the section they left, where the next person would
otherwise re-open them.

## The one decision that reorders this list

[host-merge.md](host-merge.md) says the server moves into `Headroom.app` as a
Swift library and `host/` is deleted at Phase 5. Nothing has started. Phase 0
is done in substance — `macos/Tests/ContractTests.swift` reads
`docs/demo_usage.json`, so both sides now satisfy one fixture — and Phase 1 is
the next thing.

**That decision contradicts the first item under Structural.** Splitting
`headroom_server.py` into three Python modules is work the merge throws away.
Either the merge starts and the Python split is dropped, or the merge is
deferred long enough that the split pays for itself. Do not do both.

The recommendation is Phase 1: the library skeleton, HTTP, routing, auth,
Bonjour, config read, byte cache and `/health`, with every source proxying to
the Python host on a second port. It is a bigger commitment than the split and
it is the one that ends somewhere.

Phase 0 residue, whichever way that goes: `contract`, `machines` and
`services_order` are absent from `demo_usage.json`, so no test asserts them
against the floor CI actually gets.

## Structural

All three files roughly doubled since the last review. The argument for
splitting them is not that they are untidy; it is that each grew that much
without gaining a seam.

**Split `headroom_server.py`.** 3226 lines, up from 1500. Four jobs: the HTTP
handler, the `/usage` document builder, attention scoring, and the poll loop.
Read the section above before starting — this is the item the host merge
deletes.

- `host/http_api.py` gets `Handler` plus the auth and permission gates. It
  already talks to the rest only through `rollup()`, `publish()`, and the
  `_refresh_*` helpers, so this is a move, not a redesign.
- `host/usage_doc.py` gets `_compute_doc`, `_flatten_*`, `_build_activity`,
  `_bodies`. This is the harder half: the flatteners reach into module state.
- `host/attention.py` gets `_build_attention` and its weights. Self-contained
  once the doc builder is out. File split only — scoring stays product policy
  (`docs/attention.md`), not a Settings surface.
- The poller and `main()` stay.

`test_contract.py` passing unchanged is the whole acceptance test. Do it in
three commits, not one.

**Split `HeadroomModels.swift`** (3188 lines, up from 1489). Decodables, then
the computed views over them (`focusProviders`, ring math), then the copy
helpers. Unaffected by the host merge, which is the reason it now outranks the
Python split: the merge cannot make this file smaller, and the file is the
first one a Swift contributor opens.

**Split `firmware/src/main.cpp`** (5851 lines, up from 2919). It was one
translation unit carrying panel bring-up, Wi-Fi, USB CDC, JSON parsing and
every screen. It now also carries three board layouts — `esp32-s3-18`,
`esp32-s3-216` and `esp32-s3-175-round` — which is what doubled it. Board
variance is exactly the axis the drawing code and the transport should be
separated along, so this is no longer the low-priority item it was: a fourth
panel lands in the same file as the first three.

## Hygiene

- **Pin CI actions by SHA.** `@v4`, `@v5` and `@v2` are mutable tags, in both
  `ci.yml` and `release.yml`. A public repo running on `macos-latest` with
  `contents: write` on the release job deserves pinned actions. Unchanged since
  the last review.
- **Issue templates.** One bug form that asks for host version, `/health`
  output, and which provider. Most reports will be "provider X went blank".
  `.github/ISSUE_TEMPLATE/` still does not exist.
- **Correct the mirrored-constants table in [contract.md](contract.md).** It
  says three rows are enforced and four are held together by a comment. That is
  now wrong in the safe direction: `test_contract.py` compares eight firmware
  constants against `host/device_view.py`, and
  `scripts/check-mirrored-constants.sh` covers six. The table understates the
  cover, which is the kind of error that gets a checker written twice.

*Done since the last review:* the Python floor is tested — `ci.yml` runs the
host suite on 3.9 and 3.12. Phase 5 of the host merge deletes the floor
entirely, so the matrix entry retires with `host/`.

## Contract and access

Written up in [contract.md](contract.md), [trust.md](trust.md) and
[product.md](product.md). The docs landed first on purpose — each of these is a
separate release, and the rule each one implements is now stated somewhere the
next person can find it.

- **Show the contract mismatch.** `contract` ships in `/usage`, `/health` and
  the board projection, and `UsageSnapshot.contractSatisfied` answers the
  question. Nothing draws the answer yet: the only callers are three assertions
  in `ContractTests.swift`. What is left is the banner on the phone and the
  Mac, with copy naming the version to update to.
- **Generate the mirrored constants.** The stopgap landed —
  `scripts/check-mirrored-constants.sh` plus the contract tests make drift loud
  for eight of them. Drift is still possible where no checker reaches:
  `FOCUS_LIMIT` is mirrored into firmware `MAX_SLOTS`, the menu bar and the
  widget by comment alone, and `historyFraction` is a Swift-to-C++ pair the
  Python-to-C++ checker cannot see. The fix is unchanged: one `contract.json`
  emitting a firmware header, a Swift file and a Python module. `boot_max.h`
  and the `HostVersion` golden vector are the two precedents already in the
  repo.
- **Decide the transport for `agents`.** Approving a command that runs on the
  Mac still rides a plaintext bearer token with Face ID enforced only by the
  client. `/agents/tasks` accepts any private-range caller holding the `agents`
  grant; `_is_private()` already separates Tailscale CGNAT from RFC1918, so
  option 1 in [trust.md](trust.md) remains one predicate.
- **A clear-history control.** The ledger prunes at 30 days
  (`agent_events.RETENTION_S`), which was the urgent half. The remaining half
  is a button — deleting a SQLite file with the host stopped is not a thing to
  ask of anyone, and it is the natural home for a "forget this session" too.
- **Export and import history.** A new Mac still costs you every chart, and the
  charts are spread across five files with three formats:
  `~/.headroom/claude_history.json`, `daily_burn.json`, `quota_samples.jsonl`,
  `quota_resets.jsonl` and `attention.sqlite3`. Consolidating them into the
  SQLite that already exists is what turns this from a project into a feature.
  See [product.md](product.md#history-is-a-user-asset).

*Done since the last review:* the non-optional Swift field audit. `/usage` now
decodes through hand-written initializers where every field is
`decodeIfPresent` and every list goes through `decodeLossyArrayIfPresent`
(`Shared/LossyDecode.swift`). Sixteen non-optional decoded fields remain and
all of them are identity keys — `id`, `date`, `ref`, `name`, `domain` — inside
those lossy lists, so a bad element drops itself instead of blanking the
popover. That is the end state; do not reopen it by making an identity key
optional.

## Product

- **Provider fixture tests.** Every quota source parses a vendor file that can
  change without notice, and there is still no fixture directory under `host/`.
  Checked-in fixtures per provider would turn a silent blank card into a red
  test. This is the item the source count keeps making more expensive.
- **First-run without a provider.** `detect_sources.py` landed and solved the
  common half: a Claude-only machine no longer polls empty Codex and Cursor.
  The stated case is still exactly as it was, and now has an address —
  `detect_sources.suggested_enabled()` turns *all* quota sources on when it
  detects none, deliberately, so the UI can show sign-in errors. Someone who
  has none of them sees three errors and no explanation.
- **Board reconnect.** Wi-Fi to USB CDC failover works and the copy improved:
  the board keeps the HTTP reason when USB also fails, and says
  `no wi-fi, no usb host` rather than one blank cause. What is still thin is
  the transition itself — there is no state between "working" and a `WHY` line,
  so a board mid-failover reads as a broken board.
