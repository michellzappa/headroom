"""Token usage from the logs of other coding agents, for the usage study.

Claude Code and Codex are read in usage_study.py. This module reads five
more, each from the format its own open-source code writes. The formats were
taken from upstream source on 2026-10-04, at these commits:

  * OpenCode    anomalyco/opencode 907b3bc5 (v1.18.34): SQLite, opencode*.db
  * Gemini CLI  google-gemini/gemini-cli fb972b2f (v0.62.0): chats/*.jsonl
  * Qwen Code   QwenLM/qwen-code 1fb5a715: projects/*/chats/*.jsonl
  * Kimi CLI    MoonshotAI/kimi-cli 9ab1286b (1.52.0): sessions/*/*/wire.jsonl
  * Goose       block/goose 591edd47 (v1.53.0): sessions.db, usage_ledger

These parsers were written from source and tested against fixtures in that
shape, not against logs from a real install. So each one is defensive: a
record it does not understand is skipped, never guessed at, and a whole file
that fails to parse costs that file and nothing else. See docs/product.md.

Closed-source agents (Copilot CLI, Factory Droid, Amp) are not here: their
format is known only from third-party trackers, not from the vendor. Crush
keeps only a running cost, and Cursor keeps no tokens on disk at all.

Each scanner reports through a `sink` (usage_study.Sink), so this module does
no bookkeeping of its own. Token counts reach the sink already normalised:
`fresh` input without any cached part, `cache_read`, `cache_write`, and
`output` with reasoning included, the same convention as the Codex parser.

Stdlib only.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

TOOLS = ("opencode", "gemini", "qwen", "kimi", "goose")

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_MODEL_CHARS = re.compile(r"[^a-z0-9.-]+")


def tool_family(tool, model):
    """('opencode', 'anthropic/claude-sonnet-4-5-20250929') ->
    'opencode.claude-sonnet-4-5'.

    The tool goes in front because two tools can run the same model, and the
    study keeps them apart. A card accepts at most 32 characters of
    [a-z0-9.-], so the model part is cleaned and cut to fit.
    """
    # "anthropic/claude-sonnet-4-5": the provider prefix says where the
    # model ran, which the tool prefix already covers, and it costs room.
    raw = str(model or "").strip().lower().rsplit("/", 1)[-1]
    name = _MODEL_CHARS.sub("-", raw).strip("-.")
    name = _DATE_SUFFIX.sub("", name) or "unknown"
    room = 32 - len(tool) - 1
    return f"{tool}.{name[:room].rstrip('-.') or 'unknown'}"


def default_roots(env=None):
    """Where each tool keeps its logs on macOS, honouring each tool's env var."""
    env = os.environ if env is None else env
    home = os.path.expanduser("~")
    data = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    goose_root = env.get("GOOSE_PATH_ROOT")
    return {
        "opencode": os.path.join(data, "opencode"),
        "gemini": os.path.join(env.get("GEMINI_CLI_HOME") or home, ".gemini",
                               "tmp"),
        "qwen": os.path.join(env.get("QWEN_HOME")
                             or os.path.join(home, ".qwen"), "projects"),
        "kimi": os.path.join(env.get("KIMI_SHARE_DIR")
                             or os.path.join(home, ".kimi"), "sessions"),
        "goose": (os.path.join(goose_root, "data", "sessions") if goose_root
                  else os.path.join(data, "goose", "sessions")),
    }


def files(tool, root):
    """The files a scan reads, for the study's change fingerprint."""
    if not root or not os.path.isdir(root):
        return []
    pattern = {
        "opencode": "opencode*.db",
        "gemini": os.path.join("*", "chats", "**", "*.json*"),
        "qwen": os.path.join("*", "chats", "*.jsonl"),
        "kimi": os.path.join("*", "*", "wire.jsonl"),
        "goose": "sessions.db",
    }[tool]
    paths = glob.glob(os.path.join(root, pattern), recursive=True)
    return sorted(p for p in paths
                  if not p.endswith(("-wal", "-shm", "-journal")))


def scan(tool, root, sink):
    """Read every log `tool` keeps under `root` into `sink`."""
    reader = {"opencode": _opencode, "gemini": _gemini, "qwen": _qwen,
              "kimi": _kimi, "goose": _goose}[tool]
    for path in files(tool, root):
        try:
            reader(path, sink)
        except (OSError, ValueError, sqlite3.Error):
            continue          # one unreadable file costs that file only


# ------------------------------------------------------------ helpers


def _int(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value < 0:
        return 0
    return int(value)


def _iso(text):
    if not isinstance(text, str) or not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _epoch(seconds):
    try:
        return datetime.fromtimestamp(float(seconds), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _jsonl(path):
    with open(path, "r", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(rec, dict):
                yield rec


def _text(parts):
    """Text out of a Gemini-style Parts array, a string, or None."""
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    return "".join(p.get("text", "") for p in parts
                   if isinstance(p, dict) and isinstance(p.get("text"), str))


def _readonly(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


# ----------------------------------------------------------- OpenCode


def _opencode(path, sink):
    """opencode*.db. `message.data` is the message JSON; `part` holds text
    and tool calls; `session.parent_id` marks a subagent.

    `tokens.input` excludes the cache and `output` excludes reasoning
    (session.ts getUsage). OpenCode rewrites a message while it streams, so
    only completed assistant messages count.
    """
    con = _readonly(path)
    try:
        cur = con.cursor()
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "message" not in tables:
            return
        children = set()
        if "session" in tables:
            children = {r[0] for r in cur.execute(
                "SELECT id FROM session WHERE parent_id IS NOT NULL")}
        user_ids = {}
        sessions = {}
        for message_id, session_id, data in cur.execute(
                "SELECT id, session_id, data FROM message"):
            try:
                msg = json.loads(data)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(msg, dict):
                continue
            created = (msg.get("time") or {}).get("created")
            when = _epoch(created / 1000) if isinstance(
                created, (int, float)) else None
            if when is None:
                continue
            sessions.setdefault(session_id, []).append(when)
            if msg.get("role") == "user":
                user_ids[message_id] = session_id
                continue
            if msg.get("role") != "assistant":
                continue
            tokens = msg.get("tokens")
            if not isinstance(tokens, dict) or not (msg.get("time") or {}) \
                    .get("completed"):
                continue
            cache = tokens.get("cache") if isinstance(
                tokens.get("cache"), dict) else {}
            model = msg.get("modelID") or (msg.get("model") or {}).get("id")
            sink.turn("opencode", model, when,
                      fresh=_int(tokens.get("input")),
                      cache_read=_int(cache.get("read")),
                      cache_write=_int(cache.get("write")),
                      output=_int(tokens.get("output"))
                      + _int(tokens.get("reasoning")),
                      session=("opencode", path, session_id),
                      sidechain=session_id in children)
        if "part" in tables:
            for message_id, data in cur.execute(
                    "SELECT message_id, data FROM part"):
                try:
                    part = json.loads(data)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool" and part.get("tool"):
                    sink.tool(part["tool"])
                elif (part.get("type") == "text" and message_id in user_ids
                      and not part.get("synthetic")):
                    sink.prompt(part.get("text") or "",
                                session=("opencode", path,
                                         user_ids[message_id]))
        for session_id, stamps in sessions.items():
            sink.session(("opencode", path, session_id), stamps)
    finally:
        con.close()


# --------------------------------------------------------- Gemini CLI


def _gemini(path, sink):
    """chats/session-*.jsonl, or the older single-document .json.

    A record is written again when its tokens arrive, so the last record for
    each id wins, and `$rewindTo` drops everything after a message. `input`
    includes `cached`; `output` excludes `thoughts`.
    """
    records = {}
    order = []
    sidechain = False
    if path.endswith(".json"):
        with open(path, "r", errors="replace") as handle:
            doc = json.load(handle)
        lines = doc.get("messages", []) if isinstance(doc, dict) else []
        sidechain = isinstance(doc, dict) and doc.get("kind") == "subagent"
    else:
        lines = _jsonl(path)
    for rec in lines:
        if not isinstance(rec, dict):
            continue
        if "sessionId" in rec and "type" not in rec:
            sidechain = rec.get("kind") == "subagent"
            continue
        if "$rewindTo" in rec:
            target = rec.get("$rewindTo")
            if target in records:
                keep = order[:order.index(target) + 1]
                for gone in order[len(keep):]:
                    records.pop(gone, None)
                order = keep
            continue
        if any(k.startswith("$") for k in rec):
            continue          # metadata updates; nothing that counts
        rid = rec.get("id")
        if not isinstance(rid, str):
            continue
        if rid not in records:
            order.append(rid)
        records[rid] = rec
    stamps = []
    key = ("gemini", path)
    for rid in order:
        rec = records[rid]
        when = _iso(rec.get("timestamp"))
        if when is None:
            continue
        stamps.append(when)
        kind = rec.get("type")
        if kind == "user":
            sink.prompt(_text(rec.get("content")), session=key)
            continue
        if kind != "gemini":
            continue
        for call in rec.get("toolCalls") or []:
            if isinstance(call, dict) and call.get("name"):
                sink.tool(call["name"])
        tokens = rec.get("tokens")
        if not isinstance(tokens, dict):
            continue
        cached = _int(tokens.get("cached"))
        sink.turn("gemini", rec.get("model"), when,
                  fresh=max(0, _int(tokens.get("input")) - cached),
                  cache_read=cached, cache_write=0,
                  output=_int(tokens.get("output"))
                  + _int(tokens.get("thoughts")),
                  session=key, sidechain=sidechain)
    sink.session(key, stamps)


# ---------------------------------------------------------- Qwen Code


def _qwen(path, sink):
    """projects/<cwd>/chats/<session>.jsonl, one ChatRecord per line.

    `/branch` copies records with the same uuid, so a uuid counts once.
    `promptTokenCount` includes the cached part.
    """
    seen = set()
    stamps = []
    key = ("qwen", path)
    for rec in _jsonl(path):
        uid = rec.get("uuid")
        if uid in seen:
            continue
        if uid is not None:
            seen.add(uid)
        when = _iso(rec.get("timestamp"))
        if when is None:
            continue
        stamps.append(when)
        kind = rec.get("type")
        message = rec.get("message") if isinstance(
            rec.get("message"), dict) else {}
        if kind == "user":
            provenance = rec.get("provenance")
            if rec.get("subtype") or provenance not in (None, "real_user"):
                continue
            sink.prompt(_text(message.get("parts")), session=key)
            continue
        if kind != "assistant":
            continue
        for part in message.get("parts") or []:
            call = part.get("functionCall") if isinstance(part, dict) else None
            if isinstance(call, dict) and call.get("name"):
                sink.tool(call["name"])
        usage = rec.get("usageMetadata")
        if not isinstance(usage, dict):
            continue
        cached = _int(usage.get("cachedContentTokenCount"))
        sink.turn("qwen", rec.get("model"), when,
                  fresh=max(0, _int(usage.get("promptTokenCount")) - cached),
                  cache_read=cached, cache_write=0,
                  output=_int(usage.get("candidatesTokenCount"))
                  + _int(usage.get("thoughtsTokenCount")),
                  session=key, sidechain=bool(rec.get("isSidechain")))
    sink.session(key, stamps)


# ----------------------------------------------------------- Kimi CLI


def _kimi(path, sink):
    """sessions/<workdir>/<session>/wire.jsonl. Each `StatusUpdate` carries
    one step's `token_usage`, input without the cache. The file names no
    model, so every step is booked as Kimi's.
    """
    stamps = []
    key = ("kimi", path)
    for rec in _jsonl(path):
        when = _epoch(rec.get("timestamp"))
        message = rec.get("message") if isinstance(
            rec.get("message"), dict) else {}
        if when is None:
            continue
        stamps.append(when)
        if message.get("type") != "StatusUpdate":
            continue
        payload = message.get("payload") if isinstance(
            message.get("payload"), dict) else {}
        usage = payload.get("token_usage")
        if not isinstance(usage, dict):
            continue
        sink.turn("kimi", "kimi", when,
                  fresh=_int(usage.get("input_other")),
                  cache_read=_int(usage.get("input_cache_read")),
                  cache_write=_int(usage.get("input_cache_creation")),
                  output=_int(usage.get("output")),
                  session=key, sidechain=False)
    sink.session(key, stamps)


# -------------------------------------------------------------- Goose


def _goose(path, sink):
    """sessions.db `usage_ledger`, one row per model call (Goose 1.5x+).

    Whether `input_tokens` includes the cache is not stated in the source
    read, so a row is taken as including it only when it is large enough
    to: input below cache read + write means the counts are separate.
    """
    con = _readonly(path)
    try:
        cur = con.cursor()
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "usage_ledger" not in tables:
            return
        children = set()
        if "sessions" in tables:
            try:
                children = {r[0] for r in cur.execute(
                    "SELECT id FROM sessions WHERE session_type = 'sub_agent' "
                    "OR parent_session_id IS NOT NULL")}
            except sqlite3.Error:
                children = set()
        stamps = {}
        for row in cur.execute(
                "SELECT session_id, created_timestamp, model, input_tokens, "
                "output_tokens, cache_read_tokens, cache_write_tokens "
                "FROM usage_ledger"):
            session_id, created, model, inp, out, read, write = row
            when = _epoch(created)
            if when is None:
                continue
            inp, read, write = _int(inp), _int(read), _int(write)
            fresh = inp - read - write if inp >= read + write else inp
            sink.turn("goose", model, when, fresh=fresh, cache_read=read,
                      cache_write=write, output=_int(out),
                      session=("goose", path, session_id),
                      sidechain=session_id in children)
            stamps.setdefault(session_id, []).append(when)
        for session_id, whens in stamps.items():
            sink.session(("goose", path, session_id), whens)
    finally:
        con.close()
