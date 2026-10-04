# Reset calendar

Headroom serves upcoming resets and grant expiries as an iCalendar feed that
Calendar.app subscribes to, at `http://127.0.0.1:8737/calendar.ics`.
Settings → Calendar sets the options, copies the link and opens Calendar;
the subscription itself is File → New Calendar Subscription, with Location
set to On My Mac.

**The link is `http://`, never `webcal://`.** Calendar turns a webcal link
into HTTPS and gives up when TLS fails; the host has no certificate. Given an
`http://` link, Calendar tries TLS, then fetches over plain HTTP, and that
works (seen from a probe on 2026-10-03: two TLS handshakes, then
`GET /calendar.ics` from `dataaccessd`). Calendar may send `HEAD` first, so
the route answers it.

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

## On the iPhone: the Headroom calendar

The feed cannot reach the iPhone (see below), so the app can also write the
same events into a calendar named **Headroom** through EventKit. Settings →
Calendar → On your iPhone turns it on and asks for calendar access.

- The host decides the events. `GET /calendar.json` (loopback) returns the
  feed's events with each alert resolved; `ResetCalendarSync` creates, moves
  and removes events to match and decides nothing. The JSON ignores the
  feed's on/off switch, which only governs the .ics feed.
- The calendar goes in the iCloud account when there is one, so it syncs.
  A second Mac finds the existing "Headroom" calendar by name and writes into
  it rather than making another.
- An event is ours when its URL is `headroom-calendar:<uid>`. Nothing else in
  any calendar is read or changed. Duplicates of one uid, from two Macs
  racing on a first round, are removed down to one.
- Only future events are removed. A reset that already happened stays as a
  record. Turning the option off stops updates and leaves the calendar.
- A round runs at launch and every 5 minutes, and writes nothing when the
  events have not changed.
- Hardened Runtime needs `com.apple.security.personal-information.calendars`
  in `Headroom.entitlements`, and the prompt text is
  `NSCalendarsFullAccessUsageDescription` in `project.yml`. Neither needs a
  provisioning profile. Full access, not write-only, because write-only
  cannot find the events it made to move them.

## Why the feed is local

The host serves `/calendar.ics` to loopback only, with no token, because
Calendar.app cannot send one ([trust.md](trust.md), Class 1). So:

- **Set Location to On My Mac when you subscribe.** A subscription stored in
  iCloud is fetched by Apple's servers, which cannot reach this Mac, and the
  calendar stays empty with no error.
- The phone does not see it. That is what the Headroom calendar above is
  for.

Calendar.app polls on its own schedule, which you choose in the subscription
dialog (every 5 minutes at the fastest). `REFRESH-INTERVAL` asks for 15
minutes; Calendar.app mostly ignores it.

## Where the code is

| File | Job |
|---|---|
| `host/reset_calendar.py` | The feed: events from a document, then iCal text |
| `host/app_config.py` | `calendar_settings()` / `set_calendar()`, key `calendar` |
| `host/headroom_server.py` | `GET /calendar.ics`, `GET`/`POST /config/calendar` |
| `macos/Sources/Settings/SettingsView+Calendar.swift` | The Settings page |
| `macos/Sources/ResetCalendarSync.swift` | The EventKit writer |

The options are per Mac and are not in `SHARED_CONFIG_KEYS`: a subscription
belongs to the Mac whose Calendar holds it.
