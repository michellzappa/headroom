"""Long-range Claude usage history, backfilled from the session logs on disk.

The live server keeps 7 days of minute buckets in memory, because that is all
the rolling windows need and a cold start has to be fast. But ~/.claude/projects
holds *months* of the same JSONL, and throwing it away means a fresh install has
no history until it has been running for a fortnight.

So this does one resumable pass over every session file, aggregates per local
day, and persists the result. After that it is incremental: a file whose size
and mtime are unchanged is never reopened, which is what keeps the recurring
cost near zero against a 600MB tree.

What a day record answers, none of which the quota APIs expose:
  - token mix (input / output / cache read / cache write) and cost
  - which models did the work
  - active minutes, and how those clustered into sessions

Note the ceiling: this is *token* history, not *quota-percent* history. The
percentages come from Anthropic's API, which only ever reports right now, so
this cannot retroactively draw a burndown curve. It can say how hard a given
day was, which is what makes a day-one forecast better than a guess.

Claude only — Codex and Cursor have no local log to read. Stdlib only.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from glob import glob

import pricing

STORE_PATH = os.path.expanduser("~/.headroom/claude_history.json")
LOG_ROOT = os.path.expanduser("~/.claude/projects")

# A gap longer than this between active minutes starts a new session.
SESSION_GAP_S = 30 * 60
# Keep the store bounded; well past any window worth charting.
RETENTION_DAYS = 400
# 2: one assistant message is billed once, not once per content block. Bumping
# discards stores written under the inflated count and rebuilds them.
SCHEMA_VERSION = 2

_lock = threading.Lock()
_state = None
_status = {"running": False, "done": False, "files": 0, "scanned": 0,
           "elapsed_s": 0.0, "error": None}


def usage_from_record(rec):
    """One assistant log record → (t, model, in, out, cache_read, w5, w1h, cost).

    Single source of truth for reading a Claude JSONL usage line: the live
    server's minute bucketing and this backfill both go through it, so pricing
    and the cache-token breakdown can't drift between them.
    """
    if not isinstance(rec, dict):
        return None
    msg = rec.get("message") or {}
    usage = msg.get("usage")
    if not usage:
        return None

    stamp = rec.get("timestamp")
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None

    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    creation = usage.get("cache_creation") or {}
    w5 = creation.get("ephemeral_5m_input_tokens", 0) or 0
    w1h = creation.get("ephemeral_1h_input_tokens", 0) or 0
    if not (w5 or w1h):
        w5 = usage.get("cache_creation_input_tokens", 0) or 0

    model = msg.get("model") or "unknown"
    cost = pricing.cost_usd(
        model, input_tokens=inp, output_tokens=out,
        cache_read=cache_read, cache_write_5m=w5, cache_write_1h=w1h,
    )
    return (t, model, inp, out, cache_read, w5, w1h, cost)


class MessageDeduper:
    """Collapse the several JSONL lines Claude Code writes per assistant message.

    One assistant message becomes one line per content block — thinking, text,
    and each tool_use — and every line repeats the *same* `message.usage`, the
    same `message.id` and the same `requestId`. Counting each line as an API
    call inflates tokens and cost by the block count: x1.83 measured over 120
    real session files, 2,560 of 4,298 messages emitting more than one record.

    The blocks of a message are written consecutively, so remembering only the
    previous id is enough to catch every repeat. That keeps this O(1) per file
    — which is what lets the live tail hold one of these per open session for
    the life of the process, and lets a message straddle a read boundary
    without escaping the check.

    Records with no `message.id` are always counted. Subagent runs carry their
    own distinct ids and are genuinely separate API calls, so they survive.

    `usage_from_record()` stays stateless and stays the single source of truth
    for *reading* a line; this is the single source of truth for deciding
    whether a line is a repeat of the one before it. Both callers use both.
    """

    __slots__ = ("_last",)

    def __init__(self):
        self._last = None

    def accept(self, rec):
        """True if this record is a new message; False if it repeats the last."""
        if not isinstance(rec, dict):
            return True
        message_id = (rec.get("message") or {}).get("id")
        if message_id is None:
            return True
        if message_id == self._last:
            return False
        self._last = message_id
        return True


def _blank_day():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "cost_usd": 0.0, "active_minutes": 0, "sessions": 0,
            "by_model": {}}


def _default_state():
    return {"version": SCHEMA_VERSION, "days": {}, "files": {}}


def _load():
    try:
        with open(STORE_PATH) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return _default_state()  # schema bump re-scans rather than guesses
    days = data.get("days") if isinstance(data.get("days"), dict) else {}
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    return {"version": SCHEMA_VERSION, "days": days, "files": files}


def _save(state):
    folder = os.path.dirname(STORE_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(state, handle, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, STORE_PATH)


def _state_locked():
    global _state
    if _state is None:
        _state = _load()
    return _state


def _scan_file(path, tz, days, minutes):
    """Fold one session file into `days` / `minutes`. Streams line by line."""
    deduper = MessageDeduper()
    try:
        with open(path, "r", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not deduper.accept(rec):
                    continue
                parsed = usage_from_record(rec)
                if parsed is None:
                    continue
                t, model, inp, out, cache_read, w5, w1h, cost = parsed
                try:
                    key = datetime.fromtimestamp(t, tz).date().isoformat()
                except (OverflowError, OSError, ValueError):
                    continue
                row = days.setdefault(key, _blank_day())
                row["input"] += inp
                row["output"] += out
                row["cache_read"] += cache_read
                row["cache_write"] += w5 + w1h
                row["cost_usd"] += cost
                total = inp + out + cache_read + w5 + w1h
                row["by_model"][model] = row["by_model"].get(model, 0) + total
                minutes.setdefault(key, set()).add(int(t) // 60)
    except OSError:
        return False
    return True


def _finalise(days, minutes):
    """Derive active minutes and session counts from the minute sets."""
    for key, mins in minutes.items():
        row = days.get(key)
        if row is None:
            continue
        ordered = sorted(mins)
        row["active_minutes"] = len(ordered)
        sessions = 1 if ordered else 0
        for prev, cur in zip(ordered, ordered[1:]):
            if (cur - prev) * 60 > SESSION_GAP_S:
                sessions += 1
        row["sessions"] = sessions
        row["cost_usd"] = round(row["cost_usd"], 4)


def _prune(days):
    if len(days) <= RETENTION_DAYS:
        return days
    keep = sorted(days)[-RETENTION_DAYS:]
    return {key: days[key] for key in keep}


def status():
    with _lock:
        return dict(_status)


def backfill(tz=None, force=False, log=None):
    """Scan every session file not already consumed. Returns a status dict.

    Safe to call repeatedly: unchanged files are skipped by (size, mtime), so
    after the first pass this costs a stat per file.
    """
    started = time.time()
    tz = tz or datetime.now().astimezone().tzinfo
    with _lock:
        if _status["running"]:
            return dict(_status)
        _status.update(running=True, error=None)

    try:
        with _lock:
            state = dict(_state_locked())
        days = {key: dict(value) for key, value in (state.get("days") or {}).items()}
        for row in days.values():
            row["by_model"] = dict(row.get("by_model") or {})
        ledger = dict(state.get("files") or {})
        if force:
            days, ledger = {}, {}

        paths = sorted(glob(os.path.join(LOG_ROOT, "**", "*.jsonl"),
                            recursive=True))
        # Rebuilding a day means rebuilding it from every file that touches it,
        # so a partial rescan would double-count. Any changed file therefore
        # forces the affected days to be recomputed from scratch.
        changed = []
        for path in paths:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            fingerprint = [int(stat.st_size), int(stat.st_mtime)]
            if ledger.get(path) != fingerprint:
                changed.append((path, fingerprint))

        if not changed:
            with _lock:
                _status.update(running=False, done=True, files=len(paths),
                               scanned=0, elapsed_s=time.time() - started)
                return dict(_status)

        # Simplest correct approach at this size: recompute everything when
        # anything changed. 600MB takes under two minutes and it only happens
        # on the first run or when old sessions are edited.
        days, minutes = {}, {}
        scanned = 0
        new_ledger = {}
        for path in paths:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if _scan_file(path, tz, days, minutes):
                new_ledger[path] = [int(stat.st_size), int(stat.st_mtime)]
                scanned += 1
                if log and scanned % 250 == 0:
                    log(f"  history: {scanned}/{len(paths)} files")

        _finalise(days, minutes)
        days = _prune(days)
        state = {"version": SCHEMA_VERSION, "days": days, "files": new_ledger}
        _save(state)
        with _lock:
            globals()["_state"] = state
            _status.update(running=False, done=True, files=len(paths),
                           scanned=scanned, elapsed_s=time.time() - started)
            return dict(_status)
    except Exception as exc:      # a backfill must never take down the host
        with _lock:
            _status.update(running=False, error=str(exc),
                           elapsed_s=time.time() - started)
            return dict(_status)


def series(days=30):
    """Oldest→newest day rows, most recent `days` that have data."""
    with _lock:
        stored = dict(_state_locked().get("days") or {})
    keys = sorted(stored)[-days:]
    out = []
    for key in keys:
        row = stored[key]
        out.append({
            "date": key,
            "input": row.get("input", 0),
            "output": row.get("output", 0),
            "cache_read": row.get("cache_read", 0),
            "cache_write": row.get("cache_write", 0),
            "total": (row.get("input", 0) + row.get("output", 0)
                      + row.get("cache_read", 0) + row.get("cache_write", 0)),
            "cost_usd": round(float(row.get("cost_usd", 0.0)), 4),
            "active_minutes": row.get("active_minutes", 0),
            "sessions": row.get("sessions", 0),
        })
    return out


def summary(days=30):
    """Headline stats over the trailing window. None when there is no history."""
    rows = series(days=days)
    active = [row for row in rows if row["total"] > 0]
    if not active:
        return None

    total_tokens = sum(row["total"] for row in active)
    cache_read = sum(row["cache_read"] for row in active)
    fresh_input = sum(row["input"] for row in active)
    denominator = cache_read + fresh_input
    with _lock:
        stored = dict(_state_locked().get("days") or {})

    by_model = {}
    for key in sorted(stored)[-days:]:
        for model, tokens in (stored[key].get("by_model") or {}).items():
            by_model[model] = by_model.get(model, 0) + tokens

    return {
        "days_covered": len(rows),
        "active_days": len(active),
        "first_day": rows[0]["date"],
        "last_day": rows[-1]["date"],
        "total_tokens": total_tokens,
        "total_cost_usd": round(sum(row["cost_usd"] for row in active), 2),
        "avg_tokens_per_active_day": int(total_tokens / len(active)),
        "avg_cost_per_active_day": round(
            sum(row["cost_usd"] for row in active) / len(active), 2),
        "avg_sessions_per_active_day": round(
            sum(row["sessions"] for row in active) / len(active), 1),
        "avg_active_minutes": int(
            sum(row["active_minutes"] for row in active) / len(active)),
        # Share of read context served from cache. Low means sessions keep
        # rebuilding context from cold instead of building on it.
        "cache_hit_pct": (round(100.0 * cache_read / denominator, 1)
                          if denominator else None),
        "top_models": sorted(
            ({"model": m, "tokens": t} for m, t in by_model.items()),
            key=lambda row: -row["tokens"],
        )[:4],
        # Models in this window that pricing.py has no rates for, so their
        # share of the cost above came from the Sonnet-tier fallback. Almost
        # always means a model shipped and the table has not caught up.
        #
        # It is a list rather than a flag because the names are the fix: they
        # are exactly what someone needs to add to BASE. Empty is the normal
        # case and the one worth being able to see.
        #
        # Zero-token entries are excluded, and that is not tidying. Claude Code
        # logs a `<synthetic>` model for injected messages that never hit the
        # API; it carries no tokens, so it costs nothing at any rate, and
        # listing it would report a pricing problem that does not exist. A
        # model only matters here if it actually spent something.
        "unpriced_models": sorted(
            m for m, t in by_model.items() if t and not pricing.is_known(m)),
    }


def reset_for_tests():
    global _state
    with _lock:
        _state = None
        _status.update(running=False, done=False, files=0, scanned=0,
                       elapsed_s=0.0, error=None)
