#!/usr/bin/env python3
"""Claude OAuth credential selection and token refresh.

Covers the failure that shipped in 2.0.0: Headroom imported Claude Code's
blob once, that copy's refresh token expired, and every later poll preferred
the dead copy over the good one `claude login` kept writing to the Keychain.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import oauth_usage


HOUR_MS = 3600 * 1000


def blob(access="tok", refresh="ref", expires_in_h=1, refresh_in_h=24):
    """A Claude Code credential blob, aged relative to now."""
    now_ms = time.time() * 1000
    oauth = {
        "accessToken": access,
        "refreshToken": refresh,
        "expiresAt": int(now_ms + expires_in_h * HOUR_MS),
        "rateLimitTier": "default_claude_max_5x",
    }
    if refresh_in_h is not None:
        oauth["refreshTokenExpiresAt"] = int(now_ms + refresh_in_h * HOUR_MS)
    return {"claudeAiOauth": oauth}


def http_error(code, payload):
    return urllib.error.HTTPError(
        "https://example.invalid", code, "err", {},
        __import__("io").BytesIO(json.dumps(payload).encode()))


class LivenessTests(unittest.TestCase):
    def test_expired_refresh_token_is_not_a_credential(self):
        dead = blob(refresh_in_h=-1)
        self.assertIsNotNone(oauth_usage._oauth_block(dead))
        self.assertIsNone(oauth_usage._live_oauth(dead))

    def test_missing_refresh_expiry_is_unknown_not_dead(self):
        # Older Claude Code blobs have no refreshTokenExpiresAt. Treating the
        # absent field as expired would strand those installs entirely.
        legacy = blob(refresh_in_h=None)
        self.assertIsNotNone(oauth_usage._live_oauth(legacy))

    def test_live_refresh_token_passes(self):
        self.assertIsNotNone(oauth_usage._live_oauth(blob()))


class CredentialSelectionTests(unittest.TestCase):
    def setUp(self):
        oauth_usage.reset_for_tests()
        self.addCleanup(oauth_usage.reset_for_tests)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owned = Path(self.tmp.name) / "claude.json"
        self.patchers = [
            patch.object(oauth_usage, "OAUTH_DIR", self.tmp.name),
            patch.object(oauth_usage, "_headroom_path",
                         return_value=str(self.owned)),
            patch.object(oauth_usage, "_path_from_headroom_store",
                         return_value=str(self.owned)),
            # No stray credential file on the test machine.
            patch.object(oauth_usage, "_creds_file",
                         return_value=str(Path(self.tmp.name) / "absent.json")),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def write_owned(self, data):
        self.owned.write_text(json.dumps(data))

    def test_dead_own_copy_falls_through_to_keychain(self):
        self.write_owned(blob(access="stale", refresh="dead", refresh_in_h=-1))
        fresh = blob(access="fresh", refresh="live")
        with patch.object(oauth_usage, "_read_keychain_blob",
                          return_value=fresh):
            store, got = oauth_usage._read_creds_blob()
        self.assertEqual(got["claudeAiOauth"]["accessToken"], "fresh")
        # And it is adopted, so the next poll needs no Keychain read at all.
        self.assertTrue(store.startswith(oauth_usage.HEADROOM_STORE_PREFIX))
        self.assertEqual(
            json.loads(self.owned.read_text())["claudeAiOauth"]["accessToken"],
            "fresh")

    def test_live_own_copy_never_reads_keychain(self):
        self.write_owned(blob(access="mine"))
        with patch.object(oauth_usage, "_read_keychain_blob") as read:
            store, got = oauth_usage._read_creds_blob()
        read.assert_not_called()
        self.assertEqual(got["claudeAiOauth"]["accessToken"], "mine")

    def test_dead_everywhere_still_returns_something(self):
        # Nothing renewable anywhere: the caller must still get a store to
        # report against rather than "no credentials", which reads as a
        # missing login rather than an expired one.
        self.write_owned(blob(access="stale", refresh_in_h=-1))
        with patch.object(oauth_usage, "_read_keychain_blob",
                          return_value=None):
            store, got = oauth_usage._read_creds_blob()
        self.assertIsNotNone(store)
        self.assertEqual(got["claudeAiOauth"]["accessToken"], "stale")

    def test_rejected_grant_falls_through_even_when_dated_live(self):
        # The failure this guards: `claude /login` rotates the grant without
        # moving the expiry the old blob states, so the replaced copy goes on
        # testing as live and the early return above never reaches the fresh
        # Keychain item. Only the token endpoint can tell the two apart.
        self.write_owned(blob(access="stale", refresh="rotated"))
        oauth_usage._bury_grant("rotated")
        fresh = blob(access="fresh", refresh="live")
        with patch.object(oauth_usage, "_read_keychain_blob",
                          return_value=fresh):
            store, got = oauth_usage._read_creds_blob()
        self.assertEqual(got["claudeAiOauth"]["accessToken"], "fresh")
        self.assertTrue(store.startswith(oauth_usage.HEADROOM_STORE_PREFIX))

    def test_burial_expires_the_copy_on_disk(self):
        # Memory alone would forget across a KeepAlive respawn, and the
        # daemon would be pinned again on the next start.
        self.write_owned(blob(access="stale", refresh="rotated"))
        oauth_usage._bury_grant("rotated")
        self.assertIsNone(
            oauth_usage._live_oauth(json.loads(self.owned.read_text())))

    def test_rejected_grant_is_not_imported_back(self):
        # The same dead token still sits in the Keychain until the user logs
        # in. Importing it would undo the expiry just written.
        self.write_owned(blob(access="stale", refresh="rotated"))
        oauth_usage._bury_grant("rotated")
        with patch.object(oauth_usage, "_read_keychain_blob",
                          return_value=blob(access="same", refresh="rotated")):
            _store, got = oauth_usage._read_creds_blob()
        self.assertNotEqual(got["claudeAiOauth"]["accessToken"], "same")
        self.assertIsNone(
            oauth_usage._live_oauth(json.loads(self.owned.read_text())))

    def test_invalid_grant_buries_the_token_it_used(self):
        self.write_owned(blob(access="stale", refresh="rotated"))
        data = blob(access="stale", refresh="rotated")
        with patch.object(oauth_usage.http_util, "request_json",
                          side_effect=http_error(400, {
                              "error": "invalid_grant",
                              "error_description": "Refresh token not found",
                          })):
            with self.assertRaises(oauth_usage.OAuthLoginRequired):
                oauth_usage._refresh(
                    data["claudeAiOauth"], oauth_usage._headroom_store(), data)
        self.assertIn("rotated", oauth_usage._dead_refresh_tokens())

    def test_legacy_blobs_still_selected(self):
        self.write_owned(blob(access="legacy", refresh_in_h=None))
        with patch.object(oauth_usage, "_read_keychain_blob") as read:
            _store, got = oauth_usage._read_creds_blob()
        read.assert_not_called()
        self.assertEqual(got["claudeAiOauth"]["accessToken"], "legacy")


class RefreshErrorTests(unittest.TestCase):
    def setUp(self):
        oauth_usage.reset_for_tests()
        self.addCleanup(oauth_usage.reset_for_tests)

    def test_invalid_grant_stops_before_the_next_host(self):
        calls = []

        def fake(url, **kwargs):
            calls.append(url)
            raise http_error(400, {
                "error": "invalid_grant",
                "error_description": "Refresh token not found or invalid",
            })

        with patch.object(oauth_usage.http_util, "request_json",
                          side_effect=fake):
            with self.assertRaises(oauth_usage.OAuthLoginRequired) as caught:
                oauth_usage._refresh(blob()["claudeAiOauth"], "store", blob())
        self.assertEqual(len(calls), 1, "a definitive answer must not fan out")
        self.assertIn("Refresh token not found", str(caught.exception))

    def test_route_gone_never_owns_the_message(self):
        # The 2.0.0 symptom exactly: a 404 from a dead fallback host arriving
        # last and masking the reason the first host gave.
        def fake(url, **kwargs):
            if "platform" in url:
                raise http_error(500, {"error_description": "upstream boom"})
            raise http_error(404, {"type": "error"})

        with patch.object(oauth_usage.http_util, "request_json",
                          side_effect=fake):
            with self.assertRaises(RuntimeError) as caught:
                oauth_usage._refresh(blob()["claudeAiOauth"], "store", blob())
        msg = str(caught.exception)
        self.assertIn("upstream boom", msg)
        self.assertNotIn("404", msg)

    def test_no_refresh_token_is_a_login_problem(self):
        with self.assertRaises(oauth_usage.OAuthLoginRequired):
            oauth_usage._refresh({"accessToken": "t"}, "store", {})

    def test_console_endpoint_is_gone(self):
        self.assertNotIn(
            "https://console.anthropic.com/v1/oauth/token",
            oauth_usage.TOKEN_URLS)


class FetchQuotaTests(unittest.TestCase):
    def setUp(self):
        oauth_usage.reset_for_tests()
        self.addCleanup(oauth_usage.reset_for_tests)

    def test_dead_grant_reports_auth_required(self):
        dead = blob(expires_in_h=-1, refresh_in_h=-1)

        with patch.object(oauth_usage, "_load_oauth",
                          return_value=("store", dead,
                                        dead["claudeAiOauth"])), \
             patch.object(oauth_usage, "_refresh",
                          side_effect=oauth_usage.OAuthLoginRequired(
                              "Claude sign-in expired")), \
             patch.object(oauth_usage.cache_util, "load_disk",
                          return_value=None), \
             patch.object(oauth_usage, "_http_get_usage") as usage:
            out = oauth_usage.fetch_quota(force=True)

        usage.assert_not_called()
        self.assertTrue(out["auth_required"])
        self.assertIn("expired", out["error"])


if __name__ == "__main__":
    unittest.main()
