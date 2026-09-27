"""Raw quota sample log — the time series behind burndown + forecasting.

`daily_burn` answers "how many points did I burn on Tuesday". That is the wrong
instrument for an intra-window burndown, for three reasons: it is daily
granularity (a weekly window gives 7 stair steps), it is keyed by calendar date
while the plan windows roll from an arbitrary start, and it detects a window
reset only to re-baseline, throwing the boundary away.

So this module keeps the primitive both of those need: (t, pct) samples per
pool, tagged with the window they belong to. Everything downstream — the
burndown chart, time-to-exhaustion, pace deltas, per-window history — is a
query over this file rather than new collection.

Storage is append-only JSONL at ~/.headroom/quota_samples.jsonl, downsampled to
one row per pool per BUCKET_S. At 5-minute buckets that is ~7 rows/interval and
~28k rows over the retention window, a few MB. Appending beats rewriting a JSON
blob every tick, and the file stays greppable.

Window identity is derived, not stored by the API. The anchor is the *reset
instant* — `window_end = now + resets_in_s` — rather than the start, because
the end is the one moment the source actually reports and the one the UI
prints. Deriving the start from a held end (`end - window_s`) is what keeps a
chart's axis and its "6d 23h to reset" caption from disagreeing; anchoring the
other way round lets them drift apart by days.

In practice the sources report `resets_in_s` loosely enough that a fresh
reading is not stable at all, so a derived end that lands *later* than the
held one only replaces it once a full window has passed — see `window_for`.
Wake-cache freezes walk the derived end forward a poll at a time; holding
against those is what keeps the axis from forking. An end that lands
*earlier* is not that failure mode, so it re-anchors: otherwise a sample
written under the wrong login (or any other briefly-wrong long countdown)
sticks for the rest of the week. A provider can also refill the counter
without moving the scheduled reset (Claude does this after a token reset).
That is not a new scheduled window, but it is still a boundary for fitting and
drawing the current burn; `boundaries` records both kinds. The other exception
is a reset granted out of band (Codex handing everyone a fresh week
mid-window), which no elapsed-time rule can ever recognise; `rolled_window`
detects those from the reading itself, and `rolls` reads them back out of the
stored labels so the chart can mark the moment instead of silently starting a
new line. Reads then select by sample *time* inside the window, so labels
forked before the hold still reunite on the chart.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import threading
import time

import cache_util

import sources_config

STORE_PATH = os.path.expanduser("~/.headroom/quota_samples.jsonl")
# Compact grant log — survives sample compaction so the heatmap can reach
# further back than the burndown curve's two-week samples.
ROLLS_PATH = os.path.expanduser("~/.headroom/quota_resets.jsonl")

# One row per pool per bucket. Fine enough to regress a 5h session window,
# coarse enough that a week of samples stays small.
BUCKET_S = 5 * 60
# Keep enough samples that a mid-window grant can still be re-detected weeks
# later if the journal write failed, and that `history` / burndown still have
# something to draw. Grants themselves live in `ROLLS_PATH` for longer.
RETENTION_S = 90 * 24 * 3600
# Rewrite the file once it grows past this many lines (~2x a full retention
# window), dropping rows older than the cutoff. At 5-minute buckets × ~7 pools
# × 90 days that is ~180k rows; the threshold sits above one full window so a
# quiet install does not rewrite every tick.
COMPACT_AT_LINES = 400_000
# Slack on "a full window later", so a reset observed slightly early still
# reads as a roll rather than jitter.
WINDOW_TOLERANCE_S = 15 * 60
# Out-of-band reset detection. Both bars have to be cleared at once — see
# `rolled_window` for why either alone is ambiguous.
RESET_MIN_DROP_PCT = 1.0
RESET_MIN_GAIN_S = 15 * 60
# How far back sample-derived `rolls` looks when seeding the grant journal.
# Charts clip to their own domain; the journal is what keeps history past the
# sample retention window.
ROLL_LOOKBACK_S = RETENTION_S
# Grant journal retention — long enough to hold the full public Codex
# announcement feed (codex-resets.com goes back ~a year) plus local detections.
ROLL_RETENTION_S = 400 * 24 * 3600
# Codex grants cluster — four in six days on a normal week of use. Cap is a
# safety net on the journal read, not the real retention bound.
MAX_ROLLS = 400
# How far ahead of its held reset a window has to roll before `rolls` calls it
# a grant, as a fraction of the window. Flat minutes are the wrong bar: a 5h
# session that rolls 18 minutes early is the source rounding, while a weekly
# window doing the same thing is still just the week ending. The real grants
# clear their old reset by days.
ROLL_GRANT_MIN_EARLY = 0.1
# Bytes read from the tail to reseed bucket/window state after a restart.
TAIL_BYTES = 64 * 1024


class Pool:
    """One quota meter: a provider payload key plus its window length."""

    __slots__ = ("provider", "pool", "key", "default_window_s")

    def __init__(self, provider, pool, key, default_window_s=None):
        self.provider = provider
        self.pool = pool
        self.key = key
        self.default_window_s = default_window_s

    @property
    def id(self):
        return (self.provider, self.pool)


# Derived from sources_config.QUOTA_SOURCES — add pools there, not here.
POOLS = tuple(
    Pool(provider, pool, key, default_window_s)
    for provider, pool, key, default_window_s in sources_config.pool_rows()
)

BY_ID = {pool.id: pool for pool in POOLS}
PROVIDERS = tuple(dict.fromkeys(pool.provider for pool in POOLS))

_lock = threading.Lock()
# (provider, pool) -> newest row written. Carries the bucket (so a restart or a
# fast poll does not duplicate a row) and the pct/resets_in/window_end a roll
# decision needs.
_last_row = {}
_seeded = False


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # drop NaN


def extract(provider, pool_id, payload):
    """Pull {pct, window_s, resets_in_s} for one pool out of a source payload.

    Returns None when the payload has nothing usable, which is the normal case
    for a provider the user has not configured.
    """
    spec = BY_ID.get((provider, pool_id))
    if spec is None or not isinstance(payload, dict):
        return None

    bucket = payload.get(spec.key)
    if not isinstance(bucket, dict):
        return None

    pct = _num(bucket.get("pct"))
    if pct is None:
        return None

    window_s = _num(bucket.get("window_s")) or spec.default_window_s
    # Cursor reports one billing cycle at the top level for every pool.
    resets_in_s = _num(bucket.get("resets_in_s"))
    if resets_in_s is None:
        resets_in_s = _num(payload.get("resets_in_s"))

    if not window_s or window_s <= 0 or resets_in_s is None:
        return None

    return {
        "pct": round(pct, 2),
        "window_s": int(window_s),
        "resets_in_s": max(0, int(resets_in_s)),
    }


def _previous_end(previous, window_s):
    """The reset instant carried by a stored row, or None."""
    if not isinstance(previous, dict):
        return None
    end = _num(previous.get("window_end"))
    if end is not None:
        return int(end)
    start = _num(previous.get("window_start"))
    # Rows written before window_end was carried on each sample.
    return None if start is None else int(start) + int(window_s)


def rolled_window(previous, pct, resets_in_s, now):
    """True when this reading belongs to a later window than `previous`'s.

    Two independent facts have to agree, because either one alone is
    ambiguous. Used percent fell, which cannot happen inside a window — you
    do not un-spend — except that a credit grant lowers it without rolling
    anything. And `resets_in_s` jumped past the decay the clock predicts,
    which jitter and post-wake cached responses also do — except that those
    repeat the previous `pct` rather than dropping it. Only a real roll
    produces both at the same moment.

    Over two weeks of live samples across every pool this fires exactly on the
    genuine rolls and on nothing else, including the multi-hour `resets_in_s`
    jumps that follow a sleep.
    """
    prev_pct = _num((previous or {}).get("pct"))
    prev_resets = _num((previous or {}).get("resets_in_s"))
    prev_t = _num((previous or {}).get("t"))
    if prev_pct is None or prev_resets is None or prev_t is None:
        return False
    if prev_pct - pct < RESET_MIN_DROP_PCT:
        return False
    expected = prev_resets - max(0.0, now - prev_t)
    return resets_in_s - expected >= RESET_MIN_GAIN_S


def rolls(rows, *, since=None, limit=MAX_ROLLS):
    """Granted resets visible in `rows` (one pool's samples, oldest first).

    Every window boundary shows up in the log as a change of `window_start`.
    Most are the window simply running out, which is not news: the axis already
    ends there and the chart draws the rule. The ones worth surfacing are the
    grants — Codex handing back a week you had already spent — and those are
    exactly the boundaries that landed while the previous window's reset was
    still in the future.

    Read off the stored labels rather than by re-running `rolled_window`, so
    what the chart marks and what the log says can never disagree: a relabelled
    row moves both at once.

    Returns [{t, kind, forgiven_pct}, …], oldest first. Prefer `rolls_for` at
    the burndown boundary — that merges these detections into the durable
    grant journal so the heatmap outlives sample compaction.
    """
    out = []
    previous = None
    for row in rows:
        start = row.get("window_start")
        if (previous is not None
                and start is not None
                and start != previous.get("window_start")):
            window_s = _num(previous.get("window_s")) or _num(row.get("window_s"))
            previous_end = _previous_end(previous, window_s or 0)
            t = _num(row.get("t"))
            forgiven = ((_num(previous.get("pct")) or 0.0)
                        - (_num(row.get("pct")) or 0.0))
            early_by = max(WINDOW_TOLERANCE_S,
                           (window_s or 0) * ROLL_GRANT_MIN_EARLY)
            if (previous_end is not None
                    and t is not None
                    and previous_end - t > early_by
                    and forgiven >= RESET_MIN_DROP_PCT):
                out.append({
                    "t": int(t),
                    "kind": "granted",
                    "forgiven_pct": round(forgiven, 2),
                })
        previous = row
    if since is not None:
        out = [roll for roll in out if roll["t"] >= since]
    return out[-limit:] if limit else out


def rolls_for(provider, pool, rows, *, now=None, since=None, limit=MAX_ROLLS,
              announced=None):
    """Granted resets for one pool, including history past sample retention.

    Detects grants in `rows`, optionally merges a canonical announcement feed
    (`announced`: [{t, tweet_id, tweet_url, …}]), appends any new ones to the
    durable journal, and returns the union — observed, announced, or both.
    Charts still clip to their own domain; the heatmap reads the full lookback.
    """
    now = time.time() if now is None else float(now)
    if since is None:
        since = now - ROLL_RETENTION_S
    detected = rolls(rows, since=min(since, now - ROLL_LOOKBACK_S), limit=None)

    if announced:
        # Late import keeps the samples module free of the network fetcher for
        # callers that never pass announcements (every non-Codex pool).
        import codex_resets
        matched, unmatched = codex_resets.match(detected, announced)
        to_remember = list(matched)
        for ann in unmatched:
            if ann["t"] < since:
                continue
            to_remember.append({
                "t": int(ann["t"]),
                "kind": "granted",
                "forgiven_pct": None,
                "source": "announced",
                "tweet_id": ann.get("tweet_id"),
                "tweet_url": ann.get("tweet_url"),
            })
    else:
        to_remember = [
            {
                "t": int(event["t"]),
                "kind": event.get("kind") or "granted",
                "forgiven_pct": event.get("forgiven_pct"),
                "source": "observed",
                "tweet_id": None,
                "tweet_url": None,
            }
            for event in detected
        ]

    _remember_rolls(provider, pool, to_remember, now=now)
    return remembered_rolls(
        provider, pool, since=since, limit=limit, now=now)


def remembered_rolls(provider, pool, *, since=None, limit=MAX_ROLLS, now=None):
    """Grants already written to the durable journal for one pool."""
    now = time.time() if now is None else float(now)
    if since is None:
        since = now - ROLL_RETENTION_S
    out = []
    try:
        with open(ROLLS_PATH) as handle:
            for row in _iter_rows(handle):
                if row.get("provider") != provider or row.get("pool") != pool:
                    continue
                t = _num(row.get("t"))
                # A grant stamped ahead of `now` is not news — it is usually a
                # frozen test clock that leaked into the desk journal. Serving
                # it makes Activity say "0s" forever (`fmt_ago` clamps futures).
                if t is None or t < since or t > now:
                    continue
                event = {
                    "t": int(t),
                    "kind": row.get("kind") or "granted",
                    "forgiven_pct": (
                        None if row.get("forgiven_pct") is None
                        else round(_num(row.get("forgiven_pct")) or 0.0, 2)
                    ),
                    "source": row.get("source") or "observed",
                }
                if row.get("tweet_id"):
                    event["tweet_id"] = str(row["tweet_id"])
                if row.get("tweet_url"):
                    event["tweet_url"] = row["tweet_url"]
                out.append(event)
    except OSError:
        return []
    out.sort(key=lambda roll: roll["t"])
    # Dedup: prefer tweet_id when present, else the sample instant. A later
    # poll that upgrades observed → both replaces the weaker row.
    by_key = {}
    order = []
    for roll in out:
        key = roll.get("tweet_id") or f"t:{roll['t']}"
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = roll
            order.append(key)
            continue
        by_key[key] = _prefer_roll(prev, roll)
    deduped = [by_key[key] for key in order]
    # Two keys can still name one event (obs before match, then tweet_id).
    # Collapse by time within a bucket after the tweet-id pass.
    collapsed = []
    for roll in deduped:
        if (collapsed
                and abs(collapsed[-1]["t"] - roll["t"]) <= 5 * 60
                and (collapsed[-1].get("tweet_id") or roll.get("tweet_id"))):
            collapsed[-1] = _prefer_roll(collapsed[-1], roll)
            continue
        collapsed.append(roll)
    return collapsed[-limit:] if limit else collapsed


def _prefer_roll(left, right):
    """Keep the richer of two journal rows for the same grant."""
    rank = {"announced": 0, "observed": 1, "both": 2}
    if rank.get(right.get("source"), 0) > rank.get(left.get("source"), 0):
        winner, other = dict(right), left
    else:
        winner, other = dict(left), right
    if winner.get("forgiven_pct") is None and other.get("forgiven_pct") is not None:
        winner["forgiven_pct"] = other["forgiven_pct"]
    if not winner.get("tweet_id") and other.get("tweet_id"):
        winner["tweet_id"] = other["tweet_id"]
        winner["tweet_url"] = other.get("tweet_url")
    # Observed sample time outranks a pure announcement clock so the chart
    # rule still meets the curve.
    if (other.get("source") in ("observed", "both")
            and winner.get("source") == "announced"):
        winner["t"] = other["t"]
    return winner


def _rolls_path_is_live():
    """True when the journal is the desk owner's `~/.headroom` file.

    Unit tests redirect `ROLLS_PATH` (or `HOME`) so they can freeze `now`
    years ahead and still persist. The live path must not accept those
    futures — one unpatched burndown fixture already stamped a Jan 2027
    "18% back" grant that Activity then showed as `0s` forever.
    """
    live = os.path.expanduser("~/.headroom/quota_resets.jsonl")
    try:
        return os.path.samefile(ROLLS_PATH, live)
    except OSError:
        return os.path.abspath(ROLLS_PATH) == os.path.abspath(live)


def _remember_rolls(provider, pool, events, *, now=None):
    """Append newly seen grants to the journal. Idempotent per stable key."""
    if not events:
        return
    now = time.time() if now is None else float(now)
    # On the live journal, also refuse wall-clock futures. Tests may pass a
    # frozen `now` years ahead; those only write when ROLLS_PATH is redirected.
    wall = time.time()
    live = _rolls_path_is_live()
    known_ids = set()
    known_t = set()
    for roll in remembered_rolls(provider, pool, since=0, limit=None, now=now):
        if roll.get("tweet_id"):
            known_ids.add(str(roll["tweet_id"]))
        known_t.add(int(roll["t"]))
    fresh = []
    for event in events:
        t = _num(event.get("t"))
        if t is None:
            continue
        if t > now + WINDOW_TOLERANCE_S:
            continue
        if live and t > wall + WINDOW_TOLERANCE_S:
            continue
        tweet_id = event.get("tweet_id")
        tweet_id = str(tweet_id) if tweet_id else None
        if tweet_id and tweet_id in known_ids:
            continue
        if not tweet_id and int(t) in known_t:
            continue
        # Observed row already stored; a later match with a tweet_id still
        # appends so readers can upgrade to `both` via `_prefer_roll`.
        if tweet_id:
            known_ids.add(tweet_id)
        known_t.add(int(t))
        row = {
            "t": int(t),
            "provider": provider,
            "pool": pool,
            "kind": event.get("kind") or "granted",
            "source": event.get("source") or "observed",
        }
        if event.get("forgiven_pct") is not None:
            row["forgiven_pct"] = round(
                _num(event.get("forgiven_pct")) or 0.0, 2)
        if tweet_id:
            row["tweet_id"] = tweet_id
        if event.get("tweet_url"):
            row["tweet_url"] = event["tweet_url"]
        fresh.append(row)
    if not fresh:
        return
    folder = os.path.dirname(ROLLS_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    try:
        with open(ROLLS_PATH, "a") as handle:
            for row in fresh:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError as exc:
        print("quota_resets write error:", exc)
        return
    _maybe_compact_rolls()


def _maybe_compact_rolls(now=None):
    """Drop journal rows older than ROLL_RETENTION_S, only when any age out."""
    now = time.time() if now is None else float(now)
    cutoff = now - ROLL_RETENTION_S
    try:
        with open(ROLLS_PATH) as handle:
            rows = list(_iter_rows(handle))
    except OSError:
        return
    kept = [row for row in rows if (_num(row.get("t")) or 0) >= cutoff]
    if len(kept) == len(rows):
        return
    tmp = ROLLS_PATH + ".tmp"
    try:
        with open(tmp, "w") as handle:
            for row in kept:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.replace(tmp, ROLLS_PATH)
    except OSError as exc:
        print("quota_resets compact error:", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def boundaries(rows, *, since=None):
    """Every instant this pool refilled, oldest first — scheduled or in-place.

    `rolls` answers "when was I handed a week I had already spent", which is
    news and earns a rule and a notification. This answers the duller
    question underneath it: where does one quota epoch stop and the next
    begin. Nearly every boundary is a session simply running out on time,
    which is not worth announcing but *is* worth drawing — it is where the
    cross-window `history` curve climbs back to full. Some providers can also
    refill a counter while leaving the scheduled window unchanged; those are
    boundaries for the history and forecast, but not granted-reset events.

    A boundary that did not refill anything is not one of these. Sources
    report `resets_in_s` loosely enough that a window gets relabelled without
    the reading moving, and a riser of zero height is only a point budget
    spent on nothing.

    Returns bare epochs. The instant is the new window's own start where the
    log agrees on one, not the first sample after it: at five-minute buckets
    that sample lands up to a bucket late, and the riser then stands beside
    the axis edge it is supposed to land on rather than on it.
    """
    out = []
    previous = None
    for row in rows:
        start = row.get("window_start")
        if previous is not None:
            t = _num(row.get("t"))
            prev_t = _num(previous.get("t"))
            refilled = ((_num(previous.get("pct")) or 0.0)
                        - (_num(row.get("pct")) or 0.0))
            if (t is not None and prev_t is not None
                    and refilled >= RESET_MIN_DROP_PCT):
                # A changed label identifies a scheduled or granted window
                # boundary. With the same label, the provider refilled the
                # existing counter in place; the sample timestamp is the only
                # honest cut we have.
                if (start is not None
                        and start != previous.get("window_start")):
                    cut = _num(start)
                    if cut is None or not prev_t < cut <= t:
                        cut = t
                else:
                    cut = t
                out.append(int(cut))
        previous = row
    if since is not None:
        out = [t for t in out if t >= since]
    return out


def window_for(now, window_s, resets_in_s, *, pct=None, previous=None):
    """Derive `(start, end)` for one reading, holding the end against jitter.

    A window that rolled on time puts its reset a full window after the held
    one, so that is the bar a fresh reading has to clear. A derived end that
    lands later but short of that bar is the source misreporting
    `resets_in_s` — most often a cached response served after the machine
    wakes, which freezes `resets_in_s` while `now` keeps moving and walks the
    derived window forward a poll at a time. Those stay held.

    A derived end that lands earlier than the held one is not that failure
    mode: wake-cache freezes only walk forward. Holding against an earlier
    reading is how a wrong login's weekly countdown, once sampled under this
    pool, keeps printing on the right account for the rest of the week. Past
    tolerance, the earlier end wins.

    Holding rather than snapping still matters because a fork is not
    cosmetic: every sample collected before it belongs to a window nothing
    queries again, which is enough to starve the burndown fit of the history
    it needs. But holding against *every* later reading is how a reset
    granted early stays invisible for days, so `rolled_window` gets a say
    too.
    """
    window_s = int(window_s)
    resets_in_s = max(0, min(int(resets_in_s), window_s))
    end = int(round(now + resets_in_s))
    previous_end = _previous_end(previous, window_s)
    if previous_end is not None:
        on_time = end >= previous_end + window_s - WINDOW_TOLERANCE_S
        early = pct is not None and rolled_window(
            previous, pct, resets_in_s, now)
        if on_time or early:
            pass
        elif abs(end - previous_end) <= WINDOW_TOLERANCE_S:
            end = previous_end
        elif end < previous_end:
            # Corrected identity / cleared bad sample: adopt the earlier end.
            pass
        else:
            # Later but short of a full window — wake-cache freeze / jitter.
            end = previous_end
    return end - window_s, end


def _iter_rows(handle):
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(row, dict) and row.get("t") is not None:
            yield row


def _relabel_locked(rows):
    """Replay `window_for` over every row, oldest first. Returns rows changed.

    Window labels are derived, so they are only ever as good as the rule that
    derived them. Replaying that rule across the whole log is what lets a fix
    reach the history it was already wrong about; without it a window
    mislabelled before the fix stays mislabelled until it rolls on its own,
    which on a weekly pool is a week of wrong charts.

    Only the derived keys move. The readings themselves are never touched.
    """
    rows.sort(key=lambda row: row.get("t") or 0)
    previous = {}
    changed = 0
    for row in rows:
        key = (row.get("provider"), row.get("pool"))
        spec = BY_ID.get(key)
        if spec is None:
            continue
        window_s = _num(row.get("window_s")) or spec.default_window_s
        pct = _num(row.get("pct"))
        resets_in_s = _num(row.get("resets_in_s"))
        if not window_s or pct is None or resets_in_s is None:
            continue
        start, end = window_for(row["t"], int(window_s), int(resets_in_s),
                                pct=pct, previous=previous.get(key))
        if (row.get("window_start") != start
                or row.get("window_end") != end):
            changed += 1
        row["window_start"] = start
        row["window_end"] = end
        previous[key] = row
    return changed


def _migrate_locked():
    """One-shot relabel of a store written before windows carried their end."""
    try:
        with open(STORE_PATH) as handle:
            rows = list(_iter_rows(handle))
    except OSError:
        return False
    stale = any(row.get("window_end") is None
                and (row.get("provider"), row.get("pool")) in BY_ID
                for row in rows)
    if not stale:
        return False
    if _relabel_locked(rows):
        print(f"quota_samples: relabelled {len(rows)} rows onto held windows")
    return _rewrite(rows)


def _seed_locked():
    """Reseed per-pool state from the file tail so a restart is a no-op."""
    global _seeded
    if _seeded:
        return
    _seeded = True
    _migrate_locked()
    try:
        size = os.path.getsize(STORE_PATH)
        with open(STORE_PATH, "rb") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
                handle.readline()  # discard the partial first line
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return

    for row in _iter_rows(raw.splitlines()):
        key = (row.get("provider"), row.get("pool"))
        if key not in BY_ID:
            continue
        bucket = _num(row.get("t"))
        if bucket is None:
            continue
        previous = _last_row.get(key)
        if previous is None or bucket > _num(previous.get("t")):
            _last_row[key] = row


def _rewrite(rows):
    """Replace the store with `rows`, atomically. False when it could not.

    Called both under `_lock` (relabelling) and outside it (compaction), which
    is why it takes rows rather than reaching for module state.
    """
    tmp = STORE_PATH + ".tmp"
    try:
        with open(tmp, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.replace(tmp, STORE_PATH)
    except OSError as exc:
        print("quota_samples rewrite error:", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


def _append_locked(rows):
    if not rows:
        return
    folder = os.path.dirname(STORE_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(STORE_PATH, "a") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def record(state, *, now=None, persist=True):
    """Sample every pool present in `state` ({source_id: payload}).

    Returns the rows written (empty when every pool is still inside its current
    bucket). Never raises — a sampling failure must not take down a poll tick.

    A stale payload is not sampled. It carries the last good percentage with
    the last good `resets_in_s`, so writing it every bucket would hand the
    burndown a flat line it reads as a *measured* burn of zero, and a reset
    countdown that never moves — which eventually looks like a window that
    rolled. One broken credential would rewrite the history of a pool that
    nobody actually stopped using.
    """
    now = time.time() if now is None else float(now)
    bucket_t = int(now // BUCKET_S) * BUCKET_S
    written = []

    try:
        with _lock:
            _seed_locked()
            for spec in POOLS:
                payload = (state or {}).get(spec.provider)
                # A stale payload is the same reading over and over. Recording
                # it lays down a flat line that is indistinguishable from a
                # real idle stretch, and every one of those samples walks the
                # derived window forward, so a source that stopped answering
                # last night still shows a window rolling on schedule today.
                if not cache_util.trusted(payload, now):
                    continue
                if payload.get("stale"):
                    continue
                reading = extract(spec.provider, spec.pool, payload)
                if reading is None:
                    continue
                previous = _last_row.get(spec.id)
                if previous is not None and _num(previous.get("t")) == bucket_t:
                    continue  # already sampled this bucket

                start, end = window_for(
                    now,
                    reading["window_s"],
                    reading["resets_in_s"],
                    pct=reading["pct"],
                    previous=previous,
                )
                row = {
                    "t": bucket_t,
                    "provider": spec.provider,
                    "pool": spec.pool,
                    "pct": reading["pct"],
                    "window_s": reading["window_s"],
                    "resets_in_s": reading["resets_in_s"],
                    "window_start": start,
                    "window_end": end,
                }
                _last_row[spec.id] = row
                written.append(row)

            if persist and written:
                _append_locked(written)
    except OSError as exc:
        print("quota_samples write error:", exc)
        return []

    if persist and written:
        _maybe_compact(now)
    return written


def read(*, provider=None, pool=None, since=None, window_start=None):
    """Return matching rows, oldest first. Missing file reads as empty."""
    out = []
    try:
        with open(STORE_PATH) as handle:
            for row in _iter_rows(handle):
                if provider is not None and row.get("provider") != provider:
                    continue
                if pool is not None and row.get("pool") != pool:
                    continue
                if since is not None and row.get("t", 0) < since:
                    continue
                if (window_start is not None
                        and row.get("window_start") != window_start):
                    continue
                out.append(row)
    except OSError:
        return []
    out.sort(key=lambda row: row.get("t") or 0)
    return out


def latest_row(provider, pool):
    """The newest stored sample for one pool, or None.

    Callers pass this straight back into `window_for` as `previous`: it is the
    only thing a roll decision needs, and reading it here keeps that decision
    in one place rather than one copy per caller.
    """
    rows = read(provider=provider, pool=pool)
    return rows[-1] if rows else None


def current_window(provider, pool, *, window_start=None, window_s=None):
    """Rows belonging to one billing window, oldest first.

    Pass `window_start` (and ideally `window_s`) to pin the live window.
    Selection is by sample *time* falling inside `[start, start+window_s)`, not
    by exact `window_start` equality: `resets_in_s` jitter used to fork a fresh
    label every few polls, and equality then stranded the real burn curve on
    orphan labels the chart never reads again.

    When unpinned, the newest sample's `window_start` / `window_s` define the
    range. A stored label further ahead than the live one no longer wins.
    """
    rows = read(provider=provider, pool=pool)
    if not rows:
        return []
    if window_start is None or window_s is None:
        for row in reversed(rows):
            if window_start is None and isinstance(
                    row.get("window_start"), (int, float)):
                window_start = int(row["window_start"])
            if window_s is None and isinstance(row.get("window_s"), (int, float)):
                window_s = int(row["window_s"])
            if window_start is not None and window_s is not None:
                break
        if window_start is None:
            return rows
    start = int(window_start)
    if not window_s:
        return [row for row in rows if row.get("window_start") == start]
    end = start + int(window_s)
    return [
        row for row in rows
        if isinstance(row.get("t"), (int, float))
        and start <= int(row["t"]) <= end
    ]


def windows(provider, pool):
    """Distinct window_start values for one pool, oldest first."""
    starts = {
        row["window_start"] for row in read(provider=provider, pool=pool)
        if isinstance(row.get("window_start"), (int, float))
    }
    return sorted(starts)


def _line_count():
    try:
        with open(STORE_PATH, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _maybe_compact(now):
    if _line_count() <= COMPACT_AT_LINES:
        return
    compact(now=now)


def compact(*, now=None):
    """Drop rows older than the retention cutoff. Returns rows kept."""
    now = time.time() if now is None else float(now)
    cutoff = now - RETENTION_S
    try:
        with open(STORE_PATH) as handle:
            kept = [row for row in _iter_rows(handle) if row.get("t", 0) >= cutoff]
    except OSError:
        return 0
    return len(kept) if _rewrite(kept) else 0


def reset_for_tests():
    """Clear in-memory per-pool state (unit tests only)."""
    global _seeded
    with _lock:
        _last_row.clear()
        _seeded = False
