"""Anthropic OAuth plan-usage fetcher (CodexBar-equivalent).

Owns Claude OAuth material under `~/.headroom/oauth/`, importing once from
Claude Code's Keychain or `~/.claude/.credentials.json` when Headroom has
nothing yet. Refreshed tokens are written only to Headroom's store — never
back into Claude Code's Keychain item, which races that app's own refresh.

Claude Code's Keychain services remain an *import* source only:

    Claude Code-credentials-<sha256(config dir)[:8]>

The old unqualified `Claude Code-credentials` service and credential files
remain import fallbacks. After a successful import (or any refresh), the
LaunchAgent never needs to touch a foreign Keychain item again.

Keychain reads go through SecItemCopyMatching (see keychain.py). A user Deny
is sticky until Settings refresh re-arms it — collapsing Deny into a miss
used to re-prompt every fail TTL.

Stdlib only. The endpoint is undocumented and may change; failures degrade
to an empty quota dict so the desk gadget still shows local cost data.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
from datetime import datetime, timezone

import http_util
import cache_util
import keychain

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URLS = (
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
)
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_BETA = "oauth-2025-04-20"
UA = "claude-cli/2.1.201 (external, cli)"
# Claude Code used to keep this inside its config directory. It remains an
# import fallback for older installs; current releases put the same payload in
# a Keychain service derived from `CLAUDE_CONFIG_DIR`.
CREDS_NAME = ".credentials.json"
CONFIG_DIR = os.path.expanduser("~/.claude")
CREDS_FILE = os.path.join(CONFIG_DIR, CREDS_NAME)
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_SERVICE_PREFIX = KEYCHAIN_SERVICE + "-"
KEYCHAIN_STORE_PREFIX = "keychain:"
HEADROOM_STORE_PREFIX = "headroom:"
OAUTH_DIR = os.path.expanduser("~/.headroom/oauth")
CACHE_TTL_S = 60
# First retry after a miss. Consecutive failures back off from here
# (cache_util._fail_ttl_s) — without that, several accounts on this short a
# leash hammer a rate-limited endpoint hard enough to keep it rate-limited.
FAIL_TTL_S = 20


def _retry_after_s(err):
    """Seconds a 429/503 asked us to wait, or None.

    Only the delta-seconds form; the HTTP-date form is rare enough here that
    guessing at clock skew is worse than falling back to our own backoff.
    """
    try:
        raw = err.headers.get("Retry-After")
        return float(raw) if raw else None
    except (AttributeError, TypeError, ValueError):
        return None
EXPIRY_SKEW_S = 120

# One cache per account, keyed by account id ("" is the default login). The
# default's dict is still `_cache`, so anything holding that reference keeps
# talking about the same login it always did.
_cache = {"t": 0.0, "data": None, "err": None}
_caches = {"": _cache}

# In-memory OAuth blob: re-read Keychain/disk only when this expires or a
# usage call returns 401 — not on every 60s usage poll.
_oauth_mem = {}
_oauth_lock = threading.Lock()

# Sticky Keychain refusals, keyed by Claude Code service name. Survives across
# polls until rearm_keychain(); also mirrored to disk so a KeepAlive respawn
# does not immediately re-prompt.
_keychain_denied = {}
_deny_lock = threading.Lock()


def _cache_for(account):
    key = account.id if account else ""
    cache = _caches.get(key)
    if cache is None:
        cache = _caches[key] = {"t": 0.0, "data": None, "err": None}
    return cache


def _account_key(account=None):
    return account.id if account else "claude"


def _headroom_path(account=None):
    """Headroom-owned OAuth blob for one login (default or named account)."""
    if account is None:
        name = "claude.json"
    else:
        name = f"claude-{account.slug}.json"
    return os.path.join(OAUTH_DIR, name)


def _headroom_store(account=None):
    return HEADROOM_STORE_PREFIX + _account_key(account)


def _path_from_headroom_store(store):
    if not (isinstance(store, str) and store.startswith(HEADROOM_STORE_PREFIX)):
        return None
    key = store[len(HEADROOM_STORE_PREFIX):]
    if key == "claude":
        return _headroom_path(None)
    if key.startswith("claude:"):
        slug = key.split(":", 1)[1]
        return os.path.join(OAUTH_DIR, f"claude-{slug}.json")
    return None


def _keychain_service(account=None):
    """Claude Code's Keychain service for one config directory."""
    config_dir = account.root if account else CONFIG_DIR
    digest = hashlib.sha256(config_dir.encode("utf-8")).hexdigest()[:8]
    return KEYCHAIN_SERVICE_PREFIX + digest


def _keychain_store(service):
    """Opaque store id naming a Claude Code Keychain item (import only)."""
    if service == KEYCHAIN_SERVICE:
        return "keychain"
    return KEYCHAIN_STORE_PREFIX + service


def _creds_file(account=None):
    return account.child(CREDS_NAME) if account else CREDS_FILE


def _deny_path(service):
    safe = service.replace("/", "_")
    return os.path.join(OAUTH_DIR, f".denied-{safe}")


def _is_keychain_denied(service):
    with _deny_lock:
        if service in _keychain_denied:
            return True
    path = _deny_path(service)
    if os.path.isfile(path):
        with _deny_lock:
            _keychain_denied[service] = True
        return True
    return False


def _mark_keychain_denied(service, status):
    with _deny_lock:
        _keychain_denied[service] = True
    try:
        os.makedirs(OAUTH_DIR, exist_ok=True)
        with open(_deny_path(service), "w") as handle:
            handle.write(f"{status}\n")
    except OSError:
        pass


def rearm_keychain(account=None):
    """Clear sticky Keychain refusals so the next read may prompt again.

    Bound to a user-initiated refresh in the UI — background polls must not
    clear this, or Deny becomes a 20s modal loop again.
    """
    services = [_keychain_service(account)]
    if account is None:
        services.append(KEYCHAIN_SERVICE)
    with _deny_lock:
        for service in services:
            _keychain_denied.pop(service, None)
    for service in services:
        try:
            os.unlink(_deny_path(service))
        except OSError:
            pass
    _invalidate_oauth_mem(account)


def _invalidate_oauth_mem(account=None):
    key = _account_key(account)
    with _oauth_lock:
        _oauth_mem.pop(key, None)


def _oauth_mem_entry(account=None):
    key = _account_key(account)
    with _oauth_lock:
        entry = _oauth_mem.get(key)
        if not entry:
            return None
        exp = entry.get("exp")
        # No expiry → keep until a 401 forces a re-read.
        if exp is not None and time.time() >= exp - EXPIRY_SKEW_S:
            _oauth_mem.pop(key, None)
            return None
        return dict(entry)


def _store_oauth_mem(account, store, blob, oauth):
    exp = _expires_at_s(oauth)
    key = _account_key(account)
    with _oauth_lock:
        _oauth_mem[key] = {
            "store": store,
            "blob": blob,
            "oauth": oauth,
            "exp": exp,
        }


def _read_keychain_blob(service=KEYCHAIN_SERVICE):
    """Return the JSON blob, or raise KeychainRefused on user Deny.

    `None` means not found / empty / unparseable — a miss the search may
    continue past. Refusal is different and must not be retried on a timer.
    """
    if _is_keychain_denied(service):
        raise KeychainRefused(
            service, keychain.ERR_SEC_USER_CANCELED,
            sticky=True)
    try:
        status, raw = keychain.get_generic_password(service)
    except keychain.KeychainError:
        return None
    if status in (keychain.ERR_SEC_USER_CANCELED,
                  keychain.ERR_SEC_AUTH_FAILED):
        _mark_keychain_denied(service, status)
        raise KeychainRefused(service, status)
    if status != keychain.ERR_SEC_SUCCESS or not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class KeychainRefused(RuntimeError):
    """User denied (or auth failed for) a foreign Keychain read."""

    def __init__(self, service, status, sticky=False):
        self.service = service
        self.status = status
        self.sticky = sticky
        if sticky:
            msg = (
                f"Keychain access previously denied for {service} "
                f"(OSStatus {status}) — refresh this source in Settings to try "
                f"again"
            )
        else:
            msg = (
                f"Keychain access denied for {service} "
                f"(OSStatus {status}) — refresh this source in Settings to try "
                f"again"
            )
        super().__init__(msg)


def _read_file_blob(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_headroom_blob(account, blob):
    path = _headroom_path(account)
    raw = json.dumps(blob, separators=(",", ":"))
    os.makedirs(OAUTH_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(raw)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return _headroom_store(account)


def _import_to_headroom(account, blob):
    """Persist an imported Claude blob as Headroom's own copy."""
    return _write_headroom_blob(account, blob), blob


def _read_creds_blob(account=None, allow_keychain=True):
    """Return (store, blob_dict); store identifies Headroom's file after import.

    Prefer Headroom's own file. Claude Code Keychain / credential files are
    import sources only: on a successful read with a plan token, the blob is
    copied under `~/.headroom/oauth/` and that path becomes the store for
    refreshes. Foreign Keychain items are never written.

    A store that parses but carries no `claudeAiOauth.accessToken` is not an
    answer, so the search goes on rather than stopping at it. Claude Code also
    keeps per-MCP-server OAuth in its Keychain item, and a blob left holding
    only `mcpOAuth` used to end the search there.
    """
    headroom_path = _headroom_path(account)
    headroom_blob = _read_file_blob(headroom_path)
    if _oauth_block(headroom_blob):
        return _headroom_store(account), headroom_blob

    refused = None
    candidates = []

    if allow_keychain:
        service = _keychain_service(account)
        try:
            blob = _read_keychain_blob(service)
            if blob is not None:
                candidates.append((_keychain_store(service), blob))
        except KeychainRefused as exc:
            refused = exc
        if account is None:
            try:
                blob = _read_keychain_blob(KEYCHAIN_SERVICE)
                if blob is not None:
                    candidates.append(("keychain", blob))
            except KeychainRefused as exc:
                refused = refused or exc

    path = _creds_file(account)
    file_blob = _read_file_blob(path)
    if file_blob is not None:
        candidates.append((path, file_blob))

    if headroom_blob is not None:
        candidates.insert(0, (_headroom_store(account), headroom_blob))

    for store, blob in candidates:
        if _oauth_block(blob):
            # Claim ownership so the daemon never needs the foreign item again.
            if not (isinstance(store, str)
                    and store.startswith(HEADROOM_STORE_PREFIX)):
                store, blob = _import_to_headroom(account, blob)
            return store, blob

    if refused is not None and not candidates:
        raise refused
    if refused is not None and not any(_oauth_block(b) for _, b in candidates):
        # Import sources had no token either — surface the Deny so it stays
        # sticky rather than looking like a missing login.
        raise refused

    return candidates[0] if candidates else (None, None)


def _write_creds_blob(store, blob, account=None):
    """Persist a refreshed token to Headroom's store only.

    `store` may still name a Claude Code Keychain item from an older path;
    those writes are redirected. Never call SecItem* against a foreign
    service name. When `account` is omitted, recover a named login from a
    `headroom:claude:<slug>` store id.
    """
    if account is None and isinstance(store, str):
        path = _path_from_headroom_store(store)
        if path is not None:
            raw = json.dumps(blob, separators=(",", ":"))
            os.makedirs(OAUTH_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.write(raw)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            return store
    return _write_headroom_blob(account, blob)


def _oauth_block(blob):
    o = (blob or {}).get("claudeAiOauth") or {}
    if not o.get("accessToken"):
        return None
    return o


def credentials_present(account=None):
    """Whether this config directory has a usable Claude OAuth token.

    Checks Headroom's file and the Claude credential file without touching
    Keychain, so detection / seeding never pops SecurityAgent. Keychain is
    only consulted on a real fetch (and only when Headroom has no copy yet).
    """
    if _oauth_block(_read_file_blob(_headroom_path(account))):
        return True
    if _oauth_block(_read_file_blob(_creds_file(account))):
        return True
    return False


def _credentials_hint(account=None):
    path = _creds_file(account)
    owned = _headroom_path(account)
    service = _keychain_service(account)
    if account:
        return f"{owned}, Keychain service {service}, or {path}"
    return (
        f"{owned}, Keychain service {service}, legacy {KEYCHAIN_SERVICE}, "
        f"or {path}"
    )


def _shape_hint(store, blob):
    """Say what the credential store actually holds, not what we wanted.

    Claude Code owns this layout and has moved it before. An error that only
    restates the expectation sends you hunting; one that lists the keys that
    are there turns the next move into a one-line diagnosis. Key names only —
    a value printed here would be the token itself.
    """
    if isinstance(blob, dict):
        found = ", ".join(sorted(blob)) or "empty object"
    else:
        found = f"a bare {type(blob).__name__}"
    return (f"{store} has no claudeAiOauth.accessToken (found: {found}) — "
            "run `claude login` if this followed an update")


def _expires_at_s(oauth):
    ms = oauth.get("expiresAt")
    if not isinstance(ms, (int, float)):
        return None
    return ms / 1000.0 if ms > 1e12 else float(ms)


def _needs_refresh(oauth):
    exp = _expires_at_s(oauth)
    if exp is None:
        return False
    return exp - time.time() <= EXPIRY_SKEW_S


def _refresh(oauth, store, blob, account=None):
    refresh = oauth.get("refreshToken")
    if not refresh:
        raise RuntimeError("no refreshToken")
    last_err = None
    for url in TOKEN_URLS:
        try:
            data = http_util.request_json(
                url,
                json_body={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": CLIENT_ID,
                },
                method="POST",
                user_agent=UA,
            )
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} from {url}"
            continue
        except Exception as e:
            last_err = str(e)
            continue
        access = data.get("access_token")
        if not access:
            last_err = "refresh response missing access_token"
            continue
        oauth["accessToken"] = access
        if data.get("refresh_token"):
            oauth["refreshToken"] = data["refresh_token"]
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)):
            oauth["expiresAt"] = int((time.time() + expires_in) * 1000)
        blob["claudeAiOauth"] = oauth
        try:
            new_store = _write_headroom_blob(account, blob)
            store = new_store
        except Exception as exc:
            # Persisting failed (read-only home). The token in hand is still
            # good for this process — don't throw the refresh away.
            print("oauth: could not persist refreshed token:", exc)
        _store_oauth_mem(account, store, blob, oauth)
        return oauth
    raise RuntimeError(last_err or "token refresh failed")


def _http_get_usage(token):
    return http_util.request(
        USAGE_URL,
        auth=f"Bearer {token}",
        user_agent=UA,
        headers={
            "anthropic-beta": OAUTH_BETA,
            "anthropic-version": "2023-06-01",
            "x-app": "cli",
        },
    )


def _iso_to_unix(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _window_from_flat(obj):
    if not isinstance(obj, dict):
        return None
    util = obj.get("utilization")
    if util is None:
        return None
    resets = _iso_to_unix(obj.get("resets_at"))
    return {
        "pct": round(float(util), 1),
        "resets_at": obj.get("resets_at"),
        "resets_in_s": max(0, int(resets - time.time())) if resets else None,
    }


def _window_from_limit(lim):
    if not isinstance(lim, dict):
        return None
    pct = lim.get("percent")
    if pct is None:
        return None
    resets = _iso_to_unix(lim.get("resets_at"))
    return {
        "pct": round(float(pct), 1),
        "resets_at": lim.get("resets_at"),
        "resets_in_s": max(0, int(resets - time.time())) if resets else None,
    }


def _prettify_tier(raw):
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("default_"):
        s = s[len("default_"):]
    if s.startswith("claude_"):
        s = s[len("claude_"):]
    s = s.replace("_", " ")
    parts = s.split()
    if parts:
        parts[0] = parts[0].capitalize()
    return " ".join(parts)


def parse_usage(body, oauth=None):
    """Map API JSON → flat quota dict for /usage."""
    out = {
        "ok": True,
        "plan": _prettify_tier(
            (oauth or {}).get("rateLimitTier")
            or (oauth or {}).get("subscriptionType")
        ),
        "session": None,
        "week": None,
    }

    # Newer shape: limits[]
    limits = body.get("limits")
    if isinstance(limits, list) and limits:
        session = next((l for l in limits if l.get("kind") == "session"), None)
        week = next((l for l in limits if l.get("kind") == "weekly_all"), None)
        if week is None:
            # fall back to highest weekly_*
            weeklies = [l for l in limits if str(l.get("kind", "")).startswith("weekly")]
            if weeklies:
                week = max(weeklies, key=lambda l: float(l.get("percent") or 0))
        out["session"] = _window_from_limit(session)
        out["week"] = _window_from_limit(week)
        return out

    # Classic flat keys
    out["session"] = _window_from_flat(body.get("five_hour"))
    out["week"] = _window_from_flat(body.get("seven_day"))
    return out


def _load_oauth(account=None, force_read=False):
    """Return (store, blob, oauth) using the in-memory token cache when fresh."""
    if not force_read:
        entry = _oauth_mem_entry(account)
        if entry and entry.get("oauth"):
            return entry["store"], entry["blob"], entry["oauth"]

    store, blob = _read_creds_blob(account)
    oauth = _oauth_block(blob)
    if store and oauth:
        _store_oauth_mem(account, store, blob, oauth)
    return store, blob, oauth


def fetch_quota(force=False, account=None):
    """Return quota dict, using a short in-memory cache. Never raises.

    `account` is an extra login from accounts.py (None = the default one).
    Everything below is per-account: its own cache, its own disk snapshot,
    and its own Headroom OAuth file to refresh tokens back into.

    A forced refresh (Settings → refresh) re-reads credentials and the usage
    API, but does not clear a sticky Keychain Deny on its own — that re-arm
    is a deliberate user action wired through the sync-refresh path so a
    KeepAlive respawn cannot undo a Deny and re-prompt.
    """
    now = time.time()
    cache = _cache_for(account)
    disk_name = account.cache_name if account else "claude"
    if cache["data"] is None:
        disk = cache_util.load_disk(disk_name)
        if disk:
            cache.update(t=0.0, data=disk, err=None)
    if cache_util.fresh(cache, now, CACHE_TTL_S, FAIL_TTL_S, force):
        return cache["data"]

    empty = {"ok": False, "plan": None, "session": None, "week": None, "error": None}

    def _keep_stale(err, auth_required=False, retry_after_s=None):
        return cache_util.keep_stale(
            cache, now, err, empty, disk_name=disk_name,
            auth_required=auth_required, retry_after_s=retry_after_s)

    try:
        try:
            store, blob, oauth = _load_oauth(account)
        except KeychainRefused as exc:
            return _keep_stale(str(exc))
        # Nothing to authenticate with, and nothing here gets better on a
        # retry: both want a `claude login`, not patience.
        if not store:
            return _keep_stale(
                f"no Claude credentials in {_credentials_hint(account)}",
                auth_required=True)
        if not oauth:
            return _keep_stale(_shape_hint(store, blob), auth_required=True)

        if _needs_refresh(oauth):
            try:
                oauth = _refresh(oauth, store, blob, account=account)
                store = _headroom_store(account)
                blob = _read_file_blob(_headroom_path(account)) or blob
            except Exception:
                # still try the current token; it might work
                pass

        try:
            status, body = _http_get_usage(oauth["accessToken"])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                _invalidate_oauth_mem(account)
                try:
                    store, blob, oauth = _load_oauth(account, force_read=True)
                except KeychainRefused as exc:
                    return _keep_stale(str(exc))
                if not oauth:
                    return _keep_stale(
                        f"HTTP Error {e.code}: {e.reason}", auth_required=True)
                try:
                    oauth = _refresh(oauth, store, blob, account=account)
                except Exception as exc:
                    # The token was rejected and the refresh could not replace
                    # it. Left to the outer handler this reads as a generic
                    # failure, when it is the same dead login as a missing
                    # token and wants the same fix.
                    return _keep_stale(str(exc), auth_required=True)
                status, body = _http_get_usage(oauth["accessToken"])
            else:
                # 429 / 5xx — keep last good bars instead of wiping the page,
                # and let a Retry-After floor the backoff: it is the one wait
                # the provider stated rather than one we guessed.
                return _keep_stale(f"HTTP Error {e.code}: {e.reason}",
                                   retry_after_s=_retry_after_s(e))

        if status != 200:
            return _keep_stale(f"usage HTTP {status}")

        data = parse_usage(body, oauth)
        data["stale"] = False
        data["error"] = None
        return cache_util.store(cache, now, data, disk_name=disk_name)
    except KeychainRefused as e:
        return _keep_stale(str(e))
    except Exception as e:
        return _keep_stale(str(e))


def fmt_resets(seconds):
    """Match CodexBar-ish '1h 44m' / '4d 44m'."""
    if seconds is None:
        return None
    s = max(0, int(seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d > 0:
        if h > 0:
            return f"{d}d {h}h"
        if m > 0:
            return f"{d}d {m}m"
        return f"{d}d"
    if h > 0:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{m}m"


# Rolling window lengths Anthropic uses for the OAuth buckets.
SESSION_WINDOW_S = 5 * 3600
WEEK_WINDOW_S = 7 * 24 * 3600


def pace_pct(resets_in_s, window_s):
    """Where a linear burn would be right now (0–100), given time left to reset."""
    if resets_in_s is None or window_s <= 0:
        return None
    elapsed = window_s - max(0, int(resets_in_s))
    if elapsed < 0:
        elapsed = 0
    if elapsed > window_s:
        elapsed = window_s
    return round(100.0 * elapsed / window_s, 1)


def reset_for_tests():
    """Drop process-local caches (unit tests only)."""
    with _oauth_lock:
        _oauth_mem.clear()
    with _deny_lock:
        _keychain_denied.clear()
    # Clear, not update: the cache also carries bookkeeping keys written on
    # failure (fail_streak, retry_after_s, stale_since) that an update would
    # leave behind, bleeding one test's outage into the next.
    _cache.clear()
    _cache.update(t=0.0, data=None, err=None)
    _caches.clear()
    _caches[""] = _cache
