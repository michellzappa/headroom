"""Shared in-memory + last-good disk cache helpers for Headroom fetchers."""

from __future__ import annotations

import json
import os
import tempfile
import time

CACHE_DIR = os.path.expanduser("~/.headroom/cache")

# How long last-good data may be replayed before it stops being a hiccup.
# Fetchers poll on the order of a minute, so anything past this is a provider
# that changed shape, a credential that expired, or a login that went away —
# none of which clear up on their own, and all of which leave every ring
# drawn from that source quietly wrong.
STALE_ALERT_S = 15 * 60


def _disk_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.json")


def load_disk(name: str):
    """Return last-good snapshot from disk, or None.

    A snapshot written before `fetched_at` existed gets one from the file's
    mtime, which is the same instant by construction — `save_disk` only ever
    runs on a good fetch. Without it the first snapshot after an upgrade is the
    one case that cannot be aged, and that case is exactly a host restarting
    onto a cache it has been unable to refresh.
    """
    path = _disk_path(name)
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not (isinstance(data, dict) and data.get("ok")):
        return None
    if not isinstance(data.get("fetched_at"), (int, float)):
        try:
            data["fetched_at"] = os.path.getmtime(path)
        except OSError:
            pass
    return data


def save_disk(name: str, data: dict) -> None:
    """Persist a good snapshot so timeouts can reuse it after restarts."""
    if not isinstance(data, dict) or not data.get("ok"):
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _disk_path(name)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle, separators=(",", ":"))
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        pass


# A failure that keeps failing doubles its retry interval up to this cap.
# The short `fail_ttl_s` exists so a blip clears in seconds — but against a
# rate limit it is the disease: three Claude accounts retrying every 20s is
# nine requests a minute from one IP, enough to sustain the very 429 they are
# retrying. Doubling reaches the cap in minutes, and a recovered provider is
# then at most one cap behind. Kept below no ceiling a human notices: Refresh
# all still forces an immediate attempt.
FAIL_BACKOFF_CAP_S = 15 * 60

# Ceiling on a provider-sent Retry-After. It is an instruction, not a
# suggestion, so it may push the wait past the doubling cap — but an
# ill-formed or hostile header must not park a source for a day.
RETRY_AFTER_CAP_S = 60 * 60


def _fail_ttl_s(cache, fail_ttl_s):
    """Seconds to sit on a failure before retrying, given the streak so far."""
    streak = int(cache.get("fail_streak") or 1)
    ttl = min(fail_ttl_s * (2 ** max(0, streak - 1)), FAIL_BACKOFF_CAP_S)
    floor = cache.get("retry_after_s")
    if isinstance(floor, (int, float)) and floor > 0:
        ttl = max(ttl, min(float(floor), RETRY_AFTER_CAP_S))
    return ttl


def fresh(cache, now, ttl_s, fail_ttl_s, force=False):
    """True when the in-memory copy is young enough to serve as-is.

    A cache holding an error retries on the shorter `fail_ttl_s`, so a 429 or
    a dropped VPN clears in seconds rather than making the meter sit wrong for
    a full TTL. Consecutive failures back that interval off exponentially
    (`_fail_ttl_s`), so a failure that is not transient stops being polled as
    if it were.
    """
    if force or cache.get("data") is None:
        return False
    return now - cache.get("t", 0.0) < (
        _fail_ttl_s(cache, fail_ttl_s) if cache.get("err") else ttl_s
    )


def store(cache, now, data, disk_name=None):
    """Record a good fetch in memory and, when named, as the last-good disk
    snapshot `keep_stale` falls back to. Returns `data` so callers can
    `return cache_util.store(...)`.

    Stamps when the data was actually obtained, which is the only thing that
    can tell a replay from a fetch later on. It goes in the payload rather
    than beside it so it survives into the disk snapshot, and so a restart
    cannot mistake a month-old cache for something it just fetched.
    """
    if isinstance(data, dict):
        data["fetched_at"] = now
        data["stale"] = False
        # A good fetch is proof the login works. Clearing it here rather than
        # at each call site means no fetcher can leave the flag set on a
        # payload it just refreshed.
        data["auth_required"] = False
    if disk_name:
        save_disk(disk_name, data)
    # A good fetch ends the failure streak; the next miss starts over at the
    # short TTL instead of inheriting a backoff earned by an outage that is
    # over.
    cache.pop("fail_streak", None)
    cache.pop("retry_after_s", None)
    cache.pop("stale_since", None)
    cache.update(t=now, data=data, err=None)
    return data


def keep_stale(cache, now, err, empty, disk_name=None, auth_required=False,
               retry_after_s=None):
    """Prefer last-good snapshot on transient failure instead of wiping UI.

    `cache` is a dict with at least `data` (and usually `t`). On success paths
    callers still overwrite cache themselves. When `disk_name` is set, falls
    back to ~/.headroom/cache/<name>.json if memory is empty.

    `auth_required` separates the one failure the user can actually fix from
    every other reason a fetch misses. A rate limit, a dropped VPN and an
    expired login all arrive here as a stale snapshot with a message, and a
    surface that treats them alike can only say "not updating" — which reads
    as something to wait out, and is exactly wrong for a login that will never
    come back on its own.

    `fetched_at` rides along from the snapshot untouched. `cache["t"]` is when
    we last *tried*, which is what the retry TTL needs; the payload has to
    carry when the numbers were last *true*, or a source that has been failing
    for a day reads as one poll old and nothing downstream can tell the
    difference.

    `retry_after_s` is a provider-mandated wait (the Retry-After header on a
    429). It floors the next retry interval; pass it only when the provider
    actually said so, or the backoff loses the one number that is not a guess.
    """
    cache["fail_streak"] = int(cache.get("fail_streak") or 0) + 1
    if isinstance(retry_after_s, (int, float)) and retry_after_s > 0:
        cache["retry_after_s"] = float(retry_after_s)
    else:
        cache.pop("retry_after_s", None)
    prev = cache.get("data")
    if not (prev and prev.get("ok")) and disk_name:
        prev = load_disk(disk_name)
    if prev and prev.get("ok"):
        stale = dict(prev)
        stale["stale"] = True
        stale["error"] = err
        stale["auth_required"] = bool(auth_required)
        # Age from the last real fetch, not from the last attempt — every
        # attempt lands here, so counting attempts would keep resetting the
        # clock and a permanently broken source would read as fresh forever.
        since = prev.get("fetched_at")
        if not isinstance(since, (int, float)):
            # A snapshot written before this stamp existed cannot say how old
            # it is, and leaving it ageless would exempt the one case that
            # most needs escalating: a source that was already broken when
            # this shipped. Date it from the first failure seen instead, which
            # under-reports the age but never invents one.
            since = cache.get("stale_since") or now
        cache["stale_since"] = since
        stale["stale_for_s"] = int(max(0, now - since))
        cache.update(t=now, data=stale, err=err)
        return stale
    out = dict(empty)
    out["ok"] = False
    out["error"] = err
    out["stale"] = False
    out["auth_required"] = bool(auth_required)
    cache.update(t=now, data=out, err=err)
    return out


# How long a last-good snapshot may still stand in for a live reading. A poll
# that misses once is a blip — the numbers are seconds old and everything
# derived from them holds. Past this the percentages are still worth showing,
# because they are the last thing that was true, but nothing computed against
# *now* may be: a countdown, a pace, a forecast, or a fresh chart sample all
# claim a currency the reading no longer has.
TRUSTED_STALE_S = 600


def age_s(payload, now=None):
    """Seconds since these numbers were true, or None if the source never said."""
    if not isinstance(payload, dict):
        return None
    fetched = payload.get("fetched_at")
    if not isinstance(fetched, (int, float)) or fetched <= 0:
        return None
    return max(0.0, (time.time() if now is None else float(now)) - fetched)


def trusted(payload, now=None):
    """True when a payload is fresh enough to derive live values from.

    A payload with no `fetched_at` predates the field, so it is taken at face
    value rather than being treated as ancient — an old snapshot on disk must
    not make a working source look broken on the first poll after an upgrade.
    """
    if not isinstance(payload, dict) or not payload.get("ok"):
        return False
    if not payload.get("stale"):
        return True
    age = age_s(payload, now)
    return age is None or age <= TRUSTED_STALE_S
