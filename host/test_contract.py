"""The /usage contract, checked against both clients.

The document shape is written down three times — Python dicts here, shared
Swift Codable structs in Shared/HeadroomModels.swift, and field reads in
firmware/src/main.cpp — and nothing forced them to agree. Renaming a key was a
silent break: Swift decodes it to nil, the board renders "--", and neither
fails loudly.

These tests pin the parts that cross a process boundary. The Swift half lives
in macos/Tests/ContractTests.swift.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import app_config
import device_view
import headroom_server
import sources_config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_PATH = os.path.join(REPO_ROOT, "docs", "demo_usage.json")
FIRMWARE_PATH = os.path.join(REPO_ROOT, "firmware", "src", "main.cpp")
MODELS_PATH = os.path.join(REPO_ROOT, "Shared", "HeadroomModels.swift")


def _demo_doc():
    with open(DEMO_PATH) as handle:
        return json.load(handle)


# Matched loosely on the name alone: how the builder returns the filter is the
# board's business, and pinning the whole signature turned a refactor there into
# a ValueError here instead of the contract check it exists to run.
FILTER_BUILDER_RE = re.compile(r"^static\b[^\n]*\busageFilter\w*\s*\(", re.M)


def _firmware_filter_paths():
    """Key paths from the usageFilter builder in main.cpp, e.g. ('vercel', 'deployments')."""
    with open(FIRMWARE_PATH) as handle:
        source = handle.read()
    match = FILTER_BUILDER_RE.search(source)
    if not match:
        raise AssertionError(
            "no usageFilter* builder in firmware/src/main.cpp — it was renamed "
            "or removed; point FILTER_BUILDER_RE at the real one so this "
            "contract keeps checking something"
        )
    start = match.start()
    end = source.index("\n}", start)
    body = source[start:end]

    paths = set()
    # filter["a"]["b"][0]["c"] = true;  → ("a", "b", "c")
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("filter["):
            continue
        keys = re.findall(r'\["([^"]+)"\]', line)
        if keys:
            paths.add(tuple(keys))
    # The flat top-level block is written as a loop over a string list.
    for match in re.finditer(r'for \(const char \*key : \{(.+?)\}\)', body, re.S):
        for key in re.findall(r'"([^"]+)"', match.group(1)):
            paths.add((key,))
    if not paths:
        raise AssertionError(
            "parsed zero keys out of the usageFilter builder — the assignment "
            "style changed, and an empty set would pass this contract silently"
        )
    return paths


# What device_view is capable of emitting, derived from its own constants so
# the test can't drift from the projection it is checking.
EMITTABLE = (
    {(key,) for key in device_view.CLAUDE_FIELDS}
    | {("updated",)}
    | {("codex", key) for key in device_view.CODEX_FIELDS}
    | {("cursor", key) for key in device_view.CURSOR_FIELDS}
    | {("codex",), ("cursor",)}
    | {("vercel",), ("vercel", "ok"), ("vercel", "team"),
       ("vercel", "deployments")}
    | {("vercel", "deployments", key) for key in device_view.DEPLOY_FIELDS}
    | {("git",), ("git", "ok"), ("git", "commits")}
    | {("git", "commits", key) for key in device_view.COMMIT_FIELDS}
    | {("local",), ("local", "ok"), ("local", "host"), ("local", "servers")}
    | {("local", "servers", key) for key in device_view.SERVER_FIELDS}
    | {("sources",)}
    | {("sources", key) for key in device_view.SOURCE_FIELDS}
    | {("activity_history",)}
    | {("activity_history", key) for key in (
        "source", "start", "start_weekday", "levels", "active_days",
        "current_streak")}
    | {("daily_burn",), ("daily_burn", "source"),
       ("daily_burn", "days")}
    | {("daily_burn", "days", key)
       for key in ("date", "label", "burns", "total")}
    | {("spend",), ("spend", "estimated")}
    | {("spend", key) for key in ("today", "total", "days", "avg")}
    # Whole subtree, like burndown: device_view already trimmed it to the
    # focus providers and their ring pools.
    | {("providers",)}
    | {("device_effect",), ("device_effect", "id"),
       ("device_effect", "kind"), ("device_effect", "provider")}
    # Host-owned panel settings (Settings → Desk display). Whole subtree: the
    # board applies what arrives and keeps no opinion about it.
    | {("display",), ("display", "brightness"), ("display", "celebrate_resets"),
       ("display", "boot_splash"), ("display", "pages")}
    | {("display", "pages", page_id) for page_id in app_config.DISPLAY_PAGE_IDS}
    | {("burndown",)}
    | {("burndown", provider) for provider in sources_config.BURN_SOURCE_IDS}
    | {("burndown", provider, key)
       for provider in sources_config.BURN_SOURCE_IDS
       for key in device_view.BURNDOWN_FIELDS}
)


class DeviceViewContractTests(unittest.TestCase):
    def test_firmware_filter_only_asks_for_emittable_keys(self):
        """Every key the board filters for is one the host can actually send."""
        missing = sorted(_firmware_filter_paths() - EMITTABLE)
        self.assertEqual(
            missing, [],
            "firmware/src/main.cpp usageFilter() wants keys device_view.py "
            "never emits — one side was renamed without the other",
        )

    def test_display_block_rides_along_only_when_the_host_supplies_one(self):
        bare = device_view.build(_demo_doc())
        self.assertNotIn("display", bare)
        device = device_view.build(_demo_doc(), display={
            "brightness": 191, "celebrate_resets": False, "boot_splash": True,
            "pages": {"vercel": True, "git": False, "local": True},
        })
        self.assertEqual(device["display"], {
            "brightness": 191, "celebrate_resets": False, "boot_splash": True,
            "pages": {"vercel": True, "git": False, "local": True},
        })

    def test_device_view_survives_a_fully_populated_document(self):
        device = device_view.build(_demo_doc())
        self.assertTrue(device["quota_ok"])
        self.assertEqual(device["plan"], "Max 5x")
        self.assertTrue(device["codex"]["ok"])
        self.assertIn("total_pct", device["cursor"])
        self.assertTrue(device["vercel"]["deployments"])
        self.assertTrue(device["git"]["commits"])
        self.assertTrue(device["sources"])

    def test_device_view_carries_an_additive_effect_command(self):
        device = device_view.build(_demo_doc(), effect={
            "id": 42, "kind": "reset", "provider": "codex",
        })
        self.assertEqual(device["device_effect"], {
            "id": 42, "kind": "reset", "provider": "codex",
        })

    def test_device_view_drops_nulls(self):
        doc = {"plan": None, "week_pct": 12.0, "updated": "x",
               "codex": {"ok": True, "plan": None}}
        device = device_view.build(doc)
        self.assertNotIn("plan", device)
        self.assertEqual(device["week_pct"], 12.0)
        self.assertNotIn("plan", device["codex"])

    def test_device_view_caps_rows_to_firmware_storage(self):
        doc = {
            "vercel": {"ok": True, "deployments": [{"project": f"p{i}"}
                                                   for i in range(20)]},
            "git": {"ok": True, "commits": [{"repo": f"r{i}"}
                                            for i in range(20)]},
            "local": {"ok": True, "servers": [{"name": f"s{i}"}
                                              for i in range(20)]},
        }
        device = device_view.build(doc)
        self.assertEqual(len(device["vercel"]["deployments"]),
                         device_view.MAX_DEPLOYS)
        self.assertEqual(len(device["git"]["commits"]), device_view.MAX_COMMITS)
        self.assertEqual(len(device["local"]["servers"]),
                         device_view.MAX_SERVERS)

    def test_device_view_sends_the_focus_three_in_order(self):
        device = device_view.build(_demo_doc())
        rows = device["providers"]
        self.assertEqual([row["id"] for row in rows], _demo_doc()["focus"])
        # Which three, what they are called, and what color they are painted
        # all cross the wire now — the board hardcodes none of it.
        self.assertEqual(rows[0]["title"], "Claude")
        self.assertEqual(rows[0]["accent"], "#D97757")
        self.assertEqual([pool["t"] for pool in rows[0]["pools"]],
                         ["Weekly", "Session"])

    def test_device_view_sends_only_ring_pools_with_readings(self):
        doc = {
            "focus": ["cursor"],
            "providers": [{
                "id": "cursor", "title": "Cursor", "ok": True,
                "accent": "#789BC8",
                "pools": {
                    "auto": {"title": "Auto", "rank": 1, "pct": 5.0,
                             "ring": False},
                    "total": {"title": "Total", "rank": 0, "pct": 34.0},
                    "api": {"title": "API", "rank": 2, "pct": None},
                },
            }],
        }
        pools = device_view.build(doc)["providers"][0]["pools"]
        # Auto is charted but never drawn; API has no reading to draw.
        self.assertEqual([pool["t"] for pool in pools], ["Total"])

    def test_device_view_honours_the_slot_limit(self):
        ids = [f"p{i}" for i in range(6)]
        doc = {
            "focus": ids,
            "providers": [{"id": pid, "title": pid, "ok": True, "pools": {}}
                          for pid in ids],
        }
        rows = device_view.build(doc)["providers"]
        self.assertEqual(len(rows), device_view.MAX_PROVIDERS)

    def test_device_view_charts_only_what_the_board_can_show(self):
        doc = {
            "focus": ["claude", "claude:work"],
            "providers": [
                {"id": "claude", "title": "Claude", "ok": True, "pools": {}},
                {"id": "claude:work", "title": "Claude - Work", "ok": True,
                 "pools": {}},
            ],
            "burndown": {
                pid: {"week": {"pool": "week", "window_start": 0,
                               "window_end": 100, "actual": [[1, 90]]}}
                for pid in ("claude", "claude:work", "gemini")
            },
        }
        charted = set(device_view.build(doc)["burndown"])
        # The slots, plus the legacy trio an un-reflashed board draws by name.
        self.assertIn("claude:work", charted)
        self.assertNotIn("gemini", charted)

    def test_device_slot_caps_match_firmware_constants(self):
        with open(FIRMWARE_PATH) as handle:
            source = handle.read()

        def firmware_const(name):
            match = re.search(
                rf"static const uint8_t {name} = (\d+);", source)
            self.assertIsNotNone(match, f"{name} missing from main.cpp")
            return int(match.group(1))

        self.assertEqual(firmware_const("MAX_SLOTS"),
                         device_view.MAX_PROVIDERS)
        self.assertEqual(firmware_const("MAX_POOLS"), device_view.MAX_POOLS)

    def test_device_row_caps_match_firmware_constants(self):
        with open(FIRMWARE_PATH) as handle:
            source = handle.read()

        def firmware_const(name):
            match = re.search(
                rf"static const uint8_t {name} = (\d+);", source)
            self.assertIsNotNone(match, f"{name} missing from main.cpp")
            return int(match.group(1))

        self.assertEqual(firmware_const("MAX_DEPLOYS"), device_view.MAX_DEPLOYS)
        self.assertEqual(firmware_const("MAX_COMMITS"), device_view.MAX_COMMITS)
        self.assertEqual(firmware_const("MAX_SERVERS"), device_view.MAX_SERVERS)
        self.assertEqual(firmware_const("MAX_SOURCES"), device_view.MAX_SOURCES)
        self.assertEqual(firmware_const("MAX_ACTIVITY_DAYS"),
                         device_view.MAX_ACTIVITY_DAYS)
        self.assertEqual(firmware_const("MAX_DAILY_BURN_DAYS"),
                         device_view.MAX_DAILY_BURN_DAYS)

    def test_device_view_projects_mixed_daily_burn(self):
        rows = [
            {"date": "2026-07-28", "burns": {"claude": 1.234,
                                               "codex": -2}},
            {"date": "2026-07-29", "claude": 2.5, "cursor": 0.5},
        ]
        device = device_view.build({"by_day": rows})
        self.assertEqual(device["daily_burn"]["source"], "mixed")
        self.assertEqual(device["daily_burn"]["days"][-1], {
            "date": "2026-07-29", "label": "Wed",
            "burns": {"claude": 2.5, "cursor": 0.5}, "total": 3.0,
        })

    def test_device_view_projects_estimated_spend(self):
        device = device_view.build({
            "today": {"cost_usd": 12.475},
            "history": {"total_cost_usd": 12475.4,
                        "active_days": 30,
                        "avg_cost_per_active_day": 415.846},
        })
        self.assertEqual(device["spend"], {
            "estimated": True, "today": 12.47, "total": 12475.4,
            "days": 30, "avg": 415.85,
        })

    def test_device_payload_is_much_smaller_than_the_full_document(self):
        doc = _demo_doc()
        full = len(json.dumps(doc))
        device = len(json.dumps(device_view.build(doc), separators=(",", ":")))
        self.assertLess(device, full // 2)

    def test_cursor_burndown_overlays_total_and_api(self):
        """Cursor ships Total + API on one chart; Auto is never the second line."""
        window = {
            "window_s": 30 * 24 * 3600,
            "window_start": 1_700_000_000,
            "window_end": 1_700_000_000 + 30 * 24 * 3600,
            "actual": [[1_700_000_100, 90.0], [1_700_000_200, 80.0]],
            "projected": [[1_700_000_200, 80.0], [1_700_100_000, 0.0]],
            "exhausts_before_reset": False,
            "rate_source": "measured",
        }
        doc = {
            "burndown": {
                "cursor": {
                    "total": {**window, "pool": "total", "status": "ok"},
                    "auto": {**window, "pool": "auto", "status": "ok",
                             "actual": [[1_700_000_100, 100.0]]},
                    "api": {**window, "pool": "api", "status": "exhausted",
                            "actual": [[1_700_000_100, 40.0],
                                       [1_700_000_200, 0.0]],
                            "exhausts_before_reset": True},
                }
            }
        }
        device = device_view.build(doc)
        burn = device["burndown"]["cursor"]
        self.assertEqual(burn["pool"], "total")
        self.assertEqual(burn["pool2"], "api")
        self.assertEqual(burn["status2"], "exhausted")
        self.assertTrue(burn["warn2"])
        self.assertEqual(burn["pts2"][-1][1], 0.0)
        self.assertNotIn("auto", json.dumps(burn))

    def test_device_view_ascii_folds_verdict_middot(self):
        # glcdfont can't draw · — those UTF-8 bytes become garbage glyphs.
        doc = _demo_doc()
        week = doc["burndown"]["claude"]["week"]
        week.update({
            "window_start": 1_700_000_000,
            "window_end": 1_700_604_800,
            "window_s": 604_800,
            "verdict": "On track · 15%",
            "actual": [[1_700_000_100, 85.0]],
        })
        burn = device_view.build(doc)["burndown"]["claude"]
        self.assertEqual(burn["verdict"], "On track - 15%")
        self.assertNotIn("\u00b7", burn["verdict"])


class RollupContractTests(unittest.TestCase):
    def setUp(self):
        # Payload order follows the pinned provider order, which lives in
        # ~/.headroom/sources.json. Run against a throwaway store so these
        # assertions describe the code, not whatever this machine has pinned.
        sources_config.reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(
            sources_config, "STORE_PATH",
            os.path.join(self.tmp.name, "sources.json"))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
        sources_config.reset_for_tests()

    def test_rollup_exposes_every_registered_source(self):
        doc = headroom_server.publish()
        ids = [row["id"] for row in doc["sources"]]
        self.assertEqual(ids, list(sources_config.SOURCE_IDS))

    def test_rollup_order_follows_the_pinned_provider_order(self):
        rest = [s for s in sources_config.BURN_SOURCE_IDS if s != "cursor"]
        sources_config.set_order(["cursor"] + rest)
        doc = headroom_server.publish()
        self.assertEqual(doc["providers"][0]["id"], "cursor")
        self.assertEqual(doc["providers"][0]["rank"], 0)
        self.assertEqual([row["id"] for row in doc["sources"]][0], "cursor")
        self.assertEqual(doc["focus"][0], "cursor")
        self.assertLessEqual(len(doc["focus"]), sources_config.FOCUS_LIMIT)

    def test_rollup_has_the_keys_the_mac_app_decodes(self):
        doc = headroom_server.publish()
        for key in ("updated", "today", "by_day", "codex", "cursor", "vercel",
                    "git", "github", "activity", "local", "supabase",
                    "plausible", "posthog", "claude_status", "sources", "attention",
                    "quota_ok", "session_pct", "week_pct", "integrations_order"):
            self.assertIn(key, doc)

    def test_demo_fixture_matches_the_served_shape(self):
        """The README fixture must stay decodable as a real response."""
        served = set(headroom_server.publish().keys())
        demo = set(_demo_doc().keys())
        unknown = sorted(demo - served)
        self.assertEqual(
            unknown, [],
            "docs/demo_usage.json has keys the host no longer serves",
        )


def _swift_wire_names():
    """Every JSON key Shared/HeadroomModels.swift is able to decode.

    Three sources, because the file uses all three styles: the raw value where
    a case is remapped (`case windowStart = "window_start"`), the case name
    where it isn't (`case provider, pool`), and the stored property name for
    the structs that rely on synthesized keys.
    """
    with open(MODELS_PATH) as handle:
        source = handle.read()

    names = set()
    for block in re.finditer(r"enum CodingKeys[^{]*\{(.*?)\n    \}", source, re.S):
        for line in block.group(1).splitlines():
            line = line.strip()
            if not line.startswith("case "):
                continue
            for part in line[len("case "):].split(","):
                part = part.strip()
                if "=" in part:
                    raw = re.search(r'"([^"]+)"', part)
                    if raw:
                        names.add(raw.group(1))
                elif part:
                    names.add(part)
    names |= set(re.findall(r"^\s*(?:var|let)\s+([A-Za-z_]\w*)\s*:", source, re.M))
    return names


# Paths whose child keys are *data*, not field names — pool ids, model names,
# provider ids. Descending into them would compare Swift field names against
# values.
DYNAMIC_MAP_PATHS = {
    "providers[].pools",
    "quota",
    "by_model",
    "by_day[].burns",
    "daily_burn.days[].burns",
    # Per-provider maps keyed by registry id, then by pool id.
    "burndown",
    "burndown[]",
}

# Subtrees skipped whole, by path. Empty since the Spend card: `history` used
# to be skipped as "detail nobody decodes", and the moment something decoded it
# that exemption became the exact trapdoor this file warns about — a host-side
# rename of `total_cost_usd` would blank the card and pass every test.
UNDECODED_SUBTREES = set()

# Individual keys the host emits that no Swift client decodes.
#
# Adding a line here is a decision: it says the Mac and iPhone deliberately
# ignore that field. A key arriving here *by accident* is the failure this test
# exists to catch — it almost always means one side of a rename landed.
UNDECODED_KEYS = {
    # Token windows shaped exactly like `today`, which Swift does decode. The
    # apps chart burn from `by_day` instead.
    "week",
    "session_5h",
    "last_hour",
    # Containers whose children are dynamic (pool ids, model names) and whose
    # own contents nothing on the Swift side reads.
    "quota",
    "by_model",
    # Token mix inside every window. TokenBucket decodes total + cost_usd only.
    "input",
    "output",
    "cache_read",
    "cache_write",
    # Derived convenience fields on codex/cursor, superseded by the burndown
    # rows the apps actually read.
    "cost_remaining_usd",
    "pace_delta_pct",
    "pace_in_deficit",
    "runs_out_in_s",
    "session_resets_in_s",
    "week_resets_in_s",
    # `history` fields the Spend card does not draw. It shows spend, the
    # per-model split and the price-table warning; these are the shape of the
    # window rather than what it cost, and the card is a glance, not a report.
    "days_covered",
    "first_day",
    "last_day",
    "avg_tokens_per_active_day",
    "avg_sessions_per_active_day",
    "avg_active_minutes",
}


def _emitted_key_names():
    """Field names the host serves, from a live document and the demo fixture.

    Both, because a bare machine has no Supabase or Plausible section and would
    quietly stop checking those keys; the committed fixture keeps the floor the
    same everywhere.
    """
    names = set()

    def walk(node, path):
        if isinstance(node, dict):
            if path in DYNAMIC_MAP_PATHS:
                for value in node.values():
                    walk(value, f"{path}[]")
                return
            for key, value in node.items():
                names.add(key)
                child = f"{path}.{key}" if path else key
                if child in UNDECODED_SUBTREES:
                    continue
                walk(value, child)
        elif isinstance(node, list):
            for value in node:
                walk(value, f"{path}[]")

    walk(headroom_server.publish(), "")
    walk(_demo_doc(), "")
    return names


class SwiftModelContractTests(unittest.TestCase):
    """Renames caught mechanically rather than one assertion at a time.

    The other tests in this file pin hand-picked fields, which means a renamed
    key three levels down — `burndown[].exhausts_before_reset`, say — passes
    everything: the host still emits it, Swift decodes nil, and a card renders
    blank. This walks the whole served document instead.

    Scope is the /usage document. /health and /setup are decoded by models that
    live in the app rather than in Shared/, and are covered by their own tests.
    """

    def test_every_emitted_key_is_known_to_the_swift_models(self):
        unknown = sorted(
            _emitted_key_names() - _swift_wire_names()
            - UNDECODED_KEYS - UNDECODED_SUBTREES)
        self.assertEqual(
            unknown, [],
            "the host emits keys Shared/HeadroomModels.swift can't decode: "
            f"{unknown}. Either the Swift side missed a rename, or the field "
            "is deliberately unread — in which case add it to UNDECODED_KEYS "
            "with a reason.",
        )

    def test_the_allowlist_does_not_outlive_the_keys_it_excuses(self):
        """A stale exemption would silently re-hide a real rename later."""
        emitted = set()

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    emitted.add(key)
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(headroom_server.publish())
        walk(_demo_doc())
        stale = sorted((UNDECODED_KEYS | UNDECODED_SUBTREES) - emitted)
        self.assertEqual(
            stale, [],
            f"these keys are excused but no longer served: {stale} — drop them "
            "from UNDECODED_KEYS / UNDECODED_SUBTREES",
        )

    def test_the_parser_finds_the_models_it_is_checking_against(self):
        """A regex that silently matches nothing would make this test vacuous."""
        names = _swift_wire_names()
        self.assertGreater(len(names), 100)
        for key in ("window_start", "remaining_pct", "cost_usd", "provider"):
            self.assertIn(key, names)


def _swift_struct_specs():
    """Parse HeadroomModels.swift into per-struct decode facts.

    Returns {struct_name: spec} where spec carries the declaration list
    (`props`), the CodingKeys mapping when one exists (`coding`, None means
    synthesized keys), whether the struct writes its own `init(from:)`
    (`hand_init`), the struct's own body text (`body`, innermost struct only,
    so a nested type's decode calls cannot leak into its parent), and the
    protocol clause (`inherits`).

    A line scanner with brace counting, not a Swift parser. The vacuity test
    below is what keeps that honest: if the file's style drifts far enough
    that this stops matching, the floor assertions fail loudly.
    """
    with open(MODELS_PATH) as handle:
        source = handle.read()

    prop_re = re.compile(r"^\s*(?:let|var)\s+(\w+)\s*:\s*([^={]+?)\s*$")
    struct_re = re.compile(r"^\s*(?:public\s+)?struct\s+(\w+)\s*(?::\s*([^{]+))?\{")
    other_re = re.compile(r"^\s*(?:public\s+)?(?:enum|extension|class|protocol)\s+\w+")

    structs = {}
    stack = []  # (kind, name, depth_at_open)
    depth = 0
    keys_owner = None
    for line in source.splitlines():
        m = struct_re.match(line)
        if m:
            full = ".".join(
                [s[1] for s in stack if s[0] == "struct"] + [m.group(1)])
            structs[full] = {
                "inherits": m.group(2) or "",
                "props": [], "coding": None, "hand_init": False, "body": [],
            }
            stack.append(("struct", m.group(1), depth))
        elif re.match(r"^\s*enum\s+CodingKeys\b", line):
            keys_owner = ".".join(s[1] for s in stack if s[0] == "struct")
            if keys_owner in structs:
                structs[keys_owner]["coding"] = {}
            stack.append(("codingkeys", "CodingKeys", depth))
        elif other_re.match(line) and "{" in line:
            stack.append(("other", "?", depth))

        owners = [s[1] for s in stack if s[0] == "struct"]
        if owners and stack[-1][0] == "struct":
            name = ".".join(owners)
            if name in structs:
                structs[name]["body"].append(line)
                if re.search(r"\binit\s*\(\s*from\b", line):
                    structs[name]["hand_init"] = True
                pm = prop_re.match(line)
                if pm and depth == stack[-1][2] + 1:
                    structs[name]["props"].append(
                        (pm.group(1), pm.group(2).strip()))

        if keys_owner is not None and stack and stack[-1][0] == "codingkeys":
            cm = re.match(r"^\s*case\s+(.*)$", line)
            if cm and keys_owner in structs:
                for part in cm.group(1).split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if "=" in part:
                        raw = re.search(r'"([^"]+)"', part)
                        if raw:
                            structs[keys_owner]["coding"][
                                part.split("=")[0].strip()] = raw.group(1)
                    else:
                        structs[keys_owner]["coding"][part] = part

        depth += line.count("{") - line.count("}")
        while stack and depth <= stack[-1][2]:
            if stack.pop()[0] == "codingkeys":
                keys_owner = None
    return structs


def _swift_required_by_struct():
    """{struct: wire keys its decoder cannot survive without}, /usage only.

    Requiredness is read two ways, matching how the file decodes. A struct
    with a hand-written `init(from:)` requires exactly what it passes to
    plain `container.decode(...)` — `decodeIfPresent` and the lossy-array
    helpers are survivable. A synthesized struct requires every stored
    property whose type is non-optional, because the compiler's decoder
    throws on a missing key even when the property has a default.

    Scope is the type graph reachable from UsageSnapshot; the agent-event
    and mobile-control families decode other endpoints and answer to their
    own tests.
    """
    structs = _swift_struct_specs()
    decode_re = re.compile(
        r"\.decode\(\s*[\w\[\]<>.?]+\.self\s*,\s*forKey:\s*\.(\w+)")

    simple = {}
    for full in structs:
        simple.setdefault(full.split(".")[-1], set()).add(full)
    reachable, todo = set(), ["UsageSnapshot"]
    while todo:
        cur = todo.pop()
        if cur in reachable or cur not in structs:
            continue
        reachable.add(cur)
        for _, typ in structs[cur]["props"]:
            for ref in re.findall(r"[A-Z]\w*", typ):
                todo.extend(simple.get(ref, ()))
                todo.append(cur + "." + ref)

    out = {}
    for name in reachable:
        spec = structs[name]
        if not re.search(r"\b(Decodable|Codable)\b", spec["inherits"]):
            continue
        if spec["hand_init"]:
            props = set(decode_re.findall("\n".join(spec["body"])))
        else:
            props = {p for p, typ in spec["props"]
                     if not typ.split("//")[0].strip().endswith("?")}
        coding = spec["coding"]
        keys = {coding[p] for p in props if p in coding} if coding is not None \
            else props
        if keys:
            out[name] = keys
    return out


class SwiftRequiredFieldTests(unittest.TestCase):
    """The other half of additive-only: a key Swift *requires* must be served.

    SwiftModelContractTests proves every emitted key is decodable. Nothing
    proved the reverse — that every key some Swift struct refuses to decode
    without is still being emitted. That gap is how a host-side removal or
    rename of a required field ships: Swift drops the row (or the section)
    on the floor with no error, and the first symptom is a blank card on a
    phone that updates on Apple's schedule.

    Checked against docs/demo_usage.json alone, because the fixture is the
    floor every machine shares — a live document on a configured Mac proves
    nothing about CI (see AGENTS.md). If this fails after a fixture edit,
    either restore the key or make the Swift field optional first; the
    contract permits loosening, never tightening.
    """

    def test_every_required_swift_field_is_in_the_demo_fixture(self):
        emitted = set()

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    emitted.add(key)
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(_demo_doc())
        missing = {
            name: sorted(keys - emitted)
            for name, keys in _swift_required_by_struct().items()
            if keys - emitted
        }
        self.assertEqual(
            missing, {},
            "Swift requires these keys and docs/demo_usage.json no longer "
            f"carries them: {missing}. A shipped client cannot decode a "
            "document without them — restore the key, or make the Swift "
            "field optional before the host stops emitting it.",
        )

    def test_the_struct_parser_is_not_vacuous(self):
        """A scraper that quietly matches nothing would pass forever."""
        required = _swift_required_by_struct()
        flat = set().union(*required.values())
        self.assertGreaterEqual(len(required), 10)
        self.assertIn("QuotaProviderInfo", required)
        for key in ("date", "domain", "ref"):
            self.assertIn(key, flat)
        # Agent events decode /agent endpoints, not /usage; if one of these
        # ever becomes reachable from UsageSnapshot the scope of the fixture
        # check just changed, and that should be a decision, not an accident.
        self.assertNotIn("AgentAttentionEvent", required)


class AttentionContractTests(unittest.TestCase):
    def test_attention_never_leaks_internal_weights(self):
        doc = headroom_server.publish()
        for reason in doc["attention"]["reasons"]:
            self.assertNotIn("weight", reason)

    def test_attention_levels_are_known_values(self):
        doc = headroom_server.publish()
        self.assertIn(doc["attention"]["level"], ("ok", "warn", "critical"))


if __name__ == "__main__":
    unittest.main()
