"""GitHub Actions failures for Headroom Activity.

Auth (never returned via /usage), in order:
  GITHUB_TOKEN / HEADROOM_GITHUB_TOKEN
  Keychain item com.centaur-labs.headroom.github / access-token
  `gh auth token` (if available)

Repos: always includes configured always-repos, plus remotes discovered under
configured `dev_root` whose owner matches any configured prefix (capped), plus
optional
~/.headroom/github.json → {"repos":["owner/name", ...]}.

Stdlib only. Failures degrade to {ok:false} with keep-stale.
"""

from __future__ import annotations

import json
import os
import subprocess
import keychain
import time
import urllib.error
import urllib.parse
import urllib.request

import app_config
import http_util
import cache_util

API = "https://api.github.com"
CACHE_TTL_S = 90
FAIL_TTL_S = 30
KEYCHAIN_SERVICE = "com.centaur-labs.headroom.github"
KEYCHAIN_ACCOUNT = "access-token"
CONFIG_PATH = os.path.expanduser("~/.headroom/github.json")
KEEP_RUNS = 8
UA = "Headroom/1"
# Only failures this fresh light Attention. Older runs still appear in the
# activity feed, but a 100-day-old red CI on some other repo shouldn't nag.
ATTENTION_FAIL_MAX_AGE_S = 24 * 3600
# Bot / advisory workflows — show in the feed if useful later, but don't
# inflate Attention fail counts (same PR often fans out into many of these).
NOISE_WORKFLOW_NAMES = {
    "claude code review",
    "claude",
    "codex review",
    "dependency review",
}

_cache = {"t": 0.0, "data": None}
_EMPTY = {
    "ok": False,
    "configured": False,
    "error": None,
    "runs": [],
    "fail_count": 0,
    "running_count": 0,
}


def _keychain_token():
    # Read in-process. Shelling out to security(1) makes /usr/bin/security the
    # caller, and it is on no item's ACL — so macOS prompts on every read, even
    # for items Headroom created itself. keychain.read_secret never shows UI.
    return keychain.read_secret(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def _gh_token():
    candidates = (
        "gh",
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        os.path.expanduser("~/.local/bin/gh"),
    )
    for binary in candidates:
        try:
            return subprocess.check_output(
                [binary, "auth", "token"],
                stderr=subprocess.DEVNULL,
                timeout=4,
                text=True,
            ).strip() or None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError, OSError):
            continue
    return None


def _token():
    for key in ("HEADROOM_GITHUB_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    token = _keychain_token()
    if token:
        return token
    return _gh_token()


def _config_repos():
    try:
        with open(CONFIG_PATH) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    repos = data.get("repos") if isinstance(data.get("repos"), list) else []
    out = []
    for item in repos:
        if isinstance(item, str) and "/" in item:
            out.append(item.strip().removesuffix(".git"))
    return out


def _is_git_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))


def _remote_slug(path):
    try:
        raw = subprocess.check_output(
            ["git", "-C", path, "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
            timeout=3,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return None
    if raw.startswith("git@github.com:"):
        raw = raw.removeprefix("git@github.com:")
    elif raw.startswith("https://github.com/"):
        raw = raw.removeprefix("https://github.com/")
    else:
        return None
    return raw.removesuffix(".git")


def _matches_owner(slug, prefixes):
    """No prefixes configured watches every discovered repo, as before."""
    if not prefixes:
        return True
    lowered = slug.lower()
    return any(lowered.startswith(prefix) for prefix in prefixes)


def _discover_org_repos():
    """Pick owner-prefix remotes under configured dev_root (shallow scan)."""
    found = []
    root = app_config.dev_root()
    prefixes = app_config.github_org_prefixes()
    max_discovered = app_config.github_max_discovered()
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return found
    candidates = []
    for name in entries:
        if name.startswith("."):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        paths = [path] if _is_git_repo(path) else []
        if not paths:
            try:
                children = os.listdir(path)
            except OSError:
                continue
            for child in children:
                if child.startswith("."):
                    continue
                child_path = os.path.join(path, child)
                if os.path.isdir(child_path) and _is_git_repo(child_path):
                    paths.append(child_path)
        for repo_path in paths:
            slug = _remote_slug(repo_path)
            if not slug or not _matches_owner(slug, prefixes):
                continue
            try:
                mtime = os.path.getmtime(repo_path)
            except OSError:
                mtime = 0
            candidates.append((mtime, slug))
    candidates.sort(reverse=True)
    for _, slug in candidates:
        if slug not in found:
            found.append(slug)
        if len(found) >= max_discovered:
            break
    return found


def _repos():
    ordered = []
    for slug in (
        list(app_config.github_always_repos())
        + _config_repos()
        + _discover_org_repos()
    ):
        if slug and slug not in ordered:
            ordered.append(slug)
    return ordered


def watched_repos():
    """The resolved watch list, for Settings to show before anything is polled."""
    return _repos()


def invalidate():
    """Config changed: drop the cached runs so the next poll re-reads the list."""
    _cache.update(t=0.0)


def _get(path, token, query=None, timeout=12):
    return http_util.request_json(
        API + path,
        auth=f"Bearer {token}",
        query=query,
        accept="application/vnd.github+json",
        user_agent=UA,
        timeout=timeout,
        headers={"X-GitHub-Api-Version": "2022-11-28"},
    )


def _parse_ts(value):
    if not value or not isinstance(value, str):
        return 0.0
    # 2026-07-23T12:00:00Z
    try:
        from datetime import datetime, timezone
        return datetime.strptime(
            value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z"
        ).timestamp()
    except ValueError:
        try:
            from datetime import datetime, timezone
            return datetime.strptime(
                value[:19] + "+0000", "%Y-%m-%dT%H:%M:%S%z"
            ).timestamp()
        except ValueError:
            return 0.0


def fmt_ago(unix_ts):
    if not unix_ts:
        return None
    ago_s = max(0, int(time.time() - float(unix_ts)))
    if ago_s < 60:
        return f"{ago_s}s"
    if ago_s < 3600:
        return f"{ago_s // 60}m"
    if ago_s < 86400:
        return f"{ago_s // 3600}h"
    return f"{ago_s // 86400}d"


def _flatten_run(repo, run):
    status = (run.get("status") or "").lower()
    conclusion = (run.get("conclusion") or "").lower()
    if status in ("queued", "in_progress", "waiting", "requested", "pending"):
        label = "running"
    elif conclusion in ("failure", "timed_out", "startup_failure"):
        label = "failure"
    elif conclusion == "cancelled":
        label = "cancelled"
    else:
        label = conclusion or status or "unknown"
    created = _parse_ts(run.get("updated_at") or run.get("run_started_at")
                        or run.get("created_at"))
    head_sha = run.get("head_sha") or ""
    return {
        "id": str(run.get("id") or f"{repo}:{head_sha}"),
        "repo": repo,
        "name": run.get("name") or run.get("display_title") or "Workflow",
        "display_title": run.get("display_title") or run.get("name"),
        "status": label,
        "conclusion": conclusion or None,
        "branch": run.get("head_branch"),
        "sha": head_sha or None,
        "short_sha": head_sha[:7] if head_sha else None,
        "url": run.get("html_url"),
        "created_at": created,
        "ago": fmt_ago(created),
        "event": run.get("event"),
    }


def _is_noise_workflow(run):
    name = (run.get("name") or run.get("display_title") or "").strip().lower()
    return name in NOISE_WORKFLOW_NAMES or name.startswith("claude code")


def _interesting(run):
    if _is_noise_workflow(run):
        return False
    status = (run.get("status") or "").lower()
    conclusion = (run.get("conclusion") or "").lower()
    if status in ("queued", "in_progress", "waiting", "requested", "pending"):
        return True
    # Skip cancelled — clutters the feed without wanting Attention.
    return conclusion in ("failure", "timed_out", "startup_failure")


def _fail_cluster_key(row):
    """One failed push / PR should count once even with many workflows."""
    return (
        row.get("repo") or "",
        row.get("sha") or row.get("branch") or row.get("id") or "",
    )


def _is_fresh_failure(row, now=None):
    """Failures without a timestamp stay fresh so we never hide unknown age."""
    if row.get("status") != "failure":
        return False
    created = row.get("created_at")
    if created is None:
        return True
    try:
        age = (now if now is not None else time.time()) - float(created)
    except (TypeError, ValueError):
        return True
    return age <= ATTENTION_FAIL_MAX_AGE_S


def attention_fail_count(rows, now=None):
    """Distinct fresh failure clusters that should light the Attention pip."""
    return len({
        _fail_cluster_key(row)
        for row in rows
        if _is_fresh_failure(row, now=now)
    })


def _fetch_repo_runs(token, repo):
    owner, _, name = repo.partition("/")
    if not owner or not name:
        return []
    path = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/actions/runs"
    try:
        payload = _get(path, token, query={"per_page": 12}, timeout=10)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise
    runs = payload.get("workflow_runs") or []
    return [
        _flatten_run(repo, run)
        for run in runs
        if isinstance(run, dict) and _interesting(run)
    ]


def fetch_actions(force=False):
    now = time.time()
    if cache_util.fresh(_cache, now, CACHE_TTL_S, FAIL_TTL_S, force):
        return _cache["data"]

    token = _token()
    if not token:
        result = {
            **_EMPTY,
            "error": "Connect GitHub in Headroom Settings (or `gh auth login`)",
            "stale": False,
            "updated_at": int(now),
            "repos": [],
        }
        if _cache["data"] and _cache["data"].get("ok"):
            return cache_util.keep_stale(_cache, now, result["error"], _EMPTY)
        _cache.update(t=now, data=result)
        return result

    repos = _repos()
    try:
        rows = []
        errors = []
        for repo in repos:
            try:
                rows.extend(_fetch_repo_runs(token, repo))
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):
                    raise
                errors.append(f"{repo}: HTTP {error.code}")
            except Exception as exc:
                errors.append(f"{repo}: {exc}")

        rows.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
        deduped = []
        seen = set()
        for row in rows:
            rid = row.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            deduped.append(row)
        rows = deduped[:KEEP_RUNS]
        fail_count = attention_fail_count(rows, now=now)
        running_count = sum(1 for row in rows if row.get("status") == "running")
        result = {
            "ok": True,
            "configured": True,
            "error": "; ".join(errors[:3]) if errors else None,
            "stale": False,
            "runs": rows,
            "fail_count": fail_count,
            "running_count": running_count,
            "repos": repos,
            "updated_at": int(now),
        }
        _cache.update(t=now, data=result, err=None)
        return result
    except urllib.error.HTTPError as error:
        message = (
            "GitHub token rejected"
            if error.code in (401, 403)
            else f"GitHub HTTP {error.code}"
        )
        if error.code in (401, 403):
            result = {
                **_EMPTY,
                "configured": True,
                "error": message,
                "stale": False,
                "updated_at": int(now),
                "repos": repos,
            }
            _cache.update(t=now, data=result, err=message)
            return result
        return cache_util.keep_stale(
            _cache, now, message, {**_EMPTY, "configured": True})
    except Exception as exc:
        return cache_util.keep_stale(
            _cache, now, str(exc), {**_EMPTY, "configured": True})
