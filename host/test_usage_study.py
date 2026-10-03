#!/usr/bin/env python3
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import usage_study

TZ = ZoneInfo("Europe/Berlin")
SECRET = "SECRET-SENTINEL-do-not-leak"


def stamp(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def assistant(when, *, model="claude-opus-5", out=100, inp=10,
              cache_read=1000, message_id=None, tool=None, sidechain=False):
    content = [{"type": "text", "text": "ok"}]
    if tool:
        content.append({"type": "tool_use", "name": tool})
    message = {"model": model, "content": content, "usage": {
        "input_tokens": inp, "output_tokens": out,
        "cache_read_input_tokens": cache_read,
        "cache_creation": {"ephemeral_5m_input_tokens": 0}}}
    if message_id:
        message["id"] = message_id
    rec = {"type": "assistant", "timestamp": stamp(when), "message": message}
    if sidechain:
        rec["isSidechain"] = True
    return rec


def user(when, text, **extra):
    rec = {"type": "user", "timestamp": stamp(when),
           "message": {"content": text}}
    rec.update(extra)
    return rec


def write(root, name, records):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def at(hour, minute=0, day=1):
    return datetime(2026, 7, day, hour, minute, tzinfo=TZ)


class HelperTests(unittest.TestCase):
    def test_family_reads_the_name_not_a_substring(self):
        f = usage_study.model_family
        self.assertEqual(f("claude-opus-5-5"), "opus")
        self.assertEqual(f("claude-fable-5-1"), "fable")
        self.assertEqual(f("claude-haiku-4-5-20251001"), "haiku")
        self.assertEqual(f("claude-sonnet-5"), "sonnet")
        self.assertEqual(f("gpt-5"), "other")
        self.assertEqual(f("something-opus-in-the-middle"), "other")
        self.assertEqual(f(None), "other")

    def test_bucket_is_log2(self):
        b = usage_study.bucket
        self.assertEqual([b(0), b(1), b(3), b(4), b(1000)],
                         ["0", "1", "2-3", "4-7", "512-1023"])

    def test_coarse_merges_into_lower_bounds(self):
        hist = {"1": 2, "4-7": 3, "8-15": 1, "16-31": 4, "2048-4095": 5,
                "131072-262143": 1}
        out = usage_study.coarse(hist, usage_study.PROMPT_EDGES)
        self.assertEqual(out[1], 5)
        self.assertEqual(out[8], 5)
        self.assertEqual(out[2048], 6)          # the whole tail is one bin

    def test_shares_round_and_drop_small_bins(self):
        out = usage_study.shares({"a": 97, "b": 2, "c": 1})
        self.assertEqual(out, {"a": 0.95})      # 2% and 1% round to zero
        self.assertEqual(usage_study.shares({}), {})

    def test_human_prompt_uses_origin_when_present(self):
        h = usage_study.is_human_prompt
        self.assertTrue(h({"origin": {"kind": "human"}}, "<looks like markup"))
        self.assertFalse(h({"origin": {"kind": "task-notification"}}, "fix it"))
        self.assertFalse(h({"origin": {"kind": "peer"}}, "fix it"))

    def test_human_prompt_falls_back_for_logs_without_origin(self):
        h = usage_study.is_human_prompt
        self.assertTrue(h({}, "please fix the build"))
        self.assertFalse(h({"isMeta": True}, "please fix the build"))
        self.assertFalse(h({"isCompactSummary": True}, "summary"))
        self.assertFalse(h({}, "  <command-name>/model</command-name>"))


class ProfileTests(unittest.TestCase):
    def profile(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, records in files.items():
            write(tmp.name, name, records)
        return usage_study.profile(root=tmp.name, tz=TZ)

    def test_empty_tree_is_none(self):
        self.assertIsNone(self.profile({}))

    def test_active_time_ignores_idle_gaps(self):
        prof = self.profile({"p/a.jsonl": [
            assistant(at(9, 0), message_id="m1"),
            assistant(at(9, 5), message_id="m2"),
            assistant(at(20, 0), message_id="m3"),    # 11 h later: idle
            assistant(at(20, 4), message_id="m4"),
        ]})
        self.assertEqual(len(prof["sessions"]), 1)
        self.assertAlmostEqual(prof["sessions"][0]["active_min"], 9.0)

    def test_a_repeated_message_counts_once(self):
        prof = self.profile({"p/a.jsonl": [
            assistant(at(9, 0), message_id="m1", out=100),
            assistant(at(9, 0), message_id="m1", out=100),   # next content block
        ]})
        self.assertEqual(prof["turns"], 1)
        self.assertEqual(prof["output"], 100)

    def test_only_human_prompts_are_measured(self):
        prof = self.profile({"p/a.jsonl": [
            user(at(9, 0), "x" * 400, origin={"kind": "human"}),
            user(at(9, 1), "y" * 4000, origin={"kind": "task-notification"}),
            user(at(9, 2), "z" * 4000, isMeta=True),
            assistant(at(9, 3), message_id="m1"),
        ]})
        self.assertEqual(sum(prof["prompt_hist"].values()), 1)
        self.assertEqual(prof["prompt_hist"]["64-127"], 1)   # 400 chars ~ 100

    def test_tool_results_are_not_prompts(self):
        prof = self.profile({"p/a.jsonl": [
            {"type": "user", "timestamp": stamp(at(9, 0)), "message": {
                "content": [{"type": "tool_result", "content": "x" * 900}]}},
            assistant(at(9, 1), message_id="m1"),
        ]})
        self.assertEqual(sum(prof["prompt_hist"].values()), 0)

    def test_synthetic_model_is_skipped(self):
        prof = self.profile({"p/a.jsonl": [
            assistant(at(9, 0), model="<synthetic>", message_id="m1"),
            assistant(at(9, 1), message_id="m2"),
        ]})
        self.assertEqual(prof["turns"], 1)

    def test_families_tools_and_subagents(self):
        prof = self.profile({"p/a.jsonl": [
            assistant(at(9, 0), model="claude-fable-5", out=30, message_id="a",
                      tool="Bash"),
            assistant(at(9, 1), model="claude-sonnet-5", out=10, message_id="b",
                      tool="mcp__x__y", sidechain=True),
        ]})
        self.assertEqual(prof["out_by_family"]["fable"], 30)
        self.assertEqual(prof["out_by_family"]["sonnet"], 10)
        self.assertEqual(prof["tools"], {"Bash": 1, "mcp": 1})
        self.assertEqual(prof["sidechain_turns"], 1)

    def test_local_timezone_decides_the_hour(self):
        prof = self.profile({"p/a.jsonl": [assistant(at(23, 30), message_id="a")]})
        self.assertEqual(prof["hours"][23], 1)     # not the UTC hour


class PayloadTests(unittest.TestCase):
    def build(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        records = [user(at(9, 0), SECRET + " " * 300, origin={"kind": "human"})]
        for i in range(40):
            records.append(assistant(at(9 + i // 20, i % 20 * 2),
                                     message_id=f"m{i}", out=50 + i))
        write(tmp.name, "p/a.jsonl", records)
        return usage_study.profile(root=tmp.name, tz=TZ)

    def test_payload_holds_shares_and_no_counts(self):
        out = usage_study.payload(self.build())
        self.assertEqual(out["schema"], usage_study.SCHEMA)
        for key in ("prompt_len_share", "out_per_turn_share",
                    "time_of_day_share", "session_active_min_share"):
            for value in out[key].values():
                self.assertLessEqual(value, 1.0, key)
        self.assertIn(out["cache_hit_bucket_pct"], range(0, 101, 10))

    def test_payload_has_only_the_documented_keys(self):
        out = usage_study.payload(self.build())
        self.assertEqual(set(out), {
            "schema", "prompt_len_share", "out_per_turn_share",
            "time_of_day_share", "session_active_min_share",
            "cache_hit_bucket_pct", "weekly_model_share"})

    def test_weeks_are_iso_weeks_and_capped(self):
        out = usage_study.payload(self.build())
        for week in out["weekly_model_share"]:
            self.assertRegex(week, r"^\d{4}-W\d{2}$")
        self.assertLessEqual(len(out["weekly_model_share"]),
                             usage_study.PAYLOAD_WEEKS)

    def test_prompt_text_reaches_neither_payload_nor_report(self):
        prof = self.build()
        self.assertNotIn(SECRET, json.dumps(usage_study.payload(prof)))
        self.assertNotIn(SECRET, usage_study.report(prof))

    def test_report_labels_dollars_as_an_estimate(self):
        self.assertIn("estimate", usage_study.report(self.build()))


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------ Codex


def codex_rec(when, kind, payload):
    return {"timestamp": stamp(when), "type": kind, "payload": payload}


def codex_tokens(when, total, last=None):
    def usage(t):
        inp, cached, out = t
        return {"input_tokens": inp, "cached_input_tokens": cached,
                "cache_write_input_tokens": 0, "output_tokens": out,
                "reasoning_output_tokens": 0, "total_tokens": inp + out}
    return codex_rec(when, "event_msg", {
        "type": "token_count",
        "info": {"total_token_usage": usage(total),
                 "last_token_usage": usage(last or total)}})


def codex_user(when, text):
    return codex_rec(when, "response_item", {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": text}]})


def codex_session(model="gpt-6-luna"):
    return [
        codex_rec(at(9), "session_meta", {"id": "s"}),
        codex_rec(at(9), "turn_context", {"model": model}),
        codex_user(at(9), "<environment_context>cwd</environment_context>"),
        codex_user(at(9), "# AGENTS.md instructions for /x"),
        codex_user(at(9, 1), "fix the failing test please " * 4),
        codex_rec(at(9, 2), "response_item",
                  {"type": "custom_tool_call", "name": "apply_patch"}),
        codex_rec(at(9, 2), "response_item",
                  {"type": "function_call", "name": "js",
                   "namespace": "mcp__cua_repl"}),
        codex_tokens(at(9, 2), (1000, 800, 100)),
        codex_tokens(at(9, 2), (1000, 800, 100)),          # a repeat
        codex_tokens(at(9, 3), (3000, 2000, 300)),
        # Compaction restarts the totals; the event's own last usage counts.
        codex_tokens(at(9, 4), (500, 0, 50), last=(500, 0, 50)),
        codex_rec(at(9, 4), "event_msg", {"type": "token_count", "info": None}),
    ]


class CodexTests(unittest.TestCase):
    def scan(self, records, claude=None, model="gpt-6-luna"):
        tmp = tempfile.mkdtemp()
        codex = Path(tmp) / "codex"
        write(codex, "2026/07/01/rollout-a.jsonl", records)
        claude_root = Path(tmp) / "claude"
        claude_root.mkdir()
        if claude:
            write(claude_root, "p/a.jsonl", claude)
        return usage_study.profile(root=str(claude_root), tz=TZ,
                                   codex_roots=(str(codex),))

    def test_deltas_repeats_and_compaction(self):
        prof = self.scan(codex_session())
        self.assertEqual(prof["turns"], 3)
        self.assertEqual(prof["output"], 100 + 200 + 50)
        self.assertEqual(prof["cache_read"], 800 + 1200)
        self.assertEqual(prof["input"], 200 + 800 + 500)
        self.assertEqual(prof["out_by_family"]["gpt-6-luna"], 350)

    def test_only_typed_prompts_count(self):
        prof = self.scan(codex_session())
        self.assertEqual(sum(prof["prompt_hist"].values()), 1)
        self.assertEqual(prof["tools"]["apply_patch"], 1)
        self.assertEqual(prof["tools"]["mcp"], 1)

    def test_priced_from_the_openai_table(self):
        prof = self.scan(codex_session())
        luna = (1500 * 0.10 + 2000 * 0.01 + 350 * 0.50) / 1e6
        self.assertAlmostEqual(prof["cost_usd"], luna, places=9)
        self.assertEqual(sum(prof["unpriced"].values()), 0)

    def test_unpriced_model_is_named_not_guessed(self):
        prof = self.scan(codex_session(model="codex-auto-review"))
        self.assertEqual(prof["cost_usd"], 0)
        self.assertEqual(prof["unpriced"]["codex-auto-review"], 350)
        self.assertEqual(prof["sidechain_turns"], 3)       # the reviewer
        d = usage_study.insights(prof)
        self.assertEqual(d["unpriced_models"], ["codex-auto-review"])

    def test_combined_with_claude(self):
        prof = self.scan(codex_session(),
                         claude=[assistant(at(10), out=40)])
        d = usage_study.insights(prof)
        self.assertEqual(d["turns"], 4)
        providers = {m["family"]: m["provider"] for m in d["models"]}
        self.assertEqual(providers, {"opus": "claude", "gpt-6-luna": "codex"})
        self.assertEqual(d["monthly"][0]["output"],
                         {"opus": 40, "gpt-6-luna": 350})
        self.assertEqual(d["files"], 2)

    def test_hostile_model_name_is_other(self):
        prof = self.scan(codex_session(model="../../Evil Model"))
        self.assertEqual(set(prof["out_by_family"]), {"other"})

    def test_a_named_claude_root_reads_no_codex_by_default(self):
        tmp = tempfile.mkdtemp()
        write(tmp, "p/a.jsonl", [assistant(at(10))])
        self.assertEqual(usage_study.profile(root=tmp, tz=TZ)["turns"], 1)

    def test_codex_survives_a_shard_round_trip(self):
        prof = self.scan(codex_session(model="codex-auto-review"))
        shard = usage_study.to_shard(prof, "m", "2026-07-01T10:00:00")
        back = usage_study.from_shard(json.loads(json.dumps(shard)))
        self.assertEqual(back["out_by_family"]["codex-auto-review"], 350)
        self.assertEqual(back["unpriced"]["codex-auto-review"], 350)
