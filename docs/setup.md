# Setup after install

First-run behaviour, source lists, extra accounts, colours, focus order, and
tokens. Install the Mac app from the [root README](../README.md) first.

## First run (Mac)

- `~/.headroom/sources.json` is seeded from **local detection** — only providers
  that look signed-in are enabled. If none are found, every quota source stays
  on so the UI can show sign-in errors.
- The Welcome sheet lets you confirm toggles before the overview appears.
- Edit `~/.headroom/config.json` later for git authors / GitHub org / timezone
  (full key list: [host.md](host.md)).

```bash
curl -s localhost:8737/health | python3 -m json.tool
curl -s localhost:8737/setup  | python3 -m json.tool
```

## Two kinds of source

Onboarding and Settings keep them apart — different questions, different setup:

- **Providers** (AI coding tools) — Claude, Codex, Cursor, Copilot, Gemini,
  Windsurf, JetBrains AI, Zed. How much plan is left. Read from the sign-in
  already on the Mac; nothing to paste. Order and focus live here; extra
  accounts via **Add account** under Library.
- **Integrations** — Vercel, Git, GitHub Actions, Supabase, Plausible, PostHog,
  Sentry, Datadog, Axiom, OpenRouter, AI Gateway, local servers, Xcode builds.
  What your projects are doing. Some want a key, pasted once in **Mac
  Settings** (Keychain — never sent to the phone or written into `/usage`).
  **Git** and **GitHub Actions** are separate on purpose: Git reads local
  commits on disk (no token, including unpushed); Actions needs a GitHub PAT
  and only sees what GitHub knows.

| Integration | Where |
|---|---|
| **Git** | Settings → Integrations → Git (`dev_root` + authors). Activity → Git commits |
| **GitHub Actions** | Settings → Integrations → GitHub Actions (classic `repo`, or fine-grained Actions + Issues + Pull requests Read) |
| **Supabase** | Settings → Integrations → Supabase account PAT (no narrower scopes) |
| **Plausible** | Settings → Integrations → Stats API key (`stats:read`; `sites:read` to list sites) |
| **PostHog** | Settings → Integrations → personal API key (`project:read`, `query:read`) |
| **Vercel** | Already signed into the Vercel CLI |
| **Sentry** | Settings → Integrations → auth token (`event:read`) |
| **Datadog** | Settings → Integrations → API + App key (`monitors_read` on the app key) |
| **Axiom** | Settings → Integrations → API token (`monitors\|read`) |
| **OpenRouter** | Settings → Integrations → Management API key from [openrouter.ai/settings/management-keys](https://openrouter.ai/settings/management-keys) (not an inference key from `/settings/keys`) |
| **AI Gateway** | Settings → Integrations → Gateway API key (not the Vercel CLI login) |
| **Local servers** | Discovered via `lsof` (no key) |

Which repos Actions watches is editable under **GitHub Actions**: owner
filters, an always-watch list, and the discovery cap.

Both lists toggle under Settings. The same flags drive the menu bar, overview
rings, iPhone, and the optional desk board.

## Extra accounts

Personal Claude and work Claude, two ChatGPT logins, a second Cursor profile —
each is a separate plan. Add them under **Settings → Providers**: multi-account
providers get an **Add account** chip in Library. Pick the provider, name it,
point it at the credential location that login already uses.

| Provider | What to point it at |
|---|---|
| **Claude** | A second config folder — `CLAUDE_CONFIG_DIR`, e.g. `~/.claude-work` |
| **Codex** | A second `CODEX_HOME`, e.g. `~/.codex-work` |
| **Gemini** | A second Gemini CLI home, e.g. `~/.gemini-work` |
| **Cursor / Windsurf** | Another profile's `state.vscdb` |

No tokens are pasted: the host imports what that CLI already wrote into
`~/.headroom/oauth/`, then refreshes that copy — it does not write back into
Claude Code's Keychain. Each account gets its own row under an id like
`claude:work`. The default login keeps the plain `claude` id. Copilot,
JetBrains AI and Zed stay single-account (one credential per Mac).

The list lives in `~/.headroom/accounts.json`. Adding or removing one restarts
the host briefly so the sample schema and meters rebuild together. Shape:
[host.md](host.md#extra-accounts).

## Colours

Each provider ships with its brand colour. Click the dot on a provider row in
Settings → Providers for a grid of 24 colours plus **Default**. Overrides land in
`~/.headroom/sources.json` as `accent`, so the menu bar, popover, burndown,
iPhone, widget, and board (recent firmware) all match. Integration rows have
no brand colour — their dot is the health light.

## Focus order (top 3)

Drag the AI rows in Settings → Providers to reorder. Compact surfaces — menu-bar tanks,
widgets, board rings — show the first three *enabled* providers. The host
picks them and ships the ids as `focus` in `/usage`, so Mac, phone, widget and
desk never disagree about which three. A provider added in a later release
lands at the end of your order instead of jumping the queue.

## Tokens (host vs mobile)

`/usage` carries repo names, commit subjects, local paths/ports, and spend.
The host binds `0.0.0.0` so LAN clients can reach it — **non-loopback callers
must present a token**. Loopback (menu bar, `curl localhost`) needs nothing.

| Name | File | Who uses it |
|---|---|---|
| **Host token** | `~/.headroom/token` | ESP32 (`HOST_TOKEN`), any generic LAN client |
| **Mobile token** | `~/.headroom/mobile-token` | iPhone only (Mac Settings → **Copy mobile token**) |

Send either as `X-Headroom-Token:` or `Authorization: Bearer`. The mobile token
is scoped by Mac Settings → iPhone pairing (`read` / `refresh` / `sources` /
`servers`). Override the host token with `auth_token`, or open the LAN with
`"require_auth": false`, in `~/.headroom/config.json`.

Route classes and who may call what: [trust.md](trust.md).

## What the host measures

**AI coding tools** — plan left, and what it cost:

| Source | How |
|---|---|
| Claude | Keychain OAuth → Anthropic usage; token *value* via `pricing.py` |
| Codex | `~/.codex/auth.json` → weekly window, pace, reset credits, spend |
| Cursor | `state.vscdb` → Auto / API / total + on-demand |
| Copilot | GitHub token → premium / chat quota |
| Gemini | `~/.gemini` OAuth → Pro / Flash buckets |
| Windsurf | IDE plan cache → daily / weekly |
| JetBrains AI | Local `AIAssistantQuotaManager2.xml` → monthly credits |
| Zed | Keychain session → edit-prediction quota |
| Daily burn | Per-day %-point burn across enabled coding providers |

**Integrations** — what the projects are doing:

| Source | How |
|---|---|
| Vercel | CLI auth → recent team deployments |
| Git | Local commits under `dev_root` matching `git_authors` (no GitHub token; includes unpushed) |
| GitHub Actions | Failed / running runs + inbox via Settings token (`repo` / fine-grained Actions+Issues+PRs Read) |
| Supabase | Project health + security advisor lints via Settings account PAT |
| PostHog | Project events / users / live via Settings personal API key (`project:read`, `query:read`) |
| Plausible | Site visitors / realtime via Settings Stats API key (`stats:read`) |
| Sentry | Unresolved issues via Settings auth token (`event:read`) |
| Datadog | Alert / Warn monitors via Settings API + App key (`monitors_read`) |
| Axiom | Open monitors via Settings API token (`monitors\|read`) |
| Local servers | `lsof` TCP LISTEN → labeled ports (stop from the menu bar) |

Failures keep the last-good snapshot (`cache_util.keep_stale`). Each row is one
entry in `BASE_SOURCES` in `host/sources_config.py`, tagged `group="ai"` or
`group="devtools"`. Adding a coding provider is one registry entry + a
fetcher; a provider with a movable credential path also carries an
`account_kind` so one entry can expand into several rows.
