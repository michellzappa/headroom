# Security

## Reporting

Email **mz@envisioning.com** with "Headroom" in the subject, or open a
[private advisory](https://github.com/michellzappa/headroom/security/advisories/new).
Please do not open a public issue for anything that exposes a running install.

This is a side project with one maintainer, so expect a first reply within a
week rather than a day. Say what an attacker gets and where they have to be on
the network to get it, and I will confirm and credit you when it is fixed.

## What Headroom touches

The host runs as you, on your Mac, and reads what other tools already left
there:

- Claude OAuth under `~/.headroom/oauth/` (imported once from Claude Code's
  Keychain or credential file; refreshes never write back into Claude Code)
- Gemini and Zed OAuth material via Keychain
- `~/.codex/auth.json`, Cursor's `state.vscdb`, IDE plan caches
- optional GitHub, Supabase, Plausible, PostHog, OpenRouter and Vercel AI Gateway keys
  you paste into Mac Settings, stored in Keychain and never written into
  `/usage`. An OpenRouter Management key or AI Gateway key can read org-wide
  spend — prefer the narrowest key the provider offers
- `git log`, `gh`, the Vercel CLI, and `lsof` for listening ports

It sends none of it anywhere unless you leave **Share anonymous product
diagnostics** on in Settings → Telemetry. That path is opt-out by default,
sends at most one aggregate batch per week, and never includes prompts,
paths, tokens, or a stable install id — see [`docs/telemetry.md`](docs/telemetry.md).
A week-scoped HMAC dedupe key stops the same Mac from counting twice in one
week without creating a long-lived identity on the server. Country of the
request is stored as an ISO-3166 code from Cloudflare’s edge geo; the IP is
not persisted.
There is no Headroom account beyond that first-party intake.


## What is exposed, and to whom

`GET /usage` carries repo names, commit subjects, branch names, local server
paths and ports, plan tier, and USD spend. The host binds `0.0.0.0` so the
ESP32 can reach it, which puts that document in front of the rest of the
network too. So:

| Caller | Needs |
|---|---|
| Loopback (menu bar, `curl localhost`) | nothing; it already implies the machine |
| ESP32 or any LAN client | the **host token**, `~/.headroom/token` |
| iPhone | the **mobile token**, scoped by Mac Settings |
| USB CDC | nothing; the cable implies physical access |

Tokens are 32 bytes from `secrets.token_urlsafe`, written 0600, compared with
`hmac.compare_digest`. The mobile token is separate so the phone's permission
scopes (`read` / `refresh` / `sources` / `servers`) cannot be
sidestepped with the host token. `POST /local/stop` re-checks the live listener
table and the process owner before it signals anything.

Which routes accept which caller, and the rule new routes are classified by, is
[docs/trust.md](docs/trust.md).

## Known limits

- **Loopback is trusted without a token**, which assumes you are the only user
  of the Mac. A second logged-in account can read `/usage` and post to the
  Mac-local routes.
- **`"require_auth": false`** opens `/usage` to the whole network. It exists
  for lab setups. On café or hotel Wi-Fi it hands your working context to
  everyone on the segment.
- **No TLS.** LAN traffic is plaintext, so the token and the document are
  visible to anyone who can watch the segment. Tailscale is the answer if you
  need the phone off your own network.
- **Logs.** `~/.headroom/logs/` is owner-only and holds no tokens, but it does
  name repos and ports. Read before pasting into an issue.
- **ESP32 OTA.** `OTA_PASSWORD` sits in `config.h` in cleartext by design.
  Anyone on the network who has it can reflash the board.
- **Supply chain.** The host is stdlib-only and has no dependencies to
  compromise. The Mac and iOS apps have none either.

## Rotating a token

```bash
rm ~/.headroom/token ~/.headroom/mobile-token && launchctl kickstart -k gui/$(id -u)/com.centaur-labs.headroom
```

New ones are generated on the next start. Re-pair the phone and reflash
`HOST_TOKEN` on the board afterwards.

## Versions

Fixes land on `main` and in the next release. Older releases are not patched.
