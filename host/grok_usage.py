"""Grok Build (xAI) subscription info via the Grok CLI's ACP interface.

Reads nothing from the network directly: spawns `grok agent stdio` (the
official Agent Client Protocol mode of the Grok CLI) and asks it for the
`_x.ai/billing` extension method. That reuses the CLI's own OAuth session
in ~/.grok/auth.json, so there are no cookies or scraped endpoints here.

xAI currently exposes: subscription tier (e.g. "SuperGrok"), the weekly
usage period bounds, on-demand credit cap/used and prepaid balance. It does
NOT expose a percent-used figure for the subscription window itself, so the
only ring-worthy pool is on-demand credits (when a cap is configured).
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import time

import cache_util
import oauth_usage
import quota_util

CACHE_TTL_S = 240
FAIL_TTL_S = 30
DISK = "grok_quota"
AUTH_NAME = "auth.json"
WEEK_WINDOW_S = 7 * 86400
ACP_TIMEOUT_S = 20

_cache = {"t": 0.0, "data": None, "err": None}
_EMPTY = {"ok": False, "plan": None, "credits": None}


def _auth_path():
    return os.path.expanduser(os.path.join("~/.grok", AUTH_NAME))


def _binary():
    for cand in ("~/.grok/bin/grok", "~/.local/bin/grok",
                 "/usr/local/bin/grok", "/opt/homebrew/bin/grok"):
        path = os.path.expanduser(cand)
        if os.access(path, os.X_OK):
            return path
    return None


def signed_in():
    if _binary() is None:
        return False
    try:
        with open(_auth_path()) as handle:
            blob = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(blob, dict):
        return False
    return any(isinstance(v, dict) and v.get("key") for v in blob.values())


def _acp_billing(binary):
    """One-shot ACP round-trip: initialize, `_x.ai/billing`, terminate."""
    proc = subprocess.Popen(
        [binary, "agent", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        payload = (
            json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            }) + "\n" +
            json.dumps({
                "jsonrpc": "2.0", "id": 2,
                "method": "_x.ai/billing", "params": {},
            }) + "\n"
        ).encode("utf-8")
        proc.stdin.write(payload)
        proc.stdin.flush()

        deadline = time.time() + ACP_TIMEOUT_S
        buf = b""
        fd = proc.stdout.fileno()
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
            for line in buf.split(b"\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if msg.get("id") == 2:
                    if "result" in msg:
                        return msg["result"]
                    err = msg.get("error") or {}
                    raise RuntimeError(
                        err.get("message") or "billing call failed")
        raise RuntimeError("Grok CLI did not answer in time")
    finally:
        try:
            proc.kill()
        except OSError:
            pass


def _num(value):
    """xAI wraps numbers as {"val": n}; accept both shapes."""
    if isinstance(value, dict):
        value = value.get("val")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _map(blob):
    config = blob.get("config") if isinstance(blob, dict) else None
    if not isinstance(config, dict):
        config = {}
    tier = blob.get("subscription_tier") if isinstance(blob, dict) else None
    period = config.get("currentPeriod")
    if not isinstance(period, dict):
        period = {}
    resets_in = quota_util.resets_from_iso(
        period.get("end") or config.get("billingPeriodEnd"))

    cap = _num(config.get("onDemandCap"))
    used = _num(config.get("onDemandUsed"))
    pct = quota_util.used_pct(used, cap) if cap else None

    credits = quota_util.pool(pct, resets_in, WEEK_WINDOW_S)
    if cap is not None:
        if credits is None:
            credits = {
                "pct": pct,
                "resets_in_s": resets_in,
                "resets_in": oauth_usage.fmt_resets(resets_in),
                "window_s": WEEK_WINDOW_S,
            }
        remaining = None
        if used is not None:
            remaining = round(max(0.0, cap - used), 2)
        credits.update({
            "used_usd": used,
            "limit_usd": cap,
            "remaining_usd": remaining,
        })

    ok = bool(tier) or resets_in is not None
    return {
        "ok": ok,
        "plan": tier,
        "error": None if ok else "no Grok billing data",
        "credits": credits,
        "week_resets_in_s": resets_in,
        "week_resets_in": oauth_usage.fmt_resets(resets_in),
        "prepaid_balance": _num(config.get("prepaidBalance")),
        "stale": False,
    }


def fetch_quota(force=False):
    now = time.time()
    if cache_util.fresh(_cache, now, CACHE_TTL_S, FAIL_TTL_S, force):
        return _cache["data"]

    binary = _binary()
    if binary is None or not signed_in():
        return cache_util.keep_stale(
            _cache, now, "not signed in to Grok CLI", _EMPTY, disk_name=DISK)

    try:
        blob = _acp_billing(binary)
        out = _map(blob)
        if out.get("ok"):
            return cache_util.store(_cache, now, out, disk_name=DISK)
        return cache_util.keep_stale(
            _cache, now, out.get("error") or "Grok billing unavailable",
            _EMPTY, disk_name=DISK)
    except Exception as exc:  # noqa: BLE001
        return cache_util.keep_stale(
            _cache, now, str(exc), _EMPTY, disk_name=DISK)
