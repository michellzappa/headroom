# The trust boundary

Every route on the host answers one question before it does anything: *is this
caller allowed to ask?* Today each route answers it individually, in a ladder
of `if`/`elif` inside `do_POST`. That ladder is correct. It is also where the
next route gets classified by whoever is adding it, at whatever hour they are
adding it.

This file is the rule that ladder is an implementation of, so a new route is a
lookup rather than a judgment call.

**This is the inward-facing half.** [SECURITY.md](../SECURITY.md) is the
outward-facing one: what an attacker gets, where they have to stand to get it,
and how to report a hole. If the two ever disagree, SECURITY.md is what users
read, so fix it first.

## The four callers

`Handler._allowed()` recognises exactly three credentials, and the transport
adds a fourth caller that needs none.

| Caller | Identified by | Credential |
|---|---|---|
| **Loopback** | source address is `127.0.0.0/8` or `::1` | none |
| **USB CDC** | arrives through `usb_bridge`, not the socket | none |
| **Phone** | `X-Headroom-Client: ios` + mobile token | `~/.headroom/mobile-token` |
| **LAN** (board, `curl`, anything else) | host token | `~/.headroom/token` |

Both tokens are 32 bytes of `secrets.token_urlsafe`, written 0600, compared
with `hmac.compare_digest`. `"require_auth": false` in `config.json` disables
the check entirely and is a lab setting.

`_is_private()` is a separate axis from identity: loopback, RFC1918, or
Tailscale's `100.64.0.0/10`. It answers *"could this plausibly be my own
network"*, and it gates capability, never identity.

## The four route classes

Every route belongs to exactly one. Pick the class first, then write the
handler.

### Class 1 — Mac-local

**Anything that names, reads, or changes credentials or on-disk configuration.**
Loopback only. Not the phone, not with the host token, not on the LAN, not
ever.

`/accounts`, `/github/watch`, `/config/git`, `/config/vercel`,
`/config/supabase`, `/config/display`, `/agents/config`, `/agents/claude/config`,
`/machines/config`, `/machines/sync`, and the three Claude hook endpoints
(`/agents/hooks/claude/{permission,question,event}`).

The hook endpoints are in this class for a different reason than the rest:
they are how a coding agent on this Mac *asks* Headroom for a decision. A
remote caller who could post to them could manufacture an approval request
that never came from an agent.

### Class 2 — Ambient read

**The usage document and the things that describe it.** This is what the board
and the phone exist to read. It splits in two, and the split is easy to miss:

| Route | Host token on the LAN | Phone |
|---|---|---|
| `/usage`, `/health`, `/setup`, `/mobile/permissions` | yes | needs `read` |
| `/agents/capabilities`, `/attention/events` | **no** | needs `read` |

The board reads `/usage` and has no business reading an agent's pending
requests, so the two agent-facing reads are loopback-or-phone only. A LAN
caller holding the host token gets a 403 there — the one place where the host
token is *weaker* than the mobile token, and deliberately so.

A paired phone whose dashboard scope was revoked gets a 403 rather than a
snapshot, on all of them.

### Class 3 — Scoped control

**Changes state on the Mac, but nothing secret.** Requires a private-network
address *and* the matching per-capability grant from Mac Settings.

| Route | Permission |
|---|---|
| `/sync/refresh` | `refresh` |
| `/sources` | `sources` |
| `/local/stop` | `servers` |
| `/attention/ack` | `read` |
| `/attention/events/{id}/respond` | `agents` |

`_mobile_permission_allowed()` requires all three of: private address, valid
mobile token, permission present in `app_config.mobile_permissions()`. The
permissions are a **whitelist** — an unknown string in the config file is
dropped, not honoured.

### Class 4 — Agent control

`/attention/events/{id}/respond` is listed above under Class 3, and that
placement is the open question in this design.

Answering an agent's permission request approves **a command that then runs on
this Mac**. It is the same HTTP shape as toggling a source, and nothing like
it in consequence. Right now what stands between the network and that
capability is: a private-range source address, a bearer token, and the
`agents` grant. Face ID is enforced **on the phone**, by the phone, and a
client that chooses not to ask is not detected.

There is no TLS, so on a shared segment the token and every approval travel in
cleartext.

**This is a known gap, stated deliberately rather than left implicit.** Three
ways to close it, in rising order of work:

1. **Restrict `agents` to loopback + Tailscale.** `_is_private()` already
   separates CGNAT from RFC1918; the change is one predicate. Costs the
   café-Wi-Fi case, which is arguably not a case anyone should want.
2. **Per-response HMAC.** The event already carries a `revision` and an
   `idempotency_key` for compare-and-swap. Signing `(event id, revision,
   action)` with a pairing-time secret makes a captured body inert against any
   other event, without introducing TLS.
3. **TLS with a certificate pinned at pairing.** Solves eavesdropping for every
   route, not just this one, and is the honest answer to "no TLS" in
   SECURITY.md. It is also the most work, and a self-signed cert on a LAN name
   has its own sharp edges.

Whichever lands, the rule stays: **a route that can cause code to run is not
the same class as a route that changes a display preference**, even when their
handlers look alike.

## The loopback exemption assumes one human

Loopback is trusted with no token at all. The reasoning in `auth.py` is sound
as far as it goes: the menu bar, `curl localhost` and the USB bridge all
already imply access to the machine.

What it actually implies is access to *the machine*, not to *your account*. On
a Mac with a second logged-in user, that user can read `/usage` — repo names,
commit subjects, branch names, local server paths and PIDs, plan tier, USD
spend — and can post to every Class 1 route, including the agent hooks.

For the machine this is built for, that is fine. It should still be a stated
assumption rather than an accident: **Headroom assumes a single-user Mac.** If
that ever stops being true, the exemption becomes a token check against a file
only your account can read, which is the same mechanism already in place for
the LAN.

## The board holds the stronger credential

The ESP32 carries `HOST_TOKEN` in flash, in cleartext, by design — same as
`OTA_PASSWORD`. That token is the *host* token, which is the fallback
credential for every non-iOS caller, so it is strictly more powerful than the
phone's.

A board taken off the desk is a credential that reads `/usage` from anywhere
on the network until it is rotated. Rotation is in
[SECURITY.md](../SECURITY.md) and requires a reflash, which requires the cable,
which is the same physical access that took the board. The exposure is real and
small; the point is that it is the *host* token and not a third, weaker one.

Giving the board its own scoped credential is the clean fix and has never been
worth the third token file. Worth revisiting if a second device class ever
needs LAN read.

## Adding a route

1. **Does it touch credentials or config on disk?** → Class 1. Loopback only.
2. **Does it only read?** → Class 2. Add the `read` gate for phones.
3. **Does it change something?** → Class 3. Give it a permission from
   `MOBILE_PERMISSION_ORDER`, or add one; never reuse a neighbour's.
4. **Can it cause code to execute?** → Class 4. Do not ship it under a Class 3
   gate without saying so here.
5. POST requires `Content-Type: application/json`; the handler rejects
   anything else with 415 before parsing.
6. Add the route to `docs/privacy.md` if it exposes anything new to the phone,
   and to `SECURITY.md` if it changes what an attacker gets.
