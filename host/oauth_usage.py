"""Anthropic OAuth plan-usage fetcher (CodexBar-equivalent).

Reads the Claude Code OAuth token from macOS Keychain or
`~/.claude/.credentials.json`, calls `GET /api/oauth/usage`, and returns
session/weekly utilization + reset times. Current Claude Code uses one
Keychain service per config directory:

    Claude Code-credentials-<sha256(config dir)[:8]>

The old unqualified `Claude Code-credentials` service and credential files
remain fallbacks. Refreshed tokens go back to the store they came from —
through the Security framework (see keychain.py), never through `security -w`,
which would expose the token in the process table.

Stdlib only. The endpoint is undocumented and may change; failures degrade
to an empty quota dict so the desk gadget still shows local cost data.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
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
# Claude Code used to keep this inside its config directory. It remains a
# fallback for older installs; current releases put the same payload in a
# Keychain service derived from `CLAUDE_CONFIG_DIR`.
CREDS_NAME = ".credentials.json"
CONFIG_DIR = os.path.expanduser("~/.claude")
CREDS_FILE = os.path.join(CONFIG_DIR, CREDS_NAME)
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_SERVICE_PREFIX = KEYCHAIN_SERVICE + "-"
KEYCHAIN_STORE_PREFIX = "keychain:"
CACHE_TTL_S = 60
FAIL_TTL_S = 20          # retry sooner after transient misses (429, etc.)
EXPIRY_SKEW_S = 120

# One cache per account, keyed by account id ("" is the default login). The
# default's dict is still `_cache`, so anything holding that reference keeps
# talking about the same login it always did.
_cache = {"t": 0.0, "data": None, "err": None}
_caches = {"": _cache}


def _cache_for(account):
    key = account.id if account else ""
    cache = _caches.get(key)
    if cache is None:
        cache = _caches[key] = {"t": 0.0, "data": None, "err": None}
    return cache


def _keychain_service(account=None):
    """Claude Code's Keychain service for one config directory."""
    config_dir = account.root if account else CONFIG_DIR
    digest = hashlib.sha256(config_dir.encode("utf-8")).hexdigest()[:8]
    return KEYCHAIN_SERVICE_PREFIX + digest


def _keychain_store(service):
    """Opaque store id that lets refresh write back to the same service."""
    if service == KEYCHAIN_SERVICE:
        return "keychain"
    return KEYCHAIN_STORE_PREFIX + service


def _service_from_store(store):
    if store == "keychain":
        return KEYCHAIN_SERVICE
    if isinstance(store, str) and store.startswith(KEYCHAIN_STORE_PREFIX):
        return store[len(KEYCHAIN_STORE_PREFIX):]
    return None


def _keychain_account(service=KEYCHAIN_SERVICE):
    # Left as a subprocess deliberately: this reads *attributes* only (no -w),
    # and attribute reads are not ACL-gated, so they never prompt. Adding -w
    # here would reintroduce the modal loop.
    try:
        out = subprocess.check_output(
            ["security", "find-generic-password", "-s", service],
            stderr=subprocess.DEVNULL, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"acct"'):
            # "acct"<blob>="mz"
            i = line.find('="')
            if i >= 0:
                return line[i + 2:].rstrip('"')
    return os.environ.get("USER") or os.environ.get("LOGNAME")


def _creds_file(account=None):
    return account.child(CREDS_NAME) if account else CREDS_FILE


# Services this process is not allowed to read. Denial is a property of the
# item's ACL, so it will not change between polls — retrying at FAIL_TTL_S is
# what turned one refusal into an unbounded prompt loop. Cleared only by a
# restart or an explicit user-initiated re-check.
_denied_services = set()


def _read_keychain_blob(service=KEYCHAIN_SERVICE):
    # In-process and UI-free. The old subprocess made /usr/bin/security the
    # caller, which is on no item's ACL, so every poll raised a modal — and a
    # denial was indistinguishable from "absent", so the caller retried it.
    if service in _denied_services:
        return None
    try:
        status, raw = keychain.get_generic_password(service)
    except (keychain.KeychainError, OSError, ValueError):
        return None
    if status in keychain.DENIED_STATUSES:
        _denied_services.add(service)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return None


def _read_file_blob(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_creds_blob(account=None):
    """Return (store, blob_dict); store identifies a Keychain item or file.

    Current Claude Code gives every config directory its own hashed Keychain
    service, which is what makes simultaneous `CLAUDE_CONFIG_DIR` logins
    distinct. The default login also checks the old global service, and every
    login checks its legacy credential file, so upgrades do not strand either
    storage generation.

    A store that parses but carries no `claudeAiOauth.accessToken` is not an
    answer, so the search goes on rather than stopping at it. That item is
    shared: Claude Code also keeps per-MCP-server OAuth in it, and a blob left
    holding only `mcpOAuth` used to end the search on the Keychain and make the
    file unreachable — a quota that could not come back on its own even after a
    fresh login wrote a good token to disk. When nothing anywhere has a token
    the first store that parsed is still returned, so the failure is reported
    against the place the credentials are supposed to be.
    """
    service = _keychain_service(account)
    stores = [
        (_keychain_store(service), _read_keychain_blob(service)),
    ]
    if account is None:
        stores.append(("keychain", _read_keychain_blob(KEYCHAIN_SERVICE)))
    path = _creds_file(account)
    stores.append((path, _read_file_blob(path)))

    found = [(store, blob) for store, blob in stores if blob is not None]
    for store, blob in found:
        if _oauth_block(blob):
            return store, blob
    return found[0] if found else (None, None)


def _write_creds_blob(store, blob):
    raw = json.dumps(blob, separators=(",", ":"))
    service = _service_from_store(store)
    if service:
        acct = _keychain_account(service) or "Claude"
        # Via the Security framework, not `security -w`, which would put the
        # refresh token in argv where any process can read it out of `ps`.
        keychain.set_generic_password(service, acct, raw)
        return
    tmp = store + ".tmp"
    with open(tmp, "w") as f:
        f.write(raw)
    os.chmod(tmp, 0o600)
    os.replace(tmp, store)


def _oauth_block(blob):
    o = (blob or {}).get("claudeAiOauth") or {}
    if not o.get("accessToken"):
        return None
    return o


def credentials_present(account=None):
    """Whether this config directory has a usable Claude OAuth token."""
    _store, blob = _read_creds_blob(account)
    return bool(_oauth_block(blob))


def _credentials_hint(account=None):
    path = _creds_file(account)
    service = _keychain_service(account)
    if account:
        return f"Keychain service {service} or {path}"
    return (
        f"Keychain service {service}, legacy {KEYCHAIN_SERVICE}, "
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


def _refresh(oauth, store, blob):
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
            _write_creds_blob(store, blob)
        except Exception as exc:
            # Persisting failed (locked Keychain, read-only home). The token in
            # hand is still good for this process — don't throw the refresh away.
            print("oauth: could not persist refreshed token:", exc)
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


def fetch_quota(force=False, account=None):
    """Return quota dict, using a short in-memory cache. Never raises.

    `account` is an extra login from accounts.py (None = the default one).
    Everything below is per-account: its own cache, its own disk snapshot,
    and its own credential file to refresh tokens back into.
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

    def _keep_stale(err):
        return cache_util.keep_stale(
            cache, now, err, empty, disk_name=disk_name)

    try:
        store, blob = _read_creds_blob(account)
        if not store:
            return _keep_stale(
                f"no Claude credentials in {_credentials_hint(account)}")
        oauth = _oauth_block(blob)
        if not oauth:
            return _keep_stale(_shape_hint(store, blob))

        if _needs_refresh(oauth):
            try:
                oauth = _refresh(oauth, store, blob)
            except Exception:
                # still try the current token; it might work
                pass

        try:
            status, body = _http_get_usage(oauth["accessToken"])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                oauth = _refresh(oauth, store, blob)
                status, body = _http_get_usage(oauth["accessToken"])
            else:
                # 429 / 5xx — keep last good bars instead of wiping the page.
                return _keep_stale(f"HTTP Error {e.code}: {e.reason}")

        if status != 200:
            return _keep_stale(f"usage HTTP {status}")

        data = parse_usage(body, oauth)
        data["stale"] = False
        data["error"] = None
        return cache_util.store(cache, now, data, disk_name=disk_name)
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
