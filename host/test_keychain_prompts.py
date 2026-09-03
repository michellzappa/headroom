#!/usr/bin/env python3
"""Keychain + Zed credential helpers that must not pop SecurityAgent."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import detect_sources
import keychain
import oauth_usage
import zed_usage


class DetectSourcesKeychainTests(unittest.TestCase):
    def test_github_probe_uses_fail_closed_read_token(self):
        with patch.object(keychain, "read_token", return_value="tok") as read:
            with patch.dict(os.environ, {}, clear=False):
                for key in ("HEADROOM_GITHUB_TOKEN", "GITHUB_TOKEN"):
                    os.environ.pop(key, None)
                self.assertTrue(detect_sources.github_signed_in())
        read.assert_called_once_with(
            "com.centaur-labs.headroom.github", "access-token",
            allow_ui=False)

    def test_datadog_needs_both_keys(self):
        seen = []

        def fake(service, account, allow_ui=True, **kwargs):
            seen.append((account, allow_ui))
            return "x" if account == "api-key" else None

        with patch.object(keychain, "read_token", side_effect=fake):
            with patch.dict(os.environ, {}, clear=False):
                for key in (
                    "DD_API_KEY", "HEADROOM_DATADOG_API_KEY",
                    "DD_APP_KEY", "DD_APPLICATION_KEY",
                    "HEADROOM_DATADOG_APP_KEY",
                ):
                    os.environ.pop(key, None)
                self.assertFalse(detect_sources.datadog_signed_in())
        self.assertEqual(
            seen,
            [("api-key", False), ("app-key", False)],
        )


class ZedKeychainTests(unittest.TestCase):
    def setUp(self):
        zed_usage.rearm_keychain()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(zed_usage.rearm_keychain)
        deny = Path(self.tmp.name) / ".denied-zed"
        self.patchers = [
            patch.object(zed_usage, "_deny_path", return_value=str(deny)),
            patch.object(zed_usage, "_settings_server",
                         return_value="zed.dev"),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_signed_in_never_allows_ui(self):
        with patch.object(
            keychain, "get_internet_password",
            return_value=(keychain.ERR_SEC_SUCCESS, "tok", "user"),
        ) as internet:
            self.assertTrue(zed_usage.signed_in())
        internet.assert_called_once_with("zed.dev", allow_ui=False)

    def test_deny_sticks_across_fetches(self):
        with patch.object(
            keychain, "get_internet_password",
            return_value=(keychain.ERR_SEC_USER_CANCELED, None, None),
        ):
            with patch.object(
                keychain, "get_generic_password",
                return_value=(keychain.ERR_SEC_ITEM_NOT_FOUND, None),
            ):
                user, token = zed_usage._keychain_creds(allow_ui=True)
        self.assertIsNone(user)
        self.assertIsNone(token)
        self.assertTrue(os.path.isfile(zed_usage._deny_path()))

        with patch.object(keychain, "get_internet_password") as internet:
            self.assertIsNone(zed_usage._keychain_creds(allow_ui=True)[1])
        internet.assert_not_called()

        zed_usage.rearm_keychain()
        with patch.object(
            keychain, "get_internet_password",
            return_value=(keychain.ERR_SEC_SUCCESS, "tok", "u"),
        ) as internet:
            user, token = zed_usage._keychain_creds(allow_ui=True)
        self.assertEqual((user, token), ("u", "tok"))
        internet.assert_called_once_with("zed.dev", allow_ui=True)


if __name__ == "__main__":
    unittest.main()


class ClaudeCredentialPresenceTests(unittest.TestCase):
    """`credentials_present` may consult Keychain, never interactively."""

    def _isolated(self, root):
        return patch.multiple(
            oauth_usage,
            OAUTH_DIR=str(root / "oauth"),
            CREDS_FILE=str(root / ".credentials.json"),
        )

    CLAUDE_LOGIN = json.dumps({
        "claudeAiOauth": {"accessToken": "sk-live", "refreshToken": "r"},
    })
    MCP_ONLY = json.dumps({"mcpOAuth": {"some-server": {"accessToken": "x"}}})

    def _keychain(self, status, raw):
        return patch.object(keychain, "get_generic_password",
                            return_value=(status, raw))

    def test_keychain_login_counts_as_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._isolated(Path(tmp)):
                with self._keychain(keychain.ERR_SEC_SUCCESS,
                                    self.CLAUDE_LOGIN) as read:
                    self.assertTrue(oauth_usage.credentials_present())
        self.assertEqual(read.call_args_list[0].args[0],
                         oauth_usage._keychain_service(None))

    def test_mcp_grant_alone_is_not_a_claude_login(self):
        """One blob holds unrelated grants; only claudeAiOauth is a sign-in."""
        with tempfile.TemporaryDirectory() as tmp:
            with self._isolated(Path(tmp)):
                with self._keychain(keychain.ERR_SEC_SUCCESS, self.MCP_ONLY):
                    self.assertFalse(oauth_usage.credentials_present())

    def test_legacy_service_is_still_searched(self):
        """Claude Code moved to a per-config-dir service; Macs kept the old."""
        seen = []

        def by_service(service, **kwargs):
            seen.append(service)
            if service == oauth_usage.KEYCHAIN_SERVICE:
                return keychain.ERR_SEC_SUCCESS, self.CLAUDE_LOGIN
            return keychain.ERR_SEC_ITEM_NOT_FOUND, None

        with tempfile.TemporaryDirectory() as tmp:
            with self._isolated(Path(tmp)):
                with patch.object(keychain, "get_generic_password",
                                  side_effect=by_service):
                    self.assertTrue(oauth_usage.credentials_present())
        self.assertIn(oauth_usage.KEYCHAIN_SERVICE, seen)

    def test_no_files_and_no_item_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._isolated(Path(tmp)):
                with self._keychain(keychain.ERR_SEC_ITEM_NOT_FOUND, None):
                    self.assertFalse(oauth_usage.credentials_present())

    def test_gated_item_falls_back_to_existence(self):
        """No shape to read without a prompt — and probes never prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            with self._isolated(Path(tmp)):
                with self._keychain(keychain.ERR_SEC_INTERACTION_NOT_ALLOWED,
                                    None):
                    with patch.object(keychain, "generic_password_exists",
                                      return_value=True) as exists:
                        self.assertTrue(oauth_usage.credentials_present())
        self.assertTrue(exists.called)

    def test_presence_never_allows_ui(self):
        """A denied prompt is sticky; a probe must not be able to raise one."""
        seen = []

        def fake(service, account=None, allow_ui=True, **kwargs):
            seen.append(allow_ui)
            return keychain.ERR_SEC_ITEM_NOT_FOUND, None

        with tempfile.TemporaryDirectory() as tmp:
            with self._isolated(Path(tmp)):
                with patch.object(keychain, "get_generic_password",
                                  side_effect=fake):
                    oauth_usage.credentials_present()
        self.assertTrue(seen)
        self.assertNotIn(True, seen)

    def test_exists_query_fails_closed_on_ui(self):
        seen = {}

        def fake_auth_pairs(cf, sec, allow_ui):
            seen["allow_ui"] = allow_ui
            return []

        with patch.object(keychain, "_auth_ui_pairs", side_effect=fake_auth_pairs):
            keychain.generic_password_exists("headroom-test-absent-service")
        self.assertFalse(seen.get("allow_ui", True))
