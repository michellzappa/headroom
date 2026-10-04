"""Local usage study: how a person actually uses their coding agents, from the logs.

Everything here runs on this Mac, over ~/.claude/projects and the Codex
session logs under ~/.codex. Nothing is served on
/usage, stored, or sent. It answers two questions:

  1. What does my own usage look like? (`profile` → `report`)
  2. What is the exact, coarse summary that would leave this Mac if I chose to
     contribute? (`payload`)

The second one is printed on purpose. A privacy claim you cannot inspect is a
promise; a payload you can read before anything is sent is a fact.

Two rules shape the code:

  * Prompt *text* never leaves the parse loop. `_prompt_tokens` returns a
    length and nothing else, and the profile holds counters, not strings.
  * The payload is coarse by construction: shares in 5% steps, wide bins, no
    counts, no dates finer than an ISO week. Exact counts across a 24-hour
    histogram are close to a fingerprint for one person. A share that rounds to
    zero is dropped, so a rare habit cannot single someone out.

Reading a log line and deciding a line repeats the one before it stay in
claude_history; this module calls them rather than keeping a second copy.

Stdlib only.

    python3 host/usage_study.py             # report + payload preview
    python3 host/usage_study.py --payload   # payload JSON only
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
from datetime import datetime
from glob import glob

import agent_logs
import claude_history
import pricing

SCHEMA = "study-1"

FAMILIES = ("opus", "sonnet", "haiku", "fable")
OTHER = "other"

# Codex writes one rollout file per session. `archived_sessions` is where the
# app moves a session you archive; a file is in one place or the other.
CODEX_ROOTS = (os.path.expanduser("~/.codex/sessions"),
               os.path.expanduser("~/.codex/archived_sessions"))
# A Codex model id becomes its own family. This is what a card will accept as
# one, so a hand-made log cannot put arbitrary text on a friend's card.
CODEX_FAMILY = re.compile(r"^[a-z0-9][a-z0-9.-]{0,31}$")
# Codex's approval reviewer. It runs beside your session, so its turns count as
# sub-agent turns, like Claude's sidechains.
CODEX_REVIEWER = "codex-auto-review"
# User messages Codex injects itself. What is left is what a person typed.
CODEX_INJECTED = ("<", "# AGENTS.md instructions",
                  "The following is the Codex agent history")

# A gap between two log records longer than this is idle time, not work. Wall
# clock from first to last record measured a resumed session as 887 hours.
IDLE_CAP_MIN = 10

# Prompt length is an estimate: characters // 4. There is no tokenizer here.
CHARS_PER_TOKEN = 4

# Lower bounds of the coarse bins the payload uses.
PROMPT_EDGES = (1, 8, 32, 128, 512, 2048)
OUT_EDGES = (1, 16, 128, 512, 2048)
SESSION_EDGES = (0, 8, 32, 128, 512)

SHARE_STEP = 0.05
WEEK_SHARE_STEP = 0.1
PAYLOAD_WEEKS = 8

TIME_BLOCKS = (
    ("night_00_05", 0, 6),
    ("morning_06_11", 6, 12),
    ("afternoon_12_17", 12, 18),
    ("evening_18_23", 18, 24),
)


def model_family(model):
    """'claude-fable-5-1' -> 'fable'. A name it does not know stays 'other'."""
    parts = (model or "").lower().split("-")
    if len(parts) > 1 and parts[0] == "claude" and parts[1] in FAMILIES:
        return parts[1]
    return OTHER


def codex_family(model):
    """'gpt-6-luna' -> 'gpt-6-luna'. Anything not shaped like a model id is 'other'."""
    name = (model or "").strip().lower()
    return name if CODEX_FAMILY.match(name) and name not in FAMILIES \
        and name != OTHER else OTHER


def provider_of(family):
    """Which tool a family came from: 'claude', 'codex', one of
    agent_logs.TOOLS, or 'other'.

    Codex families are bare model ids for compatibility with cards made
    before other tools were read; every other tool prefixes its own name
    ('opencode.claude-sonnet-4-5'), so two tools running one model stay apart.
    """
    if family in FAMILIES:
        return "claude"
    if family == OTHER:
        return "other"
    head = family.split(".", 1)[0]
    if "." in family and head in agent_logs.TOOLS:
        return head
    return "codex"


def bucket(n):
    """Log2 bin label: 0, 1, 2-3, 4-7, 8-15 ..."""
    if n <= 0:
        return "0"
    lo = 1 << int(math.log2(n))
    return str(lo) if lo == 1 else f"{lo}-{2 * lo - 1}"


def coarse(hist, edges):
    """Merge a log2 histogram into wide bins keyed by their lower bound."""
    out = collections.Counter()
    for label, count in hist.items():
        lo = int(label.split("-")[0])
        out[max((e for e in edges if e <= lo), default=edges[0])] += count
    return out


def shares(counter, step=SHARE_STEP):
    """Counts -> shares rounded to `step`. A bin that rounds to zero is dropped.

    Each share is rounded on its own, so a set can sum to a little over or under
    one. Show it as approximate.
    """
    total = sum(counter.values())
    if not total:
        return {}
    rounded = {str(k): round(round(v / total / step) * step, 2)
               for k, v in counter.items()}
    return {k: v for k, v in rounded.items() if v > 0}


def _ts(rec):
    stamp = rec.get("timestamp")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _prompt_tokens(rec):
    """(estimated tokens, first 200 chars) of a user record, or None.

    None for a tool result, which is the agent talking to itself. The head is
    returned only so `is_human_prompt` can look at it; callers must not keep it.
    """
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return len(content) // CHARS_PER_TOKEN, content[:200]
    if not isinstance(content, list):
        return None
    if any(isinstance(b, dict) and b.get("type") == "tool_result"
           for b in content):
        return None
    text = "".join(b.get("text", "") for b in content
                   if isinstance(b, dict) and b.get("type") == "text")
    return len(text) // CHARS_PER_TOKEN, text[:200]


def is_human_prompt(rec, head):
    """True for something a person typed.

    Claude Code stamps `origin.kind` on user records: `human`, `task-notification`,
    `peer` (another session). Older logs have no origin, so there the rule is
    negative: not a meta or compact-summary record, and not injected markup.
    """
    origin = rec.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return False
    return not head.lstrip().startswith("<")


def _tool_names(rec):
    for block in (rec.get("message") or {}).get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = str(block.get("name") or "?")
            yield "mcp" if name.startswith("mcp__") else name


def _blank():
    return {
        "turns": 0, "sidechain_turns": 0,
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        "cost_usd": 0.0,
        "out_by_family": collections.Counter(),
        "all_by_family": collections.Counter(),
        "out_by_month": collections.defaultdict(collections.Counter),
        "out_by_week": collections.defaultdict(collections.Counter),
        "daily_tokens": collections.Counter(),
        "prompt_hist": collections.Counter(),
        "out_hist": collections.Counter(),
        "hours": collections.Counter(),
        "weekdays": collections.Counter(),
        "tools": collections.Counter(),
        # Output tokens by family that have no rates, so the cost estimate can
        # say what it left out instead of pricing it at a guess.
        "unpriced": collections.Counter(),
        "sessions": [],
    }


def _scan_file(path, tz, prof):
    deduper = claude_history.MessageDeduper()
    first = last = None
    active = 0.0
    s_turns = s_prompts = 0
    try:
        handle = open(path, "r", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue

            when = _ts(rec)
            if when:
                if last:
                    gap = (when - last).total_seconds() / 60
                    if 0 < gap <= IDLE_CAP_MIN:
                        active += gap
                first = first or when
                last = when

            kind = rec.get("type")
            if kind == "user" and not rec.get("isSidechain"):
                got = _prompt_tokens(rec)
                if got and got[0] and is_human_prompt(rec, got[1]):
                    prof["prompt_hist"][bucket(got[0])] += 1
                    s_prompts += 1
                continue

            if kind != "assistant" or not deduper.accept(rec):
                continue
            parsed = claude_history.usage_from_record(rec)
            if parsed is None:
                continue
            t, model, inp, out, cache_read, w5, w1h, cost = parsed
            if str(model).startswith("<"):
                continue                      # <synthetic>: never hit the API
            try:
                local = datetime.fromtimestamp(t, tz)
            except (OverflowError, OSError, ValueError):
                continue

            _book(prof, local, model_family(model), inp, out, cache_read,
                  w5 + w1h, cost, sidechain=bool(rec.get("isSidechain")))
            for name in _tool_names(rec):
                prof["tools"][name] += 1
            s_turns += 1

    if first and s_turns:
        prof["sessions"].append(
            {"active_min": active, "turns": s_turns, "prompts": s_prompts})


def _book(prof, local, fam, inp, out, cache_read, cache_write, cost,
          sidechain=False):
    """Add one model response to the profile. Shared by both log formats."""
    everything = inp + out + cache_read + cache_write
    year, week, _ = local.isocalendar()
    prof["turns"] += 1
    prof["input"] += inp
    prof["output"] += out
    prof["cache_read"] += cache_read
    prof["cache_write"] += cache_write
    prof["cost_usd"] += cost
    prof["out_by_family"][fam] += out
    prof["all_by_family"][fam] += everything
    prof["out_by_month"][local.strftime("%Y-%m")][fam] += out
    prof["out_by_week"][f"{year}-W{week:02d}"][fam] += out
    prof["daily_tokens"][local.date().isoformat()] += everything
    prof["out_hist"][bucket(out)] += 1
    prof["hours"][local.hour] += 1
    prof["weekdays"][local.weekday()] += 1
    if sidechain:
        prof["sidechain_turns"] += 1


_CODEX_FIELDS = ("input_tokens", "cached_input_tokens",
                 "cache_write_input_tokens", "output_tokens")


def _codex_usage(info, prev):
    """This response's usage from a `token_count` event, or None.

    Codex logs running totals per session and repeats an event now and then,
    so the delta from the previous total is the response. A total that went
    down means the session was compacted and the count restarted; then the
    event's own `last_token_usage` is the response.
    """
    total = info.get("total_token_usage")
    if not isinstance(total, dict):
        return None, prev
    if prev is not None:
        delta = {k: _int(total.get(k)) - _int(prev.get(k)) for k in _CODEX_FIELDS}
        if all(v >= 0 for v in delta.values()):
            return (delta if any(delta.values()) else None), total
    last = info.get("last_token_usage")
    if not isinstance(last, dict):
        return None, total
    return {k: _int(last.get(k)) for k in _CODEX_FIELDS}, total


def _int(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value < 0:
        return 0
    return int(value)


def _codex_text(payload):
    parts = payload.get("content")
    if not isinstance(parts, list):
        return ""
    return "".join(b.get("text", "") for b in parts
                   if isinstance(b, dict) and isinstance(b.get("text"), str))


def _scan_codex_file(path, tz, prof):
    """One Codex rollout file into the profile.

    OpenAI counts cached input inside `input_tokens` and reasoning inside
    `output_tokens`, so fresh input is input minus the cached and written
    parts, and output is taken as logged.
    """
    model = None
    prev = None
    first = last = None
    active = 0.0
    s_turns = s_prompts = 0
    try:
        handle = open(path, "r", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue

            when = _ts(rec)
            if when:
                if last:
                    gap = (when - last).total_seconds() / 60
                    if 0 < gap <= IDLE_CAP_MIN:
                        active += gap
                first = first or when
                last = when

            kind = rec.get("type")
            if kind == "turn_context":
                if isinstance(payload.get("model"), str):
                    model = payload["model"]
                continue
            if kind == "response_item":
                ptype = payload.get("type")
                if ptype == "message" and payload.get("role") == "user":
                    text = _codex_text(payload)
                    head = text.lstrip("\n")
                    if text and not head.startswith(CODEX_INJECTED):
                        tokens = len(text) // CHARS_PER_TOKEN
                        if tokens:
                            prof["prompt_hist"][bucket(tokens)] += 1
                            s_prompts += 1
                elif ptype in ("function_call", "custom_tool_call"):
                    name = str(payload.get("name") or "?")
                    space = str(payload.get("namespace") or "")
                    prof["tools"][
                        "mcp" if space.startswith("mcp__")
                        or name.startswith("mcp__") else name] += 1
                continue
            if kind != "event_msg" or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            usage, prev = _codex_usage(info, prev)
            if usage is None or when is None:
                continue
            try:
                local = when.astimezone(tz)
            except (OverflowError, OSError, ValueError):
                continue
            cached = usage["cached_input_tokens"]
            written = usage["cache_write_input_tokens"]
            fresh = max(0, usage["input_tokens"] - cached - written)
            out = usage["output_tokens"]
            fam = codex_family(model)
            cost = pricing.openai_cost_usd(
                model, input_tokens=fresh + written, cached_input=cached,
                output_tokens=out)
            if cost is None:
                prof["unpriced"][fam] += out
                cost = 0.0
            _book(prof, local, fam, fresh, out, cached, written, cost,
                  sidechain=model == CODEX_REVIEWER)
            s_turns += 1

    if first and s_turns:
        prof["sessions"].append(
            {"active_min": active, "turns": s_turns, "prompts": s_prompts})


def _price(model, fresh, cache_read, cache_write, output):
    """USD for one response from another tool, or None when unpriced.

    Claude models price from the Claude table only when it knows them, and
    OpenAI models from the OpenAI table; anything else (Gemini, Qwen, Kimi)
    is left unpriced and named, never priced at a guess.
    """
    name = str(model or "").lower().rsplit("/", 1)[-1]
    if name.startswith("claude-") and pricing.is_known(name):
        return pricing.cost_usd(name, input_tokens=fresh,
                                output_tokens=output, cache_read=cache_read,
                                cache_write_5m=cache_write)
    return pricing.openai_cost_usd(name, input_tokens=fresh + cache_write,
                                   cached_input=cache_read,
                                   output_tokens=output)


class Sink:
    """Where agent_logs reports. Turns tool records into profile counts."""

    def __init__(self, prof, tz):
        self.prof = prof
        self.tz = tz
        self._turns = collections.Counter()
        self._prompts = collections.Counter()

    def turn(self, tool, model, when, *, fresh, cache_read, cache_write,
             output, session, sidechain=False):
        try:
            local = when.astimezone(self.tz)
        except (OverflowError, OSError, ValueError):
            return
        fam = agent_logs.tool_family(tool, model)
        cost = _price(model, fresh, cache_read, cache_write, output)
        if cost is None:
            self.prof["unpriced"][fam] += output
            cost = 0.0
        _book(self.prof, local, fam, fresh, output, cache_read, cache_write,
              cost, sidechain=sidechain)
        self._turns[session] += 1

    def prompt(self, text, *, session):
        tokens = len(text or "") // CHARS_PER_TOKEN
        if tokens:
            self.prof["prompt_hist"][bucket(tokens)] += 1
            self._prompts[session] += 1

    def tool(self, name):
        name = str(name)
        self.prof["tools"]["mcp" if name.startswith("mcp__") else name] += 1

    def session(self, key, stamps):
        if not self._turns[key]:
            return
        active = 0.0
        ordered = sorted(stamps)
        for before, after in zip(ordered, ordered[1:]):
            gap = (after - before).total_seconds() / 60
            if 0 < gap <= IDLE_CAP_MIN:
                active += gap
        self.prof["sessions"].append(
            {"active_min": active, "turns": self._turns[key],
             "prompts": self._prompts[key]})


def _codex_paths(roots):
    """Every rollout file under `roots`, once each by file name."""
    seen = {}
    for root in roots or ():
        for path in glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            seen.setdefault(os.path.basename(path), path)
    return sorted(seen.values())


def profile(root=None, tz=None, codex_roots=(), agent_roots=None):
    """Fold every session file into one profile. None if empty.

    `root` is the Claude Code log tree. `codex_roots` are Codex session trees,
    empty by default so a caller that names a Claude tree reads nothing else;
    the service passes CODEX_ROOTS. `agent_roots` maps each of
    agent_logs.TOOLS to its log root, likewise empty unless passed.
    """
    root = root or claude_history.LOG_ROOT
    tz = tz or datetime.now().astimezone().tzinfo
    prof = _blank()
    paths = sorted(glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
    for path in paths:
        _scan_file(path, tz, prof)
    codex = _codex_paths(codex_roots)
    for path in codex:
        _scan_codex_file(path, tz, prof)
    agent_files = 0
    sink = Sink(prof, tz)
    for tool, tool_root in (agent_roots or {}).items():
        if tool in agent_logs.TOOLS:
            agent_files += len(agent_logs.files(tool, tool_root))
            agent_logs.scan(tool, tool_root, sink)
    if not prof["turns"]:
        return None
    prof["files"] = len(paths) + len(codex) + agent_files
    return prof


SHARD_VERSION = 1
SHARD_MAX_BYTES = 8 * 1024 * 1024

_SCALARS = ("turns", "sidechain_turns", "input", "output", "cache_read",
            "cache_write", "files")
_COUNTERS = ("out_by_family", "all_by_family", "daily_tokens", "prompt_hist",
             "out_hist", "hours", "weekdays", "tools", "unpriced")
_NESTED = ("out_by_month", "out_by_week")
_INT_KEYED = ("hours", "weekdays")


def to_shard(prof, machine, now, name=None):
    """One Mac's profile as plain JSON, keyed by that Mac.

    A shard holds *counts*, which is what makes two Macs addable. It is for
    moving between your own machines and never goes into a card: exact counts
    are the fingerprint the payload is built to avoid. `now` is an ISO string;
    the caller supplies it so tests need no clock.
    """
    shard = {"version": SHARD_VERSION, "machine": str(machine),
             "generated": now,
             "cost_usd": round(prof["cost_usd"], 4)}
    if name:
        shard["name"] = str(name)[:64]     # what a person calls this Mac
    for key in _SCALARS:
        shard[key] = prof.get(key, 0)
    for key in _COUNTERS:
        shard[key] = {str(k): v for k, v in prof[key].items()}
    for key in _NESTED:
        shard[key] = {k: dict(v) for k, v in prof[key].items()}
    shard["sessions"] = [[round(s["active_min"], 1), s["turns"], s["prompts"]]
                         for s in prof["sessions"]]
    return shard


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value < 0:
        raise ValueError("count must be a non-negative number")
    return value


def from_shard(shard):
    """Validate a shard from another Mac and return it as a profile.

    A shard arrives by file today and by sync later, so it is input. A wrong
    type raises ValueError instead of being coerced into a wrong total.
    """
    if not isinstance(shard, dict) or shard.get("version") != SHARD_VERSION:
        raise ValueError("not a version-%d shard" % SHARD_VERSION)
    if not isinstance(shard.get("machine"), str) or not shard["machine"]:
        raise ValueError("shard has no machine id")
    prof = _blank()
    prof["cost_usd"] = float(_count(shard.get("cost_usd", 0)))
    for key in _SCALARS:
        prof[key] = _count(shard.get(key, 0))
    for key in _COUNTERS:
        raw = shard.get(key, {})
        if not isinstance(raw, dict):
            raise ValueError(key + " must be an object")
        for k, v in raw.items():
            prof[key][int(k) if key in _INT_KEYED else k] = _count(v)
    for key in _NESTED:
        raw = shard.get(key, {})
        if not isinstance(raw, dict):
            raise ValueError(key + " must be an object")
        for outer, inner in raw.items():
            if not isinstance(inner, dict):
                raise ValueError(key + " rows must be objects")
            for k, v in inner.items():
                prof[key][outer][k] = _count(v)
    for row in shard.get("sessions", []):
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError("bad session row")
        prof["sessions"].append({"active_min": float(_count(row[0])),
                                 "turns": _count(row[1]),
                                 "prompts": _count(row[2])})
    return prof


def merge(shards):
    """Add the profiles of several Macs into one. None when all are empty.

    Usage on two Macs is disjoint sessions, so unlike a provider quota there is
    nothing to double count and the honest merge is a sum. One shard per Mac:
    if a machine appears twice, the newer `generated` stamp wins, so importing
    a stale copy of a Mac's shard cannot inflate anything.
    """
    newest = {}
    for shard in shards:
        seen = newest.get(shard["machine"])
        if seen is None or str(shard["generated"]) > str(seen["generated"]):
            newest[shard["machine"]] = shard
    out = _blank()
    out["files"] = 0
    out["machines"] = len(newest)
    for shard in newest.values():
        part = from_shard(shard)
        for key in _SCALARS + ("cost_usd",):
            out[key] += part[key]
        for key in _COUNTERS:
            out[key].update(part[key])
        for key in _NESTED:
            for outer, inner in part[key].items():
                out[key][outer].update(inner)
        out["sessions"].extend(part["sessions"])
    return out if out["turns"] else None


def combined(machine, now, peer_paths=(), root=None, tz=None):
    """This Mac's live scan merged with shards read from `peer_paths`.

    The live scan replaces any older shard of this same Mac found among them.
    """
    shards = []
    own = profile(root=root, tz=tz)
    if own is not None:
        shards.append(to_shard(own, machine, now))
    for path in peer_paths:
        if os.path.getsize(path) > SHARD_MAX_BYTES:
            raise ValueError(f"{path}: larger than {SHARD_MAX_BYTES} bytes")
        with open(path) as handle:
            shards.append(json.load(handle))
    return merge(shards)


def payload(prof):
    """The coarse summary that would leave this Mac. Shares, never counts."""
    blocks = collections.Counter()
    for hour, count in prof["hours"].items():
        for name, lo, hi in TIME_BLOCKS:
            if lo <= hour < hi:
                blocks[name] += count
    sessions = collections.Counter(
        bucket(int(s["active_min"])) for s in prof["sessions"])
    everything_in = prof["input"] + prof["cache_read"] + prof["cache_write"]
    weeks = sorted(prof["out_by_week"])[-PAYLOAD_WEEKS:]
    return {
        "schema": SCHEMA,
        "prompt_len_share": shares(coarse(prof["prompt_hist"], PROMPT_EDGES)),
        "out_per_turn_share": shares(coarse(prof["out_hist"], OUT_EDGES)),
        "time_of_day_share": shares(blocks),
        "session_active_min_share": shares(coarse(sessions, SESSION_EDGES)),
        "cache_hit_bucket_pct": (
            int(100 * prof["cache_read"] / everything_in) // 10 * 10
            if everything_in else None),
        "weekly_model_share": {
            week: shares(prof["out_by_week"][week], WEEK_SHARE_STEP)
            for week in weeks},
    }


def _pct(ordered, p):
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


WEEKLY_POINTS = 26


def insights(prof):
    """Everything the local view draws, as plain JSON.

    This is the person's own data on their own Mac, so it carries real counts.
    It is never a card and never leaves the Mac. `report` renders it as text;
    the Mac app draws it. One computation, so the two cannot disagree.
    """
    days = sorted(prof["daily_tokens"])
    daily = sorted(prof["daily_tokens"].values())
    minutes = sorted(s["active_min"] for s in prof["sessions"])
    turns = sorted(s["turns"] for s in prof["sessions"])
    everything_in = prof["input"] + prof["cache_read"] + prof["cache_write"]
    total_all = sum(prof["all_by_family"].values())
    total_out = prof["output"]
    # Every family that produced tokens, Claude's in tier order first, then
    # Codex models by output, then other.
    tools = ("codex",) + agent_logs.TOOLS
    codex = [f for tool in tools for f in sorted(
        (f for f in prof["all_by_family"] if provider_of(f) == tool),
        key=lambda f: (-prof["out_by_family"][f], f))]
    families = list(FAMILIES) + codex + [OTHER]
    return {
        "machines": prof.get("machines", 1),
        "files": prof.get("files", 0),
        "turns": prof["turns"],
        "sessions": len(prof["sessions"]),
        "active_days": len(days),
        "first_day": days[0],
        "last_day": days[-1],
        "tokens": {"input": prof["input"], "output": prof["output"],
                   "cache_read": prof["cache_read"],
                   "cache_write": prof["cache_write"]},
        "cache_hit_pct": (round(100 * prof["cache_read"] / everything_in, 1)
                          if everything_in else None),
        "output_per_1k_input": (round(1000 * prof["output"] / everything_in, 1)
                                if everything_in else None),
        "cost_usd_estimate": round(prof["cost_usd"], 2),
        # The older of the two tables, so the caption never claims fresher
        # rates than the stalest one used.
        "rates_checked": (min(pricing.RATES_CHECKED,
                              pricing.OPENAI_RATES_CHECKED)
                          if codex else pricing.RATES_CHECKED),
        "unpriced_models": sorted(f for f, n in prof["unpriced"].items() if n),
        "models": [
            {"family": fam,
             "provider": provider_of(fam),
             "output_share": (prof["out_by_family"][fam] / total_out
                              if total_out else 0.0),
             "total_share": prof["all_by_family"][fam] / total_all}
            for fam in families if prof["all_by_family"][fam]],
        "monthly": [
            {"month": month,
             "output": {f: prof["out_by_month"][month][f]
                        for f in families if prof["out_by_month"][month][f]}}
            for month in sorted(prof["out_by_month"])],
        "weekly": [
            {"week": week, "output": sum(prof["out_by_week"][week].values())}
            for week in sorted(prof["out_by_week"])[-WEEKLY_POINTS:]],
        "daily_tokens": {"median": _pct(daily, .5), "p90": _pct(daily, .9),
                         "max": daily[-1]},
        "hours": [prof["hours"][h] for h in range(24)],
        "weekdays": [prof["weekdays"][d] for d in range(7)],
        "prompt_len": [
            {"bin": label, "count": prof["prompt_hist"][label]}
            for label in sorted(prof["prompt_hist"],
                                key=lambda k: int(k.split("-")[0]))],
        "session": {
            "median_min": _pct(minutes, .5), "p90_min": _pct(minutes, .9),
            "longest_min": minutes[-1],
            "median_turns": _pct(turns, .5), "p90_turns": _pct(turns, .9)},
        "subagent_share": prof["sidechain_turns"] / prof["turns"],
        "tools": [{"name": k, "count": v}
                  for k, v in prof["tools"].most_common(10)],
    }


def report(prof):
    """Human-readable text. Local only. Dollars are estimates and say so."""
    d = insights(prof)
    lines = []
    add = lines.append
    if d["machines"] > 1:
        add(f"combined from {d['machines']} Macs")
    add(f"files {d['files']}  turns {d['turns']:,}  sessions {d['sessions']:,}"
        f"  active days {d['active_days']}  {d['first_day']} .. {d['last_day']}")
    t = d["tokens"]
    add(f"\nTOKENS  fresh-in {t['input']:,}  cache-write {t['cache_write']:,}"
        f"  cache-read {t['cache_read']:,}  out {t['output']:,}")
    if d["cache_hit_pct"] is not None:
        add(f"cache hit rate (read / all input): {d['cache_hit_pct']}%")
        add(f"output per 1k input tokens: {d['output_per_1k_input']}")
    add(f"estimated API-equivalent cost: ${d['cost_usd_estimate']:,.0f}  "
        f"(estimate; rates as of {d['rates_checked']})")

    add("\nMODEL MIX  share of output / share of all tokens")
    for m in d["models"]:
        add(f"  {m['family']:7s} {m['output_share']:6.1%}   "
            f"{m['total_share']:6.1%}")
    add("\nMONTHLY output tokens")
    for row in d["monthly"]:
        add(f"  {row['month']}  " + "  ".join(
            f"{f} {n / 1e6:6.2f}M" for f, n in row["output"].items()))
    q = d["daily_tokens"]
    add(f"\nDAILY total tokens  median {q['median'] / 1e6:.1f}M  "
        f"p90 {q['p90'] / 1e6:.1f}M  max {q['max'] / 1e6:.1f}M")

    peak = max(d["hours"])
    add("\nHOUR OF DAY (turns, local time)")
    for hour, n in enumerate(d["hours"]):
        add(f"  {hour:02d} {'#' * int(40 * n / peak):40s} {n:,}")
    names = "Mon Tue Wed Thu Fri Sat Sun".split()
    add("\nDAY OF WEEK  " + "  ".join(
        f"{names[i]} {n / d['turns']:.0%}" for i, n in enumerate(d["weekdays"])))
    add("\nHUMAN PROMPT LENGTH (estimated tokens)")
    for row in d["prompt_len"]:
        add(f"  {row['bin']:>9s} {row['count']:6,}")

    s = d["session"]
    add(f"\nSESSIONS (active time, gaps over {IDLE_CAP_MIN} min excluded)  "
        f"median {s['median_min']:.0f} min  p90 {s['p90_min']:.0f} min  "
        f"longest {s['longest_min'] / 60:.1f} h;  median turns "
        f"{s['median_turns']}  p90 {s['p90_turns']}")
    add(f"sub-agent turns: {d['subagent_share']:.1%}")
    add("\nTOP TOOLS  " + ", ".join(
        f"{r['name']} {r['count']:,}" for r in d["tools"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=None,
                        help="log directory (default ~/.claude/projects)")
    parser.add_argument("--payload", action="store_true",
                        help="print only the payload JSON")
    parser.add_argument("--export-shard", metavar="PATH",
                        help="write this Mac's counts, for merging on another Mac")
    parser.add_argument("--merge", metavar="SHARD", nargs="+", default=[],
                        help="add shards exported from your other Macs")
    args = parser.parse_args(argv)

    import machine_identity
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    machine = machine_identity.machine_id()
    if args.export_shard:
        own = profile(root=args.root)
        if own is None:
            print(f"no usage found under {args.root or claude_history.LOG_ROOT}")
            return 1
        with open(args.export_shard, "w") as handle:
            json.dump(to_shard(own, machine, now,
                               machine_identity.display_name()), handle,
                      separators=(",", ":"), sort_keys=True)
        os.chmod(args.export_shard, 0o600)
        print(f"wrote {args.export_shard}  (counts; keep it between your own Macs)")
        return 0

    prof = combined(machine, now, args.merge, root=args.root)
    if prof is None:
        print(f"no usage found under {args.root or claude_history.LOG_ROOT}")
        return 1
    if not args.payload:
        print(report(prof))
        print("\n--- PAYLOAD PREVIEW (what would be uploaded; nothing is sent) ---")
    print(json.dumps(payload(prof), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
