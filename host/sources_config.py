"""The source registry: one entry per thing Headroom watches.

This is the single place that knows a source exists. Adding one means adding a
row to BASE_SOURCES — the HTTP payload, the Mac Settings list, the ESP32
footer, the poll schedule, and the log line all follow from it. Enabled flags
persist to ~/.headroom/sources.json; both Mac Settings and the ESP32 read them
back via /usage → sources[].

Quota providers (Claude / Codex / Cursor / …) are sources with kind="quota"
plus pool specs. POOLS, daily-burn headlines, and /usage → providers[] all
derive from those rows — so a Claude-only user can disable the rest, and a
new provider is one registry entry + a fetcher rather than a client enum.

`kind` is about metering; `group` is about what the thing *is*. Onboarding and
Settings split rows by group: the AI coding tools you're signed into locally
(no keys to paste) versus the dev tools you connect with a token. Section
titles live in Shared/HeadroomCopy.swift — the registry only owns membership.

A provider whose credentials live at a path you can point elsewhere carries an
`account_kind`, and then a second login is a second row: the registry expands
`claude` into `claude` plus `claude:work` at import (see accounts.py). Every
consumer already iterates the registry, so extra accounts get their own poll,
their own samples, their own burndown and their own ring for free — and the
default login keeps the bare id, so nothing stored under it moves.

Stdlib only.
"""

from __future__ import annotations

import functools
import json
import os
import re
import threading
from typing import Callable, NamedTuple, Optional

import accounts
import claude_status
import codex_usage
import copilot_usage
import cursor_usage
import detect_sources
import gemini_usage
import git_activity
import github_actions
import grok_usage
import jetbrains_usage
import local_servers
import oauth_usage
import plausible_usage
import supabase_usage
import vercel_builds
import windsurf_usage
import zed_usage

STORE_PATH = os.path.expanduser("~/.headroom/sources.json")

# Onboarding / Settings groups. Order is the order sections render in.
GROUP_AI = "ai"              # coding models you're already signed into
GROUP_DEVTOOLS = "devtools"  # everything else you point Headroom at
GROUP_IDS = (GROUP_AI, GROUP_DEVTOOLS)


# ---------------------------- meter kinds ----------------------------
# What shape a meter is, which is what decides what its numbers mean and which
# mark draws it. Ships as `providers[].pools.<id>.kind`; see docs/metering.md.
#
# **Not** `Source.kind` below. That one is a different axis entirely — it says
# whether a *source* feeds the quota machinery at all (quota vs activity). The
# two words are unavoidable: one describes a service, one describes a meter.
#
# Only KIND_WINDOW is implemented. The rest are named anyway, because the set
# *is* the design: a form with no name here is a form that arrives as flat keys
# bolted onto a provider payload, which is the thing docs/metering.md exists to
# stop. Naming one costs a string; discovering you needed it costs a schema.
KIND_WINDOW = "window"      # % of a pool that refills on a clock
KIND_GRANT = "grant"        # countable items, each with its own expiry
KIND_OVERAGE = "overage"    # a window, then dollars once the window is spent
KIND_CALENDAR = "calendar"  # dollars accrued against a month boundary
KIND_BALANCE = "balance"    # dollars remaining; depletes and never refills
KIND_RATE = "rate"          # per-minute utilisation, true for sixty seconds
KIND_SEAT = "seat"          # a flat licence — no meter at all, and that is the
                            # point: a monthly total that omits it is wrong
METER_KINDS = (KIND_WINDOW, KIND_GRANT, KIND_OVERAGE, KIND_CALENDAR,
               KIND_BALANCE, KIND_RATE, KIND_SEAT)

# Where a meter's numbers came from. Orthogonal to kind — any kind can be
# either. `observed` is the provider's own reading. `estimated` is one this
# host derived from local token counts, and it never reaches a surface without
# saying so, because nobody audits a percentage against a card statement but
# everybody audits a dollar. See docs/metering.md decision 3.
BASIS_OBSERVED = "observed"
BASIS_ESTIMATED = "estimated"


class MeterSpec(NamedTuple):
    """One meter on a provider payload (session, week, total, …).

    Every meter that exists today is a window, which is why `kind` defaults to
    the honest answer instead of being a required argument: the registry rows
    below are unchanged by this field existing, and that is the whole point of
    landing it before anything needs it.
    """

    id: str
    key: str
    title: str
    default_window_s: Optional[int] = None
    # Sampled for burndown history even when False; False hides the ring.
    ring: bool = True
    kind: str = KIND_WINDOW
    basis: str = BASIS_OBSERVED


# The wire key stays `pools`, and so does `Source.pools`; this rename is
# Python-side only. Kept as an alias so an in-flight branch that imported the
# old name still resolves — several agents work in this tree at once.
PoolSpec = MeterSpec


class Source(NamedTuple):
    """Everything the host needs to know about one watched service.

    `detail`, `summary` and `blank` are methods, not fields: a plain quota
    source derives all three from its `pools` and `headline`, so a new
    provider is a registry row plus a fetcher and nothing else. The `*_fn`
    overrides exist for the payloads that carry more than pools — Codex
    credits, Cursor's shared billing cycle, and every non-quota source.
    """

    id: str
    title: str
    hint: str
    poll_s: int          # how often the background poller refreshes it
    fetch: Callable      # fetch(force=bool) -> payload dict, never raises
    # Overrides for the generic pool-derived versions below. None means
    # "derive it", which is the right answer for most quota sources.
    detail_fn: Optional[Callable] = None   # (payload) -> status string or None
    summary_fn: Optional[Callable] = None  # (payload) -> log line body
    blank_fn: Optional[Callable] = None    # () -> shape before first fetch
    # "quota" feeds burndown / daily burn / provider tabs; everything else is
    # activity (Vercel, git, …) and only shows in Settings + ship-status panes.
    kind: str = "activity"
    # GROUP_AI or GROUP_DEVTOOLS — which Settings/onboarding section it lands
    # in. Independent of kind: a dev tool could grow a quota meter one day.
    group: str = GROUP_DEVTOOLS
    pools: tuple = ()
    # Daily-burn headline: try these pool keys in order (first pct wins).
    headline: tuple = ()
    # If every headline key is missing, use max(pct) across these (Cursor).
    headline_fallback_max: tuple = ()
    # #RRGGBB — shared with firmware COL_* and macos UsageProvider.tint.
    accent: Optional[str] = None
    # Where a granted reset gets explained, when the provider explains them
    # somewhere. A permalink only — Headroom never fetches it, so the page can
    # change shape without breaking anything here, and nothing about your
    # account leaves the Mac. None for providers that announce nothing.
    reset_note_url: Optional[str] = None
    # Extra logins. accounts.KIND_DIR when a second account is a second
    # credential *directory* (Claude, Codex, Gemini), accounts.KIND_FILE when
    # it is the credential store itself (Cursor / Windsurf state.vscdb).
    # None means one login per Mac and Settings offers no Add button.
    account_kind: Optional[str] = None
    # Filename inside a KIND_DIR account, so Settings can say whether the
    # folder someone picked holds legacy file credentials. The fetcher owns
    # the constant; this is a reference to it, not a second copy.
    account_file: Optional[str] = None
    # What Settings shows as the example location for a new account.
    account_hint: Optional[str] = None
    # Optional provider-owned credential probe. Claude needs this because
    # current Claude Code stores one hashed Keychain item per config directory
    # instead of a file inside that directory.
    account_probe: Optional[Callable] = None
    # Set on rows the expansion created; None on the default login.
    account: Optional[object] = None

    # ---- meter selection ----

    def windows(self):
        """The meters shaped like a percentage of a pool that refills.

        Every consumer written before meter kinds existed means *this*, not
        `pools`: a percentage to average, a pool to sample into the burndown
        store, a headline for the row to speak for. Handing one of the other
        kinds to that machinery does not fail loudly — it logs `credits=None%`
        every poll, or appends a null-pct row to `quota_samples.jsonl`, which
        is an append-only user asset (docs/product.md) and not a place to
        discover a shape mistake later.

        `pools` stays the full list, for the two consumers that genuinely want
        every meter: the `providers[]` payload and `blank()`.
        """
        return tuple(spec for spec in self.pools if spec.kind == KIND_WINDOW)

    # ---- derived presentation (override with the *_fn fields above) ----

    def _headline_pool(self):
        """The pool a one-line status speaks for: the first headline key the
        row declares, else its first window.

        First *window*, not first meter — a row that declares a grant ahead of
        its windows would otherwise silently change what the Mac Settings line
        and the ESP32 footer are talking about.
        """
        if self.headline:
            return self.headline[0]
        windows = self.windows()
        return windows[0].id if windows else None

    def detail(self, payload):
        """Short status for the Mac Settings row and the ESP32 footer."""
        if self.detail_fn is not None:
            return self.detail_fn(payload)
        key = self._headline_pool()
        if key is None:
            return payload.get("error")
        return _window_detail(payload, key, key)

    def summary(self, payload):
        """Log line body for a good fetch, under the LaunchAgent."""
        if self.summary_fn is not None:
            return self.summary_fn(payload)
        bits = [f"plan={payload.get('plan')}"]
        for spec in self.windows():
            pct = (payload.get(spec.key) or {}).get("pct")
            bits.append(f"{spec.id}={pct}%")
        return "  ".join(bits)

    def blank(self):
        """The payload shape before the first fetch.

        Every meter, not just the windows: this is about which keys the
        payload has, which is a question about the fetcher's shape and not
        about what the numbers inside them mean.
        """
        if self.blank_fn is not None:
            return self.blank_fn()
        out = {"ok": False, "plan": None}
        for spec in self.pools:
            out[spec.key] = None
        return out


# ---------------- detail formatters (Mac Settings + ESP32 rows) ----------------

def _window_detail(payload, key, label):
    plan = payload.get("plan")
    pct = (payload.get(key) or {}).get("pct")
    if plan and pct is not None:
        return f"{plan} · {label} {pct:.0f}%"
    return plan or payload.get("error")


def _detail_vercel(payload):
    team = payload.get("team")
    count = len(payload.get("deployments") or [])
    if team:
        return f"{team} · {count} deploys"
    return payload.get("error")


def _detail_git(payload):
    if not payload.get("ok"):
        return payload.get("error")
    return f"{len(payload.get('commits') or [])} commits"


def _detail_github(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    fails = payload.get("fail_count") or 0
    running = payload.get("running_count") or 0
    bits = []
    if fails:
        bits.append(f"{fails} failed")
    if running:
        bits.append(f"{running} running")
    return " · ".join(bits) if bits else "all clear"


def _detail_claude_status(payload):
    if not payload.get("ok"):
        return payload.get("error") or "unreachable"
    if payload.get("alerting"):
        name = payload.get("incident_name") or payload.get("description")
        return f"major outage · {name}" if name else "major outage"
    indicator = payload.get("indicator") or "none"
    if indicator == "none":
        return "all clear"
    return payload.get("description") or indicator


def _detail_local(payload):
    if not payload.get("ok"):
        return payload.get("error")
    count = len(payload.get("servers") or [])
    return f"{payload.get('host') or 'local'} · {count} servers"


def _detail_supabase(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    count = payload.get("project_count") or 0
    bits = [f"{count} projects"]
    alerts = payload.get("alert_count") or 0
    if alerts:
        bits.append(f"{alerts} alerts")
    errors = payload.get("lint_error_count") or 0
    if errors:
        bits.append(f"{errors} security")
    return " · ".join(bits)


def _detail_plausible(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    if not payload.get("ok"):
        return payload.get("error")
    count = payload.get("site_count") or 0
    live = payload.get("realtime") or 0
    today = payload.get("visitors_today") or 0
    label = payload.get("range_label") or "today"
    bits = [f"{count} site" + ("" if count == 1 else "s")]
    if live:
        bits.append(f"{live} live")
    bits.append(f"{today} {label}")
    return " · ".join(bits)


# ---------------- log summaries (stdout under the LaunchAgent) ----------------

def _summary_codex(payload):
    session = (payload.get("session") or {}).get("pct")
    week = (payload.get("week") or {}).get("pct")
    pace = (payload.get("pace") or {}).get("label") or "-"
    credits = (payload.get("reset_credits") or {}).get("available")
    return (f"plan={payload.get('plan')}  session={session}%  week={week}%  "
            f"pace={pace}  credits={credits}")


def _summary_cursor(payload):
    auto = (payload.get("auto") or {}).get("pct")
    api = (payload.get("api") or {}).get("pct")
    return (f"plan={payload.get('plan')}  auto={auto}%  api={api}%  "
            f"resets={payload.get('resets_in_s')}")


def _summary_vercel(payload):
    return (f"team={payload.get('team')}  "
            f"deploys={len(payload.get('deployments') or [])}")


def _summary_git(payload):
    return f"commits={len(payload.get('commits') or [])}"


def _summary_github(payload):
    return (f"fails={payload.get('fail_count')}  "
            f"running={payload.get('running_count')}  "
            f"repos={len(payload.get('repos') or [])}")


def _summary_claude_status(payload):
    return (f"indicator={payload.get('indicator')}  "
            f"alerting={payload.get('alerting')}")


def _summary_local(payload):
    return (f"host={payload.get('host')}  "
            f"servers={len(payload.get('servers') or [])}")


def _summary_supabase(payload):
    return (f"projects={payload.get('project_count')} "
            f"alerts={payload.get('alert_count')} "
            f"lint_errors={payload.get('lint_error_count')}")


def _summary_plausible(payload):
    return (f"sites={payload.get('site_count')} "
            f"live={payload.get('realtime')} "
            f"today={payload.get('visitors_today')}")


# ---------------- blank payloads (shape before the first fetch) ----------------
# Quota sources derive theirs from `pools`; only the shapes that carry more
# than plan-plus-meters are spelled out.

def _blank_vercel():
    return {"ok": False, "team": None, "deployments": []}


def _blank_git():
    return {"ok": False, "commits": []}


def _blank_github():
    return {"ok": False, "configured": False, "runs": [],
            "fail_count": 0, "running_count": 0, "error": None}


def _blank_claude_status():
    return {
        "ok": False,
        "configured": True,
        "indicator": "none",
        "description": None,
        "alerting": False,
        "incident_name": None,
        "incident_impact": None,
        "url": claude_status.PAGE_URL,
        "updated_at": None,
        "error": None,
    }


def _blank_local():
    return {"ok": False, "host": None, "servers": []}


def _blank_supabase():
    return {"ok": False, "configured": False, "projects": [],
            "project_count": 0, "healthy_count": 0, "alert_count": 0,
            "lint_error_count": 0, "lint_warn_count": 0, "lint_total": 0}


def _blank_plausible():
    return {"ok": False, "configured": False, "sites": [],
            "site_count": 0, "visitors_today": 0, "realtime": 0,
            "range": "24h", "range_label": "24h"}


# Order matters — Mac Settings rows and the ESP32 footer dots follow it, and
# clients render groups in GROUP_IDS order, so keep AI rows first.
# Quota accents match firmware COL_CLAUDE / COL_OPENAI / COL_CURSOR.
_CLAUDE_POOLS = (
    PoolSpec("session", "session", "Session", oauth_usage.SESSION_WINDOW_S),
    PoolSpec("week", "week", "Weekly", oauth_usage.WEEK_WINDOW_S),
)
_CODEX_POOLS = (
    MeterSpec("session", "session", "Session", oauth_usage.SESSION_WINDOW_S),
    MeterSpec("week", "week", "Weekly", oauth_usage.WEEK_WINDOW_S),
    # Banked limit resets. A grant, not a window: you hold a count of them,
    # each expires on its own clock, and none of it is a percentage of
    # anything. Declared after the windows so `_headline_pool` is unaffected
    # even for a row that did not pin `headline` — and `ring=False` because
    # there is no arc to sweep. Already on the wire as the flat
    # `reset_credits_*` keys, which keep shipping for clients that read them.
    MeterSpec("credits", "reset_credits", "Credits",
              kind=KIND_GRANT, ring=False),
    # The workspace spend cap, same shape as Cursor's on-demand and for the
    # same reason. Already on the wire as the flat `cost_*` keys.
    MeterSpec("spend", "spend", "Spend", kind=KIND_OVERAGE, ring=False),
)
_CURSOR_POOLS = (
    MeterSpec("total", "total", "Total"),
    MeterSpec("auto", "auto", "Auto", ring=False),
    MeterSpec("api", "api", "API"),
    # What you pay once the plan above is spent. Dollars against a cap, so
    # `ring=False` — an arc would put it next to the plan meters as if it were
    # more of the same thing, and it is the opposite: the plan running out is
    # what makes this one start moving. Already on the wire as `on_demand_*`.
    MeterSpec("on_demand", "on_demand", "On-demand",
              kind=KIND_OVERAGE, ring=False),
)
_COPILOT_POOLS = (
    PoolSpec("premium", "premium", "Premium", jetbrains_usage.MONTH_WINDOW_S),
    PoolSpec("chat", "chat", "Chat", jetbrains_usage.MONTH_WINDOW_S),
)
_GEMINI_POOLS = (
    PoolSpec("pro", "pro", "Pro", gemini_usage.DAY_WINDOW_S),
    PoolSpec("flash", "flash", "Flash", gemini_usage.DAY_WINDOW_S),
)
_WINDSURF_POOLS = (
    PoolSpec("session", "session", "Daily", windsurf_usage.DAY_WINDOW_S),
    PoolSpec("week", "week", "Weekly", windsurf_usage.WEEK_WINDOW_S),
)
_JETBRAINS_POOLS = (
    PoolSpec("month", "month", "Monthly", jetbrains_usage.MONTH_WINDOW_S),
)
_ZED_POOLS = (
    PoolSpec("predictions", "predictions", "Predictions",
             zed_usage.MONTH_WINDOW_S),
)
# xAI exposes no percent-used figure for the subscription window (only the
# window bounds + on-demand credit cap/used), so the sole meter is on-demand
# credits — dollars against a cap, same shape as Cursor's on-demand.
_GROK_POOLS = (
    PoolSpec("credits", "credits", "Credits", grok_usage.WEEK_WINDOW_S,
             kind=KIND_OVERAGE, ring=False),
)


def _detail_grok(payload):
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    parts = []
    if payload.get("plan"):
        parts.append(str(payload["plan"]))
    credits = payload.get("credits")
    if isinstance(credits, dict) and credits.get("pct") is not None:
        parts.append("credits {:.0f}%".format(credits["pct"]))
    if payload.get("week_resets_in"):
        parts.append("resets {}".format(payload["week_resets_in"]))
    return " · ".join(parts) if parts else None

BASE_SOURCES = (
    Source("claude", "Claude", "~/.headroom/oauth (imports Claude login)", 60,
           oauth_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_CLAUDE_POOLS,
           headline=("week", "session"), accent="#D97757",
           account_kind=accounts.KIND_DIR,
           account_file=oauth_usage.CREDS_NAME,
           account_hint="~/.claude-work (a second CLAUDE_CONFIG_DIR)",
           account_probe=oauth_usage.credentials_present),
    Source("codex", "Codex", "~/.codex/auth.json", 60,
           codex_usage.fetch_quota, summary_fn=_summary_codex,
           kind="quota", group=GROUP_AI, pools=_CODEX_POOLS,
           headline=("week", "session"), accent="#10A37F",
           # Codex resets are announced by OpenAI's Codex lead rather than on a
           # status page, so the permalink is the account itself.
           reset_note_url="https://x.com/thsottiaux",
           account_kind=accounts.KIND_DIR,
           account_file=codex_usage.AUTH_NAME,
           account_hint="~/.codex-work (a second CODEX_HOME)"),
    Source("cursor", "Cursor", "Cursor IDE signed-in JWT", 60,
           cursor_usage.fetch_quota, summary_fn=_summary_cursor,
           kind="quota", group=GROUP_AI, pools=_CURSOR_POOLS,
           headline=("total",), headline_fallback_max=("auto", "api"),
           accent="#789BC8",
           account_kind=accounts.KIND_FILE,
           account_hint="another profile's state.vscdb"),
    Source("copilot", "Copilot", "GitHub token / `gh auth`", 60,
           copilot_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_COPILOT_POOLS,
           headline=("premium", "chat"), accent="#A371F7"),
    Source("gemini", "Gemini", "~/.gemini OAuth (Gemini CLI)", 60,
           gemini_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_GEMINI_POOLS,
           headline=("pro", "flash"), accent="#4285F4",
           account_kind=accounts.KIND_DIR,
           account_file=gemini_usage.CREDS_NAME,
           account_hint="~/.gemini-work"),
    Source("windsurf", "Windsurf", "Windsurf IDE plan cache", 60,
           windsurf_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_WINDSURF_POOLS,
           headline=("week", "session"), accent="#00C2A8",
           account_kind=accounts.KIND_FILE,
           account_hint="another profile's state.vscdb"),
    Source("jetbrains", "JetBrains AI", "Local AI Assistant quota XML", 60,
           jetbrains_usage.fetch_quota,
           kind="quota", group=GROUP_AI,
           pools=_JETBRAINS_POOLS, headline=("month",), accent="#FE315D"),
    Source("zed", "Zed", "Zed Keychain session", 60,
           zed_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_ZED_POOLS,
           headline=("predictions",), accent="#084CCF"),
    Source("grok", "Grok", "~/.grok/auth.json (Grok CLI)", 300,
           grok_usage.fetch_quota, detail_fn=_detail_grok,
           kind="quota", group=GROUP_AI, pools=_GROK_POOLS,
           accent="#8E8E93"),
    # Non-quota rows are appended after quotas in ordered_sources(); keep this
    # with the other activity sources so SOURCE_IDS stays in rollup order.
    Source("claude-status", "Claude Status", "status.claude.com", 60,
           claude_status.fetch, _detail_claude_status, _summary_claude_status,
           _blank_claude_status, kind="activity", group=GROUP_AI),
    Source("vercel", "Vercel", "Vercel CLI login", 60,
           vercel_builds.fetch_deployments, _detail_vercel, _summary_vercel,
           _blank_vercel),
    Source("git", "Git", "Local commits under configured Dev root", 60,
           git_activity.fetch_commits, _detail_git, _summary_git,
           _blank_git),
    Source("github", "GitHub Actions", "Failed / running workflows", 90,
           github_actions.fetch_actions, _detail_github, _summary_github,
           _blank_github),
    Source("local", "Local", "Listening dev servers", local_servers.CACHE_TTL_S,
           local_servers.fetch_servers, _detail_local, _summary_local,
           _blank_local),
    Source("supabase", "Supabase", "PAT in Headroom Keychain", 5 * 60,
           supabase_usage.fetch_projects, _detail_supabase, _summary_supabase,
           _blank_supabase),
    Source("plausible", "Plausible", "Stats/Sites API key in Keychain",
           plausible_usage.CACHE_TTL_S,
           plausible_usage.fetch_stats, _detail_plausible, _summary_plausible,
           _blank_plausible),
)


def _account_row(base, account):
    """A registry row for one extra login of `base`.

    Everything except identity and where the credentials are is inherited, so
    an account row meters, logs, charts and colors exactly like the provider
    it belongs to. `fetch` is bound to the account rather than wrapped, so the
    fetcher keeps its own `force=` contract.

    `title` keeps the brand for text-only surfaces (Settings, menu bar, the
    board). Clients that already draw the brand mark use `label` instead —
    repeating "Claude" next to a Claude glyph is how three identical tabs
    all truncate to "Claude…".
    """
    return base._replace(
        id=account.id,
        title=f"{base.title} · {account.label}",
        hint=account.raw_root,
        fetch=functools.partial(base.fetch, account=account),
        account=account,
    )


def _expand(bases):
    """Registry rows: each base source followed by its extra accounts."""
    rows = []
    for base in bases:
        rows.append(base)
        if not base.account_kind:
            continue
        for account in accounts.for_provider(base.id):
            rows.append(_account_row(base, account))
    return tuple(rows)


SOURCES = _expand(BASE_SOURCES)

BY_ID = {source.id: source for source in SOURCES}
SOURCE_IDS = tuple(source.id for source in SOURCES)
BASE_BY_ID = {source.id: source for source in BASE_SOURCES}

# Quota subset — pollers, daily burn, burndown samples, /usage providers[].
QUOTA_SOURCES = tuple(s for s in SOURCES if s.kind == "quota")
BURN_SOURCE_IDS = tuple(s.id for s in QUOTA_SOURCES)


def add_account(provider, label, root):
    """Register a second login for `provider` and switch it on.

    Enabling here rather than leaving it to the first-run defaults is the
    point: someone who just pointed Headroom at a second Claude folder means
    to see it, and a new id would otherwise default off like any other source
    the registry grew. Raises ValueError for anything worth showing a person.
    """
    base = BASE_BY_ID.get(str(provider))
    if base is None:
        raise ValueError(f"unknown provider {provider!r}")
    if not base.account_kind:
        raise ValueError(f"{base.title} supports only one account")
    account = accounts.add(
        base.id, label, root, base.account_kind)
    set_enabled({account.id: True})
    return account


def remove_account(source_id):
    """Drop an extra login and forget its enabled flag. True when removed."""
    if not accounts.remove(source_id):
        return False
    _forget_enabled(source_id)
    return True


def get(source_id):
    return BY_ID.get(source_id)


def group_of(source_id):
    source = BY_ID.get(source_id)
    return source.group if source else GROUP_DEVTOOLS


def sources_in_group(group):
    return tuple(s for s in SOURCES if s.group == group)


def is_quota(source_id):
    source = BY_ID.get(source_id)
    return bool(source and source.kind == "quota")


def meta_for(source_id):
    source = BY_ID.get(source_id)
    if source is None:
        return {"title": source_id, "hint": ""}
    return {"title": source.title, "hint": source.hint}


def headline_pct(source_id, payload):
    """Stable plan-window % used for daily burn accounting. None if unknown."""
    source = BY_ID.get(source_id)
    payload = payload or {}
    if source is None or source.kind != "quota":
        return None
    for key in source.headline:
        pct = (payload.get(key) or {}).get("pct")
        if pct is not None:
            try:
                return float(pct)
            except (TypeError, ValueError):
                continue
    vals = []
    for key in source.headline_fallback_max:
        pct = (payload.get(key) or {}).get("pct")
        if pct is None:
            continue
        try:
            vals.append(float(pct))
        except (TypeError, ValueError):
            continue
    return max(vals) if vals else None


def pool_rows():
    """Flat (provider, pool_id, key, default_window_s) for quota_samples.

    Windows only. The sample store is a series of percentages against a window
    that refills, and everything downstream of it — the burndown curves, the
    pace lines, the exhaustion forecasts — reads it that way. A meter of any
    other kind has no pct to record and no window to record it against, so it
    is not a row here; it is a row in `providers[]`, which is where the full
    set lives.
    """
    rows = []
    for source in QUOTA_SOURCES:
        for pool in source.windows():
            rows.append((source.id, pool.id, pool.key, pool.default_window_s))
    return tuple(rows)


def detail_for(source_id, payload):
    """Short status line for Mac Settings. Never raises."""
    source = BY_ID.get(source_id)
    payload = payload or {}
    if source is None:
        return payload.get("error")
    try:
        return source.detail(payload)
    except Exception:
        return payload.get("error")


def blank_state():
    """Fresh payload dict for every source, keyed by id."""
    return {source.id: source.blank() for source in SOURCES}


# ---------------- enabled flags (~/.headroom/sources.json) ----------------

_lock = threading.Lock()
_state = None

# How many providers the compact surfaces show: menu-bar tanks, iOS widget,
# ESP32 glance slots. One number, because they must agree about which three.
FOCUS_LIMIT = 3


# A stored accent is `#RRGGBB` and nothing else. The host does not police
# taste — the Mac offers a curated grid (HeadroomPalette.accentChoices) — but
# it does refuse anything that would paint black on every surface at once.
ACCENT_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def _normalize_accent(value):
    """'#d97757' / 'D97757' → '#D97757'. None for anything else."""
    text = str(value or "").strip()
    if not ACCENT_RE.match(text):
        return None
    return "#" + text.lstrip("#").upper()


def normalize_accent(value):
    """Public form of the accent rule, for callers validating before writing."""
    return _normalize_accent(value)


def _clean_accents(raw):
    """Overrides as stored: {source_id: '#RRGGBB'}, junk dropped."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for sid, value in raw.items():
        accent = _normalize_accent(value)
        # Kept even for ids this build doesn't know: a color set for a
        # provider you disabled, or one added by a newer release, should
        # still be there when it comes back rather than quietly reset.
        if accent:
            out[str(sid)] = accent
    return out


def _known_ids():
    """Registry ids, plus accounts added since this process started.

    An account written by POST /accounts is real on disk but not in `SOURCES`
    until the host restarts. Without it here, the enabled flag written next to
    it would be dropped on the way to the file and the new row would come back
    switched off.
    """
    ids = list(SOURCE_IDS)
    for rows in accounts.all_accounts().values():
        for account in rows:
            if account.id not in ids:
                ids.append(account.id)
    return tuple(ids)


def _default_enabled():
    """First-run defaults: only sources that look signed-in locally.

    Extra accounts are not in `detect_sources.PROBES` — nothing can probe for
    a folder it has not been told about — so they are seeded from their own
    credential check. Without this, deleting sources.json would bring the
    machine back with every extra login switched off.
    """
    enabled = detect_sources.suggested_enabled(
        SOURCE_IDS, quota_ids=BURN_SOURCE_IDS)
    for source in SOURCES:
        if source.account is None:
            continue
        if source.account_probe is not None:
            enabled[source.id] = bool(source.account_probe(source.account))
        else:
            enabled[source.id] = accounts.present(
                source.account, source.account_kind, source.account_file)
    return enabled


def _normalize_order(raw):
    """Pinned quota ids, deduped, with unpinned ones appended.

    A provider added to the registry after the user pinned an order lands at
    the end rather than silently jumping to the front — and an id that left
    the registry drops out instead of poisoning the list.
    """
    out = []
    for sid in (raw or []):
        if sid in BY_ID and is_quota(sid) and sid not in out:
            out.append(sid)
    for sid in BURN_SOURCE_IDS:
        if sid in out:
            continue
        # An extra account slots in behind its provider rather than at the
        # very end: "Claude · Work" belongs under Claude, not under Zed.
        base = accounts.split_id(sid)[0]
        if base != sid and base in out:
            at = len(out) - 1
            while at >= 0 and accounts.split_id(out[at])[0] != base:
                at -= 1
            out.insert(at + 1, sid)
            continue
        out.append(sid)
    return out


def _blank_store():
    enabled = _default_enabled()
    return {
        "enabled": enabled,
        "dismissed": _infer_dismissed(enabled, None),
        "order": _normalize_order(None),
        "accents": {},
    }


def _infer_dismissed(enabled, raw):
    """Full dismissed map: stored values, else inferred from enabled.

    `dismissed` says whether a source sits in Settings' Library rather than
    its Active list; disabled-but-not-dismissed is "paused" — configured,
    visible, not polled. Files from before the flag never distinguished the
    two: off *meant* Library. So a missing id inherits `not enabled`, which
    keeps every pre-upgrade Library chip a Library chip instead of promoting
    ten switched-off sources to paused rows on first launch.
    """
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for sid, on in enabled.items():
        if sid in raw:
            out[sid] = bool(raw[sid])
        else:
            out[sid] = not bool(on)
    return out


def _load():
    try:
        with open(STORE_PATH) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        # Seed once so Claude-only machines don't poll empty Codex/Cursor.
        enabled = _default_enabled()
        state = {
            "enabled": enabled,
            "dismissed": _infer_dismissed(enabled, None),
            "order": _normalize_order(None),
            "seeded_from": "detect",
            "detected": detect_sources.detected_map(),
        }
        try:
            _save(state)
        except OSError:
            pass
        return {"enabled": enabled, "dismissed": state["dismissed"],
                "order": state["order"], "accents": {}}
    except (OSError, json.JSONDecodeError):
        return _blank_store()
    if not isinstance(data, dict):
        return _blank_store()
    order = _normalize_order(
        data.get("order") if isinstance(data.get("order"), list) else None)
    accents = _clean_accents(data.get("accents"))
    known = _known_ids()
    enabled = {sid: False for sid in known}
    # Legacy files without an explicit map keep prior all-on behaviour only
    # when the key is missing entirely and the file has other content — but
    # normal files always have "enabled". Missing ids default off so new
    # sources don't surprise existing installs.
    raw = data.get("enabled") if isinstance(data.get("enabled"), dict) else {}
    if not raw and data.get("seeded_from") is None:
        # Pre-detect era file that somehow lacks enabled — treat as all on.
        enabled = {sid: True for sid in known}
        return {"enabled": enabled,
                "dismissed": _infer_dismissed(enabled, data.get("dismissed")),
                "order": order, "accents": accents}
    for sid in known:
        if sid in raw:
            enabled[sid] = bool(raw[sid])
    return {"enabled": enabled,
            "dismissed": _infer_dismissed(enabled, data.get("dismissed")),
            "order": order, "accents": accents}


def _save(state):
    folder = os.path.dirname(STORE_PATH)
    os.makedirs(folder, exist_ok=True)
    raw = json.dumps(state, indent=2, sort_keys=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w") as handle:
        handle.write(raw)
    os.replace(tmp, STORE_PATH)


def _state_locked():
    global _state
    if _state is None:
        _state = _load()
    return _state


def enabled_map():
    with _lock:
        return dict(_state_locked()["enabled"])


def is_enabled(source_id):
    return bool(enabled_map().get(source_id, True))


def set_enabled(updates):
    """Apply {source_id: bool} updates. Returns the full enabled map.

    Turning a source on also un-dismisses it — a source someone just enabled
    is by definition one they track, so it must land in Active, not stay a
    Library chip that happens to poll. Turning one off leaves `dismissed`
    alone: that is the paused state.
    """
    known = set(_known_ids())
    with _lock:
        state = _state_locked()
        enabled = dict(state["enabled"])
        dismissed = dict(state.get("dismissed") or {})
        for sid, value in (updates or {}).items():
            if sid in known:
                enabled[sid] = bool(value)
                if value:
                    dismissed[sid] = False
        state["enabled"] = enabled
        state["dismissed"] = dismissed
        _save(state)
        return dict(enabled)


def dismissed_map():
    with _lock:
        state = _state_locked()
        return dict(state.get("dismissed") or {})


def set_dismissed(updates):
    """Apply {source_id: bool}. Returns the full dismissed map.

    Dismissing also disables — a Library chip must not keep polling — but
    un-dismissing does not enable by itself, so a caller restoring a source
    to Active decides separately whether it comes back on.
    """
    known = set(_known_ids())
    with _lock:
        state = _state_locked()
        enabled = dict(state["enabled"])
        dismissed = dict(state.get("dismissed") or {})
        for sid, value in (updates or {}).items():
            if sid in known:
                dismissed[sid] = bool(value)
                if value:
                    enabled[sid] = False
        state["enabled"] = enabled
        state["dismissed"] = dismissed
        _save(state)
        return dict(dismissed)


def _forget_enabled(source_id):
    """Drop a removed account's flag so re-adding the same slug starts clean."""
    with _lock:
        state = _state_locked()
        enabled = dict(state["enabled"])
        if enabled.pop(source_id, None) is None:
            return
        state["enabled"] = enabled
        dismissed = dict(state.get("dismissed") or {})
        dismissed.pop(source_id, None)
        state["dismissed"] = dismissed
        state["order"] = [sid for sid in state["order"] if sid != source_id]
        accents = dict(state.get("accents") or {})
        accents.pop(source_id, None)
        state["accents"] = accents
        _save(state)


def default_accent(source_id):
    """The registry's own color for a row, ignoring any override."""
    source = BY_ID.get(source_id)
    return source.accent if source else None


def accent_overrides():
    with _lock:
        return dict(_state_locked().get("accents") or {})


def accent_for(source_id):
    """The color every surface should paint this row: override, else registry.

    One resolution, on the host, because the menu bar, the popover rings, the
    phone and its widget each read `accent` off the payload — if they each
    merged an override locally they would drift the moment one of them was a
    poll behind.
    """
    return accent_overrides().get(source_id) or default_accent(source_id)


def set_accents(updates):
    """Apply {source_id: '#RRGGBB' | None}. None (or '') restores the default.

    Returns the stored override map. Raises ValueError on a color that isn't
    six hex digits, so Settings can say so rather than silently keeping the
    old one.
    """
    known = set(_known_ids())
    cleaned = {}
    for sid, value in (updates or {}).items():
        if sid not in known:
            continue
        if value is None or str(value).strip() == "":
            cleaned[sid] = None
            continue
        accent = _normalize_accent(value)
        if accent is None:
            raise ValueError(f"{value!r} is not a #RRGGBB color")
        cleaned[sid] = accent
    with _lock:
        state = _state_locked()
        accents = dict(state.get("accents") or {})
        for sid, accent in cleaned.items():
            if accent is None:
                accents.pop(sid, None)
            else:
                accents[sid] = accent
        state["accents"] = accents
        _save(state)
        return dict(accents)


def order_ids():
    """Quota ids in the user's pinned order (registry order until pinned)."""
    with _lock:
        return list(_state_locked()["order"])


def set_order(ids):
    """Pin the provider order. Returns the normalized full list."""
    with _lock:
        state = _state_locked()
        state["order"] = _normalize_order(ids)
        _save(state)
        return list(state["order"])


def ordered_quota_sources():
    """QUOTA_SOURCES resequenced by the pinned order."""
    return tuple(BY_ID[sid] for sid in order_ids())


def ordered_sources():
    """Registry rows with quota providers resequenced by the pinned order.

    Onboarding and Settings should list providers the way the meters do.
    """
    return ordered_quota_sources() + tuple(
        s for s in SOURCES if s.kind != "quota")


def focus_ids(limit=FOCUS_LIMIT):
    """The first `limit` *enabled* providers in pinned order.

    Menu bar, widget and the ESP32 glance all render this list rather than
    each slicing their own top-N — that is what keeps the three surfaces
    showing the same providers between polls.
    """
    enabled = enabled_map()
    picked = [sid for sid in order_ids() if enabled.get(sid, True)]
    return picked[:limit]


def _detected_map():
    """Local probes, extended with one existence check per extra account.

    The probe answers "is there a credential store here", not "is the token
    good" — a signed-out account still shows up as a row so its real error is
    on screen instead of the row silently vanishing.
    """
    detected = detect_sources.detected_map()
    for source in SOURCES:
        if source.account is None:
            continue
        if source.account_probe is not None:
            detected[source.id] = bool(source.account_probe(source.account))
        else:
            detected[source.id] = accounts.present(
                source.account, source.account_kind, source.account_file)
    return detected


def accounts_payload():
    """Shape for GET/POST /accounts — extra logins, and who can hold them."""
    return {
        "providers": [
            {
                "id": base.id,
                "title": base.title,
                "kind": base.account_kind,
                "hint": base.account_hint,
                "accent": accent_for(base.id),
                "max": accounts.MAX_PER_PROVIDER,
                "accounts": [
                    account.payload()
                    for account in accounts.for_provider(base.id)
                ],
            }
            for base in BASE_SOURCES if base.account_kind
        ],
    }


def detection_payload():
    """Shape for GET /setup — what local probes found + current enables."""
    detected = _detected_map()
    enabled = enabled_map()
    return {
        "detected": detected,
        "enabled": enabled,
        "groups": list(GROUP_IDS),
        "order": order_ids(),
        "focus": focus_ids(),
        "accents": accent_overrides(),
        "sources": [
            {
                "id": source.id,
                "title": source.title,
                "hint": source.hint,
                "kind": source.kind,
                "group": source.group,
                "accent": accent_for(source.id),
                "accent_default": source.accent,
                "detected": bool(detected.get(source.id, False)),
                "enabled": bool(enabled.get(source.id, False)),
                **({"label": source.account.label}
                   if source.account is not None else {}),
            }
            for source in ordered_sources()
        ],
        **accounts_payload(),
    }

def reset_for_tests():
    """Drop cached enabled flags (unit tests only)."""
    global _state
    with _lock:
        _state = None


def reload_registry():
    """Rebuild `SOURCES` from the accounts file (tests only).

    The running host restarts instead: `quota_samples.POOLS` and the poller's
    per-source clocks are derived at import, and quietly swapping the registry
    under them would leave the sample schema disagreeing with the meters.
    """
    global SOURCES, BY_ID, SOURCE_IDS, QUOTA_SOURCES, BURN_SOURCE_IDS
    accounts.reload()
    SOURCES = _expand(BASE_SOURCES)
    BY_ID = {source.id: source for source in SOURCES}
    SOURCE_IDS = tuple(source.id for source in SOURCES)
    QUOTA_SOURCES = tuple(s for s in SOURCES if s.kind == "quota")
    BURN_SOURCE_IDS = tuple(s.id for s in QUOTA_SOURCES)
    reset_for_tests()
