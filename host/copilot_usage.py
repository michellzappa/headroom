"""GitHub Copilot premium / chat quota.

Uses the same GitHub token sources as Actions (`gh auth`, env, Keychain), then
`GET https://api.github.com/copilot_internal/user`. CodexBar-equivalent.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error

import http_util
import cache_util
import github_actions
import quota_util

CACHE_TTL_S = 60
FAIL_TTL_S = 20
DISK = "copilot_quota"
USAGE_URL = "https://api.github.com/copilot_internal/user"
UA = "GitHubCopilotChat/0.26.7"
MONTH_WINDOW_S = 30 * 86400

_cache = {"t": 0.0, "data": None, "err": None}
_EMPTY = {"ok": False, "plan": None, "premium": None, "chat": None}


# Copilot's own local state, written by the editor plugins and `gh copilot`.
# `apps.json` is the current layout, `hosts.json` the one before it; either
# naming github.com means this Mac has actually signed in to Copilot.
CONFIG_PATHS = (
    "~/.config/github-copilot/apps.json",
    "~/.config/github-copilot/hosts.json",
)


def has_token():
    """Whether a GitHub token exists to authenticate a Copilot fetch with."""
    return bool(github_actions._token())


def _local_config_signed_in():
    for path in CONFIG_PATHS:
        try:
            with open(os.path.expanduser(path)) as handle:
                blob = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict):
            continue
        for key, row in blob.items():
            if "github.com" not in str(key):
                continue
            if isinstance(row, dict) and (row.get("oauth_token")
                                          or row.get("token")):
                return True
    return False


def signed_in():
    """Whether this Mac shows evidence of Copilot itself — not of GitHub.

    This used to be `bool(github_actions._token())`, which answers a different
    question: "has anyone run `gh auth login`". First-run seeding enables every
    detected source, so anyone with a `gh` token got Copilot ticked for them,
    and a seat they do not have resolves to a permanent "GitHub token lacks
    Copilot access" on a row they never asked for.

    Two local signals, no network — `/setup` runs every probe per request:
      * Copilot's own config, written when an editor or `gh copilot` signs in;
      * a last-good quota snapshot, which only exists after the API confirmed
        a plan, so an entitlement that arrives through an org (no local config,
        Copilot never opened here) still latches on once the row has fetched.
    """
    if _local_config_signed_in():
        return True
    snapshot = cache_util.load_disk(DISK)
    return bool(isinstance(snapshot, dict) and snapshot.get("plan"))


def _fetch(token):
    return http_util.request_json(
        USAGE_URL,
        auth=f"token {token}",
        user_agent=UA,
        timeout=12,
        headers={
            "Editor-Version": "vscode/1.96.2",
            "Editor-Plugin-Version": "copilot-chat/0.26.7",
            "X-Github-Api-Version": "2025-04-01",
        },
    )


def _snapshot_used_pct(snapshots, key):
    if not isinstance(snapshots, dict):
        return None
    row = snapshots.get(key)
    if not isinstance(row, dict):
        return None
    # API reports percent_remaining; Headroom meters used %.
    if row.get("percent_remaining") is not None:
        return quota_util.remaining_pct_to_used(row["percent_remaining"])
    return quota_util.used_pct(row.get("used"), row.get("limit"))


def _map(blob):
    plan = blob.get("copilot_plan") or blob.get("plan") or blob.get("tier")
    if isinstance(plan, dict):
        plan = plan.get("name") or plan.get("slug")
    snapshots = blob.get("quota_snapshots") or blob.get("quotaSnapshots") or {}
    premium = _snapshot_used_pct(snapshots, "premium_interactions")
    if premium is None:
        premium = _snapshot_used_pct(snapshots, "premiumInteractions")
    chat = _snapshot_used_pct(snapshots, "chat")
    ok = premium is not None or chat is not None
    return {
        "ok": ok,
        "plan": str(plan).replace("_", " ").title() if plan else None,
        "error": None if ok else "no Copilot quota in response",
        "premium": quota_util.pool(premium, None, MONTH_WINDOW_S),
        "chat": quota_util.pool(chat, None, MONTH_WINDOW_S),
        "stale": False,
    }


def fetch_quota(force=False):
    now = time.time()
    if cache_util.fresh(_cache, now, CACHE_TTL_S, FAIL_TTL_S, force):
        return _cache["data"]

    token = github_actions._token()
    if not token:
        return cache_util.keep_stale(
            _cache, now,
            "Connect GitHub in Headroom Settings (or run `gh auth login`)",
            _EMPTY, disk_name=DISK, auth_required=True)

    try:
        blob = _fetch(token)
        out = _map(blob)
        if out.get("ok"):
            return cache_util.store(_cache, now, out, disk_name=DISK)
        return cache_util.keep_stale(
            _cache, now, out.get("error") or "Copilot quota unavailable",
            _EMPTY, disk_name=DISK)
    except urllib.error.HTTPError as exc:
        err = f"Copilot HTTP {exc.code}"
        if exc.code in (401, 403):
            err = "GitHub token lacks Copilot access"
        return cache_util.keep_stale(
            _cache, now, err, _EMPTY, disk_name=DISK)
    except Exception as exc:  # noqa: BLE001
        return cache_util.keep_stale(
            _cache, now, str(exc), _EMPTY, disk_name=DISK)
