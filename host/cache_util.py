"""Shared in-memory + last-good disk cache helpers for Headroom fetchers."""

from __future__ import annotations

import email.utils
import json
import os
import tempfile
import time
from datetime import timezone

CACHE_DIR = os.path.expanduser("~/.headroom/cache")

# How long to wait after a 429 before asking that provider again, indexed by
# consecutive strike. A rate limit is the one failure where retrying at the
# usual cadence makes the problem worse, and the usual cadence was all we had:
# the poll interval kept knocking and every forced refresh went straight past
# the TTL on top of it, so one 429 sustained itself.
RATE_LIMIT_BACKOFF_S = (60, 120, 300, 900)

# A server-supplied Retry-After outranks the schedule above, since it is the
# only party that knows the real window. The ceiling is there so one absurd
# header cannot park a source for a day.
RATE_LIMIT_CEILING_S = 3600

# A 429 is the case where retrying makes things worse, but it is not the only
# case where retrying is pointless. A 5xx, a timeout and a provider that
# changed shape all used to be retried on a flat `fail_ttl_s` forever, which
# is a fixed rate of traffic aimed at something already known to be failing.
# Consecutive failures double that interval from the provider's own base up to
# this cap; one good fetch puts it straight back on the short leash.
FAILURE_BACKOFF_MAX_S = 15 * 60

# Doubling is unbounded and the streak is not, so the exponent is clamped
# before it is used. The cap above already flattens the curve long before
# this bites; this only stops the arithmetic from getting silly.
FAILURE_BACKOFF_MAX_SHIFT = 20

# How long last-good data may be replayed before it stops being a hiccup.
# Fetchers poll on the order of a minute, so anything past this is a provider
# that changed shape, a credential that expired, or a login that went away —
# none of which clear up on their own, and all of which leave every ring
# drawn from that source quietly wrong.
STALE_ALERT_S = 15 * 60

# A rate limit is the host doing the right thing, not a source falling over.
# Attention waits longer before paging on those so a backoff the user already
# sees as "Paused" does not also light the menu-bar warning.
STALE_ALERT_RATE_LIMIT_S = 45 * 60


def stale_kind(error):
    """Machine-readable reason a fetch froze, or None when unknown.

    Clients use this to pick quiet wording for things the host is already
    waiting out (rate limits, 5xx, network) without parsing error prose.
    """
    low = str(error or "").lower()
    if "429" in low or "too many requests" in low:
        return "rate_limited"
    if ("http error 5" in low or "usage http 5" in low
            or "bad gateway" in low or "service unavailable" in low):
        return "provider"
    if any(mark in low for mark in (
            "timed out", "timeout", "unreachable", "connection",
            "getaddrinfo", "nodename", "network", "temporary failure")):
        return "network"
    return None


def _stamp_stale_meta(payload, cache, now, err):
    """Attach cause + absolute retry deadline onto a frozen payload."""
    kind = stale_kind(err)
    if kind:
        payload["stale_cause"] = kind
    else:
        payload.pop("stale_cause", None)
    retry_at = cache.get("retry_at", 0.0)
    if isinstance(retry_at, (int, float)) and retry_at > now:
        payload["retry_at"] = float(retry_at)
    else:
        payload.pop("retry_at", None)


def _stamp_fix(payload, fix):
    if fix:
        payload["fix"] = fix
    else:
        payload.pop("fix", None)


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


def parse_retry_after(value, now=None):
    """Seconds to wait from a Retry-After header value, or None.

    The header is either a count of seconds or an HTTP date, and both are
    allowed. A date already in the past means wait zero, not wait forever.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(int(text)))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - (time.time() if now is None else float(now)))


def note_rate_limit(cache, now, retry_after=None):
    """Record a 429 against this cache and return the seconds to wait.

    Consecutive strikes walk up `RATE_LIMIT_BACKOFF_S`; any good fetch clears
    them, so a provider that recovers is not punished for an old strike.
    """
    strikes = int(cache.get("rl_strikes", 0)) + 1
    cache["rl_strikes"] = strikes
    scheduled = RATE_LIMIT_BACKOFF_S[
        min(strikes, len(RATE_LIMIT_BACKOFF_S)) - 1]
    stated = parse_retry_after(retry_after, now)
    # `Retry-After` raises the wait and never lowers it. Letting it win
    # outright was a real bug: Anthropic answers a 429 with a sub-minute
    # header, so honouring it literally retried *sooner* than our own first
    # step and the backoff never got off the ground. The provider is
    # authoritative about waiting longer, not about waiting less than we
    # already decided we owe it.
    wait = scheduled if stated is None else max(float(stated), scheduled)
    wait = min(wait, RATE_LIMIT_CEILING_S)
    cache["retry_at"] = now + wait
    return wait


def failure_ttl(cache, fail_ttl_s):
    """How long to wait after the failures this cache has seen in a row.

    The first miss waits the provider's own `fail_ttl_s`, which is what it
    always did: one dropped poll is a blip and the next one usually works.
    Past that, each consecutive failure doubles the wait to `FAILURE_BACKOFF_MAX_S`,
    because a source that has missed five times running is not having a blip
    and asking it every twenty seconds is just aimed traffic.
    """
    streak = int(cache.get("fail_streak", 0))
    if streak < 2:
        return fail_ttl_s
    shift = min(streak - 1, FAILURE_BACKOFF_MAX_SHIFT)
    return min(fail_ttl_s * (2 ** shift), FAILURE_BACKOFF_MAX_S)


def fresh(cache, now, ttl_s, fail_ttl_s, force=False):
    """True when the in-memory copy is young enough to serve as-is.

    A cache holding an error retries on `fail_ttl_s`, widened by how many
    failures came before it, so a dropped VPN still clears in seconds while a
    source that has been failing all afternoon stops being asked every twenty.

    The two backoffs differ on `force`, and deliberately. A 429 holds against
    it: Settings refresh, the phone and the board's long-press are exactly
    what someone reaches for when the numbers look wrong, which is exactly
    when a rate limit is in effect, so letting them through turns a user's
    frustration into more of the traffic that caused it. Every other failure
    yields to it, because forcing is how a fixed login or a reconnected VPN is
    meant to be picked up, and making someone wait out a backoff they have
    already resolved is its own bug.
    """
    if cache.get("data") is None:
        return False
    if now < cache.get("retry_at", 0.0):
        return True
    if force:
        return False
    return now - cache.get("t", 0.0) < (
        failure_ttl(cache, fail_ttl_s) if cache.get("err") else ttl_s
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
        data.pop("fix", None)
    if disk_name:
        save_disk(disk_name, data)
    # A good fetch also ends both backoffs: the provider answered, so neither
    # streak has anything left to escalate against.
    cache.update(t=now, data=data, err=None, rl_strikes=0, retry_at=0.0,
                 fail_streak=0)
    return data


def keep_stale(cache, now, err, empty, disk_name=None, auth_required=False,
               fix=None):
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

    `fix` is what the reader should do about this failure, when the fetcher
    knows better than the generic advice the server derives from the cause.
    """
    # Every failing fetch lands here, whatever the reason, which makes this
    # the one place a streak can be counted without each fetcher remembering
    # to. `fresh` turns it into the widening retry interval.
    cache["fail_streak"] = int(cache.get("fail_streak", 0)) + 1

    prev = cache.get("data")
    if not (prev and prev.get("ok")) and disk_name:
        prev = load_disk(disk_name)
    if prev and prev.get("ok"):
        stale = dict(prev)
        stale["stale"] = True
        stale["error"] = err
        stale["auth_required"] = bool(auth_required)
        _stamp_fix(stale, fix)
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
        _stamp_stale_meta(stale, cache, now, err)
        cache.update(t=now, data=stale, err=err)
        return stale
    out = dict(empty)
    out["ok"] = False
    out["error"] = err
    out["stale"] = False
    out["auth_required"] = bool(auth_required)
    _stamp_fix(out, fix)
    _stamp_stale_meta(out, cache, now, err)
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
