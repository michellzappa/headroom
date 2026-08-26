#!/usr/bin/env python3
"""The live tail's half of the one-message-one-call rule.

`headroom_server` reads each session file from a byte offset, so the blocks of
one assistant message can land in two different polls. The deduper therefore
has to outlive a single read — which is what these cover.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

import claude_history
import headroom_server


def line(message_id, *, inp=1000, out=500, model="claude-sonnet-5", when=None):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                          time.gmtime(when if when is not None else time.time()))
    message = {"model": model, "role": "assistant",
               "usage": {"input_tokens": inp, "output_tokens": out}}
    if message_id is not None:
        message["id"] = message_id
    return json.dumps({"type": "assistant", "timestamp": stamp,
                       "message": message}) + "\n"


class LiveRollupDedupeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "session.jsonl")
        self.cutoff = time.time() - headroom_server.RETENTION_S

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, text):
        with open(self.path, "a") as fh:
            fh.write(text)

    def totals(self, events):
        return sum(ev[2] for ev in events), sum(ev[3] for ev in events)

    def test_content_blocks_in_one_read_are_billed_once(self):
        self.append(line("msg_01A") * 3)
        events, _ = headroom_server._read_file(self.path, 0, self.cutoff)
        self.assertEqual(self.totals(events), (1000, 500))

    def test_a_message_split_across_two_reads_is_billed_once(self):
        deduper = claude_history.MessageDeduper()
        self.append(line("msg_01A"))
        first, offset = headroom_server._read_file(
            self.path, 0, self.cutoff, deduper)
        # Same message, more content blocks, written after the first poll.
        self.append(line("msg_01A") * 2)
        second, _ = headroom_server._read_file(
            self.path, offset, self.cutoff, deduper)
        self.assertEqual(self.totals(first + second), (1000, 500))

    def test_a_new_message_after_the_boundary_is_billed(self):
        deduper = claude_history.MessageDeduper()
        self.append(line("msg_01A") * 2)
        first, offset = headroom_server._read_file(
            self.path, 0, self.cutoff, deduper)
        self.append(line("msg_01B", inp=7))
        second, _ = headroom_server._read_file(
            self.path, offset, self.cutoff, deduper)
        self.assertEqual(self.totals(first + second), (1007, 1000))

    def test_records_without_a_message_id_are_never_collapsed(self):
        self.append(line(None) * 2)
        events, _ = headroom_server._read_file(self.path, 0, self.cutoff)
        self.assertEqual(self.totals(events), (2000, 1000))

    def test_scan_drops_the_deduper_with_the_offset(self):
        """A rotated file must not inherit the previous file's last id."""
        headroom_server._dedupers[self.path] = claude_history.MessageDeduper()
        headroom_server._offsets[self.path] = 99
        try:
            headroom_server._dedupers.pop(self.path, None)
            self.assertNotIn(self.path, headroom_server._dedupers)
        finally:
            headroom_server._offsets.pop(self.path, None)
            headroom_server._dedupers.pop(self.path, None)


class DeduperTests(unittest.TestCase):
    def test_accepts_anything_that_is_not_a_dict(self):
        deduper = claude_history.MessageDeduper()
        self.assertTrue(deduper.accept(None))
        self.assertTrue(deduper.accept("nonsense"))

    def test_only_collapses_a_repeat_of_the_line_before(self):
        deduper = claude_history.MessageDeduper()
        rec_a = {"message": {"id": "a"}}
        rec_b = {"message": {"id": "b"}}
        self.assertTrue(deduper.accept(rec_a))
        self.assertFalse(deduper.accept(rec_a))
        self.assertTrue(deduper.accept(rec_b))
        self.assertTrue(deduper.accept(rec_a))


if __name__ == "__main__":
    unittest.main()
