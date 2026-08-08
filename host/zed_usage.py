"""Zed editor plan + edit-prediction quota.

Reads the Zed internet-password Keychain item for https://zed.dev, then
`GET https://cloud.zed.dev/client/users/me`. CodexBar-equivalent; stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import keychain
import time
import urllib.error
import urllib.request

import http_util
import cache_util
import quota_util

CACHE_TTL_S = 60
FAIL_TTL_S = 20
DISK = "zed_quota"
ME_URL = "https://cloud.zed.dev/client/users/me"
KEYCHAIN_SERVER = "zed.dev"
UA = "Headroom/1"
MONTH_WINDOW_S = 30 * 86400

_cache = {"t": 0.0, "data": None, "err": None}
_EMPTY = {"ok": False, "plan": None, "predictions": None}


def _settings_server():
    path = os.path.expanduser("~/.config/zed/settings.json")
    try:
        with open(path) as handle:
            blob = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return KEYCHAIN_SERVER
    raw = blob.get("credentials_url") or blob.get("server_url") or ""
    text = str(raw).strip()
    if not text:
        return KEYCHAIN_SERVER
    text = text.replace("https://", "").replace("http://", "").rstrip("/")
    # Only trust Zed's known hosts — never forward Keychain tokens elsewhere.
    if text in ("zed.dev", "staging.zed.dev"):
        return text
    return KEYCHAIN_SERVER


def _keychain_creds():
    # All in-process. security(1) prompted on every call (it is on no item's
    # ACL), and `find-internet-password -g` additionally echoed the secret to
    # stderr. keychain.* never shows UI and never leaks to a stream.
    server = _settings_server()
    try:
        _, user_id, token = keychain.get_internet_password(server)
    except (keychain.KeychainError, OSError, ValueError):
        return None, None
    if not token:
        token = keychain.read_secret(f"https://{server}")
    return user_id, token


def signed_in():
    _, token = _keychain_creds()
    return bool(token)


def _fetch_me(user_id, token):
    auth = f"{user_id} {token}" if user_id else token
    return http_util.request_json(
        ME_URL, auth=auth, user_agent=UA, timeout=12)


def _map(blob):
    plan = blob.get("plan") if isinstance(blob, dict) else None
    if not isinstance(plan, dict):
        plan = {}
    label = plan.get("plan_v3") or plan.get("name") or plan.get("plan")
    if isinstance(label, str):
        label = label.replace("_", " ").strip().title()
    usage = plan.get("usage") if isinstance(plan.get("usage"), dict) else {}
    preds = usage.get("edit_predictions")
    pct = None
    if isinstance(preds, dict):
        if preds.get("unlimited"):
            pct = 0.0
        else:
            pct = quota_util.used_pct(preds.get("used"), preds.get("limit"))
    period = plan.get("subscription_period")
    resets_in = None
    if isinstance(period, dict):
        resets_in = quota_util.resets_from_iso(period.get("ended_at"))
    overdue = bool(plan.get("has_overdue_invoices"))
    ok = pct is not None or label is not None
    return {
        "ok": ok,
        "plan": label,
        "error": "overdue invoice" if overdue and ok else (
            None if ok else "no Zed plan data"),
        "predictions": quota_util.pool(pct, resets_in, MONTH_WINDOW_S),
        "stale": False,
    }


def fetch_quota(force=False):
    now = time.time()
    if cache_util.fresh(_cache, now, CACHE_TTL_S, FAIL_TTL_S, force):
        return _cache["data"]

    user_id, token = _keychain_creds()
    if not token:
        return cache_util.keep_stale(
            _cache, now, "not signed in to Zed", _EMPTY, disk_name=DISK)

    try:
        blob = _fetch_me(user_id, token)
        out = _map(blob)
        if out.get("ok"):
            return cache_util.store(_cache, now, out, disk_name=DISK)
        return cache_util.keep_stale(
            _cache, now, out.get("error") or "Zed quota unavailable",
            _EMPTY, disk_name=DISK)
    except urllib.error.HTTPError as exc:
        return cache_util.keep_stale(
            _cache, now, f"Zed HTTP {exc.code}", _EMPTY, disk_name=DISK)
    except Exception as exc:  # noqa: BLE001
        return cache_util.keep_stale(
            _cache, now, str(exc), _EMPTY, disk_name=DISK)
