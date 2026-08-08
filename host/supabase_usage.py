"""Supabase project portfolio health and security advisors for Headroom.

Uses a Supabase Management API personal access token from, in order:
SUPABASE_ACCESS_TOKEN, the Headroom macOS Keychain item, or the Supabase CLI
fallback file. Tokens are never returned in payloads or logs.

Two signals, deliberately kept apart. Health answers "is the project up right
now"; advisors answer "it is up and misconfigured" — the RLS-disabled and
exposed-column lints Supabase emails about. Folding lints into `healthy` would
make one red dot mean two things and paint a fine project as down.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import keychain
import time
import urllib.error
import urllib.parse

import http_util
import cache_util

API = "https://api.supabase.com"
CACHE_TTL_S = 5 * 60
FAIL_TTL_S = 45
KEYCHAIN_SERVICE = "com.centaur-labs.headroom.supabase"
KEYCHAIN_ACCOUNT = "access-token"
CLI_TOKEN_PATH = os.path.expanduser("~/.supabase/access-token")
HEALTH_SERVICES = ("auth", "db", "rest", "realtime", "storage")
HEALTHY_WORDS = {
    "active_healthy", "healthy", "ok", "online", "running", "up",
}

# Advisors run a schema lint, so they move on the order of days, not minutes —
# a project-sized TTL of its own keeps the 5-minute portfolio poll from
# doubling the fan-out against the Management API rate limit.
ADVISOR_TTL_S = 30 * 60
ADVISOR_FAIL_TTL_S = 5 * 60
# Per project, so one repo with a hundred unindexed keys can't dominate the
# document every client downloads. Counts stay exact either way.
LINT_LIMIT = 40
LEVEL_RANK = {"ERROR": 0, "WARN": 1, "INFO": 2}

_cache = {"t": 0.0, "data": None}
# ref -> {"t": float, "lints": [...], "error": str | None}
_advisor_cache = {}
_EMPTY = {
    "ok": False,
    "configured": False,
    "error": None,
    "projects": [],
    "project_count": 0,
    "healthy_count": 0,
    "alert_count": 0,
    "lint_error_count": 0,
    "lint_warn_count": 0,
    "lint_total": 0,
}


def _keychain_token():
    # Read in-process. Shelling out to security(1) makes /usr/bin/security the
    # caller, and it is on no item's ACL — so macOS prompts on every read, even
    # for items Headroom created itself. keychain.read_secret never shows UI.
    return keychain.read_secret(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def _token():
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if token:
        return token.strip()
    token = _keychain_token()
    if token:
        return token
    try:
        with open(CLI_TOKEN_PATH) as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _get(path, token, query=None, timeout=15):
    return http_util.request_json(
        API + path, auth=f"Bearer {token}", query=query, timeout=timeout)


def _status_healthy(status):
    return str(status or "").strip().lower() in HEALTHY_WORDS


def _service_rows(payload):
    """Normalize the Management API's evolving health response."""
    rows = []
    source = payload
    if isinstance(payload, dict):
        source = (
            payload.get("services")
            or payload.get("result")
            or payload.get("data")
            or payload
        )
    if isinstance(source, dict):
        source = [
            {"service": name, **(value if isinstance(value, dict)
                                 else {"status": value})}
            for name, value in source.items()
        ]
    if not isinstance(source, list):
        return rows
    for item in source:
        if not isinstance(item, dict):
            continue
        name = (
            item.get("service")
            or item.get("name")
            or item.get("id")
            or item.get("type")
        )
        status = (
            item.get("status")
            or item.get("state")
            or item.get("health")
        )
        healthy = item.get("healthy")
        if healthy is None:
            healthy = _status_healthy(status)
        if name:
            rows.append({
                "name": str(name),
                "status": str(status or ("healthy" if healthy else "unhealthy")),
                "healthy": bool(healthy),
            })
    return rows


def _project_health(project, token):
    ref = project.get("ref") or project.get("id")
    if not ref:
        return [], "missing project ref"
    try:
        payload = _get(
            f"/v1/projects/{urllib.parse.quote(str(ref))}/health",
            token,
            query={
                "services": list(HEALTH_SERVICES),
                "timeout_ms": 3500,
            },
            timeout=8,
        )
        return _service_rows(payload), None
    except urllib.error.HTTPError as error:
        return [], f"HTTP {error.code}"
    except (urllib.error.URLError, OSError, ValueError) as error:
        return [], str(error)


def _lint_entity(metadata):
    if not isinstance(metadata, dict):
        return None
    entity = metadata.get("entity")
    if entity:
        return str(entity)
    schema, name = metadata.get("schema"), metadata.get("name")
    if schema and name:
        return f"{schema}.{name}"
    return str(name) if name else None


def _lint_rows(payload):
    """Normalize one advisors response into sorted lint rows.

    `name` stays a free string rather than being checked against the enum the
    Management API publishes: Supabase ships new lints between spec updates
    (`sensitive_columns_exposed` is already live and absent from it), and an
    unrecognized one is exactly the kind worth putting on screen.
    """
    source = payload
    if isinstance(payload, dict):
        source = payload.get("lints")
        if not isinstance(source, list):
            source = payload.get("result") or payload.get("data")
    if not isinstance(source, list):
        return []

    rows = []
    for item in source:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if not name:
            continue
        level = str(item.get("level") or "").strip().upper()
        if level not in LEVEL_RANK:
            # An unknown severity is not an excuse to hide the finding.
            level = "WARN"
        categories = item.get("categories")
        categories = [
            str(value).strip().upper()
            for value in (categories if isinstance(categories, list) else [])
            if value
        ]
        rows.append({
            "name": name,
            "title": str(item.get("title") or name.replace("_", " ")),
            "level": level,
            "categories": categories,
            "description": str(item.get("description") or "") or None,
            "detail": str(item.get("detail") or "") or None,
            "remediation": str(item.get("remediation") or "") or None,
            "entity": _lint_entity(item.get("metadata")),
        })

    rows.sort(key=lambda row: (
        LEVEL_RANK[row["level"]],
        row["name"],
        (row.get("entity") or ""),
    ))
    return rows


def _project_advisors(project, token, force=False):
    """Security lints for one project, cached per ref.

    The endpoint is flagged experimental *and* deprecated in the Management API
    spec, so every failure is soft: the portfolio still renders, the project
    carries an `advisor_error`, and a previous good answer is preferred over an
    empty list so a blip doesn't read as "all clear".
    """
    ref = project.get("ref") or project.get("id")
    if not ref:
        return [], "missing project ref"

    now = time.time()
    hit = _advisor_cache.get(ref)
    if hit and not force:
        ttl = ADVISOR_FAIL_TTL_S if hit.get("error") else ADVISOR_TTL_S
        if now - hit["t"] < ttl:
            return hit["lints"], hit.get("error")

    try:
        payload = _get(
            f"/v1/projects/{urllib.parse.quote(str(ref))}/advisors/security",
            token,
            timeout=12,
        )
        rows = _lint_rows(payload)
        _advisor_cache[ref] = {"t": now, "lints": rows, "error": None}
        return rows, None
    except urllib.error.HTTPError as error:
        message = (
            "advisors unavailable" if error.code in (404, 410, 501)
            else f"HTTP {error.code}"
        )
    except (urllib.error.URLError, OSError, ValueError) as error:
        message = str(error)

    previous = hit["lints"] if hit else []
    _advisor_cache[ref] = {"t": now, "lints": previous, "error": message}
    return previous, message


def _count_levels(lints):
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for lint in lints:
        counts[lint["level"]] = counts.get(lint["level"], 0) + 1
    return counts


def _flatten_project(project, services, health_error, lints, advisor_error):
    ref = project.get("ref") or project.get("id")
    status = project.get("status")
    unhealthy = [row["name"] for row in services if not row["healthy"]]
    project_healthy = _status_healthy(status)
    if services:
        project_healthy = project_healthy and not unhealthy
    levels = _count_levels(lints)
    return {
        "ref": ref,
        "name": project.get("name") or ref or "Supabase project",
        "organization_id": project.get("organization_id"),
        "region": project.get("region"),
        "status": status,
        "healthy": project_healthy,
        "services": services,
        "unhealthy_services": unhealthy,
        "health_error": health_error,
        "lints": lints[:LINT_LIMIT],
        "lint_truncated": len(lints) > LINT_LIMIT,
        "lint_error_count": levels["ERROR"],
        "lint_warn_count": levels["WARN"],
        "lint_info_count": levels["INFO"],
        "lint_total": len(lints),
        "advisor_error": advisor_error,
        "created_at": project.get("inserted_at") or project.get("created_at"),
        "dashboard_url": (
            f"https://supabase.com/dashboard/project/{ref}" if ref else None
        ),
    }


def _prune_advisor_cache(live_refs):
    """Drop lints for projects that left the portfolio."""
    for ref in [ref for ref in _advisor_cache if ref not in live_refs]:
        _advisor_cache.pop(ref, None)


def fetch_projects(force=False):
    now = time.time()
    if cache_util.fresh(_cache, now, CACHE_TTL_S, FAIL_TTL_S, force):
        return _cache["data"]

    token = _token()
    if not token:
        result = {
            **_EMPTY,
            "error": "Connect Supabase in Headroom Settings",
            "stale": False,
            "updated_at": int(now),
        }
        # Don't wipe a previously good portfolio if the Keychain briefly fails.
        if _cache["data"] and _cache["data"].get("ok"):
            return cache_util.keep_stale(
                _cache, now, result["error"], _EMPTY)
        _cache.update(t=now, data=result)
        return result

    try:
        raw = _get("/v1/projects", token)
        projects = raw if isinstance(raw, list) else (
            raw.get("projects") or raw.get("data") or [])
        health_by_ref = {}
        advisors_by_ref = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}
            for project in projects:
                ref = project.get("ref") or project.get("id")
                futures[pool.submit(_project_health, project, token)] = (
                    health_by_ref, ref, ([], "health unavailable"))
                futures[pool.submit(
                    _project_advisors, project, token, force)] = (
                    advisors_by_ref, ref, ([], "advisors unavailable"))
            for future, (sink, ref, fallback) in futures.items():
                try:
                    sink[ref] = future.result()
                except Exception as error:
                    sink[ref] = (fallback[0], str(error))

        rows = []
        for project in projects:
            ref = project.get("ref") or project.get("id")
            services, health_error = health_by_ref.get(
                ref, ([], "health unavailable"))
            lints, advisor_error = advisors_by_ref.get(
                ref, ([], "advisors unavailable"))
            rows.append(_flatten_project(
                project, services, health_error, lints, advisor_error))
        rows.sort(key=lambda row: (
            0 if not row["healthy"] else 1,
            0 if row["lint_error_count"] else 1,
            (row.get("name") or "").casefold(),
        ))
        _prune_advisor_cache({row["ref"] for row in rows})
        healthy_count = sum(1 for row in rows if row["healthy"])
        result = {
            "ok": True,
            "configured": True,
            "error": None,
            "stale": False,
            "projects": rows,
            "project_count": len(rows),
            "healthy_count": healthy_count,
            "alert_count": len(rows) - healthy_count,
            "lint_error_count": sum(row["lint_error_count"] for row in rows),
            "lint_warn_count": sum(row["lint_warn_count"] for row in rows),
            "lint_total": sum(row["lint_total"] for row in rows),
            "updated_at": int(now),
        }
        _cache.update(t=now, data=result, err=None)
        return result
    except urllib.error.HTTPError as error:
        message = "Supabase token rejected" if error.code in (401, 403) else (
            f"Supabase HTTP {error.code}")
        # Auth rejection is a hard miss — don't keep pretending we're connected.
        if error.code in (401, 403):
            _advisor_cache.clear()
            result = {
                **_EMPTY,
                "configured": True,
                "error": message,
                "stale": False,
                "updated_at": int(now),
            }
            _cache.update(t=now, data=result, err=message)
            return result
        return cache_util.keep_stale(_cache, now, message, {
            **_EMPTY, "configured": True,
        })
    except Exception as error:
        return cache_util.keep_stale(_cache, now, str(error), {
            **_EMPTY, "configured": True,
        })
