#!/usr/bin/env python3
import base64
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import study_card
import usage_study
from test_usage_study import TZ, assistant, at, user, write

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
CARD_ID = "0123456789abcdef0123456789abcdef"


def make_root(tmp, records, name="p/a.jsonl"):
    write(tmp, name, records)
    return tmp


def one_profile(records):
    tmp = tempfile.TemporaryDirectory()
    root = make_root(tmp.name, records)
    prof = usage_study.profile(root=root, tz=TZ)
    tmp.cleanup()
    return prof


def sample_records(day=1, n=30, model="claude-opus-5", prefix="m"):
    recs = [user(at(9, 0, day), "hello there " * 20,
                 origin={"kind": "human"})]
    for i in range(n):
        recs.append(assistant(at(9 + i // 20, i % 20 * 2, day),
                              message_id=f"{prefix}{i}", out=50 + i,
                              model=model))
    return recs


def raw_card(**changes):
    card = study_card.build(one_profile(sample_records()), CARD_ID,
                            "nightowl", NOW)
    card = copy.deepcopy(card)
    card.update(changes)
    return card


def encode_raw(obj):
    body = base64.urlsafe_b64encode(
        json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")
    return study_card.PREFIX + body


class HandleTests(unittest.TestCase):
    def test_strips_controls_and_bidi_and_zero_width(self):
        s = study_card.sanitize_handle
        self.assertEqual(s("ann\x1b[31ma"), "ann[31ma")
        self.assertEqual(s("a‮b"), "ab")            # right-to-left override
        self.assertEqual(s("a​b"), "ab")            # zero-width space
        self.assertEqual(s("a  b"), "a b")     # nbsp folds, collapses
        self.assertEqual(s("  tab\there  "), "tab here")

    def test_length_is_capped(self):
        self.assertEqual(len(study_card.sanitize_handle("x" * 500)),
                         study_card.HANDLE_MAX)

    def test_nothing_usable_is_none(self):
        self.assertIsNone(study_card.sanitize_handle("​‮"))
        self.assertIsNone(study_card.sanitize_handle(""))
        self.assertIsNone(study_card.sanitize_handle(42))

    def test_generated_handle_is_stable_and_clean(self):
        a = study_card.generated_handle(CARD_ID)
        self.assertEqual(a, study_card.generated_handle(CARD_ID))
        self.assertRegex(a, r"^[a-z]+-[a-z]+$")


class CardIdTests(unittest.TestCase):
    def test_minted_once_private_and_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "study", "card_id")
            first = study_card.own_card_id(path)
            self.assertRegex(first, r"^[0-9a-f]{32}$")
            self.assertEqual(study_card.own_card_id(path), first)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_a_damaged_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "card_id")
            Path(path).write_text("not an id")
            self.assertRegex(study_card.own_card_id(path), r"^[0-9a-f]{32}$")


class RoundTripTests(unittest.TestCase):
    def test_encode_decode_round_trip(self):
        card = raw_card()
        back = study_card.decode(study_card.encode(card))
        self.assertEqual(back, card)
        self.assertEqual(back["week"], "2026-W40")
        self.assertEqual(back["handle"], "nightowl")

    def test_card_is_small(self):
        self.assertLess(len(study_card.encode(raw_card())), 2000)

    def test_no_handle_gets_a_generated_one(self):
        card = study_card.build(one_profile(sample_records()), CARD_ID, None, NOW)
        self.assertEqual(card["handle"], study_card.generated_handle(CARD_ID))

    def test_prompt_text_is_not_in_the_card(self):
        recs = sample_records()
        recs[0] = user(at(9, 0), "SECRET-SENTINEL " * 30,
                       origin={"kind": "human"})
        card = study_card.build(one_profile(recs), CARD_ID, "x", NOW)
        self.assertNotIn("SECRET-SENTINEL", json.dumps(card))

    def test_describe_is_readable(self):
        text = study_card.describe(raw_card())
        self.assertIn("nightowl", text)
        self.assertIn("2026-W40", text)


class DecodeRefusesTests(unittest.TestCase):
    def refuses(self, text):
        with self.assertRaises(study_card.CardError):
            study_card.decode(text)

    def test_wrong_prefix_and_junk(self):
        self.refuses("hello")
        self.refuses("hrc9." + "AAAA")
        self.refuses(study_card.PREFIX + "!!!not-base64!!!")
        self.refuses(study_card.PREFIX + base64.urlsafe_b64encode(
            b"[1,2]").decode())
        self.refuses(None)

    def test_size_cap(self):
        self.refuses(study_card.PREFIX + "A" * study_card.MAX_ENCODED)

    def test_unknown_top_level_key(self):
        card = raw_card()
        card["extra"] = "x"
        self.refuses(encode_raw(card))

    def test_missing_key(self):
        card = raw_card()
        del card["week"]
        self.refuses(encode_raw(card))

    def test_bad_id_week_version(self):
        self.refuses(encode_raw(raw_card(id="short")))
        self.refuses(encode_raw(raw_card(id=CARD_ID.upper())))
        self.refuses(encode_raw(raw_card(week="2026-W99")))
        self.refuses(encode_raw(raw_card(week="today")))
        self.refuses(encode_raw(raw_card(v=2)))

    def test_unknown_data_key_and_bin(self):
        card = raw_card()
        card["data"]["email"] = "a@b.c"
        self.refuses(encode_raw(card))
        card = raw_card()
        card["data"]["prompt_len_share"]["777"] = 0.5
        self.refuses(encode_raw(card))

    def test_shares_must_be_numbers_in_range(self):
        for bad in (1.5, -0.1, "0.5", True, None):
            card = raw_card()
            card["data"]["time_of_day_share"] = {"night_00_05": bad}
            self.refuses(encode_raw(card))

    def test_cache_bucket_rules(self):
        for bad in (55, -10, 110, "90", True, 90.0):
            card = raw_card()
            card["data"]["cache_hit_bucket_pct"] = bad
            self.refuses(encode_raw(card))

    def test_too_many_weeks_and_bad_week_key(self):
        card = raw_card()
        card["data"]["weekly_model_share"] = {
            f"2026-W{w:02d}": {"opus": 1.0} for w in range(1, 12)}
        self.refuses(encode_raw(card))
        card = raw_card()
        card["data"]["weekly_model_share"] = {"lastweek": {"opus": 1.0}}
        self.refuses(encode_raw(card))

    def test_unknown_model_family(self):
        card = raw_card()
        card["data"]["weekly_model_share"] = {"2026-W40": {"gpt": 1.0}}
        self.refuses(encode_raw(card))

    def test_a_hostile_handle_is_cleaned_not_refused(self):
        back = study_card.decode(encode_raw(raw_card(
            handle="ev‮il\x1b[2J" + "z" * 100)))
        self.assertNotIn("‮", back["handle"])
        self.assertNotIn("\x1b", back["handle"])
        self.assertLessEqual(len(back["handle"]), study_card.HANDLE_MAX)

    def test_an_unusable_handle_falls_back_to_generated(self):
        back = study_card.decode(encode_raw(raw_card(handle="​​")))
        self.assertEqual(back["handle"], study_card.generated_handle(CARD_ID))


class TwoMacTests(unittest.TestCase):
    """Usage on two Macs is disjoint sessions: the merge is a sum."""

    def shard(self, machine, records, generated="2026-09-30T10:00:00+02:00"):
        return usage_study.to_shard(one_profile(records), machine, generated)

    def test_two_macs_add_up(self):
        a = self.shard("mac-a", sample_records(day=1, n=30, prefix="a"))
        b = self.shard("mac-b", sample_records(day=2, n=10, prefix="b",
                                               model="claude-sonnet-5"))
        merged = usage_study.merge([a, b])
        self.assertEqual(merged["machines"], 2)
        self.assertEqual(merged["turns"], a["turns"] + b["turns"])
        self.assertEqual(merged["output"], a["output"] + b["output"])
        self.assertEqual(merged["out_by_family"]["opus"], sum(
            50 + i for i in range(30)))
        self.assertEqual(merged["out_by_family"]["sonnet"], sum(
            50 + i for i in range(10)))
        self.assertEqual(len(merged["sessions"]), 2)
        self.assertEqual(sum(merged["prompt_hist"].values()), 2)

    def test_the_same_mac_twice_does_not_double_count(self):
        old = self.shard("mac-a", sample_records(n=10),
                         generated="2026-09-29T10:00:00+02:00")
        new = self.shard("mac-a", sample_records(n=30),
                         generated="2026-09-30T10:00:00+02:00")
        merged = usage_study.merge([new, old])           # order must not matter
        self.assertEqual(merged["machines"], 1)
        self.assertEqual(merged["turns"], 30)
        self.assertEqual(usage_study.merge([old, new])["turns"], 30)

    def test_one_mac_merge_equals_its_own_profile(self):
        recs = sample_records()
        prof = one_profile(recs)
        merged = usage_study.merge([self.shard("mac-a", recs)])
        self.assertEqual(usage_study.payload(merged), usage_study.payload(prof))

    def test_card_from_two_macs_differs_from_either_alone(self):
        a = self.shard("mac-a", sample_records(day=1, n=40, prefix="a"))
        b = self.shard("mac-b", sample_records(day=2, n=40, prefix="b",
                                               model="claude-sonnet-5"))
        both = usage_study.payload(usage_study.merge([a, b]))
        only_a = usage_study.payload(usage_study.merge([a]))
        self.assertNotEqual(both["weekly_model_share"],
                            only_a["weekly_model_share"])

    def test_shard_survives_json(self):
        shard = self.shard("mac-a", sample_records())
        back = usage_study.from_shard(json.loads(json.dumps(shard)))
        self.assertEqual(back["turns"], shard["turns"])
        self.assertEqual(sorted(back["hours"]), sorted(int(k) for k in shard["hours"]))

    def test_a_bad_shard_is_refused(self):
        good = self.shard("mac-a", sample_records())
        for change in ({"version": 9}, {"machine": ""}, {"turns": -1},
                       {"turns": "12"}, {"hours": []}, {"sessions": [[1, 2]]},
                       {"out_by_week": {"2026-W40": []}}):
            bad = dict(good, **change)
            with self.assertRaises(ValueError, msg=str(change)):
                usage_study.merge([bad])

    def test_shard_holds_no_prompt_text(self):
        recs = sample_records()
        recs[0] = user(at(9, 0), "SECRET-SENTINEL " * 30,
                       origin={"kind": "human"})
        self.assertNotIn("SECRET-SENTINEL",
                         json.dumps(self.shard("mac-a", recs)))

    def test_combined_replaces_a_stale_shard_of_this_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(os.path.join(tmp, "logs"),
                             sample_records(n=30))
            stale = usage_study.to_shard(
                one_profile(sample_records(n=5)), "mac-a",
                "2000-01-01T00:00:00+00:00")
            peer = usage_study.to_shard(
                one_profile(sample_records(day=3, n=7, prefix="z")), "mac-b",
                "2026-09-30T10:00:00+02:00")
            paths = []
            for i, s in enumerate((stale, peer)):
                p = os.path.join(tmp, f"s{i}.json")
                Path(p).write_text(json.dumps(s))
                paths.append(p)
            merged = usage_study.combined(
                "mac-a", "2026-09-30T12:00:00+02:00", paths, root=root, tz=TZ)
            self.assertEqual(merged["machines"], 2)
            self.assertEqual(merged["turns"], 30 + 7)


if __name__ == "__main__":
    unittest.main()
