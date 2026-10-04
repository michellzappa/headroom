"""Upcoming resets and grant expiries as an iCalendar feed.

Calendar.app subscribes to `http://127.0.0.1:8737/calendar.ics` and polls
it on its own schedule. Every poll rebuilds the feed from the current `/usage`
document, so an event moves when a provider moves its reset and disappears
when a grant is spent. Nothing is stored.

Two kinds of event:

  * **A window reset.** One event per window pool, at its next reset. Windows
    shorter than a day (the 5-hour session) are opt-in: they reset several
    times a day and the time moves with use, which makes a noisy calendar.
  * **A grant expiry.** One event per held item, at the moment it expires
    (Codex reset credits today). These are the ones worth an alarm.

The feed is served to loopback only. A subscription stored in iCloud is
fetched by Apple's servers, which cannot reach this Mac, so it has to be
stored On My Mac. See docs/calendar.md.

Pure: a document, the options and a clock in, text out. Stdlib only.
"""

from __future__ import annotations

from datetime import datetime, timezone

PRODID = "-//Centaur Labs//Headroom//EN"
CALENDAR_NAME = "Headroom resets"
# How often a client should poll. Calendar.app offers its own choices and
# mostly ignores this; other clients read it.
REFRESH = "PT15M"
# Windows at least this long are on by default. The session window is not.
LONG_WINDOW_S = 24 * 3600
EVENT_MINUTES = 15
# Reset times drift by seconds between polls. Rounding to the minute keeps an
# unchanged reset byte-identical, so a client sees no edit.
ROUND_S = 60

ALERT_CHOICES = (None, 0, 15, 60, 1440)


def _document_time(doc, now):
    """When the document's relative fields were measured. Falls back to now."""
    text = (doc or {}).get("updated")
    if isinstance(text, str) and text:
        # "2026-07-25T14:32:00+0200": fromisoformat on 3.9 wants the colon.
        if len(text) > 5 and text[-5] in "+-" and text[-4:].isdigit():
            text = text[:-2] + ":" + text[-2:]
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except ValueError:
            pass
    return now


def _round(t):
    return int(round(t / ROUND_S) * ROUND_S)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def events(doc, options, now):
    """The events the feed carries, as plain dicts, soonest first.

    `options` is app_config.calendar_settings(). Each event: uid, start (epoch
    seconds), title, description, kind ("reset" or "expiry").
    """
    measured = _document_time(doc, now)
    out = []
    for provider in (doc or {}).get("providers") or []:
        if not isinstance(provider, dict) or not provider.get("enabled"):
            continue
        pid = str(provider.get("id") or "")
        name = str(provider.get("title") or pid)
        pools = provider.get("pools") or {}
        if not isinstance(pools, dict):
            continue
        for pool_id, pool in pools.items():
            if not isinstance(pool, dict):
                continue
            kind = pool.get("kind") or "window"
            title = str(pool.get("title") or pool_id)
            if kind == "window" and options.get("resets", True):
                out.extend(_reset(pid, name, pool_id, title, pool, options,
                                  measured))
            elif kind == "grant" and options.get("expiries", True):
                out.extend(_expiries(doc, pid, name, pool_id, title, pool,
                                     measured))
    return sorted(out, key=lambda e: (e["start"], e["uid"]))


def _reset(pid, name, pool_id, title, pool, options, measured):
    left = _number(pool.get("resets_in_s"))
    window = _number(pool.get("window_s"))
    # Zero means the provider has not said when; a past time means the same.
    if left is None or left <= 0:
        return []
    if (window is None or window < LONG_WINDOW_S) \
            and not options.get("short_windows", False):
        return []
    return [{
        "uid": f"reset-{pid}-{pool_id}",
        "start": _round(measured + left),
        "title": f"{name} {title.lower()} resets",
        "description": f"Your {name} {title.lower()} window starts again.",
        "kind": "reset",
    }]


def _expiries(doc, pid, name, pool_id, title, pool, measured):
    # Codex states each credit's expiry. Other grants only state the soonest.
    flat = (doc or {}).get(pid) or {}
    stamps = flat.get("reset_credits_expire_at") if isinstance(flat, dict) \
        else None
    times = []
    if isinstance(stamps, list) and stamps:
        times = [t for t in (_number(s) for s in stamps) if t is not None]
    else:
        left = _number(pool.get("expires_in_s"))
        if left is not None and left > 0:
            times = [measured + left]
    out = []
    for at in times:
        at = _round(at)
        if at <= measured:
            continue
        out.append({
            "uid": f"expiry-{pid}-{pool_id}-{at}",
            "start": at,
            "title": f"{name} {title.lower()}: one expires",
            "description": (f"One of your {name} {title.lower()} expires. "
                            "Use it before then or lose it."),
            "kind": "expiry",
        })
    return out


def payload(doc, options, now):
    """The events as JSON for the Mac app, which writes them to Calendar.

    Same events as the feed, with each one's alert already resolved, so the
    app carries them and decides nothing. Independent of `enabled`, which
    only governs the .ics feed.
    """
    out = []
    for event in events(doc, options, now):
        alert = options.get("expiry_alert_min" if event["kind"] == "expiry"
                            else "reset_alert_min")
        out.append({**event, "end": event["start"] + EVENT_MINUTES * 60,
                    "alert_min": alert})
    return {"ok": True, "events": out}


# ------------------------------------------------------------------ iCal


def _escape(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r", "").replace("\n", "\\n"))


def _fold(line):
    """RFC 5545: lines over 75 octets continue with a leading space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    parts, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > (75 if not parts else 74):
            parts.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += b
    parts.append(chunk.decode("utf-8"))
    return [parts[0]] + [" " + p for p in parts[1:]]


def _stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _alarm(minutes):
    trigger = "PT0M" if minutes == 0 else f"-PT{minutes}M"
    return ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Headroom",
            f"TRIGGER:{trigger}", "END:VALARM"]


def render(doc, options, now, host="headroom"):
    """The whole feed as text, CRLF line endings."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}",
        f"X-PUBLISHED-TTL:{REFRESH}",
    ]
    # DTSTAMP is the document's time, not the clock, so an unchanged feed is
    # byte-identical between polls.
    stamp = _stamp(_round(_document_time(doc, now)))
    for event in events(doc, options, now):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['uid']}@{host}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_stamp(event['start'])}",
            f"DTEND:{_stamp(event['start'] + EVENT_MINUTES * 60)}",
            f"SUMMARY:{_escape(event['title'])}",
            f"DESCRIPTION:{_escape(event['description'])}",
            "TRANSP:TRANSPARENT",
        ]
        alert = options.get("expiry_alert_min" if event["kind"] == "expiry"
                            else "reset_alert_min")
        if alert is not None:
            lines += _alarm(int(alert))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    out = []
    for line in lines:
        out.extend(_fold(line))
    return "\r\n".join(out) + "\r\n"
