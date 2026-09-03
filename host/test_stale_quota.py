#!/usr/bin/env python3
"""A stale quota reading must not be able to pass for a live one.

The bug these cover shipped as three separate half-truths that only added up
to a wrong answer together: a credential store that had lost its plan token
ended the search instead of continuing it, so the fetch could never recover on
its own; the retry clock stood in for the reading's age, so fifteen hours of
failure reported as one poll; and the countdown kept being derived from `now`
against a percentage that had stopped moving, so a dead window ticked to zero
in front of you. Percentages that hold at last-known are the design. A
countdown that keeps running is the lie.
"""
import contextlib
import json
import os
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

import accounts
import ai_gateway_usage
import axiom_monitors
import burndown
import cache_util
import datadog_monitors
import github_actions
import headroom_server
import keychain
import oauth_usage
import openrouter_usage
import plausible_usage
import posthog_usage
import quota_samples
import sentry_alerts
import sources_config
import supabase_usage

NOW = 1_800_000_000.0
WEEK_S = 7 * 24 * 3600


def quota_payload(**over):
    payload = {
        "ok": True,
        "plan": "Max 5x",
        "session": {"pct": 42.0, "resets_in_s": 2130, "window_s": 5 * 3600},
        "week": {"pct": 35.0, "resets_in_s": 3 * 24 * 3600,
                 "window_s": WEEK_S},
        "stale": False,
        "error": None,
        "fetched_at": NOW,
    }
    payload.update(over)
    return payload


class CredentialSearchTests(unittest.TestCase):
    """The store that ends the search has to be the one holding a token."""

    MCP_ONLY = {"mcpOAuth": {"plugin:x|abc": {"accessToken": "mcp-token"}}}
    GOOD = {"claudeAiOauth": {"accessToken": "plan-token",
                              "refreshToken": "r"}}

    def setUp(self):
        oauth_usage.reset_for_tests()
        self._oauth_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._oauth_home.cleanup)
        self._oauth_dir = self._oauth_home.name
        self._oauth_patch = patch.object(
            oauth_usage, "OAUTH_DIR", self._oauth_dir)
        self._oauth_patch.start()
        self.addCleanup(self._oauth_patch.stop)

    @staticmethod
    def _account(root="/Users/example/.claudewho-work"):
        return accounts.Account(
            provider="claude",
            slug="work",
            label="Work",
            root=root,
            raw_root=root,
        )

    def test_profile_keychain_service_hashes_the_config_directory(self):
        service = oauth_usage._keychain_service(self._account())
        self.assertEqual(service, "Claude Code-credentials-abfbd7ee")

    def test_profile_imports_hashed_keychain_into_headroom_store(self):
        account = self._account()
        service = oauth_usage._keychain_service(account)

        def keychain_blob(wanted):
            return self.GOOD if wanted == service else None

        with patch.object(oauth_usage, "_read_keychain_blob",
                          side_effect=keychain_blob):
            store, blob = oauth_usage._read_creds_blob(account)
        self.assertEqual(store, "headroom:claude:work")
        self.assertEqual(oauth_usage._oauth_block(blob)["accessToken"],
                         "plan-token")
        owned = oauth_usage._headroom_path(account)
        self.assertTrue(os.path.isfile(owned))
        with open(owned) as handle:
            self.assertEqual(json.load(handle)["claudeAiOauth"]["accessToken"],
                             "plan-token")

    def test_keychain_without_plan_token_falls_through_to_file(self):
        # The real shape that broke it: Claude Code keeps per-MCP-server OAuth
        # in the same Keychain item, and a blob left holding only `mcpOAuth`
        # used to end the search there and make the file unreachable.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".credentials.json")
            with open(path, "w") as handle:
                json.dump(self.GOOD, handle)
            with patch.object(oauth_usage, "_read_keychain_blob",
                              return_value=self.MCP_ONLY), \
                 patch.object(oauth_usage, "CREDS_FILE", path):
                store, blob = oauth_usage._read_creds_blob()
        self.assertEqual(store, "headroom:claude")
        self.assertEqual(oauth_usage._oauth_block(blob)["accessToken"],
                         "plan-token")

    def test_legacy_keychain_imports_as_default_login_fallback(self):
        hashed = oauth_usage._keychain_service()

        def keychain_blob(service):
            if service == hashed:
                return None
            if service == oauth_usage.KEYCHAIN_SERVICE:
                return self.GOOD
            return None

        with patch.object(oauth_usage, "_read_keychain_blob",
                          side_effect=keychain_blob), \
             patch.object(oauth_usage, "CREDS_FILE", "/nonexistent/creds"):
            store, blob = oauth_usage._read_creds_blob()
        self.assertEqual(store, "headroom:claude")
        self.assertTrue(oauth_usage._oauth_block(blob))

    def test_hashed_keychain_wins_for_the_default_login(self):
        hashed = oauth_usage._keychain_service()

        def keychain_blob(service):
            return self.GOOD if service == hashed else None

        with patch.object(oauth_usage, "_read_keychain_blob",
                          side_effect=keychain_blob), \
             patch.object(oauth_usage, "CREDS_FILE", "/nonexistent/creds"):
            store, blob = oauth_usage._read_creds_blob()
        self.assertEqual(store, "headroom:claude")
        self.assertTrue(oauth_usage._oauth_block(blob))

    def test_headroom_store_skips_foreign_keychain(self):
        path = oauth_usage._headroom_path()
        with open(path, "w") as handle:
            json.dump(self.GOOD, handle)
        with patch.object(oauth_usage, "_read_keychain_blob") as read_kc:
            store, blob = oauth_usage._read_creds_blob()
        read_kc.assert_not_called()
        self.assertEqual(store, "headroom:claude")
        self.assertEqual(oauth_usage._oauth_block(blob)["accessToken"],
                         "plan-token")

    def test_token_nowhere_still_reports_against_a_real_store(self):
        # Nothing to find, but the error has to name a place that exists or it
        # reads as "Headroom lost your credentials".

        def keychain_blob(service):
            if service == oauth_usage.KEYCHAIN_SERVICE:
                return self.MCP_ONLY
            return None

        with patch.object(oauth_usage, "_read_keychain_blob",
                          side_effect=keychain_blob), \
             patch.object(oauth_usage, "CREDS_FILE", "/nonexistent/creds"):
            store, blob = oauth_usage._read_creds_blob()
        self.assertEqual(store, "keychain")
        self.assertIsNone(oauth_usage._oauth_block(blob))

    def test_no_store_at_all_is_none(self):
        with patch.object(oauth_usage, "_read_keychain_blob",
                          return_value=None), \
             patch.object(oauth_usage, "CREDS_FILE", "/nonexistent/creds"):
            self.assertEqual(oauth_usage._read_creds_blob(), (None, None))

    def test_refresh_writes_only_to_headroom_store(self):
        account = self._account()
        foreign = f"keychain:{oauth_usage._keychain_service(account)}"
        with patch.object(oauth_usage.keychain, "set_generic_password") as put:
            store = oauth_usage._write_creds_blob(foreign, self.GOOD, account)
        put.assert_not_called()
        self.assertEqual(store, "headroom:claude:work")
        with open(oauth_usage._headroom_path(account)) as handle:
            self.assertEqual(
                json.load(handle)["claudeAiOauth"]["accessToken"],
                "plan-token")

    def test_keychain_deny_is_sticky_until_rearm(self):
        service = oauth_usage._keychain_service()

        def denied(wanted):
            raise oauth_usage.KeychainRefused(
                wanted, keychain.ERR_SEC_USER_CANCELED)

        with patch.object(oauth_usage, "_read_keychain_blob",
                          side_effect=denied), \
             patch.object(oauth_usage, "CREDS_FILE", "/nonexistent/creds"):
            with self.assertRaises(oauth_usage.KeychainRefused):
                oauth_usage._read_creds_blob()

        oauth_usage._mark_keychain_denied(
            service, keychain.ERR_SEC_USER_CANCELED)
        with self.assertRaises(oauth_usage.KeychainRefused) as caught:
            oauth_usage._read_keychain_blob(service)
        self.assertTrue(caught.exception.sticky)

        oauth_usage.rearm_keychain()
        self.assertFalse(oauth_usage._is_keychain_denied(service))

    def test_credentials_present_never_prompts(self):
        """Presence reads the shape fail-closed; the blob path is untouched.

        `_read_keychain_blob` is the path that can pop SecurityAgent and the
        path a sticky Deny guards. Detection goes straight to Keychain with
        `allow_ui=False` instead, so a Keychain-stored Claude Code login is
        found without a prompt and a gated one cannot raise it.
        """
        with patch.object(oauth_usage, "_read_keychain_blob") as read_kc, \
             patch.object(keychain, "get_generic_password",
                          return_value=(keychain.ERR_SEC_ITEM_NOT_FOUND,
                                        None)) as read, \
             patch.object(oauth_usage, "CREDS_FILE", "/nonexistent/creds"):
            self.assertFalse(oauth_usage.credentials_present())
        read_kc.assert_not_called()
        self.assertFalse(read.call_args_list[0].kwargs.get("allow_ui", True))

    def test_oauth_mem_skips_reread_until_expiry(self):
        path = oauth_usage._headroom_path()
        blob = {
            "claudeAiOauth": {
                "accessToken": "plan-token",
                "refreshToken": "r",
                "expiresAt": int((time.time() + 3600) * 1000),
            }
        }
        with open(path, "w") as handle:
            json.dump(blob, handle)
        store, loaded, oauth = oauth_usage._load_oauth()
        self.assertEqual(store, "headroom:claude")
        self.assertEqual(oauth["accessToken"], "plan-token")
        with patch.object(oauth_usage, "_read_creds_blob") as read_creds:
            store2, loaded2, oauth2 = oauth_usage._load_oauth()
        read_creds.assert_not_called()
        self.assertEqual(oauth2["accessToken"], "plan-token")
        self.assertIs(loaded2, loaded)


class StaleAgeTests(unittest.TestCase):
    """`fetched_at` records when the numbers were true, not when we last tried."""

    def test_store_stamps_fetched_at(self):
        cache = {"t": 0.0, "data": None, "err": None}
        data = cache_util.store(cache, NOW, {"ok": True})
        self.assertEqual(data["fetched_at"], NOW)

    def test_keep_stale_does_not_refresh_the_stamp(self):
        cache = {"t": 0.0, "data": None, "err": None}
        cache_util.store(cache, NOW, {"ok": True, "pct": 42})
        # Four hours of failing polls, an hour apart.
        for hour in range(1, 5):
            out = cache_util.keep_stale(
                cache, NOW + hour * 3600, "boom", {"ok": False})
        self.assertTrue(out["stale"])
        self.assertEqual(out["fetched_at"], NOW)
        self.assertEqual(cache_util.age_s(out, NOW + 4 * 3600), 4 * 3600)

    def test_fresh_payload_is_trusted(self):
        self.assertTrue(cache_util.trusted(quota_payload(), NOW))

    def test_briefly_stale_is_still_trusted(self):
        # A single missed poll is a blip; the numbers are seconds old and
        # everything derived from them still holds.
        payload = quota_payload(stale=True)
        self.assertTrue(cache_util.trusted(payload, NOW + 60))

    def test_long_stale_is_not_trusted(self):
        payload = quota_payload(stale=True)
        self.assertFalse(
            cache_util.trusted(payload, NOW + cache_util.TRUSTED_STALE_S + 1))

    def test_payload_without_a_stamp_is_taken_at_face_value(self):
        # An old snapshot must not make a working source look broken on the
        # first poll after an upgrade.
        payload = quota_payload(stale=True)
        del payload["fetched_at"]
        self.assertTrue(cache_util.trusted(payload, NOW + 10 * 3600))

    def test_disk_snapshot_without_a_stamp_ages_from_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache_util, "CACHE_DIR", tmp):
                path = os.path.join(tmp, "claude.json")
                with open(path, "w") as handle:
                    json.dump({"ok": True, "plan": "Max 5x"}, handle)
                os.utime(path, (NOW, NOW))
                data = cache_util.load_disk("claude")
        self.assertEqual(data["fetched_at"], NOW)
        self.assertEqual(cache_util.age_s(data, NOW + 900), 900)


class StaleDerivationTests(unittest.TestCase):
    """Nothing measured against `now` may be computed off a frozen reading."""

    def test_countdown_is_suppressed_when_untrusted(self):
        held = {"claude": {"session": {"resets_in_s": 1200}}}
        self.assertEqual(
            headroom_server._held_resets(held, "claude", "session", 999), 1200)
        self.assertIsNone(
            headroom_server._held_resets(
                held, "claude", "session", 999, trusted=False))

    def test_stale_provider_keeps_percentages_but_loses_the_countdown(self):
        stale = quota_payload(stale=True, error="no plan token")
        state = {"claude": stale}
        old = NOW + 15 * 3600
        with patch.object(time, "time", return_value=old):
            rows = headroom_server._providers_payload(state, burndowns={})
        claude = next(row for row in rows if row["id"] == "claude")
        self.assertTrue(claude["stale"])
        self.assertEqual(claude["age_s"], 15 * 3600)
        # Last-known is still worth showing. "Resets in 2m" is not.
        self.assertEqual(claude["pools"]["session"]["pct"], 42.0)
        self.assertIsNone(claude["pools"]["session"]["resets_in"])
        self.assertIsNone(claude["pools"]["session"]["pace_pct"])

    def test_stale_provider_draws_no_chart(self):
        stale = quota_payload(stale=True)
        old = NOW + 15 * 3600
        self.assertEqual(burndown.compute_all({"claude": stale}, now=old), {})
        # Same payload inside the blip window still charts.
        fresh = burndown.compute_all({"claude": stale}, now=NOW + 60)
        self.assertIn("claude", fresh)

    def test_stale_provider_is_not_sampled(self):
        stale = quota_payload(stale=True)
        old = NOW + 15 * 3600
        with patch.object(quota_samples, "_last_row", {}), \
             patch.object(quota_samples, "_seeded", True):
            rows = quota_samples.record(
                {"claude": stale}, now=old, persist=False)
        self.assertEqual(rows, [])

    def test_fresh_provider_is_still_sampled(self):
        with patch.object(quota_samples, "_last_row", {}), \
             patch.object(quota_samples, "_seeded", True):
            rows = quota_samples.record(
                {"claude": quota_payload()}, now=NOW, persist=False)
        self.assertEqual(
            sorted(row["pool"] for row in rows), ["session", "week"])


if __name__ == "__main__":
    unittest.main()


class AuthRequiredTests(unittest.TestCase):
    """A dead login is not a slow fetch, and the payload has to say which.

    Both arrive as a stale snapshot with a message attached, which is why the
    Mac spent eleven hours reporting a missing Claude token as "Not updating"
    — true, unactionable, and indistinguishable from a rate limit.
    """

    def setUp(self):
        oauth_usage.reset_for_tests()
        self._oauth_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._oauth_home.cleanup)
        self._oauth_patch = patch.object(
            oauth_usage, "OAUTH_DIR", self._oauth_home.name)
        self._oauth_patch.start()
        self.addCleanup(self._oauth_patch.stop)

    def test_keep_stale_defaults_to_not_an_auth_problem(self):
        cache = {"t": 0.0, "data": None, "err": None}
        cache_util.store(cache, NOW, {"ok": True, "pct": 42})
        out = cache_util.keep_stale(cache, NOW + 60, "HTTP Error 429", {"ok": False})
        self.assertTrue(out["stale"])
        self.assertFalse(out["auth_required"])

    def test_keep_stale_marks_an_auth_problem_when_told(self):
        cache = {"t": 0.0, "data": None, "err": None}
        cache_util.store(cache, NOW, {"ok": True, "pct": 42})
        out = cache_util.keep_stale(
            cache, NOW + 60, "no token", {"ok": False}, auth_required=True)
        self.assertTrue(out["stale"])
        self.assertTrue(out["auth_required"])

    def test_a_provider_that_never_fetched_still_reports_the_flag(self):
        # No prior snapshot to replay: the empty branch has to carry it too,
        # or a first run with no login looks like a provider that is merely
        # not configured.
        cache = {"t": 0.0, "data": None, "err": None}
        out = cache_util.keep_stale(
            cache, NOW, "no token", {"ok": False}, auth_required=True)
        self.assertFalse(out["stale"])
        self.assertTrue(out["auth_required"])

    def test_a_good_fetch_clears_the_flag(self):
        cache = {"t": 0.0, "data": None, "err": None}
        cache_util.keep_stale(
            cache, NOW, "no token", {"ok": False}, auth_required=True)
        out = cache_util.store(cache, NOW + 60, {"ok": True, "pct": 42})
        self.assertFalse(out["auth_required"])

    def test_a_store_holding_only_mcp_oauth_asks_for_a_login(self):
        # The exact shape that broke: Claude Code's Keychain item present and
        # parseable, holding MCP plugin tokens and no plan token.
        blob = {"mcpOAuth": {"plugin:x|abc": {"accessToken": "mcp-token"}}}
        with patch.object(oauth_usage, "_read_creds_blob",
                          return_value=("keychain", blob)):
            out = oauth_usage.fetch_quota(force=True)
        self.assertTrue(out["auth_required"])
        self.assertIn("claudeAiOauth", out["error"])

    def test_no_credentials_anywhere_asks_for_a_login(self):
        with patch.object(oauth_usage, "_read_creds_blob",
                          return_value=(None, None)):
            out = oauth_usage.fetch_quota(force=True)
        self.assertTrue(out["auth_required"])

    def test_a_rate_limit_is_not_an_auth_problem(self):
        blob = {"claudeAiOauth": {"accessToken": "t", "refreshToken": "r"}}
        boom = urllib.error.HTTPError(
            oauth_usage.USAGE_URL, 429, "Too Many Requests", None, None)
        with patch.object(oauth_usage, "_read_creds_blob",
                          return_value=("keychain", blob)), \
             patch.object(oauth_usage, "_http_get_usage", side_effect=boom):
            out = oauth_usage.fetch_quota(force=True)
        self.assertFalse(out["auth_required"])
        self.assertIn("429", out["error"])

    def test_a_rate_limit_backs_off_and_says_for_how_long(self):
        blob = {"claudeAiOauth": {"accessToken": "t", "refreshToken": "r"}}
        boom = urllib.error.HTTPError(
            oauth_usage.USAGE_URL, 429, "Too Many Requests", None, None)
        calls = []

        def _boom(token):
            calls.append(token)
            raise boom

        with patch.object(oauth_usage, "_read_creds_blob",
                          return_value=("keychain", blob)), \
             patch.object(oauth_usage, "_http_get_usage", side_effect=_boom):
            first = oauth_usage.fetch_quota(force=True)
            # A user leaning on refresh must not become the traffic that
            # caused the 429. The second call is served from cache.
            second = oauth_usage.fetch_quota(force=True)

        self.assertEqual(len(calls), 1)
        self.assertIn("retrying in", first["error"])
        self.assertEqual(second["error"], first["error"])
        # Typed cause + absolute deadline so clients can say Paused without
        # parsing the error string, and so retry_in_s keeps counting down.
        self.assertEqual(first["stale_cause"], "rate_limited")
        self.assertIsInstance(first.get("retry_at"), float)
        self.assertGreater(first["retry_at"], time.time())

    def test_the_endpoint_is_asked_again_once_the_backoff_expires(self):
        blob = {"claudeAiOauth": {"accessToken": "t", "refreshToken": "r"}}
        boom = urllib.error.HTTPError(
            oauth_usage.USAGE_URL, 429, "Too Many Requests", None, None)
        calls = []

        def _boom(token):
            calls.append(token)
            raise boom

        later = time.time() + cache_util.RATE_LIMIT_BACKOFF_S[0] + 1
        with patch.object(oauth_usage, "_read_creds_blob",
                          return_value=("keychain", blob)), \
             patch.object(oauth_usage, "_http_get_usage", side_effect=_boom):
            oauth_usage.fetch_quota(force=True)
            with patch.object(oauth_usage.time, "time", return_value=later):
                oauth_usage.fetch_quota()

        self.assertEqual(len(calls), 2)

    def test_the_flag_reaches_providers_and_sources(self):
        state = {"claude": quota_payload(
            stale=True, auth_required=True, error="no plan token")}

        providers = headroom_server._providers_payload(state, burndowns={})
        claude = next(row for row in providers if row["id"] == "claude")
        self.assertTrue(claude["auth_required"])
        # Still ok, still replaying bars — which is exactly why the flag has
        # to travel separately from `ok`.
        self.assertTrue(claude["ok"])

        sources = headroom_server._sources_payload(state)
        row = next(row for row in sources if row["id"] == "claude")
        self.assertTrue(row["auth_required"])
        self.assertTrue(row["ok"])

    def test_a_healthy_source_carries_the_flag_as_false(self):
        # Absent would be indistinguishable from an older host on the wire,
        # and clients read a missing flag as "this host cannot tell me".
        state = {"claude": quota_payload()}
        row = next(r for r in headroom_server._sources_payload(state)
                   if r["id"] == "claude")
        self.assertFalse(row["auth_required"])

    def test_attention_calls_out_a_login_without_waiting_for_stale(self):
        # Under STALE_ALERT_S, so the stale reason would not fire yet. A login
        # that is gone does not get more fixed by waiting fifteen minutes.
        doc = {"providers": [{
            "id": "claude", "title": "Claude", "kind": "quota",
            "enabled": True, "ok": True, "stale": True,
            "auth_required": True, "stale_for_s": 60,
        }]}
        reasons = headroom_server._build_attention(doc)["reasons"]
        kinds = [r["kind"] for r in reasons]
        self.assertIn("signin", kinds)
        self.assertNotIn("stale", kinds)
        summary = next(r["summary"] for r in reasons if r["kind"] == "signin")
        self.assertEqual(
            summary, "Claude needs sign-in — run `claude /login`")

    def test_login_hint_drops_the_cli_when_there_is_no_cli(self):
        """`claude /login` is not a remedy on a Mac with no `claude`.

        Headroom reads the blob the CLI writes; the desktop app authenticates
        its own session and leaves nothing here. Telling a desktop-app-only
        Mac to run a command it does not have is a dead end, so the hint says
        to install it first.
        """
        oauth_usage._cli_cache["t"] = 0.0
        try:
            with patch.object(oauth_usage.shutil, "which", return_value=None), \
                 patch.object(oauth_usage.os, "access", return_value=False):
                remedy = sources_config.login_remedy("claude")
            self.assertIn("install the Claude Code CLI", remedy)
            self.assertNotIn("desktop app's login", remedy)
            oauth_usage._cli_cache["t"] = 0.0
            with patch.object(oauth_usage.shutil, "which",
                              return_value="/opt/homebrew/bin/claude"):
                self.assertEqual(sources_config.login_remedy("claude"),
                                 "run `claude /login`")
        finally:
            oauth_usage._cli_cache["t"] = 0.0

    def test_login_hint_finds_a_cli_off_the_launchagent_path(self):
        """`~/.claude/local/claude` is a default install and not on PATH."""
        oauth_usage._cli_cache["t"] = 0.0
        try:
            with patch.object(oauth_usage.shutil, "which", return_value=None), \
                 patch.object(oauth_usage, "CLI_EXTRA_PATHS",
                              ("~/.claude/local/claude",)), \
                 patch.object(oauth_usage.os, "access", return_value=True):
                self.assertTrue(oauth_usage.cli_installed())
        finally:
            oauth_usage._cli_cache["t"] = 0.0

    def test_signin_attention_uses_each_provider_login_hint(self):
        # Installed-but-not-authed is the case this line exists for — name
        # the command (or the IDE) rather than a generic "tool again".
        for source_id, needle in (
            ("claude", "`claude /login`"),
            ("codex", "`codex login`"),
            ("cursor", "sign in to Cursor"),
            ("gemini", "`gemini`"),
        ):
            doc = {"providers": [{
                "id": source_id,
                "title": sources_config.title_for(source_id),
                "kind": "quota", "enabled": True, "ok": False,
                "auth_required": True,
            }]}
            summary = next(
                r["summary"]
                for r in headroom_server._build_attention(doc)["reasons"]
                if r["kind"] == "signin"
            )
            self.assertIn(needle, summary, source_id)

    def test_stale_attention_names_the_cause(self):
        # "Stuck at 17h old" alone reads the same whether the fix is patience
        # or a login — the line has to say which. The recorded error is on the
        # provider row already; the reason phrases it.
        def summary_for(error, held=None, cause=None):
            row = {
                "id": "claude", "title": "Claude", "kind": "quota",
                "enabled": True, "ok": True, "stale": True,
                "stale_for_s": held if held is not None
                else cache_util.STALE_ALERT_S + 60,
                "error": error,
            }
            if cause is not None:
                row["stale_cause"] = cause
            doc = {"providers": [row]}
            reasons = headroom_server._build_attention(doc)["reasons"]
            return next(
                (r["summary"] for r in reasons if r["kind"] == "stale"),
                None)

        self.assertIn(
            "provider is rate-limiting, retrying",
            summary_for("HTTP Error 429: Too Many Requests",
                        held=cache_util.STALE_ALERT_RATE_LIMIT_S + 60,
                        cause="rate_limited"))
        # Under the rate-limit threshold, Attention stays quiet — the card
        # already says Paused.
        self.assertIsNone(summary_for(
            "HTTP Error 429: Too Many Requests",
            held=cache_util.STALE_ALERT_S + 60,
            cause="rate_limited"))
        self.assertIn(
            "provider error, retrying",
            summary_for("HTTP Error 503: Service Unavailable"))
        self.assertIn(
            "check network",
            summary_for("<urlopen error [Errno 8] nodename nor servname "
                        "provided, or not known>"))
        # An unrecognized error shows verbatim rather than vanishing.
        self.assertIn("keychain said no", summary_for("keychain said no"))
        self.assertIn("try Refresh all", summary_for(None))

    def test_providers_payload_carries_stale_cause_and_retry(self):
        now = time.time()
        state = {"claude": quota_payload(
            stale=True,
            error="HTTP Error 429: Too Many Requests, retrying in 5m",
            stale_cause="rate_limited",
            retry_at=now + 300,
        )}
        row = next(
            r for r in headroom_server._providers_payload(state, burndowns={})
            if r["id"] == "claude")
        self.assertEqual(row["stale_cause"], "rate_limited")
        self.assertIsInstance(row["retry_in_s"], int)
        self.assertGreater(row["retry_in_s"], 0)
        self.assertLessEqual(row["retry_in_s"], 300)


class SourceStampTests(unittest.TestCase):
    """Every fetcher's success path must run through `cache_util.store`.

    Ten sources hand-wrote `_cache.update(t=now, data=result)` instead, which
    skipped the `fetched_at` stamp — so `trusted()` took a week-old replayed
    balance at face value — and skipped the disk snapshot, so none of them
    survived a host restart the way the quota providers do.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(cache_util, "CACHE_DIR", tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fetch_ok(self, module, fetch, disk, patches):
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(module, "_cache", {"t": 0.0, "data": None}))
            for patcher in patches:
                stack.enter_context(patcher)
            out = fetch(force=True)
        self.assertIsNotNone(out, "fetch returned None instead of a payload")
        self.assertTrue(out.get("ok"), out.get("error"))
        self.assertFalse(out["stale"])
        self.assertIsInstance(out.get("fetched_at"), float)
        snapshot = cache_util.load_disk(disk)
        self.assertIsNotNone(snapshot, f"no last-good {disk}.json on disk")
        self.assertEqual(snapshot["fetched_at"], out["fetched_at"])
        return out

    def _openrouter_patches(self):
        return [
            patch.object(openrouter_usage, "_token", return_value="sk-mgmt"),
            patch.object(openrouter_usage, "_require_management_key"),
            patch.object(openrouter_usage, "_request", return_value={
                "data": {"total_credits": 10.0, "total_usage": 2.0}}),
            patch.object(openrouter_usage, "_fetch_spend", return_value=None),
        ]

    def test_ai_gateway_stamps_and_snapshots(self):
        self._fetch_ok(
            ai_gateway_usage, ai_gateway_usage.fetch_quota,
            ai_gateway_usage.DISK, [
                patch.object(ai_gateway_usage, "_token", return_value="tok"),
                patch.object(
                    ai_gateway_usage.http_util, "request_json",
                    return_value={"balance": 5.0, "total_used": 1.0}),
                patch.object(
                    ai_gateway_usage, "_fetch_spend", return_value=None),
            ])

    def test_openrouter_stamps_and_snapshots(self):
        self._fetch_ok(
            openrouter_usage, openrouter_usage.fetch_quota,
            openrouter_usage.DISK, self._openrouter_patches())

    def test_supabase_stamps_and_snapshots(self):
        self._fetch_ok(
            supabase_usage, supabase_usage.fetch_projects,
            supabase_usage.DISK, [
                patch.object(supabase_usage, "_token", return_value="sbp"),
                patch.object(
                    supabase_usage, "_list_projects", return_value=[]),
                patch.object(supabase_usage.app_config,
                             "supabase_project_refs", return_value=[]),
            ])

    def test_posthog_stamps_and_snapshots(self):
        row = {"id": 1, "name": "p", "realtime": 0,
               "events_today": 1, "users_today": 1}
        self._fetch_ok(
            posthog_usage, posthog_usage.fetch_stats,
            posthog_usage.DISK, [
                patch.object(posthog_usage, "_token", return_value="phx"),
                patch.object(posthog_usage, "_resolve_projects",
                             return_value=([{"id": 1, "name": "p"}], "cfg")),
                patch.object(posthog_usage, "_fetch_project",
                             return_value=row),
            ])

    def test_plausible_stamps_and_snapshots(self):
        row = {"domain": "example.com", "realtime": 0, "visitors_today": 1}
        self._fetch_ok(
            plausible_usage, plausible_usage.fetch_stats,
            plausible_usage.DISK, [
                patch.object(plausible_usage, "_token", return_value="pl"),
                patch.object(plausible_usage, "_resolve_sites",
                             return_value=(["example.com"], "cfg")),
                patch.object(plausible_usage, "_fetch_site",
                             return_value=row),
            ])

    def test_sentry_stamps_and_snapshots(self):
        self._fetch_ok(
            sentry_alerts, sentry_alerts.fetch_issues,
            sentry_alerts.DISK, [
                patch.object(sentry_alerts, "_token", return_value="sn"),
                patch.object(sentry_alerts, "_resolve_org",
                             return_value=("acme", None)),
                patch.object(sentry_alerts, "_get", return_value=[]),
            ])

    def test_datadog_stamps_and_snapshots(self):
        self._fetch_ok(
            datadog_monitors, datadog_monitors.fetch_monitors,
            datadog_monitors.DISK, [
                patch.object(datadog_monitors, "_api_key",
                             return_value="k"),
                patch.object(datadog_monitors, "_app_key",
                             return_value="a"),
                patch.object(
                    datadog_monitors, "_api_host",
                    return_value=("https://api.datadoghq.com",
                                  "datadoghq.com")),
                patch.object(datadog_monitors.http_util, "request_json",
                             return_value=[]),
            ])

    def test_axiom_stamps_and_snapshots(self):
        self._fetch_ok(
            axiom_monitors, axiom_monitors.fetch_alerts,
            axiom_monitors.DISK, [
                patch.object(axiom_monitors, "_token", return_value="ax"),
                patch.object(axiom_monitors, "_org_id", return_value="org"),
                patch.object(axiom_monitors, "_api_host",
                             return_value="https://api.axiom.co"),
                patch.object(axiom_monitors, "_get", return_value=[]),
            ])

    def test_github_actions_stamps_and_snapshots(self):
        self._fetch_ok(
            github_actions, github_actions.fetch_actions,
            github_actions.DISK, [
                patch.object(github_actions, "_token", return_value="gh"),
                patch.object(github_actions, "_repos", return_value=[]),
                patch.object(github_actions, "fetch_inbox",
                             return_value=[]),
            ])

    def test_openrouter_snapshot_survives_a_restart(self):
        # A fresh process (empty memory) whose first fetch fails must replay
        # the last-good disk snapshot, aged from the original stamp, instead
        # of blanking the card — the behavior claude/codex/cursor already had.
        first = self._fetch_ok(
            openrouter_usage, openrouter_usage.fetch_quota,
            openrouter_usage.DISK, self._openrouter_patches())
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(
                openrouter_usage, "_cache", {"t": 0.0, "data": None}))
            stack.enter_context(patch.object(
                openrouter_usage, "_token", return_value="sk-mgmt"))
            stack.enter_context(patch.object(
                openrouter_usage, "_require_management_key"))
            stack.enter_context(patch.object(
                openrouter_usage, "_request",
                side_effect=OSError("gateway down")))
            out = openrouter_usage.fetch_quota(force=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["stale"])
        self.assertEqual(out["fetched_at"], first["fetched_at"])

    def test_alert_sources_fetch_once_then_serve_from_cache(self):
        # Regression for the inverted freshness gate these three shipped
        # with: an empty cache returned None forever instead of fetching,
        # and a populated one refetched on every poll. First call must hit
        # the API; the second, inside the TTL, must not.
        calls = []

        def fake_get(*args, **kwargs):
            calls.append(args)
            return []

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(
                sentry_alerts, "_cache", {"t": 0.0, "data": None}))
            stack.enter_context(patch.object(
                sentry_alerts, "_token", return_value="sn"))
            stack.enter_context(patch.object(
                sentry_alerts, "_resolve_org", return_value=("acme", None)))
            stack.enter_context(patch.object(
                sentry_alerts, "_get", side_effect=fake_get))
            first = sentry_alerts.fetch_issues()
            second = sentry_alerts.fetch_issues()
        self.assertTrue(first["ok"])
        self.assertEqual(len(calls), 1)
        self.assertIs(second, first)
