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

import colorsys
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
import ai_gateway_usage
import github_actions
import grok_usage
import jetbrains_usage
import local_servers
import oauth_usage
import openrouter_usage
import plausible_usage
import posthog_usage
import sentry_alerts
import datadog_monitors
import axiom_monitors
import supabase_usage
import vercel_builds
import windsurf_usage
import xcode_builds
import zed_usage

STORE_PATH = os.path.expanduser("~/.headroom/sources.json")

# Onboarding / Settings groups. Order is the order sections render in.
GROUP_AI = "ai"              # coding models you're already signed into
GROUP_DEVTOOLS = "devtools"  # everything else you point Headroom at
GROUP_IDS = (GROUP_AI, GROUP_DEVTOOLS)

# Subscription prices are provider metadata, not quota math. They live in the
# source registry so every client gets the same catalog through providers[];
# `pricing.py` remains reserved for per-model token rates.
SUBSCRIPTION_PRICES_CHECKED = "2026-08-01"


# ---------------------------- meter kinds ----------------------------
# What shape a meter is, which is what decides what its numbers mean and which
# mark draws it. Ships as `providers[].pools.<id>.kind`; see docs/metering.md.
#
# **Not** `Source.kind` below. That one is a different axis entirely — it says
# whether a *source* feeds the quota machinery at all (quota vs activity). The
# two words are unavoidable: one describes a service, one describes a meter.
#
# Only window, grant, overage and balance are implemented. The rest are named
# anyway, because the set *is* the design: a form with no name here is a form
# that arrives as flat keys bolted onto a provider payload, which is the thing
# docs/metering.md exists to stop. Naming one costs a string; discovering you
# needed it costs a schema.
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


class SubscriptionPrice(NamedTuple):
    """One published subscription price for a provider plan.

    `annual_usd` is the total charged for one year, rather than the monthly
    equivalent. That lets clients say "billed annually" without guessing how
    the provider rounds or collects the charge. A missing price means custom,
    regional, or otherwise not safely representable as one USD number.
    """

    id: str
    title: str
    monthly_usd: Optional[float] = None
    annual_usd: Optional[float] = None
    unit: str = "user"
    note: Optional[str] = None


def _subscription_price_payload(price):
    return {
        "id": price.id,
        "title": price.title,
        "monthly_usd": price.monthly_usd,
        "annual_usd": price.annual_usd,
        "unit": price.unit,
        "note": price.note,
    }


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
    # Published plan prices. These are informational and never affect quota
    # percentages, spend estimates, or billing calculations.
    subscription_prices: tuple = ()
    subscription_pricing_url: Optional[str] = None
    subscription_prices_checked: Optional[str] = None
    # What Attention / meter errors say after "needs sign-in — …". A CLI
    # command in backticks when one exists; plain prose for IDE-only tools.
    # Installed-but-not-authed is the common case this exists for.
    login_hint: Optional[str] = None

    def subscription_pricing_payload(self):
        """Provider-owned plan catalog for the additive /usage payload."""
        if not self.subscription_prices and not self.subscription_pricing_url:
            return None
        return {
            "currency": "USD",
            "checked": (
                self.subscription_prices_checked
                or SUBSCRIPTION_PRICES_CHECKED
            ),
            "url": self.subscription_pricing_url,
            "plans": [
                _subscription_price_payload(price)
                for price in self.subscription_prices
            ],
        }

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
    if not payload.get("ok"):
        return payload.get("error") or "not reporting"
    fails = payload.get("fail_count") or 0
    running = payload.get("running_count") or 0
    inbox = payload.get("inbox_count") or 0
    bits = []
    if fails:
        bits.append(f"{fails} failed")
    if running:
        bits.append(f"{running} running")
    if inbox:
        bits.append(f"{inbox} inbox")
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


def _fetch_local(force=False):
    """Listening servers plus active Xcode / Swift builds on this Mac."""
    servers = local_servers.fetch_servers(force=force)
    builds = xcode_builds.fetch_builds(force=force)
    server_ok = bool(servers.get("ok"))
    build_ok = bool(builds.get("ok"))
    return {
        "ok": server_ok or build_ok,
        "host": servers.get("host") or builds.get("host"),
        "error": (None if (server_ok or build_ok)
                  else (servers.get("error") or builds.get("error"))),
        "stale": bool(servers.get("stale") or builds.get("stale")),
        "servers": servers.get("servers") or [],
        "builds": builds.get("builds") or [],
    }


def _detail_local(payload):
    if not payload.get("ok"):
        return payload.get("error")
    servers = len(payload.get("servers") or [])
    builds = len(payload.get("builds") or [])
    bits = [f"{servers} servers"]
    if builds:
        bits.append(f"{builds} builds")
    return f"{payload.get('host') or 'local'} · " + " · ".join(bits)


def _detail_supabase(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    # Same order as Plausible: a rejected PAT used to read as "0 projects",
    # which looked like an empty portfolio rather than a bad key.
    if not payload.get("ok"):
        return payload.get("error") or "not reporting"
    count = payload.get("project_count") or 0
    bits = [f"{count} projects"]
    alerts = payload.get("alert_count") or 0
    if alerts:
        bits.append(f"{alerts} alerts")
    errors = payload.get("lint_error_count") or 0
    if errors:
        bits.append(f"{errors} security")
    return " · ".join(bits)


def _detail_balance(payload):
    """Settings / footer line for a prepaid balance source."""
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    if not payload.get("ok"):
        return payload.get("error") or "unavailable"
    rem = (payload.get("balance") or {}).get("remaining_usd")
    if rem is None:
        return payload.get("error") or "no balance"
    bits = [f"${rem:,.2f} left"]
    spend = payload.get("spend") or {}
    runway = spend.get("runway_days")
    if isinstance(runway, (int, float)) and runway > 0:
        if runway >= 10:
            bits.append(f"~{runway:.0f}d runway")
        else:
            bits.append(f"~{runway:.1f}d runway")
    elif spend.get("today_usd") is not None:
        bits.append(f"${spend['today_usd']:,.2f} today")
    return " · ".join(bits)


def _summary_balance(payload):
    rem = (payload.get("balance") or {}).get("remaining_usd")
    used = (payload.get("balance") or {}).get("used_usd")
    bits = [f"remaining={rem}"]
    if used is not None:
        bits.append(f"used={used}")
    spend = payload.get("spend") or {}
    if spend.get("today_usd") is not None:
        bits.append(f"today={spend['today_usd']}")
    if spend.get("runway_days") is not None:
        bits.append(f"runway_d={spend['runway_days']}")
    return "  ".join(bits)


def _blank_balance():
    return {
        "ok": False,
        "configured": False,
        "error": None,
        "plan": None,
        "balance": None,
        "spend": None,
    }


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


def _detail_posthog(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    if not payload.get("ok"):
        return payload.get("error")
    count = payload.get("project_count") or 0
    live = payload.get("realtime") or 0
    events = payload.get("events_today") or 0
    label = payload.get("range_label") or "today"
    bits = [f"{count} project" + ("" if count == 1 else "s")]
    if live:
        bits.append(f"{live} live")
    bits.append(f"{events} events {label}")
    return " · ".join(bits)


def _detail_sentry(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    if not payload.get("ok"):
        return payload.get("error") or "not reporting"
    alerts = payload.get("alert_count") or 0
    org = payload.get("org")
    if alerts:
        bit = f"{alerts} fresh" + ("" if alerts == 1 else " issues")
        return f"{org} · {bit}" if org else bit
    return f"{org} · all clear" if org else "all clear"


def _detail_datadog(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    if not payload.get("ok"):
        return payload.get("error") or "not reporting"
    alerts = payload.get("alert_count") or 0
    warns = payload.get("warn_count") or 0
    bits = []
    if alerts:
        bits.append(f"{alerts} alert" + ("" if alerts == 1 else "s"))
    if warns:
        bits.append(f"{warns} warn" + ("" if warns == 1 else "s"))
    return " · ".join(bits) if bits else "all clear"


def _detail_axiom(payload):
    if not payload.get("configured"):
        return payload.get("error") or "not connected"
    if not payload.get("ok"):
        return payload.get("error") or "not reporting"
    alerts = payload.get("alert_count") or 0
    if alerts:
        return f"{alerts} open" + ("" if alerts == 1 else " alerts")
    return "all clear"


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
            f"inbox={payload.get('inbox_count')}  "
            f"repos={len(payload.get('repos') or [])}")


def _summary_claude_status(payload):
    return (f"indicator={payload.get('indicator')}  "
            f"alerting={payload.get('alerting')}")


def _summary_local(payload):
    return (f"host={payload.get('host')}  "
            f"servers={len(payload.get('servers') or [])}  "
            f"builds={len(payload.get('builds') or [])}")


def _summary_supabase(payload):
    return (f"projects={payload.get('project_count')} "
            f"alerts={payload.get('alert_count')} "
            f"lint_errors={payload.get('lint_error_count')}")


def _summary_plausible(payload):
    return (f"sites={payload.get('site_count')} "
            f"live={payload.get('realtime')} "
            f"today={payload.get('visitors_today')}")


def _summary_posthog(payload):
    return (f"projects={payload.get('project_count')} "
            f"live={payload.get('realtime')} "
            f"events={payload.get('events_today')}")


def _summary_sentry(payload):
    return (f"org={payload.get('org')}  "
            f"alerts={payload.get('alert_count')}  "
            f"issues={len(payload.get('issues') or [])}")


def _summary_datadog(payload):
    return (f"alerts={payload.get('alert_count')}  "
            f"warns={payload.get('warn_count')}  "
            f"site={payload.get('site')}")


def _summary_axiom(payload):
    return (f"alerts={payload.get('alert_count')}  "
            f"host={payload.get('host')}")


# ---------------- blank payloads (shape before the first fetch) ----------------
# Quota sources derive theirs from `pools`; only the shapes that carry more
# than plan-plus-meters are spelled out.

def _blank_vercel():
    return {"ok": False, "team": None, "deployments": []}


def _blank_git():
    return {"ok": False, "commits": []}


def _blank_github():
    return {"ok": False, "configured": False, "runs": [],
            "fail_count": 0, "running_count": 0,
            "inbox": [], "inbox_count": 0, "error": None}


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
    return {"ok": False, "host": None, "servers": [], "builds": []}


def _blank_supabase():
    return {"ok": False, "configured": False, "projects": [],
            "project_count": 0, "healthy_count": 0, "alert_count": 0,
            "lint_error_count": 0, "lint_warn_count": 0, "lint_total": 0}


def _blank_plausible():
    return {"ok": False, "configured": False, "sites": [],
            "site_count": 0, "visitors_today": 0, "realtime": 0,
            "range": "24h", "range_label": "24h"}


def _blank_posthog():
    return {"ok": False, "configured": False, "projects": [],
            "project_count": 0, "events_today": 0, "users_today": 0,
            "realtime": 0, "range": "24h", "range_label": "24h"}


def _blank_sentry():
    return {"ok": False, "configured": False, "issues": [],
            "alert_count": 0, "org": None, "error": None}


def _blank_datadog():
    return {"ok": False, "configured": False, "monitors": [],
            "alert_count": 0, "warn_count": 0, "site": None, "error": None}


def _blank_axiom():
    return {"ok": False, "configured": False, "alerts": [],
            "alert_count": 0, "host": None, "org_id": None, "error": None}


# Order matters — Mac Settings rows and the ESP32 footer dots follow it, and
# clients render groups in GROUP_IDS order, so keep AI rows first.
# Quota accents match firmware COL_CLAUDE / COL_OPENAI / COL_CURSOR.
# Week before session: the longer window is the outer ring, the shorter the
# inner one. Same order for every provider that ships this split — Claude,
# Codex, Windsurf — so the glyph reads the same way across them.
_CLAUDE_POOLS = (
    PoolSpec("week", "week", "Weekly", oauth_usage.WEEK_WINDOW_S),
    PoolSpec("session", "session", "Session", oauth_usage.SESSION_WINDOW_S),
)
_CODEX_POOLS = (
    MeterSpec("week", "week", "Weekly", oauth_usage.WEEK_WINDOW_S),
    MeterSpec("session", "session", "Session", oauth_usage.SESSION_WINDOW_S),
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
    PoolSpec("week", "week", "Weekly", windsurf_usage.WEEK_WINDOW_S),
    PoolSpec("session", "session", "Daily", windsurf_usage.DAY_WINDOW_S),
)
_JETBRAINS_POOLS = (
    PoolSpec("month", "month", "Monthly", jetbrains_usage.MONTH_WINDOW_S),
)
_ZED_POOLS = (
    PoolSpec("predictions", "predictions", "Predictions",
             zed_usage.MONTH_WINDOW_S),
)
_OPENROUTER_POOLS = (
    # Prepaid credits. No window, so no ring — the mark is a depletion bar.
    MeterSpec("balance", "balance", "Balance",
              kind=KIND_BALANCE, ring=False),
)
_AI_GATEWAY_POOLS = (
    MeterSpec("balance", "balance", "Balance",
              kind=KIND_BALANCE, ring=False),
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

# Prices are USD list prices and intentionally do not try to infer what an
# account pays after tax, regional conversion, nonprofit discounts, or an
# employer arrangement. `None` is used for plans whose provider only quotes
# custom pricing.
_CLAUDE_SUBSCRIPTION_PRICES = (
    SubscriptionPrice("free", "Free", 0, 0, note="No subscription"),
    SubscriptionPrice("pro", "Pro", 20, 200),
    SubscriptionPrice("max-5x", "Max 5x", 100),
    SubscriptionPrice("max-20x", "Max 20x", 200),
    SubscriptionPrice("team-standard", "Team standard", 25, 240),
    SubscriptionPrice("team-premium", "Team premium", 125, 1200),
    SubscriptionPrice("enterprise", "Enterprise", 20, note="Plus usage"),
)
_CODEX_SUBSCRIPTION_PRICES = (
    SubscriptionPrice("free", "Free", 0, 0, note="No subscription"),
    SubscriptionPrice("plus", "Plus", 20),
    # Codex's usage endpoint calls these multiplier plans, while the billing
    # product calls them ChatGPT Plus/Pro/Business. Keep the reported labels
    # here so the current plan resolves without a fuzzy match.
    SubscriptionPrice("pro-5x", "Pro 5x", note="See provider"),
    SubscriptionPrice("pro-20x", "Pro 20x", 200),
    SubscriptionPrice("pro", "Pro", 200),
    SubscriptionPrice("team", "Team", 25, 240),
    SubscriptionPrice("business", "Business", 25, 240),
    SubscriptionPrice("enterprise", "Enterprise", note="Custom"),
)
_CURSOR_SUBSCRIPTION_PRICES = (
    SubscriptionPrice("hobby", "Hobby", 0, 0, note="No subscription"),
    SubscriptionPrice("pro", "Pro", 20),
    SubscriptionPrice("pro-plus", "Pro+", note="See provider"),
    SubscriptionPrice("ultra", "Ultra", note="See provider"),
    SubscriptionPrice("teams", "Teams", 40),
    SubscriptionPrice("enterprise", "Enterprise", note="Custom"),
)
_COPILOT_SUBSCRIPTION_PRICES = (
    SubscriptionPrice("free", "Free", 0, 0, note="No subscription"),
    SubscriptionPrice("pro", "Pro", 10),
    SubscriptionPrice("pro-plus", "Pro+", 39),
    SubscriptionPrice("max", "Max", 100),
    SubscriptionPrice("business", "Business", 19),
    SubscriptionPrice("enterprise", "Enterprise", note="Custom"),
)
_GEMINI_SUBSCRIPTION_PRICES = (
    SubscriptionPrice("free", "Free", 0, 0, note="No subscription"),
    SubscriptionPrice("google-ai-pro", "Google AI Pro", 19.99),
    SubscriptionPrice("google-ai-ultra", "Google AI Ultra", 249.99),
)
_ZED_SUBSCRIPTION_PRICES = (
    SubscriptionPrice("free", "Personal", 0, 0, note="No subscription"),
    SubscriptionPrice("pro", "Pro", 10),
    SubscriptionPrice("business", "Business", 30),
)

BASE_SOURCES = (
    Source("claude", "Claude", "Claude Code login — Keychain or ~/.claude", 120,
           oauth_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_CLAUDE_POOLS,
           headline=("week", "session"), accent="#D97757",
           account_kind=accounts.KIND_DIR,
           account_file=oauth_usage.CREDS_NAME,
           account_hint="~/.claude-work (a second CLAUDE_CONFIG_DIR)",
           account_probe=oauth_usage.credentials_present,
           subscription_prices=_CLAUDE_SUBSCRIPTION_PRICES,
           subscription_pricing_url="https://www.anthropic.com/pricing",
           login_hint="run `claude /login`"),
    Source("codex", "Codex", "~/.codex/auth.json", 60,
           codex_usage.fetch_quota, summary_fn=_summary_codex,
           kind="quota", group=GROUP_AI, pools=_CODEX_POOLS,
           headline=("week", "session"), accent="#10A37F",
           # Codex global resets are announced by OpenAI's Codex lead; the
           # tracker is the checkable record Headroom merges into the heatmap.
           reset_note_url="https://codex-resets.com",
           account_kind=accounts.KIND_DIR,
           account_file=codex_usage.AUTH_NAME,
           account_hint="~/.codex-work (a second CODEX_HOME)",
           subscription_prices=_CODEX_SUBSCRIPTION_PRICES,
           subscription_pricing_url="https://chatgpt.com/pricing",
           login_hint="run `codex login`"),
    Source("cursor", "Cursor", "Cursor IDE signed-in JWT", 60,
           cursor_usage.fetch_quota, summary_fn=_summary_cursor,
           kind="quota", group=GROUP_AI, pools=_CURSOR_POOLS,
           headline=("total",), headline_fallback_max=("auto", "api"),
           accent="#789BC8",
           account_kind=accounts.KIND_FILE,
           account_hint="another profile's state.vscdb",
           subscription_prices=_CURSOR_SUBSCRIPTION_PRICES,
           subscription_pricing_url="https://cursor.com/pricing",
           login_hint="sign in to Cursor"),
    Source("copilot", "Copilot", "Copilot sign-in on this Mac", 60,
           copilot_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_COPILOT_POOLS,
           headline=("premium", "chat"), accent="#A371F7",
           subscription_prices=_COPILOT_SUBSCRIPTION_PRICES,
           subscription_pricing_url="https://github.com/features/copilot/plans",
           login_hint="run `gh auth login`"),
    Source("gemini", "Gemini", "~/.gemini OAuth (Gemini CLI)", 60,
           gemini_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_GEMINI_POOLS,
           headline=("pro", "flash"), accent="#4285F4",
           account_kind=accounts.KIND_DIR,
           account_file=gemini_usage.CREDS_NAME,
           account_hint="~/.gemini-work",
           subscription_prices=_GEMINI_SUBSCRIPTION_PRICES,
           subscription_pricing_url="https://gemini.google.com/advanced",
           login_hint="run `gemini` and sign in"),
    Source("windsurf", "Windsurf", "Windsurf IDE plan cache", 60,
           windsurf_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_WINDSURF_POOLS,
           headline=("week", "session"), accent="#00C2A8",
           subscription_pricing_url="https://windsurf.com/pricing",
           account_kind=accounts.KIND_FILE,
           account_hint="another profile's state.vscdb",
           login_hint="sign in to Windsurf"),
    Source("jetbrains", "JetBrains AI", "Local AI Assistant quota XML", 60,
           jetbrains_usage.fetch_quota,
           kind="quota", group=GROUP_AI,
           pools=_JETBRAINS_POOLS, headline=("month",), accent="#FE315D",
           subscription_pricing_url="https://www.jetbrains.com/ai/",
           login_hint="sign in to JetBrains AI"),
    Source("zed", "Zed", "Zed Keychain session", 60,
           zed_usage.fetch_quota,
           kind="quota", group=GROUP_AI, pools=_ZED_POOLS,
           headline=("predictions",), accent="#084CCF",
           subscription_prices=_ZED_SUBSCRIPTION_PRICES,
           subscription_pricing_url="https://zed.dev/pricing",
           login_hint="sign in to Zed"),
    Source("grok", "Grok", "~/.grok/auth.json (Grok CLI)", 300,
           grok_usage.fetch_quota, detail_fn=_detail_grok,
           kind="quota", group=GROUP_AI, pools=_GROK_POOLS,
           accent="#8E8E93",
           login_hint="run `grok login`"),
    Source("openrouter", "OpenRouter", "Management API key in Keychain",
           openrouter_usage.CACHE_TTL_S,
           openrouter_usage.fetch_quota,
           _detail_balance, _summary_balance, _blank_balance,
           kind="quota", group=GROUP_AI, pools=_OPENROUTER_POOLS,
           accent="#8B5CF6"),
    Source("ai-gateway", "AI Gateway", "Vercel AI Gateway key in Keychain",
           ai_gateway_usage.CACHE_TTL_S,
           ai_gateway_usage.fetch_quota,
           _detail_balance, _summary_balance, _blank_balance,
           kind="quota", group=GROUP_AI, pools=_AI_GATEWAY_POOLS,
           accent="#111111"),
    # Non-quota rows are appended after quotas in ordered_sources(); keep this
    # with the other activity sources so SOURCE_IDS stays in rollup order.
    Source("claude-status", "Claude Status", "status.claude.com — incidents, not your quota", 60,
           claude_status.fetch, _detail_claude_status, _summary_claude_status,
           _blank_claude_status, kind="activity", group=GROUP_AI),
    Source("vercel", "Vercel", "Vercel CLI login", 60,
           vercel_builds.fetch_deployments, _detail_vercel, _summary_vercel,
           _blank_vercel, login_hint="run `vercel login`"),
    Source("git", "Git", "Local commits under configured Dev root", 60,
           git_activity.fetch_commits, _detail_git, _summary_git,
           _blank_git),
    Source("github", "GitHub Actions", "Failed / running workflows", 90,
           github_actions.fetch_actions, _detail_github, _summary_github,
           _blank_github, login_hint="run `gh auth login`"),
    Source("local", "Local", "Listening servers and Xcode builds",
           min(local_servers.CACHE_TTL_S, xcode_builds.CACHE_TTL_S),
           _fetch_local, _detail_local, _summary_local, _blank_local),
    Source("supabase", "Supabase", "PAT in Headroom Keychain", 5 * 60,
           supabase_usage.fetch_projects, _detail_supabase, _summary_supabase,
           _blank_supabase),
    Source("plausible", "Plausible", "Stats API key (stats:read; sites:read to list)",
           plausible_usage.CACHE_TTL_S,
           plausible_usage.fetch_stats, _detail_plausible, _summary_plausible,
           _blank_plausible),
    Source("posthog", "PostHog", "Personal API key (project:read, query:read)",
           posthog_usage.CACHE_TTL_S,
           posthog_usage.fetch_stats, _detail_posthog, _summary_posthog,
           _blank_posthog),
    Source("sentry", "Sentry", "Auth token (event:read)",
           sentry_alerts.CACHE_TTL_S,
           sentry_alerts.fetch_issues, _detail_sentry, _summary_sentry,
           _blank_sentry),
    Source("datadog", "Datadog", "API + App key (monitors_read)",
           datadog_monitors.CACHE_TTL_S,
           datadog_monitors.fetch_monitors, _detail_datadog, _summary_datadog,
           _blank_datadog),
    Source("axiom", "Axiom", "API token (monitors|read)",
           axiom_monitors.CACHE_TTL_S,
           axiom_monitors.fetch_alerts, _detail_axiom, _summary_axiom,
           _blank_axiom),
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

# Integrations catalog: everything you watch on Activity (and connect).
# Distinct from quota `order` (Sources / rings). `servers`/`builds` are
# Activity blocks under the `local` source; enable maps through SOURCE_FOR_WATCH.
INTEGRATION_CATALOG_IDS = (
    "git",
    "github",
    "vercel",
    "openrouter",
    "ai-gateway",
    "supabase",
    "plausible",
    "posthog",
    "sentry",
    "datadog",
    "axiom",
    "servers",
    "builds",
)

# Catalog ids that paint an Activity block. Prepaid balances (OpenRouter,
# AI Gateway) paint account-use panels here — not Usage rings. Claude Code /
# Codex are Coding agents, not Integrations catalog rows.
ACTIVITY_BLOCK_IDS = (
    "git",
    "github",
    "vercel",
    "openrouter",
    "ai-gateway",
    "supabase",
    "plausible",
    "posthog",
    "sentry",
    "datadog",
    "axiom",
    "servers",
    "builds",
)

# Watch catalog id → sources.enabled key.
SOURCE_FOR_WATCH = {
    "servers": "local",
    "builds": "local",
}


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
    return {"title": title_for(source_id), "hint": source.hint}


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


# Provider display names are registry defaults until Settings renames one.
TITLE_MAX_LEN = 40
_PROVIDER_IDS = frozenset(source.id for source in BASE_SOURCES)


def _normalize_title(value):
    """Trimmed display name, or None when empty / too long."""
    text = str(value or "").strip()
    if not text or len(text) > TITLE_MAX_LEN:
        return None
    return text


def normalize_title(value):
    """Public form of the title rule, for callers validating before writing."""
    return _normalize_title(value)


def _clean_titles(raw):
    """Overrides as stored: {provider_id: name}, junk dropped."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for sid, value in raw.items():
        if sid not in _PROVIDER_IDS:
            continue
        title = _normalize_title(value)
        if title:
            out[str(sid)] = title
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


def _normalize_integrations_order(raw):
    """Pinned Integrations catalog ids, deduped, with unknowns dropped.

    New catalog entries after a pin land at the end. Ids that left the set
    drop out rather than poisoning the list.
    """
    known = set(INTEGRATION_CATALOG_IDS)
    out = []
    for sid in (raw or []):
        if sid in known and sid not in out:
            out.append(sid)
    for sid in INTEGRATION_CATALOG_IDS:
        if sid not in out:
            out.append(sid)
    return out


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
        "integrations_order": _normalize_integrations_order(None),
        "accents": {},
        "titles": {},
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
            "integrations_order": _normalize_integrations_order(None),
            "seeded_from": "detect",
            "detected": detect_sources.detected_map(),
        }
        try:
            _save(state)
        except OSError:
            pass
        return {"enabled": enabled, "dismissed": state["dismissed"],
                "order": state["order"],
                "integrations_order": state["integrations_order"],
                "accents": {}, "titles": {}}
    except (OSError, json.JSONDecodeError):
        return _blank_store()
    if not isinstance(data, dict):
        return _blank_store()
    order = _normalize_order(
        data.get("order") if isinstance(data.get("order"), list) else None)
    # Prefer integrations_order; fall back to the short-lived services_order
    # pin so a Mac that only ever set Activity panels keeps that prefix.
    if isinstance(data.get("integrations_order"), list):
        integrations_seed = data.get("integrations_order")
    elif isinstance(data.get("services_order"), list):
        integrations_seed = data.get("services_order")
    else:
        integrations_seed = None
    integrations_order = _normalize_integrations_order(integrations_seed)
    accents = _clean_accents(data.get("accents"))
    titles = _clean_titles(data.get("titles"))
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
                "order": order, "integrations_order": integrations_order,
                "accents": accents, "titles": titles}
    for sid in known:
        if sid in raw:
            enabled[sid] = bool(raw[sid])
    return {"enabled": enabled,
            "dismissed": _infer_dismissed(enabled, data.get("dismissed")),
            "order": order, "integrations_order": integrations_order,
            "accents": accents, "titles": titles}


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


def title_overrides():
    with _lock:
        return dict(_state_locked().get("titles") or {})


def default_title(source_id):
    """The registry's own name for a row, ignoring any override."""
    provider, slug = accounts.split_id(source_id)
    base = BASE_BY_ID.get(provider)
    if base is None:
        source = BY_ID.get(source_id)
        return source.title if source else source_id
    return base.title


def title_for(source_id):
    """The display name every surface should print for this row."""
    provider, slug = accounts.split_id(source_id)
    brand = title_overrides().get(provider) or default_title(provider)
    if slug is None:
        return brand
    source = BY_ID.get(source_id)
    if source is not None and source.account is not None:
        return f"{brand} · {source.account.label}"
    return brand


def login_remedy(source_id):
    """Phrase after "needs sign-in — …" for Attention and meter errors.

    Extra-account ids inherit the base provider's hint. Key/PAT sources with
    no CLI leave the generic fallback — Settings already names where to paste.
    """
    provider, _slug = accounts.split_id(source_id or "")
    base = BASE_BY_ID.get(provider) or BY_ID.get(source_id)
    hint = (base.login_hint if base is not None else None) or ""
    hint = hint.strip()
    return hint or "log in with the tool again"


def _purge_duplicate_account_accents(accents, provider_id, previous_base, new_base):
    """Drop account overrides that mirror a provider color.

    Older Settings builds copied the provider swatch onto every account row.
    When the provider color moves, those stale keys would otherwise win over
    the derived shades.
    """
    prefix = provider_id + ":"
    for sid in list(accents.keys()):
        if not sid.startswith(prefix):
            continue
        value = accents[sid]
        if value == previous_base or value == new_base:
            accents.pop(sid, None)


def _account_accent(source_id, base_hex):
    """Return a stable same-hue shade for an extra account.

    The bare provider row keeps the registry/override color. Extra accounts
    use lightness offsets in their registry order, so the colors remain
    recognizable as one service while still identifying each account. The
    result is derived from the base color rather than stored, which means a
    provider accent override recolors all of its account shades together.
    """
    provider, slug = accounts.split_id(source_id)
    if slug is None or not _normalize_accent(base_hex):
        return base_hex

    siblings = [
        source.id for source in SOURCES
        if accounts.split_id(source.id)[0] == provider
    ]
    try:
        index = siblings.index(source_id)
    except ValueError:
        return base_hex

    # The default row is index zero. Alternating lighter/darker stops keeps
    # the shades legible on both light and dark surfaces without changing the
    # provider hue. There are at most eight extra accounts today.
    offsets = (0.0, 0.14, -0.14, 0.24, -0.24, 0.32, -0.32, 0.38, -0.38)
    offset = offsets[min(index, len(offsets) - 1)]
    value = int(base_hex.lstrip("#"), 16)
    red = ((value >> 16) & 0xFF) / 255.0
    green = ((value >> 8) & 0xFF) / 255.0
    blue = (value & 0xFF) / 255.0
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    lightness = min(0.80, max(0.20, lightness + offset))
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#{:02X}{:02X}{:02X}".format(
        round(red * 255), round(green * 255), round(blue * 255))


def derived_accent_for(source_id):
    """The shade a row would paint with no explicit account override."""
    provider, slug = accounts.split_id(source_id)
    if slug is None:
        overrides = accent_overrides()
        return overrides.get(provider) or default_accent(provider)
    base = accent_overrides().get(provider) or default_accent(provider)
    return _account_accent(source_id, base)


def accent_for(source_id):
    """The color every surface should paint this row.

    One resolution, on the host, because the menu bar, the popover rings, the
    phone and its widget each read `accent` off the payload — if they each
    merged an override locally they would drift the moment one of them was a
    poll behind. Extra accounts get a derived shade unless they have an
    explicit per-account override.
    """
    overrides = accent_overrides()
    explicit = overrides.get(source_id)
    is_account = accounts.is_account_id(source_id)
    provider, _ = accounts.split_id(source_id)
    base = overrides.get(provider) or default_accent(provider)
    # Older Settings builds wrote the same service override to every account
    # row. Treat that legacy duplicate as "no account override" so upgrading
    # immediately restores distinct shades instead of preserving the bug.
    if explicit and (not is_account or explicit != base):
        return explicit
    default = default_accent(source_id)
    if not is_account:
        return default
    return _account_accent(source_id, base)


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
            provider, slug = accounts.split_id(sid)
            if slug is None:
                previous_base = accents.get(sid) or default_accent(sid)
                new_base = accent if accent is not None else default_accent(sid)
                _purge_duplicate_account_accents(
                    accents, sid, previous_base, new_base)
            if accent is None:
                accents.pop(sid, None)
            else:
                accents[sid] = accent
        state["accents"] = accents
        _save(state)
        return dict(accents)


def set_titles(updates):
    """Apply {provider_id: name | None}. None (or '') restores the default."""
    cleaned = {}
    for sid, value in (updates or {}).items():
        if sid not in _PROVIDER_IDS:
            continue
        if value is None or str(value).strip() == "":
            cleaned[sid] = None
            continue
        title = _normalize_title(value)
        if title is None:
            raise ValueError(
                f"{value!r} is not a provider name (1–{TITLE_MAX_LEN} chars)")
        cleaned[sid] = title
    with _lock:
        state = _state_locked()
        titles = dict(state.get("titles") or {})
        for sid, title in cleaned.items():
            if title is None:
                titles.pop(sid, None)
            else:
                titles[sid] = title
        state["titles"] = titles
        _save(state)
        return dict(titles)


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


def services_order_ids():
    """Activity block ids in pin order (subset of integrations_order)."""
    return [sid for sid in integrations_order_ids() if sid in ACTIVITY_BLOCK_IDS]


def set_services_order(ids):
    """Legacy write: merge Activity pins into the full integrations catalog."""
    # Keep relative order of the rest of the catalog; put the given Activity
    # ids first in the order supplied.
    current = integrations_order_ids()
    wanted = _normalize_integrations_order(ids)
    activity = [sid for sid in wanted if sid in ACTIVITY_BLOCK_IDS]
    rest = [sid for sid in current if sid not in ACTIVITY_BLOCK_IDS]
    return set_integrations_order(activity + rest)


def integrations_order_ids():
    """Integrations catalog ids in the user's pinned order."""
    with _lock:
        state = _state_locked()
        if "integrations_order" not in state:
            legacy = state.get("services_order")
            state["integrations_order"] = _normalize_integrations_order(
                legacy if isinstance(legacy, list) else None)
            state.pop("services_order", None)
        return list(state["integrations_order"])


def set_integrations_order(ids):
    """Pin the Integrations catalog order. Returns the normalized full list."""
    with _lock:
        state = _state_locked()
        state["integrations_order"] = _normalize_integrations_order(ids)
        state.pop("services_order", None)
        _save(state)
        return list(state["integrations_order"])


def source_id_for_watch(watch_id):
    """sources.enabled key for a catalog watch id."""
    return SOURCE_FOR_WATCH.get(watch_id, watch_id)


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
        "integrations_order": integrations_order_ids(),
        "focus": focus_ids(),
        "accents": accent_overrides(),
        "titles": title_overrides(),
        "sources": [
            {
                "id": source.id,
                "title": title_for(source.id),
                "title_default": default_title(source.id),
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
