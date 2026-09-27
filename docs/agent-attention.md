# Coding-agent attention gateway

Headroom's attention gateway turns provider callbacks into one durable feed.
The feed is provider-neutral; each adapter declares what it can safely send
back. Codex is the first adapter.

This is the **gateway** layer (events ledger + adapters). The separate
**rollup** Attention card / menu-bar pip is product policy, not Settings —
see [`attention.md`](attention.md).

## What exists now

- A SQLite attention ledger with compare-and-swap revisions, idempotency keys,
  expiry, provider resolution, and disconnect/orphan states.
- A supervised Codex App Server process using its JSONL stdio transport.
- Stable Codex command-execution and file-change approval requests normalized
  into the common event shape, including the `commandActions` and
  `networkApprovalContext` a command approval carries.
- Codex structured questions (`item/tool/requestUserInput`) answered from the
  phone, and `turn/interrupt` on both providers.
- Managed Claude Code HTTP hooks for permission requests, stop/idle attention,
  elicitation notices, prompt submission, and session end.
- The provider's actual request, field by field, on every client — see
  **Request exposure** below.
- Synchronous Claude permission answers: the hook request waits in a bounded
  host thread, and an iPhone Allow once / Deny response wakes that exact
  request with Claude's documented decision JSON.
- `GET /agents/capabilities`
- `GET /attention/events?state=open&limit=50&after_ms=…`
- `POST /attention/events/{id}/respond`
- Every event and response identifies its serving Mac with `machine_id` and
  `machine_name`; the ID is stable across host restarts and renames, while the
  name follows the Mac's current computer name. The fields are additive, so an
  older host can still be read by a newer phone.
- A separate `agents` mobile permission. It is off by default, even when the
  ordinary iPhone dashboard permissions use their defaults.
- An **Agent alerts** setting. When off, passive `agent_waiting` notices such
  as "Ready for your next instruction" stay out of the shared feed and phone
  notifications; questions, choices, and approvals remain visible.
- iPhone feed rows, local notifications, and in-app responses. Privileged
  actions marked `requires_biometric` invoke device-owner authentication.

The gateway itself is opt-in. On the Mac that runs the Headroom host, open
**Headroom Settings → Agents**, enable the Codex attention gateway,
choose the Codex executable if `codex` is not on the host's launchd path, and
click **Apply & test**. That test starts `codex app-server`, performs its
protocol handshake, and reports the live adapter version/status. It does not
yet spend tokens or create a test task.

The equivalent config, useful for headless hosts, is:

```json
{
  "agent_gateway_enabled": true,
  "codex_binary": "codex",
  "agent_alerts": true
}
```

Put those keys in `~/.headroom/config.json` and restart the host. The Mac UI
uses localhost-only `GET` and `POST /agents/config`; this endpoint is
intentionally unavailable over LAN because it controls a local executable.

Claude Code does not require Headroom to launch its sessions. In
**Headroom Settings → Agents**, click **Install hooks**. Headroom adds
only marked entries under `~/.claude/settings.json`, preserves every foreign
hook, writes atomically, and keeps `settings.json.bak-headroom`. **Remove
hooks** removes only Headroom-owned entries.

The installed `PermissionRequest` hook posts the complete tool request to the
localhost host and can return Allow or Deny. Lifecycle hooks post finished,
idle, and elicitation attention. A prompt submission or session end resolves
the corresponding passive rows. If the host is unavailable or the permission
wait expires, the hook supplies no decision and Claude falls back to its normal
local permission dialog.

## Answering a question without an answer API

No Claude Code hook can hand `AskUserQuestion` a selected option. All 30 hook
events were checked: `PreToolUse` can block a call, `PermissionRequest` can
allow or deny it, `PostToolUse` fires too late to change anything. The
`Elicitation` hook returns form values, but only for MCP elicitation — it never
fires for `AskUserQuestion`.

What a hook *can* do is block the call and give a reason. The docs are explicit
that a denied `PreToolUse` **"blocks the tool call, and shows Claude the
reason"**. That reason is the answer channel: tapping an option on the phone
denies the call with the chosen label as `permissionDecisionReason`, and Claude
reads the choice and continues.

This is a workaround and the code says so. Claude sees a blocked tool plus
your words, not a clean tool result, so it may acknowledge the block or ask
again — which is why `structured_question` reports `experimental` rather than
`stable`. Four rules keep it honest:

- **One question, two to six options, no `multiSelect`.** Anything else
  returns `defer` and still appears on the phone as a read-only notice (with
  the options listed), so you answer it on the Mac. A half-answered set is
  worse than a question that simply arrives on the Mac.
- **`defer` is the default.** Timeout, an unmapped action, or **Ask on Mac**
  all defer, which is the documented way to say "no opinion" and hands the
  question back untouched.
- **The hook is scoped `"matcher": "AskUserQuestion"`, and it is off by
  default.** `PreToolUse` is the only hook Headroom installs that can block a
  tool call, and holding a question is not free: it is unanswerable at the Mac
  for exactly as long as Headroom holds it, and a host that is down or
  restarting takes every question in every session with it. That is a bad
  trade unless you actually want to answer from your phone, so the blocking
  hook is installed only in **Let iPhone answer** mode. **Show on Mac + iPhone**
  mirrors a question without holding Claude, and **Mac only** removes the
  question hook rather than leaving a blocker behind.
- **The wait is bounded.** A question parks for 120 seconds, not the approval's
  285. An approval has nowhere else to be answered; a question is sitting in
  front of you on the Mac the whole time.
- **No decision is an empty body.** Returning an explicit `permissionDecision`
  is only as safe as our reading of the enum, and a wrong value there does not
  degrade — it breaks the tool call.
- **No duplicate row.** A denied `PreToolUse` stops the chain, so
  `PermissionRequest` never fires for a question that was answered. When the
  phone stays quiet, the deferral lets the ordinary permission flow resume.

The reason text names the phone explicitly. A bare "denied" reads to the model
as a refusal; it has to say an answer was given and where it came from.

## Testing the iPhone handoff

Run this short matrix after installing hooks and granting the Mac's **Answer
coding agents** permission to the iPhone:

1. **Passive notice:** click **Add a test row**. It appears in Attention with
   the repository name, has no tab badge, and can be dismissed individually or
   with **Dismiss all**. The Mac's own row remains visible.
2. **Permission:** trigger a Claude permission request. Attention shows the
   repository, request details, and Allow/Deny/Reply actions. Tapping one
   resolves the same request in Claude; it does not create a second computer
   notification.
3. **Question:** set **Claude questions → Let iPhone answer**, reinstall hooks
   if needed, and trigger a two-option `AskUserQuestion`. The iPhone shows the
   prompt and choices without a duplicate raw Request block. Choosing an option
   wakes Claude; the Mac-side question remains visible in its own terminal.
4. **Mac-only fallback:** set **Claude questions → Mac only**. A question no
   longer waits on Headroom and is answered in the terminal as usual.
5. **Routing:** tap an iPhone notification. It opens Attention and highlights
   the matching repository row, including when several Macs are connected.

## Starting a Codex session Headroom can answer

**Today you cannot, and the ledger proves it.** After months of the adapter
reporting `connection: ready`, this Mac's `~/.headroom/attention.sqlite3` held
112 `claude-code` rows and **zero** `codex` rows. Not one Codex approval has
ever been raised.

The reason is structural, not a bug. Headroom spawns its own private child:

```
codex app-server --listen stdio://
```

That child talks to Headroom over a pipe and nothing else. A `codex` session
you start in a terminal is a different process on a different transport, and
it has no way to reach Headroom's App Server. Approvals go to your terminal
because that is the only client its own App Server has. Headroom never calls
`thread/start`, so its App Server has no threads either — the handshake
succeeds, the version is reported, and nothing ever happens.

So the adapter today is a working protocol client with nothing plugged into it.
Everything below it — the ledger, the typed fields, questions, interrupt — is
tested against synthetic messages and is correct; it simply has no live source.

**Slice 2 is now built, and it works.** Headroom starts the thread itself:

This is a Mac-local capability. The iPhone deliberately has no task launcher:
it can answer a request raised by an existing session, but it cannot create a
session, send an arbitrary prompt, or steer a live turn.

```bash
curl -s -X POST localhost:8737/agents/codex/tasks \
  -H 'Content-Type: application/json' \
  -d '{"cwd":"/path/to/repo","prompt":"fix the flaky test"}'
```

`thread/start` runs with `approvalPolicy: "on-request"` and
`sandbox: "workspace-write"`, which is what makes Codex ask rather than
proceed silently; `approvalsReviewer` defaults to `user`, and the client is
Headroom. `turn/start` sends the prompt. `POST /agents/codex/steer` adds to a
running turn through `turn/steer`, gated on `expectedTurnId` so words meant for
one turn never land in the next. `GET /agents/codex/task` reports what is live.
All three are localhost-only: they drive a local executable.

The limitation is unchanged and worth restating — **only work Headroom started
is visible.** A session you start in a terminal still talks to its own App
Server. That is an OpenAI-side restriction, not a design choice, and it is
why the shared-daemon route below is the one to revisit.

Two ways this was approached:

1. **Headroom drives the session** (slice 2). `thread/start`, `turn/start`,
   and the approvals arrive on the socket Headroom already owns. This works
   with today's transport and needs no new Codex feature — but Headroom
   becomes the place you start Codex work, which is a product decision, not a
   plumbing one.
2. **Share one App Server** (slice 1). This is the shape you want — you keep
   starting sessions the way you do now and Headroom watches — but as of Codex
   0.145.0 it does not work, and this was established by trying it rather than
   by reading:

   - `codex app-server daemon start` runs and creates
     `~/.codex/app-server-control/app-server-control.sock`.
   - `daemon enable-remote-control` reports `remoteControlEnabled: true`.
   - A second client that connects to that socket and sends the documented
     `initialize` gets **zero bytes back and the socket closed**.
   - Going through `codex app-server proxy`, which exists to relay stdio to
     that socket, produces no response either.

   The name is the clue: it is a *control* socket, and `daemon version` speaks
   a management protocol over it. Whatever negotiation `proxy` is meant to
   perform is undocumented, and both subcommands are `[experimental]`. Until
   that settles, a second client cannot attach.

   The blocker is transport, not design. When the daemon does answer, the rest
   of the adapter is ready: the ledger, typed fields, questions and interrupt
   are all provider-neutral and already tested.

Until one of those lands, **Settings → Agents should not imply the Codex
gateway is doing anything.** It connects, and that is all it does.

## Deliberate boundary

This Codex App Server instance currently uses Codex's stdio transport and owns
**Headroom-launched sessions**. It cannot attach itself to an unrelated Codex
Desktop, CLI, or IDE process and intercept that process's callbacks. In
particular, an already-running conversation cannot be transparently moved
between App Server processes while a turn is live.

Codex now ships `codex app-server daemon` and `codex app-server proxy`, both
`[experimental]`. A shared daemon is the right next transport for Headroom: one Headroom-supervised App Server, with Headroom and
future CLI clients subscribing to its tasks. Stored inactive tasks may be
resumed by task ID, but live ownership and approval routing must remain with
the App Server that received the request.

The iPhone currently schedules a local notification when a foreground or
background refresh sees a new event. Guaranteed immediate delivery while the
app is suspended requires APNs registration plus a push provider on the
server; polling alone cannot provide that guarantee.

## Request exposure

An approval you cannot read is not an approval. Adapters no longer paraphrase
the provider's request into one line; they hand the raw request object to
`host/agent_request.py`, which returns an ordered list of typed fields under
`detail.request`:

```json
{
  "key": "old_string",
  "label": "Replacing",
  "kind": "code",
  "value": "const port = 3000",
  "truncated": false,
  "full_chars": 17
}
```

`key` is the provider's own name and `label` is for humans. `kind` is one of
`text`, `command`, `code`, `path`, `json`, `number`, `bool`; a client that meets
an unknown kind draws it as text, so a tool nobody has seen renders without an
app update. Known tools keep the order a person reads them in
(`Edit` is file, before, after); everything else follows sorted, which is what
makes an arbitrary MCP tool legible rather than a special case.

Three rules hold the contract together:

- **Bounds live in the host, not the client.** One `Write` of a large file
  would otherwise land in the SQLite ledger, every `/attention/events`
  response, and every phone that polls it. Per-field, per-request and total
  caps are in `agent_request`; `agent_events.MAX_DETAIL` is the backstop that
  catches an adapter bug rather than trusting one.
- **Clipping is never silent.** `truncated` plus `full_chars` reach the client,
  which says so on screen. Approving a prefix of a command while believing it
  is the whole command is the failure this design exists to prevent.
- **`detail.reasons` is why the provider is asking.** Claude sends
  `permission_reasons`; older builds sent `permission_suggestions`. The adapter
  reads both, because losing the explanation costs the user the only context
  they get.

The raw provider request is deliberately **not** stored. The typed fields carry
both key and value, so nothing downstream needs it — and `updatedInput` merges
per field, so answering never needs the original either.

## Provider capability metadata

Clients must render only actions the provider adapter reports as supported.
`GET /agents/capabilities` returns one record per adapter, including:

```json
{
  "provider": "codex",
  "adapter": "codex-app-server",
  "session_ownership": "headroom_launched",
  "enabled": true,
  "available": true,
  "connection": "ready",
  "capabilities": {
    "command_approval": {"supported": true, "maturity": "stable"},
    "file_approval": {"supported": true, "maturity": "stable"},
    "structured_question": {"supported": false, "maturity": "experimental"},
    "send_message": {"supported": false, "maturity": "planned"},
    "interrupt": {"supported": false, "maturity": "planned"}
  }
}
```

Event actions also carry per-action policy metadata:

- `risk`: `safe`, `privileged`, or `destructive`
- `requires_foreground`
- `requires_biometric`

The server remains authoritative. A stale event revision, reused idempotency
key with a different action, provider-side resolution, or disconnected adapter
returns a conflict instead of sending a second answer.

## Common event lifecycle

```text
provider request
  -> pending
  -> responding
  -> resolved

pending/responding
  -> declined | cancelled | expired | orphaned
```

The SQLite row is durable, but a live JSON-RPC callback is not. If the Codex
process disconnects, open events become `orphaned`; Headroom never guesses that
a request ID from a previous process can still be answered.

## Reading the Codex protocol rather than guessing at it

`codex app-server generate-json-schema --out DIR` writes the whole protocol —
234 schemas for v2. Every capability question below was settled from that
bundle, and it is the right first move for anything else Codex-shaped:

| Wire method | What it gives Headroom |
|---|---|
| `item/commandExecution/requestApproval` | `commandActions`, `networkApprovalContext`, proposed policy amendments |
| `item/tool/requestUserInput` | structured questions — `header`, `id`, options with `label` + `description`, plus `isSecret` |
| `item/permissions/requestApproval` | the persistent grant that would back `permission_grant` |
| `turn/interrupt` | stop a turn, keyed by `threadId` + `turnId` |
| `turn/steer` | send a message into a *live* turn, gated on `expectedTurnId` |
| `thread/start`, `thread/resume` | Headroom-owned task creation and resume |

Codex's question shape is close enough to Claude's `AskUserQuestion` that both
go through the same choice actions and render with one control on the phone.
`isSecret` is the exception that matters: a question marked secret is never
offered remotely and never enters the ledger.

## Next slices

1. Replace the private stdio child with a shared App Server. Codex now ships
   `codex app-server daemon` and `codex app-server proxy` (proxies stdio to a
   running control socket), which is a cleaner route than the `--remote
   unix://PATH` this list originally assumed. Both are `[experimental]`. The
   hard part is unchanged and is not transport: live ownership of an approval
   must stay with the process that received it.
2. Add Headroom-owned Codex task creation, resume/subscription, turn start,
   message, and interrupt routes, storing provider task IDs beside events.
3. Add an explicit end-to-end test task that requests a harmless approval,
   appears on iPhone, and verifies the selected answer reaches Codex.
4. **APNs push.** Polling cannot wake a suspended app, so an approval that
   arrives while your phone is in your pocket waits until you open Headroom.
   What this needs, in order:

   | Step | What it is | Who can do it |
   |---|---|---|
   | APNs auth key | A `.p8` from the Apple Developer portal (Keys → Apple Push Notifications service). Gives a key ID and the team ID | You — it is an account credential |
   | Capability | Push Notifications on the iOS app id, and `aps-environment` in the entitlements | You, then the profile has to be reissued |
   | Device registry | `POST /agents/devices` storing token, environment and machine, keyed so a reinstall replaces rather than duplicates | Buildable now |
   | Push worker | Host signs a JWT with the `.p8` (ES256, refreshed hourly) and posts to `api.push.apple.com` over HTTP/2 | Buildable now, but stdlib has no HTTP/2 — either a dependency or `curl --http2` |
   | Sandbox vs production | TestFlight and App Store builds use different APNs hosts. A token from one is rejected by the other | Config, and a trap worth writing down |

   The `.p8` is a signing key for your whole team and cannot be re-downloaded.
   It belongs in Keychain or a wrangler secret, never in the repo — the same
   rule the host token already follows.

   Payload policy is already decided and should not drift: an opaque event id
   and revision, never credentials, never command text. The phone fetches the
   real row over the existing authenticated endpoint. A notification is a
   doorbell, not a delivery.

   Note the stdlib constraint is real: `host/` is deliberately dependency-free,
   and APNs requires HTTP/2. Shelling out to `curl --http2` keeps that promise;
   adding `httpx` or `hyper` breaks it. That is a decision to make on purpose
   rather than discover.
5. **Actionable notifications.** Once 4 lands, a category with buttons lets a
   safe answer be given from the lock screen. The rule already enforced in the
   ledger carries over unchanged: `requires_foreground` and
   `requires_biometric` actions — every approval, every grant, both
   interrupts — must open the app. In practice that leaves **Dismiss** and a
   question's options as the only lock-screen answers, which is the right
   scope: a notification that can approve `rm -rf` from a pocket is a bug.
6. ~~Add structured questions~~ **Done.** `item/tool/requestUserInput` is
   wired to the same choice actions Claude uses. It stays `experimental` in
   Headroom's capability metadata because Codex marks the request EXPERIMENTAL
   in its own schema — the maturity mirrors the provider's, not our confidence
   in the wiring.
7. **Answer Claude's questions and rules.** Verified against Claude Code
   2.1.220, so this is a wiring gap rather than a provider gap:

   | Answer | How | Headroom today |
   |---|---|---|
   | Always allow | `decision.updatedPermissions: [{rule, mode}]` | `permission_grant` still reports `planned` |
   | Form values | `Elicitation` hook returns `action: "accept"` + `content` | neither `Elicitation` nor `ElicitationResult` is installed |
   | Edit before allowing | `decision.updatedInput` (merges per field) | not modelled |
   | Interrupt | `decision.interrupt` | Codex has **Stop task**; Claude has no equivalent |

   The `Elicitation` hook receives `schema` plus `questions[]` (`field`,
   `question`, `type`, `options`, `default`) — the real question, not the
   `Notification` shadow with `notification_type: elicitation_dialog` that
   Headroom listens to now. That shadow carries no schema and accepts no
   answer, which is why questions currently read as notify-only.

   Two transport changes gate all four: `AgentAttentionResponseRequest` carries
   only `{revision, action, idempotency_key}` and needs a payload for form
   `content`, a chosen rule, or an edited input; and the answer path has to stay
   revision-safe, since a rule that outlives the request is a durable grant made
   from a phone.

   `claude_hooks.EVENTS` also omits the documented `agent_needs_input`,
   `agent_completed`, `elicitation_complete` and `elicitation_response`
   notification types.
8. Implement a Cursor adapter against the same ledger. An adapter may be
   display-only; unsupported response capabilities remain visible in metadata
   rather than being inferred by the client.
9. Project the same open feed onto ESP32. The board is a strong ambient signal;
   sending approvals from a two-button device should be limited to actions
   explicitly marked safe.

## What Claude Monitor teaches us

[Claude Monitor](https://github.com/cliq/claude-monitor) is a strong reference
for provider installation and attention-state UX, even though it does not send
answers back to Claude:

- Detect each provider installation and show **Install**, **Installed**,
  **Outdated**, or **Modified externally**, rather than hiding setup in a
  config file.
- Own only clearly marked config entries, write settings atomically, and keep a
  backup. Its Claude hook commands carry managed-by/version markers because
  another JSON serializer may discard unknown metadata.
- Make event hooks fail open. Monitoring must never block the coding agent.
- Normalize noisy provider events into a small state machine such as working,
  background work, waiting, needs attention, finished, and stale.
- Retain provider/session context such as task ID, working directory, PID, and
  terminal so the app can offer **Open on Mac** when remote answering is not
  supported.
- Keep notification policy separate from transport, with an obvious master
  toggle and a real test action.

Headroom should copy those lifecycle and resilience patterns. Its differentiator
is the durable event ledger plus capability-checked, revision-safe response
path; provider adapters must never pretend they can answer when they can only
notify or focus the original terminal.
