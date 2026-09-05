"""Tests for ~/.headroom/config.json helpers."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import app_config


class AppConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config.json")
        self.patcher = mock.patch.object(app_config, "STORE_PATH", self.path)
        self.patcher.start()
        app_config.reload()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        app_config.reload()

    def test_defaults_without_file(self):
        self.assertEqual(app_config.timezone_name(), "UTC")
        self.assertTrue(app_config.dev_root().endswith("/Dev"))
        self.assertEqual(app_config.git_authors(), [])
        self.assertEqual(app_config.vercel_team_slugs(), ())
        self.assertEqual(app_config.github_org_prefixes(), ())
        self.assertEqual(app_config.github_always_repos(), ())
        self.assertEqual(app_config.github_max_discovered(), 6)
        self.assertEqual(app_config.plausible_sites(), ())
        self.assertEqual(app_config.plausible_host(), "https://plausible.io")
        self.assertEqual(app_config.plausible_range(), "24h")
        self.assertEqual(app_config.posthog_projects(), ())
        self.assertEqual(app_config.posthog_host(), "https://us.posthog.com")
        self.assertEqual(app_config.posthog_range(), "24h")
        self.assertEqual(
            app_config.mobile_permissions(),
            {"read", "refresh", "sources", "servers"},
        )
        self.assertFalse(app_config.agent_gateway_enabled())
        self.assertEqual(app_config.codex_binary(), "codex")
        self.assertTrue(app_config.agent_alerts())

    def test_overrides_from_file(self):
        with open(self.path, "w") as handle:
            json.dump({
                "timezone": "America/Los_Angeles",
                "dev_root": "~/Projects",
                "git_authors": ["Ada"],
                "vercel_team_slugs": ["acme"],
                "github_org_prefix": ["acme/", "Ada/", " ", "acme/"],
                "github_always_repos": ["acme/app"],
                "github_max_discovered": 2,
                "plausible_sites": ["acme.dev"],
                "plausible_host": "https://analytics.example.com/",
                "plausible_range": "7d",
                "posthog_projects": ["12345"],
                "posthog_host": "https://eu.posthog.com/",
                "posthog_range": "30d",
                "mobile_permissions": ["read", "sources", "agents", "unknown"],
                "agent_gateway_enabled": True,
                "codex_binary": "/opt/codex",
                "agent_alerts": False,
            }, handle)
        app_config.reload()
        self.assertEqual(app_config.timezone_name(), "America/Los_Angeles")
        self.assertTrue(app_config.dev_root().endswith("/Projects"))
        self.assertEqual(app_config.git_authors(), ["Ada"])
        self.assertEqual(app_config.vercel_team_slugs(), ("acme",))
        self.assertEqual(
            app_config.github_org_prefixes(), ("acme/", "ada/"))
        self.assertEqual(app_config.github_always_repos(), ("acme/app",))
        self.assertEqual(app_config.github_max_discovered(), 2)
        self.assertEqual(app_config.plausible_sites(), ("acme.dev",))
        self.assertEqual(
            app_config.plausible_host(), "https://analytics.example.com")
        self.assertEqual(app_config.plausible_range(), "7d")
        self.assertEqual(app_config.posthog_projects(), ("12345",))
        self.assertEqual(app_config.posthog_host(), "https://eu.posthog.com")
        self.assertEqual(app_config.posthog_range(), "30d")
        self.assertEqual(
            app_config.mobile_permissions(), {"read", "sources", "agents"})
        self.assertTrue(app_config.agent_gateway_enabled())
        self.assertEqual(app_config.codex_binary(), "/opt/codex")
        self.assertFalse(app_config.agent_alerts())

    def test_a_bare_org_prefix_string_still_works(self):
        """Configs written before the key took a list must keep working."""
        with open(self.path, "w") as handle:
            json.dump({"github_org_prefix": "acme/"}, handle)
        app_config.reload()
        self.assertEqual(app_config.github_org_prefixes(), ("acme/",))

    def test_persists_github_watch_without_losing_other_config(self):
        with open(self.path, "w") as handle:
            json.dump({"timezone": "Europe/Amsterdam"}, handle)
        result = app_config.set_github_watch(
            prefixes=["Acme/", " ", "acme/"],
            always_repos=["acme/api", "acme/api", "ada/site"],
            max_discovered=999,
        )
        self.assertEqual(result["owners"], ["acme/"])
        self.assertEqual(result["always_repos"], ["acme/api", "ada/site"])
        self.assertEqual(
            result["max_discovered"], app_config.GITHUB_MAX_DISCOVERED_LIMIT)
        self.assertEqual(app_config.github_org_prefixes(), ("acme/",))
        self.assertEqual(app_config.timezone_name(), "Europe/Amsterdam")

    def test_a_repo_that_is_not_owner_slash_name_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            app_config.set_github_watch(always_repos=["acme"])
        self.assertIn("acme", str(caught.exception))
        # Nothing written: the whole save fails rather than half-applying.
        self.assertEqual(app_config.github_always_repos(), ())

    def test_omitted_keys_are_left_alone(self):
        app_config.set_github_watch(prefixes=["acme/"], max_discovered=3)
        app_config.set_github_watch(always_repos=["acme/api"])
        self.assertEqual(app_config.github_org_prefixes(), ("acme/",))
        self.assertEqual(app_config.github_max_discovered(), 3)

    def test_clearing_a_list_is_not_the_same_as_omitting_it(self):
        app_config.set_github_watch(prefixes=["acme/"])
        app_config.set_github_watch(prefixes=[])
        self.assertEqual(app_config.github_org_prefixes(), ())

    def test_git_config_stores_the_root_unexpanded(self):
        # Settings shows this field back to whoever typed it. Expanding on the
        # way in makes an edit of "~/Dev" look like the save rewrote it.
        result = app_config.set_git_config(root="~/", authors=["Ada", " ", "Ada"])
        self.assertEqual(result["dev_root"], "~/")
        self.assertEqual(result["dev_root_path"], os.path.expanduser("~/"))
        self.assertEqual(result["authors"], ["Ada"])
        self.assertEqual(app_config.dev_root_setting(), "~/")

    def test_a_dev_root_that_is_not_there_is_refused(self):
        app_config.set_git_config(root="~/")
        with self.assertRaises(ValueError) as caught:
            app_config.set_git_config(root="/nope/not/a/folder")
        self.assertIn("/nope/not/a/folder", str(caught.exception))
        # The previous root survives — a rejected save must not strand the
        # scanner on a path nobody chose.
        self.assertEqual(app_config.dev_root_setting(), "~/")

    def test_an_empty_dev_root_is_refused(self):
        with self.assertRaises(ValueError):
            app_config.set_git_config(root="   ")

    def test_git_config_omitted_keys_are_left_alone(self):
        app_config.set_git_config(root="~/", authors=["Ada"])
        app_config.set_git_config(authors=["Grace"])
        self.assertEqual(app_config.dev_root_setting(), "~/")
        self.assertEqual(app_config.git_authors(), ["Grace"])

    def test_vercel_teams_are_lowercased_and_deduped(self):
        result = app_config.set_vercel_teams(slugs=["Acme", "acme", " ", "Ada"])
        self.assertEqual(result["teams"], ["acme", "ada"])
        self.assertEqual(app_config.vercel_team_slugs(), ("acme", "ada"))

    def test_clearing_vercel_teams_reads_every_team(self):
        app_config.set_vercel_teams(slugs=["acme"])
        app_config.set_vercel_teams(slugs=[])
        self.assertEqual(app_config.vercel_team_slugs(), ())

    def test_git_and_vercel_writes_leave_other_config(self):
        with open(self.path, "w") as handle:
            json.dump({"timezone": "Europe/Amsterdam"}, handle)
        app_config.set_git_config(root="~/", authors=["Ada"])
        app_config.set_vercel_teams(slugs=["acme"])
        self.assertEqual(app_config.timezone_name(), "Europe/Amsterdam")
        self.assertEqual(app_config.git_authors(), ["Ada"])

    def test_dev_root_stays_out_of_the_multi_mac_merge(self):
        # A path describing one machine's disk must never follow a settings
        # sync to a Mac where it does not exist.
        self.assertNotIn("dev_root", app_config.SHARED_CONFIG_KEYS)
        self.assertIn("git_authors", app_config.SHARED_CONFIG_KEYS)
        self.assertIn("vercel_team_slugs", app_config.SHARED_CONFIG_KEYS)
        self.assertIn("posthog_projects", app_config.SHARED_CONFIG_KEYS)
        self.assertIn("posthog_host", app_config.SHARED_CONFIG_KEYS)
        self.assertIn("posthog_range", app_config.SHARED_CONFIG_KEYS)

    def test_persists_mobile_permissions_without_losing_other_config(self):
        with open(self.path, "w") as handle:
            json.dump({"timezone": "Europe/Amsterdam"}, handle)
        granted = app_config.set_mobile_permissions(["read", "refresh", "bad"])
        self.assertEqual(granted, {"read", "refresh"})
        self.assertEqual(app_config.mobile_permissions(), {"read", "refresh"})
        self.assertEqual(app_config.timezone_name(), "Europe/Amsterdam")

    def test_persists_agent_gateway_configuration(self):
        result = app_config.set_agent_gateway(
            enabled=True, codex_binary_value="~/bin/codex")
        self.assertTrue(result["enabled"])
        self.assertTrue(result["codex_binary"].endswith("/bin/codex"))
        self.assertTrue(app_config.agent_gateway_enabled())

    def test_persists_agent_alerts(self):
        self.assertFalse(app_config.set_agent_alerts(False))
        self.assertFalse(app_config.agent_alerts())
        with self.assertRaises(ValueError):
            app_config.set_agent_alerts("off")

    def test_persists_attention_ack_without_losing_other_config(self):
        with open(self.path, "w") as handle:
            json.dump({"timezone": "Europe/Amsterdam"}, handle)
        value = app_config.set_attention_ack_fingerprint("warn|vercel|1 failed")
        self.assertEqual(value, "warn|vercel|1 failed")
        self.assertEqual(
            app_config.attention_ack_fingerprint(),
            "warn|vercel|1 failed",
        )
        self.assertEqual(app_config.timezone_name(), "Europe/Amsterdam")


class AttentionTests(unittest.TestCase):
    def test_critical_on_actions_fail(self):
        import headroom_server as hs
        import time
        now = time.time()
        doc = {
            "github": {
                "configured": True,
                "fail_count": 2,
                "runs": [
                    {
                        "status": "failure",
                        "repo": "acme/app",
                        "name": "CI",
                        "created_at": now - 60,
                    },
                    {
                        "status": "failure",
                        "repo": "acme/api",
                        "name": "PR Check",
                        "created_at": now - 120,
                        "sha": "abc",
                    },
                ],
            },
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "codex": {"ok": True},
            "cursor": {"ok": True},
            "sources": [],
        }
        attention = hs._build_attention(doc)
        self.assertEqual(attention["level"], "critical")
        self.assertGreater(attention["score"], 0)
        self.assertTrue(attention["reasons"])
        self.assertIn("GitHub Actions failures", attention["summary"])
        self.assertIn("app", attention["summary"])

    def test_inbox_on_watched_repos_warns(self):
        import headroom_server as hs
        attention = hs._build_attention({
            "github": {
                "configured": True,
                "fail_count": 0,
                "inbox_count": 1,
                "inbox": [{
                    "reason": "review_request",
                    "repo": "acme/web",
                    "title": "Tighten glyph",
                }],
                "runs": [],
            },
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "codex": {"ok": True},
            "cursor": {"ok": True},
            "sources": [],
        })
        self.assertEqual(attention["level"], "warn")
        self.assertEqual(attention["reasons"][0]["kind"], "github-inbox")
        self.assertIn("review requested", attention["summary"])

    def test_names_single_fresh_failure(self):
        import headroom_server as hs
        import time
        attention = hs._build_attention({
            "github": {
                "configured": True,
                "fail_count": 1,
                "runs": [{
                    "status": "failure",
                    "repo": "envisioning/app",
                    "name": "PR Check",
                    "created_at": time.time() - 600,
                }],
            },
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "codex": {"ok": True},
            "cursor": {"ok": True},
            "sources": [],
        })
        self.assertEqual(attention["level"], "critical")
        self.assertEqual(attention["summary"], "app · PR Check failed")

    def test_acknowledgement_clears_only_matching_attention(self):
        import headroom_server as hs
        doc = {
            "github": {"configured": True, "fail_count": 0},
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": [{"status": "error"}]},
            "codex": {"ok": True},
            "cursor": {"ok": True},
            "sources": [],
        }
        with mock.patch(
            "headroom_server.app_config.attention_ack_fingerprint",
            return_value=None,
        ):
            active = hs._build_attention(doc)
        self.assertEqual(active["level"], "warn")
        self.assertFalse(active["acknowledged"])

        with mock.patch(
            "headroom_server.app_config.attention_ack_fingerprint",
            return_value=active["fingerprint"],
        ):
            cleared = hs._build_attention(doc)
        self.assertEqual(cleared["level"], "ok")
        self.assertEqual(cleared["reasons"], [])
        self.assertTrue(cleared["acknowledged"])

        doc["vercel"]["deployments"].append({"status": "error"})
        with mock.patch(
            "headroom_server.app_config.attention_ack_fingerprint",
            return_value=active["fingerprint"],
        ):
            changed = hs._build_attention(doc)
        self.assertEqual(changed["level"], "critical")
        self.assertFalse(changed["acknowledged"])

    def test_stale_fail_count_zero_is_ok(self):
        """Age-gating happens in github_actions; Attention trusts fail_count."""
        import headroom_server as hs
        import time
        attention = hs._build_attention({
            "github": {
                "configured": True,
                "fail_count": 0,
                "runs": [{
                    "status": "failure",
                    "repo": "envisioning/signals",
                    "name": "CI",
                    "created_at": time.time() - 100 * 86400,
                }],
            },
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "codex": {"ok": True},
            "cursor": {"ok": True},
            "sources": [],
        })
        self.assertEqual(attention["level"], "ok")
        self.assertEqual(attention["reasons"], [])

    def test_ok_when_clear(self):
        import headroom_server as hs
        attention = hs._build_attention({
            "github": {"configured": True, "fail_count": 0},
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": [{"status": "ready"}]},
            "session_pct": 10,
            "week_pct": 20,
            "codex": {"ok": True, "week_pct": 30},
            "cursor": {"ok": True, "total_pct": 4},
            "sources": [{"enabled": True, "ok": True}],
        })
        self.assertEqual(attention["level"], "ok")
        self.assertEqual(attention["score"], 0)

    def test_drained_quota_does_not_nag(self):
        import headroom_server as hs
        attention = hs._build_attention({
            "github": {"configured": True, "fail_count": 0},
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "session_pct": 98,
            "week_pct": 90,
            "codex": {"ok": True, "session_pct": 100, "week_pct": 100},
            "cursor": {"ok": True, "total_pct": 99},
            "sources": [],
        })
        self.assertEqual(attention["level"], "ok")
        self.assertEqual(attention["reasons"], [])

    def test_provider_timeout_does_not_nag(self):
        import headroom_server as hs
        attention = hs._build_attention({
            "github": {"configured": True, "fail_count": 0},
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "codex": {"ok": False},
            "cursor": {"ok": False},
            "sources": [
                {
                    "id": "claude",
                    "title": "Claude",
                    "enabled": True,
                    "ok": False,
                    "configured": True,
                    "error": "HTTP Error 429: Too Many Requests",
                },
            ],
        })
        self.assertEqual(attention["level"], "ok")
        self.assertEqual(attention["reasons"], [])


class SpendParseTests(unittest.TestCase):
    @mock.patch("codex_usage.time.time", return_value=1785500000)
    def test_codex_reset_credit_keeps_exact_expiry(self, _time):
        import codex_usage
        import headroom_server

        parsed = codex_usage.parse_usage(
            {"plan_type": "team", "rate_limit": {}},
            credits_body={
                "available_count": 1,
                "credits": [{
                    "status": "available",
                    "expires_at": "2026-08-01T12:00:00Z",
                }],
            },
        )
        credit = parsed["reset_credits"]["credits"][0]
        self.assertEqual(credit["expires_at_s"], 1785585600)

        flattened = headroom_server._flatten_codex(parsed)
        self.assertEqual(
            flattened["reset_credits_expire_at"],
            [1785585600],
        )

    def test_codex_spend_control(self):
        import codex_usage
        parsed = codex_usage.parse_usage({
            "plan_type": "team",
            "rate_limit": {},
            "spend_control": {
                "reached": True,
                "individual_limit": {
                    "limit": "500",
                    "used": "120.5",
                    "remaining": "0",
                    "used_percent": 24,
                    "source": "workspace_spend_controls",
                },
            },
        })
        self.assertEqual(parsed["spend"]["used_usd"], 120.5)
        self.assertEqual(parsed["spend"]["limit_usd"], 500.0)
        self.assertFalse(parsed["spend"]["reached"])
        self.assertEqual(parsed["spend"]["label"], "$120 / $500")

    def test_codex_spend_ignores_cents_scale_and_sticky_reached(self):
        import codex_usage
        parsed = codex_usage.parse_usage({
            "plan_type": "team",
            "rate_limit": {},
            "spend_control": {
                "reached": True,
                "individual_limit": {
                    "limit": "500",
                    "used": "46204.09",
                    "remaining": "0",
                    "used_percent": 0,
                    "source": "workspace_spend_controls",
                },
            },
        })
        self.assertEqual(parsed["spend"]["used_usd"], 462.04)
        self.assertEqual(parsed["spend"]["limit_usd"], 500.0)
        self.assertFalse(parsed["spend"]["reached"])
        self.assertEqual(parsed["spend"]["label"], "$462 / $500")

    def test_cursor_plan_spend(self):
        import cursor_usage
        parsed = cursor_usage.parse_usage({
            "planUsage": {
                "totalSpend": 1515,
                "includedSpend": 1515,
                "remaining": 485,
                "limit": 2000,
                "autoPercentUsed": 0,
                "apiPercentUsed": 10,
            },
            "spendLimitUsage": {
                "individualLimit": 3000,
                "individualRemaining": 2500,
            },
            "billingCycleStart": 0,
            "billingCycleEnd": 2_592_000_000,
        })
        self.assertEqual(parsed["spend"]["used_usd"], 15.15)
        self.assertEqual(parsed["spend"]["limit_usd"], 20.0)
        self.assertEqual(parsed["on_demand"]["used_usd"], 5.0)


class TimezoneSettingTests(unittest.TestCase):
    """The zone every day boundary is drawn in.

    It defaults to UTC and drives `ZoneInfo(...)` on the request path, so a
    name that does not resolve has to be refused where it is typed rather
    than raised once per document afterwards.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config.json")
        self.patcher = mock.patch.object(app_config, "STORE_PATH", self.path)
        self.patcher.start()
        app_config.reload()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        app_config.reload()

    def test_round_trips_a_real_zone(self):
        self.assertEqual(
            app_config.set_timezone("America/Los_Angeles"),
            "America/Los_Angeles")
        self.assertEqual(app_config.timezone_name(), "America/Los_Angeles")

    def test_rejects_a_zone_the_tz_database_cannot_resolve(self):
        app_config.set_timezone("Europe/Berlin")
        with self.assertRaises(ValueError):
            app_config.set_timezone("Mars/Olympus_Mons")
        # The bad write must not have disturbed the good one.
        self.assertEqual(app_config.timezone_name(), "Europe/Berlin")

    def test_rejects_blank(self):
        with self.assertRaises(ValueError):
            app_config.set_timezone("   ")

    def test_follows_you_between_macs(self):
        # One person has one notion of "today"; burndown history merges
        # across Macs, so two disagreeing day boundaries would thin one
        # curve against another's buckets.
        self.assertIn("timezone", app_config.SHARED_CONFIG_KEYS)


class PlausibleHostSettingTests(unittest.TestCase):
    """Parity with `posthog_host` — the same shape of value.

    `plausible_host` was readable and synced from the start but had no
    setter, so a self-hosted instance could only be reached by hand editing
    config.json while PostHog had a picker for the identical decision.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config.json")
        self.patcher = mock.patch.object(app_config, "STORE_PATH", self.path)
        self.patcher.start()
        app_config.reload()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        app_config.reload()

    def test_round_trips_and_trims_a_trailing_slash(self):
        self.assertEqual(
            app_config.set_plausible_host("https://stats.example.com/"),
            "https://stats.example.com")
        self.assertEqual(
            app_config.plausible_host(), "https://stats.example.com")

    def test_assumes_https_when_no_scheme_is_given(self):
        self.assertEqual(
            app_config.set_plausible_host("stats.example.com"),
            "https://stats.example.com")

    def test_rejects_blank(self):
        with self.assertRaises(ValueError):
            app_config.set_plausible_host("")

    def test_refuses_a_scheme_that_is_not_http(self):
        # These hosts are where a Keychain key gets sent as a bearer token,
        # and `file://` / `gopher://` contain "://" so a mere presence test
        # lets them reach urlopen.
        for value in ("file:///etc/passwd", "gopher://evil.tld",
                      "ftp://evil.tld"):
            with self.assertRaises(ValueError, msg=value):
                app_config.set_plausible_host(value)
            with self.assertRaises(ValueError, msg=value):
                app_config.set_posthog_host(value)


class SharedConfigValidationTests(unittest.TestCase):
    """A peer's record is untrusted input, not a shortcut past the setters.

    `plausible_host`, `posthog_host` and `axiom_host` are synced, and each is
    the destination a provider key is sent to. The folder transport may be
    Dropbox or Syncthing, which can have other participants.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config.json")
        self.patcher = mock.patch.object(app_config, "STORE_PATH", self.path)
        self.patcher.start()
        app_config.reload()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        app_config.reload()

    def test_a_synced_api_host_goes_through_the_same_validator(self):
        with self.assertRaises(ValueError):
            app_config.set_shared_config({"posthog_host": "file:///etc/passwd"})
        self.assertEqual(
            app_config.posthog_host(), "https://us.posthog.com")

    def test_a_synced_host_without_a_scheme_is_normalised_not_trusted(self):
        app_config.set_shared_config({"plausible_host": "stats.example.com"})
        self.assertEqual(
            app_config.plausible_host(), "https://stats.example.com")

    def test_keys_outside_the_whitelist_are_still_ignored(self):
        app_config.set_shared_config({"auth_token": "nope", "dev_root": "/tmp"})
        self.assertIsNone(app_config.get("auth_token"))

    def test_setting_the_host_leaves_the_site_list_alone(self):
        app_config.set_plausible_sites(sites=["a.example", "b.example"])
        app_config.set_plausible_host("https://stats.example.com")
        self.assertEqual(
            app_config.plausible_sites(), ("a.example", "b.example"))


if __name__ == "__main__":
    unittest.main()


class DeskDisplayConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config.json")
        self.patcher = mock.patch.object(app_config, "STORE_PATH", self.path)
        self.patcher.start()
        app_config.reload()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        app_config.reload()

    def test_defaults(self):
        settings = app_config.display_settings(now=0)
        self.assertEqual(settings["brightness_pct"], 75)
        self.assertFalse(settings["dim_at_night"])
        self.assertFalse(settings["dimmed_now"])
        self.assertTrue(settings["celebrate_resets"])
        self.assertTrue(settings["boot_splash"])
        self.assertEqual(settings["pages"],
                         {"vercel": True, "git": True, "local": True})
        projection = app_config.display_projection(now=0)
        self.assertEqual(projection["brightness"], 191)
        self.assertEqual(projection["pages"], settings["pages"])

    def test_brightness_is_one_of_the_offered_steps(self):
        app_config.set_display(brightness_pct=25)
        self.assertEqual(app_config.display_projection()["brightness"], 64)
        app_config.set_display(brightness_pct="100")
        self.assertEqual(app_config.display_projection()["brightness"], 255)
        with self.assertRaises(ValueError):
            app_config.set_display(brightness_pct=60)
        # A hand-edited file with a value off the scale reads as the default.
        with open(self.path, "w") as handle:
            json.dump({"display_brightness_pct": 12}, handle)
        app_config.reload()
        self.assertEqual(app_config.display_brightness_pct(), 75)

    def test_dimming_fades_over_thirty_minutes_in_the_configured_zone(self):
        app_config.set_timezone("Europe/Berlin")
        app_config.set_display(dim_at_night=True)
        # 2026-01-15, Berlin is UTC+1. 22:00 local = 21:00 UTC.
        start = 1768510800
        minute = 60
        self.assertEqual(app_config.display_effective_brightness_pct(now=start - minute), 75)
        # Fifteen minutes in: halfway between 75 and 10.
        self.assertEqual(app_config.display_effective_brightness_pct(now=start + 15 * minute), 42)
        # Past the ramp: fully dimmed, and dimmed through the night.
        self.assertEqual(app_config.display_effective_brightness_pct(now=start + 31 * minute), 10)
        self.assertEqual(app_config.display_projection(now=start + 3 * 3600)["brightness"], 26)
        # 07:00 local starts the fade back; 07:15 is halfway; 07:30 is done.
        end = start + 9 * 3600
        self.assertEqual(app_config.display_effective_brightness_pct(now=end), 10)
        self.assertEqual(app_config.display_effective_brightness_pct(now=end + 15 * minute), 42)
        self.assertEqual(app_config.display_effective_brightness_pct(now=end + 30 * minute), 75)
        self.assertFalse(app_config.display_dimmed_now(now=end + 30 * minute))
        self.assertTrue(app_config.display_settings(now=start + 3600)["dimmed_now"])
        self.assertEqual(app_config.display_settings(now=start + 3600)["brightness_now_pct"], 10)
        app_config.set_display(dim_at_night=False)
        self.assertEqual(app_config.display_projection(now=start + 3600)["brightness"], 191)

    def test_dim_window_hours_are_configurable_and_may_cross_midnight(self):
        app_config.set_timezone("UTC")
        app_config.set_display(dim_at_night=True, dim_start_hour=9, dim_end_hour=17)
        settings = app_config.display_settings()
        self.assertEqual((settings["dim_start_hour"], settings["dim_end_hour"]), (9, 17))
        noon = 1768478400   # 2026-01-15 12:00 UTC
        self.assertEqual(app_config.display_effective_brightness_pct(now=noon), 10)
        self.assertEqual(app_config.display_effective_brightness_pct(now=noon + 8 * 3600), 75)
        # Defaults cross midnight: 22 → 7.
        app_config.set_display(dim_start_hour=22, dim_end_hour=7)
        self.assertEqual(app_config.display_effective_brightness_pct(now=noon), 75)
        self.assertEqual(app_config.display_effective_brightness_pct(now=noon + 14 * 3600), 10)
        # Equal hours: no window.
        app_config.set_display(dim_start_hour=9, dim_end_hour=9)
        self.assertEqual(app_config.display_effective_brightness_pct(now=noon), 75)
        for bad in (24, -1, "nine", True):
            with self.assertRaises(ValueError):
                app_config.set_display(dim_start_hour=bad)

    def test_pages_store_only_the_hidden_ones(self):
        app_config.set_display(pages={"git": False})
        self.assertEqual(app_config.display_pages(),
                         {"vercel": True, "git": False, "local": True})
        with open(self.path) as handle:
            self.assertEqual(json.load(handle)["display_pages"], {"git": False})
        app_config.set_display(pages={"git": True, "local": False})
        self.assertEqual(app_config.display_pages(),
                         {"vercel": True, "git": True, "local": False})
        with self.assertRaises(ValueError):
            app_config.set_display(pages={"slot0": False})
        with self.assertRaises(ValueError):
            app_config.set_display(pages={"git": "no"})

    def test_toggles_must_be_booleans_and_omitted_keys_stay(self):
        app_config.set_display(celebrate_resets=False)
        app_config.set_display(boot_splash=False)
        self.assertFalse(app_config.display_celebrate_resets())
        self.assertFalse(app_config.display_boot_splash())
        with self.assertRaises(ValueError):
            app_config.set_display(celebrate_resets="yes")
        self.assertFalse(app_config.display_celebrate_resets())

    def test_display_keys_stay_local_to_this_mac(self):
        for key in app_config.DEFAULTS:
            if key.startswith("display_"):
                self.assertNotIn(key, app_config.SHARED_CONFIG_KEYS)
