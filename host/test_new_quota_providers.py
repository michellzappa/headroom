#!/usr/bin/env python3
"""Unit tests for the five new coding-quota fetchers."""

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import cache_util
import copilot_usage
import github_actions
import grok_usage
import jetbrains_usage
import quota_util
import sources_config
import windsurf_usage


class QuotaUtilTests(unittest.TestCase):
    def test_used_and_remaining(self):
        self.assertEqual(quota_util.used_pct(25, 100), 25.0)
        self.assertEqual(quota_util.remaining_pct_to_used(40), 60.0)
        self.assertIsNone(quota_util.used_pct(1, 0))


class RegistryHasNewProviders(unittest.TestCase):
    def test_eleven_quota_sources(self):
        ids = [s.id for s in sources_config.QUOTA_SOURCES]
        self.assertEqual(
            ids,
            ["claude", "codex", "cursor", "copilot", "gemini",
             "windsurf", "jetbrains", "zed", "grok", "openrouter",
             "ai-gateway"],
        )
        self.assertTrue(all(s.group == sources_config.GROUP_AI for s in
                            sources_config.QUOTA_SOURCES))


class GrokMapTests(unittest.TestCase):
    def test_maps_overage_as_dollars_without_a_burn_headline(self):
        out = grok_usage._map({
            "subscription_tier": "SuperGrok",
            "config": {
                "currentPeriod": {"end": "2099-08-01T00:00:00Z"},
                "onDemandCap": {"val": 20},
                "onDemandUsed": {"val": 5},
            },
        })
        self.assertTrue(out["ok"])
        self.assertEqual(out["credits"]["used_usd"], 5.0)
        self.assertEqual(out["credits"]["limit_usd"], 20.0)
        self.assertEqual(out["credits"]["remaining_usd"], 15.0)
        self.assertIsNone(sources_config.headline_pct("grok", out))


class JetBrainsParseTests(unittest.TestCase):
    def test_parses_quota_xml(self):
        quota = {
            "type": "Available",
            "current": 2500,
            "maximum": 10000,
            "tariffQuota": {"available": 7500},
        }
        refill = {
            "type": "Known",
            "next": "2099-08-01T00:00:00Z",
        }
        root = ET.Element("application")
        ET.SubElement(root, "component", {
            "name": "AIAssistantQuotaManager2",
            "quotaInfo": json.dumps(quota),
            "nextRefill": json.dumps(refill),
        })
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "IntelliJIdea2025.1" / "options"
            base.mkdir(parents=True)
            path = base / "AIAssistantQuotaManager2.xml"
            ET.ElementTree(root).write(path)
            parsed = jetbrains_usage._parse_file(path)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["month"]["pct"], 25.0)
        self.assertIn("IntelliJIdea", parsed["plan"])


class CopilotMapTests(unittest.TestCase):
    def test_maps_premium_and_chat(self):
        out = copilot_usage._map({
            "copilot_plan": "pro",
            "quota_snapshots": {
                "premium_interactions": {"percent_remaining": 40},
                "chat": {"percent_remaining": 80},
            },
        })
        self.assertTrue(out["ok"])
        self.assertEqual(out["premium"]["pct"], 60.0)
        self.assertEqual(out["chat"]["pct"], 20.0)
        self.assertEqual(out["plan"], "Pro")


class CopilotDetectionTests(unittest.TestCase):
    """Detection must be about Copilot, not about `gh auth login`."""

    def test_github_token_alone_is_not_copilot(self):
        with patch.object(copilot_usage, "_local_config_signed_in",
                          return_value=False):
            with patch.object(cache_util, "load_disk", return_value=None):
                with patch.object(github_actions, "_token",
                                  return_value="ghp_xxx"):
                    self.assertFalse(copilot_usage.signed_in())
                    self.assertTrue(copilot_usage.has_token())

    def test_local_copilot_config_detects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "apps.json"
            path.write_text(json.dumps({
                "github.com:Iv1.abc": {"oauth_token": "gho_x", "user": "me"},
            }))
            with patch.object(copilot_usage, "CONFIG_PATHS", (str(path),)):
                with patch.object(cache_util, "load_disk", return_value=None):
                    self.assertTrue(copilot_usage.signed_in())

    def test_config_without_a_token_is_not_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hosts.json"
            path.write_text(json.dumps({"github.com": {"user": "me"}}))
            with patch.object(copilot_usage, "CONFIG_PATHS", (str(path),)):
                with patch.object(cache_util, "load_disk", return_value=None):
                    self.assertFalse(copilot_usage.signed_in())

    def test_confirmed_plan_on_disk_latches_detection(self):
        """Org seats leave no local config — the first good fetch is the proof."""
        with patch.object(copilot_usage, "_local_config_signed_in",
                          return_value=False):
            with patch.object(cache_util, "load_disk",
                              return_value={"ok": True, "plan": "Business"}):
                self.assertTrue(copilot_usage.signed_in())
            with patch.object(cache_util, "load_disk",
                              return_value={"ok": True, "plan": None}):
                self.assertFalse(copilot_usage.signed_in())


class WindsurfMapTests(unittest.TestCase):
    def test_maps_remaining_percents(self):
        out = windsurf_usage._map({
            "planName": "Pro",
            "dailyQuotaRemainingPercent": 70,
            "weeklyQuotaRemainingPercent": 55,
            "dailyQuotaResetAtUnix": 4102444800,
            "weeklyQuotaResetAtUnix": 4102444800,
        })
        self.assertTrue(out["ok"])
        self.assertEqual(out["plan"], "Pro")
        self.assertEqual(out["session"]["pct"], 30.0)
        self.assertEqual(out["week"]["pct"], 45.0)


if __name__ == "__main__":
    unittest.main()
