# Host reference

Config files under `~/.headroom/`, HTTP endpoints, and how the menu bar notices
a stale embedded host. For first-run setup see [setup.md](setup.md).

## Personal config (`~/.headroom/config.json`)

| Key | Purpose |
|---|---|
| `timezone` | Local day boundary for burn + timestamps |
| `dev_root` | Where git / GitHub repo discovery walks |
| `git_authors` | `git log --author` patterns (empty = all authors) |
| `vercel_team_slugs` | Preferred Vercel team(s); empty → CLI current team |
| `github_org_prefix` | Owner filter for discovered Actions repos — one `"owner/"` or a list; empty = every repo found |
| `github_always_repos` | Always-watched `owner/name` list |
| `github_max_discovered` | Cap on auto-discovered repos |
| `plausible_sites` | Optional domain filter / fallback when the key cannot list sites |
| `plausible_host` | Cloud or self-hosted base URL (default `https://plausible.io`) |
| `plausible_range` | Primary window: `day`, `24h` (default), `7d`, or `30d` |
| `posthog_projects` | Optional project-id filter / fallback when the key cannot list projects |
| `posthog_host` | US / EU / self-hosted base URL (default `https://us.posthog.com`) |
| `posthog_range` | Primary window: `day`, `24h` (default), `7d`, or `30d` |
| `sentry_org` | Sentry organization slug (token is Keychain-only) |
| `datadog_site` | Datadog site host, e.g. `datadoghq.com` / `datadoghq.eu` |
| `axiom_host` | Axiom API base (default `https://api.axiom.co`; EU `https://api.eu.axiom.co`) |
| `axiom_org_id` | Optional org id header for Axiom PATs |
| `gemini_oauth_client_id` / `_secret` | Only if Gemini refreshes fail: public client constants the host normally reads from the installed `gemini` CLI |
| `auth_token` | Override the generated **host token** |
| `require_auth` | `false` opens `/usage` to the whole network (default `true`) |
| `mobile_permissions` | iOS grants: `read`, `refresh`, `sources`, `servers` |

## Source preferences (`~/.headroom/sources.json`)

Seeded from local detection on first run, then written by Settings. Three keys:

- `enabled` — `{id: bool}`
- `order` — pinned provider ids; the first three enabled become `focus`
- `integrations_order` — pinned Integrations catalog ids (code and deploys,
  balances, services, local servers/builds). Activity lays out the subset that
  paints blocks in this order, including OpenRouter / AI Gateway account-use
  panels. Claude Code / Codex live under Settings →
  Agents, not this list. Legacy `services_order` is still read once as a
  migrate seed.
- `accents` — `{id: "#RRGGBB"}` for rows you recolored; delete an entry to
  restore the shipped colour

## Extra accounts (`~/.headroom/accounts.json`)

Written by **Settings → Providers → Add account**; editable by hand and picked
up on the next host start. Each entry is a label plus a credential location —
never a token.

```json
{
  "claude": [{ "slug": "work", "label": "Work", "root": "~/.claude-work" }],
  "codex":  [{ "slug": "alt",  "label": "Alt",  "root": "~/.codex-alt" }]
}
```

`root` is the config folder for Claude / Codex / Gemini and the `state.vscdb`
itself for Cursor / Windsurf. The row shows up as `claude:work` everywhere a
source id does — `sources[]`, `providers[]`, `focus`, burndown, samples.

## Endpoints

Everything below is loopback-open and token-gated off-box. Trust classes:
[trust.md](trust.md). Contract rules for `/usage`: [contract.md](contract.md).

| Path | Notes |
|---|---|
| `GET /usage` | Full flat JSON for the menu bar (~40KB) |
| `GET /usage?view=device` | ~5KB projection the ESP32 polls |
| `GET /health` | Host `version` + `build`, uptime, compact source status, cache age, last board check-in |
| `GET /setup` | Detected credentials + enabled map + order / focus / accents (first-run sheet) |
| `GET /github/watch` | Actions watch settings + resolved repos (loopback) |
| `POST /github/watch` | Replace those watch settings (loopback) |
| `GET /accounts` | Extra logins per provider (loopback) |
| `POST /accounts` | Add or drop an extra login, then restart (loopback) |
| `GET /mobile/permissions` | Four effective permissions for the paired iOS app |
| `POST /sync/refresh` | Force-refresh (LAN OK — ESP32 long-press) |
| `POST /sources` | Toggle sources, pin order, and/or recolor rows. Loopback or paired iOS with `sources` scope |
| `POST /supabase/refresh` | Force Supabase poll (loopback) |
| `POST /plausible/refresh` | Force Plausible poll (loopback) |
| `POST /posthog/refresh` | Force PostHog poll (loopback) |
| `POST /local/stop` | Stop server (loopback or paired iOS with `servers` scope) |
| `POST /attention/ack` | Clear the current warning everywhere until its reasons change |
| `POST /mobile/permissions` | Replace iOS grants (loopback / Mac settings only) |

The served document is rebuilt once per poll tick and cached as bytes.
`attention.level` is `ok` | `warn` | `critical` — acknowledgement is stored by
fingerprint so clearing on one surface clears the same warning everywhere.

## ESP32 USB fallback

Wi-Fi is the normal board transport. To use the ESP32 over its USB CDC serial
connection, open **Headroom → Settings → Host** and enable **Use USB
fallback for the ESP32**. Headroom restarts the current host supervisor with
`HEADROOM_ENABLE_USB=1`, then reports the detected and active `/dev/cu.*` device
in the same section.

USB is deliberately opt-in because the bridge reserves the serial port. Turn
it off before flashing firmware or opening a serial monitor; either operation
needs exclusive access to the same device.

## Who owns the host process

Two modes, same `headroom_server.py` on the same port. **Settings → Host →
Keep the host running when Headroom is closed.**

| Mode | Owner | Quitting Headroom | Default |
|---|---|---|---|
| On | launchd, via `~/Library/LaunchAgents/com.centaur-labs.headroom.plist` | host keeps serving | yes |
| Off | `Headroom.app`, as a child process | host stops, and so do the board, iPhone and Watch | no |

Switching modes stops the outgoing owner, waits for :8737 to go quiet, then
starts the incoming one. Both directions need that wait: the incoming host
binds the port on start and exits 0 rather than fighting for it, and under
`KeepAlive: SuccessfulExit false` launchd does not bring the loser back.

In app-owned mode the app passes `--exit-with-pid <its pid>` and the host
watches it (`host/parent_watch.py`). Two things stop the child, and both are
needed: `applicationWillTerminate` sends SIGTERM, and the pid watch catches a
crash or a force quit, where no handler runs at all. Without the watch the
child is reparented to launchd and holds :8737, so the next launch finds a
foreign host it cannot stop from the UI.

`kill` works in app-owned mode and is meant to. The app restarts a child that
dies with a non-zero status, up to four times with a widening delay, and never
restarts a clean exit. A clean exit is either someone stopping it on purpose or
the host standing down from a port something else owns, and restarting into
either is the crash loop 1.9.3 shipped.

Logs go to `~/.headroom/logs/headroom.log` and `.err` in both modes.

### Leaving cleanly

**Settings → Host → Remove background service.** It stops the host,
boots out and deletes the plist for both the current and the legacy label, and
quits Headroom. It appears only while a plist exists.

Quitting is part of the action rather than advice afterwards. The app installs
the LaunchAgent whenever it finds no host running, so a removal that left the
app open would be undone by its own poll loop.

Use it before deleting `Headroom.app`. The plist names a script inside the
bundle:

```
/usr/bin/python3 /Applications/Headroom.app/Contents/Resources/host/headroom_server.py
```

Delete the app without removing the service and that job outlives it. launchd
keeps trying to exec a path that is gone, and `KeepAlive` is
`SuccessfulExit: false`, so a failed exec respawns every `ThrottleInterval`.
Nothing is left running to clean it up. `scripts/uninstall-host.sh` fixes it
from a clone; without one, boot out the label and delete both plists by hand.

## Host version

launchd keeps whatever host it was given running across app updates, so the
menu bar can end up reading a build it never shipped. `/health` reports
`version` (`host/VERSION`) and `build` (fingerprint of shipped `.py` files).
The app offers **Update host** in the popover when the two disagree.

macOS / iOS marketing versions track `host/VERSION`; `CFBundleVersion` is the
git commit count. Cut releases with `./scripts/cut-release.sh` — see
[releasing.md](releasing.md).
