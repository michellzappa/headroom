#!/usr/bin/env python3
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import study_card
import study_service
from test_study_card import sample_records
from test_usage_study import TZ, write


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def mac(self, name, records=None, clock=None):
        logs = os.path.join(self.tmp, name, "logs")
        os.makedirs(logs, exist_ok=True)
        if records is not None:
            write(logs, "p/a.jsonl", records)
        return study_service.StudyService(
            log_root=logs, state_dir=os.path.join(self.tmp, name, "study"),
            clock=clock or Clock(), tz=TZ, machine=lambda n=name: n,
            machine_name=lambda n=name: n.title())

    def ready(self, svc):
        svc.refresh(wait=True)
        return svc.snapshot()


class SnapshotTests(ServiceCase):
    def test_first_call_does_not_block_and_then_settles(self):
        svc = self.mac("a", sample_records())
        first = svc.snapshot()
        self.assertIn(first["status"], ("scanning", "ready"))
        if svc._thread is not None:
            svc._thread.join()
        snap = svc.snapshot()
        self.assertEqual(snap["status"], "ready")
        self.assertEqual(snap["insights"]["turns"], 30)
        self.assertTrue(snap["card_text"].startswith(study_card.PREFIX))
        self.assertEqual(study_card.decode(snap["card_text"])["id"],
                         snap["card"]["id"])
        self.assertEqual(snap["payload"]["schema"], "study-1")

    def test_no_logs_and_no_peers_is_empty(self):
        snap = self.ready(self.mac("a"))
        self.assertEqual(snap["status"], "empty")
        self.assertIsNone(snap["insights"])

    def test_changed_logs_wait_for_the_rescan_floor(self):
        clock = Clock()
        svc = self.mac("a", sample_records(n=10), clock)
        self.assertEqual(self.ready(svc)["insights"]["turns"], 10)
        write(svc.log_root, "p/a.jsonl", sample_records(n=30))
        svc.snapshot()
        if svc._thread is not None:
            svc._thread.join()
        self.assertEqual(svc.snapshot()["insights"]["turns"], 10)   # too soon
        clock.t += study_service.MIN_RESCAN_S + 1
        svc.snapshot()
        thread = svc._thread          # a fast scan clears it before we look
        if thread is not None:
            thread.join()
        self.assertEqual(svc.snapshot()["insights"]["turns"], 30)

    def test_a_scan_failure_is_reported_not_raised(self):
        svc = self.mac("a", sample_records())
        svc._tz = "not a timezone"           # makes the scan raise
        snap = self.ready(svc)
        self.assertIsNotNone(snap["error"])
        self.assertIn(snap["status"], ("empty", "ready"))

    def test_snapshot_carries_no_prompt_text(self):
        recs = sample_records()
        from test_usage_study import user, at
        recs[0] = user(at(9, 0), "SECRET-SENTINEL " * 30,
                       origin={"kind": "human"})
        snap = self.ready(self.mac("a", recs))
        self.assertNotIn("SECRET-SENTINEL", json.dumps(snap))


class HandleTests(ServiceCase):
    def test_default_is_generated_and_stable(self):
        svc = self.mac("a", sample_records())
        first = svc.handle()
        self.assertRegex(first, r"^[a-z]+-[a-z]+$")
        self.assertEqual(svc.handle(), first)

    def test_set_clear_and_reject(self):
        svc = self.mac("a", sample_records())
        self.assertEqual(svc.set_handle("  night‮owl ")["handle"], "nightowl")
        self.assertEqual(self.ready(svc)["card"]["handle"], "nightowl")
        generated = svc.set_handle(None)["handle"]
        self.assertRegex(generated, r"^[a-z]+-[a-z]+$")
        with self.assertRaises(study_service.StudyError):
            svc.set_handle("​​")
        with self.assertRaises(study_service.StudyError):
            svc.set_handle(42)

    def test_state_files_are_private(self):
        svc = self.mac("a", sample_records())
        svc.set_handle("x")
        mode = stat.S_IMODE(os.stat(os.path.join(svc.dir, "state.json")).st_mode)
        self.assertEqual(mode, 0o600)


class FriendTests(ServiceCase):
    def friend_card(self, name="anna", handle="anna", n=20):
        other = self.mac(name, sample_records(n=n, prefix=name))
        other.set_handle(handle)
        return self.ready(other)["card_text"]

    def test_add_list_and_replace_keeps_the_alias(self):
        me = self.mac("me", sample_records())
        text = self.friend_card()
        added = me.add_friend(text)
        self.assertFalse(added["updated"])
        fid = added["friend"]["id"]
        me.set_alias(fid, "Anna (opus goblin)")
        again = me.add_friend(self.friend_card(n=40))     # same sender, new week
        self.assertTrue(again["updated"])
        snap = self.ready(me)
        self.assertEqual(len(snap["friends"]), 1)
        self.assertEqual(snap["friends"][0]["name"], "Anna (opus goblin)")
        self.assertEqual(snap["friends"][0]["handle"], "anna")

    def test_your_own_card_is_refused(self):
        me = self.mac("me", sample_records())
        with self.assertRaises(study_service.StudyError):
            me.add_friend(self.ready(me)["card_text"])

    def test_bad_cards_are_refused(self):
        me = self.mac("me", sample_records())
        for bad in ("hello", "", None, study_card.PREFIX + "AAAA"):
            with self.assertRaises(study_service.StudyError):
                me.add_friend(bad)
        self.assertEqual(self.ready(me)["friends"], [])

    def test_alias_is_cleaned_and_can_be_cleared(self):
        me = self.mac("me", sample_records())
        fid = me.add_friend(self.friend_card())["friend"]["id"]
        self.assertEqual(me.set_alias(fid, "a‮b")["friend"]["alias"], "ab")
        self.assertIsNone(me.set_alias(fid, None)["friend"]["alias"])
        with self.assertRaises(study_service.StudyError):
            me.set_alias(fid, "​")
        with self.assertRaises(study_service.StudyError):
            me.set_alias("nope", "x")

    def test_remove(self):
        me = self.mac("me", sample_records())
        fid = me.add_friend(self.friend_card())["friend"]["id"]
        me.remove_friend(fid)
        self.assertEqual(self.ready(me)["friends"], [])
        with self.assertRaises(study_service.StudyError):
            me.remove_friend(fid)

    def test_a_hand_edited_friends_file_cannot_smuggle_anything(self):
        me = self.mac("me", sample_records())
        fid = me.add_friend(self.friend_card())["friend"]["id"]
        path = os.path.join(me.dir, "friends.json")
        data = json.loads(Path(path).read_text())
        data[fid]["card"] = "hrc1.garbage"
        data["f" * 32] = {"card": 7}
        data["e" * 32] = "not a row"
        Path(path).write_text(json.dumps(data))
        self.assertEqual(self.ready(me)["friends"], [])

    def test_the_friend_cap(self):
        me = self.mac("me", sample_records())
        old = study_service.MAX_FRIENDS
        study_service.MAX_FRIENDS = 2
        self.addCleanup(setattr, study_service, "MAX_FRIENDS", old)
        me.add_friend(self.friend_card("f1", "one"))
        me.add_friend(self.friend_card("f2", "two"))
        with self.assertRaises(study_service.StudyError):
            me.add_friend(self.friend_card("f3", "three"))

    def test_friends_are_sorted_by_the_name_you_see(self):
        me = self.mac("me", sample_records())
        me.add_friend(self.friend_card("f1", "zed"))
        me.add_friend(self.friend_card("f2", "amy"))
        names = [f["name"] for f in self.ready(me)["friends"]]
        self.assertEqual(names, ["amy", "zed"])


class TwoMacTests(ServiceCase):
    def test_a_shard_from_the_other_mac_makes_one_person(self):
        laptop = self.mac("laptop", sample_records(day=1, n=30, prefix="l"))
        desk = self.mac("desk", sample_records(day=2, n=10, prefix="d"))
        desk.import_shard(laptop.export_shard())
        snap = self.ready(desk)
        self.assertEqual(snap["insights"]["turns"], 40)
        self.assertEqual(snap["insights"]["machines"], 2)
        self.assertEqual([m["name"] for m in snap["machines"]],
                         ["Desk", "Laptop"])
        self.assertTrue(snap["machines"][0]["this_mac"])

    def test_this_mac_alone_still_works_with_a_peer_that_has_no_logs(self):
        laptop = self.mac("laptop", sample_records(n=30))
        desk = self.mac("desk")                       # no logs at all
        desk.import_shard(laptop.export_shard())
        snap = self.ready(desk)
        self.assertEqual(snap["insights"]["turns"], 30)
        self.assertFalse(snap["machines"][0]["has_usage"])

    def test_the_same_mac_is_refused_and_bad_shards_are_refused(self):
        desk = self.mac("desk", sample_records())
        with self.assertRaises(study_service.StudyError):
            desk.import_shard(desk.export_shard())
        for bad in ({}, {"version": 1}, "x", None, []):
            with self.assertRaises(study_service.StudyError):
                desk.import_shard(bad)

    def test_importing_twice_replaces_and_an_older_shard_is_refused(self):
        laptop = self.mac("laptop", sample_records(n=10))
        desk = self.mac("desk", sample_records(day=2, n=5, prefix="d"))
        new = laptop.export_shard()
        old = dict(new, generated="2000-01-01T00:00:00+00:00", turns=999)
        desk.import_shard(new)
        desk.import_shard(new)
        with self.assertRaises(study_service.StudyError):
            desk.import_shard(old)
        self.assertEqual(self.ready(desk)["insights"]["turns"], 15)
        self.assertEqual(len(os.listdir(os.path.join(desk.dir, "shards"))), 1)

    def test_remove_a_mac(self):
        laptop = self.mac("laptop", sample_records(n=10))
        desk = self.mac("desk", sample_records(day=2, n=5, prefix="d"))
        desk.import_shard(laptop.export_shard())
        desk.remove_shard("laptop")
        self.assertEqual(self.ready(desk)["insights"]["turns"], 5)
        with self.assertRaises(study_service.StudyError):
            desk.remove_shard("laptop")

    def test_a_hostile_machine_id_cannot_become_a_path(self):
        laptop = self.mac("laptop", sample_records(n=10))
        desk = self.mac("desk", sample_records(day=2, n=5, prefix="d"))
        shard = dict(laptop.export_shard(), machine="../../etc/passwd")
        desk.import_shard(shard)
        names = os.listdir(os.path.join(desk.dir, "shards"))
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^[0-9a-f]{24}\.json$")

    def test_a_hostile_mac_name_is_cleaned(self):
        laptop = self.mac("laptop", sample_records(n=10))
        desk = self.mac("desk", sample_records(day=2, n=5, prefix="d"))
        desk.import_shard(dict(laptop.export_shard(), name="x‮y\x1b[2J"))
        names = [m["name"] for m in self.ready(desk)["machines"]]
        self.assertNotIn("‮", "".join(names))
        self.assertNotIn("\x1b", "".join(names))

    def test_a_corrupt_shard_on_disk_is_skipped(self):
        laptop = self.mac("laptop", sample_records(n=10))
        desk = self.mac("desk", sample_records(day=2, n=5, prefix="d"))
        desk.import_shard(laptop.export_shard())
        shard_file = os.path.join(desk.dir, "shards",
                                  os.listdir(os.path.join(desk.dir, "shards"))[0])
        Path(shard_file).write_text("{not json")
        self.assertEqual(self.ready(desk)["insights"]["turns"], 5)

    def test_the_mac_cap(self):
        desk = self.mac("desk", sample_records())
        old = study_service.MAX_SHARDS
        study_service.MAX_SHARDS = 1
        self.addCleanup(setattr, study_service, "MAX_SHARDS", old)
        a = self.mac("a", sample_records(n=3, prefix="a"))
        b = self.mac("b", sample_records(n=3, prefix="b"))
        desk.import_shard(a.export_shard())
        with self.assertRaises(study_service.StudyError):
            desk.import_shard(b.export_shard())
        desk.import_shard(a.export_shard())         # replacing is not adding


if __name__ == "__main__":
    unittest.main()
