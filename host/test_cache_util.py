#!/usr/bin/env python3
"""Last-good cache: what a replay is, and how old it says it is."""

from __future__ import annotations

import unittest

import cache_util


EMPTY = {"ok": False, "plan": None, "error": None}
NOW = 1_800_000_000.0


class KeepStaleTests(unittest.TestCase):
    def test_a_good_fetch_is_stamped_and_not_stale(self):
        cache = {}
        data = cache_util.store(cache, NOW, {"ok": True, "plan": "Max"})
        self.assertEqual(data["fetched_at"], NOW)
        self.assertFalse(data["stale"])

    def test_a_replay_is_marked_and_dated_from_the_last_real_fetch(self):
        cache = {}
        cache_util.store(cache, NOW, {"ok": True, "plan": "Max"})
        out = cache_util.keep_stale(cache, NOW + 4200, "boom", EMPTY)
        self.assertTrue(out["ok"])
        self.assertTrue(out["stale"])
        self.assertEqual(out["error"], "boom")
        self.assertEqual(out["stale_for_s"], 4200)

    def test_the_age_counts_from_the_fetch_not_from_the_last_attempt(self):
        # Every failing poll lands in keep_stale. If the clock restarted on
        # each one, a permanently broken source would read as fresh forever
        # and nothing downstream could ever escalate it.
        cache = {}
        cache_util.store(cache, NOW, {"ok": True, "plan": "Max"})
        for minute in range(1, 60):
            out = cache_util.keep_stale(
                cache, NOW + minute * 60, "boom", EMPTY)
        self.assertEqual(out["stale_for_s"], 59 * 60)

    def test_nothing_good_to_replay_fails_closed(self):
        out = cache_util.keep_stale({}, NOW, "boom", EMPTY)
        self.assertFalse(out["ok"])
        self.assertFalse(out["stale"])
        self.assertEqual(out["error"], "boom")

    def test_a_snapshot_without_the_stamp_ages_from_the_first_failure(self):
        # Disk caches written before `fetched_at` existed cannot say how old
        # they are. Ageing them from the first failure under-reports — the
        # data is older than this — but it never invents a number, and it
        # stops a source that broke before the upgrade from being exempt.
        cache = {"data": {"ok": True, "plan": "Max"}}
        first = cache_util.keep_stale(cache, NOW, "boom", EMPTY)
        self.assertEqual(first["stale_for_s"], 0)
        later = cache_util.keep_stale(cache, NOW + 3600, "boom", EMPTY)
        self.assertEqual(later["stale_for_s"], 3600)


class FailureBackoffTests(unittest.TestCase):
    """A failure that keeps failing must stop being retried like a blip.

    The shipped counterexample: three Claude accounts on a 20-second retry is
    nine requests a minute at a rate-limited endpoint — a cadence that keeps
    the 429 alive for as long as anyone lets it run.
    """

    TTL, FAIL = 60, 20

    def _fail(self, cache, at):
        cache_util.keep_stale(cache, at, "HTTP Error 429", EMPTY)

    def test_first_failure_keeps_the_short_retry(self):
        cache = {}
        cache_util.store(cache, NOW, {"ok": True})
        self._fail(cache, NOW + 60)
        self.assertTrue(cache_util.fresh(
            cache, NOW + 60 + self.FAIL - 1, self.TTL, self.FAIL))
        self.assertFalse(cache_util.fresh(
            cache, NOW + 60 + self.FAIL + 1, self.TTL, self.FAIL))

    def test_consecutive_failures_double_the_wait(self):
        cache = {}
        cache_util.store(cache, NOW, {"ok": True})
        at = NOW
        for _ in range(3):
            self._fail(cache, at)
        # Third consecutive miss → 4 × FAIL before the next try.
        self.assertTrue(cache_util.fresh(
            cache, at + 4 * self.FAIL - 1, self.TTL, self.FAIL))
        self.assertFalse(cache_util.fresh(
            cache, at + 4 * self.FAIL + 1, self.TTL, self.FAIL))

    def test_the_backoff_is_capped(self):
        cache = {}
        cache_util.store(cache, NOW, {"ok": True})
        for _ in range(40):
            self._fail(cache, NOW)
        self.assertFalse(cache_util.fresh(
            cache, NOW + cache_util.FAIL_BACKOFF_CAP_S + 1,
            self.TTL, self.FAIL))

    def test_a_good_fetch_resets_the_streak(self):
        cache = {}
        cache_util.store(cache, NOW, {"ok": True})
        for _ in range(6):
            self._fail(cache, NOW)
        cache_util.store(cache, NOW + 600, {"ok": True})
        self._fail(cache, NOW + 700)
        # Back at the short leash, not the one the old outage earned.
        self.assertFalse(cache_util.fresh(
            cache, NOW + 700 + self.FAIL + 1, self.TTL, self.FAIL))

    def test_retry_after_floors_the_wait_and_is_capped(self):
        cache = {}
        cache_util.store(cache, NOW, {"ok": True})
        cache_util.keep_stale(cache, NOW, "HTTP Error 429", EMPTY,
                              retry_after_s=300)
        # First failure would retry at FAIL; the provider said 300s.
        self.assertTrue(cache_util.fresh(
            cache, NOW + 299, self.TTL, self.FAIL))
        self.assertFalse(cache_util.fresh(
            cache, NOW + 301, self.TTL, self.FAIL))
        # A garbage header cannot park the source for a day.
        cache_util.keep_stale(cache, NOW, "HTTP Error 429", EMPTY,
                              retry_after_s=86_400)
        self.assertFalse(cache_util.fresh(
            cache, NOW + cache_util.RETRY_AFTER_CAP_S + 1,
            self.TTL, self.FAIL))

    def test_force_ignores_the_backoff(self):
        # Refresh all is a human at the wheel; the backoff is for the poller.
        cache = {}
        cache_util.store(cache, NOW, {"ok": True})
        for _ in range(10):
            self._fail(cache, NOW)
        self.assertFalse(cache_util.fresh(
            cache, NOW + 1, self.TTL, self.FAIL, force=True))


if __name__ == "__main__":
    unittest.main()
