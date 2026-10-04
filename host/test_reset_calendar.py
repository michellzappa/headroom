#!/usr/bin/env python3
import unittest

import app_config
import reset_calendar

NOW = 1_800_000_000.0
DEFAULTS = dict(app_config.CALENDAR_DEFAULTS)


def doc():
    return {
        "updated": "2027-01-15T09:00:00+0200",
        "providers": [
            {"id": "claude", "title": "Claude", "enabled": True, "pools": {
                "week": {"title": "Weekly", "kind": "window",
                         "window_s": 604800, "resets_in_s": 7200},
                "session": {"title": "Session", "kind": "window",
                            "window_s": 18000, "resets_in_s": 600},
            }},
            {"id": "codex", "title": "Codex", "enabled": True, "pools": {
                "week": {"title": "Weekly", "kind": "window",
                         "window_s": 604800, "resets_in_s": 0},
                "credits": {"title": "Credits", "kind": "grant",
                            "expires_in_s": 999},
            }},
            {"id": "cursor", "title": "Cursor", "enabled": False, "pools": {
                "total": {"title": "Total", "kind": "window",
                          "window_s": 2592000, "resets_in_s": 100},
            }},
        ],
        "codex": {"reset_credits_expire_at": [1_800_000_000 + 86_400,
                                              1_800_000_000 + 172_800,
                                              1_000]},
    }


class EventTests(unittest.TestCase):
    def measured(self):
        return reset_calendar._document_time(doc(), NOW)

    def test_document_time_reads_the_compact_offset(self):
        self.assertNotEqual(self.measured(), NOW)

    def test_defaults_carry_weekly_resets_and_each_credit(self):
        events = reset_calendar.events(doc(), DEFAULTS, NOW)
        uids = [e["uid"] for e in events]
        self.assertIn("reset-claude-week", uids)
        self.assertNotIn("reset-claude-session", uids)     # opt-in
        self.assertNotIn("reset-codex-week", uids)         # no time stated
        self.assertFalse(any(u.startswith("reset-cursor") for u in uids))
        expiries = [e for e in events if e["kind"] == "expiry"]
        self.assertEqual(len(expiries), 2)                 # past one dropped

    def test_reset_lands_at_measured_plus_left(self):
        week = [e for e in reset_calendar.events(doc(), DEFAULTS, NOW)
                if e["uid"] == "reset-claude-week"][0]
        self.assertEqual(week["start"], int(self.measured() + 7200))

    def test_short_windows_when_asked(self):
        options = dict(DEFAULTS, short_windows=True)
        uids = [e["uid"] for e in reset_calendar.events(doc(), options, NOW)]
        self.assertIn("reset-claude-session", uids)

    def test_switches_off(self):
        options = dict(DEFAULTS, resets=False, expiries=False)
        self.assertEqual(reset_calendar.events(doc(), options, NOW), [])

    def test_grant_without_stamps_uses_the_soonest(self):
        d = doc()
        del d["codex"]
        expiries = [e for e in reset_calendar.events(d, DEFAULTS, NOW)
                    if e["kind"] == "expiry"]
        self.assertEqual(len(expiries), 1)

    def test_junk_document_is_an_empty_feed(self):
        for junk in (None, {}, {"providers": "x"},
                     {"providers": [None, {"enabled": True, "pools": []}]}):
            self.assertEqual(reset_calendar.events(junk, DEFAULTS, NOW), [])


class RenderTests(unittest.TestCase):
    def test_feed_is_valid_shape_and_stable(self):
        first = reset_calendar.render(doc(), DEFAULTS, NOW)
        again = reset_calendar.render(doc(), DEFAULTS, NOW + 30)
        self.assertEqual(first, again)
        self.assertTrue(first.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(first.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(first.count("BEGIN:VEVENT"), 3)
        for line in first.split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)

    def test_alarms_follow_the_options(self):
        text = reset_calendar.render(doc(), DEFAULTS, NOW)
        self.assertEqual(text.count("BEGIN:VALARM"), 2)    # expiries only
        self.assertIn("TRIGGER:-PT1440M", text)
        options = dict(DEFAULTS, reset_alert_min=0, expiry_alert_min=None)
        text = reset_calendar.render(doc(), options, NOW)
        self.assertEqual(text.count("BEGIN:VALARM"), 1)
        self.assertIn("TRIGGER:PT0M", text)

    def test_text_is_escaped_and_long_lines_fold(self):
        d = doc()
        d["providers"][0]["title"] = "A, b; c\\" + "x" * 120
        text = reset_calendar.render(d, DEFAULTS, NOW)
        self.assertIn(r"A\, b\; c\\", text)
        self.assertIn("\r\n ", text)


class PayloadTests(unittest.TestCase):
    def test_payload_resolves_alerts_and_ignores_enabled(self):
        options = dict(DEFAULTS, enabled=False, reset_alert_min=15)
        got = reset_calendar.payload(doc(), options, NOW)["events"]
        self.assertEqual(len(got), 3)
        by_kind = {e["kind"]: e for e in got}
        self.assertEqual(by_kind["reset"]["alert_min"], 15)
        self.assertEqual(by_kind["expiry"]["alert_min"], 1440)
        for e in got:
            self.assertEqual(e["end"] - e["start"],
                             reset_calendar.EVENT_MINUTES * 60)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self.tmp = tempfile.TemporaryDirectory()
        self._path = app_config.STORE_PATH
        app_config.STORE_PATH = os.path.join(self.tmp.name, "config.json")
        app_config.reload()

    def tearDown(self):
        app_config.STORE_PATH = self._path
        app_config.reload()
        self.tmp.cleanup()

    def test_defaults_and_round_trip(self):
        self.assertEqual(app_config.calendar_settings(), DEFAULTS)
        out = app_config.set_calendar({"short_windows": True,
                                       "reset_alert_min": 15})
        self.assertTrue(out["short_windows"])
        self.assertEqual(out["reset_alert_min"], 15)
        self.assertTrue(out["enabled"])

    def test_bad_values_are_refused(self):
        for bad in ({"nope": True}, {"enabled": "yes"},
                    {"reset_alert_min": 7}, {"expiry_alert_min": True}, []):
            with self.assertRaises(ValueError):
                app_config.set_calendar(bad)


if __name__ == "__main__":
    unittest.main()


class CalendarHTTPTests(unittest.TestCase):
    """The feed is loopback only, answers HEAD, and goes away when off."""

    def setUp(self):
        from unittest import mock
        for patcher in (
                mock.patch("headroom_server.rollup", return_value=doc()),
                mock.patch("headroom_server.app_config.calendar_settings",
                           side_effect=lambda: dict(self.options))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.options = dict(DEFAULTS)

    def test_get_and_head_from_loopback(self):
        status, _, body = self.raw("GET")
        self.assertEqual(status, 200)
        self.assertIn(b"BEGIN:VCALENDAR", body)
        status, _, body = self.raw("HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_lan_is_refused_even_with_a_token(self):
        from unittest import mock
        import headroom_server
        with mock.patch.object(headroom_server.Handler, "_allowed",
                               return_value=True):
            status, _, _ = self.raw("GET", peer=("192.168.1.9", 4000))
        self.assertEqual(status, 403)

    def test_json_for_the_app(self):
        import json
        status, _, body = self.raw("GET", path="/calendar.json")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["events"]), 3)
        status, _, _ = self.raw("GET", path="/calendar.json",
                                peer=("192.168.1.9", 4000))
        self.assertNotEqual(status, 200)

    def test_off_is_not_found(self):
        self.options["enabled"] = False
        status, _, _ = self.raw("GET")
        self.assertEqual(status, 404)

    def raw(self, method, peer=("127.0.0.1", 12345), path="/calendar.ics"):
        import socket
        from types import SimpleNamespace
        import headroom_server
        server, client = socket.socketpair()
        try:
            client.sendall(f"{method} {path} HTTP/1.0\r\n"
                           "Host: localhost\r\n\r\n".encode())
            client.shutdown(socket.SHUT_WR)
            headroom_server.Handler(server, peer,
                                    SimpleNamespace(server_port=8737))
            server.close()
            data = b""
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            client.close()
        head, _, body = data.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        return status, head, body
