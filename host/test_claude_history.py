#!/usr/bin/env python3
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import claude_history


TZ = ZoneInfo("Europe/Berlin")


def record(when, *, model="claude-sonnet-5", inp=100, out=50,
           cache_read=0, cache_write=0, message_id=None):
    usage = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cache_read,
        "cache_creation": {"ephemeral_5m_input_tokens": cache_write},
    }
    message = {"model": model, "usage": usage}
    if message_id is not None:
        message["id"] = message_id
    return json.dumps({
        "timestamp": when.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"),
        "message": message,
    })


class RecordParsingTests(unittest.TestCase):
    def test_reads_tokens_and_prices_them(self):
        line = record(datetime(2026, 7, 1, 12, 0, tzinfo=TZ),
                      inp=1000, out=500)
        parsed = claude_history.usage_from_record(json.loads(line))
        self.assertIsNotNone(parsed)
        _, model, inp, out, _, _, _, cost = parsed
        self.assertEqual((model, inp, out), ("claude-sonnet-5", 1000, 500))
        self.assertGreater(cost, 0)

    def test_falls_back_to_flat_cache_field(self):
        rec = {
            "timestamp": "2026-07-01T10:00:00.000Z",
            "message": {"model": "claude-sonnet-5", "usage": {
                "input_tokens": 10, "cache_creation_input_tokens": 400}},
        }
        parsed = claude_history.usage_from_record(rec)
        self.assertEqual(parsed[5], 400)   # 5m cache write

    def test_records_without_usage_are_skipped(self):
        self.assertIsNone(claude_history.usage_from_record(
            {"timestamp": "2026-07-01T10:00:00.000Z", "message": {}}))
        self.assertIsNone(claude_history.usage_from_record({"message": {}}))
        self.assertIsNone(claude_history.usage_from_record(None))

    def test_unparseable_timestamp_is_skipped(self):
        self.assertIsNone(claude_history.usage_from_record(
            {"timestamp": "not-a-date",
             "message": {"usage": {"input_tokens": 1}}}))


class BackfillTests(unittest.TestCase):
    def setUp(self):
        claude_history.reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        (self.root / "alpha").mkdir(parents=True)
        self.store = str(Path(self.tmp.name) / "claude_history.json")
        self.patches = [
            patch.object(claude_history, "STORE_PATH", self.store),
            patch.object(claude_history, "LOG_ROOT", str(self.root)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        claude_history.reset_for_tests()

    def write(self, name, lines):
        path = self.root / "alpha" / name
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_aggregates_a_day(self):
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [
            record(day, inp=1000, out=500, cache_read=200),
            record(day.replace(minute=5), inp=300, out=100),
        ])
        claude_history.backfill(tz=TZ)
        rows = claude_history.series(days=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-07-01")
        self.assertEqual(rows[0]["input"], 1300)
        self.assertEqual(rows[0]["output"], 600)
        self.assertEqual(rows[0]["cache_read"], 200)

    def test_splits_days_in_local_time(self):
        self.write("a.jsonl", [
            record(datetime(2026, 7, 1, 23, 30, tzinfo=TZ)),
            record(datetime(2026, 7, 2, 0, 30, tzinfo=TZ)),
        ])
        claude_history.backfill(tz=TZ)
        self.assertEqual([r["date"] for r in claude_history.series(days=10)],
                         ["2026-07-01", "2026-07-02"])

    def test_counts_sessions_by_gap(self):
        base = datetime(2026, 7, 1, 9, 0, tzinfo=TZ)
        self.write("a.jsonl", [
            record(base),
            record(base.replace(minute=10)),
            # 90 minutes later — a new session.
            record(base.replace(hour=11)),
        ])
        claude_history.backfill(tz=TZ)
        row = claude_history.series(days=10)[0]
        self.assertEqual(row["sessions"], 2)
        self.assertEqual(row["active_minutes"], 3)

    def test_merges_across_files(self):
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [record(day, inp=100)])
        self.write("b.jsonl", [record(day.replace(minute=30), inp=400)])
        claude_history.backfill(tz=TZ)
        self.assertEqual(claude_history.series(days=10)[0]["input"], 500)

    def test_second_pass_is_a_no_op(self):
        self.write("a.jsonl", [record(datetime(2026, 7, 1, 12, 0, tzinfo=TZ))])
        claude_history.backfill(tz=TZ)
        again = claude_history.backfill(tz=TZ)
        self.assertEqual(again["scanned"], 0)
        self.assertTrue(again["done"])

    def test_new_file_is_picked_up_without_double_counting(self):
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [record(day, inp=100)])
        claude_history.backfill(tz=TZ)
        self.write("b.jsonl", [record(day.replace(minute=30), inp=100)])
        claude_history.reset_for_tests()
        claude_history.backfill(tz=TZ)
        self.assertEqual(claude_history.series(days=10)[0]["input"], 200)

    def test_one_message_is_billed_once_across_its_content_blocks(self):
        """Claude Code writes one line per content block, repeating the usage.

        Thinking, text and each tool_use are separate JSONL records carrying
        the same message.id and the same message.usage. They are one API call.
        """
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [
            record(day, inp=1000, out=500, message_id="msg_01A"),
            record(day, inp=1000, out=500, message_id="msg_01A"),
            record(day, inp=1000, out=500, message_id="msg_01A"),
        ])
        claude_history.backfill(tz=TZ)
        row = claude_history.series(days=10)[0]
        self.assertEqual(row["input"], 1000)
        self.assertEqual(row["output"], 500)
        self.assertEqual(row["total"], 1500)

    def test_distinct_messages_still_add_up(self):
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [
            record(day, inp=100, message_id="msg_01A"),
            record(day, inp=100, message_id="msg_01A"),
            record(day, inp=100, message_id="msg_01B"),
        ])
        claude_history.backfill(tz=TZ)
        self.assertEqual(claude_history.series(days=10)[0]["input"], 200)

    def test_subagent_messages_are_kept(self):
        """Sidechain runs carry their own message ids — separate API calls."""
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [
            record(day, inp=100, message_id="msg_main"),
            record(day, inp=100, message_id="msg_main"),
            record(day, inp=700, message_id="msg_sidechain"),
        ])
        claude_history.backfill(tz=TZ)
        self.assertEqual(claude_history.series(days=10)[0]["input"], 800)

    def test_records_without_a_message_id_are_never_collapsed(self):
        """Older logs carry no message.id; two of them are two calls."""
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [record(day, inp=100), record(day, inp=100)])
        claude_history.backfill(tz=TZ)
        self.assertEqual(claude_history.series(days=10)[0]["input"], 200)

    def test_the_same_id_in_two_files_is_two_messages(self):
        """The deduper is per file, matching what the log tree actually does."""
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [record(day, inp=100, message_id="msg_01A")])
        self.write("b.jsonl", [record(day, inp=100, message_id="msg_01A")])
        claude_history.backfill(tz=TZ)
        self.assertEqual(claude_history.series(days=10)[0]["input"], 200)

    def test_corrupt_lines_do_not_abort_the_file(self):
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", ["{broken", record(day, inp=250), "also broken"])
        claude_history.backfill(tz=TZ)
        self.assertEqual(claude_history.series(days=10)[0]["input"], 250)

    def test_summary_reports_cache_efficiency(self):
        day = datetime(2026, 7, 1, 12, 0, tzinfo=TZ)
        self.write("a.jsonl", [record(day, inp=250, cache_read=750)])
        claude_history.backfill(tz=TZ)
        summary = claude_history.summary(days=10)
        self.assertEqual(summary["active_days"], 1)
        self.assertEqual(summary["cache_hit_pct"], 75.0)
        self.assertEqual(summary["top_models"][0]["model"], "claude-sonnet-5")

    def test_summary_is_none_without_history(self):
        claude_history.backfill(tz=TZ)
        self.assertIsNone(claude_history.summary(days=10))

    def test_missing_log_root_is_survivable(self):
        with patch.object(claude_history, "LOG_ROOT", "/nope/does/not/exist"):
            result = claude_history.backfill(tz=TZ)
        self.assertIsNone(result["error"])
        self.assertEqual(claude_history.series(days=10), [])


if __name__ == "__main__":
    unittest.main()
