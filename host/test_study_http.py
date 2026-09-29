"""HTTP contract for the usage-study routes: Class 1, loopback only.

The routes name habits, import a stranger's card and write under
~/.headroom/study, so they belong with credentials and config (docs/trust.md).
What these tests pin is that the gate does not depend on holding a token: a
LAN caller and a paired phone are refused even when auth would let them in.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import headroom_server
import study_service
from test_agent_http import AgentHTTPTests
from test_study_card import sample_records
from test_usage_study import TZ, write

LAN = ("192.168.1.9", 40000)
LOCAL = ("127.0.0.1", 40000)

GET_ROUTES = ("/study", "/study/shard")
POST_ROUTES = (
    ("/study/handle", {"handle": "x"}),
    ("/study/friends", {"card": "hrc1.x"}),
    ("/study/friends/alias", {"id": "a" * 32, "alias": "x"}),
    ("/study/friends/remove", {"id": "a" * 32}),
    ("/study/shard", {"version": 1}),
    ("/study/shard/remove", {"machine": "m"}),
    ("/study/refresh", {}),
)


class StudyHTTPTests(unittest.TestCase):
    request = AgentHTTPTests.request     # the socketpair harness, not its tests

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        logs = os.path.join(self._tmp.name, "logs")
        write(logs, "p/a.jsonl", sample_records())
        self.service = study_service.StudyService(
            log_root=logs, state_dir=os.path.join(self._tmp.name, "study"),
            tz=TZ, machine=lambda: "this-mac", machine_name=lambda: "Studio")
        self.service.refresh(wait=True)
        for patcher in (
                mock.patch("headroom_server.study_service.get",
                           return_value=self.service),
                # Credentials are a separate axis. Let everyone past auth so the
                # only thing left standing is the route class.
                mock.patch.object(headroom_server.Handler, "_allowed",
                                  return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body, **kw):
        return self.request("POST", path, body, **kw)

    # ---- who may ask ---------------------------------------------------
    def test_loopback_reads_the_snapshot(self):
        status, body = self.get("/study", peer=LOCAL)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["insights"]["turns"], 30)
        self.assertTrue(body["card_text"].startswith("hrc1."))

    def test_a_lan_caller_is_refused_every_route(self):
        for path in GET_ROUTES:
            status, body = self.get(path, peer=LAN)
            self.assertEqual((status, body["error"]), (403, "localhost only"),
                             path)
        for path, payload in POST_ROUTES:
            status, body = self.post(path, payload, peer=LAN)
            self.assertEqual((status, body["error"]), (403, "localhost only"),
                             path)

    def test_a_paired_phone_is_refused_even_with_every_permission(self):
        headers = {"X-Headroom-Client": "ios"}
        with mock.patch("headroom_server.app_config.mobile_permissions",
                        return_value=["read", "refresh", "sources",
                                      "servers", "agents"]):
            for path in GET_ROUTES:
                self.assertEqual(
                    self.get(path, peer=LAN, headers=headers)[0], 403, path)
            for path, payload in POST_ROUTES:
                self.assertEqual(
                    self.post(path, payload, peer=LAN, headers=headers)[0],
                    403, path)

    def test_dns_rebinding_is_refused(self):
        # A page on evil.tld whose DNS flipped to 127.0.0.1: a genuine loopback
        # socket, but the Host header still names the site it resolved.
        with mock.patch.object(headroom_server.Handler, "_header_host",
                               return_value="evil.tld"):
            self.assertEqual(self.get("/study", peer=LOCAL)[0], 403)
            self.assertEqual(
                self.post("/study/friends", {"card": "x"}, peer=LOCAL)[0], 403)

    def test_a_cross_site_page_is_refused(self):
        for headers in ({"Origin": "https://evil.tld"},
                        {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.get("/study", peer=LOCAL, headers=headers)[0],
                             403)
            self.assertEqual(
                self.post("/study/handle", {"handle": "x"}, peer=LOCAL,
                          headers=headers)[0], 403)

    # ---- what they do --------------------------------------------------
    def test_handle_then_card(self):
        status, body = self.post("/study/handle", {"handle": "nightowl"},
                                 peer=LOCAL)
        self.assertEqual((status, body["handle"]), (200, "nightowl"))
        self.assertEqual(self.get("/study", peer=LOCAL)[1]["card"]["handle"],
                         "nightowl")

    def test_friend_lifecycle_over_http(self):
        other = study_service.StudyService(
            log_root=os.path.join(self._tmp.name, "other"),
            state_dir=os.path.join(self._tmp.name, "other-study"), tz=TZ,
            machine=lambda: "their-mac", machine_name=lambda: "Theirs")
        write(other.log_root, "p/a.jsonl", sample_records(n=12, prefix="t"))
        other.refresh(wait=True)
        other.set_handle("anna")
        card = other.snapshot()["card_text"]

        status, added = self.post("/study/friends", {"card": card}, peer=LOCAL)
        self.assertEqual(status, 200)
        fid = added["friend"]["id"]
        status, aliased = self.post(
            "/study/friends/alias", {"id": fid, "alias": "Anna Banana"},
            peer=LOCAL)
        self.assertEqual(aliased["friend"]["name"], "Anna Banana")
        listed = self.get("/study", peer=LOCAL)[1]["friends"]
        self.assertEqual([f["name"] for f in listed], ["Anna Banana"])
        self.assertEqual(
            self.post("/study/friends/remove", {"id": fid}, peer=LOCAL)[0], 200)
        self.assertEqual(self.get("/study", peer=LOCAL)[1]["friends"], [])

    def test_a_bad_card_is_a_400_with_the_reason(self):
        status, body = self.post("/study/friends", {"card": "hrc1.nope"},
                                 peer=LOCAL)
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("card", body["error"])
        self.assertEqual(self.post("/study/friends", {}, peer=LOCAL)[0], 400)

    def test_bad_types_in_a_body_are_a_400_not_a_crash(self):
        for path, body in (("/study/friends/alias", {"id": ["x"], "alias": 1}),
                           ("/study/friends/remove", {"id": {"a": 1}}),
                           ("/study/handle", {"handle": 5}),
                           ("/study/shard/remove", {"machine": None})):
            self.assertEqual(self.post(path, body, peer=LOCAL)[0], 400, path)

    def test_shard_export_and_import_between_two_macs(self):
        status, shard = self.get("/study/shard", peer=LOCAL)
        self.assertEqual(status, 200)
        self.assertEqual((shard["machine"], shard["name"]),
                         ("this-mac", "Studio"))
        self.assertEqual(shard["turns"], 30)

        other = study_service.StudyService(
            log_root=os.path.join(self._tmp.name, "desk"),
            state_dir=os.path.join(self._tmp.name, "desk-study"), tz=TZ,
            machine=lambda: "desk", machine_name=lambda: "Desk")
        write(other.log_root, "p/a.jsonl", sample_records(day=3, n=8, prefix="d"))
        other.refresh(wait=True)
        with mock.patch("headroom_server.study_service.get",
                        return_value=other):
            self.assertEqual(self.post("/study/shard", shard, peer=LOCAL)[0], 200)
            snap = self.get("/study", peer=LOCAL)[1]
            self.assertEqual(snap["insights"]["turns"], 38)
            self.assertEqual(snap["insights"]["machines"], 2)
            status, body = self.post("/study/shard", shard, peer=LOCAL)  # again
            self.assertEqual(status, 200)

    def test_the_same_mac_shard_is_a_400(self):
        shard = self.get("/study/shard", peer=LOCAL)[1]
        status, body = self.post("/study/shard", shard, peer=LOCAL)
        self.assertEqual(status, 400)
        self.assertIn("this Mac", body["error"])

    def test_body_limits(self):
        big_handle = {"handle": "x" * 5000}
        self.assertEqual(self.post("/study/handle", big_handle, peer=LOCAL)[0],
                         400)                       # small routes stay at 4 KB
        shard = self.get("/study/shard", peer=LOCAL)[1]
        shard["machine"] = "other"
        shard["tools"] = {f"tool{i}": 1 for i in range(330)}
        self.assertGreater(len(json.dumps(shard)), 4096)
        self.assertEqual(self.post("/study/shard", shard, peer=LOCAL)[0], 200)

    def test_usage_document_is_untouched(self):
        """The study is its own route. A new key on /usage could blank the popover."""
        with open(headroom_server.__file__) as handle:
            source = handle.read()
        self.assertNotIn('"study"', source.split("def rollup")[1].split("def ")[0])


if __name__ == "__main__":
    unittest.main()
