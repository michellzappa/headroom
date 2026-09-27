"""Tests for status.claude.com major/critical Attention gating."""

from __future__ import annotations

import unittest
from unittest import mock

import claude_status
import headroom_server
import sources_config


def _summary(*, indicator="none", description=None, incidents=None):
    return {
        "page": {
            "id": "tymt9n04zgry",
            "name": "Claude",
            "url": "https://status.claude.com",
            "updated_at": "2026-07-30T06:26:26.783Z",
        },
        "status": {
            "indicator": indicator,
            "description": description or {
                "none": "All Systems Operational",
                "minor": "Minor Service Outage",
                "major": "Major Service Outage",
                "critical": "Partial System Outage",
            }.get(indicator, indicator),
        },
        "incidents": incidents or [],
        "components": [],
    }


def _incident(name, impact, *, status="identified", resolved=False):
    return {
        "id": "inc1",
        "name": name,
        "status": "resolved" if resolved else status,
        "impact": impact,
        "resolved_at": "2026-07-30T07:00:00.000Z" if resolved else None,
    }


class ParseSummaryTests(unittest.TestCase):
    def test_none_no_incidents_is_quiet(self):
        payload = claude_status.parse_summary(_summary(indicator="none"))
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["alerting"])
        self.assertEqual(payload["indicator"], "none")
        self.assertIsNone(payload["incident_name"])

    def test_minor_with_minor_incident_is_quiet(self):
        payload = claude_status.parse_summary(_summary(
            indicator="minor",
            incidents=[_incident("Elevated errors", "minor")],
        ))
        self.assertFalse(payload["alerting"])

    def test_minor_with_major_incident_alerts(self):
        """Today's shape: page minor, open incident impact major."""
        payload = claude_status.parse_summary(_summary(
            indicator="minor",
            description="Minor Service Outage",
            incidents=[_incident(
                "Elevated errors across many models", "major")],
        ))
        self.assertTrue(payload["alerting"])
        self.assertEqual(
            payload["incident_name"],
            "Elevated errors across many models",
        )
        self.assertEqual(payload["incident_impact"], "major")

    def test_major_indicator_alerts(self):
        payload = claude_status.parse_summary(_summary(indicator="major"))
        self.assertTrue(payload["alerting"])
        self.assertIsNone(payload["incident_name"])

    def test_critical_indicator_alerts(self):
        payload = claude_status.parse_summary(_summary(indicator="critical"))
        self.assertTrue(payload["alerting"])

    def test_resolved_major_incident_is_ignored(self):
        payload = claude_status.parse_summary(_summary(
            indicator="none",
            incidents=[_incident("Past outage", "major", resolved=True)],
        ))
        self.assertFalse(payload["alerting"])

    def test_attention_summary_prefers_incident_name(self):
        self.assertEqual(
            claude_status.attention_summary({
                "incident_name": "Elevated errors across many models",
                "description": "Minor Service Outage",
            }),
            "Elevated errors across many models",
        )
        self.assertEqual(
            claude_status.attention_summary({
                "description": "Major Service Outage",
            }),
            "Claude · Major Service Outage",
        )
        self.assertEqual(
            claude_status.attention_summary({}),
            "Claude major outage",
        )


class FetchTests(unittest.TestCase):
    def setUp(self):
        claude_status._cache.clear()
        claude_status._cache.update(t=0.0, data=None)

    def tearDown(self):
        claude_status._cache.clear()
        claude_status._cache.update(t=0.0, data=None)

    @mock.patch("claude_status.http_util.request_json")
    def test_fetch_parses_summary(self, request_json):
        request_json.return_value = _summary(
            indicator="minor",
            incidents=[_incident("Elevated errors", "major")],
        )
        payload = claude_status.fetch(force=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["alerting"])
        self.assertFalse(payload.get("stale"))

    @mock.patch("claude_status.http_util.request_json",
                side_effect=TimeoutError("timed out"))
    def test_fetch_keeps_stale_on_failure(self, _request_json):
        good = claude_status.parse_summary(_summary(indicator="none"))
        claude_status._cache.update(t=1.0, data=dict(good), err=None)
        # Stamp as a real prior fetch so keep_stale can age it.
        claude_status._cache["data"]["fetched_at"] = 1.0
        claude_status._cache["data"]["ok"] = True

        payload = claude_status.fetch(force=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["stale"])
        self.assertIn("timed out", payload.get("error") or "")


class AttentionTests(unittest.TestCase):
    def test_status_is_not_a_standalone_integration(self):
        self.assertNotIn(
            "claude-status", sources_config.INTEGRATION_CATALOG_IDS)

    def test_alerting_lights_critical(self):
        attention = headroom_server._build_attention({
            "github": {"configured": True, "fail_count": 0},
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "claude_status": {
                "configured": True,
                "alerting": True,
                "incident_name": "Elevated errors across many models",
                "description": "Minor Service Outage",
            },
        })
        self.assertEqual(attention["level"], "critical")
        kinds = [r["kind"] for r in attention["reasons"]]
        self.assertIn("claude-status", kinds)
        self.assertEqual(
            attention["summary"],
            "Elevated errors across many models",
        )

    def test_quiet_when_not_alerting(self):
        attention = headroom_server._build_attention({
            "github": {"configured": True, "fail_count": 0},
            "supabase": {"configured": True, "alert_count": 0},
            "vercel": {"deployments": []},
            "claude_status": {
                "configured": True,
                "alerting": False,
                "indicator": "minor",
            },
        })
        self.assertEqual(attention["level"], "ok")
        self.assertEqual(attention["reasons"], [])

    def test_activity_row_when_alerting(self):
        items = headroom_server._build_activity(
            {"deployments": []},
            {"commits": []},
            claude_status_payload={
                "alerting": True,
                "incident_name": "Elevated errors across many models",
                "url": "https://status.claude.com",
            },
        )
        match = [i for i in items if i.get("kind") == "claude-status"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["status"], "error")
        self.assertEqual(
            match[0]["subject"],
            "Elevated errors across many models",
        )


if __name__ == "__main__":
    unittest.main()
