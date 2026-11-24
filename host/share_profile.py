"""The share profile: your stack, this week's burndowns, and your colours, as a link.

A profile is what you show the public, where a stats card is what you hand a
friend. It carries:

  * **subs**: each source that is on and reading, with its plan, the colour
    you picked in Settings, and the plan's list price when the registry knows
    it. The price is the published list price, so the page labels it an
    estimate.
  * **curves**: for each sub, the remaining percentage of its weekly pool,
    sampled every six hours since the window opened, in whole percent.
  * **habits**: the data of your stats card, unchanged, when the study has one.

Two decisions that are not obvious:

  * **The link is the profile.** `encode` packs the profile into the URL
    fragment of a static page (`docs/p/index.html`). A browser never sends a
    fragment to the server, so the page host sees a request for one HTML file
    and nothing about you. There is no upload and no account. A link is a
    snapshot: to update it, share it again.
  * **Same rules as a card.** A profile holds percentages, plan names and
    colours. No token counts, no paths, no repository names
    (docs/product.md, "Friends, cards and the usage study"). `decode` is
    strict for the same reason `study_card.decode` is: a profile is text that
    anyone can edit.

Stdlib only.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import datetime, timezone

import study_card

VERSION = 1
PREFIX = "hrp%d." % VERSION
PAGE_URL = "https://updates.centaur-labs.io/p/"
MAX_ENCODED = 16384
MAX_SUBS = 16
MAX_POINTS = 64
STEP_H = 6
TEXT_MAX = 40
WEEK_S = 7 * 86400

_PROFILE_KEYS = {"v", "handle", "week", "made", "subs", "habits"}
_SUB_KEYS = {"id", "title", "plan", "accent", "usd", "curve"}
_CURVE_KEYS = {"pool", "window_h", "step_h", "elapsed_h", "dow", "hour", "rem"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_ACCENT = re.compile(r"^#[0-9A-F]{6}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProfileError(ValueError):
    """A profile that is not well formed. The message is safe to show."""


def _text(value):
    """Plan names and titles get the same cleaning as a handle, longer cap."""
    if not isinstance(value, str):
        return None
    clean = unicodedata.normalize("NFKC", value)
    clean = "".join(" " if ch in "\t\n\r" else study_card._keep(ch)
                    for ch in clean)
    clean = " ".join(clean.split())[:TEXT_MAX].strip()
    return clean or None


def _price(provider):
    """Monthly list price of the plan the source reports, or None."""
    plan = (provider.get("plan") or "").strip().lower()
    pricing = provider.get("subscription_pricing") or {}
    if not plan or pricing.get("currency") != "USD":
        return None
    for row in pricing.get("plans") or []:
        if str(row.get("title") or "").strip().lower() == plan:
            usd = row.get("monthly_usd")
            if isinstance(usd, (int, float)) and not isinstance(usd, bool) \
                    and 0 <= usd <= 10000:
                return round(float(usd), 2)
    return None


def _pick_pool(pools, ranks):
    """The weekly pool, else the window closest to a week, the source's own
    headline order breaking ties. None if no pool spans a day or more."""
    if "week" in pools:
        return "week"
    timed = [(abs((s.get("window_s") or 0) - WEEK_S),
              (ranks.get(name) or {}).get("rank", 99), name)
             for name, s in pools.items()
             if isinstance(s, dict) and (s.get("window_s") or 0) >= 86400]
    return min(timed)[2] if timed else None


def _curve(series, title, tz):
    """One pool's burndown, resampled to a coarse grid of whole percents."""
    start, end = series.get("window_start"), series.get("window_end")
    now = series.get("now")
    actual = [p for p in series.get("actual") or []
              if isinstance(p, (list, tuple)) and len(p) == 2]
    if not (isinstance(start, (int, float)) and isinstance(end, (int, float))
            and end > start):
        return None
    now = min(float(now if isinstance(now, (int, float)) else end), end)
    if now <= start:
        return None
    step = STEP_H * 3600

    def remaining_at(t):
        value = 100.0                     # a window opens full
        for ts, rem in actual:
            if ts > t:
                break
            value = rem
        return max(0, min(100, int(round(value))))

    rem = []
    t = float(start)
    while t <= now and len(rem) < MAX_POINTS - 1:
        rem.append(remaining_at(t))
        t += step
    elapsed = now - start
    if elapsed % step:
        current = series.get("remaining_pct")
        rem.append(max(0, min(100, int(round(current))))
                   if isinstance(current, (int, float))
                   else remaining_at(now))
    opened = datetime.fromtimestamp(start, tz or timezone.utc)
    return {
        "pool": _text(title) or "Weekly",
        "window_h": max(1, int(round((end - start) / 3600))),
        "step_h": STEP_H,
        "elapsed_h": max(1, int(round(elapsed / 3600))),
        "dow": opened.weekday(),
        "hour": opened.hour,
        "rem": rem,
    }


def build(doc, card=None, handle=None, now=None, tz=None):
    """A profile from a /usage document and, when there is one, a stats card."""
    now = now or datetime.now(tz or timezone.utc)
    year, week, _ = now.isocalendar()
    burndown = doc.get("burndown") or {}
    subs = []
    for provider in doc.get("providers") or []:
        if not (provider.get("enabled") and provider.get("ok")):
            continue
        sid = str(provider.get("id") or "")
        if not _ID.match(sid):
            continue
        accent = str(provider.get("accent") or "").upper()
        pools = burndown.get(sid) or {}
        meta = provider.get("pools") or {}
        name = _pick_pool(pools, meta)
        curve = None
        if name:
            title = (meta.get(name) or {}).get("title")
            curve = _curve(pools[name], title, tz)
        subs.append({
            "id": sid,
            "title": _text(provider.get("title")) or sid,
            "plan": _text(provider.get("plan")),
            "accent": accent if _ACCENT.match(accent) else None,
            "usd": _price(provider),
            "curve": curve,
        })
        if len(subs) == MAX_SUBS:
            break
    return {
        "v": VERSION,
        "handle": study_card.sanitize_handle(handle)
                  or (card or {}).get("handle") or "someone",
        "week": f"{year}-W{week:02d}",
        "made": now.strftime("%Y-%m-%d"),
        "subs": subs,
        "habits": (card or {}).get("data"),
    }


def encode(profile):
    raw = json.dumps(profile, separators=(",", ":"), sort_keys=True)
    return PREFIX + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def link(text):
    return PAGE_URL + "#" + text


# ---- decode -------------------------------------------------------------

def _int(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) \
            or not low <= value <= high:
        raise ProfileError(f"{name} must be a whole number {low}..{high}")
    return value


def _check_curve(curve):
    if curve is None:
        return
    if not isinstance(curve, dict) or set(curve) != _CURVE_KEYS:
        raise ProfileError("curve has the wrong fields")
    if _text(curve["pool"]) != curve["pool"]:
        raise ProfileError("bad pool name")
    _int(curve["window_h"], 1, 800, "window_h")
    _int(curve["step_h"], 1, 24, "step_h")
    _int(curve["elapsed_h"], 1, 800, "elapsed_h")
    _int(curve["dow"], 0, 6, "dow")
    _int(curve["hour"], 0, 23, "hour")
    rem = curve["rem"]
    if not isinstance(rem, list) or not 1 <= len(rem) <= MAX_POINTS:
        raise ProfileError("rem must hold 1..%d points" % MAX_POINTS)
    for value in rem:
        _int(value, 0, 100, "rem")


def _check_sub(sub):
    if not isinstance(sub, dict) or set(sub) != _SUB_KEYS:
        raise ProfileError("sub has the wrong fields")
    if not isinstance(sub["id"], str) or not _ID.match(sub["id"]):
        raise ProfileError("bad sub id")
    if _text(sub["title"]) != sub["title"]:
        raise ProfileError("bad sub title")
    if sub["plan"] is not None and _text(sub["plan"]) != sub["plan"]:
        raise ProfileError("bad plan name")
    if sub["accent"] is not None and (not isinstance(sub["accent"], str)
                                      or not _ACCENT.match(sub["accent"])):
        raise ProfileError("accent must be #RRGGBB")
    usd = sub["usd"]
    if usd is not None and (isinstance(usd, bool)
                            or not isinstance(usd, (int, float))
                            or not 0 <= usd <= 10000):
        raise ProfileError("usd must be 0..10000")
    _check_curve(sub["curve"])


def decode(text):
    """Text -> profile dict, or ProfileError. Mirrors the checks in the page."""
    if not isinstance(text, str):
        raise ProfileError("profile must be text")
    text = text.strip()
    if "#" in text:
        text = text.rsplit("#", 1)[1]
    if len(text) > MAX_ENCODED:
        raise ProfileError("profile is too large")
    if not text.startswith(PREFIX):
        raise ProfileError("not a Headroom profile, or a newer version")
    body = text[len(PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        profile = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProfileError("profile is not readable") from exc
    if not isinstance(profile, dict) or set(profile) != _PROFILE_KEYS:
        raise ProfileError("profile has the wrong fields")
    if profile["v"] != VERSION:
        raise ProfileError("unsupported profile version")
    if study_card.sanitize_handle(profile["handle"]) != profile["handle"]:
        raise ProfileError("bad handle")
    if not isinstance(profile["week"], str) \
            or not study_card._WEEK.match(profile["week"]):
        raise ProfileError("bad week")
    if not isinstance(profile["made"], str) or not _DATE.match(profile["made"]):
        raise ProfileError("bad date")
    subs = profile["subs"]
    if not isinstance(subs, list) or len(subs) > MAX_SUBS:
        raise ProfileError("subs must be a list of at most %d" % MAX_SUBS)
    for sub in subs:
        _check_sub(sub)
    if profile["habits"] is not None:
        try:
            study_card._check_data(profile["habits"], study_card.CARD_VERSION)
        except study_card.CardError as exc:
            raise ProfileError("habits: %s" % exc) from exc
    return profile
