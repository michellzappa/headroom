#!/usr/bin/env python3
"""headroom — Claude + Codex + Cursor (+ Vercel + Git + GitHub + Local) desk host.

Parses ~/.claude/projects/**/*.jsonl (the same usage logs `ccusage` reads),
aggregates token counts and cost into rolling time windows, and serves a flat
JSON document at GET http://<mac>:8737/usage for a Waveshare ESP32-S3 to poll
(Wi-Fi), with a best-effort USB CDC fallback (HR framed protocol) in-process.

Also polls Anthropic OAuth, OpenAI Codex (wham/usage), and Cursor
(GetCurrentPeriodUsage) quotas, Vercel team deployments, local git activity,
GitHub Actions failures, and listening local servers so the desk gadget can
flip pages. Every watched service is one row in sources_config.SOURCES.

The served document is rebuilt once per poll tick and cached as bytes — a GET
is a memcpy, not a re-aggregation, because three clients poll this thing.
`?view=device` returns the trimmed projection the ESP32 reads (see
device_view.py). Non-loopback callers must present the shared token (auth.py).

Zero dependencies — Python 3 standard library only. Incremental: each file's
byte offset is remembered so a poll only reads newly-appended lines instead of
rescanning every file, and files untouched since the retention cutoff are never
opened at all.

Run:  python3 headroom_server.py [--port 8737] [--interval 15]
"""

import argparse
import concurrent.futures
import errno
import ipaddress
import json
import os
import random
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import accounts
import agent_events
import agent_gateway
import app_config
import activity_history
import claude_hooks
import auth
import burndown
import cache_util
import claude_history
import claude_status
import codex_usage
import cursor_usage
import daily_burn
import detect_sources
import device_view
import datadog_monitors
import axiom_monitors
import git_activity
import github_actions
import host_version
import icloud_sync
import local_servers
import meters
import oauth_usage
import parent_watch
import plausible_usage
import posthog_usage
import quota_samples
import sentry_alerts
import sources_config
import supabase_usage
import usb_bridge
import vercel_builds
import zed_usage

LOG_ROOT = os.path.expanduser("~/.claude/projects")
RETENTION_S = 7 * 24 * 3600  # keep events long enough for the weekly window
BOOT_T0 = time.time()
LOG_ROTATE_S = 15 * 60


def _advertise_bonjour(port):
    """Advertise the host to native clients without adding a Python package."""
    binary = "/usr/bin/dns-sd"
    if not os.path.exists(binary):
        return None
    machine = socket.gethostname().split(".", 1)[0] or "Headroom"
    try:
        process = subprocess.Popen(
            [
                binary, "-R", machine, "_headroom._tcp", "local.",
                str(port), "path=/usage",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"Bonjour: {machine}._headroom._tcp.local.", flush=True)
        return process
    except OSError as exc:
        print(f"Bonjour unavailable: {exc}", flush=True)
        return None


def _local_tz():
    try:
        return ZoneInfo(app_config.timezone_name())
    except Exception:
        return ZoneInfo("UTC")


_lock = threading.Lock()
_offsets = {}   # filepath -> bytes already consumed
# filepath -> MessageDeduper, so the several log lines Claude Code writes per
# assistant message are billed once. It has to outlive a single read: the
# blocks of one message can straddle a poll boundary. O(1) each, and dropped
# with the offset when a file is finished, rotated, or gone.
_dedupers = {}
# (minute_epoch, model) -> [input, output, cache_read, write_5m, write_1h, cost].
# Bucketing by minute bounds memory by active minutes rather than by message
# count, and makes the rollup O(active minutes) instead of O(every message).
_buckets = {}
_state = sources_config.blank_state()          # source id -> latest payload
_source_times = {sid: 0.0 for sid in sources_config.SOURCE_IDS}

# Pre-rendered response bodies, rebuilt at the end of each poll tick.
_cache_lock = threading.Lock()
_cache = {"doc": None, "usage": b"", "device": b"", "built": 0.0}


def _unix_seconds(value):
    try:
        value = float(value)
        return value / 1000.0 if value > 1e12 else value
    except (TypeError, ValueError):
        return 0.0


def _reset_activity_rows(burndowns):
    """Granted quota resets as feed rows, newest first.

    A grant is an event that happened to you at a moment you can name, which is
    what this feed is for — and it is the one event here you did not cause. The
    burndown already detected it; this only reshapes it, so the chart mark and
    the feed row can never disagree about when or how much.

    `url` is the provider's own permalink from the registry, opened on click
    and never fetched.
    """
    rows = []
    for provider, pools in (burndowns or {}).items():
        source = sources_config.BY_ID.get(provider)
        title = sources_config.title_for(provider) if source else provider.capitalize()
        note_url = source.reset_note_url if source else None
        for pool, result in (pools or {}).items():
            pool_title = next(
                (spec.title for spec in (source.pools if source else ())
                 if spec.id == pool),
                pool.capitalize(),
            )
            for event in (result.get("resets") or []):
                at = event.get("t")
                if at is None:
                    continue
                forgiven = event.get("forgiven_pct") or 0
                rows.append({
                    "id": f"reset:{provider}:{pool}:{int(at)}",
                    "kind": "reset",
                    "status": "granted",
                    "subject": f"{title} {pool_title.lower()} limits reset",
                    "repo": None,
                    # Percent, matching the burndown caption this row explains.
                    "project": f"{int(round(forgiven))}% back",
                    "branch": None,
                    "sha": None,
                    "short_sha": None,
                    "target": None,
                    "created_at": int(at),
                    "ago": git_activity.fmt_ago(at),
                    "error_message": None,
                    "url": note_url,
                    "inspector_url": None,
                })
    return rows


def _build_activity(vercel, git, supabase=None, github=None,
                    claude_status_payload=None, burndowns=None,
                    sentry=None, datadog=None, axiom=None):
    """Merge deploys, commits, Actions failures, backend alerts, and grants."""
    deployments = vercel.get("deployments") or []
    commits = git.get("commits") or []
    deployed_shas = {d.get("sha") for d in deployments if d.get("sha")}
    items = []

    # Picked before the commits loop so a commit whose workflow is already in
    # the feed doesn't also get its own row. The run carries the same subject
    # line plus how CI went, so the pair read as two events when it was one.
    github = github or {}
    runs = []
    for run in (github.get("runs") or [])[:8]:
        status = run.get("status") or "failure"
        # Same 24h gate as Attention: day-old red CI shouldn't crowd the feed.
        if status == "failure" and not github_actions._is_fresh_failure(run):
            continue
        if status not in ("failure", "running"):
            continue
        runs.append((run, status))
    run_shas = {run.get("sha") for run, _ in runs if run.get("sha")}

    for deployment in deployments:
        state = deployment.get("status") or "unknown"
        items.append({
            "id": deployment.get("id") or "|".join(filter(None, [
                deployment.get("project"),
                deployment.get("sha"),
                str(deployment.get("created_at") or ""),
            ])),
            "kind": "deployment",
            "status": state,
            "subject": (deployment.get("commit_message")
                        or deployment.get("project")
                        or "Deployment"),
            "repo": deployment.get("repo") or deployment.get("project"),
            "project": deployment.get("project"),
            "branch": deployment.get("branch"),
            "sha": deployment.get("sha"),
            "short_sha": deployment.get("short_sha"),
            "target": deployment.get("target"),
            "created_at": _unix_seconds(deployment.get("created_at")),
            "ago": deployment.get("ago"),
            "error_message": deployment.get("error_message"),
            "url": deployment.get("url"),
            "inspector_url": deployment.get("inspector_url"),
        })

    for commit in commits:
        if commit.get("sha") in deployed_shas or commit.get("sha") in run_shas:
            continue
        pushed = commit.get("pushed")
        status = "pushed" if pushed is True else (
            "local" if pushed is False else "committed")
        items.append({
            "id": commit.get("sha") or "|".join(filter(None, [
                commit.get("repo"),
                commit.get("subject"),
                str(commit.get("created_at") or ""),
            ])),
            "kind": "commit",
            "status": status,
            "subject": commit.get("subject") or "Commit",
            "repo": commit.get("repo"),
            "project": None,
            "branch": commit.get("branch"),
            "sha": commit.get("sha"),
            "short_sha": commit.get("short_sha"),
            "target": None,
            "created_at": _unix_seconds(commit.get("created_at")),
            "ago": commit.get("ago"),
            "error_message": None,
            "url": commit.get("repo_url"),
            "inspector_url": None,
        })

    for run, status in runs:
        subject = run.get("display_title") or run.get("name") or "Workflow"
        items.append({
            "id": f"github:{run.get('id')}",
            "kind": "github",
            "status": status,
            "subject": subject,
            "repo": run.get("repo"),
            "project": run.get("name"),
            "branch": run.get("branch"),
            "sha": run.get("sha"),
            "short_sha": run.get("short_sha"),
            "target": None,
            "created_at": _unix_seconds(run.get("created_at")),
            "ago": run.get("ago"),
            # Nothing to add: the row's own caption already prints the repo,
            # the workflow, and the status in words. This line used to repeat
            # all three under it.
            "error_message": None,
            "url": run.get("url"),
            "inspector_url": run.get("url"),
        })

    for item in (github.get("inbox") or [])[:8]:
        reason = item.get("reason") or "assigned"
        items.append({
            "id": f"github-inbox:{item.get('id')}",
            "kind": "github",
            "status": reason,
            # Aged rows keep their status word in the feed and stop counting
            # as Attention — the status vocabulary alone cannot say that.
            "needs_attention": item.get("needs_attention", True),
            "subject": item.get("title") or "GitHub",
            "repo": item.get("repo"),
            "project": None,
            "branch": None,
            "sha": None,
            "short_sha": None,
            "target": None,
            "author": item.get("author"),
            "number": item.get("number"),
            "created_at": _unix_seconds(item.get("created_at")),
            "ago": item.get("ago"),
            "error_message": None,
            "url": item.get("url"),
            "inspector_url": item.get("url"),
        })

    supabase = supabase or {}
    supabase_alerts = [
        project for project in (supabase.get("projects") or [])
        if not project.get("healthy")
    ][:5]
    for project in supabase_alerts:
        failed = project.get("unhealthy_services") or []
        detail = ", ".join(failed) if failed else (
            project.get("status") or project.get("health_error")
            or "health unavailable")
        items.append({
            "id": f"supabase:{project.get('ref') or project.get('name')}",
            "kind": "supabase",
            "status": "error",
            "subject": f"{project.get('name') or 'Supabase'} needs attention",
            "repo": "Supabase",
            "project": project.get("name"),
            "branch": None,
            "sha": None,
            "short_sha": None,
            "target": None,
            "created_at": supabase.get("updated_at") or time.time(),
            "ago": "now",
            "error_message": detail,
            "url": project.get("dashboard_url"),
            "inspector_url": project.get("dashboard_url"),
        })

    # One row per project rather than per lint: a schema with twenty findings
    # would otherwise bury the feed. The Supabase section carries the detail.
    lint_alerts = [
        project for project in (supabase.get("projects") or [])
        if (project.get("lint_error_count") or 0) > 0
    ][:3]
    for project in lint_alerts:
        errors = int(project["lint_error_count"])
        top = next(
            (lint for lint in (project.get("lints") or [])
             if lint.get("level") == "ERROR"),
            None,
        )
        ref = project.get("ref")
        items.append({
            "id": f"supabase-security:{ref or project.get('name')}",
            "kind": "supabase",
            "status": "error",
            "subject": (
                f"{project.get('name') or 'Supabase'} · {errors} security "
                + ("issue" if errors == 1 else "issues")
            ),
            "repo": "Supabase",
            "project": project.get("name"),
            "branch": None,
            "sha": None,
            "short_sha": None,
            "target": None,
            "created_at": supabase.get("updated_at") or time.time(),
            "ago": "now",
            "error_message": (top or {}).get("title") or "security advisor",
            "url": (
                f"https://supabase.com/dashboard/project/{ref}/advisors/security"
                if ref else project.get("dashboard_url")
            ),
            "inspector_url": project.get("dashboard_url"),
        })

    claude_status_payload = claude_status_payload or {}
    if claude_status_payload.get("alerting"):
        subject = (
            claude_status_payload.get("incident_name")
            or claude_status_payload.get("description")
            or "Claude major outage"
        )
        items.append({
            "id": "claude-status",
            "kind": "claude-status",
            "status": "error",
            "subject": subject,
            "repo": "Claude",
            "project": None,
            "branch": None,
            "sha": None,
            "short_sha": None,
            "target": None,
            "created_at": time.time(),
            "ago": "now",
            "error_message": claude_status_payload.get("description"),
            "url": claude_status_payload.get("url") or claude_status.PAGE_URL,
            "inspector_url": (
                claude_status_payload.get("url") or claude_status.PAGE_URL
            ),
        })

    sentry = sentry or {}
    for issue in (sentry.get("issues") or [])[:6]:
        if not sentry_alerts._is_fresh(issue):
            continue
        items.append({
            "id": f"sentry:{issue.get('id')}",
            "kind": "sentry",
            "status": "error",
            "subject": issue.get("title") or "Sentry issue",
            "repo": issue.get("project") or "Sentry",
            "project": issue.get("short_id") or issue.get("project"),
            "branch": None,
            "sha": None,
            "short_sha": None,
            "target": None,
            "created_at": _unix_seconds(issue.get("last_seen")),
            "ago": issue.get("ago"),
            "error_message": issue.get("level"),
            "url": issue.get("url"),
            "inspector_url": issue.get("url"),
        })

    datadog = datadog or {}
    for monitor in (datadog.get("monitors") or [])[:6]:
        items.append({
            "id": f"datadog:{monitor.get('id')}",
            "kind": "datadog",
            "status": "error",
            "subject": monitor.get("name") or "Datadog monitor",
            "repo": "Datadog",
            "project": monitor.get("overall_state"),
            "branch": None,
            "sha": None,
            "short_sha": None,
            "target": None,
            "created_at": _unix_seconds(monitor.get("created_at")),
            "ago": monitor.get("ago"),
            "error_message": monitor.get("overall_state"),
            "url": monitor.get("url"),
            "inspector_url": monitor.get("url"),
        })

    axiom = axiom or {}
    for alert in (axiom.get("alerts") or [])[:6]:
        items.append({
            "id": f"axiom:{alert.get('id')}",
            "kind": "axiom",
            "status": "error",
            "subject": alert.get("name") or "Axiom alert",
            "repo": "Axiom",
            "project": alert.get("type"),
            "branch": None,
            "sha": None,
            "short_sha": None,
            "target": None,
            "created_at": _unix_seconds(alert.get("created_at")),
            "ago": alert.get("ago"),
            "error_message": alert.get("description"),
            "url": alert.get("url"),
            "inspector_url": alert.get("url"),
        })

    items.extend(_reset_activity_rows(burndowns))

    items.sort(
        key=lambda item: (
            0 if (
                item.get("kind") == "github"
                and item.get("status") in ("failure", "running")
            ) else 1,
            -(item.get("created_at") or 0),
        )
    )
    return items[:14]


def _github_attention_summary(github):
    """Actionable one-liner for fresh Actions failures (fail_count already aged)."""
    fails = int(github.get("fail_count") or 0)
    if fails <= 0:
        return None
    fresh = [
        row for row in (github.get("runs") or [])
        if github_actions._is_fresh_failure(row)
    ]
    # One cluster → name it. Several → count, with the newest as a hint.
    if fails == 1 and fresh:
        row = fresh[0]
        repo = (row.get("repo") or "").rsplit("/", 1)[-1]
        workflow = row.get("name") or row.get("display_title") or "workflow"
        if repo:
            return f"{repo} · {workflow} failed"
        return f"{workflow} failed"
    if fresh:
        row = fresh[0]
        repo = (row.get("repo") or "").rsplit("/", 1)[-1]
        workflow = row.get("name") or row.get("display_title")
        if repo and workflow:
            return f"{fails} GitHub Actions failures · {repo} {workflow}"
    return f"{fails} GitHub Actions failure" + ("" if fails == 1 else "s")


def _event_from(rec, cutoff):
    """Turn one assistant log record into a bucket key + totals, or None.

    Parsing and pricing live in claude_history so the live rollup and the
    long-range backfill can't drift apart; this only adds minute bucketing and
    the retention cutoff.
    """
    parsed = claude_history.usage_from_record(rec)
    if parsed is None:
        return None
    t, model, inp, out, cr, w5, w1, cost = parsed
    if t < cutoff:
        return None
    return (int(t) // 60, model, inp, out, cr, w5, w1, cost)


def _read_file(path, from_offset, cutoff, deduper=None):
    """Read a jsonl file from a byte offset; return (events, new_offset)."""
    if deduper is None:
        deduper = claude_history.MessageDeduper()
    events = []
    try:
        with open(path, "rb") as fh:
            fh.seek(from_offset)
            data = fh.read()
    except OSError:
        return events, from_offset

    # Only consume up to the last complete line; leave a partial tail for next time.
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return events, from_offset  # no complete line yet
    consumed = data[: last_nl + 1]
    new_offset = from_offset + len(consumed)

    for line in consumed.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not deduper.accept(rec):
            continue
        ev = _event_from(rec, cutoff)
        if ev:
            events.append(ev)
    return events, new_offset


def scan():
    """Incrementally ingest new log lines and prune stale buckets."""
    now = time.time()
    cutoff = now - RETENTION_S
    fresh = []
    seen = set()

    for path in glob(os.path.join(LOG_ROOT, "**", "*.jsonl"), recursive=True):
        seen.add(path)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        off = _offsets.get(path, 0)
        # A file untouched since the cutoff can only hold events we would prune
        # anyway. Mark it fully consumed and never open it again — this is what
        # keeps a cold start from parsing hundreds of MB of archived sessions.
        if stat.st_mtime < cutoff:
            _offsets[path] = stat.st_size
            _dedupers.pop(path, None)
            continue
        if stat.st_size < off:
            off = 0          # truncated or rotated — start over
            _dedupers.pop(path, None)
        elif stat.st_size == off:
            continue         # nothing appended since last tick
        deduper = _dedupers.get(path)
        if deduper is None:
            deduper = _dedupers[path] = claude_history.MessageDeduper()
        evs, new_off = _read_file(path, off, cutoff, deduper)
        _offsets[path] = new_off
        fresh.extend(evs)

    # Forget files that disappeared, so the offset map tracks the log dir
    # rather than growing for the life of the process.
    if len(_offsets) > len(seen):
        for gone in [p for p in _offsets if p not in seen]:
            del _offsets[gone]
            _dedupers.pop(gone, None)

    cutoff_minute = int(cutoff) // 60
    with _lock:
        for minute, model, inp, out, cr, w5, w1, cost in fresh:
            bucket = _buckets.get((minute, model))
            if bucket is None:
                _buckets[(minute, model)] = [inp, out, cr, w5, w1, cost]
                continue
            bucket[0] += inp
            bucket[1] += out
            bucket[2] += cr
            bucket[3] += w5
            bucket[4] += w1
            bucket[5] += cost
        stale = [key for key in _buckets if key[0] < cutoff_minute]
        for key in stale:
            del _buckets[key]


def _blank():
    return {"input": 0, "output": 0, "cache_read": 0,
            "cache_write": 0, "total": 0, "cost_usd": 0.0}


def _accumulate(target, bucket):
    inp, out, cr, w5, w1, cost = bucket
    target["input"] += inp
    target["output"] += out
    target["cache_read"] += cr
    target["cache_write"] += w5 + w1
    target["total"] += inp + out + cr + w5 + w1
    target["cost_usd"] += cost


def _age_seconds(payload, now=None):
    """How old this payload's numbers are, or None when it never said."""
    age = cache_util.age_s(payload, now)
    return None if age is None else int(max(0, age))


def _held_resets(burndowns, provider, pool, raw, trusted=True):
    """Seconds to reset for one pool, preferring the burndown's held window.

    Sources report `resets_in_s` loosely enough that it drifts against the
    clock, so the burndown pins a window's reset to the moment it was observed
    and holds it there. Printing the raw reading next to a chart drawn on the
    held one is how the same question gets two answers in the same window, and
    on Codex the two are hours apart. Every countdown in this document comes
    through here so there is only ever one.

    Falls back to the raw value for a pool with no burndown yet: a source that
    is off, unconfigured, or still collecting its first sample.

    `trusted=False` gives no countdown at all. A stale snapshot still has a
    `resets_in_s` in it, and it is the most convincing wrong number in the
    document: it was true once, it is the right shape, and counting it down
    against the clock walks a dead window to zero in front of you. A percentage
    with no countdown reads as last-known. A percentage with a live countdown
    reads as now.
    """
    if not trusted:
        return None
    pools = (burndowns or {}).get(provider) or {}
    held = (pools.get(pool) or {}).get("resets_in_s")
    return raw if held is None else held


def _flatten_codex(codex, burndowns=None):
    """CodexBar-style flat fields for the ESP32 Codex page."""
    session_q = codex.get("session") or {}
    week_q = codex.get("week") or {}
    pace = codex.get("pace") or {}
    credits = codex.get("reset_credits") or {}
    spend = codex.get("spend") or {}
    trusted = cache_util.trusted(codex)
    session_resets = _held_resets(burndowns, "codex", "session",
                                  session_q.get("resets_in_s"), trusted)
    week_resets = _held_resets(burndowns, "codex", "week",
                               week_q.get("resets_in_s"), trusted)
    session_window = session_q.get("window_s") or oauth_usage.SESSION_WINDOW_S
    week_window = week_q.get("window_s") or oauth_usage.WEEK_WINDOW_S
    return {
        "ok": bool(codex.get("ok")),
        "plan": codex.get("plan"),
        "error": codex.get("error"),
        "session_pct": session_q.get("pct"),
        "session_pace_pct": oauth_usage.pace_pct(session_resets, session_window),
        "session_resets_in_s": session_resets,
        "session_resets_in": oauth_usage.fmt_resets(session_resets),
        "week_pct": week_q.get("pct"),
        "week_pace_pct": oauth_usage.pace_pct(week_resets, week_window),
        "week_resets_in_s": week_resets,
        "week_resets_in": oauth_usage.fmt_resets(week_resets),
        "pace_label": pace.get("label"),
        "pace_delta_pct": pace.get("delta_pct"),
        "pace_in_deficit": pace.get("in_deficit"),
        "runs_out_in": pace.get("runs_out_in"),
        "runs_out_in_s": pace.get("runs_out_in_s"),
        "reset_credits_available": credits.get("available"),
        "reset_credits_expiries": credits.get("expiries") or [],
        "reset_credits_expire_at": [
            credit["expires_at_s"]
            for credit in (credits.get("credits") or [])
            if isinstance(credit, dict)
            and credit.get("expires_at_s") is not None
        ],
        "cost_usd": spend.get("used_usd"),
        "cost_limit_usd": spend.get("limit_usd"),
        "cost_remaining_usd": spend.get("remaining_usd"),
        "cost_label": spend.get("label"),
        "cost_reached": spend.get("reached"),
    }


def _flatten_cursor(cursor, burndowns=None):
    """Flat Total/Auto/API fields for the ESP32 and menu-bar Cursor views."""
    total_q = cursor.get("total") or {}
    auto_q = cursor.get("auto") or {}
    api_q = cursor.get("api") or {}
    pace = cursor.get("pace") or {}
    spend = cursor.get("spend") or {}
    on_demand = cursor.get("on_demand") or {}
    # Cursor reports one billing cycle at the top level for every pool, so a
    # bucket without its own reading inherits it before the burndown is asked.
    trusted = cache_util.trusted(cursor)

    def pool_resets(pool, bucket):
        raw = bucket.get("resets_in_s")
        if raw is None:
            raw = cursor.get("resets_in_s")
        return _held_resets(burndowns, "cursor", pool, raw, trusted)

    total_resets = pool_resets("total", total_q)
    auto_resets = pool_resets("auto", auto_q)
    api_resets = pool_resets("api", api_q)
    resets = (
        total_resets
        if total_resets is not None
        else (auto_resets if auto_resets is not None else api_resets)
    )
    auto_window = auto_q.get("window_s") or 0
    api_window = api_q.get("window_s") or auto_window
    return {
        "ok": bool(cursor.get("ok")),
        "plan": cursor.get("plan"),
        "error": cursor.get("error"),
        "total_pct": total_q.get("pct"),
        "total_pace_pct": oauth_usage.pace_pct(
            total_resets, total_q.get("window_s"))
        if total_q.get("window_s") else None,
        "auto_pct": auto_q.get("pct"),
        "auto_pace_pct": oauth_usage.pace_pct(auto_resets, auto_window)
        if auto_window else None,
        "api_pct": api_q.get("pct"),
        "api_pace_pct": oauth_usage.pace_pct(api_resets, api_window)
        if api_window else None,
        "resets_in_s": resets,
        "resets_in": oauth_usage.fmt_resets(resets),
        "pace_label": pace.get("label"),
        "pace_delta_pct": pace.get("delta_pct"),
        "pace_in_deficit": pace.get("in_deficit"),
        "cost_usd": spend.get("used_usd"),
        "cost_limit_usd": spend.get("limit_usd"),
        "cost_remaining_usd": spend.get("remaining_usd"),
        "cost_label": spend.get("label"),
        "on_demand_label": on_demand.get("label"),
        "on_demand_remaining_usd": on_demand.get("remaining_usd"),
        "on_demand_limit_usd": on_demand.get("limit_usd"),
        "on_demand_used_usd": on_demand.get("used_usd"),
    }


def _cost_since(start_ts):
    """USD of Claude work recorded since `start_ts`, from the live buckets."""
    with _lock:
        buckets = [(minute, bucket[5]) for (minute, _model), bucket
                   in _buckets.items()]
    return sum(cost for minute, cost in buckets if minute * 60 >= start_ts)


# The ratio needs enough of a window behind it to mean anything.
PRIOR_MIN_ELAPSED_S = 2 * 3600
PRIOR_MIN_PCT = 1.0
PRIOR_MIN_COST_USD = 1.0


def _burn_priors(state, history, now):
    """Per-provider %/day burn estimates for windows too fresh to fit.

    The quota APIs only ever report *now*, so no amount of backfill can
    reconstruct a percent history. What the session logs do give is cost, and
    the current window supplies the missing conversion: it has both a percent
    used and the work that produced it. That ratio against the historical daily
    average is a defensible %/day estimate.

    Cost rather than token count, deliberately: a cache read is a tenth of an
    input token and output is five times one, so raw totals wildly understate
    the price of the tokens that actually move the meter. `pricing` already
    carries those weights.

    Both sides must cover the *same* window. Anthropic's weekly window rolls
    from its own start, which is rarely 7 calendar days ago, so the denominator
    is summed from that start rather than from a rolling week.

    Claude only — Codex and Cursor keep no local log. Anything resting on this
    is marked `estimated` by burndown.
    """
    if not history:
        return {}
    week = ((state or {}).get("claude") or {}).get("week") or {}
    try:
        used_pct = float(week.get("pct"))
        resets_in_s = float(week.get("resets_in_s"))
    except (TypeError, ValueError):
        return {}

    window_s = week.get("window_s") or oauth_usage.WEEK_WINDOW_S
    elapsed = window_s - max(0.0, min(resets_in_s, window_s))
    avg_cost_per_day = history.get("avg_cost_per_active_day") or 0
    if elapsed < PRIOR_MIN_ELAPSED_S or used_pct < PRIOR_MIN_PCT:
        return {}
    if avg_cost_per_day <= 0:
        return {}

    cost_in_window = _cost_since(now - elapsed)
    if cost_in_window < PRIOR_MIN_COST_USD:
        return {}

    estimate = (used_pct / cost_in_window) * float(avg_cost_per_day)
    # A prior that predicts blowing the window many times over is a broken
    # ratio, not a real forecast.
    if not (0 < estimate < 200):
        return {}
    return {"claude": estimate}


def _compute_doc():
    """Build the full /usage document from current state. Pure-ish; no I/O."""
    now = time.time()
    local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()

    today, week, session_5h, last_hour = _blank(), _blank(), _blank(), _blank()
    by_model = {}

    with _lock:
        buckets = {key: list(value) for key, value in _buckets.items()}
        state = {sid: dict(payload) for sid, payload in _state.items()}

    # Bucket timestamps are minute-aligned, so window edges are accurate to
    # the minute — well inside what a desk gadget can show.
    for (minute, model), bucket in buckets.items():
        t = minute * 60
        if t >= now - 7 * 24 * 3600:
            _accumulate(week, bucket)
        if t >= local_midnight:
            _accumulate(today, bucket)
            _accumulate(by_model.setdefault(model, _blank()), bucket)
        if t >= now - 5 * 3600:
            _accumulate(session_5h, bucket)
        if t >= now - 3600:
            _accumulate(last_hour, bucket)

    for bucket in (today, week, session_5h, last_hour, *by_model.values()):
        bucket["cost_usd"] = round(bucket["cost_usd"], 4)

    quota = state["claude"]
    vercel = state["vercel"]
    git = state["git"]
    github = state["github"]
    local = state["local"]
    supabase = state["supabase"]
    plausible = state["plausible"]
    posthog = state["posthog"]
    sentry = state.get("sentry") or {}
    datadog = state.get("datadog") or {}
    axiom = state.get("axiom") or {}
    claude_status_payload = state.get("claude-status") or {}

    local_tz = _local_tz()
    history = claude_history.summary(days=30)
    by_day = daily_burn.series(tz=local_tz)
    mixed_activity = activity_history.build(
        claude_history.series(days=activity_history.RETENTION_DAYS),
        daily_burn.series(
            tz=local_tz, days=activity_history.QUOTA_HISTORY_DAYS),
        today=datetime.now(local_tz).date(),
        available_sources=("claude", *sources_config.BURN_SOURCE_IDS),
    )
    burndowns = burndown.compute_all(
        state, now=now, tz=local_tz,
        priors=_burn_priors(state, history, now),
    )
    # Flatten Claude fields at the top level (back-compat with older firmware).
    session_q = quota.get("session") or {}
    week_q = quota.get("week") or {}
    quota_trusted = cache_util.trusted(quota, now)
    session_resets = _held_resets(burndowns, "claude", "session",
                                  session_q.get("resets_in_s"), quota_trusted)
    week_resets = _held_resets(burndowns, "claude", "week",
                               week_q.get("resets_in_s"), quota_trusted)
    doc = {
        "updated": datetime.now(local_tz).strftime("%Y-%m-%dT%H:%M:%S%z"),
        # Shape of this document, for clients that ship separately from the
        # host — the phone and the board. A client older than its own floor
        # says so instead of drawing blanks. See host_version.CONTRACT and
        # docs/contract.md for when this moves.
        "contract": host_version.CONTRACT,
        "plan": quota.get("plan"),
        "session_pct": session_q.get("pct"),
        "session_pace_pct": oauth_usage.pace_pct(
            session_resets, oauth_usage.SESSION_WINDOW_S),
        "session_resets_in_s": session_resets,
        "session_resets_in": oauth_usage.fmt_resets(session_resets),
        "week_pct": week_q.get("pct"),
        "week_pace_pct": oauth_usage.pace_pct(
            week_resets, oauth_usage.WEEK_WINDOW_S),
        "week_resets_in_s": week_resets,
        "week_resets_in": oauth_usage.fmt_resets(week_resets),
        "quota_ok": bool(quota.get("ok")),
        "quota_error": quota.get("error"),
        "today": today,
        "week": week,
        "session_5h": session_5h,
        "last_hour": last_hour,
        "by_model": by_model,
        "by_day": by_day,
        # Per-pool burndown: ideal decay, actual curve, and time-to-exhaustion.
        # `burndown_primary` is the pool most worth showing — what the menu-bar
        # icon and the board's headline follow.
        "burndown": burndowns,
        "burndown_primary": burndown.primary(burndowns),
        # Months of real Claude usage, backfilled from the session logs. Token
        # history, not quota-percent history — see claude_history.
        "history": history,
        # Mixed activity: Claude's local session history plus daily burn from
        # every quota source. Native source numbers stay in each sparse day;
        # the level is only an evidence ramp, never a fake common unit.
        "activity_history": mixed_activity,
        "quota": quota,
        "codex": _flatten_codex(state["codex"], burndowns),
        "cursor": _flatten_cursor(state["cursor"], burndowns),
        # Dynamic provider list (enabled flags + pool schema). Prefer this over
        # the legacy Claude-top-level / codex / cursor objects when adding UI.
        "providers": _providers_payload(state, burndowns),
        "vercel": {
            "ok": bool(vercel.get("ok")),
            "team": vercel.get("team"),
            "error": vercel.get("error"),
            "stale": bool(vercel.get("stale")),
            "deployments": vercel.get("deployments") or [],
        },
        "git": {
            "ok": bool(git.get("ok")),
            "error": git.get("error"),
            "stale": bool(git.get("stale")),
            "commits": git.get("commits") or [],
        },
        "activity": _build_activity(
            vercel, git, supabase, github, claude_status_payload, burndowns,
            sentry, datadog, axiom),
        "supabase": supabase,
        "plausible": plausible,
        "posthog": posthog,
        "sentry": {
            "ok": bool(sentry.get("ok")),
            "configured": bool(sentry.get("configured")),
            "error": sentry.get("error"),
            "stale": bool(sentry.get("stale")),
            "org": sentry.get("org"),
            "alert_count": sentry.get("alert_count") or 0,
            "issues": sentry.get("issues") or [],
        },
        "datadog": {
            "ok": bool(datadog.get("ok")),
            "configured": bool(datadog.get("configured")),
            "error": datadog.get("error"),
            "stale": bool(datadog.get("stale")),
            "site": datadog.get("site"),
            "alert_count": datadog.get("alert_count") or 0,
            "warn_count": datadog.get("warn_count") or 0,
            "monitors": datadog.get("monitors") or [],
        },
        "axiom": {
            "ok": bool(axiom.get("ok")),
            "configured": bool(axiom.get("configured")),
            "error": axiom.get("error"),
            "stale": bool(axiom.get("stale")),
            "host": axiom.get("host"),
            "org_id": axiom.get("org_id"),
            "alert_count": axiom.get("alert_count") or 0,
            "alerts": axiom.get("alerts") or [],
        },
        "claude_status": {
            "ok": bool(claude_status_payload.get("ok")),
            "configured": bool(claude_status_payload.get("configured", True)),
            "error": claude_status_payload.get("error"),
            "stale": bool(claude_status_payload.get("stale")),
            "indicator": claude_status_payload.get("indicator") or "none",
            "description": claude_status_payload.get("description"),
            "alerting": bool(claude_status_payload.get("alerting")),
            "incident_name": claude_status_payload.get("incident_name"),
            "incident_impact": claude_status_payload.get("incident_impact"),
            "url": claude_status_payload.get("url") or claude_status.PAGE_URL,
            "updated_at": claude_status_payload.get("updated_at"),
        },
        "github": {
            "ok": bool(github.get("ok")),
            "configured": bool(github.get("configured")),
            "error": github.get("error"),
            "stale": bool(github.get("stale")),
            "fail_count": github.get("fail_count") or 0,
            "running_count": github.get("running_count") or 0,
            "runs": github.get("runs") or [],
            "inbox": github.get("inbox") or [],
            "inbox_count": github.get("inbox_count") or 0,
            "repos": github.get("repos") or [],
        },
        "local": {
            "ok": bool(local.get("ok")),
            "host": local.get("host"),
            "error": local.get("error"),
            "stale": bool(local.get("stale")),
            "servers": local.get("servers") or [],
            "builds": local.get("builds") or [],
        },
        "sources": _sources_payload(state),
        # The providers the compact surfaces show, already picked. Menu bar,
        # widget and the board's three slots read this instead of each slicing
        # their own top-N.
        "focus": sources_config.focus_ids(),
        # Integrations catalog order (Activity blocks + Settings list).
        "integrations_order": sources_config.integrations_order_ids(),
    }
    doc["attention"] = _build_attention(doc)
    # Last, because it summarizes everything above it.
    doc["machines"] = icloud_sync.machines_payload(_machine_beacon(doc))
    return doc


def _machine_beacon(doc):
    """What this Mac tells the others about itself.

    Deliberately a summary and not a slice of the document. A peer Mac cannot
    act on the desktop's git log or open its local servers, and merging either
    into one list would invent a machine that does not exist. What travels is
    what makes you get up and walk over: what it is burning, whether something
    is waiting on you there, and how long ago it said so.
    """
    providers = []
    for row in (doc.get("providers") or []):
        if not row.get("enabled"):
            continue
        pool = (row.get("pools") or {}).get(row.get("headline")) or {}
        providers.append({
            "id": row.get("id"),
            "title": row.get("title"),
            "pct": pool.get("pct"),
            "accent": row.get("accent"),
        })
    attention = doc.get("attention") or {}
    local = doc.get("local") or {}
    beacon = {
        "host_version": host_version.version(),
        "providers": providers[:sources_config.FOCUS_LIMIT],
        "servers": len(local.get("servers") or []),
    }
    if attention.get("level") not in (None, "ok"):
        beacon["attention_open"] = len(attention.get("reasons") or [])
        beacon["attention_top"] = attention.get("summary")
    # An agent waiting for an answer is the one thing here that is genuinely
    # urgent and genuinely invisible from the other Mac.
    try:
        open_events = (agent_gateway.get().events(state="open") or {})
        waiting = len(open_events.get("events") or [])
    except Exception:
        waiting = 0
    if waiting:
        beacon["agent"] = waiting
    if _device_payload(time.time()):
        # Only one Mac has the board on its desk, and that is worth saying:
        # it is the machine whose numbers the panel is showing.
        beacon["board"] = True
    return beacon


def publish():
    """Rebuild the cached document and its encoded bodies. Returns the doc."""
    doc = _compute_doc()
    usage = json.dumps(doc).encode()
    device = json.dumps(
        device_view.build(doc, effect=_device_effect_payload()),
        separators=(",", ":")).encode()
    with _cache_lock:
        _cache.update(doc=doc, usage=usage, device=device, built=time.time())
    return doc


def rollup():
    """Current /usage document, building one if the cache is cold."""
    with _cache_lock:
        doc = _cache["doc"]
    return doc if doc is not None else publish()


def _bodies():
    """(usage_bytes, device_bytes) for the current document."""
    with _cache_lock:
        if _cache["doc"] is not None:
            return _cache["usage"], _cache["device"]
    publish()
    with _cache_lock:
        return _cache["usage"], _cache["device"]


def _stale_cause(error, kind=None):
    """One clause naming why a quota froze and whether waiting fixes it.

    The glossary splits staleness into things you wait out (rate limits,
    outages — the host is already retrying) and things you go fix. The
    attention line has to say which one this is, or "stuck at 17h old"
    reads as equally mysterious either way.
    """
    kind = kind or cache_util.stale_kind(error)
    if kind == "rate_limited":
        return "provider is rate-limiting, retrying"
    if kind == "provider":
        return "provider error, retrying"
    if kind == "network":
        return "no route to provider — check network"
    text = str(error or "")
    if text:
        # An error nothing above recognizes is exactly the one worth
        # showing verbatim, clipped to fit a menu-bar subtitle.
        return text if len(text) <= 48 else text[:47] + "…"
    return "no error recorded — try Refresh all"


def _provider_stale_cause(provider):
    """Prefer the stamped cause; fall back to classifying the error string."""
    cause = provider.get("stale_cause")
    if cause in ("rate_limited", "provider", "network"):
        return cause
    return cache_util.stale_kind(provider.get("error"))


def _retry_in_s(payload, now=None):
    """Seconds until a rate-limit hold lifts, or None when none is active."""
    retry_at = (payload or {}).get("retry_at")
    if not isinstance(retry_at, (int, float)) or retry_at <= 0:
        return None
    when = time.time() if now is None else float(now)
    left = int(retry_at - when)
    return left if left > 0 else None


def _build_attention(doc):
    """Single glance score for menu-bar warning light + Overview card."""
    reasons = []

    def add(level, kind, summary, weight):
        reasons.append({
            "level": level,
            "kind": kind,
            "summary": summary,
            "weight": weight,
        })

    github = doc.get("github") or {}
    if github.get("configured") and (github.get("fail_count") or 0) > 0:
        fails = int(github["fail_count"])
        summary = _github_attention_summary(github) or (
            f"{fails} GitHub Actions failure" + ("" if fails == 1 else "s")
        )
        add(
            "critical",
            "github",
            summary,
            40 + min(30, fails * 5),
        )

    # Incoming review requests / assignments on the watched list — warn, not
    # critical: they are yours to answer, but they are not a red CI.
    # Only rows inside ATTENTION_INBOX_MAX_AGE_S count here. An assignment
    # nobody has touched in two weeks stays in the feed and stops paging.
    inbox = github_actions.attention_inbox(github.get("inbox") or [])
    inbox_count = len(inbox)
    if github.get("configured") and inbox_count > 0:
        summary = github_actions.attention_inbox_summary(inbox) or (
            f"{inbox_count} GitHub inbox"
        )
        add(
            "warn",
            "github-inbox",
            summary,
            18 + min(18, inbox_count * 4),
        )

    claude_status_doc = doc.get("claude_status") or {}
    if claude_status_doc.get("configured") and claude_status_doc.get("alerting"):
        add(
            "critical",
            "claude-status",
            claude_status.attention_summary(claude_status_doc),
            45,
        )

    supabase = doc.get("supabase") or {}
    if supabase.get("configured") and (supabase.get("alert_count") or 0) > 0:
        alerts = int(supabase["alert_count"])
        add(
            "warn" if alerts < 3 else "critical",
            "supabase",
            f"{alerts} Supabase alert" + ("" if alerts == 1 else "s"),
            25 + min(25, alerts * 8),
        )

    # ERROR-level lints only. WARN and INFO are still listed in the app, but a
    # schema finding sits there until someone changes the schema — pipping on
    # all three would leave the light amber forever and teach you to ignore it.
    lint_errors = int(supabase.get("lint_error_count") or 0)
    if supabase.get("configured") and lint_errors:
        add(
            "warn",
            "supabase-security",
            f"{lint_errors} Supabase security issue"
            + ("" if lint_errors == 1 else "s"),
            18 + min(18, lint_errors * 3),
        )

    deploys = ((doc.get("vercel") or {}).get("deployments")) or []
    deploy_errors = sum(
        1 for d in deploys
        if (d.get("status") or "").lower() == "error"
        or (d.get("state") or "").upper() in ("ERROR", "FAILED")
    )
    if deploy_errors:
        add(
            "critical" if deploy_errors >= 2 else "warn",
            "vercel",
            f"{deploy_errors} failed deploy" + ("" if deploy_errors == 1 else "s"),
            20 + min(20, deploy_errors * 8),
        )

    sentry = doc.get("sentry") or {}
    sentry_alerts_n = int(sentry.get("alert_count") or 0)
    if sentry.get("configured") and sentry_alerts_n > 0:
        add(
            "critical" if sentry_alerts_n >= 3 else "warn",
            "sentry",
            f"{sentry_alerts_n} Sentry issue"
            + ("" if sentry_alerts_n == 1 else "s"),
            28 + min(24, sentry_alerts_n * 4),
        )

    datadog = doc.get("datadog") or {}
    dd_alerts = int(datadog.get("alert_count") or 0)
    dd_warns = int(datadog.get("warn_count") or 0)
    if datadog.get("configured") and dd_alerts > 0:
        add(
            "critical" if dd_alerts >= 2 else "warn",
            "datadog",
            f"{dd_alerts} Datadog alert" + ("" if dd_alerts == 1 else "s"),
            30 + min(25, dd_alerts * 5),
        )
    elif datadog.get("configured") and dd_warns > 0:
        add(
            "warn",
            "datadog",
            f"{dd_warns} Datadog warn" + ("" if dd_warns == 1 else "s"),
            16 + min(16, dd_warns * 3),
        )

    axiom = doc.get("axiom") or {}
    axiom_alerts = int(axiom.get("alert_count") or 0)
    if axiom.get("configured") and axiom_alerts > 0:
        add(
            "critical" if axiom_alerts >= 2 else "warn",
            "axiom",
            f"{axiom_alerts} Axiom alert" + ("" if axiom_alerts == 1 else "s"),
            28 + min(24, axiom_alerts * 5),
        )

    # Quota % lives on the rings — don't nag Attention for a drained meter.
    # Only call out time-sensitive / hard-limit events.
    codex = doc.get("codex") or {}
    if codex.get("ok"):
        runs_out = codex.get("runs_out_in_s")
        if isinstance(runs_out, (int, float)) and 0 < runs_out <= 3 * 3600:
            add("warn", "codex", f"Codex runs out in {codex.get('runs_out_in')}", 22)
        if codex.get("cost_reached"):
            add("critical", "codex", "Codex spend limit reached", 40)

    # A source timing out keeps its last-good data and stays quiet — that is
    # the point of the fallback. Past STALE_ALERT_S it is not a timeout any
    # more, and silence becomes the problem: the meter still reads 42%, the
    # countdown still ticks, and nothing on any surface says the number is
    # from an hour ago. One reason per source, because each is fixed
    # separately.
    for provider in doc.get("providers") or []:
        if provider.get("kind") != "quota" or not provider.get("enabled"):
            continue
        title = provider.get("title") or provider.get("id")
        # A dead login is not a slow one, so it does not wait out STALE_ALERT_S
        # first. Every minute spent under the stale threshold is a minute the
        # meter reads plausibly and is not being refreshed by anyone, and no
        # amount of waiting fixes it — outranking stale for the same reason.
        if provider.get("auth_required"):
            add(
                "warn",
                "signin",
                f"{title} needs sign-in — "
                f"{sources_config.login_remedy(provider.get('id'))}",
                45,
            )
            continue
        held = provider.get("stale_for_s")
        if not provider.get("stale") or not isinstance(held, (int, float)):
            continue
        cause = _provider_stale_cause(provider)
        # Rate limits are the host backing off on purpose. Wait longer before
        # Attention pages — the card already says Paused with a retry time.
        threshold = (cache_util.STALE_ALERT_RATE_LIMIT_S
                     if cause == "rate_limited"
                     else cache_util.STALE_ALERT_S)
        if held < threshold:
            continue
        add(
            "warn",
            "stale",
            f"{title} quota stuck at {oauth_usage.fmt_resets(held)} old"
            f" — {_stale_cause(provider.get('error'), cause)}",
            30,
        )

    score = min(100, sum(r["weight"] for r in reasons))
    if any(r["level"] == "critical" for r in reasons):
        level = "critical"
    elif reasons:
        level = "warn"
    else:
        level = "ok"
    # Drop weight from public payload — clients only need level/summary.
    public_reasons = [
        {"level": r["level"], "kind": r["kind"], "summary": r["summary"]}
        for r in sorted(reasons, key=lambda r: -r["weight"])
    ]
    fingerprint = "\n".join(sorted(
        "|".join(str(reason.get(key) or "") for key in ("level", "kind", "summary"))
        for reason in public_reasons
    )) or "ok"
    acknowledged = (
        bool(public_reasons)
        and fingerprint == app_config.attention_ack_fingerprint()
    )
    if acknowledged:
        return {
            "level": "ok",
            "score": 0,
            "summary": "All clear",
            "reasons": [],
            "fingerprint": fingerprint,
            "acknowledged": True,
        }
    return {
        "level": level,
        "score": score,
        "summary": (
            public_reasons[0]["summary"] if public_reasons
            else "All clear"
        ),
        "reasons": public_reasons,
        "fingerprint": fingerprint,
        "acknowledged": False,
    }


def _login_email_for(source):
    """Best-effort signed-in email for a quota source. Additive, Mac-local.

    Codex reads the ChatGPT id_token; Cursor reads the IDE's cached profile.
    Claude's OAuth token is opaque, so it stays unset until we have another
    source. Never invents an address.
    """
    account = getattr(source, "account", None)
    base = source.id.split(":", 1)[0]
    try:
        if base == "codex":
            return codex_usage.login_email(account)
        if base == "cursor":
            return cursor_usage.login_email(account)
    except Exception:
        return None
    return None


def _sources_payload(state):
    enabled = sources_config.enabled_map()
    dismissed = sources_config.dismissed_map()
    now = time.time()
    with _lock:
        times = dict(_source_times)
    rows = []
    # Pinned order too, so the Settings list and the meters agree.
    for source in sources_config.ordered_sources():
        payload = state.get(source.id) or {}
        age = times.get(source.id) or 0.0
        fetched_age = _age_seconds(payload, now)
        row = {
            "id": source.id,
            "title": sources_config.title_for(source.id),
            "title_default": sources_config.default_title(source.id),
            "hint": source.hint,
            "kind": source.kind,
            "group": source.group,
            # Brand accent so Settings can identify a row by color instead of
            # spending its only dot on health. Null for rows with no brand.
            # Resolved on the host, so the menu bar, the phone and the popover
            # cannot disagree about a color one of them just changed.
            "accent": sources_config.accent_for(source.id),
            # What Settings' color grid marks as "Default" — the registry's
            # own answer, which is also how a client tells an override apart
            # from the shipped color.
            "accent_default": source.accent,
            **({"accent_derived": sources_config.derived_accent_for(source.id)}
               if source.account is not None else {}),
            "enabled": bool(enabled.get(source.id, True)),
            # Library vs Active membership. Off-but-not-dismissed is paused:
            # the row stays in Active, dimmed, and nothing polls it.
            "dismissed": bool(dismissed.get(
                source.id, not enabled.get(source.id, True))),
            "ok": bool(payload.get("ok")),
            "stale": bool(payload.get("stale")),
            # The login is gone or rejected. Distinct from `stale`, which this
            # also sets: staleness says the numbers stopped, this says why and
            # who can fix it.
            "auth_required": bool(payload.get("auth_required")),
            "configured": payload.get("configured"),
            "error": payload.get("error"),
            "detail": sources_config.detail_for(source.id, payload),
            # Age of the *numbers*, falling back to the last poll for sources
            # that carry no timestamp of their own. `_source_times` records
            # when we last tried, and a failing source is tried on the same
            # schedule as a healthy one — so reporting that as the age is how
            # something broken since last night reads as a minute old, and how
            # the "N minutes stale" line stays at one minute forever.
            "age_s": (fetched_age if fetched_age is not None
                      else (int(max(0, now - age)) if age > 0 else None)),
            # Same typed cause the meters carry, so Settings can say Paused
            # for a rate limit instead of painting every freeze amber.
            "stale_cause": (
                _provider_stale_cause(payload) if payload.get("stale")
                else None),
        }
        # Named accounts only. Clients put this next to the brand mark so the
        # mark names the tool and the label names the login — "Claude · Work"
        # beside a Claude glyph truncates to the one word that was already
        # obvious.
        if source.account is not None:
            row["label"] = source.account.label
        email = _login_email_for(source)
        if email:
            row["email"] = email
        rows.append(row)
    return rows


def _providers_payload(state, burndowns=None):
    """Normalized quota providers for dynamic Mac clients.

    Additive next to the legacy flat Claude + nested codex/cursor fields so
    firmware and older builds keep working. Pool keys match the nested fetcher
    shape; `ring` tells the UI which meters to chart.
    """
    enabled = sources_config.enabled_map()
    rows = []
    # Pinned order, so a client that simply iterates providers[] agrees with
    # the menu bar, the widget, and the board about sequence.
    for rank, source in enumerate(sources_config.ordered_quota_sources()):
        payload = state.get(source.id) or {}
        trusted = cache_util.trusted(payload)
        pools = {}
        # `pools` is a JSON object, so declaration order is lost on the wire.
        # Ship it as a rank the way providers[] already does, so rings, bars
        # and burndown charts can all sort by the one order defined here.
        for pool_rank, spec in enumerate(source.pools):
            bucket = payload.get(spec.key) or {}
            level, headroom = meters.readings(spec, bucket)
            if spec.kind == sources_config.KIND_WINDOW:
                raw = bucket.get("resets_in_s")
                if raw is None:
                    # Cursor reports one billing cycle at the top level for
                    # every pool, so a bucket with no reading of its own
                    # inherits it.
                    raw = payload.get("resets_in_s")
                resets = _held_resets(
                    burndowns, source.id, spec.id, raw, trusted)
                window = bucket.get("window_s") or spec.default_window_s
            else:
                # Windows only, and the fallback above is why. A grant has no
                # reading of its own, so it would inherit the provider's
                # cycle and count down to a refill that is not coming — a
                # countdown that is wrong but entirely plausible, which is the
                # worst kind. Same reasoning as `_held_resets(trusted=False)`.
                resets = None
                window = None
            pools[spec.id] = {
                "title": spec.title,
                "rank": pool_rank,
                # What shape this meter is, and where its numbers came from.
                # What shape this meter is, and where its numbers came from.
                # See docs/metering.md.
                "kind": spec.kind,
                "basis": spec.basis,
                # The kind-agnostic pair. A client that wants to draw any
                # meter reads these; the window-shaped fields below stay for
                # the ones that were built before other kinds existed.
                "level": level,
                "headroom": headroom,
                # Window-shaped, and null on every other kind — which is what
                # keeps an old client (and the board) from drawing them: both
                # already skip a pool with no pct. See docs/contract.md.
                "pct": bucket.get("pct"),
                "window_s": window,
                "resets_in_s": resets,
                "resets_in": oauth_usage.fmt_resets(resets),
                "pace_pct": (
                    oauth_usage.pace_pct(resets, window) if window else None),
                "ring": bool(spec.ring),
            }
            # A grant's clock is per item and counts toward losing something,
            # so it is its own field rather than a second meaning for
            # `resets_in_s`. Only emitted where it exists.
            if spec.kind == sources_config.KIND_GRANT:
                pools[spec.id]["expires_in_s"] = meters.next_expiry_s(bucket)
        age = payload.get("stale_for_s")
        if not isinstance(age, (int, float)):
            age = _age_seconds(payload)
        row = {
            "id": source.id,
            "title": sources_config.title_for(source.id),
            "title_default": sources_config.default_title(source.id),
            "kind": "quota",
            "rank": rank,
            "enabled": bool(enabled.get(source.id, True)),
            "ok": bool(payload.get("ok")),
            # Bars stay up on a stale payload — last-known beats blank — so the
            # card needs the flag to say so. Without it the only difference
            # between last night's numbers and this minute's is a countdown
            # that is now deliberately absent, which is not a difference a
            # reader can be expected to notice.
            "stale": bool(payload.get("stale")),
            # Why the bars froze, when the answer is a login. Clients word a
            # dead credential differently from a provider that is merely
            # unreachable, because only one of them is the reader's to fix.
            "auth_required": bool(payload.get("auth_required")),
            "age_s": age,
            # Follow-up clients use the explicit stale-duration name. Keep
            # age_s for clients already shipped against the first freshness
            # payload; both are derived from the same fetched_at timestamp.
            "stale_for_s": age,
            # Typed freeze reason + seconds until the host will ask again.
            # Absolute `retry_at` on the payload is turned into a countdown
            # here so a card that redraws every minute keeps moving without
            # another Anthropic round-trip.
            "stale_cause": (
                _provider_stale_cause(payload) if payload.get("stale")
                else None),
            "retry_in_s": _retry_in_s(payload),
            "plan": payload.get("plan"),
            "error": payload.get("error"),
            "accent": sources_config.accent_for(source.id),
            "accent_default": source.accent,
            **({"accent_derived": sources_config.derived_accent_for(source.id)}
               if source.account is not None else {}),
            "headline": source.headline[0] if source.headline else None,
            "reset_note_url": source.reset_note_url,
            # Subscription prices are registry metadata, not account usage.
            # Keep them next to the provider plan and meters so every client
            # can render the same catalog without copying a price table.
            "subscription_pricing": source.subscription_pricing_payload(),
            "pools": pools,
        }
        # Prepaid balance leaf: observed daily spend / runway / models.
        # Additive and absent on window providers — see docs/metering.md.
        spend = payload.get("spend")
        if isinstance(spend, dict) and spend:
            row["spend"] = spend
        # See `_sources_payload`: brand mark + user label, not "Brand · Label".
        if source.account is not None:
            row["label"] = source.account.label
        email = _login_email_for(source)
        if email:
            row["email"] = email
        rows.append(row)
    return rows


# What the board last told us about itself. One record for both transports:
# the question "is the ROM on my desk the build I flashed" should not have a
# Wi-Fi answer and a cable answer.
_device_lock = threading.Lock()
_device = {"firmware": None, "seen": 0.0, "via": None}
_device_effect_lock = threading.Lock()
_device_effect = {"id": 0, "kind": None, "provider": None}


def _note_device(query, via):
    """Record a board's reported build from a request's query string.

    Best-effort and never raises: a board sending nonsense must still get its
    document. An unversioned board simply never calls this, which is itself
    the signal that it predates build stamping.
    """
    try:
        firmware = urllib.parse.parse_qs(query or "").get("fw", [""])[0].strip()
    except Exception:
        return
    if not firmware:
        return
    with _device_lock:
        if _device["firmware"] != firmware:
            print(f"device firmware {firmware} (via {via})", flush=True)
        _device.update(firmware=firmware[:64], seen=time.time(), via=via)


def _device_payload(now):
    with _device_lock:
        if not _device["firmware"]:
            return None
        return {
            "firmware": _device["firmware"],
            "via": _device["via"],
            "age_s": int(max(0, now - _device["seen"])),
        }


def _device_effect_payload():
    with _device_effect_lock:
        if not _device_effect["id"] or not _device_effect["kind"]:
            return None
        return dict(_device_effect)


def trigger_device_effect(kind="reset", provider=None):
    """Queue an additive board effect for the next device projection."""
    if kind != "reset":
        raise ValueError("unknown device effect")
    provider = provider.strip() if isinstance(provider, str) else None
    if provider == "":
        provider = None
    with _device_effect_lock:
        # Epoch seconds survive a host restart in practice; the increment
        # handles two test commands issued in the same second.
        effect_id = max(int(time.time()), _device_effect["id"] + 1)
        _device_effect.update(id=effect_id, kind=kind, provider=provider)
        return dict(_device_effect)


def _github_watch_payload():
    """Watch list as configured, plus the repos it actually resolves to.

    Settings shows both: owners and always-repos are what you type (or tick),
    `available` is every GitHub remote under `dev_root` for the picker, and
    `watching` is what the scan made of the filters — the part that used to
    need a shell and a JSON file to find out.
    """
    return {
        "ok": True,
        "owners": list(app_config.github_org_prefixes()),
        "always_repos": list(app_config.github_always_repos()),
        "max_discovered": app_config.github_max_discovered(),
        "dev_root": app_config.dev_root(),
        "available": github_actions.discoverable_repos(),
        "watching": github_actions.watched_repos(),
    }


def _git_config_payload():
    """Where local commits come from, plus what that folder actually holds.

    `repos` is the same courtesy `watching` pays on the GitHub side: a Dev
    root with nothing under it and a Dev root that is simply quiet look
    identical from Settings otherwise.
    """
    root = app_config.dev_root()
    repos = [os.path.basename(path)
             for path in git_activity.discovered_repos()]
    return {
        "ok": True,
        "dev_root": app_config.dev_root_setting(),
        "dev_root_path": root,
        "dev_root_exists": os.path.isdir(root),
        "authors": list(app_config.git_authors()),
        "repos": repos,
    }


def _vercel_config_payload():
    return {
        "ok": True,
        "teams": list(app_config.vercel_team_slugs()),
        "available": vercel_builds.available_teams(),
        "signed_in": detect_sources.vercel_signed_in(),
    }


def _supabase_config_payload():
    listing = supabase_usage.available_projects()
    return {
        "ok": listing.get("error") is None,
        "configured": bool(supabase_usage.has_token()),
        "projects": list(app_config.supabase_project_refs()),
        "available": listing.get("projects") or [],
        "error": listing.get("error"),
    }


def _plausible_config_payload():
    listing = plausible_usage.available_sites()
    return {
        "ok": listing.get("error") is None,
        "configured": bool(plausible_usage.has_token()),
        "host": app_config.plausible_host(),
        "sites": list(app_config.plausible_sites()),
        "available": listing.get("sites") or [],
        "error": listing.get("error"),
    }


def _timezone_config_payload():
    return {
        "ok": True,
        "timezone": app_config.timezone_name(),
    }


def _posthog_config_payload():
    listing = posthog_usage.available_projects()
    return {
        "ok": listing.get("error") is None,
        "configured": bool(posthog_usage.has_token()),
        "host": app_config.posthog_host(),
        "projects": list(app_config.posthog_projects()),
        "available": listing.get("projects") or [],
        "error": listing.get("error"),
    }


def _sentry_config_payload():
    return {
        "ok": True,
        "configured": bool(sentry_alerts.has_token()),
        "org": app_config.sentry_org(),
    }


def _datadog_config_payload():
    return {
        "ok": True,
        "configured": bool(datadog_monitors.has_keys()),
        "site": app_config.datadog_site(),
    }


def _axiom_config_payload():
    return {
        "ok": True,
        "configured": bool(axiom_monitors.has_token()),
        "host": app_config.axiom_host(),
        "org_id": app_config.axiom_org_id(),
    }


# The Bonjour advertiser, so a restart can take it down before re-exec rather
# than leaving a second one advertising the same service.
_bonjour = None


def _restart_host():
    """Re-exec this host, off the request thread.

    Adding an account changes the source list, and the sample schema, the
    poller's per-source clocks and the burndown pools are all derived from it
    at import. Rebuilding those live would leave them disagreeing with each
    other for one tick; a restart is two seconds and is always right. Exec
    rather than exit so it also works for a host run by hand from a clone,
    where nothing would restart it.
    """
    def _go():
        time.sleep(0.4)     # let the response reach the client first
        print("accounts changed — restarting", flush=True)
        try:
            if _bonjour is not None and _bonjour.poll() is None:
                _bonjour.terminate()
        except Exception:
            pass
        try:
            os.execv(sys.executable,
                     [sys.executable, os.path.abspath(__file__), *sys.argv[1:]])
        except Exception as exc:
            # Under launchd, KeepAlive brings us straight back — but only for a
            # non-zero exit. Zero is reserved for "someone else owns the port,
            # stay down" (see main), and using it here would strand the host.
            print("re-exec failed, exiting for launchd:", exc, flush=True)
            os._exit(1)

    threading.Thread(target=_go, daemon=True).start()


def _accounts_payload(restarting=False):
    return {
        "ok": True,
        "restarting": bool(restarting),
        **sources_config.accounts_payload(),
    }


def _health_payload():
    doc = rollup()
    with _cache_lock:
        built = _cache["built"] or 0.0
    by_id = {row["id"]: row for row in (doc.get("sources") or [])}
    return {
        "ok": True,
        # Which host is answering. The menu bar compares these against the copy
        # bundled in the .app and offers to reinstall when they diverge, so a
        # launchd job left over from an older build stops masquerading as
        # current. See host_version.py.
        **host_version.payload(),
        "uptime_s": int(max(0, time.time() - BOOT_T0)),
        "updated": doc.get("updated"),
        "built_age_s": int(max(0, time.time() - built)),
        "usb": usb_bridge.status_payload(),
        # The board's own account of what it is running, absent until one
        # reports in. A board that never populates this is either offline or
        # predates build stamping.
        "device": _device_payload(time.time()),
        "agents": agent_gateway.get().capabilities(),
        "sources": {
            sid: {
                "ok": by_id.get(sid, {}).get("ok"),
                "stale": by_id.get(sid, {}).get("stale"),
                "enabled": by_id.get(sid, {}).get("enabled"),
                "age_s": by_id.get(sid, {}).get("age_s"),
                "error": by_id.get(sid, {}).get("error"),
                "detail": by_id.get(sid, {}).get("detail"),
            }
            for sid in sources_config.SOURCE_IDS
        },
    }


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        self._send_bytes(status, json.dumps(payload).encode())

    def _send_bytes(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self):
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return None
        mapped = getattr(address, "ipv4_mapped", None)
        return mapped or address

    def _header_host(self):
        """The hostname the client asked for, lowercased, without the port."""
        raw = (self.headers.get("Host") or "").strip()
        if not raw:
            return ""
        if raw.startswith("["):            # [::1]:8737
            end = raw.find("]")
            return raw[1:end].lower() if end > 0 else ""
        return (raw.rsplit(":", 1)[0] if ":" in raw else raw).lower()

    def _is_loopback(self):
        """Trusted-because-local, checked at both ends of the request.

        The socket check alone is not enough. A page on `evil.tld` whose DNS
        answer flips to 127.0.0.1 — classic rebinding, and this host is a
        fixed port advertised over mDNS — reaches us on a loopback socket
        like any local process, and would inherit the whole Mac-local class:
        starting an agent task, reading `/config/git`, restarting the host.
        What it cannot do is change the name in `Host`, because that is the
        name it had to resolve to get here. So the header has to agree.
        """
        address = self._client_ip()
        if not (address and address.is_loopback):
            return False
        return self._header_host() in ("", "127.0.0.1", "localhost", "::1")

    def _is_browser_cross_origin(self):
        """A cross-site request from a browser, which no real client makes.

        The Mac app, the phone, the board and curl never send `Origin` or
        `Sec-Fetch-Site`; browsers always do. Treating their presence as
        disqualifying costs nothing and closes drive-by CSRF against every
        route, including the ones loopback waves through without a token.
        """
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin and origin not in ("null",):
            host = origin.split("://", 1)[-1]
            if host.startswith("["):
                end = host.find("]")
                host = host[1:end] if end > 0 else host
            else:
                host = host.rsplit(":", 1)[0] if ":" in host else host
            if host not in ("127.0.0.1", "localhost", "::1"):
                return True
        return (self.headers.get("Sec-Fetch-Site") or "").strip().lower() \
            in ("cross-site", "same-site")

    def _is_private(self):
        """Loopback, private LAN, or Tailscale CGNAT space."""
        address = self._client_ip()
        tailscale = ipaddress.ip_network("100.64.0.0/10")
        return bool(
            address
            and (
                address.is_loopback
                or address.is_private
                or (address.version == 4 and address in tailscale)
            )
        )

    def _allowed(self):
        """Loopback is trusted; remote clients use their scoped credential."""
        if self._is_loopback():
            return True
        if self.headers.get("X-Headroom-Client", "").lower() == "ios":
            return auth.authorized_mobile(self.headers)
        return auth.authorized(self.headers)

    def _is_mobile_client(self):
        return (
            self.headers.get("X-Headroom-Client", "").lower() == "ios"
            and auth.authorized_mobile(self.headers)
        )

    def _mobile_permission_allowed(self, permission):
        """A paired iOS client gets only explicitly configured capabilities."""
        return (
            self._is_private()
            and self._is_mobile_client()
            and permission in app_config.mobile_permissions()
        )

    def do_GET(self):
        if self._is_browser_cross_origin():
            self._send_json(403, {"ok": False, "error": "cross-site request"})
            return
        split = urllib.parse.urlsplit(self.path)
        path = split.path.rstrip("/")
        if path not in ("", "/usage", "/health", "/setup", "/accounts",
                        "/mobile/permissions", "/github/watch",
                        "/config/git", "/config/vercel", "/config/supabase",
                        "/config/plausible", "/config/posthog",
                        "/config/sentry", "/config/datadog", "/config/axiom",
                        "/config/timezone",
                        "/agents/capabilities", "/agents/config",
                        "/agents/claude/config", "/agents/codex/task",
                        "/agents/tasks",
                        "/machines/config",
                        "/attention/events"):
            self.send_error(404)
            return
        if not self._allowed():
            self._send_json(401, {"ok": False, "error": "token required"})
            return
        if path == "/github/watch":
            # Mac-local configuration, like the token it goes with.
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            self._send_json(200, _github_watch_payload())
            return
        if path in ("/config/git", "/config/vercel", "/config/supabase",
                    "/config/plausible", "/config/posthog",
                    "/config/sentry", "/config/datadog", "/config/axiom",
                    "/config/timezone"):
            # Names folders on this disk and the teams / projects / sites a
            # login can reach — Mac-local, same class as /github/watch.
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            if path == "/config/git":
                self._send_json(200, _git_config_payload())
            elif path == "/config/vercel":
                self._send_json(200, _vercel_config_payload())
            elif path == "/config/supabase":
                self._send_json(200, _supabase_config_payload())
            elif path == "/config/plausible":
                self._send_json(200, _plausible_config_payload())
            elif path == "/config/posthog":
                self._send_json(200, _posthog_config_payload())
            elif path == "/config/sentry":
                self._send_json(200, _sentry_config_payload())
            elif path == "/config/datadog":
                self._send_json(200, _datadog_config_payload())
            elif path == "/config/timezone":
                self._send_json(200, _timezone_config_payload())
            else:
                self._send_json(200, _axiom_config_payload())
            return
        if path == "/accounts":
            # Names folders holding live credentials — Mac-local, like the
            # tokens themselves.
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            self._send_json(200, _accounts_payload())
            return
        if path == "/agents/tasks":
            if not self._is_loopback() and not self._mobile_permission_allowed(
                    "agents"):
                self._send_json(403, {"ok": False, "error": "not allowed"})
                return
            self._send_json(200, agent_gateway.get().task_surface())
            return
        if path == "/agents/codex/task":
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            self._send_json(200, agent_gateway.get().codex_task())
            return
        if path == "/agents/config":
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            self._send_json(200, agent_gateway.get().configuration())
            return
        if path == "/machines/config":
            # Names a folder on this Mac's disk and decides whether this Mac
            # publishes anything. Mac-local, like the credentials settings.
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            self._send_json(200, icloud_sync.configuration())
            return
        if path == "/agents/claude/config":
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            try:
                result = claude_hooks.inspect(port=self.server.server_port)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._send_json(
                    200, {
                        "ok": False,
                        "provider": "claude-code",
                        "state": "error",
                        "installed": False,
                        "question_mode": app_config.agent_question_mode(),
                        "error": str(error),
                    })
                return
            self._send_json(200, {
                "ok": True,
                **result,
                "question_mode": app_config.agent_question_mode(),
            })
            return
        if path == "/mobile/permissions":
            granted = app_config.mobile_permissions()
            self._send_json(200, {
                "ok": True,
                "permissions": {
                    permission: permission in granted
                    for permission in app_config.MOBILE_PERMISSION_ORDER
                },
            })
            return
        if path in ("/agents/capabilities", "/attention/events"):
            if (not self._is_loopback()
                    and not self._mobile_permission_allowed("read")):
                self._send_json(
                    403, {"ok": False, "error": "mobile dashboard access disabled"})
                return
            if path == "/agents/capabilities":
                self._send_json(200, agent_gateway.get().capabilities())
                return
            query = urllib.parse.parse_qs(split.query)
            try:
                state = query.get("state", ["open"])[0]
                limit = int(query.get("limit", ["50"])[0])
                after_raw = query.get("after_ms", [None])[0]
                after_ms = int(after_raw) if after_raw is not None else None
                result = agent_gateway.get().events(
                    state=state, limit=limit, after_ms=after_ms)
            except (ValueError, agent_events.InvalidEvent) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            self._send_json(200, result)
            return
        if path in ("", "/usage") and self._is_mobile_client():
            if not self._mobile_permission_allowed("read"):
                self._send_json(
                    403, {"ok": False, "error": "mobile dashboard access disabled"})
                return
        if path == "/health":
            self._send_json(200, _health_payload())
            return
        if path == "/setup":
            self._send_json(200, {
                "ok": True,
                **sources_config.detection_payload(),
            })
            return
        _note_device(split.query, "wifi")
        view = urllib.parse.parse_qs(split.query).get("view", [""])[0]
        usage, device = _bodies()
        self._send_bytes(200, device if view == "device" else usage)

    def do_POST(self):
        if self._is_browser_cross_origin():
            self._send_json(403, {"ok": False, "error": "cross-site request"})
            return
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        claude_permission = path == "/agents/hooks/claude/permission"
        claude_question = path == "/agents/hooks/claude/question"
        claude_event = path == "/agents/hooks/claude/event"
        event_response_id = None
        prefix = "/attention/events/"
        suffix = "/respond"
        if path.startswith(prefix) and path.endswith(suffix):
            encoded_id = path[len(prefix):-len(suffix)]
            if encoded_id and "/" not in encoded_id:
                event_response_id = urllib.parse.unquote(encoded_id)
        if path not in (
            "/local/stop",
            "/supabase/refresh",
            "/plausible/refresh",
            "/posthog/refresh",
            "/sync/refresh",
            "/device/effect",
            "/sources",
            "/mobile/permissions",
            "/attention/ack",
            "/github/watch",
            "/config/git",
            "/config/vercel",
            "/config/supabase",
            "/config/plausible",
            "/config/posthog",
            "/config/sentry",
            "/config/datadog",
            "/config/axiom",
            "/config/timezone",
            "/accounts",
            "/agents/config",
            "/agents/claude/config",
            "/agents/codex/tasks",
            "/agents/codex/steer",
            "/agents/tasks",
            "/machines/config",
            "/machines/sync",
        ) and event_response_id is None:
            if claude_permission or claude_question or claude_event:
                pass
            else:
                self.send_error(404)
                return
        if not self._allowed():
            self._send_json(401, {"ok": False, "error": "token required"})
            return
        # Sync refresh is LAN-ok so the ESP32 long-press can poke the same
        # pipeline. Paired iOS clients get only the configured private-network
        # control scopes; secrets and provider-specific configuration remain
        # Mac-local.
        if path == "/sync/refresh":
            if not self._is_private():
                self._send_json(403, {"ok": False, "error": "private network only"})
                return
            if (self._is_mobile_client()
                    and not self._mobile_permission_allowed("refresh")):
                self._send_json(
                    403, {"ok": False, "error": "mobile refresh disabled"})
                return
        elif path == "/device/effect":
            if not self._is_private():
                self._send_json(403, {"ok": False, "error": "private network only"})
                return
            if (self._is_mobile_client()
                    and not self._mobile_permission_allowed("refresh")):
                self._send_json(
                    403, {"ok": False, "error": "mobile device control disabled"})
                return
        elif path == "/sources":
            if (not self._is_loopback()
                    and not self._mobile_permission_allowed("sources")):
                self._send_json(403, {"ok": False, "error": "mobile source control disabled"})
                return
        elif path == "/local/stop":
            if (not self._is_loopback()
                    and not self._mobile_permission_allowed("servers")):
                self._send_json(403, {"ok": False, "error": "mobile server control disabled"})
                return
        elif path == "/attention/ack":
            if (not self._is_loopback()
                    and not self._mobile_permission_allowed("read")):
                self._send_json(
                    403, {"ok": False, "error": "mobile dashboard access disabled"})
                return
        elif event_response_id is not None:
            if (not self._is_loopback()
                    and not self._mobile_permission_allowed("agents")):
                self._send_json(
                    403, {"ok": False, "error": "mobile agent control disabled"})
                return
        elif path == "/agents/config":
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
        elif path == "/agents/tasks":
            # Starting work runs a local executable with your words, so it
            # rides the same Mac-granted permission that lets a phone answer
            # an approval — off by default, and never open to the LAN at large.
            if not self._is_loopback() and not self._mobile_permission_allowed(
                    "agents"):
                self._send_json(403, {"ok": False, "error": "not allowed"})
                return
        elif path in ("/agents/claude/config", "/agents/codex/tasks",
                      "/agents/codex/steer"):
            # These start or steer a local executable, so they never answer
            # over the LAN — same rule as /agents/config.
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
        elif claude_permission or claude_question or claude_event:
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
        elif not self._is_loopback():
            self._send_json(403, {"ok": False, "error": "localhost only"})
            return
        if not self.headers.get("Content-Type", "").lower().startswith(
                "application/json"):
            self._send_json(415, {"ok": False, "error": "JSON required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            # A single machine record is a few KB of prefs and stamps, so a
            # handful of peers clears 4096 immediately. Same ceiling as the
            # hook payloads rather than a third number to keep in step.
            bulk = (claude_permission or claude_question or claude_event
                    or path == "/machines/sync")
            max_length = 128 * 1024 if bulk else 4096
            if length <= 0 or length > max_length:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid request"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "JSON object required"})
            return

        if claude_permission:
            try:
                result = agent_gateway.get().claude_permission(payload)
            except agent_events.InvalidEvent as error:
                self._send_json(400, {"error": str(error)})
                return
            # No decision deliberately hands control back to Claude's ordinary
            # local permission dialog.
            self._send_json(200, result or {})
            return

        if path == "/agents/tasks":
            try:
                result = agent_gateway.get().start_task(
                    payload.get("provider"), payload.get("cwd"),
                    payload.get("prompt"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            except (RuntimeError, OSError) as error:
                self._send_json(409, {"ok": False, "error": str(error)})
                return
            self._send_json(200, result)
            return

        if path in ("/agents/codex/tasks", "/agents/codex/steer"):
            gateway = agent_gateway.get()
            try:
                if path == "/agents/codex/tasks":
                    result = gateway.codex_start_task(
                        payload.get("cwd"), payload.get("prompt"))
                else:
                    result = gateway.codex_steer(payload.get("text"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            except RuntimeError as error:
                self._send_json(409, {"ok": False, "error": str(error)})
                return
            self._send_json(200, result)
            return

        if claude_question:
            try:
                result = agent_gateway.get().claude_question(payload)
            except agent_events.InvalidEvent as error:
                self._send_json(400, {"error": str(error)})
                return
            # A deferred decision leaves the question to the Mac.
            self._send_json(200, result or {})
            return

        if claude_event:
            try:
                agent_gateway.get().claude_event(payload)
            except agent_events.InvalidEvent as error:
                self._send_json(400, {"error": str(error)})
                return
            self._send_json(200, {})
            return

        if path == "/agents/claude/config":
            action = payload.get("action")
            try:
                if action == "install":
                    mode = payload.get("question_mode")
                    if isinstance(mode, str):
                        app_config.set_agent_question_mode(mode)
                    result = claude_hooks.install(
                        port=self.server.server_port,
                        question_mode=app_config.agent_question_mode())
                elif action == "uninstall":
                    result = claude_hooks.uninstall(
                        port=self.server.server_port)
                elif action == "test":
                    agent_gateway.get().claude_event({
                        "hook_event_name": "Notification",
                        "session_id": "headroom-claude-test",
                        "cwd": app_config.dev_root(),
                        "notification_type": "idle_prompt",
                        "message": "Claude Code hook test from this Mac",
                    })
                    result = claude_hooks.inspect(
                        port=self.server.server_port)
                else:
                    raise ValueError("unknown Claude hook action")
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            self._send_json(200, {
                "ok": True,
                **result,
                "question_mode": app_config.agent_question_mode(),
            })
            return

        if path == "/agents/config":
            try:
                if "alerts" in payload:
                    app_config.set_agent_alerts(payload["alerts"])
                has_gateway_update = (
                    "enabled" in payload or "codex_binary" in payload
                )
                if has_gateway_update:
                    app_config.set_agent_gateway(
                        enabled=payload.get("enabled"),
                        codex_binary_value=payload.get("codex_binary"),
                    )
                    result = agent_gateway.get().reconfigure()
                else:
                    result = agent_gateway.get().configuration()
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            self._send_json(200, result)
            return

        if path == "/machines/sync":
            # The Mac app hands over the peer records it fetched from CloudKit
            # and gets back the one this Mac should save. It carries bytes; the
            # merge, the whitelist and the winner rule all stay here.
            records = payload.get("records")
            if records is not None and not isinstance(records, list):
                self._send_json(
                    400, {"ok": False, "error": "records must be a list"})
                return
            if not app_config.icloud_sync_enabled():
                self._send_json(
                    409, {"ok": False, "error": "multi-Mac sync is off"})
                return
            try:
                result = icloud_sync.cloud_round(
                    records or [], beacon=_machine_beacon(rollup()))
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            if result.get("adopted"):
                publish()
            self._send_json(200, result)
            return

        if path == "/machines/config":
            try:
                app_config.set_icloud_sync(
                    enabled=payload.get("enabled"),
                    directory=payload.get("directory"),
                )
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            # Run a round now rather than leaving Settings looking broken for
            # up to a minute: switching this on and seeing nothing happen is
            # indistinguishable from it not working.
            try:
                icloud_sync.tick(beacon=_machine_beacon(rollup()))
            except Exception as exc:
                print("multi-mac sync error:", exc, flush=True)
            publish()
            self._send_json(200, icloud_sync.configuration())
            return

        if event_response_id is not None:
            try:
                result = agent_gateway.get().respond(
                    event_response_id,
                    revision=payload.get("revision"),
                    action=payload.get("action"),
                    idempotency_key=payload.get("idempotency_key"),
                    text=payload.get("text"),
                )
            except agent_events.EventNotFound as error:
                self._send_json(404, {"ok": False, "error": str(error)})
                return
            except agent_events.InvalidEvent as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            except agent_events.EventConflict as error:
                self._send_json(409, {"ok": False, "error": str(error)})
                return
            self._send_json(200, result)
            return

        if path == "/attention/ack":
            fingerprint = payload.get("fingerprint")
            current = (rollup().get("attention") or {}).get("fingerprint")
            if (not isinstance(fingerprint, str)
                    or not fingerprint.strip()
                    or fingerprint.strip() != current):
                self._send_json(
                    409, {"ok": False, "error": "attention changed; refresh first"})
                return
            app_config.set_attention_ack_fingerprint(fingerprint)
            publish()
            self._send_json(200, {"ok": True, "fingerprint": fingerprint})
            return

        if path == "/github/watch":
            try:
                app_config.set_github_watch(
                    prefixes=payload.get("owners"),
                    always_repos=payload.get("always_repos"),
                    max_discovered=payload.get("max_discovered"),
                )
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            # The cached run list was fetched for the old repos.
            github_actions.invalidate()
            self._send_json(200, _github_watch_payload())
            return

        if path == "/config/git":
            try:
                app_config.set_git_config(
                    root=payload.get("dev_root"),
                    authors=payload.get("authors"),
                )
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            # Commits and discovered Actions repos both come from dev_root, so
            # moving it strands two caches, not one.
            git_activity.invalidate()
            github_actions.invalidate()
            self._send_json(200, _git_config_payload())
            return

        if path == "/config/vercel":
            try:
                app_config.set_vercel_teams(slugs=payload.get("teams"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            vercel_builds.invalidate()
            self._send_json(200, _vercel_config_payload())
            return

        if path == "/config/supabase":
            try:
                app_config.set_supabase_projects(refs=payload.get("projects"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            supabase_usage.invalidate()
            self._send_json(200, _supabase_config_payload())
            return

        if path == "/config/plausible":
            try:
                if "host" in payload:
                    app_config.set_plausible_host(payload.get("host"))
                app_config.set_plausible_sites(sites=payload.get("sites"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            plausible_usage.invalidate()
            self._send_json(200, _plausible_config_payload())
            return

        if path == "/config/timezone":
            try:
                app_config.set_timezone(payload.get("timezone"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            # Day boundaries move, so every by-day bucket in the cached
            # document is answering the old question until it is rebuilt.
            publish()
            self._send_json(200, _timezone_config_payload())
            return

        if path == "/config/posthog":
            try:
                if "host" in payload:
                    app_config.set_posthog_host(payload.get("host"))
                app_config.set_posthog_projects(projects=payload.get("projects"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            posthog_usage.invalidate()
            self._send_json(200, _posthog_config_payload())
            return

        if path == "/config/sentry":
            try:
                if "org" in payload:
                    app_config.set_sentry_org(payload.get("org"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            sentry_alerts.invalidate()
            self._send_json(200, _sentry_config_payload())
            return

        if path == "/config/datadog":
            try:
                if "site" in payload:
                    app_config.set_datadog_site(payload.get("site"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            datadog_monitors.invalidate()
            self._send_json(200, _datadog_config_payload())
            return

        if path == "/config/axiom":
            try:
                if "host" in payload:
                    app_config.set_axiom_host(payload.get("host"))
                if "org_id" in payload:
                    app_config.set_axiom_org_id(payload.get("org_id"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            axiom_monitors.invalidate()
            self._send_json(200, _axiom_config_payload())
            return

        if path == "/accounts":
            remove = payload.get("remove")
            try:
                if isinstance(remove, str) and remove.strip():
                    if not sources_config.remove_account(remove.strip()):
                        self._send_json(
                            404, {"ok": False, "error": f"no account {remove}"})
                        return
                else:
                    sources_config.add_account(
                        payload.get("provider"),
                        payload.get("label"),
                        payload.get("root"),
                    )
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            except OSError as error:
                self._send_json(
                    500, {"ok": False, "error": f"could not save: {error}"})
                return
            # Answer with the list as stored, then rebuild the registry around
            # it — the client polls /health until the new host is up.
            self._send_json(200, _accounts_payload(restarting=True))
            _restart_host()
            return

        if path == "/mobile/permissions":
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "localhost only"})
                return
            values = payload.get("permissions")
            if not isinstance(values, dict):
                self._send_json(
                    400, {"ok": False, "error": "permissions map required"})
                return
            granted = app_config.set_mobile_permissions(
                permission for permission, enabled in values.items()
                if enabled is True
            )
            self._send_json(200, {
                "ok": True,
                "permissions": {
                    permission: permission in granted
                    for permission in app_config.MOBILE_PERMISSION_ORDER
                },
            })
            return

        if path == "/sources":
            enabled = payload.get("enabled")
            order = payload.get("order")
            integrations_order = payload.get("integrations_order")
            if integrations_order is None:
                # One-release alias from the Activity-only pin.
                integrations_order = payload.get("services_order")
            accents = payload.get("accents")
            titles = payload.get("titles")
            dismissed = payload.get("dismissed")

            def _sources_reply(**extra):
                body = {
                    "ok": True,
                    "enabled": sources_config.enabled_map(),
                    "dismissed": sources_config.dismissed_map(),
                    "order": sources_config.order_ids(),
                    "integrations_order": sources_config.integrations_order_ids(),
                    "focus": sources_config.focus_ids(),
                    "accents": sources_config.accent_overrides(),
                    "titles": sources_config.title_overrides(),
                }
                body.update(extra)
                self._send_json(200, body)

            if dismissed is not None:
                if not isinstance(dismissed, dict):
                    self._send_json(
                        400, {"ok": False, "error": "dismissed map required"})
                    return
                # Before `enabled`, so "back to Active, switched on" — the
                # Library chip tap — lands as un-dismiss then enable, and
                # set_enabled's on-implies-tracked invariant can't be undone
                # by a later dismiss write in the same request.
                sources_config.set_dismissed(dismissed)
                publish()
                if (enabled is None and order is None and accents is None
                        and titles is None and integrations_order is None):
                    _sources_reply()
                    return
            if titles is not None:
                if not isinstance(titles, dict):
                    self._send_json(
                        400, {"ok": False, "error": "titles map required"})
                    return
                try:
                    stored = sources_config.set_titles(titles)
                except ValueError as error:
                    self._send_json(400, {"ok": False, "error": str(error)})
                    return
                publish()
                if (enabled is None and order is None and accents is None
                        and integrations_order is None):
                    _sources_reply(titles=stored)
                    return
            if accents is not None:
                if not isinstance(accents, dict):
                    self._send_json(
                        400, {"ok": False, "error": "accents map required"})
                    return
                try:
                    stored = sources_config.set_accents(accents)
                except ValueError as error:
                    self._send_json(400, {"ok": False, "error": str(error)})
                    return
                # Colors are presentation only — no source needs refetching,
                # but the cached document holds the old ones.
                publish()
                if (enabled is None and order is None and titles is None
                        and integrations_order is None):
                    _sources_reply(accents=stored)
                    return
            if (enabled is None and isinstance(integrations_order, list)
                    and order is None):
                result_integrations = sources_config.set_integrations_order(
                    integrations_order)
                publish()
                _sources_reply(integrations_order=result_integrations)
                return
            if enabled is None and isinstance(order, list):
                # Reorder-only write: don't force clients to resend the map.
                result_order = sources_config.set_order(order)
                if isinstance(integrations_order, list):
                    sources_config.set_integrations_order(integrations_order)
                # The cached document still names the old first three, and
                # `focus` is what the menu bar, the widget and the board's
                # three slots are cut from — a reorder nobody can see for a
                # poll tick reads as one that didn't take.
                publish()
                _sources_reply(order=result_order)
                return
            if not isinstance(enabled, dict):
                self._send_json(400, {"ok": False, "error": "enabled map required"})
                return
            result = sources_config.set_enabled(enabled)
            if isinstance(order, list):
                sources_config.set_order(order)
            if isinstance(integrations_order, list):
                sources_config.set_integrations_order(integrations_order)
            publish()
            # Kick a refresh so ESP32/Mac see the change quickly.
            _refresh_async([sid for sid, on in result.items() if on])
            _sources_reply(enabled=result)
            return

        if path in ("/supabase/refresh", "/plausible/refresh",
                    "/posthog/refresh", "/sync/refresh"):
            wanted = payload.get("sources")
            # Named refreshes (Integrations Connect / Refresh) may target a
            # source that is still in Library; a blanket sync must not.
            explicit = isinstance(wanted, list) and bool(wanted)
            if path == "/supabase/refresh":
                wanted = ["supabase"]
                explicit = True
            elif path == "/plausible/refresh":
                wanted = ["plausible"]
                explicit = True
                if "range" in payload:
                    try:
                        range_id = app_config.set_plausible_range(
                            payload.get("range"))
                    except ValueError as error:
                        self._send_json(400, {
                            "ok": False, "error": str(error),
                        })
                        return
                    # Drop the in-memory TTL so the new window is fetched now.
                    plausible_usage._cache.update(t=0.0, data=None)
                    self._send_json(202, {
                        "ok": True,
                        "sources": wanted,
                        "range": range_id,
                        "range_label": app_config.plausible_range_label(range_id),
                    })
                    _refresh_async(wanted, require_enabled=False)
                    return
            elif path == "/posthog/refresh":
                wanted = ["posthog"]
                explicit = True
                if "range" in payload:
                    try:
                        range_id = app_config.set_posthog_range(
                            payload.get("range"))
                    except ValueError as error:
                        self._send_json(400, {
                            "ok": False, "error": str(error),
                        })
                        return
                    posthog_usage._cache.update(t=0.0, data=None)
                    self._send_json(202, {
                        "ok": True,
                        "sources": wanted,
                        "range": range_id,
                        "range_label": app_config.posthog_range_label(range_id),
                    })
                    _refresh_async(wanted, require_enabled=False)
                    return
            elif not explicit:
                wanted = list(sources_config.SOURCE_IDS)
            wanted = [
                sid for sid in wanted
                if sid in sources_config.BY_ID
            ]
            # Settings refresh is the user action that clears a sticky Keychain
            # Deny. Warmup and the poller must not — a KeepAlive respawn would
            # otherwise undo Deny and pop SecurityAgent again.
            for sid in wanted:
                if sid == "claude" or sid.startswith("claude:"):
                    source = sources_config.BY_ID.get(sid)
                    oauth_usage.rearm_keychain(
                        None if source is None else source.account)
                elif sid == "zed":
                    zed_usage.rearm_keychain()
            _refresh_async(wanted, require_enabled=not explicit)
            self._send_json(202, {"ok": True, "sources": wanted})
            return

        if path == "/device/effect":
            try:
                effect = trigger_device_effect(
                    payload.get("effect", "reset"), payload.get("provider"))
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            # Materialize immediately so the board needs only one normal poll.
            publish()
            self._send_json(202, {"ok": True, "effect": effect})
            return

        result = local_servers.stop_server(
            payload.get("pid"), payload.get("port"))
        if result.get("ok"):
            time.sleep(0.2)
            _refresh_one("local", force=True)
            publish()
            self._send_json(200, result)
        else:
            self._send_json(409, result)

    def log_message(self, *args):
        pass  # quiet; this is a desk gadget, not a web server


def _refresh_one(source_id, force=False):
    """Fetch one source and store it. Never raises."""
    source = sources_config.get(source_id)
    if source is None:
        return
    try:
        payload = source.fetch(force=force)
    except Exception as exc:
        print(f"{source_id} error:", exc)
        return
    with _lock:
        _state[source_id] = payload
        _source_times[source_id] = time.time()
    if payload.get("ok"):
        # A stale replay still reads `ok`, so logging only the numbers hides
        # why they stopped moving. That is not cosmetic: a rate limit never
        # appeared in this log at all, and reading the bare `stale` marker as
        # ordinary staleness sent a whole debugging session the wrong way.
        note = f"  stale: {payload.get('error')}" if payload.get("stale") else ""
        print(f"{source_id:9s} ok  {source.summary(payload)}{note}")
    else:
        print(f"{source_id:9s} miss:", payload.get("error"))


def _observe_burn():
    try:
        with _lock:
            payloads = {
                sid: dict(_state[sid])
                for sid in sources_config.BURN_SOURCE_IDS
            }
        today_burn = daily_burn.observe(payloads=payloads, tz=_local_tz())
        parts = "  ".join(
            f"{sid}={today_burn.get(sid)}%"
            for sid in sources_config.BURN_SOURCE_IDS
        )
        print("burn today " + parts)
    except Exception as exc:
        print("daily_burn error:", exc)


def _sample_quotas():
    """Append raw (t, pct) samples — the series behind burndown + forecast.

    Separate from _observe_burn: that accumulates %-points per calendar day,
    this keeps the intra-window shape those daily totals can't reconstruct.
    """
    try:
        with _lock:
            state = {sid: dict(_state[sid])
                     for sid in sources_config.BURN_SOURCE_IDS}
        rows = quota_samples.record(state)
        if rows:
            print("sampled " + "  ".join(
                f"{row['provider']}.{row['pool']}={row['pct']}%"
                for row in rows))
    except Exception as exc:
        print("quota_samples error:", exc)


def _refresh_selected(sources, force=False, require_enabled=True):
    """Refresh the given sources in parallel.

    The poller and a blanket sync respect `enabled` so Library / paused rows
    stay quiet. An explicit Integrations refresh (`require_enabled=False`)
    still fetches — otherwise a Keychain-connected source that was never
    promoted to Active keeps serving the blank "not connected" payload.
    """
    enabled = sources_config.enabled_map()
    wanted = [
        sid for sid in sources
        if sid in sources_config.BY_ID
        and (not require_enabled or enabled.get(sid, True))
    ]
    if not wanted:
        return
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(wanted))) as pool:
        list(pool.map(
            lambda sid: _refresh_one(sid, force=force),
            wanted,
        ))
    if any(sid in sources_config.BURN_SOURCE_IDS for sid in wanted):
        _observe_burn()
        _sample_quotas()
    publish()


def _refresh_async(sources, force=True, require_enabled=True):
    threading.Thread(
        target=_refresh_selected,
        kwargs={
            "sources": list(sources),
            "force": force,
            "require_enabled": require_enabled,
        },
        daemon=True,
    ).start()


def _rotate_logs():
    """Keep LaunchAgent logs from growing forever, and keep them private.

    launchd creates these 0644. They carry repo names, branches and local
    server paths, so every tick also narrows them to the owner — which
    retroactively closes older installs that logged more than they should.
    """
    folder = os.path.expanduser("~/.headroom/logs")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    limit = 5 * 1024 * 1024
    for name in ("headroom.log", "headroom.err", "headroom.log.1",
                 "headroom.err.1"):
        path = os.path.join(folder, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if size < limit or name.endswith(".1"):
            continue
        try:
            os.replace(path, path + ".1")
        except OSError:
            pass


def _local_health_ok(port, timeout=0.4):
    """True when something on this Mac already answers GET /health."""
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def _poller(interval):
    """One loop, driven by each source's own poll_s from the registry."""
    # Warmup already forced a full pass, so start the clocks now rather than
    # refetching everything on the first tick. A small per-source phase keeps
    # identical poll_s values (and two Macs that restarted together) from
    # sync-firing the same upstream on every wake.
    started = time.time()
    last_run = {
        source.id: started - random.uniform(
            0, max(1.0, source.poll_s * 0.15))
        for source in sources_config.SOURCES
    }
    last_rotate = started
    while True:
        try:
            scan()
        except Exception as exc:  # keep the daemon alive across odd records
            print("scan error:", exc)

        now = time.time()
        due = [
            source.id for source in sources_config.SOURCES
            if now - last_run[source.id] >= source.poll_s
        ]
        for sid in due:
            last_run[sid] = now
        if due:
            _refresh_selected(due)
        else:
            publish()   # keep `updated` and pace marks moving

        if now - last_rotate >= LOG_ROTATE_S:
            last_rotate = now
            _rotate_logs()
        time.sleep(interval)


def _sync_loop():
    """Publish this Mac to the shared folder and read the other Macs back.

    Its own thread rather than a step in `_poller`: this touches iCloud Drive,
    where a read can block on a download that has not finished. The poll loop
    is what keeps the menu bar and the board current, and it has no business
    waiting on another machine's file.
    """
    while True:
        try:
            result = icloud_sync.tick(beacon=_machine_beacon(rollup()))
            if result.get("adopted"):
                # Settings arriving from another Mac change what this one
                # polls and how it is painted, so the document has to be
                # rebuilt before anyone reads it again.
                publish()
                print(
                    f"multi-mac: adopted {len(result['adopted'])} setting(s) "
                    f"from {result['peers']} peer(s)",
                    flush=True,
                )
        except Exception as exc:
            print("multi-mac sync error:", exc, flush=True)
        time.sleep(icloud_sync.TICK_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8737)
    ap.add_argument("--interval", type=int, default=15,
                    help="seconds between log rescans")
    ap.add_argument("--exit-with-pid", type=int, default=0,
                    help="exit when this pid goes away; 0 disables. Set by "
                         "Headroom.app when it owns the host lifecycle "
                         "instead of launchd (see parent_watch.py)")
    args = ap.parse_args()

    # Unbuffered logs under LaunchAgent redirects.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    # Before the scan, not after. Bootstrapping a large ~/.claude tree takes
    # long enough that an app quitting during it would leave the orphan this
    # flag exists to prevent. Same skip-finalize reasoning as _shutdown below.
    if args.exit_with_pid > 0:
        def _exit_with_parent():
            print(f"parent {args.exit_with_pid} is gone — exiting", flush=True)
            try:
                if _bonjour is not None:
                    _bonjour.terminate()
            except Exception:
                pass
            os._exit(0)

        parent_watch.start(args.exit_with_pid, _exit_with_parent)
        print(f"Lifecycle owned by pid {args.exit_with_pid}", flush=True)

    _rotate_logs()
    print(f"Bootstrapping from {LOG_ROOT} ...", flush=True)
    t0 = time.time()
    scan()
    with _lock:
        n = len(_buckets)
    print(f"  {n} active minutes in the last 7 days "
          f"({time.time()-t0:.1f}s)", flush=True)

    def _warmup():
        print("Refreshing enabled sources ...", flush=True)
        try:
            _refresh_selected(sources_config.SOURCE_IDS, force=True)
        except Exception as exc:
            print("warmup error:", exc, flush=True)

    def _backfill_history():
        """One resumable pass over every session log, off the startup path.

        First run against a large ~/.claude tree takes a while, so it never
        blocks serving; after that unchanged files cost a stat each.
        """
        try:
            result = claude_history.backfill(
                tz=_local_tz(),
                log=lambda line: print(line, flush=True),
            )
            if result.get("error"):
                print("history backfill error:", result["error"], flush=True)
                return
            summary = claude_history.summary(days=30)
            if summary:
                print(
                    f"history: {result['scanned']} files in "
                    f"{result['elapsed_s']:.1f}s — "
                    f"{summary['active_days']} active days since "
                    f"{summary['first_day']}, "
                    f"{summary['total_tokens'] / 1e6:.1f}M tokens, "
                    f"${summary['total_cost_usd']:.2f}",
                    flush=True,
                )
            else:
                print(f"history: no usage found under {claude_history.LOG_ROOT}",
                      flush=True)
            publish()
        except Exception as exc:
            print("history backfill error:", exc, flush=True)

    publish()

    # Bind before any daemon threads. A failed bind with threads already
    # printing to stdout aborts inside Py_FinalizeEx (LaunchAgent crash loop).
    ThreadingHTTPServer.allow_reuse_address = True
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.EADDRINUSE:
            # Another healthy host already owns the port — exit 0 so KeepAlive
            # does not thrash. That only holds because the LaunchAgent asks for
            # KeepAlive/SuccessfulExit=false; a plain KeepAlive=true ignores the
            # status and respawns anyway. Otherwise leave a non-zero for retry.
            if _local_health_ok(args.port):
                print(
                    f"port {args.port} already serving /health — nothing to do",
                    flush=True,
                )
                sys.exit(0)
            print(f"port {args.port} in use and not healthy: {exc}", flush=True)
            sys.exit(1)
        raise

    # Gateway before any printing daemon threads. A raise here under threads
    # that already write stdout aborts inside Py_FinalizeEx (LaunchAgent loop).
    try:
        agent_gateway.get().start()
    except Exception as exc:
        print(f"agent gateway failed to start: {exc}", flush=True)

    threading.Thread(target=_backfill_history, daemon=True).start()
    threading.Thread(target=_warmup, daemon=True).start()
    threading.Thread(target=_poller, args=(args.interval,), daemon=True).start()
    threading.Thread(target=_sync_loop, daemon=True).start()

    def _usb_get_usage():
        # The cable is slow: hand the board its trimmed view, not the full doc.
        return _bodies()[1]

    def _usb_sync_refresh():
        _refresh_async(sources_config.SOURCE_IDS)

    if usb_bridge.enabled():
        threading.Thread(
            target=usb_bridge.run,
            kwargs={
                "get_usage": _usb_get_usage,
                "on_sync_refresh": _usb_sync_refresh,
                "on_device": lambda query: _note_device(query, "usb"),
            },
            daemon=True,
        ).start()
        print("USB CDC fallback enabled — /dev/cu.usbmodem*", flush=True)
    else:
        print(
            "USB CDC fallback disabled — Wi-Fi is the default "
            f"(set {usb_bridge.ENABLE_ENV}=1 to enable)",
            flush=True,
        )

    global _bonjour
    bonjour = _bonjour = _advertise_bonjour(args.port)
    # Materialize the dedicated iOS credential without printing it to logs.
    auth.mobile_token()

    def _shutdown(_signum, _frame):
        # launchd SIGTERM. Do not call srv.shutdown() here: that waits for
        # serve_forever on this same thread and deadlocks. Do not raise
        # SystemExit either — daemon threads keep printing into stdout and
        # Py_FinalizeEx aborts (LaunchAgent crash loop). Skip finalize.
        try:
            if bonjour is not None:
                bonjour.terminate()
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    print(f"Serving usage JSON on http://0.0.0.0:{args.port}/usage", flush=True)
    print(f"  device view:  http://0.0.0.0:{args.port}/usage?view=device",
          flush=True)
    print(f"  health check: http://127.0.0.1:{args.port}/health", flush=True)
    if auth.required():
        # Never print the value: launchd sends stdout to
        # ~/.headroom/logs/headroom.log, and troubleshooting means people paste
        # that log into issues. Same reason the mobile token stays quiet above.
        auth.token()
        print(f"LAN clients need the host token from {auth.TOKEN_PATH}:",
              flush=True)
        print(f"  cat {auth.TOKEN_PATH}", flush=True)
        print("  put it in firmware/src/config.h as HOST_TOKEN", flush=True)
    else:
        print("require_auth is off — /usage is open to the whole network",
              flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        agent_gateway.get().stop()
        if bonjour is not None and bonjour.poll() is None:
            bonjour.terminate()


if __name__ == "__main__":
    main()
