# Orgs — multiplayer window play

Standing design for an **optional** org layer on top of Headroom. Solo
Headroom stays local-first and unchanged when this is off. This file is the
spec; nothing here is shipped yet.

Related: [product.md](product.md) (single user, single desk),
[multi-mac.md](multi-mac.md) (your Macs, not other people),
[metering.md](metering.md) (windows, seats, attribution),
[rings.md](rings.md) (pace dot), [telemetry.md](telemetry.md) (anonymous
cohort — different consent, different payload), [trust.md](trust.md).

## What this is

**Multiplayer quota pacing.** An org is a roster of seats playing the same
kind of window Headroom already draws for one person. Members see each
other's *coarse* window state — not prompts, paths, or repos.

The single-player game, restated:

| Solo reading | Meaning |
|---|---|
| Usage arc vs pace dot | ahead / on pace / behind |
| Hit 100% early | crashed the window |
| Reset with a lot left | wasted the window |
| Land near 100% at reset | clean run |

The org view is that game with other humans on the course.

## What this is not

- Not a feed of what people are coding.
- Not shared control of anyone's Mac or Attention.
- Not a replacement for provider admin consoles.
- Not an identity provider for the local host. Loopback, tokens, and
  Class 1–4 routes in [trust.md](trust.md) stay as they are.
- Not automatic. Solo installs never talk to the org backend until someone
  opts in and joins (or creates) an org.

**Single user, single desk remains true for the Mac host.** Orgs are a
cloud-shaped *view* over thinned publishes, not a multi-user install.

## Why it earns a place

Headroom's distinctive claim is the window skill — burn the pool well, don't
crash, don't waste. Teams already share that pain (Max seats, Cursor
Business, "who ate Opus"). The org layer is how that skill becomes visible
across seats without turning Headroom into FinOps or a social network.

If solo never names the skill out loud (ahead / waste / clean run), the org
roster has nothing worth showing. **Solo clarity first; org second.** See
Phasing.

---

## Actors and identity (semi-anonymous)

No email-as-account required for v1. The backend knows **capability tokens**,
not people.

| Object | What it is | Stable? |
|---|---|---|
| **Player** | A local identity on one Mac install (or one iCloud-linked install — open) | yes, until rotated |
| **Player publish token** | Secret the Mac uses to POST glance rows | yes, rotatable |
| **Display name** | Short label the player chooses per org (or one default) | mutable |
| **Org** | Named roster container | yes |
| **Invite** | One-time or limited-use code / link | expires |
| **Membership** | Player ↔ org, with a role | until leave / kick |

**Semi-anonymous** means:

- The server stores `player_id` (random), display name, org ids, and thinned
  glance rows. No email, no GitHub login, no install fingerprint beyond the
  player key material we issued.
- A display name is a *handle inside an org*, not a verified identity. Two
  orgs can see different names for the same player.
- Linking a player to a real person happens only socially (you invited Ada;
  you recognise the handle). The backend does not try to know Ada.

Recovery without email is weak by design (see Open questions). That is an
acceptable v1 trade if create/join is cheap and the blast radius of a lost
player key is "rejoin orgs," not "lose local Headroom."

### Multiple orgs

A player may belong to **many orgs** at once (personal studio, employer,
OSS crew). Each membership is independent:

- Publish prefs can differ per org (publish to work, not to the weekend
  crew — or the reverse).
- The Mac (and phone) show an **org switcher** when `memberships.length > 1`.
- Creating an org does not leave other orgs.
- Leaving an org deletes that membership and stops publishing to it; it does
  not delete the player or other memberships.

Default UX when you create or accept an invite: you are in that org and the
org view appears in the client. No second "enable org dashboard" gate after
join — joining *is* the gate.

---

## Roles

| Role | Can |
|---|---|
| **owner** | rename org, invite, revoke invites, kick, transfer ownership, delete org, set org visibility defaults |
| **member** | see roster, publish own glance (if they opt to), leave, invite if org allows |

v1: two roles only. No billing admin vs viewer split until Account types
needs it.

---

## The org view (multiplayer UI)

One screen, one job: **who is where in the window.**

Per member row (thinned; see Publish payload):

- display name
- provider focus that they chose to publish (e.g. Claude week, Codex)
- `pct` used and `pace_pct` (or derived ahead/behind delta)
- coarse state: `on_pace` | `ahead` | `behind` | `crashed` | `idle` | `stale`
- model-family mix (sonnet / opus / gpt / … — same normalisation as telemetry)
- optional accent colour (taste, not identity)
- `updated_at` age

Org rollup (derived server-side or client-side from the same rows):

- seats publishing vs seats idle/stale
- median ahead/behind
- crash count this window (members who hit ~100% with material time left)
- waste at last closed window (if we ever receive reset summaries)
- model-family mix across the org

**Not in the roster:** repos, commits, local servers, Attention reasons,
exact dollars, emails, machine hostnames, raw model ids.

Interaction model is glance + switcher, same posture as the popover. No chat,
no comments, no @mention inside Headroom.

### Multiplayer logic (game rules)

Shared vocabulary, computed from published meters:

| State | Rule (v1 sketch) |
|---|---|
| `on_pace` | \|pct − pace_pct\| ≤ T₁ |
| `ahead` | pct − pace_pct > T₁ |
| `behind` | pace_pct − pct > T₁ |
| `crashed` | pct ≥ 99 and resets_in_s above a floor (still lots of window left) |
| `idle` | no publish with meaningful burn for N hours while others are active — or pct flat across publishes |
| `stale` | last publish older than S (member may have paused publishing) |

Constants T₁ / N / S are product policy (like Attention weights): hardcoded
at first, not a Settings pane. Document them next to the scorer when code
lands.

**Clean run** (optional, post-reset): when a member publishes a window-close
summary with final_pct in a high band and no mid-window crash flag. Useful
for a weekly strip; not required for v1 roster.

Competitive chrome (leaderboard, scores) is **off by default** and org-optional.
The default org view is operational ("is Ada about to wipe the seat"), not
gamified. Gamification is a skin on the same rows.

---

## Publish payload

Opt-in, per org (or "all my orgs" with per-org mute). The Mac host (or app)
POSTs a small document on an interval / on meaningful change — not on every
`/usage` poll.

```jsonc
{
  "player_id": "…",
  "org_id": "…",
  "published_at": 1730000000,
  "display_name": "mz",
  "meters": [
    {
      "provider": "claude",       // allowlisted registry id
      "pool": "week",             // allowlisted pool key
      "pct": 72.0,
      "pace_pct": 68.0,
      "resets_in_s": 180000,
      "accent": "#D97757"         // optional
    }
  ],
  "model_shares": { "opus": 40, "sonnet": 55, "other": 5 },
  "flags": { "crashed": false }
}
```

Server allowlists provider ids, pool keys, and model families the same way
telemetry does. Unknown keys drop; oversized bodies 413; auth failure 401.

**Never accepted:** prompts, paths, repo names, commit subjects, Attention
fingerprints, account emails, host tokens, exact USD.

Stale rows: if no publish within S, roster shows `stale` then eventually
hides from the "live" strip (membership remains).

---

## Backend (Workers + D1)

Sibling to `telemetry/`, **not** an extension of the telemetry contract.
Different consent, different retention, different auth.

Suggested layout (names flexible):

```
orgs/                    # or multiplayer/
  worker.js
  schema.sql
  wrangler.toml
  README.md
```

### Auth model

Capability URLs / bearer secrets, no session cookies in v1:

| Secret | Held by | Powers |
|---|---|---|
| `player_secret` | Mac Keychain (syncable later, like PATs) | create org, accept invite, publish, read orgs for this player, leave |
| `invite_secret` | shareable link | one accept → membership + discard/decrement |
| `org_read` (optional) | derived / separate | read-only roster for a wall display — open |

Every mutating route checks the player secret. Invite accept binds a player
to an org; it does not reveal other members' secrets.

### Routes (sketch)

| Method | Path | Job |
|---|---|---|
| `POST` | `/v1/players` | mint player id + secret (once per install that opts in) |
| `POST` | `/v1/orgs` | create org; caller becomes owner |
| `GET` | `/v1/orgs` | list memberships for this player |
| `PATCH` | `/v1/orgs/:id` | owner: rename, flags |
| `DELETE` | `/v1/orgs/:id` | owner: delete |
| `POST` | `/v1/orgs/:id/invites` | owner (or member if allowed): mint invite |
| `POST` | `/v1/invites/:token/accept` | join org |
| `POST` | `/v1/orgs/:id/leave` | member leave; owner must transfer first |
| `DELETE` | `/v1/orgs/:id/members/:player_id` | owner kick |
| `PUT` | `/v1/orgs/:id/publish` | upsert this player's glance row |
| `GET` | `/v1/orgs/:id/roster` | full thinned roster for members |
| `POST` | `/v1/players/rotate` | rotate player secret; old revoked |

Rate-limit publishes and creates. Minimum group size is **not** required
(roster is private to the org, unlike Community Pulse).

### Schema sketch

- `players` — id, secret_hash, created_at, plan (`free` \| `pro` \| …)
- `orgs` — id, name, owner_player_id, created_at, settings_json
- `memberships` — org_id, player_id, role, display_name, created_at
- `invites` — token_hash, org_id, created_by, expires_at, max_uses, uses
- `roster_rows` — org_id, player_id, payload_json, published_at

Hash secrets at rest (or encrypt). Raw secrets shown once at mint.

### Retention

- Roster payloads: short operational window (e.g. overwrite in place; no
  long history in v1).
- Optional later: closed-window summaries for waste/clean-run (30–90 days).
- Deleting an org deletes memberships, invites, and roster rows.
- Player delete (leave all + tombstone) is a v1.x need once paid exists.

---

## Client surfaces

| Surface | Org behaviour |
|---|---|
| **Mac Settings** | Opt in: create player, create/join org, invites, per-org publish toggles, plan |
| **Mac popover** | Org section when memberships exist; switcher; roster rows (read-only) |
| **iPhone** | Same roster read; join via invite link; publish still originates on Mac |
| **Watch / ESP32** | Out of scope for v1 (no room, no auth story on the board) |
| **Public web** | Invite landing ("open in Headroom") only — not a full dashboard |

Phone does not become the publisher. The Mac host (or Mac app, same debate
as telemetry ownership) owns POST `/publish`. Phone only reads roster with
the player secret it already stores for Settings — or a read-scoped child
token if we split later.

### Relationship to multi-Mac

[multi-mac.md](multi-mac.md) stays **your machines**. Orgs are **other
people**. Do not merge `machines[]` into the org roster. A single human with
two Macs should publish **one** player row (dedupe by player id), not two
seats — otherwise the org thinks they have two humans. Open question: which
Mac wins the publish when both are awake (last-write-wins is enough for v1).

---

## Account types (free / paid)

Needed only so the backend can enforce limits without inventing a second
product. Plans hang on the **player** (billing subject), not on each org —
unless we later sell org seats as the SKU.

### Proposed v1 limits (numbers are placeholders)

| | Free | Pro (player) |
|---|---|---|
| Join orgs | yes | yes |
| Create orgs | 1 | many (cap) |
| Members per owned org | small (e.g. 5) | larger (e.g. 25+) |
| Publish interval | slower | faster |
| Closed-window history | no | yes |
| Competitive / clean-run strip | no | optional |
| Priority support / kit discount | — | maybe |

**Solo Headroom stays fully usable with orgs off and with Free.** Org is the
upsell surface; rings and Attention are not paywalled.

Billing provider (Paddle, Stripe, Apple) is an open decision. Apple IAP only
matters if org management ships as a significant iOS feature; Mac-first
billing is simpler for a Developer ID app.

Owner of a free org who needs more seats upgrades **their** player plan (or
we later add org-level billing — see questions).

---

## Privacy and trust

- Org publish is **off until join/create**. Creating an org implies publish
  to that org; joining implies the same unless we add a post-join publish
  toggle (recommended: explicit "Publish my glance to this org" defaulting
  **on** at join, easy to mute).
- Payload allowlist enforced server-side, same posture as telemetry.
- SECURITY.md / privacy.md must gain a section before ship: what leaves the
  Mac, retention, rotation, kick.
- Invite links are secrets. Treat like mobile pairing tokens: show once,
  rotate, expire.
- Kick removes future roster reads for that player secret on that org; cached
  phone snapshots may lag until refresh.

This feature **does** put Headroom in the cloud for opted-in players. Copy
must say that plainly — parallel to "Share settings between my Macs", not
buried under Telemetry.

---

## Phasing

| Phase | Ship | Why |
|---|---|---|
| **0** | Solo: name ahead/behind, waste, clean run in UI copy + history | Sport must exist alone |
| **1** | Player mint + create/join org + roster read/publish + Mac UI | Multiplayer MVP |
| **2** | Invites polish, kick, multi-org switcher, phone roster | Daily driver |
| **3** | Plans + enforcement | Return path |
| **4** | Window summaries, waste/clean-run org strip, optional scores | Game depth |
| **5** | Admin-API imports / shared-login attribution | Only if providers cooperate |

Do not start at Phase 3. Do not ship Phase 1 without Phase 0 readable in
the solo app — otherwise the roster is a table of percentages with no craft.

---

## Explicit non-goals (v1)

- SSO / Google / GitHub login
- SCIM, directory sync, audit log export
- Publishing Attention to the org
- Controlling a teammate's servers
- Public org pages (that is the separate "publish glance URL" idea)
- Merging provider billing admins into Headroom
- Board/Watch org roster

---

## Open questions

Answer these before implementation starts in earnest. Spec stays draft until
the load-bearing ones land.

### Product

1. **Is the SKU the player (Pro unlocks bigger orgs) or the org (seat
   licenses)?** Player-billing is simpler; org-billing matches how companies
   buy.
2. **What is Free for?** Growth wedge (small squads) or trial? Caps above are
   guesses — what feels generous vs what feels broken?
3. **Default publish on join — on or off?** On maximises the roster; off
   maximises trust. Which failure mode do we prefer?
4. **How aggressive is gamification?** Operational roster only for v1, or a
   quiet clean-run mark from day one?
5. **Display names:** unique per org? Allow duplicates? Moderation at all?
6. **Providers in v1 roster:** Claude only, all window meters the member has
   in focus, or member picks one "org meter"?
7. **Shared-login orgs (shape A):** in scope later, or never? (One Max
   account, many humans — attribution without separate seats.)

### Identity / recovery

8. **Player bound to Mac only, or sync via iCloud Keychain** like GitHub
   PATs? Sync makes multi-Mac one seat; Mac-only makes two Macs look like two
   people until we dedupe.
9. **Lost player secret:** accept "create new player + rejoin" for v1, or
   build recovery (email, iCloud) immediately?
10. **iPhone-only join** without Mac publish — allowed as read-only spectator?

### Backend / ops

11. **Same Cloudflare account as telemetry, separate Worker** — confirmed?
12. **Self-host story:** publish schema + worker like telemetry, or managed
    only while billing exists?
13. **Invite format:** `headroom://org/join/…` plus https landing?
14. **Abuse:** org spam, invite scraping, roster scraping — enough with rate
    limits + caps, or captcha on `POST /players`?

### Client

15. **Who POSTs publish — Mac app or Python host?** Telemetry is app-owned;
    org publish wants host cadence. Same split or unify?
16. **Org section placement:** popover peer of Attention, or Settings-only
    until Pro?
17. **Contract:** does `/usage` grow an `orgs[]` summary, or does the app
    fetch roster out-of-band from the Worker only?

### Legal / positioning

18. **Does "no Headroom cloud account" survive?** Proposed rewrite: *No
    account required for local use; org multiplayer is an optional cloud
    mode.* Confirm copy.
19. **MIT + paid cloud:** any concern, or binary/service clearly separate?

---

## Success criteria (when to call v1 real)

- Two humans on two Macs join one org in &lt; five minutes via invite.
- Each sees the other's week pct + ahead/behind without seeing repos or
  prompts.
- A third org can be created and joined without leaving the first.
- Publishing muted for one org does not mute the other.
- With orgs off, behaviour and privacy match today's solo app bit-for-bit.
- Free caps enforce without bricking solo features.

---

## Summary

Orgs are **optional multiplayer for the window game Headroom already is**.
Semi-anonymous players, many orgs, thinned roster, Workers/D1 sibling to
telemetry, free/paid as caps on org power — not as a lock on the glance.
Ship the solo vocabulary first; then the roster; then the plan.
