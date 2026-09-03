#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import burndown
import quota_samples


TZ = ZoneInfo("Europe/Berlin")
NOW = 1_800_000_000.0
WEEK_S = 7 * 24 * 3600
DAY_S = 24 * 3600
RESETS = 3 * DAY_S          # 3 days left, so 4 days elapsed
WINDOW_START = int(NOW - (WEEK_S - RESETS))

# `burndown.compute` persists grants via `rolls_for`. Keep that journal off the
# desk owner's ~/.headroom — a frozen NOW years ahead otherwise lands as a
# forever-"0s" Activity row (GrantedResetTests' 18% fixture did exactly that).
_rolls_tmp = None
_rolls_patcher = None


def setUpModule():
    global _rolls_tmp, _rolls_patcher
    _rolls_tmp = tempfile.TemporaryDirectory()
    path = str(Path(_rolls_tmp.name) / "quota_resets.jsonl")
    _rolls_patcher = patch.object(quota_samples, "ROLLS_PATH", path)
    _rolls_patcher.start()


def tearDownModule():
    global _rolls_tmp, _rolls_patcher
    if _rolls_patcher is not None:
        _rolls_patcher.stop()
        _rolls_patcher = None
    if _rolls_tmp is not None:
        _rolls_tmp.cleanup()
        _rolls_tmp = None


def payload(week_pct, resets_in_s=RESETS):
    return {
        "ok": True,
        "plan": "Max",
        "week": {"pct": week_pct, "resets_in_s": resets_in_s,
                 "window_s": WEEK_S},
    }


def rows(start_remaining, end_remaining, span_s=DAY_S, step=300,
         resets_in_s=RESETS):
    """Linear sample history ending exactly at NOW."""
    out = []
    first = NOW - span_s
    steps = int(span_s // step)
    for i in range(steps + 1):
        t = first + i * step
        frac = (t - first) / span_s
        remaining = start_remaining + (end_remaining - start_remaining) * frac
        out.append({
            "t": int(t),
            "provider": "claude",
            "pool": "week",
            "pct": round(100.0 - remaining, 2),
            "window_s": WEEK_S,
            "resets_in_s": resets_in_s,
            "window_start": WINDOW_START,
        })
    return out


class ShapeTests(unittest.TestCase):
    def test_unusable_payload_is_none(self):
        self.assertIsNone(burndown.compute("claude", "week", {"ok": True}))

    def test_ideal_line_spans_the_window(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertEqual(got["ideal"], [[WINDOW_START, 100.0],
                                        [WINDOW_START + WEEK_S, 0.0]])
        self.assertEqual(got["window_end"] - got["window_start"], WEEK_S)

    def test_remaining_is_the_inverse_of_used(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertEqual(got["used_pct"], 60.0)
        self.assertEqual(got["remaining_pct"], 40.0)

    def test_ideal_remaining_tracks_elapsed_window(self):
        # 4 of 7 days elapsed, so an even spend leaves 3/7 = 42.9%.
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertAlmostEqual(got["ideal_remaining_pct"], 42.9, places=1)

    def test_series_is_capped_and_ordered(self):
        got = burndown.compute("claude", "week", payload(60.0), now=NOW, tz=TZ,
                               rows=rows(60.0, 40.0), points=48)
        actual = got["actual"]
        self.assertLessEqual(len(actual), 49)
        self.assertEqual([p[0] for p in actual],
                         sorted(p[0] for p in actual))
        self.assertEqual(actual[-1][1], 40.0)

    def test_empty_history_still_returns_a_point(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=[])
        self.assertEqual(got["actual"], [[int(NOW), 40.0]])
        self.assertIsNone(got["burn_rate_pct"])


class HistoryTests(unittest.TestCase):
    """`history` is what keeps a rolled window from drawing an empty chart."""

    # The live window opened a day ago, so a spent run two days back sits
    # clearly outside it and still inside HISTORY_LOOKBACK_S.
    ROLLED_RESETS = 6 * DAY_S
    ROLLED_START = int(NOW - DAY_S)

    def _rolled(self):
        """Samples spanning a grant: a window burned to 20, then handed back.

        The second run carries a later `window_start`, which is what `rolls`
        reads the boundary out of.
        """
        before = rows(100.0, 20.0, span_s=DAY_S)
        for row in before:
            row["t"] -= 2 * DAY_S
            row["window_start"] = self.ROLLED_START - WEEK_S
            row["window_end"] = self.ROLLED_START
            row["resets_in_s"] = self.ROLLED_RESETS
        after = rows(100.0, 95.0, span_s=3600,
                     resets_in_s=self.ROLLED_RESETS)
        for row in after:
            row["window_start"] = self.ROLLED_START
            row["window_end"] = self.ROLLED_START + WEEK_S
        return before + after

    def test_history_reaches_back_past_the_window(self):
        got = burndown.compute("claude", "week",
                               payload(5.0, resets_in_s=self.ROLLED_RESETS),
                               now=NOW, tz=TZ, rows=self._rolled())
        window_start = got["window_start"]
        # `actual` is clipped to the live window by design.
        self.assertTrue(all(p[0] >= window_start for p in got["actual"]))
        # `history` is not — that is the whole point of it.
        self.assertTrue(any(p[0] < window_start for p in got["history"]))

    def test_history_is_ordered_and_capped(self):
        got = burndown.compute("claude", "week",
                               payload(5.0, resets_in_s=self.ROLLED_RESETS),
                               now=NOW, tz=TZ, rows=self._rolled(), points=48)
        history = got["history"]
        self.assertLessEqual(len(history), 49)
        self.assertEqual([p[0] for p in history],
                         sorted(p[0] for p in history))

    def test_history_is_empty_without_samples(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=[])
        self.assertEqual(got["history"], [])

    def test_forgiven_survives_alongside_history(self):
        """Superseded, never removed — old phones and boards still read it."""
        got = burndown.compute("claude", "week",
                               payload(5.0, resets_in_s=self.ROLLED_RESETS),
                               now=NOW, tz=TZ, rows=self._rolled())
        self.assertIn("forgiven", got)
        self.assertIn("history", got)


class ForecastTests(unittest.TestCase):
    def test_exhaustion_before_reset_is_critical(self):
        # 60% -> 40% over 24h is 20 points/day; 40 left runs out in 2 days,
        # inside the 3 days remaining on the window.
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertAlmostEqual(got["burn_rate_pct"], 20.0, places=1)
        self.assertAlmostEqual(got["exhausts_in_s"], 2 * DAY_S, delta=60)
        self.assertTrue(got["exhausts_before_reset"])
        self.assertEqual(got["status"], burndown.STATUS_CRITICAL)
        self.assertIn("Out ", got["headline"])

    def test_slow_burn_with_slack_is_ok(self):
        got = burndown.compute("claude", "week", payload(45.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 55.0))
        self.assertFalse(got["exhausts_before_reset"])
        self.assertEqual(got["status"], burndown.STATUS_OK)
        self.assertGreater(got["delta_pct"], 0)
        self.assertIn("On pace", got["headline"])

    def test_deficit_without_exhaustion_is_ahead(self):
        # Well under the ideal line, but burning slowly enough to survive.
        got = burndown.compute("claude", "week", payload(70.0),
                               now=NOW, tz=TZ, rows=rows(35.0, 30.0))
        self.assertLess(got["delta_pct"], -5)
        self.assertFalse(got["exhausts_before_reset"])
        self.assertEqual(got["status"], burndown.STATUS_AHEAD)
        self.assertIn("Burn ", got["headline"])
        self.assertIn(" vs ", got["headline"])
        self.assertAlmostEqual(got["allowance_pct"], 10.0, places=1)

    def test_too_little_history_makes_no_claim(self):
        got = burndown.compute("claude", "week", payload(60.0), now=NOW, tz=TZ,
                               rows=rows(41.0, 40.0, span_s=600, step=300))
        self.assertIsNone(got["burn_rate_pct"])
        self.assertIsNone(got["exhausts_at"])
        self.assertFalse(got["exhausts_before_reset"])
        self.assertIn("Collecting history", got["headline"])

    def test_flat_usage_never_exhausts(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(40.0, 40.0))
        self.assertEqual(got["burn_rate_pct"], 0.0)
        self.assertIsNone(got["exhausts_at"])
        self.assertEqual(got["status"], burndown.STATUS_OK)

    def test_flat_usage_still_projects_level_to_the_reset(self):
        # "Nothing is moving" is a forecast. Emitting no series would render
        # identically to having no measurement at all.
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(40.0, 40.0))
        self.assertEqual(len(got["projected"]), 2)
        first, last = got["projected"]
        self.assertEqual(first[1], last[1])
        self.assertEqual(last[0], got["window_end"])

    def test_exhausted_pool_projects_nothing(self):
        got = burndown.compute("claude", "week", payload(100.0),
                               now=NOW, tz=TZ, rows=rows(5.0, 0.0))
        self.assertEqual(got["projected"], [])
        self.assertEqual(got["status"], burndown.STATUS_EXHAUSTED)

    def test_projection_stops_at_the_reset(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertEqual(len(got["projected"]), 2)
        self.assertLessEqual(got["projected"][-1][0], got["window_end"])
        self.assertGreaterEqual(got["projected"][-1][1], 0.0)

    def test_month_window_fit_reaches_past_one_day(self):
        # Cursor-style: burn landed ~36h ago, then a long flat. A 24h lookback
        # would read slope zero; the month window's third (~10d, capped at 7d)
        # still sees the drop.
        month_s = 30 * DAY_S
        resets = 2 * DAY_S
        start = int(NOW - (month_s - resets))
        history = []
        # Flat at 95% remaining until the burn, then flat at 85%.
        for t in range(int(NOW - 3 * DAY_S), int(NOW - 36 * 3600), 300):
            history.append({"t": t, "pct": 5.0, "window_start": start})
        for t in range(int(NOW - 36 * 3600), int(NOW) + 1, 300):
            history.append({"t": t, "pct": 15.0, "window_start": start})
        body = {
            "ok": True,
            "total": {"pct": 15.0, "resets_in_s": resets, "window_s": month_s},
        }
        got = burndown.compute("cursor", "total", body, now=NOW, tz=TZ,
                               rows=history)
        self.assertIsNotNone(got["burn_rate_pct"])
        self.assertGreater(got["burn_rate_pct"], 0.0)
        self.assertEqual(len(got["projected"]), 2)
        self.assertLess(got["projected"][-1][1], got["projected"][0][1])


class RateUnitTests(unittest.TestCase):
    """A 5h window in points-per-day reads as a 550%/day budget. Don't."""

    SESSION_S = 5 * 3600
    SESSION_RESETS = 3600

    def session_payload(self, pct):
        return {"ok": True, "session": {"pct": pct,
                                        "resets_in_s": self.SESSION_RESETS,
                                        "window_s": self.SESSION_S}}

    def session_rows(self, start_remaining, end_remaining, span_s=6000):
        start = int(NOW - (self.SESSION_S - self.SESSION_RESETS))
        out = []
        steps = int(span_s // 300)
        for i in range(steps + 1):
            t = NOW - span_s + i * 300
            frac = (t - (NOW - span_s)) / span_s
            remaining = start_remaining + (end_remaining - start_remaining) * frac
            out.append({"t": int(t), "pct": round(100.0 - remaining, 2),
                        "window_start": start})
        return out

    def test_short_window_rates_are_per_hour(self):
        got = burndown.compute("claude", "session", self.session_payload(70.0),
                               now=NOW, tz=TZ,
                               rows=self.session_rows(33.0, 30.0))
        self.assertEqual(got["rate_unit"], "hour")
        # 3 points over 6000s is 1.8 points/hour.
        self.assertAlmostEqual(got["burn_rate_pct"], 1.8, places=1)
        # 30 points left with 1h to go.
        self.assertAlmostEqual(got["allowance_pct"], 30.0, places=1)
        self.assertNotIn("/day", got["headline"])

    def test_long_window_rates_are_per_day(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertEqual(got["rate_unit"], "day")
        self.assertIn("/day", got["headline"])

    def test_small_rates_keep_precision(self):
        # A slow burn must not round to "0%/day" in the sentence.
        got = burndown.compute("claude", "week", payload(70.0),
                               now=NOW, tz=TZ, rows=rows(30.2, 30.0))
        self.assertEqual(got["status"], burndown.STATUS_AHEAD)
        self.assertIn("0.2 vs", got["headline"])
        self.assertIn("%/day", got["headline"])

    def test_pace_delta_is_percent_and_says_which_way(self):
        """The old copy ran abs() over a signed delta and called it "points".

        A pool 1 behind and a pool 1 ahead both read "1 point", and "points"
        collides with the credits and premium requests providers actually bill
        in. Percent, with the direction attached.
        """
        got = burndown.compute("claude", "week", payload(45.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 55.0))
        self.assertEqual(got["status"], burndown.STATUS_OK)
        self.assertIn("12% to spare", got["headline"])
        self.assertNotIn("point", got["headline"])


class PriorTests(unittest.TestCase):
    """Token history covers the window a fresh install can't fit a slope over."""

    def test_prior_forecasts_before_any_samples_exist(self):
        got = burndown.compute("claude", "week", payload(60.0), now=NOW, tz=TZ,
                               rows=[], prior_pct_per_day=20.0)
        self.assertEqual(got["rate_source"], "estimated")
        self.assertAlmostEqual(got["burn_rate_pct"], 20.0, places=1)
        # 40 left at 20/day runs out in 2 days, inside the 3 remaining.
        self.assertAlmostEqual(got["exhausts_in_s"], 2 * DAY_S, delta=60)
        self.assertEqual(got["status"], burndown.STATUS_CRITICAL)

    def test_estimated_headline_hedges(self):
        got = burndown.compute("claude", "week", payload(60.0), now=NOW, tz=TZ,
                               rows=[], prior_pct_per_day=20.0)
        self.assertIn("~20%/day", got["headline"])
        self.assertNotIn("about ", got["headline"])
        self.assertNotIn("Based on your recent usage", got["headline"])

    def test_measured_samples_beat_the_prior(self):
        # A wildly wrong prior must not survive contact with real data.
        got = burndown.compute("claude", "week", payload(60.0), now=NOW, tz=TZ,
                               rows=rows(60.0, 40.0), prior_pct_per_day=999.0)
        self.assertEqual(got["rate_source"], "measured")
        self.assertAlmostEqual(got["burn_rate_pct"], 20.0, places=1)
        self.assertNotIn("~", got["headline"])
        self.assertNotIn("about", got["headline"])

    def test_no_prior_and_no_samples_makes_no_claim(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=[])
        self.assertIsNone(got["rate_source"])
        self.assertIsNone(got["burn_rate_pct"])
        self.assertIn("Collecting history", got["headline"])

    def test_prior_is_scaled_into_the_pool_unit(self):
        got = burndown.compute(
            "claude", "session",
            {"ok": True, "session": {"pct": 70.0, "resets_in_s": 3600,
                                     "window_s": 5 * 3600}},
            now=NOW, tz=TZ, rows=[], prior_pct_per_day=24.0)
        self.assertEqual(got["rate_unit"], "hour")
        self.assertAlmostEqual(got["burn_rate_pct"], 1.0, places=2)

    def test_prior_is_ignored_once_exhausted(self):
        got = burndown.compute("claude", "week", payload(100.0), now=NOW, tz=TZ,
                               rows=[], prior_pct_per_day=20.0)
        self.assertEqual(got["status"], burndown.STATUS_EXHAUSTED)
        self.assertIsNone(got["exhausts_at"])


class ExhaustedTests(unittest.TestCase):
    """A spent pool is a fact to wait out, not an alarm to raise."""

    def test_zero_remaining_is_exhausted_not_ahead(self):
        got = burndown.compute("claude", "week", payload(100.0),
                               now=NOW, tz=TZ, rows=rows(20.0, 0.0))
        self.assertTrue(got["exhausted"])
        self.assertEqual(got["status"], burndown.STATUS_EXHAUSTED)
        self.assertEqual(got["remaining_pct"], 0.0)

    def test_exhausted_makes_no_forecast(self):
        got = burndown.compute("claude", "week", payload(100.0),
                               now=NOW, tz=TZ, rows=rows(20.0, 0.0))
        self.assertIsNone(got["exhausts_at"])
        self.assertIsNone(got["exhausts_in_s"])
        self.assertFalse(got["exhausts_before_reset"])
        self.assertEqual(got["projected"], [])

    def test_exhausted_headline_states_the_reset(self):
        got = burndown.compute("claude", "week", payload(100.0),
                               now=NOW, tz=TZ, rows=rows(20.0, 0.0))
        # Prose says when, not how long: clock form, never "3d".
        self.assertTrue(got["headline"].startswith("Exhausted · resets "))
        self.assertNotIn("3d", got["headline"])
        self.assertEqual(got["resets_in"], "3d")

    def test_healthy_pool_is_not_exhausted(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertFalse(got["exhausted"])


class GrantedResetTests(unittest.TestCase):
    """A provider handing everyone a fresh window mid-cycle. Nothing about the
    elapsed clock says a reset happened, and until the window moves every
    number on the card is drawn from two cycles at once."""

    RESET_AT = int(NOW - 6 * 3600)
    NEW_RESETS = WEEK_S - 6 * 3600

    def rows_across_the_reset(self):
        """A flat day on the old window, then six hours of real burn."""
        old_start = int(self.RESET_AT - 5 * DAY_S)
        out = [
            {"t": int(self.RESET_AT - 24 * 3600 + i * 300),
             "provider": "codex", "pool": "week", "pct": 18.0,
             "window_s": WEEK_S, "resets_in_s": 2 * DAY_S,
             "window_start": old_start, "window_end": old_start + WEEK_S}
            for i in range(int(24 * 3600 // 300))
        ]
        steps = int(6 * 3600 // 300)
        out += [
            {"t": int(self.RESET_AT + i * 300),
             "provider": "codex", "pool": "week",
             "pct": round(12.0 * i / steps, 2),
             "window_s": WEEK_S,
             "resets_in_s": int(WEEK_S - i * 300),
             "window_start": self.RESET_AT,
             "window_end": self.RESET_AT + WEEK_S}
            for i in range(steps + 1)
        ]
        return out

    def compute(self):
        return burndown.compute(
            "codex", "week",
            {"ok": True, "week": {"pct": 12.0, "window_s": WEEK_S,
                                  "resets_in_s": self.NEW_RESETS}},
            now=NOW, tz=TZ, rows=self.rows_across_the_reset())

    def test_window_follows_the_granted_reset(self):
        got = self.compute()
        self.assertEqual(got["window_start"], self.RESET_AT)
        self.assertEqual(got["window_end"], self.RESET_AT + WEEK_S)

    def test_curve_starts_at_the_reset_not_before_it(self):
        got = self.compute()
        self.assertEqual(got["actual"][0][0], self.RESET_AT)
        # 82% remaining is last cycle's plateau. It must not be on this chart.
        self.assertNotIn(82.0, [point[1] for point in got["actual"]])

    def test_rate_is_measured_after_the_reset(self):
        # 12 points in 6h is 48/day. Fitting across the reset instead sees a
        # jump upward, reads the slope as positive, and reports 0.
        got = self.compute()
        self.assertEqual(got["rate_source"], "measured")
        self.assertAlmostEqual(got["burn_rate_pct"], 48.0, delta=1.0)

    def test_axis_and_countdown_cannot_disagree(self):
        # The caption is rendered from resets_in and the chart ends at
        # window_end. They are the same instant or the card contradicts itself.
        got = self.compute()
        self.assertEqual(got["window_end"] - int(NOW), got["resets_in_s"])
        self.assertEqual(got["ideal"][-1][0], got["window_end"])


class InPlaceResetTests(unittest.TestCase):
    """A provider reset the counter without moving its scheduled window."""

    RESET_AT = int(NOW - 6 * 3600)

    def _rows(self):
        return [
            {"t": self.RESET_AT - 3600, "provider": "claude",
             "pool": "week", "pct": 39.0, "window_s": WEEK_S,
             "resets_in_s": 3 * DAY_S + 3600,
             "window_start": WINDOW_START},
            {"t": self.RESET_AT, "provider": "claude", "pool": "week",
             "pct": 0.0, "window_s": WEEK_S,
             "resets_in_s": 3 * DAY_S,
             "window_start": WINDOW_START},
            {"t": NOW - 3 * 3600, "provider": "claude", "pool": "week",
             "pct": 15.0, "window_s": WEEK_S,
             "resets_in_s": 3 * DAY_S - 3 * 3600,
             "window_start": WINDOW_START},
            {"t": NOW, "provider": "claude", "pool": "week",
             "pct": 30.0, "window_s": WEEK_S,
             "resets_in_s": RESETS,
             "window_start": WINDOW_START},
        ]

    def test_fit_starts_after_in_place_reset(self):
        got = burndown.compute(
            "claude", "week", payload(30.0), now=NOW, tz=TZ,
            rows=self._rows())
        self.assertEqual(got["window_start"], WINDOW_START)
        self.assertEqual(got["actual"][0][0], self.RESET_AT)
        self.assertEqual(got["boundaries"], [self.RESET_AT])
        self.assertAlmostEqual(got["burn_rate_pct"], 120.0, delta=1.0)
        self.assertLess(got["projected"][-1][1], got["projected"][0][1])


class VerdictTests(unittest.TestCase):
    """One short phrase per state, sitting above a stat row rather than
    restating it. Every surface renders the same words."""

    def test_critical_names_the_moment(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertTrue(got["verdict"].startswith("Runs out "))

    def test_on_pace_states_the_slack(self):
        got = burndown.compute("claude", "week", payload(45.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 55.0))
        # 55 left against the 42.9 an even spend would leave. "On pace" pairs
        # with "Over pace" — one axis, one noun, headline and verdict agreeing.
        self.assertEqual(got["verdict"], "On pace · 12%")

    def test_ahead_of_pace_still_says_whether_it_lasts(self):
        got = burndown.compute("claude", "week", payload(70.0),
                               now=NOW, tz=TZ, rows=rows(35.0, 30.0))
        self.assertEqual(got["verdict"], "Over pace")

    def test_exhausted_points_at_the_reset(self):
        got = burndown.compute("claude", "week", payload(100.0),
                               now=NOW, tz=TZ, rows=rows(20.0, 0.0))
        self.assertEqual(got["verdict"], "Spent, back in 3d")

    def test_no_forecast_says_so_rather_than_guessing(self):
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=[])
        self.assertEqual(got["verdict"], "Collecting history")

    def test_verdict_never_repeats_the_stat_row(self):
        # Left / Burning / Budget live in the row beside it. A verdict that
        # also carried them is how the card grew back into a paragraph.
        got = burndown.compute("claude", "week", payload(60.0),
                               now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        self.assertNotIn("%/day", got["verdict"])
        self.assertNotIn("left", got["verdict"].lower())


class AllowanceTests(unittest.TestCase):
    def test_allowance_is_dropped_once_it_stops_meaning_anything(self):
        # 40% left with a minute to go is not a "57000%/day budget".
        got = burndown.compute("claude", "week", payload(60.0, resets_in_s=60),
                               now=NOW, tz=TZ, rows=[])
        self.assertIsNone(got["allowance_pct"])
        self.assertNotIn("budget", got["headline"])


class AggregateTests(unittest.TestCase):
    def test_compute_all_skips_sources_that_are_not_ok(self):
        state = {"claude": dict(payload(60.0), ok=False)}
        self.assertEqual(burndown.compute_all(state, now=NOW, tz=TZ), {})

    def test_compute_all_groups_by_provider_and_pool(self):
        state = {"claude": payload(60.0)}
        got = burndown.compute_all(state, now=NOW, tz=TZ)
        self.assertEqual(list(got), ["claude"])
        self.assertEqual(list(got["claude"]), ["week"])

    def test_primary_prefers_critical(self):
        critical = burndown.compute("claude", "week", payload(60.0),
                                    now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        healthy = burndown.compute("claude", "week", payload(45.0),
                                   now=NOW, tz=TZ, rows=rows(60.0, 55.0))
        got = burndown.primary({"claude": {"week": healthy},
                                "codex": {"week": critical}})
        self.assertEqual(got["status"], burndown.STATUS_CRITICAL)

    def test_primary_of_nothing_is_none(self):
        self.assertIsNone(burndown.primary({}))

    def test_primary_prefers_about_to_run_out_over_already_out(self):
        # Nothing to decide about a spent pool; a critical one is still a call.
        critical = burndown.compute("claude", "week", payload(60.0),
                                    now=NOW, tz=TZ, rows=rows(60.0, 40.0))
        spent = burndown.compute("codex", "week", payload(100.0),
                                 now=NOW, tz=TZ, rows=rows(20.0, 0.0))
        got = burndown.primary({"codex": {"week": spent},
                                "claude": {"week": critical}})
        self.assertEqual(got["status"], burndown.STATUS_CRITICAL)

    def test_primary_prefers_exhausted_over_healthy(self):
        healthy = burndown.compute("claude", "week", payload(45.0),
                                   now=NOW, tz=TZ, rows=rows(60.0, 55.0))
        spent = burndown.compute("codex", "week", payload(100.0),
                                 now=NOW, tz=TZ, rows=rows(20.0, 0.0))
        got = burndown.primary({"claude": {"week": healthy},
                                "codex": {"week": spent}})
        self.assertEqual(got["status"], burndown.STATUS_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
