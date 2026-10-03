# Reset calendar

Headroom serves upcoming resets and grant expiries as an iCalendar feed that
Calendar.app subscribes to. Settings → General → Calendar sets the options and
has a **Subscribe in Calendar** button.

## What is in it

| Event | When | Default |
|---|---|---|
| A window reset | The next reset of each window pool of a day or longer (weekly, monthly) | on |
| A session reset | The next reset of each window shorter than a day (the 5-hour session) | off |
| A grant expiry | One event per held item, at its expiry (Codex reset credits) | on, alert 1 day before |

The feed is rebuilt from the current `/usage` document on every request. An
event moves when a provider moves its reset and disappears when a credit is
spent. Nothing is stored. A window whose provider has not stated a reset time
(`resets_in_s` of 0) has no event.

Session resets are opt-in because they happen several times a day and the time
moves with use. Each window has one upcoming event; the feed does not project
later resets, because a rolling window's next start depends on when you next
use it.

Times are rounded to the minute and `DTSTAMP` is the document's time, so an
unchanged feed is byte-identical between polls and the client sees no edit.

## Why it is local

The host serves `/calendar.ics` to loopback only, with no token, because
Calendar.app cannot send one ([trust.md](trust.md), Class 1). So:

- **Set Location to On My Mac when you subscribe.** A subscription stored in
  iCloud is fetched by Apple's servers, which cannot reach this Mac, and the
  calendar stays empty with no error.
- The phone does not see it. A calendar that reaches every device needs a
  feed served from somewhere public, or the app writing events through
  EventKit into a calendar that syncs. Both are open, not built.

Calendar.app polls on its own schedule, which you choose in the subscription
dialog (every 5 minutes at the fastest). `REFRESH-INTERVAL` asks for 15
minutes; Calendar.app mostly ignores it.

## Where the code is

| File | Job |
|---|---|
| `host/reset_calendar.py` | The feed: events from a document, then iCal text |
| `host/app_config.py` | `calendar_settings()` / `set_calendar()`, key `calendar` |
| `host/headroom_server.py` | `GET /calendar.ics`, `GET`/`POST /config/calendar` |
| `macos/Sources/Settings/SettingsView+Calendar.swift` | The Settings section |

The options are per Mac and are not in `SHARED_CONFIG_KEYS`: a subscription
belongs to the Mac whose Calendar holds it.
