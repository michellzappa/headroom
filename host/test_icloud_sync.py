"""Multi-Mac sync: the merge rules, and two machines actually agreeing.

The interesting failures here are not parse errors, they are loops — two Macs
trading the same setting back and forth forever because each reads the other's
normalization as a fresh edit. So the centrepiece is `_Machine`, which gives
each simulated Mac its own `~/.headroom` and its own id over one shared folder,
and the assertions are about what happens on the *second* and *third* round.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import app_config
import icloud_sync
import machine_identity
import shared_prefs
import sources_config


class _Machine:
    """One simulated Mac: private stores, shared folder, explicit clock."""

    def __init__(self, root, share, name):
        self.home = os.path.join(root, name)
        os.makedirs(self.home, exist_ok=True)
        self.name = name
        self.share = share
        self.patchers = [
            mock.patch.object(
                app_config, "STORE_PATH",
                os.path.join(self.home, "config.json")),
            mock.patch.object(
                sources_config, "STORE_PATH",
                os.path.join(self.home, "sources.json")),
            mock.patch.object(
                icloud_sync, "STATE_PATH",
                os.path.join(self.home, "icloud_state.json")),
            mock.patch.object(
                machine_identity, "STORE_PATH",
                os.path.join(self.home, "machine.json")),
        ]

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        # Every module caches its file in memory; entering a machine means
        # forgetting the last one's.
        app_config.reload()
        machine_identity.reload()
        sources_config._state = None
        icloud_sync._last_write.update(payload=None, at=0.0)
        icloud_sync._peers.clear()
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self.patchers):
            patcher.stop()
        return False

    def enable_sync(self):
        app_config._persist(icloud_sync=True, icloud_dir=self.share)

    def tick(self, now):
        return icloud_sync.tick(beacon={"servers": 1}, now=now)


class MergeTests(unittest.TestCase):
    """`_reconcile` in isolation — no files, no folder."""

    def test_local_edit_is_stamped(self):
        state = {"mirror": {"a": 1}, "stamps": {"a": 10.0}}
        updates, winners, stamps = icloud_sync._reconcile(
            {"a": 2}, state, [], now=99.0)
        self.assertEqual(updates, {})
        self.assertEqual(winners, {})
        self.assertEqual(stamps["a"], 99.0)

    def test_newer_peer_wins(self):
        state = {"mirror": {"a": 1}, "stamps": {"a": 10.0}}
        peer = {"prefs": {"a": 5}, "stamps": {"a": 50.0}}
        updates, winners, _ = icloud_sync._reconcile(
            {"a": 1}, state, [peer], now=99.0)
        self.assertEqual(updates, {"a": 5})
        self.assertEqual(winners["a"], 50.0)

    def test_older_peer_loses(self):
        state = {"mirror": {"a": 1}, "stamps": {"a": 80.0}}
        peer = {"prefs": {"a": 5}, "stamps": {"a": 50.0}}
        updates, _, _ = icloud_sync._reconcile(
            {"a": 1}, state, [peer], now=99.0)
        self.assertEqual(updates, {})

    def test_tie_keeps_local(self):
        state = {"mirror": {"a": 1}, "stamps": {"a": 50.0}}
        peer = {"prefs": {"a": 5}, "stamps": {"a": 50.0}}
        updates, _, _ = icloud_sync._reconcile(
            {"a": 1}, state, [peer], now=99.0)
        self.assertEqual(updates, {})

    def test_newest_of_several_peers_wins(self):
        state = {"mirror": {"a": 1}, "stamps": {"a": 10.0}}
        peers = [
            {"prefs": {"a": "old"}, "stamps": {"a": 20.0}},
            {"prefs": {"a": "new"}, "stamps": {"a": 60.0}},
            {"prefs": {"a": "mid"}, "stamps": {"a": 40.0}},
        ]
        updates, _, _ = icloud_sync._reconcile(
            {"a": 1}, state, peers, now=99.0)
        self.assertEqual(updates, {"a": "new"})

    def test_agreeing_peer_advances_the_stamp_without_a_write(self):
        state = {"mirror": {"a": 1}, "stamps": {"a": 10.0}}
        peer = {"prefs": {"a": 1}, "stamps": {"a": 50.0}}
        updates, _, stamps = icloud_sync._reconcile(
            {"a": 1}, state, [peer], now=99.0)
        self.assertEqual(updates, {})
        self.assertEqual(stamps["a"], 50.0)

    def test_junk_peer_is_ignored(self):
        state = {"mirror": {}, "stamps": {}}
        peers = [{"prefs": "nope", "stamps": {}}, {"nothing": True}]
        updates, _, _ = icloud_sync._reconcile({"a": 1}, state, peers, now=9.0)
        self.assertEqual(updates, {})


class KeyspaceTests(unittest.TestCase):
    """shared_prefs projects the two stores into flat keys and back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.machine = _Machine(self.tmp.name, "", "solo").__enter__()

    def tearDown(self):
        self.machine.__exit__()
        self.tmp.cleanup()
        app_config.reload()
        sources_config._state = None

    def test_round_trip(self):
        sources_config.set_accents({"claude": "#123456"})
        prefs = shared_prefs.read()
        self.assertEqual(prefs["sources.accent.claude"], "#123456")
        self.assertIn("sources.enabled.claude", prefs)
        self.assertIsInstance(prefs["sources.order"], list)

    def test_unset_accent_is_an_explicit_none(self):
        prefs = shared_prefs.read()
        self.assertIsNone(prefs["sources.accent.claude"])

    def test_apply_writes_through(self):
        applied = shared_prefs.apply({
            "sources.accent.claude": "#ABCDEF",
            "sources.enabled.claude": False,
        })
        self.assertIn("sources.accent.claude", applied)
        self.assertEqual(sources_config.accent_overrides()["claude"], "#ABCDEF")
        self.assertFalse(sources_config.enabled_map()["claude"])

    def test_apply_clears_an_accent(self):
        sources_config.set_accents({"claude": "#123456"})
        shared_prefs.apply({"sources.accent.claude": None})
        self.assertNotIn("claude", sources_config.accent_overrides())

    def test_unknown_source_is_dropped_not_stored(self):
        applied = shared_prefs.apply({"sources.enabled.nope:zzz": True})
        self.assertEqual(applied, [])
        self.assertNotIn("nope:zzz", sources_config.enabled_map())

    def test_bad_colour_is_dropped_without_raising(self):
        applied = shared_prefs.apply({"sources.accent.claude": "octarine"})
        self.assertEqual(applied, [])

    def test_config_keys_are_whitelisted(self):
        shared_prefs.apply({
            "config.plausible_sites": ["example.com"],
            "config.auth_token": "hunter2",
            "config.dev_root": "/tmp/nope",
        })
        with open(app_config.STORE_PATH) as handle:
            stored = json.load(handle)
        self.assertEqual(stored["plausible_sites"], ["example.com"])
        self.assertNotIn("auth_token", stored)
        self.assertNotIn("dev_root", stored)

    def test_secrets_never_appear_in_the_shared_keyspace(self):
        app_config._persist(auth_token="hunter2", dev_root="/Users/me/Dev")
        prefs = shared_prefs.read()
        self.assertNotIn("config.auth_token", prefs)
        self.assertNotIn("config.dev_root", prefs)
        self.assertNotIn("hunter2", json.dumps(prefs))


class CloudKitRoundTests(unittest.TestCase):
    """The transport the Mac app drives: records in, this Mac's record out.

    No folder is involved, which is the point — these assert that the merge is
    reachable without touching the filesystem, so CloudKit and the folder share
    one implementation rather than two that drift.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # No icloud_dir → cloudkit mode.
        self.machine = _Machine(self.tmp.name, "", "laptop").__enter__()
        app_config._persist(icloud_sync=True)

    def tearDown(self):
        self.machine.__exit__()
        self.tmp.cleanup()
        app_config.reload()
        sources_config._state = None

    def test_mode_is_cloudkit_without_a_directory(self):
        self.assertEqual(icloud_sync.mode(), "cloudkit")
        self.assertIsNone(icloud_sync.root_dir())

    def test_folder_tick_is_a_no_op_in_cloudkit_mode(self):
        """The app drives the schedule there — the host must not also write."""
        result = icloud_sync.tick(now=1_000.0)
        self.assertFalse(result["enabled"])

    def test_round_returns_a_publishable_record(self):
        result = icloud_sync.cloud_round([], beacon={"servers": 2}, now=1_000.0)
        record = result["record"]
        self.assertTrue(result["ok"])
        self.assertEqual(record["servers"], 2)
        self.assertEqual(record["updated"], 1_000.0)
        self.assertIn("prefs", record)
        self.assertIn("stamps", record)
        self.assertEqual(record["id"], machine_identity.machine_id())

    def test_round_adopts_from_a_peer_record(self):
        peer = {
            "id": "peer-machine",
            "name": "Studio",
            "updated": 1_000.0,
            "prefs": {"sources.accent.claude": "#0F0F0F"},
            "stamps": {"sources.accent.claude": 999.0},
        }
        result = icloud_sync.cloud_round([peer], now=1_010.0)
        self.assertIn("sources.accent.claude", result["adopted"])
        self.assertEqual(
            sources_config.accent_overrides()["claude"], "#0F0F0F")
        self.assertEqual(len(result["peers"]), 1)
        self.assertEqual(result["peers"][0]["name"], "Studio")

    def test_round_ignores_a_record_claiming_to_be_us(self):
        """A stale copy of our own record must not merge back over us."""
        mine = {
            "id": machine_identity.machine_id(),
            "updated": 1_000.0,
            "prefs": {"sources.accent.claude": "#AAAAAA"},
            "stamps": {"sources.accent.claude": 9_999.0},
        }
        result = icloud_sync.cloud_round([mine], now=1_010.0)
        self.assertEqual(result["peers"], [])
        self.assertEqual(result["adopted"], [])

    def test_round_survives_junk_records(self):
        result = icloud_sync.cloud_round(
            ["not a dict", {}, {"id": 7}, None], now=1_000.0)
        self.assertEqual(result["peers"], [])

    def test_study_rides_in_the_record_and_never_in_the_peer_view(self):
        shard = {"version": 1, "machine": "peer-machine", "turns": 3}
        peer = {"id": "peer-machine", "name": "Studio", "updated": 1_000.0,
                "prefs": {}, "stamps": {}, "study": shard}
        mine = {"version": 1, "machine": machine_identity.machine_id()}
        result = icloud_sync.cloud_round([peer], now=1_010.0, study=mine)
        self.assertEqual(result["record"]["study"], mine)
        self.assertEqual(result["studies"], [shard])
        self.assertNotIn("study", result["peers"][0])

    def test_no_study_means_no_key(self):
        result = icloud_sync.cloud_round([], now=1_000.0)
        self.assertNotIn("study", result["record"])
        self.assertEqual(result["studies"], [])

    def test_folder_tick_off_never_builds_the_shard(self):
        def boom():
            raise AssertionError("built a shard with sync off")
        self.assertFalse(icloud_sync.tick(now=1_000.0, study=boom)["enabled"])

    def test_record_fits_the_post_ceiling(self):
        """A full round of records must clear the handler's body limit."""
        record = icloud_sync.cloud_round([], now=1_000.0)["record"]
        one = len(json.dumps(record))
        # Six Macs of headroom against the 128 KB the handler now allows.
        self.assertLess(one * 6, 128 * 1024)


class TwoMachineTests(unittest.TestCase):
    """The real thing: two Macs, one folder, several rounds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.share = os.path.join(self.tmp.name, "share")

    def tearDown(self):
        self.tmp.cleanup()
        app_config.reload()
        sources_config._state = None

    def machine(self, name):
        return _Machine(self.tmp.name, self.share, name)

    def test_colour_travels_from_one_mac_to_the_other(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            sources_config.set_accents({"claude": "#FF0000"})
            studio.tick(now=1_000.0)

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            self.assertIsNone(sources_config.accent_overrides().get("claude"))
            result = laptop.tick(now=1_010.0)
            self.assertEqual(result["peers"], 1)
            self.assertIn("sources.accent.claude", result["adopted"])
            self.assertEqual(
                sources_config.accent_overrides()["claude"], "#FF0000")

    def test_order_travels(self):
        pinned = None
        with self.machine("studio") as studio:
            studio.enable_sync()
            sources_config.set_order(list(reversed(sources_config.order_ids())))
            # Read it back rather than trusting the input: `set_order`
            # normalizes, and the assertion is about what travelled.
            pinned = sources_config.order_ids()
            studio.tick(now=1_000.0)

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            laptop.tick(now=1_010.0)
            self.assertEqual(sources_config.order_ids(), pinned)

    def test_settling_takes_one_round_and_stays_settled(self):
        """The loop check: no adoption once both Macs agree."""
        with self.machine("studio") as studio:
            studio.enable_sync()
            sources_config.set_accents({"codex": "#00FF00"})
            studio.tick(now=1_000.0)

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            self.assertTrue(laptop.tick(now=1_010.0)["adopted"])
            self.assertEqual(laptop.tick(now=1_020.0)["adopted"], [])
            self.assertEqual(laptop.tick(now=1_030.0)["adopted"], [])

        with self.machine("studio") as studio:
            # And the machine that started it does not adopt its own value
            # back off the peer that just echoed it.
            self.assertEqual(studio.tick(now=1_040.0)["adopted"], [])
            self.assertEqual(studio.tick(now=1_050.0)["adopted"], [])

    def test_joining_mac_adopts_wholesale(self):
        """Opening Headroom on a second Mac: it arrives already set up."""
        with self.machine("studio") as studio:
            studio.enable_sync()
            sources_config.set_accents({"claude": "#ABABAB", "codex": "#CDCDCD"})
            sources_config.set_enabled({"cursor": False})
            app_config.set_shared_config({"plausible_sites": ["example.com"]})
            studio.tick(now=1_000.0)

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            laptop.tick(now=1_010.0)
            self.assertEqual(sources_config.accent_overrides()["claude"], "#ABABAB")
            self.assertEqual(sources_config.accent_overrides()["codex"], "#CDCDCD")
            self.assertFalse(sources_config.enabled_map()["cursor"])
            self.assertEqual(app_config.plausible_sites(), ("example.com",))

    def test_first_mac_in_an_empty_folder_keeps_its_own(self):
        """The origin stamps its config, so a later joiner does not overwrite it."""
        with self.machine("studio") as studio:
            studio.enable_sync()
            sources_config.set_accents({"claude": "#ABABAB"})
            self.assertEqual(studio.tick(now=1_000.0)["adopted"], [])
            self.assertEqual(
                sources_config.accent_overrides()["claude"], "#ABABAB")

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            laptop.tick(now=1_010.0)

        with self.machine("studio") as studio:
            studio.tick(now=1_020.0)
            self.assertEqual(
                sources_config.accent_overrides()["claude"], "#ABABAB")

    def test_later_edit_on_the_second_mac_wins(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            sources_config.set_accents({"claude": "#111111"})
            studio.tick(now=1_000.0)

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            laptop.tick(now=1_010.0)
            sources_config.set_accents({"claude": "#222222"})
            laptop.tick(now=2_000.0)

        with self.machine("studio") as studio:
            studio.tick(now=2_010.0)
            self.assertEqual(
                sources_config.accent_overrides()["claude"], "#222222")

    def test_peer_shows_up_in_the_payload_with_its_name(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            studio.tick(now=1_000.0)

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            with mock.patch.object(
                    machine_identity, "_computer_name", return_value="Laptop"):
                laptop.tick(now=1_010.0)
                rows = icloud_sync.machines_payload(now=1_010.0)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["self"])
        self.assertEqual(rows[0]["name"], "Laptop")
        self.assertFalse(rows[1]["self"])
        self.assertEqual(rows[1]["servers"], 1)

    def test_stale_and_forgotten_peers(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            studio.tick(now=1_000.0)

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            laptop.tick(now=1_000.0 + icloud_sync.STALE_S + 60)
            rows = icloud_sync.machines_payload(
                now=1_000.0 + icloud_sync.STALE_S + 60)
            self.assertTrue(rows[1]["stale"])

            # Long enough and the Mac drops off the list on its own.
            laptop.tick(now=1_000.0 + icloud_sync.FORGET_S + 60)
            self.assertEqual(len(icloud_sync.machines_payload()), 1)

    def test_unreadable_folder_is_reported_not_swallowed(self):
        """A folder we can write but not list must not read as "no peers".

        This is the real-world iCloud Drive failure: TCC lets the host create
        and write its own beacon and refuses `listdir`, so every Mac publishes
        happily into a folder none of them can enumerate and all of them report
        zero peers. Indistinguishable from success unless it is said out loud.
        """
        with self.machine("studio") as studio:
            studio.enable_sync()
            studio.tick(now=1_000.0)
            folder = os.path.join(self.share, icloud_sync.MACHINES_SUBDIR)
            self.assertIsNone(icloud_sync.probe())

            with mock.patch.object(
                    icloud_sync.os, "listdir",
                    side_effect=PermissionError(1, "Operation not permitted")):
                self.assertEqual(icloud_sync.probe(), "denied")
                config = icloud_sync.configuration(now=1_010.0)
            self.assertEqual(config["trouble"], "denied")
            self.assertIn("Full Disk Access", config["trouble_detail"])
            self.assertTrue(os.path.isdir(folder))

    def test_healthy_folder_reports_no_trouble(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            studio.tick(now=1_000.0)
            config = icloud_sync.configuration(now=1_010.0)
            self.assertIsNone(config["trouble"])
            self.assertIsNone(config["trouble_detail"])

    def test_off_by_default(self):
        with self.machine("solo") as solo:
            result = solo.tick(now=1_000.0)
            self.assertFalse(result["enabled"])
            self.assertFalse(os.path.exists(self.share))
            # Still one row, so the client has one shape to decode.
            self.assertEqual(len(icloud_sync.machines_payload()), 1)

    def test_unreadable_peer_file_is_survivable(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            studio.tick(now=1_000.0)
        folder = os.path.join(self.share, icloud_sync.MACHINES_SUBDIR)
        with open(os.path.join(folder, "truncated.json"), "w") as handle:
            handle.write('{"id": "half')
        with open(os.path.join(folder, ".evicted.json.icloud"), "w") as handle:
            handle.write("placeholder")

        with self.machine("laptop") as laptop:
            laptop.enable_sync()
            self.assertEqual(laptop.tick(now=1_010.0)["peers"], 1)

    def test_idle_mac_stops_rewriting_its_file(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            self.assertTrue(studio.tick(now=1_000.0)["wrote"])
            self.assertFalse(studio.tick(now=1_060.0)["wrote"])
            sources_config.set_accents({"claude": "#ABCABC"})
            self.assertTrue(studio.tick(now=1_120.0)["wrote"])

    def test_no_secrets_reach_the_shared_folder(self):
        with self.machine("studio") as studio:
            studio.enable_sync()
            app_config._persist(auth_token="hunter2", dev_root="/Users/me/Dev")
            studio.tick(now=1_000.0)
        folder = os.path.join(self.share, icloud_sync.MACHINES_SUBDIR)
        blob = ""
        for name in os.listdir(folder):
            with open(os.path.join(folder, name)) as handle:
                blob += handle.read()
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("/Users/me/Dev", blob)


if __name__ == "__main__":
    unittest.main()
