# Metering: plans, balances, and bills

How Headroom should model paid API usage alongside the subscription plans it
already watches. [docs/product.md](product.md) covers what to build,
[docs/contract.md](contract.md) covers shape, [docs/trust.md](trust.md) covers
access. This file covers **what a meter is**, because Headroom currently knows
about exactly one kind and the world has four.

## The one abstraction, and what it does not cover

Every quota source in the registry is a `PoolSpec`: a percentage of an opaque
pool that resets on a clock. Rings, pace, burndown, daily burn, the menu-bar
tanks and the board's three slots are all built on that one shape
([docs/rings.md](rings.md)).

It is a good abstraction. It is also one of **eight** shapes people actually
pay in, and the registry can only say one of them out loud.

| Meter | Level | Headroom | Resets | Who | Today |
|---|---|---|---|---|---|
| **window** | % of an opaque pool | points to spare | rolling or calendar clock | Claude Max, Codex, Cursor, Copilot, Gemini, Windsurf, JetBrains, Zed | ✅ `KIND_WINDOW` |
| **grant** | — (a count is not a fraction) | items held, + next expiry | per item | Codex reset credits, promo grants, free-tier allotments | ✅ `KIND_GRANT` |
| **overage** | dollars spent ÷ cap | dollars to the cap | when the cap does | Cursor on-demand, Codex spend control | ✅ `KIND_OVERAGE` |
| **calendar** | dollars accrued | budget or hard cap | month boundary | API orgs billed in arrears, Bedrock, Vertex, Foundry | ⚠️ keys, no meter |
| **balance** | dollars remaining | **runway in days** | **never** — down until you top up | Anthropic Console, OpenAI, OpenRouter, Together, Groq, Fireworks | ❌ |
| **rate** | per-minute utilisation | requests left this minute | every minute | every API, every tier | ❌ (sourcing unsolved) |
| **seat** | — | — | — | Copilot Business, Cursor Business | ❌ (and it has no meter) |
| **attribution** | dollars per unit of work | — | — | "what did that run cost" | ✅ computed, and drawn — but **not a meter**, see below |

Three of those are already implemented, one is half-implemented, and the
implementations don't know they're the same kind of thing. `PoolSpec` covers
**window**. Codex reset credits are a **grant** — `reset_credits_available`,
`reset_credits_expiries`, `reset_credits_expire_at`, all hand-rolled flat keys
with a bespoke fetcher, a bespoke flattener and bespoke firmware labels.
`claude_history` computes **attribution** at 400-day retention. And Cursor and
Codex both carry the **overage** shape as loose `cost_*` and `on_demand_*` keys
bolted onto their payloads.

That is the actual problem, and it is bigger than "balances are missing."
**Every metering form past the first one has been added by bolting flat keys
onto a provider payload.** Each new form costs a fetcher change, a flattener
change, a Swift decodable, a firmware label, and a row in every doc — and gets
no ring, no burndown, no pace, no history, no attention rollup, because those
are wired to `pools` and nothing else. Add balances that way and it works. Add
the sixth form that way and `/usage` is a hundred keys with no structure and
five UIs that each know a different subset.

The postpaid row is half-done and worth looking at, because it shows the
pattern. `codex_usage._parse_spend_control` and `cursor_usage._on_demand`
already produce `used_usd` / `limit_usd` / `remaining_usd`, and
`headroom_server.py` flattens them into `cost_usd`, `cost_limit_usd`,
`cost_remaining_usd`, `cost_reached`. So the host already carries money. It
carries it as loose extra keys hanging off two provider payloads, not as a
meter — which means it gets no ring, no burndown, no pace, no history, and no
attention rollup. Money is a second-class citizen in a document that is
otherwise entirely about metering.

**A balance is the case that breaks things**, and it breaks them at the
abstraction rather than at the UI. A balance has no window, so `resets_in_s` is
undefined, so `pace_pct` is undefined, so the burndown's "where an even spend
would put you" is undefined. Three of the four numbers on a ring stop meaning
anything. That is not a rendering problem to paper over; it is the model
telling you a balance is a different kind of thing.

## The other ways people track this today

Worth writing down, because each one is a feature request in disguise and
Headroom is already better positioned for some than for others.

- **A browser tab pinned to the billing console**, refreshed nervously. This is
  the behaviour Headroom exists to delete, and it is the strongest argument for
  balances: a plan quota resets and forgives you, a prepaid balance hits zero
  and stops your agent mid-turn.
- **`ccusage` and friends in a terminal split** — parse `~/.claude` JSONL,
  print today's spend. `host/claude_history.py` already does this, with 400
  days of retention and per-model breakdown, and surfaces almost none of it.
  This is the biggest gap between what Headroom knows and what it says.
- **A spreadsheet, updated monthly**, usually because someone has to expense it.
  This is an export feature, not a display feature. See Retention below.
- **The monthly credit-card surprise.** No tooling at all. The people in this
  group don't want a chart, they want one number and a threshold.
- **A billing webhook into Slack.** Push, not poll — and out of scope while the
  host is stdlib-only ([docs/product.md](product.md#stdlib-only-and-when-that-ends)).

Two of those five are things Headroom already computes and hides. Start there.

## Decisions

### 1. `PoolSpec` becomes `MeterSpec`, and a meter declares its kind.

Not "add a field for balances." The registry gains the concept it has been
missing since the second metering form arrived: **a source has one or more
meters, and a meter has a kind.**

```python
class MeterSpec(NamedTuple):
    id: str
    key: str
    title: str
    kind: str = KIND_WINDOW      # window | grant | overage | calendar
                                 # | balance | rate | seat
    basis: str = BASIS_OBSERVED  # observed | estimated
    default_window_s: Optional[int] = None
    ring: bool = True
```

`kind=KIND_WINDOW` is every meter that exists today, so the registry rows do
not change and neither does anything reading them. The point of the field is
that the six other forms stop being flat keys and become meters — which means
each one inherits sampling, burndown, daily-burn rollup, history, focus order,
colour override and attention rollup **by existing**, instead of by five
hand-written integrations per form.

The unification that makes one set of machinery serve all seven: **every meter,
whatever its kind, answers the same two questions.**

| | `level` — where am I | `headroom` — what is left |
|---|---|---|
| window | % of pool used | time to exhaustion |
| grant | items held | seconds to next expiry |
| overage | % of window, then $ of cap | whichever half is binding |
| calendar | $ accrued this month | $ to budget, days to boundary |
| balance | $ remaining ÷ last top-up | **days of runway at trailing burn** |
| rate | % of RPM/TPM this minute | requests left now |
| seat | — | — |

`level` normalizes to 0–1 wherever it means anything, alongside the native unit
and its label. `headroom` is the number that turns a level into a warning —
pace is what window calls it, runway is what balance calls it, and they are the
same slot. Everything downstream sorts, charts, warns and rolls up on those two
without knowing the kind.

**`basis` is orthogonal to kind, not a kind of its own.** Any meter's numbers
are either observed from the provider or computed locally from token counts.
A `calendar` meter is `observed` when it comes from a billing endpoint and
`estimated` when `pricing.py` derived it. Putting it on the spec rather than
folding it into `kind` is what keeps decision 3 enforceable at every surface
instead of at each call site.

**`seat` is in the list on purpose.** A flat per-seat licence has no meter at
all — no level, no headroom, nothing to chart. It is still how a lot of people
pay for AI coding, and a monthly total that omits it is wrong. Accommodating it
means a row that renders as a plain figure with no gauge. That is the test of
whether the abstraction is honest: it has to be able to say *this one has no
meter* without pretending otherwise.

**`rate` is in the list, and its fetcher is not.** Defining the kind costs
nothing and reserves the shape. Sourcing it is the unsolved part — see decision
6, which is unchanged.

**`attribution` is in the table above, and it is not a meter.** This document
said it would become one. Building it proved otherwise, and the correction is
worth keeping rather than quietly editing out.

A meter answers `level` and `headroom`. Per-run cost answers neither: there is
no denominator — a fraction of *what?* — and nothing is remaining. Making it a
`MeterSpec` would have meant inventing a shape so the abstraction could claim
it, which is the same error as bolting flat keys onto a payload, pointed the
other way. It is history, and it surfaces as history.

The lesson generalises: a kind earns its place by having a level or a headroom
that means something. `seat` is the boundary case that proves it — it has
neither, is named anyway, and renders as a plain figure precisely *because*
the model can say "this one has no gauge" out loud.

There is a meter hiding nearby, and it is not this one: **estimated spend
against a monthly budget** is a `calendar` meter with `basis: estimated`, and
it arrives when someone sets a budget. Until there is a budget there is no
denominator, and the honest reading is the history.

**`level` and `headroom` are deliberately not on the wire yet.** They landed in
this document before they landed in the payload, and that gap is on purpose:
every meter that exists today is a window, so `level` is `pct / 100` and
`headroom` is `resets_in_s`. Shipping them now would commit the contract to two
keys with no consumer that duplicate two keys that already work — and
[docs/contract.md](contract.md) is explicit that a key is a permanent
commitment. Worse, `headroom` carries a unit that changes by kind (seconds,
days, dollars, items), and designing that shape against seven window meters and
zero of anything else is how you get a schema that fits nothing real. They ship
with the first non-window kind, which is the first moment they can be designed
honestly. `kind` and `basis` are what had to land early, because everything
else is gated on being able to ask what a meter is.

### 2. One renderer per kind. The ring becomes one of them, and does not change.

Ring and pace semantics are a cross-platform contract — `docs/rings.md`,
`Shared/HeadroomRings.swift` and the firmware constants move together or not at
all. The temptation with seven kinds is to generalize the ring until it can
draw all of them. That means editing a three-language contract to express
things it was not built to say, and it ends with a ring whose meaning depends
on what it is drawing — which is the one thing a glance surface cannot afford.

Instead: `level` + `headroom` is a shape any mark can render, so each kind gets
the mark that suits it and they all read from the same two fields.

| kind | mark | why not a ring |
|---|---|---|
| window | **ring** — unchanged | — |
| overage | a dollar bar beside the plan meters, not among them | it starts moving *because* the window ran out; an arc would read as more of the same thing |
| grant | pips, one per item, dimming toward expiry | a count is not a proportion |
| calendar | bar against the budget, month as the axis | proportion of a *self-set* budget, not a pool |
| balance | depletion bar, runway date under it | no window means no arc to sweep |
| rate | a live tick, not persisted | it is true for sixty seconds |
| seat | plain figure, no gauge | there is nothing to measure |

The ring keeps meaning exactly what it means today. It stops being *the* view
and becomes the view for `kind: window`, which is what it always actually was.

This is also what keeps the board out of the blast radius. The firmware draws
rings for the three focus providers; as long as `window` renders identically,
a board flashed today keeps working through all of this without a cable.

### 3. Estimated dollars and billed dollars are different kinds of number.

This is the trust decision and it is the one that matters most.

`pricing.py` turns local token counts into USD. `quota.py` is explicit that the
result is a compute-weighted proxy against a budget *you* set, not money. That
is honest as long as nothing prints a dollar sign next to it.

The moment a card says **$14.20 spent today**, the user compares it to the
console bill. When the two disagree — and they will, because of tier discounts,
intro pricing, batch rates, cache accounting and rounding — the app is wrong
about money, which is a different category of wrong from being wrong about a
percentage.

So:

- A number Headroom computed from local token counts is **estimated** and is
  labelled as such at every surface that shows it.
- A number a provider reported is **billed** and is labelled as such.
- **The two are never summed into one figure.** A total that mixes them is a
  number with no meaning and no way to check.

This is the same instinct as `_held_resets(trusted=False)` in
`headroom_server.py`: a stale countdown is the most convincing wrong number in
the document, so it is withheld rather than shown. An unlabelled estimate is
the money version of that, and it is worse, because nobody audits a percentage
against a credit-card statement.

### 4. The price table is now load-bearing, and it was wrong.

`host/pricing.py` had no entry for `claude-opus-5`. `_base()` falls through to
`_DEFAULT`, which is Sonnet-tier, so every Opus 5 record was priced at
**$3/$15 instead of $5/$25** — about 40% under. On this machine that is 13,185
assistant records across the local history, and it has been flowing into
`cost_usd`, daily burn, the spend card and the 400-day history the whole time.
`claude-mythos-5` had the same problem at $10/$50. Both rows are added.

`RATES_CHECKED` is deliberately not bumped: rows were added, nothing was
re-verified against a fresh catalog read.

Two follow-ons, neither done yet:

- **The fallback is silent.** Pricing an unknown model at Sonnet rates is
  better than pricing it at zero, but it produces a confident wrong number with
  no tell. Records priced by fallback should be marked, so the UI can say
  *estimated, unrecognized model* instead of just a dollar amount.
- **An unknown model id is an attention reason.** A new model shipping is
  exactly the event that makes this table stale, and noticing that something
  changed is the job Headroom already does. This is the cheapest possible
  guard against silently drifting prices, and it costs one entry in the
  attention rollup ([docs/attention.md](attention.md)).

The intro-pricing comment on `claude-sonnet-5` is a third case: a per-account
fact hardcoded as a global. Whoever qualifies for intro pricing gets a number
that is right; whoever doesn't gets one that is quietly 50% high. Leave it as a
comment until someone asks, then it is a per-account setting, not a table edit.

### 5. API sources need a pasted key, and that changes where they live.

The README splits sources two ways: **AI coding tools** are read from a sign-in
already on the Mac, with nothing to paste; **dev tools** want a key. An API
account is "how much money is left" — unmistakably an AI coding tool by that
description — and it needs a pasted key. The split conflates two independent
axes.

Make them independent:

| Axis | Values |
|---|---|
| Setup | local sign-in \| pasted key |
| Meter | plan \| balance \| bill |

An API source is `group="ai"` with a `needs_key` flag, not a third group. The
group is about what the thing *is*; setup is a property, not a category.

Access follows [docs/trust.md](trust.md) with no new rules: a key is a
credential, so it is **Mac-local** — Keychain, pasted in Mac Settings, never
written into `/usage`, never offered to the phone. Same treatment as the
GitHub, Supabase, Plausible and PostHog keys.

One thing does deserve a new sentence in `SECURITY.md`: **an org-level usage or
admin API key is a much larger credential than a stats key.** It reads spend
across every member and project, and on some providers the same key class can
do more than read. Prefer the narrowest read-only scope a provider offers, and
say plainly in the UI what the key can see. A Plausible key leaks visitor
counts. An admin key leaks the shape of the whole company's AI spend.

### 6. Rate limits are the fourth meter and Headroom should not build them.

"Am I about to get 429'd" is genuinely the Headroom question, asked at a
one-minute timescale instead of a one-week one. It is tempting.

It does not fit, for a structural reason: **RPM/ITPM/OTPM state lives in the
response headers of requests Headroom does not make.** Headroom is an observer
— it reads files other tools left on the disk. To know the live limit state it
would have to either proxy the traffic (it is not in that path and should not
be) or fire a probe request, which means spending money to measure spending.

So: **not built, and this is why**, so it stops being re-argued. The one
carve-out: if a provider's usage endpoint happens to return current limit state
alongside the spend numbers, take it as data. Don't go making calls to find it.

### 7. Extra accounts already generalize. This is the cheap part.

`accounts.py` plus `account_kind` already expand one registry row into N, each
with its own poll, samples, burndown, ring, colour and pinned order. An API
account is exactly that shape: a label plus a credential location. `claude:work`
becomes `anthropic-api:prod`.

Everything downstream — focus-3, the board's slots, colour overrides,
reordering, per-account history — comes free. This is the strongest evidence
that the registry design is right, and it means the expensive part of adding
API sources is the meter semantics, not the plumbing.

### 8. Money lands on the Mac and the phone first. The board waits.

`MAX_PROVIDERS`, `MAX_POOLS` and `FOCUS_LIMIT` are all 3, mirrored into the
firmware by a comment ([docs/contract.md](contract.md)). Money on the board
means new labels, new caps, and a reflash — and per contract.md the board's
deprecation window is "until the shim costs more than a kilobyte," which is to
say years.

Ship money where the client updates in days, learn what the numbers should say,
and only then spend a firmware revision on it. Same shape as the standing rule
that the board never gains controls: the board is a render target, and it
should render a settled idea.

### 9. Balances resolve product.md's open question.

[docs/product.md](product.md#the-unresolved-one-whose-quota-is-it) leaves one
question open: is the burndown chart *"what this Mac watched happen"* or
*"what happened to this pool"*? For a plan quota it is genuinely ambiguous.

For a balance it is not. A credit balance is one global number that both Macs
observe and either can spend against. There is no reading in which two Macs
have separate balances. Adding balances therefore **forces the second answer**
— the chart is what happened to the pool, with machine identity as provenance
rather than ownership.

That is the answer product.md guessed was right. Adding a balance source is
what makes it no longer a guess, and it should be decided before the multi-Mac
merge, not after.

### 10. Retention and export stop being optional.

product.md already calls export "the gap most worth closing." Money makes it
sharper in two directions:

- **Spend history is worth more than percentage history.** A year of "how much
  did this cost" is the thing someone has to expense, reconcile, or justify. A
  new Mac currently costs you all of it.
- **Spend history is more sensitive than percentage history.** `docs/privacy.md`
  currently describes quota samples. A per-model, per-day cost record is a
  finer-grained picture of what someone was doing and how hard. It needs its
  own line in the privacy doc before it ships, not after.

CSV export of daily cost is also the cheapest possible answer to the
spreadsheet crowd from the survey above, and it needs no new UI beyond a
button.

## What earns a Setting here

Applying product.md's rule — a preference exists when two reasonable people
using Headroom the same correct way need different values:

| Value | Where | Why |
|---|---|---|
| Which API accounts exist, and their keys | Settings | a fact about your setup |
| Monthly budget target | Settings | genuinely differs per person; nothing can derive it |
| "Make this balance last until *date*" | Settings | a personal intention, not a fact |
| Runway thresholds (when a balance turns amber) | **hardcoded** | a judgment we made |
| Whether estimated and billed are labelled | **hardcoded** | not a preference, an invariant |
| Poll interval for a spend endpoint | **hardcoded** | a tradeoff with a right answer |

No Spend Settings pane. If a threshold is wrong, our number is wrong.

## How this lands without breaking a shipped client

The whole design is additive under [docs/contract.md](contract.md), and the
mechanism is one sentence: **`pools` keeps emitting exactly the window-shaped
object it emits today, and every meter of a new kind carries `pct`,
`window_s`, `resets_in_s` and `pace_pct` as null.**

An old client iterating `pools` sees a meter with no percentage and draws
nothing for it — the same thing it already does for a provider that is off or
unconfigured. A new client reads `kind` and renders properly. No key is
removed, no key is repurposed, no type is narrowed. `contract` moves only when
a client that ignores `kind` would show something *wrong* rather than absent,
which for the emission above it would not.

The board is the strongest case for this shape. `MAX_POOLS` is 3 and the
firmware draws rings; a balance meter that arrives as a null-`pct` pool is
skipped by exactly the `.isNull()` guards already in `main.cpp`. A board
flashed today survives all seven kinds without a cable, which is the standard
`LEGACY_PROVIDER_IDS` already sets.

## Order of work

The architecture lands whole; the sources land one at a time. Steps 1 and 2 are
the accommodation — after them, a new metering form is a registry row and a
fetcher, the same as a new provider is today.

| # | What | Why here |
|---|---|---|
| 1 | `MeterSpec` with `kind` + `basis`; every existing row `kind=window`, `basis=observed` | ✅ **landed.** Pure refactor, zero behaviour change — both contract suites passed unchanged |
| 2 | `Source.windows()`; every consumer says whether it means windows or all meters | ✅ **landed.** The three that assume percentages — headline, log line, `pool_rows()` → `quota_samples` — now say so. None failed loudly when handed anything else |
| 3 | Codex credits become a `grant`; `level` + `headroom` land | ✅ **landed.** First non-window meter, and the first moment `level`/`headroom` could be designed against something that is not a percentage |
| 4 | Cursor on-demand and Codex spend control become `overage` | ✅ **landed.** Second kind, first in dollars — and it needed **no new wire keys**, which is the abstraction paying for itself |
| 5 | The Spend card draws the cost history, and `pricing` gains a tell | ✅ **landed.** 400 days of per-model cost that reached one string. See *attribution is not a meter* below — this step corrected its own plan |
| 6 | `balance` — OpenRouter + Vercel AI Gateway, Keychain key, one account per key | ✅ **landed.** First prepaid meters. Anthropic/OpenAI wait on an official remaining-$ API. Account use (daily bars, runway, top models) paints on Activity — still no ring, still no Attention, not a Usage tab |
| 7 | `calendar` — same source's month-to-date against a budget | Nearly free once 6 lands: the arithmetic is already shared with `overage`. The two together are how an API account actually reads |
| 8 | A second *calendar* or Anthropic/OpenAI when balance APIs exist | The only real test of whether the abstraction held past the first two |
| 9 | `seat` — manual entry, no gauge | Cheap, and it is what makes a monthly total true rather than partial |
| 10 | CSV export | Closes the retention gap while the data is still small |
| 11 | The board, if the semantics have settled | Last, because it is the one that cannot be taken back cheaply |

Step 1 is the one that has to be right. Everything after it is a registry row,
a fetcher and a renderer — which is the point of doing it.

Deliberately not on this list: a `rate` fetcher (decision 6 — the kind is
reserved, the sourcing is not solved), a spend Settings pane (above), summing
estimated and billed (decision 3), and generalizing the ring (decision 2).
