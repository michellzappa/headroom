# Product decisions

Standing decisions that shape what gets built, written down because they are
otherwise re-argued every time a feature touches them. None of this is about
code structure. [docs/contract.md](contract.md) covers shape,
[docs/trust.md](trust.md) covers access.

## What Headroom is

**An ambient answer to "am I about to hit a limit, and did anything break?"**

The measure of a Headroom feature is whether it makes that answer arrive
without being asked for. A ring you glance at on the way past the desk is the
product working. A screen you deliberately open to study is a screen that
needs a reason.

Three surfaces, in descending order of how much they are allowed to ask of you:

| Surface | Job | May it interrupt? |
|---|---|---|
| ESP32 | the glance, always on, never touched | no |
| Menu bar | the pip and the meters, plus detail on click | pip only |
| Phone / Watch | the same answer when you are not at the desk | yes, if it is attention-level |

## What Headroom is becoming, and the line under it

Versions 1.2.4 through 1.2.7 turned the phone into a remote control for a
coding agent: read the request, approve it, deny it with a reason, answer a
question, stop a runaway turn. That is a real second product living inside the
first, and it changes what the app is responsible for.

Being explicit about the line, so it is a decision rather than a drift:

- **Headroom answers agent requests. It does not originate work.** Approving,
  denying, replying and interrupting are all responses to something the agent
  asked. Starting a turn, editing a prompt, or launching a session from the
  phone is a different product — a mobile client for a coding agent — and
  Headroom is not it.
- **Every remote capability has a local equivalent that came first.** If the
  Mac cannot do it, the phone does not get it.
- **The board never gains agent controls.** It has no authentication, no way to
  show a full command, and a touch target the size of a thumb. It reports that
  something is waiting. See [docs/contract.md](contract.md) on the board being
  a render target.
- **A secret never leaves the Mac.** A Codex question marked `isSecret` is
  never offered remotely and never enters the ledger. This is an invariant, not
  a behaviour: any new adapter inherits it.

The product promise is simple: **your agents, wherever you are**. Headroom
keeps the computer-side request visible, mirrors it in Attention on iPhone,
and lets you answer the small set of safe, structured questions that can keep
work moving when you are away from the desk. Passive agent activity stays
dismissible; a request that can block work stays visible until you decide.

The reason to hold this line is that the two products want opposite things. The
glance wants to say less. A remote control wants to say everything, because an
approval you cannot fully read is not an approval
([docs/agent-attention.md](agent-attention.md)).

## What earns a Setting

[docs/attention.md](attention.md) already settles one case: rollup weights and
ages are hardcoded product policy, and there is no Attention Settings pane. The
general rule behind that:

**A preference exists when two reasonable people, using Headroom the same
correct way, need different values.** Not when a value was hard to choose.

| Kind of value | Where it lives | Example |
|---|---|---|
| Facts about *your* setup | Settings | which sources are on, endpoint, accounts, pinned order |
| Things only you can see | Settings | accent colours, which providers are in focus |
| A judgment we made | hardcoded | attention weights, pace thresholds, ring geometry, `FOCUS_LIMIT = 3` |
| A tradeoff with a right answer | hardcoded | poll intervals, cache TTLs, sample bucket size |

If a value is hardcoded and someone wants it changed, the response is to
consider whether *our* number is wrong — not to add a slider. A slider moves
the decision to the user and makes every future change to that number a
compatibility problem.

The Settings module is thousands of lines across the sidebar panes. That is
the cost of the rows in the top half of the table, and it is why the bottom
half stays out.

## History is a user asset

Headroom accumulates something no provider gives back: a record of how you
actually spent your quota.

| Store | Retention | Where |
|---|---|---|
| Quota samples | **90 days** | `~/.headroom/quota_samples.jsonl` |
| Quota resets | **400 days** | `~/.headroom/quota_resets.jsonl` (Codex also merges [codex-resets.com](https://codex-resets.com)) |
| Daily burn | **30 days** | `~/.headroom/daily_burn.json` |
| Claude history | **400 days** | `~/.headroom/claude_history.json` |
| Agent ledger | **30 days from settlement** | `~/.headroom/attention.sqlite3` |

Three decisions follow from calling it an asset:

- **Losing it is a bug, not a cleanup.** Anything that rewrites these files
  gets the same care as the config merge. `quota_samples` is already
  append-only with a rewrite threshold for exactly this reason.
- **It should survive a new Mac.** There is no export today and no import.
  Getting a new laptop currently costs you every chart. That is the gap most
  worth closing of anything in this file.
- **The ledger is the exception, and gets the opposite treatment.** It holds
  commands, file paths and code excerpts from every permission request, so it
  is the one store where *keeping* data is the risk. Settled events are pruned
  30 days after they settle; anything still pending survives regardless of age,
  because something is blocked on it. The clock runs from settlement, not
  creation — a request raised in March and answered today is a record of what
  you approved today.

The four numbers above are also four different formats in one directory. Time
series want to be in the SQLite that already exists; config stays JSON. That
consolidation is what makes "export your history" a feature rather than a
project.

## The unresolved one: whose quota is it

[docs/multi-mac.md](multi-mac.md) settles per-machine facts cleanly — local
servers, git commits, attention events are *reported* with an owner and never
merged, because a merged list would describe a computer that does not exist.

Quota does not fit that frame, and the doc does not claim it does.

A Claude weekly pool is **one global thing observed from two places.** It is
neither a per-machine fact nor a mergeable list. Two Macs polling the same
login see the same pool, and the burndown chart is a record of *observations*,
not of the pool. So a laptop that slept all week has a correct `week_pct` and a
sparse chart, and there is no obvious answer to which sample is truth when both
were awake.

`multi-mac.md` describes the merge mechanically — thinned curves, local samples
win, remote fills gaps, max-pct within a bucket since usage only rises inside a
window. That mechanism is right. What is undecided is the semantic underneath:

- Is the chart *"what this Mac watched happen"* (an observation log, per
  machine, never merged, consistent with everything else in that doc), or
- *"what happened to this pool"* (one series, assembled from whichever machine
  was awake, with the Mac identity as provenance rather than ownership)?

**Decide this before implementing the merge.** The mechanism is a day's work
and the semantic is what every future multi-Mac feature inherits. The second
reading is probably right — the pool is genuinely singular, and the user thinks
of it that way — but it is the first thing in this repo that breaks the "never
merged" rule, so it deserves to break it on purpose.

## Stdlib only, and when that ends

The host has no dependencies. Not few — none. That buys a supply chain with
nothing in it, a bundled host that runs on the system `python3`, and an install
that cannot break because something upstream published a bad version.

It is a real constraint and it is already load-bearing in the design: JSONL
over a database, `hmac.compare_digest` over a library, a hand-rolled OAuth
refresh.

**The exit condition is already visible.** Guaranteed push while the phone is
suspended needs APNs, and APNs needs HTTP/2, which the standard library does
not speak. [docs/agent-attention.md](agent-attention.md) names this. So the
decision to make ahead of time, rather than under pressure:

- Polling stays the default and keeps working with zero dependencies.
- If push ships, the HTTP/2 client is an **optional** component. Absent, the
  phone polls and behaves as it does today. Present, it pushes.
- It never becomes a requirement for `/usage` to serve. The core stays
  stdlib-only even if a leaf does not.

Same rule for anything else that wants a dependency: it may exist at a leaf, it
may not sit under the document.

## The support floor

Claimed, and worth keeping honest:

| Thing | Floor | Enforced? |
|---|---|---|
| Python | 3.9+ (macOS 14 ships 3.9.6) | **no** — CI runs 3.12 only |
| macOS | 14.0 | project.yml |
| iOS / iPadOS | 17.0 | project.yml |
| watchOS | 10.0 | project.yml |
| Board | Waveshare ESP32-S3-Touch-AMOLED-1.8 (368×448, SH8601/QSPI) | the only one that exists |

The Python row is the one that matters, because the bundled host runs on
whatever `/usr/bin/python3` the user has. A 3.10+ syntax feature would pass CI
and break every macOS 14 install. Adding 3.9 to the CI matrix is in
[docs/backlog.md](backlog.md) and is cheap.

The board floor is a single SKU on purpose. A second panel means a second
layout, a second set of caps, and a second thing to test with hands on a desk.

## Single user, single desk

Headroom assumes one person, one account, one board. No multi-user Mac
handling ([docs/trust.md](trust.md) states where that shows), no shared
install, no per-user views. Multi-Mac sync is *your* Macs, keyed by machine,
never someone else's.

This is not a limitation to fix. It is the assumption that lets the host skip
identity entirely and lets loopback be free.

It describes the **host**. It does not describe the **person**. Many people
run Headroom on more than one Mac, and some want to compare with a friend. The
next section records what changed for that, and what did not.

## Friends, cards and the usage study

Decided 2026-09-29. Two things now exist that are about a person and not about
one Mac: the **stats card** (`host/study_card.py`) and the **usage study**
(`host/usage_study.py`). Both read the Claude session logs and summarise how
someone uses their agent: token mix, model families, time of day, prompt
length, session length.

What stays true: the host has one account, one token model, and free loopback.
A friend is not a second user of your host. A card is a message you hand
someone, not a login.

What changes: the unit for this data is the **person**. That is why it is
written down here. The rules below are what later work inherits.

### Rules

1. **Person, not Mac.** Usage on two Macs is disjoint sessions, so the merge is
   a sum. Nothing is counted twice. This is not the quota problem in
   [the unresolved one](#the-unresolved-one-whose-quota-is-it): a provider quota
   is one pool seen from two places, and token usage is not.
   - Merge **counts**, then round once. Rounded shares from two Macs cannot be
     combined.
   - One shard per machine id. If a machine appears twice, the newer stamp wins.
     A stale copy cannot inflate a total.
2. **Nothing leaves without being shown.** `usage_study.py` prints the exact
   payload before anything is sent. A card holds shares in 5% steps, wide bins,
   and no counts. A shard holds counts and is for your own Macs only.
3. **Consent is separate from diagnostics.** Diagnostics are on by default and
   cannot follow a Mac across weeks, on purpose
   ([telemetry.md](telemetry.md)). The study is opt-in and off by default. A
   card is sent by hand, by the person who made it.
4. **Names and behaviour never join.** The card id is random and belongs to
   friends. A study id, if one exists, belongs to the study. Neither is reused
   in the other. A Game Center player id never goes to our Worker.
5. **A card is untrusted input.** `study_card.decode` is the only way one gets
   in. A friend's handle reaches your menu bar, so it is cleaned, capped and
   never rendered as markup. Importing changes config, so the import route is
   Class 1 (loopback only) in [trust.md](trust.md). A card is not signed: it
   proves nothing about who sent it.
6. **Rank on output tokens, never on raw totals.** On the maintainer's own logs
   98.4% of input is cache reads. A total is a measure of cache reuse. Any
   dollar figure says it is an estimate ([metering.md](metering.md)).
7. **Friends get numbers about usage and nothing else.** No attention events,
   no ledger, no paths, no repository names, no agent controls.

### The open question: counting people on the server

The Community Pulse counts **weekly active Macs, not users**. Two Macs are two
reports, by design ([telemetry.md](telemetry.md)). That was the right unit for
"which releases are in use". It overstates people, and anyone with two Macs is
counted twice. For a business case about *people* this matters, so the metric
name must say "Macs" until this is settled.

Seeing one person once needs one key for that person across their Macs. The
multi-Mac rule forbids the easy way. **A machine writes only its own record**
([multi-mac.md](multi-mac.md)), so a shared id cannot be copied between Macs.
Three ways out:

| Option | Cost |
|---|---|
| **A. Merge on your Macs, upload once.** One Mac is the uploader. It reads the other Macs' shards and sends one merged contribution. | Needs a way to choose the uploader, and shards to travel between Macs. |
| **B. Each Mac uploads its own shard. The server sums them.** | Breaks rule 2. The server needs counts, not shares, and counts are the fingerprint. |
| **C. Derive a shared id from something both Macs know.** | Anything both Macs know is stable, and a stable id lets the server follow a person. Rule 4 says no. |

Recommendation: **A.** It keeps the server blind to how many Macs one person
has, and it keeps counts on your own machines.

Until this is decided, **the study uploads nothing**. The only way data leaves a
Mac is a card that a person copies and sends themselves.

### Not built yet

- The friends store, local aliases, and the card import route.
- Shard sync between Macs. It needs a CloudKit schema change and a Console
  redeploy ([multi-mac.md](multi-mac.md)), so it is its own piece of work.
- Game Center, the public board, and any Settings row for any of this.
- A `docs/privacy.md` entry for cards. It goes in when a card can leave the app,
  not before.
