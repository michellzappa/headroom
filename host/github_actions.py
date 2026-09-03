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
import time
import urllib.error
import urllib.parse
import urllib.request

import app_config
import http_util
import cache_util
import keychain

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
# Same rule for the inbox, on the longer clock a human review or assignment
# deserves. An issue nobody has touched in two weeks is aged debt, not
# attention: it kept coming back on every relaunch and taught people to
# ignore the queue. Aged rows stay in the feed and stop lighting the pip.
ATTENTION_INBOX_MAX_AGE_S = 14 * 24 * 3600
# Bot / advisory workflows — show in the feed if useful later, but don't
# inflate Attention fail counts (same PR often fans out into many of these).
NOISE_WORKFLOW_NAMES = {
    "claude code review",
    "claude",
    "codex review",
    "dependency review",
}
DISK = "github_actions"

_cache = {"t": 0.0, "data": None}
_EMPTY = {
    "ok": False,
    "configured": False,
    "error": None,
    "runs": [],
    "fail_count": 0,
    "running_count": 0,
    "inbox": [],
    "inbox_count": 0,
}
KEEP_INBOX = 12


def _keychain_token():
    return keychain.read_token(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


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


def _scan_dev_root_remotes():
    """GitHub remotes under configured `dev_root`, newest first.

    Owner filter and the discover cap are applied by the callers — Settings
    wants the whole list so a person can tick repos, while the Actions poll
    still respects owners + max_discovered.
    """
    root = app_config.dev_root()
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
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
            if not slug:
                continue
            try:
                mtime = os.path.getmtime(repo_path)
            except OSError:
                mtime = 0
            candidates.append((mtime, slug))
    candidates.sort(reverse=True)
    found = []
    for _, slug in candidates:
        if slug not in found:
            found.append(slug)
    return found


def _discover_org_repos():
    """Pick owner-prefix remotes under configured dev_root (shallow scan)."""
    prefixes = app_config.github_org_prefixes()
    max_discovered = app_config.github_max_discovered()
    found = []
    for slug in _scan_dev_root_remotes():
        if not _matches_owner(slug, prefixes):
            continue
        found.append(slug)
        if len(found) >= max_discovered:
            break
    return found


def discoverable_repos():
    """Every GitHub remote under `dev_root`, for the Settings picker.

    Capped at the same list limit as always-watch so a giant Dev folder cannot
    blow the Settings payload. Always-watch entries that are not on disk are
    appended so a selected remote-only repo still appears as checked.
    """
    limit = app_config.GITHUB_LIST_LIMIT
    found = _scan_dev_root_remotes()[:limit]
    for slug in app_config.github_always_repos():
        if slug and slug not in found:
            found.append(slug)
        if len(found) >= limit:
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


def inbox_is_fresh(item, now=None):
    """Does this inbox row still belong on Attention?

    Rows without a timestamp stay fresh, same as failures — unknown age is
    never a reason to hide something.
    """
    if not isinstance(item, dict):
        return False
    updated = item.get("created_at")
    if updated is None:
        return True
    try:
        age = (now if now is not None else time.time()) - float(updated)
    except (TypeError, ValueError):
        return True
    return age <= ATTENTION_INBOX_MAX_AGE_S


def attention_inbox(items, now=None):
    """Inbox rows fresh enough to light the Attention pip."""
    return [item for item in (items or []) if inbox_is_fresh(item, now=now)]


def _repo_slug_from_api_url(url):
    """`https://api.github.com/repos/acme/web` → `acme/web`."""
    if not isinstance(url, str) or not url:
        return None
    parts = url.rstrip("/").split("/")
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1]
    if not owner or not name:
        return None
    return f"{owner}/{name}"


def _flatten_inbox_item(item, reason):
    repo = _repo_slug_from_api_url(item.get("repository_url"))
    number = item.get("number")
    title = item.get("title") or "Untitled"
    created = _parse_ts(item.get("updated_at") or item.get("created_at"))
    is_pr = bool(item.get("pull_request")) or reason == "review_request"
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    author = user.get("login")
    if not isinstance(author, str) or not author.strip():
        author = None
    else:
        author = author.strip()
    return {
        "id": str(item.get("id") or f"{repo}#{number}"),
        "reason": reason,
        # Host verdict, so every client draws the same queue without each one
        # re-deriving the age rule. See docs/contract.md.
        "needs_attention": inbox_is_fresh({"created_at": created}),
        "repo": repo,
        "number": number,
        "title": title,
        "author": author,
        "url": item.get("html_url"),
        "is_pr": is_pr,
        "created_at": created,
        "ago": fmt_ago(created),
    }


def _search_inbox(token, query, reason, watched):
    """Open issues/PRs matching `query`, limited to watched repos."""
    try:
        payload = _get(
            "/search/issues",
            token,
            query={"q": query, "per_page": "30", "sort": "updated"},
            timeout=12,
        )
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 422):
            raise
        return []
    watched_lower = {slug.lower() for slug in watched}
    out = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        row = _flatten_inbox_item(item, reason)
        repo = (row.get("repo") or "").lower()
        if watched_lower and repo not in watched_lower:
            continue
        out.append(row)
    return out


# When the same issue/PR matches more than one search, keep the loudest reason.
_INBOX_REASON_RANK = {
    "review_request": 0,
    "assigned": 1,
    "mention": 2,
}


def fetch_inbox(token, repos):
    """Review requests, assignments, and @mentions on watched repos.

    Scoped to the watch list — not every @you on GitHub — so the Attention pip
    stays useful for someone watching CI and review on their own repos.
    """
    if not repos:
        return []
    try:
        user = _get("/user", token, timeout=8)
    except Exception:
        return []
    login = (user or {}).get("login")
    if not isinstance(login, str) or not login.strip():
        return []
    login = login.strip()
    rows = []
    rows.extend(_search_inbox(
        token,
        f"is:open is:pr review-requested:{login}",
        "review_request",
        repos,
    ))
    rows.extend(_search_inbox(
        token,
        f"is:open assignee:{login}",
        "assigned",
        repos,
    ))
    rows.extend(_search_inbox(
        token,
        f"is:open mentions:{login}",
        "mention",
        repos,
    ))
    best = {}
    for row in rows:
        key = row.get("id")
        if not key:
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = row
            continue
        rank = _INBOX_REASON_RANK.get(row.get("reason"), 99)
        prev_rank = _INBOX_REASON_RANK.get(prev.get("reason"), 99)
        if rank < prev_rank:
            best[key] = row
    return sorted(
        best.values(),
        key=lambda item: item.get("created_at") or 0,
        reverse=True,
    )[:KEEP_INBOX]


def attention_inbox_summary(items):
    """One-liner for the Attention card from inbox rows."""
    if not items:
        return None
    count = len(items)
    reviews = sum(1 for item in items if item.get("reason") == "review_request")
    assigned = sum(1 for item in items if item.get("reason") == "assigned")
    mentions = sum(1 for item in items if item.get("reason") == "mention")
    first = items[0]
    repo = (first.get("repo") or "").rsplit("/", 1)[-1]
    if count == 1 and repo:
        reason = first.get("reason")
        if reason == "review_request":
            return f"{repo} · review requested"
        kind = "PR" if first.get("is_pr") else "issue"
        if reason == "mention":
            return f"{repo} · mentioned on {kind}"
        return f"{repo} · assigned {kind}"
    bits = []
    if reviews:
        bits.append(f"{reviews} review request" + ("" if reviews == 1 else "s"))
    if assigned:
        bits.append(f"{assigned} assigned")
    if mentions:
        bits.append(f"{mentions} mention" + ("" if mentions == 1 else "s"))
    return " · ".join(bits) if bits else f"{count} GitHub inbox"


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
        inbox = []
        try:
            inbox = fetch_inbox(token, repos)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise
            errors.append(f"inbox: HTTP {error.code}")
        except Exception as exc:
            errors.append(f"inbox: {exc}")
        result = {
            "ok": True,
            "configured": True,
            "error": "; ".join(errors[:3]) if errors else None,
            "stale": False,
            "runs": rows,
            "fail_count": fail_count,
            "running_count": running_count,
            "inbox": inbox,
            "inbox_count": len(inbox),
            "repos": repos,
            "updated_at": int(now),
        }
        return cache_util.store(_cache, now, result, disk_name=DISK)
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
            _cache, now, message, {**_EMPTY, "configured": True},
            disk_name=DISK)
    except Exception as exc:
        return cache_util.keep_stale(
            _cache, now, str(exc), {**_EMPTY, "configured": True},
            disk_name=DISK)
