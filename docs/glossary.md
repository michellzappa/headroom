# Headroom glossary

Canonical names for the same concepts on ESP32, macOS, iOS, and widgets.
`Shared/HeadroomCopy.swift` is the Swift source. Firmware mirrors the same
words in `firmware/src/main.cpp` (see the `LABEL_*` constants). Host-served
titles for sources and pools live in `host/sources_config.py`, and the host's
own prose — `headline`, `verdict`, attention `reasons` — is written in
`host/burndown.py`. **The host is a copy surface, not a data source**: those
strings are the ones the ESP32 draws and VoiceOver reads, and no client can
retitle them. `scripts/check-glossary-copy.sh` searches `host/` for that
reason.

When you rename something here, update the Swift copy, firmware labels, host
prose, and any hardcoded chrome that still bypasses them. **A rename is a
release**, not a commit that rides along with something else: the iPhone
replays its last saved payload (**Recent history**) and the board holds strings
in flash, so for as long as a stale client is alive both spellings are on
screen somewhere. Clients must never persist host prose across a rename.

## The decisions under all of this

The tables below are the *what*. These are the *why*, and they are the ones
that get expensive to reverse.

**Percent is the only unit Headroom claims.** Anthropic bills in points,
Cursor in requests, GitHub in premium requests, OpenAI in credits. Headroom
flattens all of them to a share of a window, and that flattening *is* the
product — it is what lets one glance compare four plans. The cost is that
Headroom's numbers never reconcile with a provider's billing page, so the copy
must never borrow a provider's unit for a figure that isn't in it. No "pts",
no "credits", no token counts. If money ever lands on a surface (`host/pricing.py`
exists and is unused by the UI), it arrives as a second, separately labelled
axis — it does not get to reuse these words.

**Voice: second person, present tense, no first person.** Headroom says *you*
and names things; it never says *we*, *our*, or *I*, and it never apologises.
Actions are imperative (**Refresh all**, **Add a test row**), states are
fragments (**Not updating**, **On pace**). Welcome is allowed to be warmer than
Settings — that is a register, and it is deliberate — but it stays in the same
person. `check-glossary-copy.sh` fails the build on `Text("We `.

**Metaphors are zoned, one per axis.** Five families were in use at once and
they pointed three directions for one fact:

| Axis | Family | Words |
|---|---|---|
| State — how much is left | Fuel | tank, drains, **Empty**, full |
| Rate — is that sustainable | Pace | **On pace**, **Over pace**, to spare, over |
| History — how it got here | Burndown | burndown, daily burn, burn rate |
| Money | Provider's own | credits, grants — **only** where the provider bills that way (Codex reset credits) |

Do not mix them. A tank does not run "over budget"; a pace is not "empty".
The product name is the fuel family's, and it is the only place the metaphor
is stated as a noun.

**Telling time: prose says *when*, compact says *how long*.**

| Form | Looks like | Where | Field |
|---|---|---|---|
| Clock | `Thu 14:00`, `tomorrow 04:18` | `headline`, anywhere with a full line | `_when()` |
| Duration | `4d 44m`, `3d` | menu bar, watch, board, `Resets 3d` captions | `resets_in`, `fmt_resets()` |

One sentence gets one form. `58% left · 4d 44m. Out tomorrow 04:18` was two
time facts in two shapes on one line. The board's `verdict` is the documented
exception — at ~25 bytes it takes duration form, and each of its branches
returns only one time fact, so the two never meet.

**Times are 24-hour, English (U.S.), not localized.** Every string is a literal;
there is no `.strings` catalogue and `host/burndown.py` formats with `%H:%M`.
That was decided by default rather than on purpose — record it here so the day
it changes is a decision and not a surprise.

**Provider names belong to other companies.** Claude, Codex, Cursor, Copilot,
Gemini, Windsurf, JetBrains, Zed. Render them exactly as the vendor does, never
possessive (*Claude's quota*), never verbed, and never phrased so the reader
takes Headroom for an official integration. Headroom reads local files those
tools already wrote; it is not endorsed by any of them.

**Accessibility strings follow one order: name, then value, then state.**
"Claude, 42 percent used, 38 percent pace". Layout and host prose stay
surface-specific (see the end of this file), but the shape does not.

## Product

| Term | Meaning |
|---|---|
| **Headroom** | Product name on every surface |

## Navigation & sections

| Term | Meaning | Surfaces |
|---|---|---|
| **Usage** | Quotas, consumption, and tokens at a glance | macOS mode, iOS tab |
| **Summary** | Aggregate usage view inside Usage, before provider detail | macOS, iOS |
| **Quotas** | Coding quota detail, reached from Usage → Summary (no longer its own iOS tab) | iOS |
| **Coding quotas** | Section title above the rings | macOS, iOS |
| **Attention** | The queue: agent questions, rollup reasons, failed rows | macOS mode, iOS tab |
| **Activity** | Merged deploys / commits / Actions feed, over the service panels | macOS mode, iOS tab, ESP32 detail pages |
| **Recent** | Chronological mixed activity feed (commits, Actions, deploys, resets); provider identity is on each row | iOS, macOS |
| **GitHub Actions** | GitHub workflow activity | iOS, macOS |
| **Vercel deployments** | Deployment activity from Vercel | iOS, macOS |
| **Git commits** | Local commit activity | iOS, macOS |
| **Quota resets** | Coding quota reset events | iOS, macOS |
| **Claude status** | Claude service status events | iOS, macOS |
| **Other activity** | Fallback group for activity kinds added by a newer host | iOS, macOS |
| **Services** | Supabase, Plausible, PostHog panels on Activity (local servers and Xcode builds are catalog rows too) | macOS Activity, iOS Activity |
| **Local servers** | Listening ports panel | macOS, iOS |
| **Xcode builds** | Active `xcodebuild` / IDE compiles on this Mac | macOS, iOS |
| **Settings** | Preferences | macOS window, iOS tab |
| **General** | Host endpoint and runtime details, host lifecycle, Open at Login, welcome, App updates | macOS Settings |
| **Keep the host running when Headroom is closed** | Who supervises the host. On, launchd owns it and it serves the board, iPhone and Watch whether or not the app is open. Off, the app owns it and quitting Headroom stops it. On by default. See [host.md](host.md) | macOS Settings |
| **Remove background service…** | Stops the host, removes its LaunchAgent for both the current and legacy label, and quits. Shown only while a plist exists. The way to leave cleanly before deleting the app | macOS Settings |
| **Open at Login** | Start the menu bar app when you log in to this Mac | macOS Settings |
| **App updates** | Whether a newer Headroom.app exists, and installing it. Always lists **Current** (this copy) and **Latest** (the update feed) | macOS Settings |
| **Current** | This Mac's installed Headroom.app version | macOS Settings → App updates |
| **Latest** | Version the update feed last reported | macOS Settings → App updates |
| **Providers** | AI coding-quota meters Headroom draws rings for. Order and focus live here. Claude Code / Codex connection settings live under Agents. OpenRouter and AI Gateway are prepaid balances under Integrations → Activity, not here | macOS Settings, iOS Settings, Welcome |
| **What to watch** | Welcome rail title for the Providers step | macOS Welcome |
| **Integrations** | Catalog of what you watch on Activity (and connect): Git, GitHub Actions, Vercel, OpenRouter, AI Gateway, Supabase, Plausible, PostHog, Sentry, Datadog, Axiom, local servers, Xcode builds. One reorderable list — enable, status, open leaf. Activity follows the same order for rows that paint a block (including OpenRouter / AI Gateway account use). Activity row count lives here; Local servers density on the Local leaf; projects and sites on each other Integration page. Claude Code and Codex live under **Agents**, not here | macOS Settings, iOS Settings |
| **Show in Headroom** | The dev-tool on/off on its Integrations leaf. Off stops polling and hides the rows; the key stays in the Keychain, which is what **Disconnect** clears | macOS Settings |
| **Code and deploys** | Integrations leaves: **Git** (local commits on this Mac), **GitHub Actions** (CI via PAT), **Vercel** (CLI login) — three leaves because they use three different credentials | macOS Settings |
| **Status** | Integration detail caption: connected, Keychain, hooks state, signed-in | macOS Settings |
| **Connect** / **Replace** / **Disconnect** | Paste, overwrite, or clear a Keychain credential | macOS Settings |
| **Keychain** | Detail Status when a token is stored on this Mac. The SecureField shows `••••••••••••` so the row does not look empty | macOS Settings |
| **Not connected** | Hub / detail when nothing is pasted yet | macOS Settings |
| **Hooks installed** / **Hooks off** | Agents caption for Claude Code hooks | macOS Settings |
| **Gateway on** / **Gateway off** | Agents caption for Codex | macOS Settings |
| **Dev root** | Folder scanned for local git repos, one level deep. Stays on this Mac | macOS Settings |
| **Commit authors** | Names or emails whose commits count as yours; blank counts everyone | macOS Settings |
| **Teams** | Vercel team filter; blank reads every team the login can see | macOS Settings |
| **Connection** | Which Mac the phone talks to | iOS Settings |
| **Permissions** | Mac-granted phone capabilities (read-only on iOS) | iOS Settings |
| **Sync** | iPhone pairing and settings sync with Other Macs | macOS Settings |
| **iPhone** | Pairing + grants on Mac; notifications on iOS | Sync on macOS, iOS Settings, Welcome |
| **Open the TestFlight invite** | Install link on Welcome’s phone step and Settings → iPhone | macOS Welcome, macOS Settings |
| **On your phone** | Welcome rail title for the iPhone step | macOS Welcome |
| **About** | Product credit in Settings: icon, version, creator, Changelog, GitHub source link, live star count, Community Pulse | macOS, iOS |
| **Created by Michell Zappa** | Personal credit on About (LICENSE copyright) | macOS, iOS |
| **Centaur Labs** | Publisher line on About (App Store entity) | macOS, iOS |
| **Changelog** | Readable release notes from the bundled `CHANGELOG.md`, opened from About | macOS, iOS |
| **Source on GitHub** | Link to the public repository from About | macOS, iOS |
| **GitHub stars** | Live star count under the source link on About | macOS, iOS |
| **Attention** | Warning / status card on the Mac, and the iOS tab it became (scoring policy: `docs/attention.md`) | macOS, iOS |
| **Answer coding agents** | Mac-granted iPhone permission to answer an agent approval request | macOS, iOS |
| **Using Codex at** | Path to the Codex executable Headroom discovered and is supervising | macOS |
| **Agents** | Claude Code hooks and the Codex attention gateway — install, test, and connection status. Not part of the Integrations catalog | macOS Settings |
| **Agent alerts** | Whether passive agent notices appear; questions, choices, and approvals remain visible when it is off | macOS Settings |
| **Your agents, wherever you are** | Headroom's companion promise: see and safely answer agent requests from iPhone while the computer keeps its full context | macOS, iOS |
| **Claude questions** | Whether Claude questions appear on both Mac and iPhone, can wait for an iPhone answer, or stay Mac-only | macOS |
| **Claude Code hooks** | Managed Claude lifecycle and permission integration | macOS |
| **Install hooks** / **Reinstall hooks** / **Remove hooks** | Manage only Headroom-owned entries in Claude settings | macOS |
| **Add a test row** | Add a harmless Claude test row to the common feed | macOS |
| **Request** | The agent's own request, field by field, above the answer buttons | iOS |
| **Why** | The provider's stated reasons for asking | iOS |
| **Show request** / **Hide request** | Expand the bulk fields (file contents, replacement bodies) | iOS |
| **Shortened to fit** | This value is a prefix; the host clipped it | iOS |
| **Options** | The choices an agent is offering, each with why you would pick it | iOS |
| **Ask on Mac** | Decline to answer a question from the phone; it appears on the Mac | iOS |
| **Allow once** / **Always allow this exact request** / **Deny** | Answers to a permission request. The middle one saves a rule | iOS |
| **Saves the rule** | The exact rule an always-allow answer will write | iOS |
| **Start task** | Give an agent a folder and a prompt | macOS, iOS |
| **Reply to the agent…** | Free-text answer to a request | iOS |
| **Answer in the terminal** | This question is showing in both places; answer it where it was asked | iOS |
| **Other Macs** | iCloud settings sync between Macs (under Sync) | macOS Settings |
| **Desk display** | The ESP32 board's panel: **Brightness** (25/50/75/100%), **Dim at night**, **Celebrate quota resets**, **Boot animation**, and which **Pages** BOOT cycles. Shows the board's **Firmware**, **Connection** and **Last seen**. Host-owned; the board applies it on its next poll. See [esp32.md](esp32.md) | macOS Settings |
| **Telemetry** | Local payload preview plus thresholded Community Pulse (weekly growth, new vs returning, builds, CPU, macOS, countries, services, models, features), shown only while anonymous diagnostics is enabled. The newest week is marked as still filling and never differenced against a finished one | macOS Settings |
| **Computers** | Macs paired to this iPhone; each token stays in the iPhone Keychain | iOS Settings |
| **Add computer** | Pair another Mac without replacing the saved pairing | iOS Settings |

Do not title the activity feed **GitHub**. That word is reserved for the
**GitHub Actions** source.

### Activity row states

Every row says its state in a word as well as a colour and a glyph, so the
feed still reads in greyscale. Host status string → word, mapped once in
`Shared/ActivityStatus.swift`:

| Host status | Word | Reads as |
|---|---|---|
| `error`, `failure` | **Failed** | Red, and sorted above the rest under **N need attention**. Tinted per row — the feed is one list of equal items, not a box stacked on a list |
| `building`, `initializing` | **Building** | Amber. In flight |
| `running` | **Running** | Amber. In flight |
| `review_request` | **Review** | Orange, and sorted under **Needs attention**. A PR on a watched repo wants your review. Caption carries repo leaf, opener `@login`, and `#number` |
| `assigned` | **Assigned** | Orange, same group. An issue or PR on a watched repo is assigned to you. Same caption shape |
| `mention` | **Mention** | Orange, same group. An open issue or PR on a watched repo @mentions you. Same caption shape |
| `queued`, `pending` | **Queued** | Amber. In flight |
| `ready` | **Deployed** | Green. Finished well |
| `success`, `completed` | **Passed** | Green. Finished well |
| `canceled` | **Canceled** | Grey — nobody has to go look at it, so it is not red |
| `pushed` | **Pushed** | Grey. Routine |
| `local` | **Local** | Grey. Committed, not pushed |
| `committed` | **Committed** | Grey. Routine |

Green means *finished well*, never *happened*. A pushed commit is grey; if
push turned green, the word would stop distinguishing a shipped deploy from a
`git push`.

## Charts & meters

| Term | Meaning | API / id |
|---|---|---|
| **Burndown** | Remaining-% over time for one pool | `burndown` |
| **Overall burndown** | Same chart across all coding providers | `burndown` / `burndown_primary` |
| **Daily burn** | Per-day %-point burn across providers | `by_day` |
| **% / day** | Unit subtitle for daily burn | — |
| **Headroom rings** | Concentric usage + pace indicator | see `docs/rings.md` |
| **N% used** | The rings' reading | — |
| **N% left** | The burndown's reading (remaining) | — |
| **On pace** / **Over pace** | Whether the current burn lands inside the window | `verdict`, `headline` |
| **N% to spare** / **N% over** | Signed distance from an even spend | `headline` |
| **Empty Thu** | Forecast reaches zero before the pool renews | — |

Rings say **used**, burndown says **left**. Keep the word attached to the
number wherever both glyphs share a surface — the watch's rectangular
complication does — so they never look like one figure disagreeing with
itself. Where only one date fits, **Empty** outranks **Resets**.

Pace has two words and only two: **On pace** and **Over pace**. "Ahead of
pace" cuts both ways to a casual reader, and "On track" was a third word for
a state that already had one. `headline` and `verdict` say the same pair, so
moving between the board and the Mac never teaches a second vocabulary.

Slack against an even spend is signed and in percent: **12% to spare**,
**4% over**. The old `_points()` ran `abs()` over a signed delta and called
the result "4 points", so a pool four behind read exactly like a pool four
ahead.

**Every burndown says which frame it draws.** There are two rules and they
disagree on purpose, so an unlabelled pair reads as one chart that keeps
changing its mind:

| Chart | Frame | Subtitle |
|---|---|---|
| Overall burndown | Clock-anchored: three and a half days either side of today | **7 days around today** (**±3.5d** on the watch) |
| Provider burndown | Reset-anchored: the pool's own window | **This window** |
| Provider burndown, monthly pool | Seven days clipped out of a window too long to draw | **7 days of this window** |

The Usage chart gets no choice: provider windows open and reset at different
times, so there is no single window to hang its axis on — which is also why it
alone has no budget diagonal. A provider chart gets no choice either, in the
other direction: the budget diagonal runs from the window's start to its reset,
so the axis has to be that window or the diagonal measures nothing. This is not
a preference and there is no toggle for it (see [product.md](product.md), "What
earns a Setting").

`frameLabel` on each chart's `Domain` picks the string, so no surface writes
these words itself, and `showsWholeWindow` is derived from the domain rather
than stored — a monthly chart cannot end up captioned "This window". The board
is the one surface with no subtitle: its header row is spent on the `verdict`,
and its weekday axis already names the days on screen.

Don’t restate “all quotas” in the Usage subtitle. Its domain is a fixed
local week — today−3.5d … today+3.5d — so history stays readable and far-out resets
don’t stretch the axis. Forecasts crop at each reset (and at empty); each
in-range reset is an accent dotted vertical rule, and the legend shows
**Resets …**.

A reset the provider hands out early — Codex clearing a week you had already
spent — is a **granted** reset. On the **Codex** burndown (not Usage) it is a
solid accent rule where an upcoming one is dotted, captioned **Reset granted ·
N% back**. Percent even here: Codex genuinely grants credits, but the number
in this caption is a share of the window the chart draws, not a credit count.
Scheduled rolls get no mark; the axis already ends on those. The
host detects them in the sample log (`burndown[].resets`), so the mark and the
history agree by construction. **Recent resets** under the chart is a calendar
heatmap of those grants — a day is lit or not (resets are binary; `% back`
lives in the day detail). Provider tint = **global** grant (codex-resets.com,
optionally matched to what this Mac saw); amber = a **banked credit you
spent**. Weekly auto-resets stay off the grid — the chart axis already ends
on those. For Codex week the host also merges the public feed at
[codex-resets.com](https://codex-resets.com) (every verified @thsottiaux
announcement), matched to local detections within a few hours when both exist,
so the grid reaches backward past what this Mac observed and keeps filling as
new announcements land.

A banked Codex reset credit has its own deadline, shown on the Codex quota card
as **N reset credits** with expiry labels — not as a renewal mark on Usage.

Pool-scoped burndown titles are `"{pool title} burndown"` (e.g. `Weekly burndown`).
Provider charts share one X-axis rule across Mac / iOS / ESP32: at most **seven
weekday-named columns** (never day-of-month numbers); windows longer than a week
clip to seven days covering now; sub-day sessions get hour ticks instead of a
blank axis.

## Status

| Term | Meaning |
|---|---|
| **Connected** | iOS link health when the Mac host is reachable. Token-backed Integrations hub rows prefer the live poll caption (**3 projects**, **Supabase token rejected**) over Connected-from-Keychain alone |
| **Not connected** | Integrations hub / detail when nothing is pasted yet |
| **Keychain** | Integration detail Credential row: a token is stored on this Mac. Status beside it says whether the last poll accepted it. The empty SecureField shows `••••••••••••` as a prompt — paste replaces |
| **Mac unavailable** | iOS cannot reach the host |
| **Host not answering** | Mac popover header and setup body while the local host is down — same fact as the menu-bar tooltip. Not a Foundation URLError |
| **Starting the host…** | Setup card while Start host / auto-start is in flight |
| **Local LaunchAgent** | Settings → Host → Process, when launchd owns the host |
| **Local process · with Headroom** | Same row, when the app owns the host and quitting stops it |
| **Reconnecting…** | Host answered again; forcing a source sync |
| **Refreshing…** | In-flight poll / sync while already connected |
| **All clear** | Healthy summary — host default, Attention card, and the Activity feed with nothing failing |
| **Needs attention** | Warning fallback when a reason has no summary; the iPhone Attention section header, and counted as **N need attention** above the Mac's Activity feed |
| **Collecting history** | Burndown empty / early verdict |
| **Not updating** | The host is replaying a source's last good numbers; the age travels with it (**Not updating · 2 hours ago**) |
| **Paused** | The host is deliberately not refreshing — usually a provider rate limit (`stale_cause: rate_limited`). Carries the wait when known (**Paused · retries in 5m**). Secondary, not orange |
| **Needs sign-in** | That source's credential is missing or was rejected — `auth_required` on `providers[]` / `sources[]`. Ages the same way. Attention names the fix (`claude /login`, `codex login`, sign in to Cursor, …) |
| **Dismiss** | Swipe one row out of the iPhone Attention queue — a passive coding-agent notice, a rollup reason, or a failed feed row |
| **Dismiss all** | Hide every row in an Attention section and ack the rollup so the menu-bar pip goes out too. iPhone: per section (passive agent notices, or **Needs attention**). Mac: the one Attention control (no per-row swipe) |
| **Refresh all** | Force-sync every source |
| **Active** | Mac Settings → Providers: the services you track, rich reorderable rows with live usage. A switched-off row stays here as **Off** — paused, configured, not polled |
| **Library** | Mac Settings → Providers: the **AI providers** you don't track, as compact chips |
| **Move to Library** | The Active row's ✕: stop tracking a service. Never touches credentials — Headroom has no sign-ins to revoke, so wording must not imply signing out |
| **Add account…** | Library chip / **Add account** section button for a multi-account-capable provider; opens the add sheet that carries the credential-path prose. Not nested under Active meter rows |
| **not detected** | A Library chip whose credential has no local trace to import — dimmed, never a dead toggle. On a service that takes accounts the chip stays live and opens **Add account…** instead |

**Needs sign-in** outranks **Paused** and **Not updating** wherever both are
true — a dead login also freezes the numbers. **Paused** outranks **Not
updating** when the freeze is a rate limit the host is already backing off
from. Only Needs sign-in paints a meter orange; the other two stay secondary so a
backoff does not read as something to go and fix. `QuotaProviderInfo.statusNote`
picks between them so no surface has to.

Do not use **All clear** for connection health — that word belongs on the
Attention card. The Usage status row uses **Connected** / **Mac unavailable**.

### Service health

Supabase, Plausible, PostHog and the Supabase advisors sit on the same axis as source
health: **does the reader wait, or go and do something.** The host's own
`error` string wins when there is one; these are the fallbacks for when there
isn't. `HeadroomCopy.serviceStatus(_:configured:)` picks between them.

| Term | Meaning |
|---|---|
| **N needs a key** | `configured == false` — nothing pasted yet. Keys live under Settings → Integrations |
| **N not reporting** | Configured, and it did not answer. Nothing to do but wait |
| **Plan unknown** | The provider didn't name the plan. Not a failure; the status label beside it already carries health |

**Nothing says "unavailable" any more** except **Mac unavailable**, which is
the transport and keeps the word. It had grown to cover a missing key, a
failed fetch, a dead host and an absent plan name — four situations, three of
them actionable, one sentence for all of them. The menu bar tooltip says
**host not answering**; "backend" is not a word this product uses.

## Empty states

Keep these short; don’t explain the pipeline.

| Term | Use |
|---|---|
| **No history yet** | Burndown chart empty |
| **No burn history yet** | Daily burn empty |
| **No coding sources** | No quota providers enabled |
| **No activity yet** | Activity feed empty |
| **No local servers** | Local servers empty |
| **No Xcode builds** | Xcode builds empty |
| **Waiting for Mac sync** | iOS before first payload |
| **Open Headroom on iPhone** | Watch before first payload — it cannot reach the Mac itself |
| **Open** | Permalink glyph (`link`) on Activity rows and detail chrome — opens the source URL. Row body drills into the leaf; only this glyph opens the browser | macOS, iOS |
| **Searching…** | Bonjour discovery in progress |

## Welcome (macOS first run)

The eight-pane window shown once per install, and again from Settings →
**Show welcome**. It is a window rather than popover content because the
popover is `.transient` and hangs off the very icon the walkthrough points at.

Only names reused across surfaces live in `HeadroomCopy`; the pane prose is
macOS-only and stays in `macos/Sources/WelcomeView.swift`.

| Term | Use |
|---|---|
| **Welcome to Headroom** | Window title and first pane heading |
| **Headroom lives here** | Callout pointing at the menu bar icon |
| **Start using Headroom** | Final pane's button; closes the window and opens the dashboard |
| **Show welcome** | Settings row that reopens the window |

## Providers (host registry titles)

Ids stay lowercase; titles are user-facing. `group` is the Settings /
onboarding section the row lands in — never mix the two lists in one
undifferentiated pile of toggles:

| id | Title | group |
|---|---|---|
| `claude` | Claude | `ai` |
| `codex` | Codex | `ai` |
| `cursor` | Cursor | `ai` |
| `copilot` | Copilot | `ai` |
| `gemini` | Gemini | `ai` |
| `windsurf` | Windsurf | `ai` |
| `jetbrains` | JetBrains AI | `ai` |
| `zed` | Zed | `ai` |
| `openrouter` | OpenRouter | `ai` |
| `ai-gateway` | AI Gateway | `ai` |
| `claude-status` | Claude Status | `ai` |
| `vercel` | Vercel | `devtools` |
| `git` | Git | `devtools` |
| `github` | GitHub Actions | `devtools` |
| `supabase` | Supabase | `devtools` |
| `plausible` | Plausible | `devtools` |
| `posthog` | PostHog | `devtools` |
| `sentry` | Sentry | `devtools` |
| `datadog` | Datadog | `devtools` |
| `axiom` | Axiom | `devtools` |
| `local` | Local | `devtools` |

**Extra accounts** is the concept; the chrome is **Add account** under
Settings → Providers → Library (not a separate Settings root). "Logins" and
"identities" are not used. An extra account (`claude:work`) keeps
a full `title` of `Claude · Work` for
text-only surfaces (Settings, menu bar, the board). The host also ships
`label` (`Work`). Anywhere a brand mark or accent already names the tool —
dashboard tabs, rings, iPhone rows, widgets — clients draw `label` instead so
three Claude accounts do not all truncate to "Claude…".

That rule is about what is *drawn*. Spoken strings are text-only by
definition — there is no mark beside them — so VoiceOver gets the full `title`
even on those same surfaces (see [`docs/rings.md`](rings.md)). Neither string
is the id: `claude:work` is identity, and reading it aloud gives "claude colon
work".

## Source groups

Membership comes from `host/sources_config.py` (`GROUP_AI` / `GROUP_DEVTOOLS`,
served as `sources[].group`). Section titles are chrome and live in
`Shared/HeadroomCopy.swift`:

| Term | Meaning | Surfaces |
|---|---|---|
| **AI coding tools** | Claude / Codex / Cursor / Copilot / … — plan left; OpenRouter and AI Gateway are prepaid balances with a pasted key; Claude Status watches status.claude.com | macOS Settings + onboarding, iOS Settings |
| **Dev tools** | Vercel, Git, Actions, Supabase, Plausible, PostHog, Sentry, Datadog, Axiom, local servers / builds | Integrations catalog on both platforms |
| **API balances** | OpenRouter and Vercel AI Gateway prepaid credits — paste a key on the Mac; account use paints on Activity | Integrations catalog · Activity |

Don't call the first group **Sources** on its own in the UI; call it
**Providers**. Activity is the ordered stack of Integration watches that paint
blocks — not a separate Settings list.

## Focus (the top 3)

The providers the compact surfaces draw: menu-bar icon, the iOS widget, and
the ESP32 glance slots. Picked host-side from the pinned order (enabled only,
`sources_config.FOCUS_LIMIT`) and served as `focus` in `/usage`, so no surface
computes its own top-N. Drag to reorder under Mac Settings → Providers.

Say **top 3** in user-facing copy, not "focus" — that word is API vocabulary.

### Menu bar icon

Mac Settings → General → **Menu bar icon** picks the glyph’s reading. Same
three slots either way; only the mark changes. **Invert** is a separate
toggle that flips whichever style is active.

| Option | Family | What each slot shows |
|---|---|---|
| **Remaining** | Fuel | Fill height = quota left (today’s tanks) |
| **Pace** | Pace | Dot above/below even-spend midline; `tanh((used − pace) / 8)` so small gaps move more than big ones |
| **Invert** | — | Remaining fills by used instead of left; Pace flips over/under |

Do not name the Pace option after the game metaphor in chrome — **Pace** is
the glossary word; the midline-and-dot shape is just how it draws.

A **Preview** strip sits above the picker, drawn by the same renderer the
status item uses at the same 18pt, so the two styles can be compared without
looking up at the menu bar. It draws the live top 3. When no coding provider
is on it falls back to sample numbers and says so — the glyph is real, the
numbers in it are not.

The ESP32 glance takes the same two words for its upper half, with **Rings**
in place of Remaining — the board's default paints used, not left, so calling
it Remaining would be a second meaning for one word. Held on the board, not
synced from the Mac ([docs/esp32.md](esp32.md)).

Pool titles (`Session`, `Weekly`, `Total`, `API`, …) come from the host
`PoolSpec` and should not be re-hardcoded in UI chrome when the API supplies them.

## Colour

Colour carries one meaning per surface. Don't borrow it for emphasis.

| Where | Rule |
|---|---|
| **Quota meters, burndown** | Provider accent only. Exhaustion desaturates (`tint.drained()`); nothing turns red or orange. Needs-sign-in is the one meter exception — it oranges, because that is actionable |
| **Attention card, source health dots, Activity rows** | Green / orange / red for alarm; soft amber only for in-flight (building, syncing) and soft stale. Never colour alone: the row carries a glyph and a word too |
| **Provider accent** | `sources_config.Source.accent` → `providers[].accent` / `sources[].accent`, mirrored by firmware `COL_*` and `UsageProvider.tint` |

**Soft amber is not a warning.** It means something is happening or not fresh.
**Orange is the warn stop** — Attention `warn`, review/mention/assigned, connection
trouble, needs-sign-in. **Red is critical / failed.** The two used to share amber,
which flattened warn into "stuff is happening."

**The burndown card never alarms.** "Runs out tomorrow 04:18" is a reading, and
the words already deliver it; painting it red says the same thing a second
time, louder. Burning exceeding Budget is visible in the cell beside it. This
was settled in `fd29592` ("drop distinct critical red tint") and then
reintroduced by a later refactor, so `scripts/check-glossary-copy.sh` now fails
the build if `Color.red` / `.orange` reappears in `BurndownCard.swift`,
`QuotaSection.swift`, or `DailyBurnCard.swift`.

If a genuinely new state needs to shout, add it to the Attention card — not to
a meter.

## What stays surface-specific

- Layout / IA (popover tabs vs iOS tab bar vs ESP32 pages)
- Host prose (`headline`, `verdict`, attention `reasons`)
- Accessibility strings that add context around a base term
