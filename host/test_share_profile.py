"""The share profile: what goes into the link, and that decode refuses junk."""

import base64
import json
import os
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import share_profile as sp

TZ = ZoneInfo("Europe/Berlin")
FIXTURE = os.path.join(os.path.dirname(__file__), "..", "docs",
                       "demo_usage.json")


def demo():
    with open(FIXTURE) as handle:
        return json.load(handle)


def raw(profile):
    body = json.dumps(profile, separators=(",", ":")).encode()
    return sp.PREFIX + base64.urlsafe_b64encode(body).decode().rstrip("=")


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 25, 12, tzinfo=TZ)
        self.profile = sp.build(demo(), None, "mz", now=self.now, tz=TZ)

    def test_round_trip(self):
        self.assertEqual(sp.decode(sp.encode(self.profile)), self.profile)

    def test_link_carries_the_profile_in_the_fragment(self):
        url = sp.link(sp.encode(self.profile))
        self.assertTrue(url.startswith(sp.PAGE_URL + "#hrp1."))
        self.assertEqual(sp.decode(url), self.profile)

    def test_subs_are_the_sources_that_are_on_and_reading(self):
        doc = demo()
        doc["providers"][1]["enabled"] = False
        ids = [s["id"] for s in sp.build(doc, None, "mz", now=self.now,
                                         tz=TZ)["subs"]]
        self.assertIn("claude", ids)
        self.assertNotIn(doc["providers"][1]["id"], ids)

    def test_plan_price_and_colour(self):
        claude = self.profile["subs"][0]
        self.assertEqual((claude["plan"], claude["usd"], claude["accent"]),
                         ("Max 5x", 100.0, "#D97757"))

    def test_unknown_plan_has_no_price(self):
        doc = demo()
        doc["providers"][0]["plan"] = "Secret tier"
        sub = sp.build(doc, None, "mz", now=self.now, tz=TZ)["subs"][0]
        self.assertIsNone(sub["usd"])

    def test_curve_is_coarse_and_starts_full(self):
        curve = self.profile["subs"][0]["curve"]
        self.assertEqual(curve["pool"], "Weekly")
        self.assertEqual(curve["step_h"], sp.STEP_H)
        self.assertEqual(curve["rem"][0], 100)
        self.assertTrue(all(isinstance(v, int) for v in curve["rem"]))
        self.assertLessEqual(len(curve["rem"]), sp.MAX_POINTS)

    def test_tie_between_week_long_pools_goes_to_the_headline(self):
        cursor = next(s for s in self.profile["subs"] if s["id"] == "cursor")
        self.assertEqual(cursor["curve"]["pool"], "Total")

    def test_no_counts_and_no_paths(self):
        text = json.dumps(self.profile)
        for word in ("tokens", "path", "repo", "/Users"):
            self.assertNotIn(word, text)

    def test_habits_come_from_the_card(self):
        card = {"handle": "x", "data": {"time_of_day_share": {"night": 1}}}
        profile = sp.build(demo(), card, None, now=self.now, tz=TZ)
        self.assertEqual(profile["habits"], card["data"])
        self.assertEqual(profile["handle"], "x")


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.profile = sp.build(demo(), None, "mz",
                                now=datetime(2026, 7, 25, tzinfo=TZ), tz=TZ)

    def refused(self, mutate):
        profile = json.loads(json.dumps(self.profile))
        mutate(profile)
        with self.assertRaises(sp.ProfileError):
            sp.decode(raw(profile))

    def test_refuses_extra_keys(self):
        self.refused(lambda p: p.update(tokens=5))
        self.refused(lambda p: p["subs"][0].update(path="/x"))

    def test_refuses_bad_colour(self):
        self.refused(lambda p: p["subs"][0].update(
            accent="red;background:url(x)"))

    def test_refuses_markup_free_text_that_is_not_clean(self):
        self.refused(lambda p: p.update(handle="mz‮"))
        self.refused(lambda p: p["subs"][0].update(plan="a\nb"))

    def test_refuses_out_of_range_curve(self):
        self.refused(lambda p: p["subs"][0]["curve"].update(rem=[101]))
        self.refused(lambda p: p["subs"][0]["curve"].update(rem=[]))
        self.refused(lambda p: p["subs"][0]["curve"].update(dow=7))

    def test_refuses_bad_habits(self):
        self.refused(lambda p: p.update(habits={"counts": {"x": 1}}))

    def test_refuses_other_prefixes_and_size(self):
        for text in ("hrc1.abc", "hrp2.abc", sp.PREFIX + "!!!",
                     sp.PREFIX + "A" * sp.MAX_ENCODED):
            with self.assertRaises(sp.ProfileError):
                sp.decode(text)


if __name__ == "__main__":
    unittest.main()
