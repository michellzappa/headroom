# Attention

Attention is two products that share a word. Do not add a Settings pane for
either one's scoring knobs.

| Layer | What | Where it lives |
|-------|------|----------------|
| **Rollup** | Glance pip + Attention card (`ok` / `warn` / `critical`) | `_build_attention` in `host/headroom_server.py` (queued for `host/attention.py` in [`backlog.md`](backlog.md)) |
| **Gateway** | Coding-agent approval / idle events | Ledger + adapters — [`agent-attention.md`](agent-attention.md) |

## Rollup scoring is product policy

Weights, ages, critical steps, and which kinds fire are intentional constants —
same posture as rings ([`rings.md`](rings.md)): one shared glance, not per-user
dials. Exposing them as Settings would teach people to mute the light until it
means nothing.

Policy that already lives next to the code (do not turn these into prefs):

- Quota % never pages Attention — rings own that reading.
- Supabase lints: ERROR only; WARN/INFO stay in the app without lighting the pip.
- Sentry: unresolved issues with `lastSeen` inside 24h only — aged debt stays out of the pip.
- Datadog: monitors in Alert (critical/warn by count); Warn-only is a quieter reason. Not APM or host maps.
- Axiom: open monitor alerts only — not ingest volume.
- Stale quotas alert after `STALE_ALERT_S`, not on every timeout.
- GitHub Actions failures age out; Codex spend/time events are the only quota
  path into Attention.
- GitHub inbox on watched repos is review requests, assignments, and
  @mentions (warn). Mentions stay scoped to the watch list — not every
  @you on GitHub — so the pip stays useful for CI and review oversight.
- Inbox rows age out at `ATTENTION_INBOX_MAX_AGE_S` (14 days, untouched),
  the same posture as Actions failures on their 24h clock. An assignment
  nobody has answered in two weeks is debt: it stays in Activity, and the
  host says so per row with `needs_attention` because the status word
  cannot ([`contract.md`](contract.md)). Before the gate these rows came
  back on every relaunch — dismissal is per-run memory, so an item that
  never leaves the search never leaves the queue.

**Dismiss all** clears the queue on this surface and acks the current
fingerprint until reasons change. Ack state (`attention_ack_fingerprint` in
local config) is not a preference, and it does not sync across Macs. Local
row dismissals (iPhone swipe, Mac **Dismiss all**) are per-surface — the ack
is what turns the menu-bar pip off everywhere on this host.

## What is already configurable

| Knob | Surface |
|------|---------|
| Codex attention gateway on/off + binary | Mac Settings → Agents |
| Claude hooks install / test | Mac Settings → Agents |
| Answer coding agents | Mac Settings → iPhone (`agents` permission, default off) |
| Attention notifications | iOS Settings → iPhone (`@AppStorage`) |
| Disable a source | Sources — stops that source's stale/derived reasons |

No Settings destination named Attention. No user-editable weights, ages, or
severity thresholds. Ack fingerprint and the event ledger stay off
`SHARED_CONFIG_KEYS` / CloudKit.

## Future escape hatch only

If a specific kind becomes chronic noise in real use (stuck amber forever), add
**mute-by-kind**: a local boolean map in `config.json`, same locality as
`agent_gateway_enabled`, gated inside the scorer. Place the toggle next to the
source that feeds it (Agents or Integrations) — still not a scoring UI.

Do not invent threshold pickers, weight sliders, or a synced attention-prefs
blob. Revisit only when mute-by-kind is clearly needed.

## Phone offline

Rollup reasons and activity failures ride inside the phone's archived `/usage`
payload (`MobileSnapshotArchive`). Coding-agent events are a separate poll, so
they have their own on-device cache (`MobileAgentAttentionArchive`). Both draw
as **Recent history** when the Mac is unreachable; answering still needs a live
host.

## Structural extract

Moving `_build_attention` into `host/attention.py` is a file split
([`backlog.md`](backlog.md)), not a settings project. It keeps weights in one
place so a later mute filter has a single gate.
