# Changelog

Marketing versions come from [`host/VERSION`](host/VERSION); every entry below
is a `v`-prefixed git tag. Apple build numbers (`git rev-list --count HEAD`)
are not tracked here because they move on every commit.

Add a section here before cutting a tag. `scripts/cut-release.sh` refuses to
tag a version that has no entry.

## 2.1.2 — 2026-09-03

### Added

- **Settings shows the menu bar glyph before you pick it.** General draws a
  Preview strip above the icon picker, using the same renderer the status item
  uses at the same size, on a dimmed strip with a wifi glyph, a battery and a
  clock. Switching between Remaining and Pace, or flipping Invert, changes the
  mark in front of you instead of sending you up to the menu bar and back. It
  draws your own top three. With no coding provider on it falls back to sample
  numbers and says so: the glyph is real, the numbers in it are not.

## 2.1.1 — 2026-09-03

### Fixed

- **GitHub review requests and assignments stopped ageing out of Attention.**
  An open issue keeps the status word `assigned` for as long as it is open, so
  a request nobody answered a year ago sat in the queue with the same weight as
  one from this morning. Dismissing it lasted until the next launch, because
  dismissal is per-run memory and the row came straight back out of the search.
  Inbox rows now leave Attention after 14 days untouched, the same way a failed
  Actions run leaves after 24 hours. They keep their word and stay in the
  Activity feed, so nothing disappears. They stop lighting the pip.

### Changed

- The host now says per row whether it belongs on Attention
  (`activity[].needs_attention`), rather than every client working it out from
  the status word. Older hosts do not send the key and behave exactly as
  before. See [`docs/contract.md`](docs/contract.md).

## 2.1.0 — 2026-08-26

### Changed

- **The desk board no longer dims at night.** It followed local solar times
  and a fixed 22:00 bedtime, and stepped the panel to 30% after sunset and
  10% after bedtime. It now holds one level all day and all night.
  `PANEL_BRIGHTNESS` in `firmware/src/config.h` sets that level and replaces
  the `BRIGHTNESS_*` set. An existing `config.h` keeps building.
- Agent docs record the rotated-grant trap behind the 2.0.9 fix.

## 2.0.9 — 2026-08-26

### Fixed

- **Claude kept saying Needs sign-in after `claude /login`.** Headroom holds
  its own copy of the Claude OAuth blob and prefers it over the Keychain, and
  it drops that copy once the refresh expiry the blob states has passed. A new
  `/login` does not wait for that date: it rotates the grant, and the replaced
  copy goes on stating an expiry days out. So the copy still tested as live,
  the read returned it before it looked at the Keychain, and every refresh came
  back `invalid_grant` — for as long as the old date said the login was fine.
  Nothing recovered it, because the one arm that handles a dead grant only
  cleared the in-memory blob and the next poll re-read the same file. The token
  endpoint is now the witness: an `invalid_grant` expires Headroom's copy on
  disk, so the next read falls through to the Keychain, and records the
  rejected token so the same grant is not imported straight back from there.

## 2.0.8 — 2026-08-26

### Fixed

- **Claude token history counted one assistant message once per content block**
  (#28, reported by @tonydzi). Claude Code writes one JSONL line per content
  block — thinking, text, each `tool_use` — and every line repeats the same
  `message.usage` under the same `message.id`. Both readers treated each line
  as its own API call, so daily tokens, `cost_usd` and the model mix were
  multiplied by the block count of every message. Measured x1.83 over 120 real
  session files here, x2.12 on the reporter's tree; 45% of the tokens on record
  were the same calls counted again. The blocks of a message are written
  consecutively, so a shared `MessageDeduper` skips a line repeating the id of
  the line before it — O(1) per file, which lets the live tail keep one per open
  session and catch a message that straddles a poll boundary. Records with no
  `message.id` and subagent runs, which carry their own ids, are untouched.
  `claude_history.json` bumps its schema so stores already written under the
  inflated count are rebuilt rather than kept.

- **The desk display's round panel stacked spend figures in three columns.**
  Three columns on a circle are width-starved at the figures' height, which
  capped the type size and left the lower band empty. One figure per row uses
  most of the chord and fills the band the way the burn and history charts
  already do. Each row is sized against its own caption and value rather than
  the widest of all three, because the long label and the long figure never
  share a row.

- **The dashboard's mode tabs scrolled away with the content.** They sat inside
  the scroll view, so they left the window as soon as a list was long enough to
  move. They now sit in the popover chrome above the divider, where the rest of
  the window furniture is.

## 2.0.7 — 2026-08-21

### Added

- **The desk display can read pace instead of rings.** Hold a glance slot and
  the upper half switches to the macOS menu bar's other glyph: one pill per
  provider with a line at even spend, and an accent mark riding above the line
  when that provider is over pace and below when it is under, on the same
  `tanh((used − pace) / 8)` curve the Mac uses. It drops the arc, so it answers
  whether the burn is ahead or behind and nothing else. Rings stay the default,
  the board keeps its own choice in NVS, and a tap still opens the detail page.

- **First-boot Wi-Fi setup for the ESP32.** When no saved network can be
  reached, the board starts a `Headroom-XXXX` setup network with a captive
  portal, scans nearby networks, saves the selected credentials in NVS, and
  reconnects after setup.
- **USB fallback controls in the Mac app.** Settings now exposes the detected
  USB serial device, lets the user enable USB when Wi-Fi is unavailable, and
  reports whether the host is waiting for or actively using the board.

## 2.0.6 — 2026-08-11

### Added

- **Edit Widget picks the provider a tile draws.** Right-click a widget on the
  Mac, touch and hold on iPhone, and the picker lists the providers your Mac
  reports. A new tile starts on the one closest to running out — whichever
  forecast runs dry first, which is the one the menu bar and the watch already
  lead with — and it stays on that provider rather than following whichever is
  emptying fastest today. "All providers" is one item down and draws the
  combined burndown. Widgets already on a screen keep drawing every provider,
  as they did before. Thanks @paulmars for the report (#27).
- **`build-app.sh --release --sign`** signs a local build with Developer ID and
  stops before notarization. The widget's app group is `TEAMID.group.…`, read
  off its own signature, so an ad-hoc build can never reach the cache the app
  writes — testing a widget locally used to mean a full notarized release.

### Changed

- **Every widget family names every provider it has before it draws a chart.**
  The small size drew the first provider alone, so a second one was invisible
  rather than absent; it now gives one provider the whole tile and shares it
  between several. The wide size leads with a row naming each provider and what
  it has left, and adds the chart under it.
- `docs/telemetry.md` records that `wrangler d1 execute` needs `--yes` in a
  non-interactive shell, and how to verify the migration landed. A repo-root
  `.wrangler` cache is now ignored.

### Fixed

- **The medium widget drew a blank box** (#27). Two causes, both able to empty
  the tile on their own. It asked whether a provider carried a burndown *key*
  rather than whether the key held a *stroke*, so a cache written by an older
  build sent it down the chart branch with nothing to draw and no words to fall
  back on. And it read the last sample as `actual.last[0]`, which is an index
  out of range on a row that is not a `[time, remaining]` pair — in a widget
  extension that is not a wrong number, it is an empty tile with nothing to say
  why. The same unguarded read is gone from the Mac's burndown card and from
  the cache writer, where it could take the app down instead.
- **`install-local-release.sh` no longer ad-hoc re-signs what it installs.** It
  replaced the signature on both the staging copy and the installed one, which
  threw away the team a signed build had just earned — and the failure reads as
  a broken widget rather than as a lost entitlement.

## 2.0.5 — 2026-08-11

### Added

- **New vs returning Macs** in Community Pulse. The weekly dedupe key rotates
  by design, so nothing could tell a returning Mac from a new one and
  retention was invisible. Each Mac now answers locally: it compares the
  period it last submitted against the one it is reporting and sends a single
  word — `new`, `returning`, or `reactivated`. The word carries no identity
  and cannot be joined across weeks, so the intake learns that some Mac came
  back, never which one. Settings and the public page both draw the split, and
  a line reading what share of the previous week returned.
- **Reset celebration on the board.** An additive `device_effect` envelope lets
  the host queue a celebration for the ESP32's next poll, either when an
  observed burndown window rolls or from a private-network `/device/effect`
  trigger. Firmware stores the last consumed effect id, so a reboot does not
  replay it. See [`docs/esp32.md`](docs/esp32.md).
- **`scripts/install-local-release.sh`**, a one-shot install of a local release
  build over `/Applications`.

### Changed

- **Community Pulse compares weeks that finished.** A Mac reports once per ISO
  week at its first launch inside it, so the newest count fills over seven
  days. Both clients differenced that partial week against a complete one,
  which printed a large negative week-over-week figure every Monday and
  Tuesday — 36 hours into a week it read −37 against a launch week that had
  had all seven days. The payload now marks the newest week `in_progress`, the
  app and page difference the last two weeks that closed, the filling bar is
  faded, and every breakdown under `latest` says once that it is week to date.
  Weeks before the first report are trimmed rather than drawn as zeroes.
- **Weekly counts are kept, and raw batches expire at 180 days** instead of 30.
  Pruning `telemetry_periods` on the batch schedule capped the growth chart at
  about five bars forever. That table holds a period, a Mac count, and the
  cohort counts; no column describes a Mac, so it is now kept. Existing D1
  needs [`telemetry/migrations/004_growth_cohorts.sql`](telemetry/migrations/004_growth_cohorts.sql),
  applied **before** deploying the Worker. See
  [`docs/telemetry.md`](docs/telemetry.md).
- **App updates list Current beside Latest.** One row used to collapse to the
  installed version, so a copy that could not install hid the version the feed
  reported. `UpdateChecker` now tracks the last successful fetch independent of
  whether an update is available.
- **Community Pulse link** in About reads as a button rather than caption text.

### Fixed

- **GitHub star counts stop freezing.** The About view used
  `returnCacheDataElseLoad` and the community page cached its fetch, so both
  served the first response forever instead of honouring GitHub's `max-age=60`.

## 2.0.4 — 2026-08-10

### Fixed

- **Watch complications follow background refresh.** iPhone background fetch
  already wrote the home-screen widget cache but never forwarded it over
  WatchConnectivity, so the wrist could sit half a day on the last open of
  the phone app. The same push path foreground refresh uses now runs after
  a successful background fetch.

## 2.0.3 — 2026-08-09

### Added

- **Menu bar Invert.** Settings → General → Invert flips whichever glyph style
  is active: Remaining fills by used instead of left, Pace swaps over/under.
  Tooltip wording follows so the hover still matches the mark.

### Changed

- **Needs sign-in names the command.** Attention (and the meter error) now say
  what to run when a coding provider is installed but not authed — `claude
  /login`, `codex login`, `gh auth login`, `grok login`, or “sign in to Cursor”
  for IDE-only tools — instead of the generic “log in with the tool again”.
  Missing credentials also set `auth_required` for Codex, Cursor, Gemini,
  Copilot, Grok, Zed and Windsurf so the orange Needs sign-in path fires for
  them the way it already did for Claude.

## 2.0.2 — 2026-08-08

### Fixed

- **Grok source stops logging its own users out.** `_acp_billing()` spawned
  `grok agent stdio` and SIGKILLed it the instant the billing answer arrived.
  The CLI rotates `~/.grok/auth.json` during startup — lock, then rewrite —
  and a kill landing inside that window deleted the login file outright,
  leaving `auth.json.lock` orphaned and the source reporting "not signed in to
  Grok CLI" until someone ran `grok login` again. Observed twice in five days
  of production polling. Shutdown now closes stdin so the agent sees EOF and
  exits cleanly, escalating to `terminate` then `kill` only if it lingers past
  two 3s timeouts; poll cost is unchanged. (#26)

## 2.0.1 — 2026-08-08

### Fixed

- **Claude stops asking for a sign-in it already has.** Headroom imported
  Claude Code's credentials once and then preferred its own copy forever. When
  that copy's refresh token expired the copy still looked valid — an access
  token stays a non-empty string long after it stops working — so every poll
  tried to renew a dead grant, and the good token each `claude login` wrote to
  the Keychain was never read. A stored login whose refresh token has expired
  no longer counts as credentials, so re-import is how the daemon recovers
  rather than something that only ever happened once. Logins from before the
  expiry field existed are unaffected.
- **The sign-in error names the reason instead of a dead URL.** Refresh also
  tried a fallback host whose route now 404s, and reported whichever error
  came last — so `invalid_grant: Refresh token not found or invalid` reached
  the menu bar as `HTTP 404 from console.anthropic.com`. That host is out of
  the list, a definitive `invalid_grant` stops the search instead of falling
  through to it, and a 404 can no longer outrank an answer that says what is
  actually wrong.
- **An expired grant reads as "needs sign-in" on every path.** One route
  raised it through a generic handler, which dropped the flag separating the
  single failure a person can fix from an outage they can only wait out.

## 2.0.0 — 2026-08-07

### Changed

- **Versioning:** after `1.9.9` the next release is `2.0.0`. Minor and patch
  stay single digits — never `1.10.0`. That number already shipped as an
  overshoot (same Plausible histograms); this tag is the roll it was owed, and
  the feed points here. `AGENTS.md`, `ship-inventory.sh`, and `version-env.sh`
  enforce the rule going forward. Same pattern as the old `1.0.10` / `1.0.11`
  tags.

## 1.10.0 — 2026-08-07

### Added

- **Plausible visitor histograms** on Activity rows and site detail — daily
  for 7d/30d windows, hourly for day/24h, same role as OpenRouter spend
  charts.

### Fixed

- **AI Gateway Create token** opens the API keys page, not the gateway home.

## 1.9.9 — 2026-08-07

### Fixed

- **Activity no longer shows forever-fresh grants from a frozen clock.** The
  live roll journal refuses and hides grants stamped ahead of now, so a
  burndown test leak cannot leave an `0s` Granted row on the desk.

## 1.9.8 — 2026-08-07

### Added

- **Changelog in About** — opens the same `CHANGELOG.md` the release pipeline
  already ships, readable offline. The Release build fails if the bundled
  file is missing.

## 1.9.7 — 2026-08-07

### Added

- **Menu bar icon Remaining or Pace** in Settings → General. Remaining keeps
  the fuel tanks; Pace places a mark above or below even spend so small gaps
  move more than big ones. Tooltips follow the same reading.

## 1.9.6 — 2026-08-07

### Added

- **Remove background service** in Settings → General → Host — stops the host,
  removes its login item for both the current and the legacy label, and quits.
  It shows only while there is one to remove, and it is the way to leave
  cleanly before deleting the app. Your settings, tokens and history stay in
  `~/.headroom`.

### Fixed

- **Deleting Headroom no longer leaves a service behind** — the login item
  names a script inside the app bundle, so removing the app left launchd
  running a job whose target was gone, respawning it every few seconds with
  nothing left to clean it up. Reported by @leolobato.

## 1.9.5 — 2026-08-07

### Added

- **Choose who runs the host** — Settings → General → Host gains *Keep the
  host running when Headroom is closed*. On, launchd owns the host and it
  serves the board, iPhone and Watch whether or not the app is open. Off,
  Headroom owns it: quitting the app stops the host, and `kill` sticks
  instead of being undone by launchd seconds later. On by default, so nothing
  changes unless you ask for it. Suggested by @leolobato.

### Changed

- **The host can exit with its parent** — a new `--exit-with-pid` makes the
  host watch the process that spawned it and stop when that process goes. A
  crash or a force quit can no longer leave a child holding port 8737, which
  the next launch would see as a foreign host it cannot stop.
- **App-owned host restarts have a budget** — four, with a widening delay,
  and never after a clean exit. A clean exit means someone stopped the host on
  purpose, or it stood down from a port something else owns. Restarting into
  either is the crash loop 1.9.3 shipped.
- **Docs: the host merges into the app** — `docs/host-merge.md` replaces the
  Swift host study. It records the decision to move the server into
  `Headroom.app` and retire the Python host, corrects the study's reason for
  ruling that out, and sets the phases.

## 1.9.4 — 2026-08-06

### Fixed

- **LaunchAgent host crash loop** — 1.9.3 called `usb_bridge.enabled()`
  without defining it, so startup died after daemon threads began and Python
  aborted in `Py_FinalizeEx` under KeepAlive. The USB bridge is opt-in again
  (`HEADROOM_ENABLE_USB=1`), `/dev/null` stays off the board, and SIGTERM
  exits without finalize so launchd restarts stay clean.

## 1.9.3 — 2026-08-06

### Fixed

- **Fewer Keychain password prompts on macOS** — first-run source detection
  and Zed quota polling no longer shell out to `/usr/bin/security`; reads go
  through `SecItemCopyMatching` with fail-closed auth UI so background polls
  do not loop SecurityAgent. Zed Deny sticks until you refresh the source in
  Settings, same as Claude.

## 1.9.2 — 2026-08-06

### Added

- **Per-account color picker** in Settings → Providers — each login under a
  provider gets its own swatch; "Derived shade" restores the auto tint that
  follows the provider base.
- **Renamable provider names** — click a provider title in Settings to edit;
  account rows pick up the new brand everywhere.

### Fixed

- **Provider color changes now reach every account** — stale per-account accent
  keys left by older Settings builds are cleared when the provider swatch
  moves, so sibling logins follow the new base instead of keeping the old hex.
  Thanks @leolobato for the report.

## 1.9.1 — 2026-08-05

### Fixed

- **Provider account colors now use distinct same-service shades** in Settings
  and across the usage surfaces, while preserving explicit account overrides.
  Thanks @leolobato for reporting the bug.

## 1.9.0 — 2026-08-05

### Changed

- **Update chrome shows installed > latest** in the popover footer and General
  settings (for example `1.8.9 > 1.9.0`), with a spoken accessibility label.
- **Community Pulse publishes smaller groups** (floor drops from five Macs to
  one) and only marketing `X.Y.Z` builds at or below the update-feed release.
- **README and marketing screenshots** refresh the Mac/iPhone shots, pair the
  ESP32 glance next to the menu bar, and shape demo burndown curves for the
  screenshot pipeline.

## 1.8.9 — 2026-08-05

### Added

- **`scripts/ship-inventory.sh`** prints the ship queue: unshipped commits since
  the last tag, dirty paths, next patch number, and recent Release runs.

### Changed

- **Release workflow queues** instead of running in parallel, so duplicate
  TestFlight uploads for the same version are less likely.

## 1.8.8 — 2026-08-05

### Added

- **Agent alerts toggle** in Coding agents settings. Passive notices such as
  “Ready for your next instruction” can be hidden; questions, choices, and
  approvals always stay visible.

### Changed

- **Choice requests render as pills** on the agent request sheet instead of
  expecting free-text only.

## 1.8.7 — 2026-08-05

### Fixed

- **Row tap opens the leaf; only the `link` glyph opens the browser.** Attention
  leftover reasons no longer treat the whole row as a permalink; Integrations
  catalog rows open their settings leaf from the title/status, not just the
  chevron; permalink control keeps a tight hit target so it cannot steal the
  drill-in.

### Changed

- **Mac Attention Dismiss all matches iPhone.** Hides every queued failure /
  reason on this surface and acks the rollup, instead of only clearing the
  menu-bar pip while the rows stayed put. Label is **Dismiss all**, not Clear.

## 1.8.6 — 2026-08-05

### Fixed

- **Host refuses rebound and cross-site control requests.** Origin checks block
  browser-driven POSTs that are not same-site; synced API host allowlists are
  validated on write.

## 1.8.5 — 2026-08-05

### Fixed

- **Widget cache from another build no longer blanks the watch face.** Lossy
  decode skips bad keys instead of failing the whole snapshot; contract tests
  cover cross-build widget payloads.
- **Demo fixture numbers no longer masquerade as yours** on widget and watch
  when the snapshot is clearly sample data.

## 1.8.4 — 2026-08-05

### Fixed

- **Settings edits no longer vanish silently.** Host config POST failures surface
  in the pane; iPhone settings appear in the Mac app when the companion is
  paired.

### Changed

- **Timezone is settable** in General settings (host persists `timezone`).
- **Self-hosted Plausible** accepts a custom API base URL, not just the cloud
  host.
- **Poll interval and repo discovery cap** are product constants again — not
  exposed as tunables that drifted between surfaces.

## 1.8.3 — 2026-08-04

### Changed

- **Recent resets heatmap uses a denser, shorter window.** Cap drops from
  ~400 days to ~200 so Mac popover cells stay readable; cell size roughly
  doubles and the grid height follows the computed cell size.

## 1.8.2 — 2026-08-04

### Changed

- **OpenRouter and AI Gateway live on Activity, not Usage.** Account-use
  panels sit in the Integrations Activity stack; Usage rings and tabs are
  coding quotas only.
- **Five macOS/iOS drift points move into Shared.** Drained quota tint,
  `/health` models, subscription pricing chrome, poll backoff, and clearer
  iPhone HTTP error prose — one implementation each instead of diverging
  copies.

### Fixed

- **A bad subscription plan row no longer deletes its provider.** Malformed
  `subscription_pricing.plans` entries are skipped lossily so one typo cannot
  blank the ring, meter, and Activity leaf.
- **Ten provider fetchers stamp and disk-cache successes through
  `cache_util.store`.** Age and last-good replay work for OpenRouter, AI
  Gateway, and the other alert/balance sources the way Claude/Codex already
  did.

### Added

- **Contract tests require every Swift-required `/usage` field in the demo
  fixture**, and mirrored-constant / firmware label checks cover four more
  pairs plus positive LABEL_* values.

## 1.8.1 — 2026-08-04

### Changed

- **OpenRouter and AI Gateway show account use history, not depletion.**
  Overview is a daily-spend sparkline; the provider leaf leads with the full
  trailing-window day chart, then totals / models / keys. Remaining credits
  stay a figure, not a bar.

## 1.8.0 — 2026-08-04

### Changed

- **OpenRouter refuses inference keys even when `/credits` answers.** Status
  names the wrong key type and points at
  [management-keys](https://openrouter.ai/settings/management-keys) so a quiet
  wrong pot cannot replace the account balance.
- **OpenRouter and AI Gateway provider leaves show observed spend.** Daily
  bars, runway, top models, and (OpenRouter) per-key usage — no ring, no
  Attention rows. Spend is billed credits from each provider's own API;
  AI Gateway Hobby notes when the report endpoint needs Pro.
- **Telemetry schema 2 week-scopes a dedupe key.** Each Mac keeps a local
  install secret and sends `HMAC-SHA256(secret, ISO week)` so Debug/Release
  copies cannot inflate weekly active Macs — without a stable install id on
  the server. Existing D1 needs `telemetry/migrations/002_dedupe_key.sql`
  and `003_country.sql`.
- **Community Pulse shows version distribution and latest release.** Histogram
  against the update feed; country mix from the Worker edge (no IPs stored).
- **Supabase Attention lint badge is a compact count + shield.** Plain
  HStack instead of a Label so the row chrome matches neighbouring service
  rows.


## 1.7.9 — 2026-08-04

### Changed

- **README and setup match Usage · Attention · Activity.** Surface table,
  Settings paths (Providers / Add account / Agents), iPhone and App Store
  feature lists, and the docs index (Telemetry, Updater) follow the current
  chrome rather than the old Services peer tab. Screenshots refreshed to
  match.

### Added

- **`docs/orgs.md`** — standing design for an optional org / multiplayer
  window layer (spec only; not shipped in the product yet).

## 1.7.8 — 2026-08-04

### Fixed

- **OpenRouter Create-token opens Management keys.** The link was
  `/settings/keys` (inference keys); it now goes to
  `/settings/management-keys`. An inference key in Keychain is rejected with
  a Status error instead of quietly reading `/credits`.
- **Community Pulse meters stay neutral grey.** Custom capsule bars replace
  `ProgressView` + `.tint`, which on macOS often fell back to the system
  accent and painted the mix rows coral.

## 1.7.7 — 2026-08-04

### Changed

- **About links the public repo and Community Pulse.** Settings → About on
  Mac and iPhone show Source on GitHub with a live star count, plus a link
  to the public community page. Open-source footer under the About block.

## 1.7.6 — 2026-08-04

### Changed

- **Community Pulse shows architecture, macOS major, and week-over-week.**
  The Worker pads empty weeks on the growth chart, publishes CPU and macOS
  mix from batch columns, and splits service mix into enabled / used /
  healthy. Settings → Telemetry and the public community page both draw the
  richer aggregate.

## 1.7.5 — 2026-08-04

### Changed

- **Activity row limit sits on every Activity source leaf.** Git, Actions,
  Vercel, Supabase, Sentry, Datadog, and Axiom each show the same
  Recent-rows stepper (3–24) that the Integrations hub already had — Local
  keeps its own servers stepper.

## 1.7.4 — 2026-08-04

### Changed

- **Host-down popover is a wait, not an error stack.** Header says **Host
  not answering** (same fact as the menu-bar tooltip) with a soft amber pip
  instead of Foundation’s “Could not connect…”. Sources and Done stay
  hidden until `/health` answers; orange is reserved for a failed Start.

## 1.7.3 — 2026-08-04

### Changed

- **Recent resets is a calendar heatmap.** Granted resets under a burndown
  card are a day grid (on/off; provider tint = global grant, amber = credit
  you spent; weekly auto-resets stay off) instead of a six-row list, sized
  from the oldest grant on hand. Codex week merges the public
  [codex-resets.com](https://codex-resets.com) announcement feed with locally
  observed sample grants — so the grid reaches back through every verified
  global reset and keeps filling forward as new ones are announced or
  detected. Still live data, never a fixture.
- **Activity and Attention rows share one layout on Mac and iPhone.** Feed
  rows and the Attention list live in `Shared/` so both surfaces draw the
  same chrome.

## 1.7.2 — 2026-08-04

### Changed

- **Integration Settings name the scopes each key needs.** PostHog asks for
  `project:read` and `query:read`; Plausible, GitHub, Supabase, Datadog,
  OpenRouter, and AI Gateway footers spell out their matching permissions too
  (Sentry and Axiom already did). PostHog's cloud region is a US / EU
  dropdown; Custom still takes a self-hosted URL. Saved keys show
  `••••••••••••` in the empty SecureField so the row does not look unused.

## 1.7.1 — 2026-08-04

### Fixed

- **Connect no longer fails to save a GitHub token when iCloud Keychain
  refuses the write.** Synced PAT saves wiped both keyspaces before the local
  fallback ran, so a refused synchronizable write could show "Could not save
  GitHub token" and drop a working local copy. Each half is update-or-add on
  its own now; the other half is dropped only after the write that should win
  has succeeded. Thanks to [@pm](https://x.com/pm/status/2084287614115328004)
  for reporting it.

## 1.6.6 — 2026-08-03

### Added

- **Sentry, Datadog, and Axiom as Attention sources.** Integrations leaves
  that surface fresh unresolved Sentry issues, Datadog Alert/Warn monitors,
  and open Axiom monitor alerts into Activity and the Attention pip — break
  signals only, not dashboards. Keys stay in Keychain; org/site/host in
  config.

## 1.7.0 — 2026-08-03

### Added

- **Anonymous product diagnostics on the Mac.** Settings → Telemetry shares
  one aggregate batch per week (app/host versions, normalized provider ids,
  model-family shares, three coarse feature flags) when left on. No prompts,
  paths, tokens, or install id. First-party Worker under `telemetry/`; see
  [`docs/telemetry.md`](docs/telemetry.md).

## 1.6.7 — 2026-08-03

### Changed

- **Warn is orange, not soft amber.** Soft amber stays for in-flight (building,
  syncing) and soft stale. Attention `warn`, review/mention/assigned, connection
  trouble, and needs-sign-in use a hotter orange (`#D98A3C`) so warn no longer
  reads as "stuff is happening." Critical stays dusty red.

- **Sources: Add account lives under Library.** The Active meter rows no longer
  carry an inline “Add account…” link. Multi-account providers get chip buttons
  in an **Add account** section under Library. Active rows show the signed-in
  email when the host can read it (Codex ChatGPT id_token, Cursor’s cached
  profile); Claude’s OAuth token is opaque so it may stay blank.

- **Integrations drops Coding agents.** Claude Code and Codex stay under the
  Coding agents tab; the Integrations catalog is watches and connections only.

- **Dashboard density leaves General.** Activity row count sits on Integrations;
  Local servers count, stop confirmation, and show/hide sit on the Local leaf.

### Added

- **Provider pages show today’s quota burn.** Same `Today N%` reading as the
  Daily burn card, scoped to that provider’s headline meter — on the ESP32
  detail page (opposite Updated), and on Mac / iPhone provider cards.

- **Menubar Activity drills into the same detail pages as iPhone.** Plausible,
  PostHog, Supabase, local servers, Xcode builds, and feed/Attention rows open
  a Back-stack detail inside the popover. A shared `link` permalink glyph on
  each row and detail chrome opens the source; Supabase pin is gone.

- **One Integrations catalog.** Settings → Integrations is a single Sources-style
  list (drag, toggle, status, open leaf) — not a separate “Activity services”
  reorderer plus connection groups. Host pin is `integrations_order` (migrates
  from the short-lived `services_order`). Activity on Mac and iPhone lays out
  git / Actions / Vercel / service panels / local servers / builds in that
  order; API balances stay on the list for enable/open but skip the Activity
  stack. Claude Code and Codex are under Coding agents only.

- **Settings sidebar selection no longer races the detail.** A `TapGesture` on
  each root was fighting `List(selection:)` and often left the highlight and
  the pane disagreeing.
- **Local Xcode builds next to local servers.** Activity on Mac and iPhone
  lists live `xcodebuild` / `swift build` (agents, CLI) and IDE compiles that
  are actually running compilers under XCBuildService / SWBBuildService —
  labeled by scheme or DerivedData project. Idle Xcode with a warm build
  service stays quiet. No stop action; Reveal in Finder only.

- **ESP32 glance dims with the sun.** Amsterdam lat/lon by default (override
  in `config.h`): full brightness until 30 minutes after sunset, then ~30%,
  then ~10% at a fixed bedtime (22:00). Back to full 30 minutes before
  sunrise. Late summer sunsets that would put the evening step after bedtime
  skip evening and go day→night at bedtime, so winter dusk plateaus stay
  long without August going haywire. Clock from SNTP when Wi-Fi is up, else
  the host's `updated` stamp. Board setup lives in [`docs/esp32.md`](docs/esp32.md)
  beside the iPhone and Watch guides.

- **Root README is the product front door.** Setup detail, host reference,
  troubleshooting, and Mac build/signing moved to [`docs/setup.md`](docs/setup.md),
  [`docs/host.md`](docs/host.md), [`docs/troubleshooting.md`](docs/troubleshooting.md),
  and [`macos/README.md`](macos/README.md).

## 1.6.5 — 2026-08-02

### Changed

- **ESP32 home follows the Mac dashboard.** The lower pane cycles Daily
  burn, History, Spend, then Burndown — stacked seven-day burn and the
  estimated spend figures the Mac card already leads with. The Activity
  feed leaves that cycle for its detail pages.

## 1.6.4 — 2026-08-02

### Changed

- **Watch dial bands follow pinned Sources order.** Outside-in used to
  sort by most spent, so positions drifted every poll. First pinned
  provider is outermost, matching Activity and the board.

## 1.6.3 — 2026-08-02

### Changed

- **Activity and Attention split on wide iPhones**, matching the Overview
  layout from 1.6.2. Landscape and unfoldable phones get two columns
  instead of one tall scroll.

## 1.6.2 — 2026-08-02

### Added

- **A mixed activity heatmap** on the Mac, phone and ESP32. Claude's local
  session history combines with daily burn from every quota source into one
  cadence strip. The level is an evidence ramp, not a fake common unit;
  tapping a day still shows the native minutes and burns.
- **ESP32 glance shows power source in the bottom-left**, mirroring the
  link glyph on the right. USB draws a plug (with percent when a cell is
  fitted); battery-only draws the cell with fill; charging adds a bolt.
  Reads the AXP2101 the board already has.

### Changed

- **Dollar amounts use thousands separators** (`$12,475` instead of
  `$12475`) in the app and in Cursor / balance source labels.
- **Wide iPhones split the Overview** into quotas beside charts, the same
  regular-width gate the other phone tabs will follow.

## 1.6.1 — 2026-08-02

### Added

- **@mentions on watched GitHub repos join the Attention inbox**, beside
  review requests and assignments. Still scoped to the watch list, not every
  @you on GitHub, so the pip stays useful for CI and review oversight.

## 1.6.0 — 2026-08-02

### Changed

- **The watch burndown ghosts the windows you already spent**, same faint
  sawtooth the phone, Mac and widget draw behind the live curve. The
  rectangular complication and the in-app chart were skipping `history` even
  though the phone already forwards it.

## 1.5.9 — 2026-08-02

### Changed

- **Sources lists providers; Integrations lists connections.** The two
  pages used to show the same nouns: every integration was also a source,
  so neither page said which question it answered. Sources now lists only
  what Headroom reads a meter for. Dev tools move to their Integrations
  leaf (with the on/off that used to live on the Sources row). Local
  servers, which has nothing to configure, gets its switch under General.
  The phone gains an Integrations root for the same split.

## 1.5.8 — 2026-08-02

### Fixed

- **PostHog's brand mark now actually renders.** The mapping shipped in
  1.5.5; the asset itself did not. Every Activity row, Attention row and
  Settings entry for PostHog has been showing a gap where the icon belongs
  since that release.

## 1.5.7 — 2026-08-02

### Added

- **Grok (xAI)** joins the coding-quota sources, reading subscription tier
  and on-demand credit usage through the Grok CLI's own Agent Client
  Protocol session — no scraped endpoint, no separate login. Contributed by
  [@ronitrade123](https://github.com/ronitrade123).

## 1.5.6 — 2026-08-02

### Added

- **The Attention queue survives a cold launch.** Coding-agent events used to
  live in memory only, so a process death between polls — the Mac asleep, the
  app backgrounded past its budget — opened to an empty queue even though the
  same rows would reappear on the next poll. They now have the same on-device
  archive the rest of the app already relies on.
- **An empty queue reads as open, not broken.** A large ghost icon replaces
  the list-with-nothing-in-it look on both the Attention and Activity tabs.

### Changed

- The Attention tab icon becomes an eye — watching is closer to what the tab
  does than a speech bubble asking a question.
- App Store screenshots and copy drop the Quotas and Services frames in
  favor of an Attention frame, matching where those tabs live now.

## 1.5.5 — 2026-08-02

### Added

- **OpenRouter and Vercel AI Gateway**, the first sources reporting a
  prepaid balance rather than a windowed quota. No ring — the mark is a
  depletion bar, since there is no window to draw as an arc.
- **PostHog**, sitting beside Plausible as a Services integration, with a
  project picker on the phone matching the Mac.
- Git, GitHub Actions, Vercel, Plausible and Supabase now carry real brand
  marks in Activity and Attention rows — they were unbranded there before,
  even though Settings already showed their icons.

### Changed

- **Settings splits into one file per integration** instead of one 2,700-line
  file everything shared. Adding the next source now touches one new file.
- **Burndown reads week before session** everywhere a provider shows both —
  the longer window is what you actually plan around.
- **A stale reading says why.** Attention now distinguishes a rate limit from
  a real outage, so a 429 the host is already backing off from calmly does
  not also light the menu-bar warning the way a genuine failure does.
- GitHub inbox rows carry the author who opened them.

## 1.5.4 — 2026-08-01

### Changed

- **Pick which Supabase projects to track**, the same list-driven pattern as
  GitHub repos and Vercel teams. A key that could see a dozen projects used to
  track all of them or none; now it tracks the ones you choose.

## 1.5.3 — 2026-08-01

### Added

- **Attention surfaces review requests and assignments from your watched
  repos.** GitHub review requests and issue/PR assignments now raise a row the
  same way a failed check or a waiting agent does, so the queue is one place to
  look rather than a second tab you have to remember to check.

## 1.5.2 — 2026-08-01

### Fixed

- **A read-only question from Claude still offers Claude's own options.** The
  Attention row was showing generic yes/no controls on a question that had its
  own named choices, because the read-only path skipped the branch that reads
  them. The options Claude actually asked for now render either way.

## 1.5.1 — 2026-08-01

### Changed

- **Pick GitHub repos and Vercel teams from a list instead of typing them.**
  Both settings were free text, which meant a typo produced an empty panel with
  nothing to say why, and getting it right meant knowing the exact slug. Headroom
  now asks the service what you have access to and offers it. Blank still means
  everything the login can see, so nothing changes if you never set one.

## 1.5.0 — 2026-08-01

### Fixed

- **Agent push titles name the repo, the agent, and the Mac** —
  `headroom • Claude • Studio` — instead of the repository alone. Opus vs
  Sonnet does not matter on a lock screen; Claude vs Codex and which computer
  does.
- **Start task is back on the Attention tab.** The `+` moved there when the
  phone split by urgency, but it loaded the task surface before the Mac-granted
  **Answer coding agents** permission had arrived, then never tried again. The
  button stayed missing for anyone who landed on Attention first. It now
  reloads once permissions are known, and shows whenever agents are allowed.
- **Passive agent notices leave Attention on their own.** Idle / finished rows
  and notify-only questions no longer sit until you tap Dismiss; they expire
  after an hour and drop out of the open feed on the next poll. Requests that
  need an answer are unchanged.

## 1.4.9 — 2026-08-01

### Changed

- **The app icon holds together at small sizes.** The three bands sat at 70 /
  80 / 90 percent, which spread them far enough apart that the glyph read as a
  loose spiral in a Dock and lost its shape entirely in a menu bar. They now sit
  at 80 / 85 / 90, tighter and more obviously one mark. The yellow is a shade
  off pure process yellow so the white pace disc still reads against it, which
  it did not reliably do before.

## 1.4.8 — 2026-08-01

### Added

- **The widget shows the windows you already spent**, ghosted faintly behind
  the live curve, the same sawtooth the Mac draws. A single curve told you where
  you are without showing whether this week is normal for you, which is most of
  what makes the number mean anything.

### Fixed

- **A stale widget cache no longer walks "now" past the strokes it is holding.**
  The chart anchored its window to the current clock while the data came from
  whenever the cache was last written, so as the cache aged the curve slid
  toward the edge of a frame that kept moving without it. The frame is now
  anchored to the newest sample the cache actually contains.

## 1.4.7 — 2026-08-01

### Fixed

- **The menu bar slot is never blank.** Before the first poll came back, or
  when nothing was enabled yet, the icon drew zero bars and collapsed to
  nothing, which looks exactly like the app having crashed rather than the app
  waiting. It now draws three empty tanks and fills them as readings arrive, so
  there is always something in the slot you can point at.

## 1.4.6 — 2026-08-01

### Changed

- **Amber in Settings now means something you can act on, and nothing else.**
  Every integration was picking its own colour and inventing its own phrase for
  the same three outcomes, so "the host is too old to tell us" and "your token
  is missing" could arrive looking equally alarming. One resolver decides the
  caption and the tone for all of them: **Not connected**, **Keychain**, **Hooks
  installed**, **Gateway on**, and their opposites. Unknown reads as quiet grey,
  because warning about something nobody can fix is just noise.
- Every integration detail pane carries the same **Status** row, so the hub
  caption and the detail agree instead of each phrasing it their own way.
- The TestFlight invite link lives in one place now. Welcome's phone step and
  Settings → iPhone were carrying separate copies of the same URL, which is the
  arrangement where one of them quietly goes stale.

## 1.4.5 — 2026-08-01

One release rather than six, because the rename underneath runs through every
surface and the features sit on top of it. Sections below are the things you
will actually notice.

### Changed

- **Overview is now Usage**, on both the Mac and the phone, with a **Summary**
  inside it before provider detail. Quotas stops being its own iPhone tab and
  is reached through Usage, which is where people were looking for it anyway.
- **The Mac gets Attention and Activity as modes of their own**, matching the
  phone. They were iPhone-only, so the computer holding all the context was the
  one place you could not see the queue.
- **Activity groups by what a row is**, not by where it came from: GitHub
  Actions, Vercel deployments, Git commits, Quota resets, Claude status, and a
  fallback group so a newer host can add a kind without an older client
  dropping it on the floor.
- **Integrations became the hub for everything Headroom connects to**, agents
  included, with a **Code and deploys** group holding Git, GitHub Actions and
  Vercel. **Dev root** and **Commit authors** are settings now: the folder
  stays on this Mac, the author list follows you to your other Macs. Vercel
  gains a **Teams** filter, and blank still reads every team the login can see.
- **Coding agents is about starting work**, with connection setup living under
  Integrations alongside everything else.
- **A Claude question waits about two minutes instead of twenty-five seconds.**
  Twenty-five was long enough for someone already at the keyboard and not long
  enough for a phone to poll, notify, and take a biometric answer, which is the
  whole point of answering from the phone.
- **An agent request is titled with the repo it came from.** It used to read
  "Claude needs permission in headroom", which spent the readable part of the
  line on words identical in every row.

### Added

- **Pair more than one Mac to an iPhone.** Settings shows **Computers** with
  everything paired, and **Add computer** adds one without replacing what is
  already saved. Each Mac's token stays in the iPhone Keychain.
- **Agent rows say which Mac they came from.** Two computers answering
  questions on one phone were previously indistinguishable.
- **Providers carry their published subscription prices**, so a plan can show
  what it costs per month and per year alongside what it is doing.
- **Dismiss and Dismiss all** on the Attention queue, which clear passive
  notices while leaving anything that still needs an answer.

### Fixed

- **A quota window that simply rolled on schedule now draws as a step.** The
  chart squared its history against `resets`, but that key is grants only, a
  provider handing back a week it had already taken. An ordinary scheduled roll
  was not in it, so it had no cut to square against and came out as a diagonal
  between whichever two samples survived thinning: a two hour ramp where the
  chart should read as a vertical riser. The host now publishes every boundary,
  `resets` keeps its old meaning exactly, and clients that predate the new key
  fall back to the old behaviour.
- **The seven day burndown frame is symmetric about today**, three and a half
  days either side rather than three back and four forward. The watch label
  says `±3.5d` to match.
- **The app icon reads at small sizes.** Thicker bands, a tighter gap and a
  larger outer radius, so the three arcs stay distinct in a menu bar and a Dock
  rather than dissolving into a ring.
- Two "Plan unavailable" strings now use the canonical copy, which says **Plan
  unknown**. A missing reading is not a failure and the product does not call
  it one anywhere else.

## 1.4.4 — 2026-08-01

### Fixed

- **The update feed is being written again.** `docs/latest.json` is what an
  installed copy polls to find out a newer Headroom exists, and it has said
  1.4.1 since this morning while 1.4.2 and 1.4.3 shipped past it. The script
  that writes it made its staging directory with `mktemp -d -t headroom-feed`,
  which macOS accepts and GNU coreutils rejects for having too few trailing
  X's. The feed job is the one part of a release that runs on Linux, so it died
  on its first line, four seconds in, every time.
- Nothing about that was visible from the outside, which is the part worth
  keeping in mind: both releases built, notarized, published their GitHub
  Release and uploaded to TestFlight exactly as they should. Only the last step
  failed, and its only symptom was installed copies quietly believing they were
  current. If you have been running 1.4.1 and wondering why no update appeared,
  this was why.

## 1.4.3 — 2026-08-01

### Fixed

- **The selected row in the Welcome sidebar is readable again.** The glass
  selection was drawn *behind* the row rather than around it, and because every
  glass shape in a container composites in one pass on top of that container's
  plain content, the label ended up behind its own frosting. The one row you
  most needed to read was the one you could not. It now wraps the row, so the
  text is content of the glass instead of underneath it.
- **The coach mark pointing at the menu bar is green again, not black.** Glass
  samples what sits behind its window, and that window is borderless and
  transparent over whatever the desktop happens to be, so there was nothing to
  sample and the tinted panel rendered as a near-black lozenge. It is now one
  solid shape, pointer and bubble in the same green, which is also the only
  thing that stays legible over an arbitrary wallpaper. The text is dark rather
  than white, which at this green carries about twice the contrast (6.4:1
  against 3.3:1), and the whole mark bobs as one piece so the pointer never
  separates from the bubble.

### Changed

- The Welcome screen shows **the menu bar icon itself**, drawn by the real
  renderer, instead of an arrow pointing off the window towards it. What you
  need at that moment is to know what you are looking for; the coach mark over
  the menu bar does the pointing.

## 1.4.2 — 2026-08-01

### Added

- **Headroom notices when a newer Headroom exists.** Settings → General → App
  updates checks a feed weekly, says what it found, and installs it on a
  button. No Sparkle and no new dependency: the download, the notarization and
  Team ID checks and the LaunchAgent bootout/bootstrap were already in
  `scripts/update-app.sh`, which is now bundled inside the app and run from
  there. The app never replaces itself, because it cannot: the host lives in
  the bundle being swapped. See [docs/updater.md](docs/updater.md).

  **This release is the one that teaches the app to update. It cannot update
  *to* itself** — a copy running 1.4.1 has no updater to hear about 1.4.2 with,
  so that hop is still a download or `./scripts/update-app.sh`. From 1.4.2
  onwards it is a button.
- **The feed lives on a hostname we own**, `updates.centaur-labs.io`, which
  CNAMEs to GitHub Pages today and can be repointed anywhere tomorrow without
  touching a shipped build. The zip's location is a field in the feed rather
  than a path the app builds, for the same reason.
- **The phone splits by urgency, not by source.** Attention is a new second tab
  carrying agent questions, the rollup's reasons and every failing feed row,
  with a badge for the count. Activity takes the rest and absorbs what used to
  be the Services tab, so the three tabs read as a summary, a queue and a log.

## 1.4.1 — 2026-08-01

### Fixed

- **`Retry-After` can no longer shorten a backoff.** 1.4.0 let the header win
  outright, on the reasoning that the provider knows the real window better
  than we do. It does, in one direction. Anthropic answers a 429 with a
  sub-minute `Retry-After`, so honouring it literally retried *sooner* than the
  schedule's own first step and the backoff never got off the ground. The
  header raises the wait now and never lowers it, which is the shape
  [#13](https://github.com/michellzappa/headroom/pull/13) had and 1.4.0 got
  wrong.
- **"retrying in 0m" is gone**, and was always meaningless: the wait was real
  but shorter than a minute, and the countdown floors to whole minutes. Nothing
  can produce a wait that short any more.
- **A rate limit is visible in the host log.** A stale replay still reads `ok`,
  so the log printed the last-known numbers and a bare `stale` marker and threw
  the reason away. Every 429 this host has ever served was invisible there,
  which is exactly long enough to convince someone reading it that no 429 had
  ever happened. The reason is printed now.

## 1.4.0 — 2026-08-01

### Fixed

- **A failing fetch stops being asked at the same rate forever.** 1.3.3 taught
  the host to back off a 429, which was the case where retrying actively makes
  things worse. It was not the only case where retrying is pointless: a 5xx, a
  timeout, an expired login and a provider that changed shape all stayed on a
  flat `fail_ttl_s`, which is a fixed rate of traffic aimed at something
  already known to be failing. Consecutive failures now double the interval
  from each provider's own base up to fifteen minutes, and one good fetch puts
  it straight back on the short leash. The first miss still waits exactly what
  it always did, because one dropped poll is a blip and nothing should get
  slower at recovering from those.
- The two backoffs disagree about a forced refresh, on purpose. A 429 holds
  against it, since Settings, the phone and the board's long-press are what
  someone reaches for when a rate limit is in effect. Every other failure
  yields to it, because forcing is how a fixed login or a reconnected VPN is
  meant to be picked up, and waiting out a backoff you have already resolved
  is its own bug.
- The generic streak can no longer shorten a rate limit's wait. A 429 carrying
  a long `Retry-After` outranks it, or honouring the header would be theatre.

Credit for the diagnosis and the general shape here goes to @leolobato, whose
[#13](https://github.com/michellzappa/headroom/pull/13) called this a week
before it was fixed properly and covered the case 1.3.3 left out.

- **CI checks the `/usage` contract against the fixture, not against this
  machine.** 1.3.9 went red because the allowlist-hygiene test asserts every
  excused key is still served, and the six `history` fields are absent on a
  bare runner: no `~/.claude`, no session log, no history block. It passed
  locally because this machine has 400 days of it. `demo_usage.json` carries
  history now, which is the whole reason a committed fixture exists.

### Documentation

- `docs/metering.md` is reachable. It existed for five releases linked from
  nowhere, and the standing-decisions section still claimed there were three
  such docs. A decision record nobody can find is not doing its job.
- `AGENTS.md` describes the workflow this repo actually uses: commit to `main`,
  version as you go, no branches and no pull requests. It documented a
  branch-per-set flow that had stopped being true, which is worse than
  documenting nothing.

## 1.3.9 — 2026-07-31

### Added

- **Spend, on the overview.** Headroom has priced every Claude session since
  1.0, per day and per model, and it keeps 400 days of that. Until now it
  showed you one line of it. The new card draws the rest: today, the window
  total, the average per active day, and where the tokens went by model. Every
  figure is labelled Estimated, because it is. These are local token counts
  priced by a table in the host, not a bill from anyone. The model split is a
  share of tokens rather than dollars, since splitting dollars by token share
  would quietly assume every model costs the same.
- **A tell for when the price table falls behind.** An unrecognised model was
  priced at Sonnet rates with nothing to say so, which made an Opus session
  read 40% cheap and look exactly as confident as a correct figure. Claude
  Opus 5 and Mythos 5 were both missing from the table, so this was not
  hypothetical. Both are in, and the card now names any model it had to guess
  at.

### Changed

- **Meters know what kind of thing they are.** Every quota Headroom watches
  used to be a percentage of a pool that refills. Anything else got bolted on
  as loose extra fields. Codex reset credits are now a grant, with their own
  expiry clock. Cursor on-demand and the Codex spend cap are overage meters,
  counted in dollars. Nothing looks different yet, and that is deliberate: the
  new meters carry no ring, and the desk display skips them. What changed is
  that the next kind, a prepaid balance or a monthly bill, is a row in a table
  instead of a new set of fields for every client to learn.

## 1.3.8 — 2026-07-31

### Fixed

- **Gemini keeps working an hour after you sign in.** The host reads the CLI's
  public OAuth client out of the installed package to refresh a token, and it
  looked in three hardcoded paths that assumed Homebrew's npm prefix and the
  old unbundled layout. A global install under any other prefix, or a current
  gemini-cli — which ships as an esbuild bundle with content-hashed chunk
  names and no `oauth2.js` to find — matched none of them, so sign-in worked,
  the ring filled, and the source flipped to **Not updating** at the first
  refresh. The host now resolves the `gemini` command the way a shell would,
  across the prefixes a LaunchAgent's fixed PATH cannot see, and searches the
  package it actually lands in. Reported in careful detail, cause and all, by
  [@Sendar](https://github.com/michellzappa/headroom/issues/18) — thank you.
- **A CLI that moves or leaves no longer takes the ring with it.** The pair is
  public and unchanging, so the host keeps the last one it read under
  `~/.headroom/`. An upgrade that reshuffles the bundle, or an uninstall after
  you signed in, refreshes from cache instead of going dark.
- **The escape hatch survives an update.** `GEMINI_OAUTH_CLIENT_ID`/`SECRET`
  were documented as the way out, but the app rewrites the LaunchAgent plist
  on every host install and takes anything hand-added to it along.
  `gemini_oauth_client_id` and `gemini_oauth_client_secret` in
  `~/.headroom/config.json` are read first and stay put. The env vars still
  work, and `GEMINI_OAUTH2_JS_PATH` now accepts a folder as well as a file.

## 1.3.7 — 2026-07-31

Thanks to [@leolobato](https://github.com/leolobato), who built the Sources
redesign below. He also found the rate-limit loop and wrote the contributor
signing support that went out in 1.3.3, both of which shipped without a word
here; this fixes that too.

### Changed

- **Sources is two zones now.** The flat list of fourteen toggles is gone.
  **Active** holds what you track, as reorderable rows with live usage bars and
  ①②③ badges marking the menu-bar slots, with extra accounts grouped under the
  service they belong to.
  **Library** holds everything else as compact chips, split into AI providers
  and Dev tools. A chip whose credential leaves no local trace dims and says
  **not detected**, unless the service takes accounts, in which case it offers
  **Add account…** rather than dead-ending. Tracking several accounts stopped
  being a Claude special case and became something any service can declare.
- **The enable switch stopped doing two jobs at once.** The toggle now pauses:
  the service stays in Active, dimmed, and nothing polls it, while its
  configuration, its place in the order and its accounts all survive. The ✕
  moves it to Library and stops tracking it. Neither one touches a credential,
  because Headroom has no sign-ins of its own to revoke, and the copy says so
  where you make the choice. Tapping a Library chip brings a service back to
  Active switched on, as a single write rather than two settings to find.
- **You can drop one extra account without disturbing its siblings**, from the
  row it sits in.

### Added

- **A meter now says what kind of thing it is.** Headroom measures four ways
  and had a word for one of them: windows. Reset credits, dollar overages and
  historical attribution were each bolted on as flat keys, and each inherited
  no ring, no burndown, no history and no place in the attention rollup,
  because all of that was wired to pools and nothing else. `PoolSpec` is now
  `MeterSpec`, carrying a kind and a basis. Nothing moves on screen yet: every
  meter that exists today is a window, so both fields are constant and the
  registry is untouched. The point is the next kind, which should be a registry
  row and a fetcher, the way a new provider already is.

### Fixed

- **Consumers that meant "window" now ask for one.** Averaging a percentage,
  sampling into the burndown store and picking the line a row speaks for were
  all written when a pool could only be a window. Handed anything else they
  would not have failed loudly; they would have logged an empty percentage
  every poll, or appended a null row to the sample store, which is an
  append-only record of your own history and the wrong place to discover a
  mistake months later.

## 1.3.6 — 2026-07-31

### Changed

- **A reset is drawn as the recharge, not as a gap where one happened.** Every
  chart used to cut the series at each grant, leaving one falling run per window
  and no sign of the moment the pool came back: the curve ended at nothing and
  restarted at full with nothing to explain it. The line now runs level to the
  reset instant, rises there, and carries on, landing on the same mark the chart
  already draws for the grant. Joining the two samples raw was never an option
  because they sit a bucket apart and the diagonal reads as an impossibly fast
  refill. A boundary the host never flagged as a grant keeps its plain diagonal,
  which is the honest picture when nothing pinned the instant.
- **The board and the apps agree about it.** The ESP32 got the recharge first
  and the Mac, phone and widgets were still splitting the same series, which is
  two renderings of one contract. Ghost curves also dim a little further into the
  background than before: with a riser in them they carry more ink, and at the
  old value they competed with the live curve.
- **Every burndown now says which seven days it is drawing.** There have always
  been two rules — the overview spans three days either side of today, a
  provider chart spans that pool's own window — and neither chart said so, so
  the pair read as one chart that kept changing its mind. The overview subtitle
  is now **7 days around today** instead of the underspecified **7 days**
  (**±3d** on the watch), a provider chart carries **This window**, and a
  monthly pool, whose plot is a seven-day slice clipped out of a window too
  long to draw, carries **7 days of this window** rather than claiming a window
  four times the one on screen. The words come from `frameLabel` on the
  domain, so no surface writes them itself.
- **The monthly slice keeps its own lookback.** It was reading the overview's
  `lookbackDays`, so retuning where the overview centres would have silently
  moved every monthly chart with it. Same value, separate constant.
- **One derivation of a provider chart's domain.** The Mac canvas, the Mac
  header and the phone each rebuilt it from the pool; the header could describe
  an axis the canvas had not drawn. `Burndown.chartDomain` is now the only
  place it happens, and the Mac canvas is handed the domain its header named.

## 1.3.5 — 2026-07-31

### Changed

- **The rings say a provider's name, not its id.** A ring band carried one
  string that was both its identity and the words VoiceOver read, so a named
  extra login spoke as "claude colon work" and the empty glyph named nothing at
  all. Bands now carry a display name of their own: the pool's title where the
  bands are one provider's windows, and the provider's full `Claude · Work`
  where a band *is* the provider — the watch's combined dial, which has no
  label beside it to lean on. The widget cache carries the full title alongside
  the short one for the same reason; the widget and the watch are the two
  surfaces with no model layer to ask. The empty glyph now speaks
  "Quotas, no reading", following the same rule as 1.3.4.

### Fixed

- **The board's provider pages keep their history across a reset too.** 1.3.3
  taught the home chart to draw the spent window behind the live curve, but the
  page behind a tile is a separate draw path and never learned it, so tapping
  into Codex just after a reset still showed a single point against a full-width
  budget diagonal. Those pages now reach back far enough to show the drop before
  the grant, and draw the same faint curve and dotted grant rules. The monthly
  view is left alone on purpose: it is a moving slice inside a longer window, and
  pulling its edge outward would put it where the budget line does not run.

## 1.3.4 — 2026-07-31

### Changed

- **Percent is the only unit Headroom claims.** Daily burn read `pts / day`, a
  granted reset read `42 pts back`, and the burndown headline read `4 points`.
  Every provider bills in a real unit of its own called points, credits, or
  premium requests, so a reader with a billing page open in another tab took
  those for numbers from somewhere else. They are all `%` now.
- **The pace delta says which way it is going.** `_points()` ran `abs()` over a
  signed number, so a pool four percent *behind* an even spend read exactly
  like one four percent ahead. It is `12% to spare` or `4% over`.
- **One pair of words for pace.** The verdict said **On track** where the
  headline said **On pace**, for the same state, on two surfaces a reader moves
  between. Both say **On pace** / **Over pace** now.
- **Host prose says when; compact surfaces say how long.** `58% left · 4d 44m.
  Out tomorrow 04:18` put two time facts in two shapes on one line. The
  headline is clock form throughout (`resets Thu 14:00`); `resets_in` and the
  board's verdict keep duration form, which is what a glance wants.
- **Nothing says "unavailable" except the Mac.** That one word covered a
  missing key, a failed fetch, a dead host and a provider that never named the
  plan — four situations, three of them fixable, one sentence for all of them.
  Services now say **needs a key** or **not reporting**, the menu bar tooltip
  says **host not answering**, and a missing plan says **Plan unknown**.
- **Send test attention** is **Add a test row** — it adds one row to the feed,
  and "attention" is the card's name, not a thing you can have one of.
- Second person everywhere. The one **we** in the product ("We are pointing at
  it") is gone, and the Settings footer no longer explains that Welcome calls
  the same list something else.
- The App Store listing name keeps its `(Max your Quotas)` suffix, and
  `docs/appstore.md` now says why: "Headroom" alone is taken, listing names
  must be unique, and `CFBundleDisplayName` is plain `Headroom` anyway.

### Fixed

- `scripts/check-glossary-copy.sh` now searches `host/`. The host writes prose,
  not just data — `verdict` is the only string the ESP32 draws and `headline`
  is what VoiceOver reads — so the most-read sentence in the product was the
  one nothing checked. New guards cover the units, the pace pair, the
  "unavailable" family, and first person in UI strings.

### Documentation

- `docs/glossary.md` gains **the decisions under all of this**: percent as the
  only claimed unit and what happens if money ever lands, the voice, the
  metaphor zones (fuel for state, pace for rate, burndown for history,
  provider vocabulary only where the provider bills that way), the
  clock-versus-duration rule, the un-localized 24-hour stance, how other
  companies' product names are rendered, and the accessibility sentence order.
  Renames are now stated to be releases, because a stale client replays old
  prose from its cache.
- `docs/appstore.md` is no longer pinned to the version it was written at and
  then outgrew by ten. What's New points at `CHANGELOG.md` rather than keeping
  a second copy that drifts, and the listing name's `(Max your Quotas)` suffix
  is documented as required — "Headroom" alone is taken.

## 1.3.3 — 2026-07-31

### Added

- **A reset is an event with a history now, not a caption.** Every grant the
  sample log still holds is listed under the pool's burndown — when it landed
  and how many points it handed back — instead of only the newest one being
  named in a line of prose. `rolls()` had been finding all of them for months;
  every consumer read `[-1]` and dropped the rest.
- Banked Codex reset credits show on iPhone. The board has drawn "1 reset
  credits" for a while and the Mac shows it too; the phone was the one surface
  that knew the number and never said it.
- Optional notification when a pool comes back, on the Mac and the phone. Off
  by default on the Mac, and turning it on is what asks macOS for permission —
  a menu bar app that requests notifications at first launch is asking for
  something it has not earned yet.
- **Sign builds as yourself.** A gitignored `macos/Local.xcconfig` sets your own
  Apple team without touching a tracked file, and `HEADROOM_BUNDLE_PREFIX` moves
  every bundle id off ours for iOS and watchOS device builds, which Apple will
  not register to a second team. Unsigned builds still need nothing, and CI and
  the release scripts are unchanged. This is for people building from source; it
  changes nothing about the app you download.

### Changed

- **A frozen quota says which kind of frozen it is.** "Quota stuck at 17h 44m
  old" read identically whether the fix was patience or a login. The attention
  line now names the cause from the error the payload already carried: rate
  limits and provider errors say the host is retrying on its own, a network
  failure says to check the connection, and "needs sign-in" gains the remedy of
  logging in with the tool again. An unrecognized error is shown verbatim rather
  than swallowed.

### Fixed

- **Spending a reset credit no longer looks like the app forgetting your
  week.** `actual` stops at the live window's start, which is correct — the
  budget diagonal is drawn against that window — but the moment one rolls it
  holds a single point, and a chart with one point draws nothing. Codex granted
  four resets in six days here, so the curve kept vanishing. A new `history`
  series carries the readings unclipped and every surface draws it faint behind
  the live curve, split at each reset so no stroke climbs across one.
- The ESP32 lost the provider's line entirely at a reset, rather than dimming
  it. `device_view` had been sending the ghost curve and the grant mark since
  the feature landed and `main.cpp` never read either key, so the board went
  from a three-day curve to nothing. It reads them now, and draws a dotted rule
  where each grant landed.
- `rolls()` looked back seven days while the sample store keeps fourteen, and
  capped at eight — enough to drop grants off a list that had room for them.

- **A rate limit from Claude no longer feeds itself.** The usage endpoint
  answers 429 when it has had enough, and the host's only response was to keep
  its poll cadence and let every forced refresh past the cache on top of that.
  A 429 now starts a backoff (1m, 2m, 5m, 15m on consecutive strikes, cleared
  by any good fetch), and the wait is honoured even by a forced refresh, since
  Settings, the phone and the board's long-press are exactly what someone
  reaches for when the numbers look wrong, which is exactly when the limit is
  in effect.
- A `Retry-After` header, in either of the two forms the spec allows, now
  outranks that schedule. Nothing was reading it before. It is capped at an
  hour so one bad header cannot park the source for a day.
- The message says how long the wait is. "Too Many Requests" on its own reads
  as something broken and invites the refresh button, which was the problem.

## 1.3.2 — 2026-07-31

### Fixed

- **Claude's questions no longer depend on Headroom being up.** 1.2.7 shipped
  question-answering as an always-installed `PreToolUse` hook with a 300-second
  timeout — the one hook Headroom installs that can block a tool call. With the
  host down or restarting, every `AskUserQuestion` in every session stalled and
  came back interrupted. The hook is off by default now, carries a 5-second
  timeout when it only observes, and says "no decision" with an empty body
  rather than an enum value read from documentation and never verified.
- **A question shows in both places now.** The default posts it and gets out of
  the way: Claude renders its own picker in the terminal, and the same question
  appears on your phone with every option listed, marked **Answer in the
  terminal**. Nothing is held, so nothing can stall. Holding the call so the
  phone can answer is still there as a mode, but it is the exception — you
  cannot both leave the question in the terminal and answer it elsewhere.
- A question is never drawn as a permission request. `AskUserQuestion` also
  arrives on the permission hook, so a two-part question rendered as **Claude
  needs permission** offering *Allow once / Deny / Stop Claude* — answers that
  mean nothing — with both questions flattened into a wall of text under it.
- Questions Headroom cannot answer remotely — several at once, multi-select —
  are shown rather than dropped. They were invisible before, which is worse
  than read-only.
- Answering Claude's questions from the phone is now **off by default**, and
  the hook that does it is only installed when it is on. `PreToolUse` is the
  one hook Headroom installs that can block a tool call: while it holds a
  question the question is unanswerable at the Mac, and a host that is down or
  restarting takes every question in every session with it. Switching the
  setting off removes the hook rather than leaving a blocker behind.
- A held question now waits 25 seconds rather than the approval's 285. An
  approval has nowhere else to be answered; a question is sitting in front of
  you the whole time.
- Headroom says "no decision" with an empty body instead of an explicit
  `permissionDecision` value. A wrong guess at that enum does not degrade — it
  breaks the tool call.

## 1.3.1 — 2026-07-31

### Fixed

- **The board gets back the 20 rows it was painting over.** A bring-up guess
  had the firmware repaint the bottom of every frame in the background colour
  to hide a fringe on the panel's native edge. It was several times what the
  panel needed, and those rows sat flat while everything else animated. The
  overscan wipe already covers the real problem without spending a row of
  picture. If the fringe ever comes back, the comment in the source now says
  the fix is a column offset on the driver, not painting over our own pixels.
- Dropping that repaint uncovered a bug it had been hiding by accident. The
  driver caches the last address window and skips re-sending it when the next
  call matches, so the raw poke that wipes the overscan columns left the panel
  addressed to a narrow strip while the driver still believed it was drawing
  full frames. The window is restored explicitly now.

### Changed

- Every dashboard on the board starts its first ink slightly lower. The bezel
  curves over the top of the panel and the largest type sits right there, so
  the side inset alone read as tight. Each page pays for the extra out of slack
  inside its own header, so nothing below the divider moved and no page lost a
  row. The desktop previews mirror it, because a preview that promises rows the
  board paints over is a preview that lies.

## 1.3.0 — 2026-07-31

Nothing in this one changes what the app does. It is the release that stops a
whole class of silent breakage from reaching you in the next one.

### Added

- **The `/usage` contract is now enforced, not just described.** Five tests pin
  the parts that fail quietly: a host too old to send a `contract` number is
  treated as speaking version 1 rather than as broken, one malformed row costs
  that row instead of blanking the whole popover, and an empty list stays
  distinguishable from a missing one. All three were already true. None of them
  was checked, and the popover decodes under a single `try`, so the first
  regression would have shown up as an empty window with no clue why.
- **A guard for constants that live in three languages.** Row caps, provider
  slots and pool counts are written out separately in the Python host, the
  Swift clients and the ESP32 firmware, kept in step by a comment. When they
  drift there is no build error, just the board drawing four rows into a
  five-row buffer. `scripts/check-mirrored-constants.sh` now fails the build
  instead.
- **CI runs the host on Python 3.9 as well as 3.12.** 3.9 is the real floor:
  Headroom.app points a LaunchAgent at the system `python3`, and macOS 14 ships
  3.9.6. A single 3.10-only line would have passed every check and then failed
  on every macOS 14 Mac.

## 1.2.9 — 2026-07-31

### Added

- **Headroom can start Codex work, which is what finally makes the Codex
  gateway do anything.** The adapter has been connecting happily for months
  and had never raised a single event, because it spawned an App Server and
  never gave it a thread. `POST /agents/codex/tasks` with a folder and a
  prompt starts one, `POST /agents/codex/steer` adds to a turn already
  running, and `GET /agents/codex/task` says what is live. All localhost-only:
  they drive a local executable.
- **Send a message into a running turn** — `turn/steer`, gated on
  `expectedTurnId` so words meant for one turn never land in the next.
  `send_message` reports supported on Codex for the first time.
- Codex work that dies now says so. The very first real turn Headroom started
  ended on "your workspace is out of credits" and told nobody, which is the
  opposite of a surface for following work. A turn that completes with an
  error raises a dismissible row carrying the provider's own message and
  error code.

- **Start a task from the Mac or the phone.** One control on both: pick the
  agent, pick the folder, say what it should do. `POST /agents/tasks` takes
  either provider, and `GET /agents/tasks` tells a client which agents can
  actually take work right now so nothing is offered that would fail.
- **Claude tasks too.** Claude Code has no "start a session" API and does not
  need one: `claude -p` runs headless and the hooks Headroom already installed
  are global, so a session started here reports back exactly like one started
  in a terminal. Verified end to end.
- Starting a task says so. Both providers answer `ok` and then work quietly,
  so a success was indistinguishable from nothing happening — the only thing
  either surface ever showed was a red line when it failed. A start now
  confirms **"Claude is working in headroom"**, and the Mac adds where the
  requests will turn up, because the Mac has no feed of its own. A failed
  start also keeps the words you typed, instead of clearing them along with
  the attempt.
- The Mac gets a folder picker; the phone picks from folders the Mac has used,
  because a phone cannot browse the Mac's disk. The host remembers the last
  eight.

### Fixed

### Changed

- Starting work from the phone rides the same Mac-granted **Answer coding
  agents** permission that lets it answer an approval — off by default, and
  never open to the LAN at large.
- `docs/agent-attention.md` records what a Codex task needs to be answerable:
  `approvalPolicy: "on-request"` and `sandbox: "workspace-write"`, because a
  policy that never asks produces no approvals to answer. It also states the
  standing limitation plainly — only work Headroom started is visible, which
  is an OpenAI-side restriction rather than a design choice.
- **Agent request history is kept for 30 days, and `SECURITY.md` now says so.**
  The retention window landed with the ledger itself and the disclosure never
  caught up, so the security notes still promised no expiry on a store that
  already prunes. Settled requests are dropped 30 days after you answer them;
  anything still pending is kept until it is answered.

## 1.2.8 — 2026-07-31

### Added

- **Your API tokens follow you to your other Macs.** Retyping a GitHub personal
  access token on the second Mac was the chore multi-Mac sync was supposed to
  remove and did not. The three tokens Headroom owns (GitHub, Plausible,
  Supabase) now travel through iCloud Keychain, end to end encrypted with your
  own keys. Tokens already saved migrate themselves the next time the app
  launches, so there is nothing to re-enter.

### Changed

- Tokens do not travel in the multi-Mac sync record, on purpose. `icloud_dir`
  can aim that same record at Dropbox or Syncthing, where a secret in the record
  would be a secret sitting in a plaintext file in someone else's folder. The
  rule that credentials never enter the record is unchanged.
- The token that authorizes reaching one Mac's host stays on that Mac, because
  the phone pairs to a single Mac. Claude Code's own Keychain item is left
  alone: its refresh token rotates, and two Macs refreshing it independently can
  invalidate each other.
- GitHub, Plausible and Supabase credentials are read through one path now
  instead of four. The old one shelled out to `security find-generic-password`,
  which is the legacy Keychain API and cannot be relied on to see synced items.
- **`SECURITY.md` and the privacy notes were a release behind.** Neither listed
  the `agents` permission, and neither said that the agent request ledger keeps
  commands, paths and code excerpts. Both do now. Nothing about what the app
  does changed here; the disclosure caught up to it.
- New reference docs for anyone changing the `/usage` payload, adding a route,
  or deciding what belongs in the product: `docs/contract.md`, `docs/trust.md`
  and `docs/product.md`. `docs/agent-attention.md` also records why the Codex
  shared-daemon route is a dead end in 0.145.0, so the next person spends the
  twenty minutes elsewhere.

## 1.2.7 — 2026-07-31

### Added

- **Codex approvals show what Codex actually asked.** `commandActions` (its own
  parse of what the command does) and `networkApprovalContext` were captured
  when the adapter was written and never reached a screen. They now travel as
  typed fields through the same contract Claude uses, so one client renders
  both providers.
- **Codex questions are answerable from the phone.** `item/tool/requestUserInput`
  maps onto the same option controls Claude's questions use. A question marked
  `isSecret` is never offered remotely and never enters the ledger — a secret
  does not leave the Mac.
- **Stop Claude** and **Stop Codex** end a runaway turn from the phone, via
  `decision.interrupt` and `turn/interrupt`. Both are destructive and sit
  behind device authentication. Deny answers one request and lets the turn
  continue; interrupt is the other thing, so it is a separate answer.

- **Reply in your own words**, on every request that has a channel for them.
  A permission request carries it as Claude's own "no, and tell it what to do
  differently"; a question carries it as the answer itself; a Codex question
  puts it straight into the answers array. None of the fixed buttons is ever
  quite the thing you want to say.

### Changed

- `docs/agent-attention.md` records that `codex app-server generate-json-schema`
  writes the whole protocol — 234 schemas for v2 — and every Codex capability
  claim in this release was settled from that bundle rather than guessed.
  `turn/steer`, `thread/start` and `item/permissions/requestApproval` are named
  there as the routes the remaining slices need.
- `docs/agent-attention.md` states plainly that **no Codex session can reach
  Headroom today**. The ledger settles it: 112 Claude rows and zero Codex rows,
  because Headroom spawns a private `--listen stdio://` child that nothing
  connects to and never starts a thread on. The adapter is a correct protocol
  client with no live source, and the doc now says which two slices close it.
- The same doc spells out what APNs push actually needs — the `.p8` auth key,
  the entitlement, the device registry, the HTTP/2 constraint against a
  stdlib-only host, and the sandbox-versus-production trap — so slice 4 is a
  shopping list rather than a wish.

## 1.2.6 — 2026-07-30

### Changed

- iPhone drops to three tabs. Quotas and Settings both leave the bar: quota
  detail is reached from Overview, where the rings already are, and Settings
  becomes a toolbar button on every tab instead of a destination competing with
  the data. Five tabs for four screens and a preferences pane was one bar doing
  two jobs.
- The Mac app icon sits on Apple's icon grid — a rounded 824-of-1024 tile with
  clear margins, no baked shadow — instead of a full-bleed square. macOS masks
  nothing for you, so the Dock was drawing a black rectangle among rounded
  ones. iPhone, Watch and the App Store PNG are unchanged: those masks come
  from the system, and App Store Connect rejects alpha.
- The Welcome window's **On your phone** pane shows the mobile token itself,
  with a Copy button, instead of sending you to Settings to fetch it — a detour
  on the one screen whose whole job is getting the phone paired. If the host
  has not written the token yet the pane says so and offers to look again.
- `scripts/update-app.sh` closes the app up front and defaults its prompt to
  yes.

### Fixed

- The ESP32 sealed its panel edge in a fixed colour, so every cold-blue boot
  frame got a warm strip along the bottom, repainted each splash frame while
  the picture above it rolled. It read as the bottom of the panel
  misbehaving. The canvas now seals to whatever colour it last cleared to, and
  `scripts/render_esp32_preview.py` mirrors the seal so previews stop promising
  pixels the panel eats.
- The ESP32 quota page reclaimed 22px it was reserving for page dots nothing
  draws, which had left 50px of nothing under the chart against 28 above the
  header.

## 1.2.5 — 2026-07-30

### Added

- Coding-agent approvals show the agent's actual request. Every field the
  provider sent is listed in reading order with its own label — an `Edit` shows
  the file, the text being replaced and the replacement, tinted so the pair
  reads as a before and after. Bulk fields sit behind **Show request** so the
  feed stays scannable.
- **Why** carries Claude's own stated reasons for asking.
- A value the host had to clip says **Shortened to fit**, and dropped fields
  are counted, so a prefix of a command is never mistaken for the whole one.
- Each agent row says how long it has been waiting — same words and placement
  as an activity row's age, because they are two halves of one feed. A request
  that has sat for six minutes reads very differently from one that just
  arrived, and the permission hook gives up at around five.
- A third answer, **Always allow this exact request**, saves a permission rule
  so Claude stops asking. Headroom writes only the exact command or path it
  showed you — Claude's own "Yes, don't ask again" widens a command to a
  prefix, and a grant made from a phone outlives the request that prompted it.
  The row prints the rule under the buttons before you tap, and glob
  characters in paths are escaped so a folder named `[2024-06] Reports` cannot
  match its siblings. Questions are never offered it.
- Each row carries the agent's own mark in its brand colour instead of one
  generic speech bubble, so a Claude row and a Codex row stop looking alike.
- Notices that can only be dismissed can be swiped away. Rows carrying a real
  answer cannot: a swipe that denied a permission would send Claude a decision
  by accident.
- **You can answer Claude's questions from the phone.** Its options become the
  buttons, and tapping one sends the choice back so Claude carries on without
  you touching the Mac. No hook can hand `AskUserQuestion` a selection — but a
  denied `PreToolUse` call is documented to show Claude the reason, so the
  choice travels as the reason. It is a workaround and behaves like one:
  Claude sees a blocked tool plus your words rather than a clean result, so it
  may occasionally acknowledge the block. Headroom answers only a single
  question of two to six options, never a `multiSelect` one, and everything
  else — a timeout, an odd shape, or **Ask on Mac** — defers, which leaves the
  question to appear on the Mac exactly as before. See
  `docs/agent-attention.md`.
- Installed hooks are now version 2, adding a `PreToolUse` entry scoped to
  `AskUserQuestion`. Settings reports **Outdated** until you reinstall them.

### Fixed

- An `AskUserQuestion` row is readable. Claude's questions arrive through the
  permission hook, and the nested `questions` array reached the phone as a wall
  of raw JSON — the question was in there, but nobody was going to find it. The
  row now leads with the question itself instead of "Use AskUserQuestion", and
  each option is one control carrying the reason you would pick it. The first
  pass listed the descriptions above a row of buttons repeating the same
  labels, which said everything twice.
- Answer buttons take the account's accent instead of the system blue, so they
  belong to the agent that asked. Short answers stay bordered pills; a
  question's options are sentences with a reason under each, and tinting those
  turned every one into a large coloured slab. They read as plain rows with a
  divider and a chevron now — the shape a grouped list uses everywhere else on
  the system — with the colour on the chevron. **Ask on Mac** sits below the
  divider rather than among the answers, because it is the way out rather than
  another option.
- The provider mark moved to the top-right corner of an agent row. Which agent
  asked is a property of the row, not the first thing to read in the sentence.
- **Claude finished responding** no longer stacks. A session's finished or idle
  notice replaces the one it makes untrue, so the feed carries at most one per
  session instead of a wall of identical rows burying the approvals that
  actually want an answer. Superseding is scoped by session and kind, so a
  notice arriving can never close a permission request you have not answered.

### Changed

- The phone used to decode four fields of a request and drop the rest, which
  made an `Edit` approval read as "Use Edit" and a `Write` show a path but
  never the content. `detail.request` now carries typed fields end to end; an
  unrecognised tool renders without an app update.
- The Claude adapter reads `permission_reasons` (the documented field) as well
  as the older `permission_suggestions`, and keeps `tool_use_id` / `prompt_id`
  for correlation.
- `docs/agent-attention.md` corrects its claim that structured questions are
  notify-only pending provider support. Against Claude Code 2.1.220 the
  `Elicitation` hook returns real form values, `updatedPermissions` makes
  "always allow" answerable, and `decision.interrupt` exists — all four are
  wiring gaps on our side, now written down as such.

## 1.2.4 — 2026-07-30

### Added

- `scripts/update-app.sh` installs the latest notarized Release from the
  command line. This landed on `main` ahead of the bump and ships here rather
  than in a release of its own.

### Changed

- iOS Overview **Connected** tile names the Mac and its address (Computer
  Name · host / IP), not just the last update time. Settings → Connection
  shows the same split.
- Watch Overall burndown lines are fully opaque, matching the ESP32 glance.
  The binding source stays thicker; context sources no longer fade.
- Mac Settings moves the Dashboard row limits out of General and in beside the
  integrations whose rows they count.
- `docs/attention.md` writes down what Attention scoring is: hardcoded product
  policy, deliberately not a Settings pane. The README and CONTRIBUTING build
  instructions also stop being wrong about XcodeGen being optional and about
  `-sdk iphonesimulator` on the iPhone target.

### Fixed

- A source whose login has gone now says **Needs sign-in** instead of **Not
  updating**, and says it on the card, in Settings, and in Attention. The host
  ships `auth_required` next to `stale`, so a dead credential is no longer
  indistinguishable from a rate limit or a dropped network.
- Quota cards show the host's error whenever there is one. The message was
  gated on `ok`, which the host deliberately keeps true while it replays the
  last good bars — so the failures that had a reason worth reading were exactly
  the ones that hid it. A missing Claude token reported eleven hours of frozen
  numbers without ever surfacing the `claude login` it was asking for.
- Attention calls out a missing login immediately rather than waiting out the
  fifteen-minute stale threshold. Waiting does not fix a login.
- The ESP32 corner glyph marks a frozen reading, not just a dropped cable. It
  was gated on whether the Mac answered, so the case that lasts — the Mac
  replying every ten seconds about numbers it has been unable to refresh since
  last night — drew nothing at all. Ages now read `42m` / `11h` / `3d`, and a
  dead login is prefixed `!`.

## 1.2.3 — 2026-07-30

### Fixed

- Multi-Mac over iCloud actually connects. The signed app declared the iCloud
  container but carried no application identity to bind it to, so CloudKit
  refused every request with "Trying to initialize a container without an
  application ID". Releases now take the application identifier, team and
  container environment from the provisioning profile, the way Xcode does when
  it signs. 1.2.2 looked correct by every check available and never wrote a
  single record.

## 1.2.2 — 2026-07-30

### Fixed

- Multi-Mac says why it is not syncing. A CloudKit round that failed was
  discarded without a word, and the host's trouble text only ever described the
  folder transport, so every failure showed up as "No other Macs yet" — the
  same words a healthy sync with nobody else on it produces. A missing record
  type, a signed-out iCloud account and an unreachable network now each say so.
- The CloudKit schema ships as `macos/Headroom-CloudKit.ckdb` instead of living
  only in Apple's web console. It has to be deployed to Production before
  multi-Mac can work at all: released builds are pinned to that environment,
  and CloudKit creates record types automatically only in Development. See
  `docs/multi-mac.md`.

## 1.2.1 — 2026-07-30

### Fixed

- Multi-Mac over iCloud now works in released builds. Every release up to 1.2.0
  was notarized without the iCloud provisioning profile, so the published app
  carried no CloudKit entitlement and Settings reported iCloud as unavailable
  on every Mac that downloaded one. Nothing was red: the release was properly
  signed and notarized, and only a note in the build log said the feature was
  off. The workflow now embeds the profile, and refuses one whose team does not
  match the signing certificate. An app downloaded before this release does not
  gain iCloud, because entitlements are sealed into a signature.
- Settings no longer claims that signed releases can use iCloud. That was false
  for exactly the people reading it on a notarized release, and sent them to
  download another copy of what they already had. A local build and a release
  built without the profile now say different things.
- The iOS archive stopped minting a `Created via API` development certificate
  on every run, which walked the team toward its certificate cap and then
  failed the archive itself with `Choose a certificate to revoke`.
- iOS release builds no longer break on SDK-specific `CODE_SIGN_IDENTITY` keys
  written into the pbxproj unquoted.

## 1.2.0 — 2026-07-30

### Added

- Major Claude outages on status.claude.com light Attention — partial blips
  stay quiet.

## 1.1.9 — 2026-07-30

### Fixed

- Claude auth no longer borrows Claude Code's Keychain on every poll. Headroom
  imports the plan token once into `~/.headroom/oauth/` (one file per account),
  refreshes only that copy, and never writes back into Claude Code's item. A
  Keychain Deny stays denied until you refresh the source in Settings, instead
  of retrying every 20 seconds and re-prompting. Named Claude accounts each get
  their own Headroom file, same as before.

### Added

- Settings → General can open Headroom at login. macOS may still ask once in
  Login Items; the toggle says so and links there when approval is pending.

## 1.1.7 — 2026-07-30

### Changed

- Named accounts next to a brand mark show the user label (`Work`), not
  `Claude · Work`. The mark already names the tool; repeating it is how a row
  of Claude tabs all truncated to "Claude…". Full titles stay in Settings, the
  menu bar and other text-only surfaces.

## 1.1.6 — 2026-07-29

### Added

- Coding agents can ask you things through Headroom. When Codex wants to run a
  command or change a file, the approval becomes an item you can answer from
  the Mac or the phone rather than a terminal you are not sitting in front of.
  Each request is held until it is answered or expires, so a question does not
  disappear when the session behind it drops. Codex is the first provider, and
  the feed does not care which agent a request came from.

### Changed

- Other Macs sync over iCloud instead of a shared folder, so there is no
  directory to agree on: turn it on and the Macs find each other. Setting
  `icloud_dir` to a path still uses the folder transport.
- Settings is organised around what you are looking for rather than which part
  of the app happens to own it.
- The watch tile drops its headline at small sizes. Around 160 by 72 points the
  legend, the percent gutter and the weekday labels all stop being readable, so
  the chart takes the whole tile instead of competing with text.
- A full ring keeps a visible seam at 12 o'clock where its two caps meet,
  rather than closing into a solid circle you cannot read a value off.

### Fixed

- A weekly countdown that briefly sampled under the wrong Claude login no
  longer sticks for the rest of the week. The held reset re-anchors when the
  live reading points at an earlier instant, so one account stops showing
  another's "6d 19h".

## 1.1.5 — 2026-07-29

### Fixed

- The iPhone app reaches TestFlight again. Every release since 1.0.9 built it
  and then failed to sign it, so testers stayed on a build from 28 July while
  the Mac app went on to 1.1.4. Releases now sign the export with the team's
  distribution certificate and named App Store profiles, rather than asking the
  build machine to create credentials it has no way to create.

### Added

- `scripts/ship-ios.sh` sends a release to TestFlight from a Mac for when the
  workflow cannot. It refuses to run on a dirty tree or away from the release
  tag: the build number comes from the commit, and a build uploaded under the
  wrong one cannot be taken back.

## 1.1.4 — 2026-07-29

### Added

- Headroom is aware of your other Macs. Settings → Other Macs turns on sharing,
  and each Mac then publishes a small summary of itself to a folder in your
  iCloud Drive: what it is burning, how many local servers it has up, and
  whether it needs your attention. The popover lists the others with their own
  timestamps rather than merging them into one reading, because two Macs are
  allowed to disagree. Off until you turn it on.
- Enabled sources, pinned provider order, accent colours and the non-secret
  half of `config.json` follow you between Macs. Opening Headroom on a second
  Mac adopts the settings already in the folder instead of starting from
  defaults. Credentials and machine paths are never synced. See
  [docs/multi-mac.md](docs/multi-mac.md).
- A first-run Welcome window introduces the menu bar app, dashboard, quota
  rings, burndown charts, iPhone, Apple Watch and ESP32 companion. Settings can
  reopen it later, and About now carries the app version and product credits
  on both Mac and iPhone.
- Banked Codex reset credits now show their own expiry deadline on burndown
  charts, distinct from quota renewals and provider-granted resets.
- The ESP32 glance includes a compact burndown view, recent local Git activity
  and clearer host connection diagnostics.

### Changed

- The Mac quota dashboard adapts its tabs and card grid to the number of
  enabled providers instead of reserving space for providers that are hidden.
- Multi-Mac sharing can be enabled, disabled and inspected directly in
  Settings, including the current Mac, discovered peers and sync directory.

### Fixed

- The LaunchAgent now runs from `~/.headroom` rather than the read-only bundled
  host directory, so runtime state and relative writes have a writable home.

## 1.1.3 — 2026-07-29

### Fixed

- Named Claude accounts now read the Keychain credentials for their own
  profile instead of reusing the default account, so each account reports its
  own quota and stale state.

## 1.1.2 — 2026-07-29

### Fixed

- The macOS download is signed with Developer ID and notarized. Earlier
  releases shipped ad-hoc signed, so Gatekeeper refused to open them and
  reported that it could not verify the app. The signing certificate stored in
  CI held a private key with no certificate alongside it, which left the build
  job with no usable identity and sent it down its unsigned fallback path
  without failing.

## 1.1.1 — 2026-07-29

### Added

- Provider marks now identify every coding quota across the Mac, iPhone, and
  ESP32 dashboards, including named accounts under each provider.

## 1.1.0 — 2026-07-29

### Added

- Headroom now runs on Apple Watch with quota-ring and burndown
  complications. The iPhone forwards its existing snapshot over
  WatchConnectivity, so the watch does not need a second API or direct access
  to the Mac.
- Provider-granted resets now remain visible instead of making the burndown
  look as if it forgot the previous window. Charts show the forgiven curve and
  reset marker, the activity feed records the grant, and the ESP32 receives a
  compact version of the same history.
- The ESP32 has a generated cold-boot sequence plus an on-screen connection
  diagnosis that distinguishes Wi-Fi, host resolution, token, HTTP, and USB
  failures. The new flashing helper refuses to race another process for the
  serial port.
- Claude, Codex, and Cursor marks now identify their tabs in the Mac
  dashboard, including named accounts under each provider.

### Fixed

- **Claude quota could sit fifteen hours out of date and still read as live.**
  Claude Code keeps per-MCP-server OAuth in the same Keychain item as your plan
  token. Once that item held only `mcpOAuth`, the search for credentials ended
  there instead of going on to `~/.claude/.credentials.json`, so every fetch
  failed and no fresh login could bring it back. The search now passes over a
  store that has no plan token in it, and when there is none anywhere it tells
  you to run `claude login` rather than naming a missing JSON key.
- A source that has been failing for a day no longer reports as one poll old.
  Payloads carry the moment they were fetched; the poll clock only ever knew
  when we last *tried*, and a failing source is retried on the same schedule as
  a healthy one. This is what the menu bar's "N minutes stale" line reads, so
  it had been stuck at one minute for the whole outage. Snapshots written
  before the field get their age from the cache file's mtime, so the count is
  right on the first poll after upgrading rather than a fetch later.
- Stale quota no longer drives anything measured against the clock. The
  percentages still show — last-known beats blank — but the countdown, the
  pace, and the burndown chart drop out instead of being computed off a reading
  that has stopped moving. A frozen `resets_in_s` counted down was the most
  convincing wrong number on the card: right shape, right units, ticking a dead
  window to zero in front of you. One missed poll is still treated as a blip
  and changes nothing.
- Stale readings are no longer written to the quota sample log. Re-recording
  one reading laid down a flat line indistinguishable from a real idle stretch,
  and each sample walked the derived window forward, so a source that stopped
  answering last night still showed windows rolling on schedule today.

### Changed

- `providers[]` carries `stale`, `age_s`, and `stale_for_s` per source, so a
  client can tell last-known numbers from current ones without inferring it
  from an absent countdown.
- Activity rows now state their outcome and what needs attention in words.
  Colour remains supporting information rather than the only status signal.
- Quota rings use the sampled burndown pace when it is available, and visibly
  mark a provider whose last-known reading is no longer updating.
- Unconfigured Plausible and Supabase services stay out of the Mac and iPhone
  dashboards until they are enabled in Settings.
- App icons now use process cyan, magenta, and yellow rings so their three
  bands remain distinct at small catalog sizes.

### Fixed

- The app no longer reports its own staleness as the host's. It fingerprinted
  the host it bundles once per launch, on the premise that a running .app owns
  a read-only bundle — false every time a build lands on top of a running copy.
  The app then compared a dead fingerprint against a live host and offered an
  update that reinstalled the host already running, for ever. The fingerprint
  now recomputes when the files under `Resources/host` move.
- Skew has a direction. A host reporting a newer release line than the .app is
  no longer replaced with an older copy; the banner names the app as the half
  that needs updating and drops the button.
- Starting the host waits for the host it started. A 200 on :8737 was taken as
  success, which the process being replaced answers on its way out — and which
  a foreign host answers for ever, since ours stands down by design when the
  port is taken. Startup now waits for the fingerprint it installed, and says
  so when something else owns the port instead of reporting success.
- One install at a time. Launch, the poll loop, the setup card and the skew
  banner each ran their own bootout/bootstrap; whoever lost the race read
  `/usage` from a host mid-restart, which is how "Host is up" and "Could not
  connect to the server" ended up on screen together.
- The LaunchAgent asks for `KeepAlive/SuccessfulExit=false`. The host exits 0
  on purpose when another process owns the port, but an unconditional
  `KeepAlive` ignores exit status and respawned it every 5 seconds for ever,
  each respawn rescanning a week of logs.
- One failed call no longer counts as "no host". A refused server stop or a
  single flaky poll replaced the whole dashboard with the onboarding sheet;
  that now keys on whether `/health` answers.
- The setup card re-checks while it is open, instead of showing three lines
  captured at three different moments.

## 1.0.11 — 2026-07-29

### Added

- The widget now runs in macOS Notification Center, not just on the phone.
  Rings on the small size, combined burndown on the medium one, same as iOS.
- The Mac widget is current rather than a refresh interval behind. The app is
  the source of its own data, so it writes the shared cache after every
  successful poll of its host. The phone can only write after a background
  refresh, which iOS schedules when it feels like it.

### Changed

- One widget source, `widget/HeadroomWidget.swift`, builds for both platforms.
  What differs is the group id, which macOS prefixes with the team, and the
  Info.plist and entitlements under `widget/ios` and `widget/macos`.
- Quota presentation logic moved out of the iPhone target into
  `Shared/QuotaPresentation.swift` so both widgets and both apps read one
  implementation.
- The Mac app and its extension share an App Group. The app is not sandboxed
  and the extension is, so the group is the only thing between them.

### Fixed

- `build-app.sh` signs the extension separately from the app instead of with
  `--deep`. The two take different entitlements, and `--deep` stamped the
  app's onto the sandboxed extension, which then lost the group container.
  The build now also verifies both ends resolve to the same group, because a
  mismatch fails as a widget that loads and draws the placeholder for ever.

## 1.0.10 — 2026-07-29

### Fixed

- Building the iPhone app from a clean clone failed for everyone outside the
  maintainer's Apple team: `No profiles for 'com.centaur-labs.headroom' were
  found`. The documented command had no unsigned path and no
  `-allowProvisioningUpdates`, so automatic signing could never mint anything.
  There is now a simulator build that needs no Apple account, and the device
  build documents the two things a fork must change first.

### Changed

- The signing table in `CONTRIBUTING.md` no longer claims iOS profiles are
  "nothing to edit". `com.centaur-labs.*` belongs to one team and Apple will
  not issue it to another.
- CI-equivalent iOS simulator build added to the contributor build list.

## 1.0.9 — 2026-07-28

### Added

- The ESP32 reads its providers, their order, and their colours from the host
  instead of carrying its own copy.
- Per-provider colour override in Settings.
- Richer widget overview and shared ring polish across Mac and iPhone.

### Changed

- Burndown chart math and canvas furniture extracted into `Shared/` so the
  three clients draw from one implementation.
- App icon regenerated from the ring glyph.

### Fixed

- Firmware pace dots, and host accent colours that did not always land.

## 1.0.8 — 2026-07-28

### Added

- More than one account per provider. Each account meters separately and the
  pool ranks fold them together.

## 1.0.7 — 2026-07-28

### Added

- GitHub Actions watch list in Settings, spanning repos from more than one
  owner.
- Supabase security advisors, fetched separately from project health, with UI
  on both clients.
- Host Settings API backing the watch list and advisor activity.

### Fixed

- The Mac app degrades gracefully against a host with no `/github/watch`
  instead of showing an empty pane.
- The `/usage` filter contract no longer pins itself to one firmware
  signature.
- Firmware builds its JSON usage filter once, in PSRAM.
- iOS archives export with automatic signing.

## 1.0.6 — 2026-07-28

### Fixed

- Firmware projection dashes run along path length and open up enough to read
  at desk distance.

## 1.0.5 — 2026-07-28

### Added

- Shared palette, compact number format, and a 7-day burndown axis used by all
  three clients.

### Changed

- Host derives `Source` detail, summary, and blank states from pools.
- `urllib` plumbing shared through `http_util`.

### Fixed

- Burndown never renders in the alarm tint, which previously read as a warning
  when nothing was wrong.

## 1.0.4 — 2026-07-28

### Added

- Host pins source order and ships a top-3 focus that every surface honours.
- Drag-to-reorder in Mac Settings, week burndown, and the app icon.
- Focus rendering on iOS quota surfaces and an ASCII-safe firmware glance.

### Changed

- Signing uses `$HEADROOM_TEAM_ID` instead of a hardcoded team.
- The generated Xcode project is no longer tracked. `macos/project.yml` is the
  source of truth.
- Contributing guide, security policy, and backlog added for the public repo.

### Fixed

- The host token stays out of the launchd logs.

## 1.0.3 — 2026-07-28

### Fixed

- Release CI imports the full Developer ID identity so notarization completes.

## 1.0.2 — 2026-07-28

### Fixed

- First attempt at the Developer ID import above. Superseded by 1.0.3.

## 1.0.1 — 2026-07-28

### Added

- First public macOS zip.
- iOS releases publish through `asc` to the Internal TestFlight group.
- App Store listing copy, privacy policy, icon, and framed iPhone slides.

### Fixed

- iOS declares itself export-compliance exempt so TestFlight can assign builds.
- TestFlight export uses manual App Store profiles.

## 1.0.0 — 2026-07-28

First tagged release.

### Added

- Menu bar app with a welcome flow, bundled-host control, and enabled-only
  providers.
- Registry-driven quota providers in the host, local detection, and `/setup`.
- `HeadroomMobile` companion app reading the same shared usage document.
- ESP32 firmware with a Cursor Total+API burndown overlay, stamped with a
  local build counter and commit hash.
- Shared models across Mac and iPhone under the Headroom name.
- Host `VERSION` fingerprint so the app can tell a stale LaunchAgent from a
  current one.
- Apple commit-count versions, notarization, and TestFlight CI.

### Changed

- Burndown and quota rings use brand-tint-only styling.
- GitHub Actions failures age out of Attention and the activity feed.

### Fixed

- iOS keeps the last `/usage` on disk when the Mac is unreachable, and forces a
  source sync when recovering from a stale archive.
- macOS forces a source sync after wake and shows a reconnecting status.
- Flat burndown projections are skipped rather than painting a misleading level
  bar.
