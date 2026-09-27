#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quota_samples


NOW = 1_800_000_000.0
WEEK_S = 7 * 24 * 3600


def claude_payload(session_pct=10.0, week_pct=40.0, week_resets=3 * 24 * 3600):
    return {
        "ok": True,
        "plan": "Max",
        "session": {"pct": session_pct, "resets_in_s": 3600,
                    "window_s": 5 * 3600},
        "week": {"pct": week_pct, "resets_in_s": week_resets,
                 "window_s": WEEK_S},
    }


class ExtractTests(unittest.TestCase):
    def test_reads_pool_from_payload(self):
        got = quota_samples.extract("claude", "week", claude_payload())
        self.assertEqual(got, {"pct": 40.0, "window_s": WEEK_S,
                               "resets_in_s": 3 * 24 * 3600})

    def test_falls_back_to_provider_level_resets(self):
        # Cursor reports one billing cycle at the top level for both pools.
        payload = {
            "ok": True,
            "resets_in_s": 12 * 3600,
            "auto": {"pct": 55.0, "window_s": 30 * 24 * 3600},
        }
        got = quota_samples.extract("cursor", "auto", payload)
        self.assertEqual(got["resets_in_s"], 12 * 3600)
        self.assertEqual(got["pct"], 55.0)

    def test_missing_pool_is_none(self):
        self.assertIsNone(quota_samples.extract("claude", "week", {"ok": True}))

    def test_missing_window_is_none(self):
        # Cursor has no default window, so a payload without one is unusable.
        payload = {"ok": True, "auto": {"pct": 5.0, "resets_in_s": 60}}
        self.assertIsNone(quota_samples.extract("cursor", "auto", payload))

    def test_unknown_pool_is_none(self):
        self.assertIsNone(quota_samples.extract("claude", "nope", {"ok": True}))


def row_at(t, pct, resets_in_s, window_s=WEEK_S, **extra):
    """A stored sample, as `window_for` receives it."""
    start, end = quota_samples.window_for(
        t, window_s, resets_in_s, pct=pct, previous=extra.pop("previous", None))
    row = {"t": t, "pct": pct, "resets_in_s": resets_in_s,
           "window_s": window_s, "window_start": start, "window_end": end}
    row.update(extra)
    return row


class WindowTests(unittest.TestCase):
    """The window is anchored on its reset, and the reset is held against
    jitter until something proves the window actually rolled."""

    def test_derived_from_resets_in(self):
        start, end = quota_samples.window_for(NOW, WEEK_S, 3 * 24 * 3600)
        self.assertEqual(end, int(NOW + 3 * 24 * 3600))
        self.assertEqual(start, int(NOW - 4 * 24 * 3600))

    def test_start_and_end_always_span_one_window(self):
        start, end = quota_samples.window_for(NOW, WEEK_S, 3 * 24 * 3600)
        self.assertEqual(end - start, WEEK_S)

    def test_snaps_to_previous_within_tolerance(self):
        first = row_at(NOW, 40.0, 3 * 24 * 3600)
        # API jitter of a couple of minutes must not look like a new window.
        _, end = quota_samples.window_for(
            NOW + 120, WEEK_S, 3 * 24 * 3600, pct=40.0, previous=first)
        self.assertEqual(end, first["window_end"])

    def test_real_reset_starts_a_new_window(self):
        first = row_at(NOW, 99.0, 60)
        _, end = quota_samples.window_for(
            NOW + 120, WEEK_S, WEEK_S, pct=0.0, previous=first)
        self.assertNotEqual(end, first["window_end"])

    def test_stale_resets_do_not_fork_a_window(self):
        # A cached response after a wake repeats `resets_in_s` while the clock
        # moves on, walking the derived window forward. That is not a reset.
        first = row_at(NOW, 40.0, 3 * 24 * 3600)
        _, end = quota_samples.window_for(
            NOW + 3600, WEEK_S, 3 * 24 * 3600, pct=40.0, previous=first)
        self.assertEqual(end, first["window_end"])

    def test_large_jitter_short_of_a_window_holds(self):
        first = row_at(NOW, 40.0, 3 * 24 * 3600)
        # A single reading 16h out — seen in the wild after a sleep — must not
        # fork either. Usage did not move, so nothing rolled.
        _, end = quota_samples.window_for(
            NOW, WEEK_S, 3 * 24 * 3600 + 16 * 3600, pct=40.0, previous=first)
        self.assertEqual(end, first["window_end"])

    def test_an_earlier_reset_reanchors(self):
        # A named account briefly sampled under another login's credentials
        # lands the other account's weekly end here. Once the right token is
        # in place the live countdown is days earlier, with usage unchanged —
        # not a grant, not a full-window roll. Holding would print the wrong
        # account's "6d 19h" for the rest of the week.
        first = row_at(NOW, 31.0, 6 * 24 * 3600 + 19 * 3600)
        _, end = quota_samples.window_for(
            NOW + 300, WEEK_S, 3 * 3600, pct=31.0, previous=first)
        self.assertEqual(end, int(NOW + 300 + 3 * 3600))
        self.assertNotEqual(end, first["window_end"])

    def test_slightly_earlier_within_tolerance_still_holds(self):
        first = row_at(NOW, 40.0, 3 * 24 * 3600)
        _, end = quota_samples.window_for(
            NOW + 60, WEEK_S, 3 * 24 * 3600 - 180, pct=40.0, previous=first)
        self.assertEqual(end, first["window_end"])

    def test_reset_observed_slightly_early_still_rolls(self):
        first = row_at(NOW, 99.0, 60)
        _, end = quota_samples.window_for(
            NOW, WEEK_S, WEEK_S - 300, pct=0.0, previous=first)
        self.assertNotEqual(end, first["window_end"])

    def test_granted_reset_mid_window_rolls(self):
        # Codex handing everyone a fresh week 1.5 days in. No elapsed-time rule
        # can see this: the new reset lands nowhere near a full window past the
        # held one. Usage dropping while resets_in jumps is the only evidence.
        first = row_at(NOW, 18.0, WEEK_S - 36 * 3600)
        start, end = quota_samples.window_for(
            NOW + 36 * 3600, WEEK_S, WEEK_S, pct=0.0, previous=first)
        self.assertEqual(start, int(NOW + 36 * 3600))
        self.assertEqual(end - start, WEEK_S)

    def test_a_grant_that_only_lowers_usage_does_not_roll(self):
        # Extra credits raise the denominator, so pct falls without the window
        # moving. resets_in keeps decaying, so this must stay one window.
        first = row_at(NOW, 40.0, 3 * 24 * 3600)
        _, end = quota_samples.window_for(
            NOW + 300, WEEK_S, 3 * 24 * 3600 - 300, pct=10.0, previous=first)
        self.assertEqual(end, first["window_end"])

    def test_a_wake_gap_alone_does_not_roll(self):
        # 11h of frozen resets_in across a sleep, seen in the live log. Usage
        # is unchanged, so it is jitter however large the gap looks.
        first = row_at(NOW, 18.0, 5 * 24 * 3600)
        _, end = quota_samples.window_for(
            NOW + 11 * 3600, WEEK_S, 5 * 24 * 3600, pct=18.0, previous=first)
        self.assertEqual(end, first["window_end"])

    def test_rows_without_window_end_still_carry_a_window(self):
        # Rows written before window_end was stored on each sample.
        legacy = {"t": NOW, "pct": 40.0, "resets_in_s": 3 * 24 * 3600,
                  "window_start": int(NOW - 4 * 24 * 3600)}
        _, end = quota_samples.window_for(
            NOW + 120, WEEK_S, 3 * 24 * 3600, pct=40.0, previous=legacy)
        self.assertEqual(end, int(NOW + 3 * 24 * 3600))


class RollTests(unittest.TestCase):
    """`rolls` reads grants back out of the stored labels, so the chart can
    mark the moment a pool came back early instead of silently restarting."""

    def _log(self, *rows):
        """Rows relabelled the way the store would have written them."""
        out = []
        for t, pct, resets_in_s, window_s in rows:
            out.append(row_at(t, pct, resets_in_s, window_s=window_s,
                              previous=out[-1] if out else None))
        return out

    def test_a_granted_reset_is_reported_with_what_it_forgave(self):
        rows = self._log(
            (NOW, 42.0, 6 * 24 * 3600, WEEK_S),
            (NOW + 3600, 0.0, WEEK_S, WEEK_S),
        )
        self.assertEqual(
            quota_samples.rolls(rows),
            [{"t": int(NOW + 3600), "kind": "granted", "forgiven_pct": 42.0}],
        )

    def test_a_window_running_out_on_time_is_not_a_grant(self):
        # The axis already ends there — marking it would label every Monday.
        rows = self._log(
            (NOW, 42.0, 300, WEEK_S),
            (NOW + 600, 0.0, WEEK_S, WEEK_S),
        )
        self.assertEqual(quota_samples.rolls(rows), [])

    def test_a_session_rolling_minutes_early_is_not_a_grant(self):
        # A 5h session reporting its reset 18 minutes early is the source
        # rounding, and it happens several times a day. Seen in the live log.
        session_s = 5 * 3600
        rows = self._log(
            (NOW, 10.0, 18 * 60, session_s),
            (NOW + 300, 2.0, session_s, session_s),
        )
        self.assertEqual(quota_samples.rolls(rows), [])

    def test_a_flat_window_is_not_a_grant(self):
        # Nothing was forgiven, so there is nothing to explain on the chart.
        rows = self._log(
            (NOW, 0.0, 6 * 24 * 3600, WEEK_S),
            (NOW + 3600, 0.0, WEEK_S, WEEK_S),
        )
        self.assertEqual(quota_samples.rolls(rows), [])

    def test_older_grants_drop_out_past_the_lookback(self):
        rows = self._log(
            (NOW, 42.0, 6 * 24 * 3600, WEEK_S),
            (NOW + 3600, 0.0, WEEK_S, WEEK_S),
        )
        self.assertEqual(
            quota_samples.rolls(rows, since=NOW + 2 * 3600), [])

    def test_in_place_refill_is_a_boundary_without_a_new_window(self):
        # Claude can clear the used counter after a token reset while keeping
        # the same scheduled weekly reset. It must be a chart/fit boundary,
        # but not a new window label or a granted-reset event.
        first = row_at(NOW, 39.0, 3 * 24 * 3600)
        second = row_at(
            NOW + 3600, 0.0, 3 * 24 * 3600 - 3600,
            previous=first)
        self.assertEqual(second["window_start"], first["window_start"])
        self.assertEqual(
            quota_samples.boundaries([first, second]), [int(NOW + 3600)])
        self.assertEqual(quota_samples.rolls([first, second]), [])


class RollJournalTests(unittest.TestCase):
    """Durable grant journal — heatmap history past sample retention."""

    def setUp(self):
        quota_samples.reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.rolls_path = str(Path(self.tmp.name) / "quota_resets.jsonl")
        self.patcher = patch.object(
            quota_samples, "ROLLS_PATH", self.rolls_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        quota_samples.reset_for_tests()

    def _log(self, *rows):
        out = []
        for t, pct, resets_in_s, window_s in rows:
            out.append(row_at(t, pct, resets_in_s, window_s=window_s,
                              previous=out[-1] if out else None))
        return out

    def test_rolls_for_remembers_across_calls(self):
        rows = self._log(
            (NOW, 42.0, 6 * 24 * 3600, WEEK_S),
            (NOW + 3600, 0.0, WEEK_S, WEEK_S),
        )
        first = quota_samples.rolls_for(
            "codex", "week", rows, now=NOW + 3600)
        self.assertEqual(first, [
            {"t": int(NOW + 3600), "kind": "granted", "forgiven_pct": 42.0,
             "source": "observed"},
        ])
        # Sample window gone — journal still answers.
        again = quota_samples.rolls_for(
            "codex", "week", [], now=NOW + 3600)
        self.assertEqual(again, first)

    def test_rolls_for_does_not_duplicate_the_same_instant(self):
        rows = self._log(
            (NOW, 42.0, 6 * 24 * 3600, WEEK_S),
            (NOW + 3600, 0.0, WEEK_S, WEEK_S),
        )
        quota_samples.rolls_for("codex", "week", rows, now=NOW + 3600)
        quota_samples.rolls_for("codex", "week", rows, now=NOW + 3600)
        with open(self.rolls_path) as handle:
            lines = [line for line in handle if line.strip()]
        self.assertEqual(len(lines), 1)

    def test_journal_outlives_sample_retention(self):
        rows = self._log(
            (NOW, 55.0, 6 * 24 * 3600, WEEK_S),
            (NOW + 3600, 0.0, WEEK_S, WEEK_S),
        )
        quota_samples.rolls_for("codex", "week", rows, now=NOW + 3600)
        later = NOW + quota_samples.RETENTION_S + 7 * 24 * 3600
        kept = quota_samples.rolls_for(
            "codex", "week", [], now=later)
        self.assertEqual(kept, [
            {"t": int(NOW + 3600), "kind": "granted", "forgiven_pct": 55.0,
             "source": "observed"},
        ])

    def test_journal_drops_rows_past_roll_retention(self):
        rows = self._log(
            (NOW, 40.0, 6 * 24 * 3600, WEEK_S),
            (NOW + 3600, 0.0, WEEK_S, WEEK_S),
        )
        quota_samples.rolls_for("codex", "week", rows, now=NOW + 3600)
        # One second past retention from the grant instant, not from `now` at
        # write time — equality on the cutoff still keeps the row.
        far = (NOW + 3600) + quota_samples.ROLL_RETENTION_S + 1
        self.assertEqual(
            quota_samples.rolls_for("codex", "week", [], now=far), [])
        # Compaction only rewrites when something ages out; force it.
        quota_samples._maybe_compact_rolls(now=far)
        self.assertEqual(
            quota_samples.remembered_rolls(
                "codex", "week", since=0, limit=None, now=far),
            [],
        )

    def test_journal_hides_grants_ahead_of_now(self):
        # A frozen test clock that leaked into the live journal must not
        # surface as a forever-fresh Activity row.
        with open(self.rolls_path, "w") as handle:
            handle.write(json.dumps({
                "t": int(NOW + 7 * 24 * 3600),
                "provider": "codex",
                "pool": "week",
                "kind": "granted",
                "source": "observed",
                "forgiven_pct": 18.0,
            }) + "\n")
            handle.write(json.dumps({
                "t": int(NOW - 3600),
                "provider": "codex",
                "pool": "week",
                "kind": "granted",
                "source": "observed",
                "forgiven_pct": 51.0,
            }) + "\n")
        got = quota_samples.remembered_rolls(
            "codex", "week", since=0, limit=None, now=NOW)
        self.assertEqual(got, [{
            "t": int(NOW - 3600),
            "kind": "granted",
            "forgiven_pct": 51.0,
            "source": "observed",
        }])

    def test_live_journal_refuses_wall_clock_futures(self):
        # Unpatched burndown tests used to append NOW-relative grants into the
        # desk owner's file. Refuse those when ROLLS_PATH is the live path.
        live = str(Path(self.tmp.name) / "live" / "quota_resets.jsonl")
        Path(live).parent.mkdir()
        with patch.object(quota_samples, "ROLLS_PATH", live), \
             patch.object(quota_samples, "_rolls_path_is_live",
                          return_value=True):
            quota_samples._remember_rolls(
                "codex", "week",
                [{"t": int(NOW - 6 * 3600), "kind": "granted",
                  "forgiven_pct": 18.0, "source": "observed"}],
                now=NOW,
            )
            self.assertFalse(Path(live).exists())


class RecordTests(unittest.TestCase):
    def setUp(self):
        quota_samples.reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "quota_samples.jsonl")
        self.patcher = patch.object(quota_samples, "STORE_PATH", self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        quota_samples.reset_for_tests()

    def test_records_one_row_per_pool(self):
        rows = quota_samples.record({"claude": claude_payload()}, now=NOW)
        self.assertEqual({row["pool"] for row in rows}, {"session", "week"})
        self.assertTrue(all(row["provider"] == "claude" for row in rows))

    def test_skips_sources_that_are_not_ok(self):
        payload = claude_payload()
        payload["ok"] = False
        self.assertEqual(quota_samples.record({"claude": payload}, now=NOW), [])

    def test_skips_sources_replaying_last_good_data(self):
        # A stale payload keeps ok=True and repeats the same pct and the same
        # resets_in_s every tick. Recorded, it would read back as a measured
        # burn of zero on a window that never advances.
        payload = claude_payload()
        payload["stale"] = True
        self.assertEqual(quota_samples.record({"claude": payload}, now=NOW), [])

    def test_a_gap_while_stale_does_not_break_the_next_real_sample(self):
        quota_samples.record({"claude": claude_payload()}, now=NOW)
        stuck = claude_payload()
        stuck["stale"] = True
        quota_samples.record({"claude": stuck}, now=NOW + 3600)
        rows = quota_samples.record(
            {"claude": claude_payload(week_pct=44.0)}, now=NOW + 7200)
        week = next(row for row in rows if row["pool"] == "week")
        self.assertEqual(week["pct"], 44.0)

    def test_dedupes_inside_one_bucket(self):
        quota_samples.record({"claude": claude_payload()}, now=NOW)
        again = quota_samples.record({"claude": claude_payload()}, now=NOW + 30)
        self.assertEqual(again, [])
        self.assertEqual(len(quota_samples.read(provider="claude")), 2)

    def test_writes_again_in_the_next_bucket(self):
        quota_samples.record({"claude": claude_payload()}, now=NOW)
        later = quota_samples.record(
            {"claude": claude_payload(week_pct=41.0)},
            now=NOW + quota_samples.BUCKET_S)
        self.assertEqual(len(later), 2)
        week = quota_samples.read(provider="claude", pool="week")
        self.assertEqual([row["pct"] for row in week], [40.0, 41.0])

    def test_restart_does_not_duplicate_a_bucket(self):
        quota_samples.record({"claude": claude_payload()}, now=NOW)
        quota_samples.reset_for_tests()  # simulate a host restart
        again = quota_samples.record({"claude": claude_payload()}, now=NOW + 60)
        self.assertEqual(again, [])
        self.assertEqual(len(quota_samples.read(provider="claude")), 2)

    def test_current_window_isolates_the_newest_window(self):
        quota_samples.record({"claude": claude_payload(week_resets=600)},
                             now=NOW)
        quota_samples.reset_for_tests()
        # Window reset: the meter refills and resets_in_s jumps back up.
        quota_samples.record(
            {"claude": claude_payload(week_pct=2.0, week_resets=WEEK_S)},
            now=NOW + 1200)
        self.assertEqual(len(quota_samples.windows("claude", "week")), 2)
        current = quota_samples.current_window("claude", "week")
        self.assertEqual([row["pct"] for row in current], [2.0])

    def test_current_window_ignores_an_orphaned_future_start(self):
        # A window_start further ahead than the live one, left behind by a bad
        # reading, must not become the pin — but a sample that actually fell
        # inside the live window still belongs on the curve.
        start = int(NOW - (WEEK_S - 600))
        rows = [
            {"t": NOW, "provider": "claude", "pool": "week", "pct": 40.0,
             "window_s": WEEK_S, "resets_in_s": 600, "window_start": start},
            {"t": NOW + 300, "provider": "claude", "pool": "week", "pct": 41.0,
             "window_s": WEEK_S, "resets_in_s": 600, "window_start": start + 99_000},
            {"t": NOW + 600, "provider": "claude", "pool": "week", "pct": 42.0,
             "window_s": WEEK_S, "resets_in_s": 600, "window_start": start},
        ]
        with open(self.path, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        current = quota_samples.current_window("claude", "week")
        self.assertEqual([row["pct"] for row in current], [40.0, 41.0, 42.0])

    def test_current_window_reunites_forked_labels_by_time(self):
        # Codex-style resets_in freeze walks window_start forward each poll.
        # The burn from 0→18% lives on the early labels; later flat samples
        # wear new ones. Time-range selection puts them back on one curve.
        real_start = int(NOW - 24 * 3600)
        rows = [
            {"t": real_start + 3600, "provider": "codex", "pool": "week",
             "pct": 0.0, "window_s": WEEK_S, "resets_in_s": WEEK_S - 3600,
             "window_start": real_start},
            {"t": real_start + 7200, "provider": "codex", "pool": "week",
             "pct": 18.0, "window_s": WEEK_S, "resets_in_s": WEEK_S - 7200,
             "window_start": real_start},
            {"t": real_start + 12 * 3600, "provider": "codex", "pool": "week",
             "pct": 18.0, "window_s": WEEK_S, "resets_in_s": WEEK_S - 3600,
             "window_start": real_start + 5 * 3600},  # forked label
        ]
        with open(self.path, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        current = quota_samples.current_window(
            "codex", "week", window_start=real_start, window_s=WEEK_S)
        self.assertEqual([row["pct"] for row in current], [0.0, 18.0, 18.0])

    def test_a_store_written_by_the_old_rule_is_relabelled(self):
        # Rows carrying a window_start that predates reset detection, with a
        # granted reset buried in the middle: usage falls and resets_in jumps
        # back to a full week, but the old labels never moved.
        stale_start = int(NOW - 2 * 24 * 3600)
        reset_at = int(NOW - 3600)
        rows = [
            {"t": stale_start + i * 300, "provider": "codex", "pool": "week",
             "pct": 18.0, "window_s": WEEK_S,
             "resets_in_s": WEEK_S - 2 * 24 * 3600,
             "window_start": stale_start}
            for i in range(4)
        ] + [
            {"t": reset_at + i * 300, "provider": "codex", "pool": "week",
             "pct": float(i), "window_s": WEEK_S,
             "resets_in_s": WEEK_S - i * 300,
             "window_start": stale_start}   # the label the old rule held
            for i in range(4)
        ]
        with open(self.path, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

        quota_samples.record({"claude": claude_payload()}, now=NOW)  # seeds

        current = quota_samples.current_window(
            "codex", "week", window_start=reset_at, window_s=WEEK_S)
        self.assertEqual([row["pct"] for row in current], [0.0, 1.0, 2.0, 3.0])
        stored = quota_samples.read(provider="codex", pool="week")
        self.assertEqual(stored[-1]["window_start"], reset_at)
        self.assertEqual(stored[-1]["window_end"], reset_at + WEEK_S)
        # The pre-reset rows land on the window the grant cut short, derived
        # from their own reading rather than the label the old rule left.
        self.assertNotEqual(stored[0]["window_start"],
                            stored[-1]["window_start"])
        self.assertEqual(stored[0]["window_end"] - stored[0]["window_start"],
                         WEEK_S)

    def test_relabelling_runs_once_and_leaves_readings_alone(self):
        quota_samples.record({"claude": claude_payload()}, now=NOW)
        before = quota_samples.read(provider="claude")
        quota_samples.reset_for_tests()
        quota_samples.record({"claude": claude_payload()},
                             now=NOW + quota_samples.BUCKET_S)
        after = quota_samples.read(provider="claude")
        self.assertEqual([row["pct"] for row in after[:len(before)]],
                         [row["pct"] for row in before])
        self.assertEqual([row["window_start"] for row in after[:len(before)]],
                         [row["window_start"] for row in before])

    def test_compact_drops_rows_past_retention(self):
        quota_samples.record({"claude": claude_payload()}, now=NOW)
        quota_samples.reset_for_tests()
        quota_samples.record({"claude": claude_payload()},
                             now=NOW + quota_samples.RETENTION_S + 3600)
        kept = quota_samples.compact(
            now=NOW + quota_samples.RETENTION_S + 3600)
        self.assertEqual(kept, 2)
        remaining = quota_samples.read(provider="claude")
        self.assertTrue(all(row["t"] > NOW for row in remaining))

    def test_read_survives_a_corrupt_line(self):
        quota_samples.record({"claude": claude_payload()}, now=NOW)
        with open(self.path, "a") as handle:
            handle.write("{not json\n")
        self.assertEqual(len(quota_samples.read(provider="claude")), 2)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(quota_samples.read(), [])
        self.assertEqual(quota_samples.current_window("claude", "week"), [])


if __name__ == "__main__":
    unittest.main()
