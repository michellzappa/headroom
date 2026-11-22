# Anonymous product diagnostics

Headroom is local-first. The Mac sends no diagnostics unless the user leaves
**Share anonymous product diagnostics** enabled in Settings → Telemetry.

The setting is on by default so we can understand which releases and provider
integrations are actually in use. It can be turned off at any time. Turning it
off deletes any pending local batch. The local install secret is kept so
toggling the setting cannot mint a fresh weekly identity.

## What is sent

At most one aggregate batch per calendar week:

- app version, app build, bundled host version, macOS major version, and CPU
  family (`arm64` / `x86_64`);
- normalized provider ids that are enabled, used, or healthy;
- rounded model-family shares from the local usage summary;
- two coarse feature flags: phone pairing and multi-Mac (the intake still
  accepts the old `agent_gateway_enabled` flag from older app versions);
- a **week-scoped dedupe key** (`HMAC-SHA256(install_secret, ISO week)` as
  64 hex chars) so the intake can ignore duplicate reports from the same Mac
  in the same week;
- a **cohort word** — `new`, `returning`, or `reactivated`.

Model names are mapped locally to families such as `sonnet`, `opus`, `gpt`,
`codex`, and `other`. The raw model string never leaves the Mac.

## What is not sent

Headroom never sends prompts, commands, code, file paths, repository names,
branches, commit messages, emails, account ids, tokens, exact spend, raw model
names, or per-request activity through this system.

There is no stable install id, hardware id, serial number, Secure Enclave key,
or App Attest identity. The install secret stays on the Mac (Keychain +
`~/.headroom/telemetry/install_secret`). Only the week-scoped HMAC leaves, and
it changes every ISO week, so the server cannot stitch a long-lived install
history. The random `batch_id` only deduplicates network retries of the same
payload.

The background Python host does not send telemetry. The Mac app owns consent,
builds the batch, and submits it over HTTPS. A failed telemetry request is
ignored and never affects Headroom's dashboard or host.

## Deduping weekly active Macs

The Worker stores at most one row per `(period, dedupe_key)`. A second batch
from the same Mac in the same week — Debug rebuild, Release copy, or a retried
POST with a new `batch_id` — is accepted as `ok` but does not increment the
rollup. Turning diagnostics off and on does not rotate the secret. Deleting
both Keychain and `~/.headroom/telemetry/install_secret` would, which is the
same class of local wipe as reinstalling.

The metric remains **weekly active Macs**, not users: two Macs are two
reports, by design.

## Retention, without an install id

The dedupe key rotates every week on purpose, so the server cannot tell that
this week's Mac is last week's Mac. That also removed any way to see whether
anyone came back, which is the one number that says an app is used rather than
merely downloaded.

The Mac answers the question itself. It already stores the last period it
submitted, so it can compare that to the week it is reporting now and send one
word:

| Cohort | Meaning |
|---|---|
| `new` | no period stored — first report from this Mac |
| `returning` | the stored period is the week immediately before |
| `reactivated` | reported before, but not last week |

The word carries no identity and cannot be joined across weeks. The intake
learns that *some* Mac came back, never which one.

Two limits worth stating. A Mac that had diagnostics off and turns them on
reports `new`, because locally that is all it knows. And a Mac that never
launches in a given week is silent, so `reactivated` counts a return after a
gap rather than a fixed-length absence.

## Weekly counts are kept; batches are not

Two retention windows, deliberately different:

- **Raw batches and per-week breakdowns** (`telemetry_batches`,
  `telemetry_dimensions`) expire after **180 days**. Six months lets a
  breakdown be read against the same week two quarters back. What is kept is
  already a whitelisted aggregate: no IP, no install id, and a dedupe key that
  is useless outside its own week.
- **Weekly counts** (`telemetry_periods`) are kept indefinitely. A row is a
  period, a count of Macs, and the three cohort counts. No column describes a
  Mac, so keeping it costs no privacy, and it is the only way a growth chart
  can show more than the raw window.

Deleting `telemetry_periods` on the batch schedule was the earlier behaviour,
and with a 30-day window it capped the weekly chart at about five bars,
forever.

## Implementation

Schema **2**. The client contract lives in
`macos/Sources/Telemetry.swift`. The first-party ingestion Worker and D1
schema live under `telemetry/`. The Worker validates and whitelists the
payload before storing it, does not persist request headers or IP addresses,
and retains raw aggregate batches for 180 days (see “Weekly counts are kept”
below).

At ingest the Worker reads Cloudflare’s edge `request.cf.country` (ISO-3166
alpha-2) and stores that code on the batch row. The IP itself is never written
to D1, logs, or the published payload. Unknown / Tor (`XX`, `T1`) stay null.

Existing D1 databases need migrations once:

```bash
npx wrangler d1 execute headroom-telemetry --remote \
  --file=telemetry/migrations/002_dedupe_key.sql
npx wrangler d1 execute headroom-telemetry --remote \
  --file=telemetry/migrations/003_country.sql
npx wrangler d1 execute headroom-telemetry --remote \
  --file=telemetry/migrations/004_growth_cohorts.sql
```

**Run a migration before deploying the Worker that needs it, never after.**
`communityStats` selects the cohort columns by name, so a Worker deployed
against an un-migrated D1 throws on every `/v1/community` request and the
Community Pulse goes blank on the page, in Settings, and on the board. The
reverse order is safe: an older Worker ignores columns it does not select.

**`wrangler d1 execute --remote` asks before it writes, and a piped run
answers no.** Send the output through a pipe — `| tail`, `| grep` — and the
confirmation prompt goes with it, so the command exits looking successful
while the schema is untouched. Pass `--yes`, and check the columns rather
than the exit code:

```bash
npx wrangler d1 execute headroom-telemetry --remote --json \
  --command="SELECT name FROM pragma_table_info('telemetry_periods');"
```

## Community Pulse

The public Community Pulse is a read-only aggregate view at the Worker’s
`/community` route. It publishes:

- weekly active Macs from the first reporting week to now (empty and
  sub-threshold weeks stay on the axis as withheld; weeks before the first
  report are trimmed rather than drawn as zeroes);
- the new / returning / reactivated split per week;
- build spread / version distribution against the current update-feed release
  (`X.Y.Z` only, capped at that release — junk and ahead-of-feed builds stay out);
- CPU architecture, macOS major version, and country mix (edge geo codes);
- service adoption in three lanes — enabled, used, and healthy;
- model-family shares and coarse feature adoption.

It never publishes raw rows. Empty groups stay withheld; any group with at
least one contributing Mac is published. The floor used to be five while the
community was still tiny; it is one now so early signal shows.

**The newest week is always partial.** A Mac reports once per ISO week, at its
first launch inside that week, so the newest count fills over seven days. The
payload marks it `in_progress: true`, on the week entry and on `latest`.
Clients must not difference it against a week that closed: doing so printed a
large negative week-over-week figure every Monday and Tuesday, and made a
Tuesday reading of a launch week look like the community had collapsed.
Everything under `latest` — version distribution, CPU, macOS, countries,
services, models, features — is week to date while that flag is true.

The page, JSON route, rollup schema, and styling are all open in `telemetry/`,
so the community can audit or self-host them.
