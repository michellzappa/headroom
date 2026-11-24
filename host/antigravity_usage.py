"""Google Antigravity quota via the local language server (with cloud fallback).

Antigravity (desktop app, IDE, and `agy` CLI) runs a local Connect-RPC
language server (`language_server*`) bound to loopback. Polling it
requires no external API keys and never triggers a Keychain prompt:
  1. `RetrieveUserQuotaSummary` — rolling 5-hour and weekly quota buckets.
  2. `GetUserStatus` — account plan tier + fallback per-model quotaInfo.
  3. `GetCommandModelConfigs` — secondary fallback for model quotaInfo.

Discovery checks both `~/.gemini/antigravity*/daemon/*.json` (where
Antigravity 2.34+ writes the server's loopback ports and CSRF token in a
user-only `0600` file instead of exposing `--csrf_token` in argv) and
running `language_server` processes from `ps` (older builds / IDE).

When Antigravity Desktop is closed (or only `agy` CLI is running), falls back
to calling `daily-cloudcode-pa.googleapis.com` (then `cloudcode-pa.googleapis.com`)
using the OAuth access token from `~/.gemini/antigravity-cli/antigravity-oauth-token`
(or `~/.gemini/antigravity/antigravity-oauth-token`, or legacy `state.vscdb`),
and otherwise preserves the last-known good snapshot on disk as stale.

A lightweight local state fingerprint (`daemon/*.json` + `antigravity-oauth-token`
mtimes) wakes polling immediately out of failure backoff when Antigravity opens
or `agy` refreshes its token, instead of waiting up to 15 minutes.

Stdlib only; Python 3.9+ compatible (LaunchAgent runs Xcode's Python 3.9).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import cache_util
import http_util
import quota_util

CACHE_TTL_S = 60
FAIL_TTL_S = 20
DISK = "antigravity_quota"
SESSION_WINDOW_S = 5 * 3600
WEEK_WINDOW_S = 7 * 86400
RPC_TIMEOUT_S = 4
CLOUD_TIMEOUT_S = 12

SERVICE_PREFIX = "/exa.language_server_pb.LanguageServerService"
METADATA = {
    "ideName": "antigravity",
    "extensionName": "antigravity",
    "ideVersion": "unknown",
    "locale": "en",
}

CLOUD_HOSTS = (
    "https://daily-cloudcode-pa.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
)
CLOUD_SUMMARY_PATH = "/v1internal:retrieveUserQuotaSummary"
CLOUD_QUOTA_PATH = "/v1internal:retrieveUserQuota"
CLOUD_TIER_PATH = "/v1internal:loadCodeAssist"
CLOUD_SUMMARY_URL = CLOUD_HOSTS[0] + CLOUD_SUMMARY_PATH
CLOUD_QUOTA_URL = CLOUD_HOSTS[0] + CLOUD_QUOTA_PATH
CLOUD_TIER_URL = CLOUD_HOSTS[0] + CLOUD_TIER_PATH
CLOUD_UA = "antigravity/hub/2.34.0"

OAUTH_TOKEN_NAME = "antigravity-oauth-token"
_ISO_FRAC_RE = re.compile(r"\.(\d+)")

STATE_DB_PATHS = (
    os.path.expanduser(
        "~/Library/Application Support/Antigravity/User/globalStorage/state.vscdb"
    ),
    os.path.expanduser(
        "~/Library/Application Support/Antigravity IDE/User/globalStorage/state.vscdb"
    ),
)
AUTH_STATUS_KEY = "antigravityAuthStatus"
AUTH_DETECT_KEYS = (
    "antigravityAuthStatus",
    "antigravityUnifiedStateSync.oauthToken",
    "antigravityUnifiedStateSync.userStatus",
    "jetskiStateSync.agentManagerInitState",
)
HOME_DIRS = (
    os.path.expanduser("~/.gemini/antigravity"),
    os.path.expanduser("~/.gemini/antigravity-cli"),
)
APP_PATHS = (
    "/Applications/Antigravity.app",
    os.path.expanduser("~/Applications/Antigravity.app"),
)
CLI_BINS = (
    os.path.expanduser("~/.local/bin/agy"),
    "/opt/homebrew/bin/agy",
    "/usr/local/bin/agy",
    "agy",
)

PS_BINS = ("/bin/ps", "/usr/bin/ps", "ps")
LSOF_BINS = ("/usr/sbin/lsof", "/usr/bin/lsof", "lsof")

_LS_BIN_RE = re.compile(
    r"(?:^|[/\\])language_server(?:_[a-z0-9_]+)?(?:\s|$)", re.I
)
_LSOF_PORT_RE = re.compile(
    r"(?:127\.0\.0\.1|localhost|\[::1\]|\*):(\d+)\s*\(LISTEN\)"
)

_cache = {"t": 0.0, "data": None, "err": None}
_EMPTY = {"ok": False, "plan": None, "week": None, "session": None}


def _find_bin(candidates):
    for cand in candidates:
        if os.path.isabs(cand):
            if os.access(cand, os.X_OK):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


def _split_cmd(cmd):
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _flag_value(tokens, name):
    """Extract `--name value` or `--name=value` from argv tokens."""
    prefix = f"--{name}="
    target = f"--{name}"
    for idx, tok in enumerate(tokens):
        if tok.startswith(prefix):
            val = tok[len(prefix):].strip()
            return val or None
        if tok == target and idx + 1 < len(tokens):
            val = tokens[idx + 1].strip()
            if val and not val.startswith("--"):
                return val
    return None


def _int_port(raw):
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return val if 1 <= val <= 65535 else None


def _is_antigravity_name(val):
    return "antigravity" in val or val == "agy" or val.startswith("agy-")


def _is_antigravity_server(cmd):
    """True if `cmd` is an Antigravity language server (not Windsurf/etc.)."""
    if not cmd or not _LS_BIN_RE.search(cmd):
        return False
    tokens = _split_cmd(cmd)
    app_dir = (_flag_value(tokens, "app_data_dir") or "").lower()
    if app_dir:
        return _is_antigravity_name(app_dir)
    ide_name = (_flag_value(tokens, "override_ide_name") or "").lower()
    if ide_name:
        return _is_antigravity_name(ide_name)
    lower = cmd.lower()
    if "windsurf" in lower:
        return False
    return "antigravity" in lower


def _parse_ps_output(text):
    """Parse `ps -ax -o pid=,command=` into candidate Antigravity servers."""
    servers = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        if not _is_antigravity_server(cmd):
            continue
        tokens = _split_cmd(cmd)
        servers.append({
            "pid": pid,
            "csrf_token": _flag_value(tokens, "csrf_token") or "",
            "https_port": _int_port(_flag_value(tokens, "https_server_port")),
            "ext_port": _int_port(_flag_value(tokens, "extension_server_port")),
        })
    # Prefer processes that carry an explicit CSRF token.
    servers.sort(key=lambda s: (0 if s["csrf_token"] else 1, s["pid"]))
    return servers


def _int_pid(raw):
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _parse_daemon_file(path, alive_check=None):
    """Parse one `~/.gemini/antigravity/daemon/*.json` discovery file."""
    try:
        with open(path, encoding="utf-8") as handle:
            blob = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(blob, dict):
        return None
    pid = _int_pid(blob.get("pid"))
    if not pid:
        return None
    check = _pid_alive if alive_check is None else alive_check
    if not check(pid):
        return None
    csrf = str(blob.get("csrfToken") or blob.get("csrf_token") or "").strip()
    https_port = _int_port(blob.get("httpsPort") or blob.get("https_port"))
    http_port = _int_port(blob.get("httpPort") or blob.get("http_port"))
    if not (csrf or https_port or http_port):
        return None
    return {
        "pid": pid,
        "csrf_token": csrf,
        "https_port": https_port,
        "http_port": http_port,
        "ext_port": None,
    }


def _read_daemon_servers(home_dirs=None, alive_check=None):
    """Read live Antigravity server metadata from `daemon/*.json` files."""
    servers = []
    for home in (HOME_DIRS if home_dirs is None else home_dirs):
        daemon_dir = os.path.join(home, "daemon")
        if not os.path.isdir(daemon_dir):
            continue
        try:
            entries = sorted(os.listdir(daemon_dir))
        except OSError:
            continue
        for name in entries:
            if not name.endswith(".json"):
                continue
            path = os.path.join(daemon_dir, name)
            if not os.path.isfile(path):
                continue
            parsed = _parse_daemon_file(path, alive_check=alive_check)
            if parsed:
                servers.append(parsed)
    return servers


def _find_ps_servers():
    ps_bin = _find_bin(PS_BINS)
    if not ps_bin:
        return []
    try:
        proc = subprocess.run(
            [ps_bin, "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_ps_output(proc.stdout)


def _merge_servers(daemon_servers, ps_servers):
    """Merge daemon discovery files and `ps` rows by PID."""
    by_pid = {}
    for row in (ps_servers or ()):
        by_pid[row["pid"]] = {
            "pid": row["pid"],
            "csrf_token": row.get("csrf_token") or "",
            "https_port": row.get("https_port"),
            "http_port": row.get("http_port"),
            "ext_port": row.get("ext_port"),
        }
    for row in (daemon_servers or ()):
        pid = row["pid"]
        cur = by_pid.get(pid)
        if cur is None:
            by_pid[pid] = {
                "pid": pid,
                "csrf_token": row.get("csrf_token") or "",
                "https_port": row.get("https_port"),
                "http_port": row.get("http_port"),
                "ext_port": row.get("ext_port"),
            }
        else:
            if row.get("csrf_token"):
                cur["csrf_token"] = row["csrf_token"]
            if row.get("https_port"):
                cur["https_port"] = row["https_port"]
            if row.get("http_port"):
                cur["http_port"] = row["http_port"]
            if row.get("ext_port") and not cur.get("ext_port"):
                cur["ext_port"] = row["ext_port"]
    merged = list(by_pid.values())
    merged.sort(key=lambda s: (
        0 if s["csrf_token"] else 1,
        0 if (s.get("https_port") or s.get("http_port")) else 1,
        s["pid"],
    ))
    return merged


def _find_servers():
    return _merge_servers(_read_daemon_servers(), _find_ps_servers())


def _parse_lsof_ports(text):
    """Extract deduplicated loopback listening TCP ports from `lsof` output."""
    ports = []
    seen = set()
    for match in _LSOF_PORT_RE.finditer(text or ""):
        port = _int_port(match.group(1))
        if port and port not in seen:
            seen.add(port)
            ports.append(port)
    return ports


def _listening_ports(pid):
    lsof_bin = _find_bin(LSOF_BINS)
    if not lsof_bin:
        return []
    try:
        proc = subprocess.run(
            [lsof_bin, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_lsof_ports(proc.stdout)


def _candidate_endpoints(server, ports=None):
    """Ordered deduplicated `(scheme, port)` candidates for a local server."""
    out = []
    seen = set()

    def add(scheme, port):
        if port and (scheme, port) not in seen:
            seen.add((scheme, port))
            out.append((scheme, port))

    https_port = server.get("https_port")
    http_port = server.get("http_port")
    ext_port = server.get("ext_port")
    if https_port:
        add("https", https_port)
    for port in (ports or ()):
        add("https", port)
    if http_port:
        add("http", http_port)
        add("https", http_port)
    for port in (ports or ()):
        add("http", port)
    if ext_port:
        add("http", ext_port)
        add("https", ext_port)
    return out


def _loopback_ssl_context():
    # Local language_server_macos uses a self-signed certificate on 127.0.0.1.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _rpc_post(scheme, port, method, csrf_token, body=None,
              timeout=RPC_TIMEOUT_S):
    """POST one Connect-RPC JSON call to 127.0.0.1. Returns (code, dict|None)."""
    url = f"{scheme}://127.0.0.1:{int(port)}{SERVICE_PREFIX}/{method}"
    payload = json.dumps(body if body is not None else {}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Connect-Protocol-Version": "1",
    }
    if csrf_token:
        headers["X-Codeium-Csrf-Token"] = csrf_token
    req = urllib.request.Request(
        url, data=payload, headers=headers, method="POST")
    handlers = [urllib.request.ProxyHandler({})]
    if scheme == "https":
        handlers.append(
            urllib.request.HTTPSHandler(context=_loopback_ssl_context()))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = None
            return resp.status, parsed if isinstance(parsed, dict) else None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else None
        except Exception:  # noqa: BLE001
            parsed = None
        return exc.code, parsed if isinstance(parsed, dict) else None
    except (urllib.error.URLError, OSError, ssl.SSLError, TimeoutError):
        return None, None


def _looks_like_connect_error(code, data):
    """True when an HTTP error still proves this port speaks Connect-RPC."""
    if code in (400, 404, 405, 501) and isinstance(data, dict):
        if "code" in data or "message" in data:
            return True
    return False


def _probe_endpoints(endpoints, csrf, req_body):
    """Probe `(scheme, port)` pairs until one answers Connect-RPC."""
    auth_err = None
    for scheme, port in endpoints:
        code, data = _rpc_post(
            scheme, port, "RetrieveUserQuotaSummary", csrf, req_body)
        if code == 200 and data is not None:
            return (scheme, port), data, None, auth_err
        if _looks_like_connect_error(code, data):
            return (scheme, port), None, None, auth_err
        if code == 401 and isinstance(data, dict) and data.get("message"):
            auth_err = auth_err or str(data["message"])
            continue
        # Probe GetUserStatus on the same endpoint before moving to the next
        # port, in case RetrieveUserQuotaSummary returned a bare 404.
        code2, data2 = _rpc_post(
            scheme, port, "GetUserStatus", csrf, req_body)
        if code2 == 200 and data2 is not None:
            return (scheme, port), None, data2, auth_err
        if code2 == 401 and isinstance(data2, dict) and data2.get("message"):
            auth_err = auth_err or str(data2["message"])
    return None, None, None, auth_err


def _query_server(server, ports=None):
    """Query one local language server; returns dict of responses or None."""
    csrf = server.get("csrf_token") or ""
    req_body = {"metadata": METADATA}

    working = None
    summary_blob = None
    status_blob = None
    configs_blob = None
    auth_err = None
    tried = set()

    if ports is not None:
        endpoints = _candidate_endpoints(server, ports)
        if not endpoints:
            return None
        working, summary_blob, status_blob, auth_err = _probe_endpoints(
            endpoints, csrf, req_body)
    else:
        if server.get("https_port") or server.get("http_port"):
            known = _candidate_endpoints(server, ())
            tried.update(known)
            working, summary_blob, status_blob, auth_err = _probe_endpoints(
                known, csrf, req_body)
        if working is None:
            discovered = _listening_ports(server["pid"])
            remaining = [
                ep for ep in _candidate_endpoints(server, discovered)
                if ep not in tried
            ]
            if remaining:
                working, summary_blob, status_blob, err2 = _probe_endpoints(
                    remaining, csrf, req_body)
                auth_err = auth_err or err2

    if working is None:
        if auth_err:
            return {
                "ok": False,
                "error": f"Antigravity language server rejected request ({auth_err})",
            }
        return None

    scheme, port = working
    if status_blob is None:
        code, data = _rpc_post(
            scheme, port, "GetUserStatus", csrf, req_body)
        if code == 200 and data is not None:
            status_blob = data

    # Only fall back to model-config endpoints if neither summary nor status
    # yielded usable buckets.
    merged = _map_combined(
        summary_blob=summary_blob, status_blob=status_blob)
    if not merged.get("ok"):
        for fallback_method in (
            "GetCascadeModelConfigData",
            "GetCommandModelConfigs",
            "GetAvailableModels",
        ):
            code, data = _rpc_post(
                scheme, port, fallback_method, csrf, req_body)
            if code == 200 and data is not None:
                configs_blob = data
                merged = _map_combined(
                    summary_blob=summary_blob,
                    status_blob=status_blob,
                    configs_blob=configs_blob,
                )
                if merged.get("ok"):
                    break
    return merged


# ---------------------------- state.vscdb & cloud fallback ----------------------------

def _read_db_keys(path, keys):
    if not os.path.isfile(path):
        return {}
    out = {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        try:
            placeholders = ",".join("?" for _ in keys)
            rows = con.execute(
                f"SELECT key, value FROM ItemTable WHERE key IN ({placeholders})",
                tuple(keys),
            ).fetchall()
            for k, val in rows:
                if val is None:
                    continue
                if isinstance(val, bytes):
                    val = val.decode("utf-8", errors="replace")
                out[k] = val
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return out


def _read_state_db_auth():
    """Return parsed `antigravityAuthStatus` dict from state.vscdb, or None."""
    for path in STATE_DB_PATHS:
        rows = _read_db_keys(path, (AUTH_STATUS_KEY,))
        raw = rows.get(AUTH_STATUS_KEY)
        if not raw:
            continue
        try:
            blob = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(blob, dict) and (blob.get("apiKey") or blob.get("email")):
            return blob
    return None


def _has_state_db_auth():
    for path in STATE_DB_PATHS:
        rows = _read_db_keys(path, AUTH_DETECT_KEYS)
        for val in rows.values():
            if str(val).strip():
                return True
    return False


def _local_install_present():
    if any(os.path.isdir(p) for p in HOME_DIRS):
        return True
    if any(os.path.isdir(p) for p in APP_PATHS):
        return True
    if any(os.path.isfile(p) for p in STATE_DB_PATHS):
        return True
    if _find_bin(CLI_BINS):
        return True
    return False


def _parse_iso_ts(value):
    """Parse an ISO-8601 timestamp to epoch seconds (Python 3.9 compatible)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        return ts / 1000.0 if ts > 1e12 else ts
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python 3.9's fromisoformat only accepts 3 or 6 fractional digits; Go's
    # RFC3339Nano (used by `agy`) emits arbitrary lengths like `.75591`.
    text = _ISO_FRAC_RE.sub(
        lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _read_oauth_token_files(home_dirs=None, now=None):
    """Read OAuth access tokens from `~/.gemini/antigravity*/antigravity-oauth-token`.

    `agy` CLI embeds its server instead of writing `daemon/*.json`, and keeps
    a fresh Google OAuth token in `antigravity-oauth-token` while active.
    Returns list of dicts `{access_token, expired, mtime}` ordered freshest first.
    """
    now_ts = time.time() if now is None else float(now)
    out = []
    for home in (HOME_DIRS if home_dirs is None else home_dirs):
        path = os.path.join(home, OAUTH_TOKEN_NAME)
        if not os.path.isfile(path):
            continue
        try:
            mtime = os.path.getmtime(path)
            with open(path, encoding="utf-8") as handle:
                blob = json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(blob, dict):
            continue
        tok = blob.get("token") if isinstance(blob.get("token"), dict) else blob
        access = str(tok.get("access_token") or tok.get("accessToken") or "").strip()
        if not access:
            continue
        exp_ts = _parse_iso_ts(tok.get("expiry") or tok.get("expires_at") or tok.get("expiresAt"))
        expired = exp_ts is not None and exp_ts <= now_ts
        out.append({
            "access_token": access,
            "expired": expired,
            "mtime": mtime,
            "path": path,
        })
    # Prefer non-expired tokens, then most recently written.
    out.sort(key=lambda row: (1 if row["expired"] else 0, -row["mtime"]))
    return out


def _has_oauth_token_file(home_dirs=None):
    for home in (HOME_DIRS if home_dirs is None else home_dirs):
        if os.path.isfile(os.path.join(home, OAUTH_TOKEN_NAME)):
            return True
    return False


def _local_state_stamp(home_dirs=None):
    """Cheap mtime fingerprint of local discovery + OAuth files (no subprocesses).

    Used to wake polling immediately out of failure backoff when Antigravity
    Desktop opens (`daemon/*.json`) or `agy` refreshes `antigravity-oauth-token`.
    """
    items = []
    for home in (HOME_DIRS if home_dirs is None else home_dirs):
        daemon_dir = os.path.join(home, "daemon")
        try:
            names = sorted(os.listdir(daemon_dir))
        except OSError:
            names = ()
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(daemon_dir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            items.append((path, st.st_mtime_ns))
        tok_path = os.path.join(home, OAUTH_TOKEN_NAME)
        try:
            st = os.stat(tok_path)
        except OSError:
            pass
        else:
            items.append((tok_path, st.st_mtime_ns))
    return tuple(items)


def _fetch_cloud_host(host, token):
    """Query one cloudcode-pa host. Returns (mapped_dict|None, auth_failed)."""
    summary_blob = None
    quota_blob = None
    tier_blob = None

    try:
        summary_blob = http_util.request_json(
            host + CLOUD_SUMMARY_PATH,
            auth=f"Bearer {token}",
            json_body={},
            method="POST",
            user_agent=CLOUD_UA,
            timeout=CLOUD_TIMEOUT_S,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return None, True
        summary_blob = None
    except Exception:  # noqa: BLE001
        summary_blob = None

    if not summary_blob:
        try:
            quota_blob = http_util.request_json(
                host + CLOUD_QUOTA_PATH,
                auth=f"Bearer {token}",
                json_body={},
                method="POST",
                user_agent=CLOUD_UA,
                timeout=CLOUD_TIMEOUT_S,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return None, True
            quota_blob = None
        except Exception:  # noqa: BLE001
            quota_blob = None

    if not summary_blob and not quota_blob:
        return None, False

    try:
        tier_blob = http_util.request_json(
            host + CLOUD_TIER_PATH,
            auth=f"Bearer {token}",
            json_body={"metadata": {"ideType": "ANTIGRAVITY"}},
            method="POST",
            user_agent=CLOUD_UA,
            timeout=CLOUD_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        tier_blob = None

    return _map_combined(
        summary_blob=summary_blob,
        configs_blob=quota_blob,
        tier_blob=tier_blob,
    ), False


def _fetch_cloud(token):
    """Best-effort cloudcode-pa fetch for `agy` CLI and closed Desktop app.

    Tries `daily-cloudcode-pa.googleapis.com` first (the endpoint Antigravity
    2.34+ uses for real quota buckets), falling back to `cloudcode-pa` only if
    that host fails or returns no buckets.
    """
    for host in CLOUD_HOSTS:
        out, auth_failed = _fetch_cloud_host(host, token)
        if out and out.get("ok"):
            return out
        if auth_failed:
            return None
    return None


# ---------------------------- payload parsing ----------------------------

def _normalize_plan(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    mapping = {
        "free": "Free",
        "free-tier": "Free",
        "starter": "Free",
        "individual": "Free",
        "standard-tier": "Paid",
        "legacy-tier": "Legacy",
        "pro": "Google AI Pro",
        "google-ai-pro": "Google AI Pro",
        "g1-pro-tier": "Google AI Pro",
        "ultra": "Google AI Ultra",
        "google-ai-ultra": "Google AI Ultra",
        "g1-ultra-tier": "Google AI Ultra",
        "g1-ultra-lite-tier": "Google AI Ultra",
    }
    return mapping.get(text.lower(), text)


def _extract_plan(blob):
    if not isinstance(blob, dict):
        return None
    status = blob.get("userStatus")
    if isinstance(status, dict):
        # Prefer `userTier` over `planStatus.planInfo`: Antigravity 2.34+
        # reports the real Google One tier in `userTier` (matching
        # `loadCodeAssist.paidTier`), while `planInfo.planName` is a legacy
        # stub ("Pro") even on Google AI Ultra accounts.
        tier = status.get("userTier")
        if isinstance(tier, dict):
            for key in ("name", "displayName", "id"):
                if tier.get(key):
                    return _normalize_plan(tier[key])
        elif isinstance(tier, str) and tier.strip():
            return _normalize_plan(tier)
        plan_status = status.get("planStatus")
        if isinstance(plan_status, dict):
            plan_info = plan_status.get("planInfo")
            if isinstance(plan_info, dict):
                for key in ("planName", "plan_name", "displayName",
                            "display_name", "teamsTier"):
                    if plan_info.get(key):
                        return _normalize_plan(plan_info[key])
        user = status.get("user")
        if isinstance(user, dict) and user.get("tier"):
            return _normalize_plan(user["tier"])

    # loadCodeAssist tier shape (shared with gemini_usage.py)
    paid = blob.get("paidTier")
    if isinstance(paid, list) and paid:
        name = (paid[0] or {}).get("name")
        if name:
            return _normalize_plan(name)
    elif isinstance(paid, dict) and paid.get("name"):
        return _normalize_plan(paid["name"])
    current = blob.get("currentTier") or blob.get("tier")
    if isinstance(current, dict):
        tid = current.get("name") or current.get("id")
        if tid:
            return _normalize_plan(tid)
    elif isinstance(current, str) and current.strip():
        return _normalize_plan(current)
    return None


def _parse_reset(row, now=None):
    raw = (
        row.get("resetTime")
        or row.get("reset_time")
        or row.get("resetsAt")
        or row.get("resets_at")
    )
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return quota_util.resets_from_unix(raw, now=now)
    return quota_util.resets_from_iso(raw, now=now)


def _used_pct_from_bucket(row, has_reset=False):
    """Compute used % from a bucket/quotaInfo dict.

    Proto3 JSON omits zero-valued floats, so an exhausted bucket may carry
    `resetTime` with `remainingFraction` omitted — treat that as 100% used.
    """
    frac = row.get("remainingFraction")
    if frac is None and "remaining_fraction" in row:
        frac = row.get("remaining_fraction")
    if frac is not None:
        try:
            return quota_util.remaining_pct_to_used(float(frac) * 100.0)
        except (TypeError, ValueError):
            pass

    rem_pct = row.get("remainingPercent")
    if rem_pct is None and "remaining_percent" in row:
        rem_pct = row.get("remaining_percent")
    if rem_pct is not None:
        return quota_util.remaining_pct_to_used(rem_pct)

    used = quota_util.used_pct(row.get("used"), row.get("limit"))
    if used is not None:
        return used

    if has_reset:
        return 100.0
    return None


def _classify_window(hint, resets_in):
    text = str(hint or "").lower()
    if any(tok in text for tok in ("week", "7d", "7_day", "seven")):
        return "week"
    if any(tok in text for tok in ("five", "5h", "5_h", "hour", "session",
                                   "daily", "day")):
        return "session"
    if resets_in is not None and resets_in > 24 * 3600:
        return "week"
    return "session"


def _aggregate_window(entries):
    """Return (pct, resets_in) for the tightest bucket in a window."""
    if not entries:
        return None, None
    max_pct = max(pct for pct, _ in entries)
    tied_resets = [
        reset for pct, reset in entries
        if reset is not None and (max_pct - pct) <= 0.5
    ]
    if tied_resets:
        return max_pct, min(tied_resets)
    all_resets = [reset for _, reset in entries if reset is not None]
    return max_pct, (min(all_resets) if all_resets else None)


def _collect_bucket(row, week_entries, session_entries, hint="", now=None):
    if not isinstance(row, dict):
        return
    if row.get("disabled") is True:
        return
    resets_in = _parse_reset(row, now=now)
    has_reset = bool(
        row.get("resetTime") or row.get("reset_time")
        or row.get("resetsAt") or row.get("resets_at")
    )
    pct = _used_pct_from_bucket(row, has_reset=has_reset)
    if pct is None:
        return
    combined_hint = " ".join(
        str(x) for x in (
            hint,
            row.get("window") or "",
            row.get("windowType") or "",
            row.get("bucketId") or "",
            row.get("displayName") or "",
        )
        if x
    )
    bucket_kind = _classify_window(combined_hint, resets_in)
    if bucket_kind == "week":
        week_entries.append((pct, resets_in))
    else:
        session_entries.append((pct, resets_in))


def _parse_quota_summary(blob, now=None):
    """Parse RetrieveUserQuotaSummary into (week_entries, session_entries)."""
    week_entries = []
    session_entries = []
    if not isinstance(blob, dict):
        return week_entries, session_entries

    root = blob.get("response") if isinstance(blob.get("response"), dict) else blob
    if isinstance(root.get("quotaSummary"), dict):
        root = root["quotaSummary"]

    groups = root.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            buckets = group.get("buckets")
            if not isinstance(buckets, list):
                continue
            for bucket in buckets:
                _collect_bucket(bucket, week_entries, session_entries, now=now)

    configs = root.get("clientModelConfigs")
    if isinstance(configs, list):
        for entry in configs:
            if not isinstance(entry, dict) or entry.get("disabled") is True:
                continue
            qinfo = entry.get("quotaInfo") or entry.get("quota_info") or {}
            windows = qinfo.get("windows") if isinstance(qinfo, dict) else None
            if isinstance(windows, list):
                for win in windows:
                    _collect_bucket(
                        win, week_entries, session_entries, now=now)
            elif isinstance(qinfo, dict):
                _collect_bucket(qinfo, week_entries, session_entries, now=now)

    return week_entries, session_entries


def _parse_model_configs(blob, now=None):
    """Parse GetUserStatus / GetCommandModelConfigs / retrieveUserQuota."""
    week_entries = []
    session_entries = []
    if not isinstance(blob, dict):
        return week_entries, session_entries

    configs = None
    status = blob.get("userStatus")
    if isinstance(status, dict):
        cascade = status.get("cascadeModelConfigData")
        if isinstance(cascade, dict) and isinstance(
                cascade.get("clientModelConfigs"), list):
            configs = cascade["clientModelConfigs"]

    if configs is None:
        cascade = blob.get("cascadeModelConfigData")
        if isinstance(cascade, dict) and isinstance(
                cascade.get("clientModelConfigs"), list):
            configs = cascade["clientModelConfigs"]

    if configs is None:
        for key in ("clientModelConfigs", "quotas", "buckets", "models",
                    "tokenBudgets"):
            val = blob.get(key)
            if isinstance(val, list):
                configs = val
                break
            if isinstance(val, dict):
                configs = list(val.values())
                break

    if not isinstance(configs, list):
        return week_entries, session_entries

    for entry in configs:
        if not isinstance(entry, dict) or entry.get("disabled") is True:
            continue
        qinfo = entry.get("quotaInfo") or entry.get("quota_info")
        if isinstance(qinfo, dict):
            windows = qinfo.get("windows")
            if isinstance(windows, list):
                for win in windows:
                    _collect_bucket(
                        win, week_entries, session_entries, now=now)
            else:
                _collect_bucket(qinfo, week_entries, session_entries, now=now)
        else:
            _collect_bucket(entry, week_entries, session_entries, now=now)

    return week_entries, session_entries


def _map_combined(summary_blob=None, status_blob=None, configs_blob=None,
                  tier_blob=None, now=None):
    plan = (
        _extract_plan(status_blob)
        or _extract_plan(summary_blob)
        or _extract_plan(tier_blob)
        or _extract_plan(configs_blob)
    )

    week_entries, session_entries = _parse_quota_summary(summary_blob, now=now)

    if not week_entries and not session_entries:
        week_entries, session_entries = _parse_model_configs(
            status_blob, now=now)

    if not week_entries and not session_entries:
        week_entries, session_entries = _parse_model_configs(
            configs_blob, now=now)

    week_pct, week_reset = _aggregate_window(week_entries)
    session_pct, session_reset = _aggregate_window(session_entries)

    ok = week_pct is not None or session_pct is not None
    return {
        "ok": ok,
        "plan": plan,
        "error": None if ok else "no Antigravity quota buckets",
        "week": quota_util.pool(week_pct, week_reset, WEEK_WINDOW_S),
        "session": quota_util.pool(session_pct, session_reset, SESSION_WINDOW_S),
        "stale": False,
    }


# ---------------------------- public entry points ----------------------------

def signed_in():
    """Cheap local detection — no network, no Keychain prompts."""
    if _find_servers():
        return True
    if _has_oauth_token_file():
        return True
    if _has_state_db_auth():
        return True
    if any(os.path.isdir(p) for p in HOME_DIRS):
        return True
    cached = cache_util.load_disk(DISK)
    return bool(cached and cached.get("ok"))


def fetch_quota(force=False):
    now = time.time()
    stamp = _local_state_stamp()
    wake = bool(_cache.get("err") and stamp != _cache.get("state_stamp"))
    if cache_util.fresh(_cache, now, CACHE_TTL_S, FAIL_TTL_S, force or wake):
        return _cache["data"]

    try:
        servers = _find_servers()
        last_err = None
        for server in servers:
            out = _query_server(server)
            if out and out.get("ok"):
                _cache["state_stamp"] = stamp
                return cache_util.store(_cache, now, out, disk_name=DISK)
            if out and out.get("error"):
                last_err = out["error"]

        # Local server wasn't running (or returned no buckets — e.g. only `agy`
        # CLI is running, Desktop is closed, or language_server returned empty
        # buckets after wake). Try the `agy` OAuth token file first, then legacy
        # `state.vscdb` auth, against daily-cloudcode-pa.googleapis.com.
        seen_tokens = set()
        for tok_row in _read_oauth_token_files(now=now):
            if tok_row.get("expired"):
                continue
            token = tok_row["access_token"]
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            cloud_out = _fetch_cloud(token)
            if cloud_out and cloud_out.get("ok"):
                _cache["state_stamp"] = stamp
                return cache_util.store(_cache, now, cloud_out, disk_name=DISK)

        auth = _read_state_db_auth()
        if auth and auth.get("apiKey") and auth["apiKey"] not in seen_tokens:
            cloud_out = _fetch_cloud(auth["apiKey"])
            if cloud_out and cloud_out.get("ok"):
                _cache["state_stamp"] = stamp
                return cache_util.store(_cache, now, cloud_out, disk_name=DISK)

        _cache["state_stamp"] = stamp
        if servers:
            err = last_err or "Antigravity language server returned no quota"
            return cache_util.keep_stale(
                _cache, now, err, _EMPTY, disk_name=DISK,
                fix="Check that you are signed in to Antigravity, then refresh Headroom.")

        if (_local_install_present() or _has_oauth_token_file()
                or _has_state_db_auth() or cache_util.load_disk(DISK)):
            return cache_util.keep_stale(
                _cache, now,
                "Antigravity is not running",
                _EMPTY, disk_name=DISK,
                auth_required=False,
                fix="Open Antigravity or run agy, then refresh Headroom.")

        return cache_util.keep_stale(
            _cache, now,
            "not signed in to Antigravity — open Antigravity and sign in",
            _EMPTY, disk_name=DISK, auth_required=True)
    except Exception as exc:  # noqa: BLE001
        _cache["state_stamp"] = stamp
        return cache_util.keep_stale(
            _cache, now, str(exc), _EMPTY, disk_name=DISK)
