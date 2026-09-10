"""Personal Headroom config (~/.headroom/config.json).

Keeps machine-specific paths, timezone, git authors, Vercel team preference,
and GitHub org filters out of the code. Missing keys fall back to the defaults
below — copy config.example.json to ~/.headroom/config.json and edit.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

STORE_PATH = os.path.expanduser("~/.headroom/config.json")

DEFAULTS = {
    "timezone": "UTC",
    "dev_root": "~/Dev",
    "git_authors": [],
    "vercel_team_slugs": [],
    # Empty = every Supabase project the token can see.
    "supabase_project_refs": [],
    # String or list of strings; see github_org_prefixes().
    "github_org_prefix": [],
    "github_always_repos": [],
    "github_max_discovered": 6,
    "plausible_sites": [],
    "plausible_host": "https://plausible.io",
    "plausible_range": "24h",
    # Empty = every PostHog project the personal API key can see.
    "posthog_projects": [],
    "posthog_host": "https://us.posthog.com",
    "posthog_range": "24h",
    # Attention/Activity alert sources — org/site only; tokens stay in Keychain.
    "sentry_org": "",
    "datadog_site": "datadoghq.com",
    "axiom_host": "https://api.axiom.co",
    "axiom_org_id": "",
    # Authenticated iOS clients may use only these capabilities, and only from
    # a private/Tailscale address. Credential management remains Mac-local.
    "mobile_permissions": ["read", "refresh", "sources", "servers"],
    # The agent gateway is opt-in while its protocol and remote-control
    # surfaces are being introduced. Merely installing/updating Headroom must
    # never launch a coding agent behind the user's back.
    "agent_gateway_enabled": False,
    "codex_binary": "codex",
    # Passive agent notices are useful when coordinating several sessions, but
    # questions and approvals remain visible when this is off.
    "agent_alerts": True,
    # Escape hatch for a Gemini CLI install the host cannot find, or a future
    # layout it cannot read. The equivalent env vars are documented but do not
    # survive: the app rewrites the LaunchAgent plist on every host install.
    # These name the CLI to Google and are public constants, not a credential
    # of the user's — but they describe one machine's install, so they stay
    # out of SHARED_CONFIG_KEYS with the other paths.
    "gemini_oauth_client_id": "",
    "gemini_oauth_client_secret": "",
    # Multi-Mac. Off until asked for: sync writes usage data to a folder that
    # leaves the machine, and installing Headroom must not start doing that on
    # its own. See icloud_sync.py and docs/multi-mac.md.
    "icloud_sync": False,
    # Where peer machines meet. Empty means the default iCloud Drive folder;
    # point it anywhere that syncs (Dropbox, Syncthing) and the rest works
    # unchanged — nothing here is iCloud-specific but the default path.
    "icloud_dir": "",
    # The ESP32 desk display. Read by Settings → Desk display and shipped to
    # the board inside `/usage?view=device` as `display`; the board mirrors the
    # block to NVS so a cold boot without the host comes up the same way.
    # One board talks to one host, so none of this is in SHARED_CONFIG_KEYS.
    "display_brightness_pct": 75,
    "display_dim_at_night": False,
    # Local hours (0-23) in the configured time zone. Equal hours mean no
    # window; a window may cross midnight.
    "display_dim_start_hour": 22,
    "display_dim_end_hour": 7,
    "display_celebrate_resets": True,
    "display_boot_splash": True,
    # Pages the BOOT button cycles through, on top of the source being enabled
    # at all. Absent id means shown.
    "display_pages": {},
}

# Config keys that are the same person's answer on every Mac, so they follow
# them from one to the next. Everything absent from this tuple stays local,
# and two of those absences are load-bearing: `auth_token` is a credential,
# and `dev_root` / `codex_binary` are paths that describe one machine's disk.
SHARED_CONFIG_KEYS = (
    # One person has one notion of "today", and burndown history merges
    # across Macs (docs/metering.md decision 9) — two machines disagreeing
    # about where the day boundary falls would thin one curve against
    # another's buckets. Unlike dev_root, this is not a fact about a disk.
    "timezone",
    "git_authors",
    "vercel_team_slugs",
    "supabase_project_refs",
    "github_org_prefix",
    "github_always_repos",
    "github_max_discovered",
    "plausible_sites",
    "plausible_host",
    "plausible_range",
    "posthog_projects",
    "posthog_host",
    "posthog_range",
    "sentry_org",
    "datadog_site",
    "axiom_host",
    "axiom_org_id",
)

_lock = threading.Lock()
_cache = None


def _load():
    try:
        with open(STORE_PATH) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def reload():
    """Drop the in-memory cache (tests / after editing config.json)."""
    global _cache
    with _lock:
        _cache = None


def _persist(**updates):
    """Write keys into config.json, leaving every other key as the user left it.

    Read-modify-write through a temp file, 0600: this file holds an optional
    host token, and a half-written config would strand the app on defaults.
    """
    data = _load()
    data.update(updates)
    folder = os.path.dirname(STORE_PATH)
    os.makedirs(folder, exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, STORE_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    reload()


def raw():
    """Merged config: defaults overlaid by ~/.headroom/config.json."""
    global _cache
    with _lock:
        if _cache is None:
            merged = dict(DEFAULTS)
            merged.update(_load())
            _cache = merged
        return dict(_cache)


def get(key, default=None):
    cfg = raw()
    if key in cfg:
        return cfg[key]
    return default


def timezone_name():
    value = get("timezone") or DEFAULTS["timezone"]
    return str(value)


def set_timezone(value):
    """Persist the zone every day boundary is drawn in.

    Validated against the system tz database here rather than at read time:
    `timezone_name()` feeds `ZoneInfo(...)` on the request path, and an
    unknown name there would raise once per document instead of once at the
    moment somebody typed it.
    """
    name = str(value or "").strip()
    if not name:
        raise ValueError("timezone must be a zone name, e.g. Europe/Berlin")
    try:
        ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"unknown timezone {name!r}") from exc
    _persist(timezone=name)
    return name


# ---- Desk display -----------------------------------------------------------

# The four steps the Settings picker offers. Panel units are 0-255; 75% is
# within a few units of the 200 the firmware shipped with as a compile-time
# constant, so a board that has never been told anything looks the same.
DISPLAY_BRIGHTNESS_STEPS = (25, 50, 75, 100)
# Scheduled dimming: one toggle plus a start and an end hour. The level it
# fades to and how long the fade takes are product judgment (docs/product.md,
# "What earns a Setting"): 10% is what the board sat at after bedtime before
# dimming was removed in 2.0.9. The fade is served, not flashed — the host
# interpolates the brightness it hands the board, and a board polling once a
# minute sees about thirty steps of a few panel units each.
DISPLAY_DIM_BRIGHTNESS_PCT = 10
DISPLAY_DIM_RAMP_MIN = 30
# Pages the board can hide. Slots are the host's `focus` and stay out of this:
# an empty slot is a page that does not exist, not one that is hidden.
DISPLAY_PAGE_IDS = ("vercel", "git", "local")


def _display_bool(key):
    value = get(key, DEFAULTS[key])
    return bool(value) if isinstance(value, bool) else bool(DEFAULTS[key])


def display_brightness_pct():
    value = get("display_brightness_pct", DEFAULTS["display_brightness_pct"])
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULTS["display_brightness_pct"]
    return value if value in DISPLAY_BRIGHTNESS_STEPS else DEFAULTS["display_brightness_pct"]


def display_dim_at_night():
    return _display_bool("display_dim_at_night")


def _display_hour(key):
    value = get(key, DEFAULTS[key])
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULTS[key]
    return value if 0 <= value <= 23 else DEFAULTS[key]


def display_dim_start_hour():
    return _display_hour("display_dim_start_hour")


def display_dim_end_hour():
    return _display_hour("display_dim_end_hour")


def display_celebrate_resets():
    return _display_bool("display_celebrate_resets")


def display_boot_splash():
    return _display_bool("display_boot_splash")


def display_pages():
    """{page_id: shown} for every page the board can hide. Unknown ids drop."""
    raw_pages = get("display_pages", {})
    if not isinstance(raw_pages, dict):
        raw_pages = {}
    pages = {}
    for page_id in DISPLAY_PAGE_IDS:
        shown = raw_pages.get(page_id, True)
        pages[page_id] = shown if isinstance(shown, bool) else True
    return pages


def _display_minute_of_day(now):
    if now is None:
        now = time.time()
    try:
        zone = ZoneInfo(timezone_name())
    except Exception:
        zone = ZoneInfo("UTC")
    local = datetime.fromtimestamp(now, zone)
    return local.hour * 60 + local.minute + local.second / 60.0


def display_dim_fraction(now=None):
    """0.0 at the chosen level, 1.0 fully dimmed, in between during a fade.

    Minutes since the start hour and since the end hour are both taken modulo
    a day, so a window that crosses midnight needs no special case. Inside the
    window the first DISPLAY_DIM_RAMP_MIN minutes fade down; after the window
    the same span fades back. Equal hours are no window.
    """
    if not display_dim_at_night():
        return 0.0
    start = display_dim_start_hour() * 60
    end = display_dim_end_hour() * 60
    length = (end - start) % 1440
    if length == 0:
        return 0.0
    t = _display_minute_of_day(now)
    since_start = (t - start) % 1440
    since_end = (t - end) % 1440
    ramp = float(DISPLAY_DIM_RAMP_MIN)
    if since_start < length:
        return min(1.0, since_start / ramp)
    if since_end < ramp:
        return 1.0 - since_end / ramp
    return 0.0


def display_dimmed_now(now=None):
    return display_dim_fraction(now) > 0.0


def display_effective_brightness_pct(now=None):
    base = display_brightness_pct()
    fraction = display_dim_fraction(now)
    return int(round(base + (DISPLAY_DIM_BRIGHTNESS_PCT - base) * fraction))


def display_settings(now=None):
    """What Settings → Desk display shows and edits."""
    return {
        "brightness_pct": display_brightness_pct(),
        "brightness_steps": list(DISPLAY_BRIGHTNESS_STEPS),
        "dim_at_night": display_dim_at_night(),
        "dim_start_hour": display_dim_start_hour(),
        "dim_end_hour": display_dim_end_hour(),
        "dim_brightness_pct": DISPLAY_DIM_BRIGHTNESS_PCT,
        "dim_ramp_minutes": DISPLAY_DIM_RAMP_MIN,
        "dimmed_now": display_dimmed_now(now),
        "brightness_now_pct": display_effective_brightness_pct(now),
        "celebrate_resets": display_celebrate_resets(),
        "boot_splash": display_boot_splash(),
        "pages": display_pages(),
    }


def display_projection(now=None):
    """The `display` block the board reads from `/usage?view=device`.

    Effective values only: the board applies `brightness` as panel units and
    never learns whether it is dimmed because of a schedule, or mid-fade.
    Deciding is the host's job (docs/contract.md — the board is a render
    target), and so is the fade.
    """
    pct = display_effective_brightness_pct(now)
    return {
        "brightness": max(1, min(255, round(255 * pct / 100))),
        "celebrate_resets": display_celebrate_resets(),
        "boot_splash": display_boot_splash(),
        "pages": display_pages(),
    }


def set_display(brightness_pct=None, dim_at_night=None, dim_start_hour=None,
                dim_end_hour=None, celebrate_resets=None, boot_splash=None,
                pages=None):
    """Persist the desk display settings. Omitted arguments are left alone."""
    updates = {}
    for key, value in (("display_dim_start_hour", dim_start_hour),
                       ("display_dim_end_hour", dim_end_hour)):
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"{key[len('display_'):]} must be an hour, 0-23")
        try:
            hour = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key[len('display_'):]} must be an hour, 0-23")
        if not 0 <= hour <= 23:
            raise ValueError(f"{key[len('display_'):]} must be an hour, 0-23")
        updates[key] = hour
    if brightness_pct is not None:
        try:
            value = int(brightness_pct)
        except (TypeError, ValueError):
            raise ValueError("brightness_pct must be one of "
                             + ", ".join(str(s) for s in DISPLAY_BRIGHTNESS_STEPS))
        if value not in DISPLAY_BRIGHTNESS_STEPS:
            raise ValueError("brightness_pct must be one of "
                             + ", ".join(str(s) for s in DISPLAY_BRIGHTNESS_STEPS))
        updates["display_brightness_pct"] = value
    for key, value in (("display_dim_at_night", dim_at_night),
                       ("display_celebrate_resets", celebrate_resets),
                       ("display_boot_splash", boot_splash)):
        if value is None:
            continue
        if not isinstance(value, bool):
            raise ValueError(f"{key[len('display_'):]} must be true or false")
        updates[key] = value
    if pages is not None:
        if not isinstance(pages, dict):
            raise ValueError("pages must be an object of page id to true/false")
        current = display_pages()
        for page_id, shown in pages.items():
            if page_id not in DISPLAY_PAGE_IDS:
                raise ValueError(f"unknown page {page_id!r}")
            if not isinstance(shown, bool):
                raise ValueError(f"pages.{page_id} must be true or false")
            current[page_id] = shown
        # Store only the hidden ones: absent means shown, so the file stays
        # empty for a person who never touched this.
        updates["display_pages"] = {
            page_id: False for page_id, shown in current.items() if not shown
        }
    if updates:
        _persist(**updates)
    return display_settings()


def dev_root():
    value = get("dev_root") or DEFAULTS["dev_root"]
    return os.path.expanduser(str(value))


def git_authors():
    value = get("git_authors")
    if isinstance(value, list) and value:
        return [str(item) for item in value if str(item).strip()]
    return list(DEFAULTS["git_authors"])


def vercel_team_slugs():
    value = get("vercel_team_slugs")
    if isinstance(value, list) and value:
        return tuple(str(item).lower() for item in value if str(item).strip())
    return tuple(DEFAULTS["vercel_team_slugs"])


def github_org_prefixes():
    """Org filters for discovered repos: one string, or a list of them.

    Personal work rarely lives under a single owner — a repo under your own
    handle is as much yours as one under the org. Empty means no filter.
    """
    value = get("github_org_prefix")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return tuple(DEFAULTS["github_org_prefix"])
    out = []
    for item in value:
        text = str(item).strip().lower()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def github_always_repos():
    value = get("github_always_repos")
    if isinstance(value, list) and value:
        return tuple(str(item) for item in value if str(item).strip())
    return tuple(DEFAULTS["github_always_repos"])


def github_max_discovered():
    value = get("github_max_discovered", DEFAULTS["github_max_discovered"])
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return int(DEFAULTS["github_max_discovered"])


GITHUB_MAX_DISCOVERED_LIMIT = 50
GITHUB_LIST_LIMIT = 50
# owner/name, the only shape the Actions API takes.
_REPO_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _clean_list(values, label):
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    out = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        if text not in out:
            out.append(text)
    if len(out) > GITHUB_LIST_LIMIT:
        raise ValueError(f"{label}: at most {GITHUB_LIST_LIMIT} entries")
    return out


def set_github_watch(prefixes=None, always_repos=None, max_discovered=None):
    """Persist which Actions repos to watch. A None argument leaves that key.

    Raises ValueError with something worth showing a person: this is reached
    from Settings, where a typo like "acme" instead of "acme/api" is the most
    likely input and silently dropping it would read as the save failing.
    """
    updates = {}
    if prefixes is not None:
        updates["github_org_prefix"] = [
            item.lower() for item in _clean_list(prefixes, "owners")
        ]
    if always_repos is not None:
        repos = _clean_list(always_repos, "always_repos")
        for repo in repos:
            if not _REPO_SLUG.match(repo):
                raise ValueError(f"{repo!r} is not owner/name")
        updates["github_always_repos"] = repos
    if max_discovered is not None:
        try:
            count = int(max_discovered)
        except (TypeError, ValueError):
            raise ValueError("max_discovered must be a number") from None
        updates["github_max_discovered"] = max(
            0, min(GITHUB_MAX_DISCOVERED_LIMIT, count))
    if updates:
        _persist(**updates)
    return {
        "owners": list(github_org_prefixes()),
        "always_repos": list(github_always_repos()),
        "max_discovered": github_max_discovered(),
    }


def dev_root_setting():
    """`dev_root` as the user wrote it, tilde and all.

    `dev_root()` expands, which is what every scanner wants and exactly what
    Settings must not show: a field that answers `/Users/mz/Dev` to an edit of
    `~/Dev` looks like the save rewrote it.
    """
    value = get("dev_root") or DEFAULTS["dev_root"]
    return str(value)


def set_git_config(root=None, authors=None):
    """Persist where local commits are scanned from, and whose count.

    A None argument leaves that key. `root` is stored unexpanded so it stays
    the user's sentence, and is validated expanded, because a path that is not
    there produces an empty commit list that reads as a broken source.
    """
    updates = {}
    if root is not None:
        text = str(root).strip()
        if not text:
            raise ValueError("dev root cannot be empty")
        if not os.path.isdir(os.path.expanduser(text)):
            raise ValueError(f"{text} is not a folder on this Mac")
        updates["dev_root"] = text
    if authors is not None:
        updates["git_authors"] = _clean_list(authors, "authors")
    if updates:
        _persist(**updates)
    return {
        "dev_root": dev_root_setting(),
        "dev_root_path": dev_root(),
        "authors": list(git_authors()),
    }


def set_vercel_teams(slugs=None):
    """Persist which Vercel teams deployments are read from.

    Empty means every team the login can see, which is the useful default and
    the reason this is a filter rather than a required setting.
    """
    if slugs is not None:
        if isinstance(slugs, str):
            slugs = [slugs]
        if not isinstance(slugs, (list, tuple)):
            raise ValueError("teams must be a list")
        # Lowercase before `_clean_list`, not after: it dedupes, so folding
        # the case afterwards turns "Acme, acme" into the same slug twice.
        _persist(vercel_team_slugs=_clean_list(
            [str(item).lower() for item in slugs], "teams"))
    return {"teams": list(vercel_team_slugs())}


def supabase_project_refs():
    value = get("supabase_project_refs")
    if isinstance(value, list) and value:
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(DEFAULTS["supabase_project_refs"])


def set_supabase_projects(refs=None):
    """Persist which Supabase projects the portfolio reads.

    Empty means every project the token can see.
    """
    if refs is not None:
        if isinstance(refs, str):
            refs = [refs]
        if not isinstance(refs, (list, tuple)):
            raise ValueError("projects must be a list")
        _persist(supabase_project_refs=_clean_list(
            [str(item).strip() for item in refs], "projects"))
    return {"projects": list(supabase_project_refs())}


def plausible_sites():
    value = get("plausible_sites")
    if isinstance(value, list) and value:
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(DEFAULTS["plausible_sites"])


def set_plausible_sites(sites=None):
    """Persist which Plausible sites the card reads.

    Empty means every site the key can list (or every configured fallback).
    """
    if sites is not None:
        if isinstance(sites, str):
            sites = [sites]
        if not isinstance(sites, (list, tuple)):
            raise ValueError("sites must be a list")
        _persist(plausible_sites=_clean_list(
            [str(item).strip().lower() for item in sites], "sites"))
    return {"sites": list(plausible_sites())}


def plausible_host():
    value = get("plausible_host") or DEFAULTS["plausible_host"]
    return str(value).rstrip("/") or DEFAULTS["plausible_host"]


def _api_host(value, key):
    """An http(s) base URL a provider key will be sent to, or ValueError.

    The scheme is checked rather than merely tested for, because these hosts
    are the destination of an `Authorization: Bearer` header — a value like
    `file:///etc/passwd` contains `://` and would otherwise sail through to
    `urlopen`. Anything without a scheme is assumed https; anything with a
    scheme we do not speak is refused rather than quietly rewritten.
    """
    host = str(value or "").strip().rstrip("/")
    if not host:
        raise ValueError(f"{key} must be a URL")
    if "://" not in host:
        return "https://" + host
    if not host.startswith(("http://", "https://")):
        raise ValueError(f"{key} must be an http or https URL")
    return host


def set_plausible_host(value):
    """Persist the Plausible API host (cloud or self-hosted).

    Same shape as `set_posthog_host`. The key was in SHARED_CONFIG_KEYS and
    readable from the start, but had no setter and no payload field, so a
    self-hosted Plausible could only be reached by hand-editing config.json
    while the identical PostHog case had a picker.
    """
    host = _api_host(value, "plausible_host")
    _persist(plausible_host=host)
    return host


PLAUSIBLE_RANGES = ("day", "24h", "7d", "30d")
PLAUSIBLE_RANGE_LABELS = {
    "day": "today",
    "24h": "24h",
    "7d": "7d",
    "30d": "30d",
}


def plausible_range():
    value = str(get("plausible_range") or DEFAULTS["plausible_range"]).strip().lower()
    return value if value in PLAUSIBLE_RANGES else DEFAULTS["plausible_range"]


def plausible_range_label(range_id=None):
    rid = range_id or plausible_range()
    return PLAUSIBLE_RANGE_LABELS.get(rid, rid)


def set_plausible_range(value):
    """Persist the primary Plausible window without disturbing other config."""
    rid = str(value or "").strip().lower()
    if rid not in PLAUSIBLE_RANGES:
        raise ValueError(
            f"plausible_range must be one of {', '.join(PLAUSIBLE_RANGES)}")
    _persist(plausible_range=rid)
    return rid


def posthog_projects():
    value = get("posthog_projects")
    if isinstance(value, list) and value:
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(DEFAULTS["posthog_projects"])


def set_posthog_projects(projects=None):
    """Persist which PostHog projects the card reads.

    Empty means every project the key can list (or every configured fallback).
    """
    if projects is not None:
        if isinstance(projects, str):
            projects = [projects]
        if not isinstance(projects, (list, tuple)):
            raise ValueError("projects must be a list")
        _persist(posthog_projects=_clean_list(
            [str(item).strip() for item in projects], "projects"))
    return {"projects": list(posthog_projects())}


def posthog_host():
    value = get("posthog_host") or DEFAULTS["posthog_host"]
    return str(value).rstrip("/") or DEFAULTS["posthog_host"]


def set_posthog_host(value):
    """Persist the PostHog API host (US / EU / self-hosted)."""
    host = _api_host(value, "posthog_host")
    _persist(posthog_host=host)
    return host


POSTHOG_RANGES = ("day", "24h", "7d", "30d")
POSTHOG_RANGE_LABELS = {
    "day": "today",
    "24h": "24h",
    "7d": "7d",
    "30d": "30d",
}


def posthog_range():
    value = str(get("posthog_range") or DEFAULTS["posthog_range"]).strip().lower()
    return value if value in POSTHOG_RANGES else DEFAULTS["posthog_range"]


def posthog_range_label(range_id=None):
    rid = range_id or posthog_range()
    return POSTHOG_RANGE_LABELS.get(rid, rid)


def set_posthog_range(value):
    """Persist the primary PostHog window without disturbing other config."""
    rid = str(value or "").strip().lower()
    if rid not in POSTHOG_RANGES:
        raise ValueError(
            f"posthog_range must be one of {', '.join(POSTHOG_RANGES)}")
    _persist(posthog_range=rid)
    return rid


def sentry_org():
    value = get("sentry_org") or DEFAULTS["sentry_org"]
    return str(value).strip()


def set_sentry_org(value):
    org = str(value or "").strip()
    _persist(sentry_org=org)
    return org


def datadog_site():
    value = get("datadog_site") or DEFAULTS["datadog_site"]
    site = str(value).strip().lstrip(".")
    site = site.removeprefix("https://").removeprefix("http://")
    site = site.removeprefix("api.").removeprefix("app.")
    site = site.split("/")[0].strip() or DEFAULTS["datadog_site"]
    return site


def set_datadog_site(value):
    site = str(value or "").strip()
    if not site:
        raise ValueError("datadog_site must be a site like datadoghq.com")
    site = site.removeprefix("https://").removeprefix("http://")
    site = site.removeprefix("api.").removeprefix("app.")
    site = site.split("/")[0].strip().lstrip(".")
    if not site or " " in site:
        raise ValueError("datadog_site must be a site like datadoghq.com")
    _persist(datadog_site=site)
    return site


def axiom_host():
    value = get("axiom_host") or DEFAULTS["axiom_host"]
    return str(value).rstrip("/") or DEFAULTS["axiom_host"]


def set_axiom_host(value):
    host = str(value or "").strip().rstrip("/")
    if not host:
        raise ValueError("axiom_host must be a URL")
    if not (host.startswith("http://") or host.startswith("https://")):
        host = "https://" + host
    _persist(axiom_host=host)
    return host


def axiom_org_id():
    value = get("axiom_org_id") or DEFAULTS["axiom_org_id"]
    return str(value).strip()


def set_axiom_org_id(value):
    org = str(value or "").strip()
    _persist(axiom_org_id=org)
    return org


MOBILE_PERMISSION_ORDER = ("read", "refresh", "sources", "servers", "agents")


def mobile_permissions():
    value = get("mobile_permissions", DEFAULTS["mobile_permissions"])
    if not isinstance(value, list):
        return frozenset()
    allowed = set(MOBILE_PERMISSION_ORDER)
    return frozenset(str(item) for item in value if str(item) in allowed)


def set_mobile_permissions(values):
    """Persist the Mac-owned capability set without disturbing other config."""
    selected = {
        str(item) for item in values
        if str(item) in MOBILE_PERMISSION_ORDER
    }
    ordered = [item for item in MOBILE_PERMISSION_ORDER if item in selected]
    _persist(mobile_permissions=ordered)
    return frozenset(ordered)


MAX_TASK_FOLDERS = 8


def task_folders():
    """Folders you have started agent work in, most recent first.

    The Mac can open a folder picker; a phone cannot browse the Mac's disk, so
    it picks from what the Mac has already used.
    """
    value = get("agent_task_folders")
    if not isinstance(value, list):
        return []
    return [entry for entry in value
            if isinstance(entry, str) and entry][:MAX_TASK_FOLDERS]


def remember_task_folder(folder):
    """Move a folder to the front of the list, without duplicating it."""
    if not isinstance(folder, str) or not folder.strip():
        return task_folders()
    folder = folder.strip()
    remaining = [entry for entry in task_folders() if entry != folder]
    ordered = [folder] + remaining
    ordered = ordered[:MAX_TASK_FOLDERS]
    _persist(agent_task_folders=ordered)
    return ordered


QUESTION_MODES = ("off", "notify", "answer")


def agent_question_mode():
    """What Headroom does when Claude asks you something.

    `notify` — the default — posts the question and gets out of the way, so it
    appears in the terminal *and* on your phone. You answer where you already
    are. `answer` holds the call so the phone can answer it, which is the only
    way to answer remotely and also the reason the question cannot be answered
    at the Mac while it is held. `off` installs no hook at all.
    """
    value = get("agent_question_mode")
    if value in QUESTION_MODES:
        return value
    # The old boolean, read once so an existing opt-in is not silently lost.
    if get("agent_remote_questions") is True:
        return "answer"
    return "notify"


def set_agent_question_mode(mode):
    if mode not in QUESTION_MODES:
        raise ValueError(f"mode must be one of {', '.join(QUESTION_MODES)}")
    _persist(agent_question_mode=mode)
    return mode


def agent_gateway_enabled():
    """Whether Headroom may launch its supervised coding-agent adapter."""
    return get("agent_gateway_enabled") is True


def codex_binary():
    """Executable used for the Codex App Server child process."""
    value = str(get("codex_binary") or DEFAULTS["codex_binary"]).strip()
    return os.path.expanduser(value or DEFAULTS["codex_binary"])


def agent_alerts():
    """Whether passive coding-agent notices appear in the attention feed."""
    return get("agent_alerts") is True


def set_agent_alerts(enabled):
    if not isinstance(enabled, bool):
        raise ValueError("alerts must be true or false")
    _persist(agent_alerts=enabled)
    return agent_alerts()


def gemini_oauth_client():
    """Return (client_id, client_secret) overrides, empty strings if unset."""
    cid = str(get("gemini_oauth_client_id") or "").strip()
    secret = str(get("gemini_oauth_client_secret") or "").strip()
    return cid, secret


def set_agent_gateway(enabled=None, codex_binary_value=None):
    """Persist the Mac-owned Codex gateway settings."""
    updates = {}
    if enabled is not None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        updates["agent_gateway_enabled"] = enabled
    if codex_binary_value is not None:
        if not isinstance(codex_binary_value, str):
            raise ValueError("codex_binary must be a string")
        binary = codex_binary_value.strip()
        if not binary or len(binary) > 4096 or "\x00" in binary:
            raise ValueError("invalid codex_binary")
        updates["codex_binary"] = binary
    if updates:
        _persist(**updates)
    return {
        "enabled": agent_gateway_enabled(),
        "codex_binary": codex_binary(),
    }


def icloud_sync_enabled():
    """Whether this Mac publishes to, and reads, the shared machine folder."""
    return get("icloud_sync") is True


def icloud_dir():
    """Configured sync folder, or None to let icloud_sync pick the default."""
    value = str(get("icloud_dir") or "").strip()
    return os.path.expanduser(value) if value else None


def set_icloud_sync(enabled=None, directory=None):
    """Persist the multi-Mac settings. Returns them as stored."""
    updates = {}
    if enabled is not None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        updates["icloud_sync"] = enabled
    if directory is not None:
        if not isinstance(directory, str):
            raise ValueError("directory must be a string")
        folder = directory.strip()
        if len(folder) > 4096 or "\x00" in folder:
            raise ValueError("invalid directory")
        updates["icloud_dir"] = folder
    if updates:
        _persist(**updates)
    return {"enabled": icloud_sync_enabled(), "directory": icloud_dir()}


def shared_config():
    """The synced subset of config.json, as stored (absent keys omitted).

    Reads the file rather than `raw()` so a key the user has never set stays
    absent instead of syncing this build's default out to every other Mac as
    though it were a choice.
    """
    data = _load()
    return {k: data[k] for k in SHARED_CONFIG_KEYS if k in data}


def set_shared_config(updates):
    """Write synced config keys. Anything outside the whitelist is ignored.

    The filter is here rather than in the caller on purpose: this module owns
    the file that holds the host token, so it is the right place to be sure a
    sync can never write one.
    """
    clean = {
        k: v for k, v in (updates or {}).items() if k in SHARED_CONFIG_KEYS
    }
    # Three of these keys are the base URL a provider key gets sent to, so a
    # peer record that sets one is an exfiltration primitive for the matching
    # Keychain secret: point `plausible_host` at a host you control and every
    # Mac in the folder ships its token there on the next poll. The whitelist
    # was reasoned about as "no credentials"; a *destination* for a
    # credential is the same problem wearing a different key. The folder
    # transport is explicitly allowed to be Dropbox or Syncthing
    # (docs/multi-mac.md), which can have other participants.
    for key in ("plausible_host", "posthog_host", "axiom_host"):
        if key in clean:
            clean[key] = _api_host(clean[key], key)
    if clean:
        _persist(**clean)
    return clean


def attention_ack_fingerprint():
    value = get("attention_ack_fingerprint")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def set_attention_ack_fingerprint(value):
    """Persist acknowledge-until-new state shared by every client surface."""
    fingerprint = str(value or "").strip()
    if not fingerprint or len(fingerprint) > 4096:
        raise ValueError("invalid attention fingerprint")
    _persist(attention_ack_fingerprint=fingerprint)
    return fingerprint
