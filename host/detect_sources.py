"""Cheap local probes for first-run source defaults.

No network. Used to seed ~/.headroom/sources.json so a Claude-only machine
doesn't poll empty Codex/Cursor (and vice versa). Stdlib only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import keychain

import app_config
import copilot_usage
import gemini_usage
import jetbrains_usage
import oauth_usage
import windsurf_usage
import zed_usage


def claude_signed_in():
    return oauth_usage.credentials_present()


def codex_signed_in():
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    path = os.path.join(home, "auth.json")
    try:
        with open(path) as handle:
            blob = json.load(handle)
        return bool((blob.get("tokens") or {}).get("access_token"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def cursor_signed_in():
    path = os.path.expanduser(
        "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    )
    if not os.path.isfile(path):
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("cursorAuth/accessToken",),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    if not row or row[0] is None:
        return False
    val = row[0]
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    return bool(str(val).strip())


def vercel_signed_in():
    path = os.path.expanduser(
        "~/Library/Application Support/com.vercel.cli/auth.json"
    )
    try:
        with open(path) as handle:
            blob = json.load(handle)
        return bool(blob.get("token") or blob.get("refreshToken"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def git_available():
    root = app_config.dev_root()
    if not os.path.isdir(root):
        return False
    # Cheap: root itself is a repo, or any immediate child is.
    if os.path.isdir(os.path.join(root, ".git")):
        return True
    try:
        for name in os.listdir(root):
            if os.path.isdir(os.path.join(root, name, ".git")):
                return True
    except OSError:
        return False
    return False


def github_signed_in():
    if os.environ.get("HEADROOM_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return True
    # In-process: security(1) prompts even for our own items.
    if keychain.read_secret("com.centaur-labs.headroom.github", "access-token"):
        return True
    hosts = os.path.expanduser("~/.config/gh/hosts.yml")
    return os.path.isfile(hosts)


def supabase_signed_in():
    if os.environ.get("SUPABASE_ACCESS_TOKEN"):
        return True
    # In-process: security(1) prompts even for our own items.
    if keychain.read_secret("com.centaur-labs.headroom.supabase", "access-token"):
        return True
    path = os.path.expanduser("~/.supabase/access-token")
    try:
        with open(path) as handle:
            return bool(handle.read().strip())
    except OSError:
        return False


def plausible_signed_in():
    if (os.environ.get("PLAUSIBLE_API_KEY")
            or os.environ.get("HEADROOM_PLAUSIBLE_TOKEN")):
        return True
    # In-process: security(1) prompts even for our own items.
    return bool(keychain.read_secret("com.centaur-labs.headroom.plausible", "access-token"))



def local_available():
    return True


PROBES = {
    "claude": claude_signed_in,
    "codex": codex_signed_in,
    "cursor": cursor_signed_in,
    "copilot": copilot_usage.signed_in,
    "gemini": gemini_usage.signed_in,
    "windsurf": windsurf_usage.signed_in,
    "jetbrains": jetbrains_usage.signed_in,
    "zed": zed_usage.signed_in,
    "vercel": vercel_signed_in,
    "git": git_available,
    "github": github_signed_in,
    "local": local_available,
    "supabase": supabase_signed_in,
    "plausible": plausible_signed_in,
}


def detected_map():
    """{source_id: bool} for every known probe (missing ids omitted)."""
    out = {}
    for sid, probe in PROBES.items():
        try:
            out[sid] = bool(probe())
        except Exception:
            out[sid] = False
    return out


def suggested_enabled(source_ids, quota_ids=None):
    """First-run enabled flags: on only when a local credential/path exists.

    If no coding provider is detected, enable all quota sources so the UI
    still surfaces sign-in errors instead of an empty overview.
    """
    detected = detected_map()
    enabled = {sid: bool(detected.get(sid, False)) for sid in source_ids}
    if quota_ids is None:
        quota_ids = ("claude", "codex", "cursor")
    known = set(quota_ids)
    selected = [sid for sid in source_ids if sid in known]
    if selected and not any(enabled[sid] for sid in selected):
        for sid in selected:
            enabled[sid] = True
    # Local servers need no auth and are useful on day one.
    if "local" in enabled:
        enabled["local"] = True
    return enabled
