#!/usr/bin/env python3
"""Fixtures in the shape each tool's own source writes (see agent_logs.py)."""

import json
import os
import sqlite3
import tempfile
import unittest

import agent_logs
import study_card
import usage_study
from test_usage_study import TZ

T0 = 1_790_000_000           # 2026-09-21, epoch seconds


def iso(seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(seconds, timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")


def jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        for rec in records:
            handle.write(json.dumps(rec) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.claude = os.path.join(self.tmp, "claude")
        os.makedirs(self.claude)

    def profile(self, **roots):
        return usage_study.profile(root=self.claude, tz=TZ, agent_roots=roots)


class OpenCodeTests(Base):
    def db(self):
        root = os.path.join(self.tmp, "opencode")
        os.makedirs(root)
        con = sqlite3.connect(os.path.join(root, "opencode.db"))
        con.executescript("""
            CREATE TABLE session (id TEXT, parent_id TEXT);
            CREATE TABLE message (id TEXT, session_id TEXT,
                                  time_created INT, time_updated INT, data TEXT);
            CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,
                               data TEXT);
        """)
        con.execute("INSERT INTO session VALUES ('s1', NULL), ('s2', 's1')")

        def msg(mid, sid, data):
            con.execute("INSERT INTO message VALUES (?,?,0,0,?)",
                        (mid, sid, json.dumps(data)))
        msg("u1", "s1", {"role": "user", "time": {"created": T0 * 1000}})
        msg("a1", "s1", {
            "role": "assistant", "modelID": "claude-sonnet-4-5",
            "providerID": "anthropic",
            "time": {"created": T0 * 1000 + 5000,
                     "completed": T0 * 1000 + 9000},
            "tokens": {"total": 20640, "input": 120, "output": 380,
                       "reasoning": 140,
                       "cache": {"read": 19000, "write": 1000}}})
        # Still streaming: no completed time, so it must not count yet.
        msg("a2", "s1", {"role": "assistant", "modelID": "claude-sonnet-4-5",
                         "time": {"created": T0 * 1000 + 60000},
                         "tokens": {"input": 999, "output": 999}})
        # A subagent session.
        msg("a3", "s2", {
            "role": "assistant", "modelID": "gpt-6-luna",
            "time": {"created": T0 * 1000 + 70000,
                     "completed": T0 * 1000 + 71000},
            "tokens": {"input": 100, "output": 10, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}}})
        for pid, mid, data in (
                ("p1", "u1", {"type": "text", "text": "x" * 400}),
                ("p2", "u1", {"type": "text", "text": "injected" * 50,
                              "synthetic": True}),
                ("p3", "a1", {"type": "tool", "tool": "bash"})):
            con.execute("INSERT INTO part VALUES (?,?,?,?)",
                        (pid, mid, "s1", json.dumps(data)))
        con.commit()
        con.close()
        return root

    def test_completed_messages_with_cache_and_reasoning(self):
        prof = self.profile(opencode=self.db())
        fam = "opencode.claude-sonnet-4-5"
        self.assertEqual(prof["turns"], 2)
        self.assertEqual(prof["out_by_family"][fam], 380 + 140)
        self.assertEqual(prof["cache_read"], 19000)
        self.assertEqual(prof["cache_write"], 1000)
        self.assertEqual(prof["input"], 120 + 100)
        self.assertEqual(prof["sidechain_turns"], 1)
        self.assertEqual(prof["tools"]["bash"], 1)
        self.assertEqual(sum(prof["prompt_hist"].values()), 1)

    def test_known_openai_model_is_priced_unknown_claude_is_named(self):
        prof = self.profile(opencode=self.db())
        self.assertGreater(prof["cost_usd"], 0)            # gpt-6-luna
        self.assertIn("opencode.claude-sonnet-4-5", prof["unpriced"])


class GeminiTests(Base):
    def test_last_record_wins_and_rewind_drops(self):
        root = os.path.join(self.tmp, "gemini")
        jsonl(os.path.join(root, "proj", "chats", "session-1.jsonl"), [
            {"sessionId": "s", "projectHash": "h", "startTime": iso(T0)},
            {"id": "m1", "timestamp": iso(T0), "type": "user",
             "content": [{"text": "y" * 200}]},
            {"id": "m2", "timestamp": iso(T0 + 5), "type": "gemini",
             "content": [{"text": "ok"}], "model": "gemini-2.5-pro"},
            # Written again once the tokens arrive.
            {"id": "m2", "timestamp": iso(T0 + 5), "type": "gemini",
             "content": [{"text": "ok"}], "model": "gemini-2.5-pro",
             "toolCalls": [{"id": "c", "name": "read_file"}],
             "tokens": {"input": 18234, "output": 412, "cached": 12000,
                        "thoughts": 96, "tool": 0, "total": 18742}},
            {"id": "m3", "timestamp": iso(T0 + 9), "type": "gemini",
             "model": "gemini-2.5-pro",
             "tokens": {"input": 5, "output": 5, "cached": 0}},
            {"$rewindTo": "m2"},
            {"$set": {"lastUpdated": iso(T0 + 10)}},
        ])
        prof = self.profile(gemini=root)
        fam = "gemini.gemini-2.5-pro"
        self.assertEqual(prof["turns"], 1)
        self.assertEqual(prof["out_by_family"][fam], 412 + 96)
        self.assertEqual(prof["input"], 18234 - 12000)
        self.assertEqual(prof["cache_read"], 12000)
        self.assertEqual(prof["tools"]["read_file"], 1)
        self.assertEqual(prof["unpriced"][fam], 508)

    def test_legacy_json_document(self):
        root = os.path.join(self.tmp, "gemini")
        path = os.path.join(root, "p", "chats", "session-old.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            json.dump({"sessionId": "s", "messages": [
                {"id": "a", "timestamp": iso(T0), "type": "gemini",
                 "model": "gemini-2.5-flash",
                 "tokens": {"input": 10, "output": 3, "cached": 4}}]}, handle)
        prof = self.profile(gemini=root)
        self.assertEqual(prof["out_by_family"]["gemini.gemini-2.5-flash"], 3)


class QwenTests(Base):
    def test_branch_copies_count_once_and_injected_prompts_do_not(self):
        root = os.path.join(self.tmp, "qwen")
        assistant = {"uuid": "a1", "timestamp": iso(T0 + 3),
                     "type": "assistant", "model": "qwen3-coder-plus",
                     "message": {"role": "model", "parts": [
                         {"functionCall": {"name": "read_file"}}]},
                     "usageMetadata": {"promptTokenCount": 15000,
                                       "candidatesTokenCount": 300,
                                       "cachedContentTokenCount": 12000,
                                       "thoughtsTokenCount": 20}}
        jsonl(os.path.join(root, "-Users-x-p", "chats", "s.jsonl"), [
            {"uuid": "u1", "timestamp": iso(T0), "type": "user",
             "provenance": "real_user",
             "message": {"role": "user", "parts": [{"text": "z" * 100}]}},
            {"uuid": "u2", "timestamp": iso(T0 + 1), "type": "user",
             "provenance": "system",
             "message": {"role": "user", "parts": [{"text": "z" * 100}]}},
            assistant, assistant,
        ])
        prof = self.profile(qwen=root)
        self.assertEqual(prof["turns"], 1)
        self.assertEqual(prof["input"], 3000)
        self.assertEqual(prof["output"], 320)
        self.assertEqual(sum(prof["prompt_hist"].values()), 1)


class KimiTests(Base):
    def test_status_updates_are_steps(self):
        root = os.path.join(self.tmp, "kimi")
        step = {"type": "StatusUpdate", "payload": {"token_usage": {
            "input_other": 1200, "output": 350, "input_cache_read": 15000,
            "input_cache_creation": 0}, "message_id": "c1"}}
        jsonl(os.path.join(root, "abc", "sess", "wire.jsonl"), [
            {"timestamp": T0, "message": {"type": "TurnBegin", "payload": {}}},
            {"timestamp": T0 + 2, "message": step},
            {"timestamp": T0 + 4, "message": step},
        ])
        prof = self.profile(kimi=root)
        self.assertEqual(prof["turns"], 2)
        self.assertEqual(prof["out_by_family"]["kimi.kimi"], 700)
        self.assertEqual(prof["cache_read"], 30000)


class GooseTests(Base):
    def test_ledger_rows_and_cache_either_way(self):
        root = os.path.join(self.tmp, "goose")
        os.makedirs(root)
        con = sqlite3.connect(os.path.join(root, "sessions.db"))
        con.executescript("""
            CREATE TABLE sessions (id TEXT, session_type TEXT,
                                   parent_session_id TEXT);
            CREATE TABLE usage_ledger (session_id TEXT, created_timestamp INT,
                model TEXT, input_tokens INT, output_tokens INT,
                total_tokens INT, cache_read_tokens INT,
                cache_write_tokens INT, cost REAL);
        """)
        con.execute("INSERT INTO sessions VALUES ('g1','user',NULL),"
                    "('g2','sub_agent','g1')")
        con.execute("INSERT INTO usage_ledger VALUES "
                    "('g1', ?, 'gpt-6-luna', 1000, 50, 1050, 800, 0, 0), "
                    "('g2', ?, NULL, 100, 10, 110, 500, 0, 0)",
                    (T0, T0 + 60))
        con.commit()
        con.close()
        prof = self.profile(goose=root)
        self.assertEqual(prof["turns"], 2)
        # Input 1000 covers its 800 cached; input 100 cannot cover 500.
        self.assertEqual(prof["input"], 200 + 100)
        self.assertEqual(prof["sidechain_turns"], 1)
        self.assertIn("goose.unknown", prof["out_by_family"])


class FamilyTests(Base):
    def test_family_names_fit_a_card(self):
        for tool, model, want in (
                ("opencode", "anthropic/Claude-Sonnet-4-5-20250929",
                 "opencode.claude-sonnet-4-5"),
                ("gemini", None, "gemini.unknown"),
                ("qwen", "x" * 80, "qwen." + "x" * 27)):
            got = agent_logs.tool_family(tool, model)
            self.assertEqual(got, want)
            self.assertTrue(usage_study.CODEX_FAMILY.match(got))
            self.assertEqual(usage_study.provider_of(got), tool)
        self.assertEqual(usage_study.provider_of("gpt-6-luna"), "codex")
        self.assertEqual(usage_study.provider_of("opus"), "claude")

    def test_a_card_carries_other_tools(self):
        root = os.path.join(self.tmp, "kimi")
        jsonl(os.path.join(root, "a", "s", "wire.jsonl"), [
            {"timestamp": T0, "message": {"type": "StatusUpdate", "payload": {
                "token_usage": {"input_other": 1, "output": 5}}}}])
        card = study_card.build(self.profile(kimi=root), "0" * 32, "n")
        self.assertEqual(card["v"], 2)
        decoded = study_card.decode(study_card.encode(card))
        shares = next(iter(decoded["data"]["weekly_model_share"].values()))
        self.assertIn("kimi.kimi", shares)

    def test_missing_roots_and_junk_files_are_harmless(self):
        junk = os.path.join(self.tmp, "opencode")
        os.makedirs(junk)
        with open(os.path.join(junk, "opencode.db"), "w") as handle:
            handle.write("not sqlite")
        self.assertIsNone(self.profile(opencode=junk,
                                       gemini="/nonexistent"))


if __name__ == "__main__":
    unittest.main()
