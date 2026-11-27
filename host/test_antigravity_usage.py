#!/usr/bin/env python3
"""Unit tests for the Google Antigravity quota provider."""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import antigravity_usage
import cache_util
import detect_sources
import headroom_server
import sources_config


def _make_state_db(path: Path, items: dict) -> str:
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        for key, value in items.items():
            con.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                (key, value),
            )
        con.commit()
    finally:
        con.close()
    return str(path)


class ProcessAndPortParsingTests(unittest.TestCase):
    def test_parses_antigravity_and_skips_windsurf(self):
        ps_out = "\n".join([
            "  101 /Applications/Windsurf.app/Contents/Resources/app/extensions/windsurf/bin/language_server_macos_arm --app_data_dir windsurf --csrf_token ws-tok --extension_server_port 51111",
            "  202 /Applications/Antigravity.app/Contents/Resources/app/extensions/antigravity/bin/language_server_macos_arm --app_data_dir antigravity --csrf_token agy-tok-1 --https_server_port 61234 --extension_server_port 61235",
            "  250 /Applications/Antigravity.app/Contents/Resources/bin/language_server --standalone --override_ide_name antigravity --subclient_type hub --https_server_port 0 --csrf_token agy-tok-hub --app_data_dir antigravity",
            "  260 /Users/mz/.local/share/agy/bin/language_server_macos_arm --override_ide_name agy --app_data_dir agy --csrf_token agy-tok-cli",
            "  303 /Users/mz/Library/Application Support/Antigravity/bin/language_server_macos --csrf_token=agy-tok-2 --https_server_port=0",
            "  404 /usr/bin/python3 headroom_server.py",
        ])
        servers = antigravity_usage._parse_ps_output(ps_out)
        self.assertEqual([s["pid"] for s in servers], [202, 250, 260, 303])
        self.assertEqual(servers[0]["csrf_token"], "agy-tok-1")
        self.assertEqual(servers[0]["https_port"], 61234)
        self.assertEqual(servers[0]["ext_port"], 61235)
        self.assertEqual(servers[1]["csrf_token"], "agy-tok-hub")
        self.assertIsNone(servers[1]["https_port"])
        self.assertEqual(servers[2]["csrf_token"], "agy-tok-cli")
        self.assertEqual(servers[3]["csrf_token"], "agy-tok-2")
        self.assertIsNone(servers[3]["https_port"])

    def test_parses_lsof_loopback_ports(self):
        lsof_out = "\n".join([
            "COMMAND     PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME",
            "language_   202   mz   12u  IPv4  0x123      0t0  TCP 127.0.0.1:54321 (LISTEN)",
            "language_   202   mz   13u  IPv6  0x124      0t0  TCP [::1]:54322 (LISTEN)",
            "language_   202   mz   14u  IPv4  0x125      0t0  TCP 127.0.0.1:54321 (LISTEN)",
            "language_   202   mz   15u  IPv4  0x126      0t0  TCP 10.0.0.5:9999 (LISTEN)",
        ])
        self.assertEqual(
            antigravity_usage._parse_lsof_ports(lsof_out),
            [54321, 54322],
        )

    def test_candidate_endpoints_deduplicates_and_orders_https_first(self):
        server = {
            "pid": 202,
            "csrf_token": "tok",
            "https_port": 61234,
            "ext_port": 61235,
        }
        endpoints = antigravity_usage._candidate_endpoints(
            server, [61234, 61235])
        self.assertEqual(
            endpoints,
            [
                ("https", 61234),
                ("https", 61235),
                ("http", 61234),
                ("http", 61235),
            ],
        )

    def test_reads_daemon_discovery_json_and_skips_dead_pids(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon_dir = Path(tmp) / "daemon"
            daemon_dir.mkdir()
            (daemon_dir / "hub_49561.json").write_text(json.dumps({
                "pid": 87892,
                "httpsPort": 49560,
                "httpPort": 49561,
                "lsVersion": "2.34.0",
                "csrfToken": "tok-live",
            }))
            (daemon_dir / "hub_11111.json").write_text(json.dumps({
                "pid": 99999,
                "httpsPort": 11110,
                "httpPort": 11111,
                "csrfToken": "tok-stale",
            }))
            (daemon_dir / "broken.json").write_text("{not json")

            servers = antigravity_usage._read_daemon_servers(
                home_dirs=(tmp,),
                alive_check=lambda pid: pid == 87892,
            )
            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0]["pid"], 87892)
            self.assertEqual(servers[0]["csrf_token"], "tok-live")
            self.assertEqual(servers[0]["https_port"], 49560)
            self.assertEqual(servers[0]["http_port"], 49561)

    def test_merges_daemon_csrf_and_ports_into_ps_server_without_cli_csrf(self):
        # Antigravity 2.34+ omits --csrf_token from argv and writes it to daemon/*.json.
        ps_out = (
            "87892 /Applications/Antigravity.app/Contents/Resources/bin/language_server "
            "--standalone --override_ide_name antigravity --subclient_type hub "
            "--override_ide_version 2.34.0 --https_server_port 0 --app_data_dir antigravity "
            "--read_host_bridge_token_from_stdin"
        )
        ps_servers = antigravity_usage._parse_ps_output(ps_out)
        self.assertEqual(ps_servers[0]["csrf_token"], "")
        self.assertIsNone(ps_servers[0]["https_port"])

        daemon_servers = [{
            "pid": 87892,
            "csrf_token": "tok-from-daemon",
            "https_port": 49560,
            "http_port": 49561,
            "ext_port": None,
        }]
        merged = antigravity_usage._merge_servers(daemon_servers, ps_servers)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["pid"], 87892)
        self.assertEqual(merged[0]["csrf_token"], "tok-from-daemon")
        self.assertEqual(merged[0]["https_port"], 49560)
        self.assertEqual(merged[0]["http_port"], 49561)

        endpoints = antigravity_usage._candidate_endpoints(merged[0], ())
        self.assertEqual(
            endpoints,
            [("https", 49560), ("http", 49561), ("https", 49561)],
        )


class QuotaMappingTests(unittest.TestCase):
    NOW = 1800000000.0  # 2027-01-15T08:00:00Z

    def test_maps_quota_summary_with_dual_windows_and_plan(self):
        summary = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "displayName": "Five-hour limit",
                                "window": "FIVE_HOUR",
                                "remainingFraction": 0.75,
                                "resetTime": "2027-01-15T12:00:00Z",
                            },
                            {
                                "bucketId": "gemini-weekly",
                                "displayName": "Weekly limit",
                                "window": "WEEKLY",
                                "remainingFraction": 0.60,
                                "resetTime": "2027-01-20T08:00:00Z",
                            },
                        ],
                    },
                    {
                        "displayName": "Claude and GPT models",
                        "buckets": [
                            {
                                "bucketId": "3p-5h",
                                "window": "FIVE_HOUR",
                                "remainingFraction": 0.30,
                                "resetTime": "2027-01-15T10:00:00Z",
                            },
                            {
                                "bucketId": "3p-weekly",
                                "window": "WEEKLY",
                                "remainingFraction": 0.80,
                                "resetTime": "2027-01-19T08:00:00Z",
                            },
                            {
                                "bucketId": "disabled-bucket",
                                "window": "WEEKLY",
                                "remainingFraction": 0.0,
                                "disabled": True,
                            },
                        ],
                    },
                ]
            }
        }
        status = {
            "userStatus": {
                "planStatus": {
                    "planInfo": {"planName": "Google AI Pro"}
                }
            }
        }
        out = antigravity_usage._map_combined(
            summary_blob=summary, status_blob=status, now=self.NOW)
        self.assertTrue(out["ok"])
        self.assertEqual(out["plan"], "Google AI Pro")
        # Tightest weekly is Gemini (40% used); tightest 5h is 3p (70% used).
        self.assertEqual(out["week"]["pct"], 40.0)
        self.assertEqual(out["week"]["resets_in_s"], 5 * 86400)
        self.assertEqual(out["week"]["window_s"], antigravity_usage.WEEK_WINDOW_S)
        self.assertEqual(out["session"]["pct"], 70.0)
        self.assertEqual(out["session"]["resets_in_s"], 2 * 3600)
        self.assertEqual(
            out["session"]["window_s"], antigravity_usage.SESSION_WINDOW_S)

    def test_handles_proto3_omitted_zero_remaining_fraction(self):
        summary = {
            "groups": [
                {
                    "buckets": [
                        {
                            "window": "FIVE_HOUR",
                            "resetTime": "2027-01-15T11:00:00Z",
                        }
                    ]
                }
            ]
        }
        out = antigravity_usage._map_combined(
            summary_blob=summary, now=self.NOW)
        self.assertTrue(out["ok"])
        self.assertEqual(out["session"]["pct"], 100.0)
        self.assertEqual(out["session"]["resets_in_s"], 3 * 3600)
        self.assertIsNone(out["week"])

    def test_falls_back_to_user_status_client_model_configs(self):
        status = {
            "userStatus": {
                "userTier": {"id": "g1-ultra-tier"},
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {
                            "label": "Gemini 3.1 Pro (High)",
                            "quotaInfo": {
                                "remainingFraction": 0.85,
                                "resetTime": "2027-01-15T11:30:00Z",
                            },
                        },
                        {
                            "label": "Claude Sonnet",
                            "quotaInfo": {
                                "remainingFraction": 0.50,
                                "resetTime": "2027-01-15T09:30:00Z",
                            },
                        },
                    ]
                },
            }
        }
        out = antigravity_usage._map_combined(status_blob=status, now=self.NOW)
        self.assertTrue(out["ok"])
        self.assertEqual(out["plan"], "Google AI Ultra 20x")
        self.assertIsNone(out["week"])
        self.assertEqual(out["session"]["pct"], 50.0)
        self.assertEqual(out["session"]["resets_in_s"], 5400)

    def test_classifies_multi_day_model_reset_as_weekly(self):
        configs = {
            "clientModelConfigs": [
                {
                    "label": "Gemini Pro",
                    "quotaInfo": {
                        "remainingFraction": 0.25,
                        "resetTime": "2027-01-20T08:00:00Z",
                    },
                }
            ]
        }
        out = antigravity_usage._map_combined(
            configs_blob=configs, now=self.NOW)
        self.assertTrue(out["ok"])
        self.assertEqual(out["week"]["pct"], 75.0)
        self.assertIsNone(out["session"])

    def test_parses_top_level_cascade_and_dict_models(self):
        cascade = {
            "cascadeModelConfigData": {
                "clientModelConfigs": [
                    {
                        "label": "Gemini Pro",
                        "quotaInfo": {
                            "remainingFraction": 0.40,
                            "resetTime": "2027-01-15T10:00:00Z",
                        },
                    }
                ]
            }
        }
        out = antigravity_usage._map_combined(
            configs_blob=cascade, now=self.NOW)
        self.assertTrue(out["ok"])
        self.assertEqual(out["session"]["pct"], 60.0)

        models_map = {
            "models": {
                "gemini-pro": {
                    "quotaInfo": {
                        "remainingFraction": 0.20,
                        "resetTime": "2027-01-15T11:00:00Z",
                    }
                }
            }
        }
        out2 = antigravity_usage._map_combined(
            configs_blob=models_map, now=self.NOW)
        self.assertTrue(out2["ok"])
        self.assertEqual(out2["session"]["pct"], 80.0)


class DetectionAndFetchTests(unittest.TestCase):
    def setUp(self):
        self._orig_cache = dict(antigravity_usage._cache)
        antigravity_usage._cache.clear()
        antigravity_usage._cache.update({"t": 0.0, "data": None, "err": None})

    def tearDown(self):
        antigravity_usage._cache.clear()
        antigravity_usage._cache.update(self._orig_cache)

    def test_detection_is_false_without_antigravity_artifacts(self):
        with patch.object(antigravity_usage, "_find_servers", return_value=[]):
            with patch.object(antigravity_usage, "STATE_DB_PATHS", ()):
                with patch.object(antigravity_usage, "HOME_DIRS", ()):
                    with patch.object(cache_util, "load_disk", return_value=None):
                        self.assertFalse(antigravity_usage.signed_in())

    def test_detection_finds_state_vscdb_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_state_db(
                Path(tmp) / "state.vscdb",
                {
                    "antigravityAuthStatus": json.dumps({
                        "apiKey": "ya29.test",
                        "email": "mz@example.com",
                    })
                },
            )
            with patch.object(antigravity_usage, "_find_servers", return_value=[]):
                with patch.object(antigravity_usage, "STATE_DB_PATHS", (db,)):
                    with patch.object(antigravity_usage, "HOME_DIRS", ()):
                        with patch.object(cache_util, "load_disk", return_value=None):
                            self.assertTrue(antigravity_usage.signed_in())
                            auth = antigravity_usage._read_state_db_auth()
                            self.assertEqual(auth["apiKey"], "ya29.test")

    def test_fetch_queries_local_server_and_stores_result(self):
        server = {
            "pid": 202,
            "csrf_token": "tok",
            "https_port": 61234,
            "ext_port": None,
        }

        def fake_rpc(scheme, port, method, csrf, body=None, timeout=4):
            self.assertEqual((scheme, port, csrf), ("https", 61234, "tok"))
            if method == "RetrieveUserQuotaSummary":
                return 200, {
                    "response": {
                        "groups": [{
                            "buckets": [
                                {"window": "WEEKLY", "remainingFraction": 0.65},
                                {"window": "FIVE_HOUR", "remainingFraction": 0.90},
                            ]
                        }]
                    }
                }
            if method == "GetUserStatus":
                return 200, {
                    "userStatus": {
                        "planStatus": {"planInfo": {"planName": "Google AI Pro"}}
                    }
                }
            return 404, None

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache_util, "CACHE_DIR", tmp):
                with patch.object(
                        antigravity_usage, "_find_servers", return_value=[server]):
                    with patch.object(
                            antigravity_usage, "_listening_ports", return_value=[61234]):
                        with patch.object(
                                antigravity_usage, "_rpc_post", side_effect=fake_rpc):
                            out = antigravity_usage.fetch_quota(force=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["plan"], "Google AI Pro")
        self.assertEqual(out["week"]["pct"], 35.0)
        self.assertEqual(out["session"]["pct"], 10.0)

    def test_query_server_skips_lsof_when_daemon_https_port_works(self):
        server = {
            "pid": 87892,
            "csrf_token": "tok-daemon",
            "https_port": 49560,
            "http_port": 49561,
            "ext_port": None,
        }

        def fake_rpc(scheme, port, method, csrf, body=None, timeout=4):
            self.assertEqual((scheme, port, csrf), ("https", 49560, "tok-daemon"))
            if method == "RetrieveUserQuotaSummary":
                return 200, {
                    "groups": [{
                        "buckets": [
                            {"window": "WEEKLY", "remainingFraction": 0.80},
                        ]
                    }]
                }
            return 200, {}

        with patch.object(
                antigravity_usage, "_listening_ports",
                side_effect=AssertionError("lsof should not be called")):
            with patch.object(
                    antigravity_usage, "_rpc_post", side_effect=fake_rpc):
                out = antigravity_usage._query_server(server)
        self.assertTrue(out["ok"])
        self.assertEqual(out["week"]["pct"], 20.0)

    def test_query_server_reports_401_missing_csrf_error(self):
        server = {
            "pid": 87892,
            "csrf_token": "",
            "https_port": 49560,
            "http_port": None,
            "ext_port": None,
        }

        def fake_rpc(scheme, port, method, csrf, body=None, timeout=4):
            return 401, {
                "code": "unauthenticated",
                "message": "unauthenticated: missing CSRF token",
            }

        with patch.object(antigravity_usage, "_listening_ports", return_value=[]):
            with patch.object(
                    antigravity_usage, "_rpc_post", side_effect=fake_rpc):
                out = antigravity_usage._query_server(server)
        self.assertFalse(out["ok"])
        self.assertIn("missing CSRF token", out["error"])

    def test_fetch_preserves_stale_with_fix_when_app_closed(self):
        prior = {
            "ok": True,
            "plan": "Google AI Pro",
            "week": {"pct": 35.0, "resets_in_s": 3600, "resets_in": "1h", "window_s": 604800},
            "session": {"pct": 10.0, "resets_in_s": 1800, "resets_in": "30m", "window_s": 18000},
            "fetched_at": 1700000000.0,
        }
        with patch.object(antigravity_usage, "HOME_DIRS", ()):
            with patch.object(antigravity_usage, "_find_servers", return_value=[]):
                with patch.object(antigravity_usage, "_read_state_db_auth", return_value=None):
                    with patch.object(antigravity_usage, "_local_install_present", return_value=True):
                        with patch.object(cache_util, "load_disk", return_value=prior):
                            out = antigravity_usage.fetch_quota(force=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["stale"])
        self.assertFalse(out["auth_required"])
        self.assertEqual(out["error"], "Antigravity is not running")
        self.assertEqual(out["fix"], "Open Antigravity or run agy, then refresh Headroom.")

    def test_reads_agy_oauth_token_file_with_rfc3339nano_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli_dir = Path(tmp) / "antigravity-cli"
            cli_dir.mkdir()
            # 5-digit fractional seconds like Go's RFC3339Nano (Python 3.9 trap).
            (cli_dir / "antigravity-oauth-token").write_text(json.dumps({
                "token": {
                    "access_token": "ya29.fresh",
                    "token_type": "Bearer",
                    "expiry": "2027-01-15T10:00:00.75591+01:00",
                },
                "auth_method": "consumer",
            }))
            desk_dir = Path(tmp) / "antigravity"
            desk_dir.mkdir()
            (desk_dir / "antigravity-oauth-token").write_text(json.dumps({
                "token": {
                    "access_token": "ya29.expired",
                    "expiry": "2027-01-15T07:00:00Z",
                },
            }))
            now = 1800000000.0  # 2027-01-15T08:00:00Z
            rows = antigravity_usage._read_oauth_token_files(
                home_dirs=(str(desk_dir), str(cli_dir)), now=now)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["access_token"], "ya29.fresh")
            self.assertFalse(rows[0]["expired"])
            self.assertEqual(rows[1]["access_token"], "ya29.expired")
            self.assertTrue(rows[1]["expired"])

    def test_extract_plan_prefers_user_tier_over_legacy_plan_info(self):
        status = {
            "userStatus": {
                "planStatus": {
                    "planInfo": {"planName": "Pro", "teamsTier": "TEAMS_TIER_PRO"}
                },
                "userTier": {
                    "id": "g1-ultra-lite-tier",
                    "name": "Google AI Ultra",
                },
            }
        }
        self.assertEqual(
            antigravity_usage._extract_plan(status), "Google AI Ultra 5x")
        self.assertEqual(
            antigravity_usage._normalize_plan("g1-ultra-lite-tier"),
            "Google AI Ultra 5x")

    def test_the_tier_id_tells_the_two_ultra_plans_apart(self):
        for tid, plan in (("g1-ultra-lite-tier", "Google AI Ultra 5x"),
                          ("g1-ultra-tier", "Google AI Ultra 20x")):
            for blob in ({"paidTier": {"id": tid, "name": "Google AI Ultra"}},
                         {"paidTier": [{"id": tid, "name": "Google AI Ultra"}]},
                         {"userStatus": {"userTier": {
                             "id": tid, "name": "Google AI Ultra"}}}):
                self.assertEqual(antigravity_usage._extract_plan(blob), plan)
        self.assertEqual(antigravity_usage._extract_plan(
            {"paidTier": {"name": "Google AI Ultra"}}), "Google AI Ultra")

    def test_each_ultra_plan_has_its_own_price(self):
        import sources_config
        source = next(s for s in sources_config.BASE_SOURCES
                      if s.id == "antigravity")
        prices = {p.title: p.monthly_usd
                  for p in source.subscription_prices}
        self.assertEqual(prices["Google AI Ultra 5x"], 100)
        self.assertEqual(prices["Google AI Ultra 20x"], 200)
        self.assertNotIn("Google AI Ultra", prices)

    def test_fetch_uses_agy_oauth_token_when_desktop_closed_or_empty_buckets(self):
        calls = []

        def fake_request_json(url, **kwargs):
            calls.append(url)
            if "retrieveUserQuotaSummary" in url:
                return {
                    "groups": [{
                        "buckets": [
                            {"window": "weekly", "remainingFraction": 0.50},
                            {"window": "5h", "remainingFraction": 0.90},
                        ]
                    }]
                }
            if "loadCodeAssist" in url:
                return {"paidTier": {"id": "g1-ultra-lite-tier", "name": "Google AI Ultra"}}
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            cli_dir = Path(tmp) / "antigravity-cli"
            cli_dir.mkdir()
            (cli_dir / "antigravity-oauth-token").write_text(json.dumps({
                "token": {
                    "access_token": "ya29.agy-live",
                    "expiry": "2099-01-01T00:00:00Z",
                }
            }))
            with patch.object(cache_util, "CACHE_DIR", tmp):
                with patch.object(antigravity_usage, "HOME_DIRS", (str(cli_dir),)):
                    with patch.object(antigravity_usage, "_find_servers", return_value=[]):
                        with patch.object(
                                antigravity_usage.http_util, "request_json",
                                side_effect=fake_request_json):
                            out = antigravity_usage.fetch_quota(force=True)
        self.assertTrue(out["ok"])
        self.assertFalse(out["stale"])
        self.assertEqual(out["plan"], "Google AI Ultra 5x")
        self.assertEqual(out["week"]["pct"], 50.0)
        self.assertEqual(out["session"]["pct"], 10.0)
        # Must hit daily-cloudcode-pa first, not prod cloudcode-pa.
        self.assertTrue(
            calls[0].startswith("https://daily-cloudcode-pa.googleapis.com"))

    def test_backoff_wakes_immediately_when_daemon_or_token_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            desk_dir = Path(tmp) / "antigravity"
            daemon_dir = desk_dir / "daemon"
            daemon_dir.mkdir(parents=True)
            with patch.object(cache_util, "CACHE_DIR", tmp):
                with patch.object(antigravity_usage, "HOME_DIRS", (str(desk_dir),)):
                    with patch.object(antigravity_usage, "_read_state_db_auth", return_value=None):
                        with patch.object(antigravity_usage, "_local_install_present", return_value=True):
                            # 1. Fail while closed and simulate high failure backoff.
                            with patch.object(antigravity_usage, "_find_servers", return_value=[]):
                                out1 = antigravity_usage.fetch_quota(force=True)
                            self.assertFalse(out1["ok"])
                            antigravity_usage._cache["fail_streak"] = 10

                            # 2. Still closed -> failure backoff holds (no _find_servers call).
                            with patch.object(
                                    antigravity_usage, "_find_servers",
                                    side_effect=AssertionError("should stay in backoff")):
                                out2 = antigravity_usage.fetch_quota(force=False)
                            self.assertFalse(out2["ok"])

                            # 3. Desktop opens and writes daemon/hub.json -> wakes immediately!
                            (daemon_dir / "hub_1234.json").write_text(json.dumps({
                                "pid": 1234,
                                "httpsPort": 55555,
                                "csrfToken": "tok-new",
                            }))
                            good = {
                                "ok": True,
                                "plan": "Google AI Ultra 5x",
                                "error": None,
                                "week": {"pct": 12.0, "resets_in_s": 3600, "resets_in": "1h", "window_s": 604800},
                                "session": None,
                                "stale": False,
                            }
                            with patch.object(
                                    antigravity_usage, "_find_servers",
                                    return_value=[{"pid": 1234, "csrf_token": "tok-new", "https_port": 55555}]):
                                with patch.object(
                                        antigravity_usage, "_query_server",
                                        return_value=good):
                                    out3 = antigravity_usage.fetch_quota(force=False)
                            self.assertTrue(out3["ok"])
                            self.assertFalse(out3["stale"])
                            self.assertEqual(out3["week"]["pct"], 12.0)

    def test_decodes_go_keyring_base64_payload_in_token_file(self):
        payload = json.dumps({
            "token": {
                "access_token": "ya29.b64-file",
                "expiry": "2099-01-01T00:00:00.12345+01:00",
            },
        }).encode("utf-8")
        encoded = "go-keyring-base64:" + base64.b64encode(payload).decode("ascii")
        decoded = antigravity_usage._decode_oauth_blob(encoded)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["access_token"], "ya29.b64-file")

    def test_registry_and_providers_payload_wire_antigravity(self):
        self.assertIn("antigravity", detect_sources.PROBES)
        src = sources_config.get("antigravity")
        self.assertIsNotNone(src)
        self.assertEqual(src.title, "Antigravity")
        self.assertEqual(src.group, sources_config.GROUP_AI)
        self.assertEqual([p.id for p in src.pools], ["week", "session"])
        self.assertEqual(
            src.detail({"plan": "Google AI Pro", "session": {"pct": 22.0}}),
            "Google AI Pro · session 22%",
        )
        self.assertEqual(
            sources_config.headline_pct(
                "antigravity", {"session": {"pct": 22.0}}),
            22.0,
        )

        sources_config.reset_for_tests()
        self.addCleanup(sources_config.reset_for_tests)
        state = sources_config.blank_state()
        state["antigravity"] = {
            "ok": True,
            "plan": "Google AI Pro",
            "week": {"pct": 42.0, "resets_in_s": 86400},
            "session": {"pct": 15.0, "resets_in_s": 3600},
        }
        rows = headroom_server._providers_payload(state)
        by_id = {row["id"]: row for row in rows}
        self.assertIn("antigravity", by_id)
        row = by_id["antigravity"]
        self.assertEqual(row["title"], "Antigravity")
        self.assertEqual(row["pools"]["week"]["pct"], 42.0)
        self.assertEqual(row["pools"]["session"]["pct"], 15.0)


if __name__ == "__main__":
    unittest.main()
