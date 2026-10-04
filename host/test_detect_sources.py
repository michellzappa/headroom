#!/usr/bin/env python3
"""First-run source detection + seeding."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import detect_sources
import oauth_usage
import sources_config


class DetectSourcesTests(unittest.TestCase):
    def test_claude_detection_uses_the_fetchers_credential_search(self):
        with patch.object(oauth_usage, "credentials_present",
                          return_value=True) as present:
            self.assertTrue(detect_sources.claude_signed_in())
        present.assert_called_once_with()

    def test_suggested_enables_detected_only(self):
        with patch.object(detect_sources, "detected_map", return_value={
            "claude": True,
            "codex": False,
            "cursor": False,
            "vercel": False,
            "git": False,
            "github": False,
            "local": True,
            "supabase": False,
            "plausible": False,
            "posthog": False,
        }):
            got = detect_sources.suggested_enabled(
                ("claude", "codex", "cursor", "vercel", "git",
                 "github", "local", "supabase", "plausible", "posthog"))
        self.assertTrue(got["claude"])
        self.assertFalse(got["codex"])
        self.assertFalse(got["cursor"])
        self.assertTrue(got["local"])
        self.assertFalse(got["vercel"])
        self.assertFalse(got["plausible"])
        self.assertFalse(got["posthog"])

    def test_suggested_falls_back_when_no_quota_detected(self):
        with patch.object(detect_sources, "detected_map", return_value={
            "claude": False, "codex": False, "cursor": False,
            "vercel": False, "git": False, "github": False,
            "local": True, "supabase": False, "plausible": False,
            "posthog": False,
        }):
            got = detect_sources.suggested_enabled(
                ("claude", "codex", "cursor", "local"))
        self.assertTrue(got["claude"])
        self.assertTrue(got["codex"])
        self.assertTrue(got["cursor"])
        self.assertTrue(got["local"])


class SeedSourcesTests(unittest.TestCase):
    def setUp(self):
        sources_config.reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "sources.json")
        self.patcher = patch.object(sources_config, "STORE_PATH", self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        sources_config.reset_for_tests()

    def test_missing_file_is_seeded_from_detection(self):
        with patch.object(detect_sources, "detected_map", return_value={
            "claude": True,
            "codex": False,
            "cursor": True,
            "vercel": False,
            "git": True,
            "github": False,
            "local": True,
            "supabase": False,
            "plausible": False,
            "posthog": False,
        }):
            enabled = sources_config.enabled_map()
        self.assertTrue(enabled["claude"])
        self.assertFalse(enabled["codex"])
        self.assertTrue(enabled["cursor"])
        self.assertTrue(os.path.isfile(self.path))
        with open(self.path) as handle:
            saved = json.load(handle)
        self.assertEqual(saved.get("seeded_from"), "detect")
        self.assertFalse(saved["enabled"]["codex"])
        self.assertFalse(saved["enabled"].get("plausible", False))
        self.assertFalse(saved["enabled"].get("posthog", False))


class ProviderOrderTests(unittest.TestCase):
    """Pinned order + the focus[] pick that drives menu bar / widget / board."""

    def setUp(self):
        sources_config.reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "sources.json")
        self.patcher = patch.object(sources_config, "STORE_PATH", self.path)
        self.patcher.start()
        sources_config.set_enabled(
            {sid: True for sid in sources_config.SOURCE_IDS})

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        sources_config.reset_for_tests()

    def test_default_order_is_registry_order(self):
        self.assertEqual(
            sources_config.order_ids(), list(sources_config.BURN_SOURCE_IDS))

    def test_pinning_moves_the_focus_window(self):
        rest = [s for s in sources_config.BURN_SOURCE_IDS if s != "cursor"]
        sources_config.set_order(["cursor"] + rest)
        self.assertEqual(sources_config.order_ids()[0], "cursor")
        self.assertEqual(sources_config.focus_ids()[0], "cursor")
        self.assertEqual(
            len(sources_config.focus_ids()), sources_config.FOCUS_LIMIT)

    def test_balances_never_fill_a_quota_slot(self):
        order = list(sources_config.BURN_SOURCE_IDS)
        sources_config.set_order(order)
        keep = {"claude", "codex", "openrouter", "ai-gateway"}
        sources_config.set_enabled({sid: sid in keep for sid in order})
        self.assertEqual(sources_config.focus_ids(), ["claude", "codex"])
        self.assertTrue(sources_config.is_balance_only("openrouter"))
        self.assertFalse(sources_config.is_balance_only("claude"))

    def test_disabled_providers_are_skipped_but_keep_their_place(self):
        order = list(sources_config.BURN_SOURCE_IDS)
        sources_config.set_order(order)
        sources_config.set_enabled({order[0]: False})
        self.assertEqual(sources_config.order_ids(), order)
        self.assertNotIn(order[0], sources_config.focus_ids())
        self.assertEqual(sources_config.focus_ids()[0], order[1])

    def test_new_provider_appends_rather_than_jumping_the_queue(self):
        # A file pinned before a provider existed must not promote it on
        # upgrade — the user's top 3 should survive a release.
        partial = list(sources_config.BURN_SOURCE_IDS)[:-1]
        sources_config.set_order(partial)
        stored = sources_config.order_ids()
        self.assertEqual(stored[:len(partial)], partial)
        self.assertEqual(sorted(stored), sorted(sources_config.BURN_SOURCE_IDS))

    def test_unknown_and_duplicate_ids_are_dropped(self):
        sources_config.set_order(
            ["claude", "claude", "plausible", "nope", "codex"])
        stored = sources_config.order_ids()
        self.assertEqual(stored[:2], ["claude", "codex"])
        self.assertNotIn("plausible", stored)
        self.assertNotIn("nope", stored)
        self.assertEqual(len(stored), len(set(stored)))
        self.assertEqual(sorted(stored), sorted(sources_config.BURN_SOURCE_IDS))

    def test_order_survives_a_reload(self):
        rest = [s for s in sources_config.BURN_SOURCE_IDS if s != "cursor"]
        sources_config.set_order(["cursor"] + rest)
        sources_config.reset_for_tests()
        self.assertEqual(sources_config.order_ids()[0], "cursor")


if __name__ == "__main__":
    unittest.main()
