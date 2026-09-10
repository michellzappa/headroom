import time
import unittest
from unittest import mock

import github_actions as ga
import headroom_server


class OwnerPrefixTests(unittest.TestCase):
    def test_no_prefixes_watches_everything(self):
        self.assertTrue(ga._matches_owner("acme/app", ()))

    def test_any_configured_owner_matches(self):
        prefixes = ("acme/", "ada/")
        self.assertTrue(ga._matches_owner("acme/app", prefixes))
        self.assertTrue(ga._matches_owner("ada/side-project", prefixes))
        self.assertFalse(ga._matches_owner("someone-else/app", prefixes))

    def test_owner_case_does_not_decide(self):
        # Remotes keep whatever case was typed; GitHub owners don't care.
        self.assertTrue(ga._matches_owner("Acme/App", ("acme/",)))


class AttentionFreshnessTests(unittest.TestCase):
    def test_fresh_failure_counts(self):
        now = time.time()
        rows = [{
            "status": "failure",
            "repo": "acme/app",
            "sha": "abc",
            "created_at": now - 60,
        }]
        self.assertEqual(ga.attention_fail_count(rows, now=now), 1)

    def test_old_failure_ignored(self):
        now = time.time()
        rows = [{
            "status": "failure",
            "repo": "acme/app",
            "sha": "abc",
            "created_at": now - (ga.ATTENTION_FAIL_MAX_AGE_S + 10),
        }]
        self.assertEqual(ga.attention_fail_count(rows, now=now), 0)

    def test_missing_timestamp_counts_as_fresh(self):
        rows = [{
            "status": "failure",
            "repo": "acme/app",
            "sha": "abc",
            "created_at": None,
        }]
        self.assertEqual(ga.attention_fail_count(rows), 1)

    def test_clusters_same_sha(self):
        now = time.time()
        rows = [
            {
                "status": "failure",
                "repo": "acme/app",
                "sha": "abc",
                "name": "CI",
                "created_at": now - 60,
            },
            {
                "status": "failure",
                "repo": "acme/app",
                "sha": "abc",
                "name": "Lint",
                "created_at": now - 90,
            },
        ]
        self.assertEqual(ga.attention_fail_count(rows, now=now), 1)

    def test_running_does_not_inflate_fail_count(self):
        now = time.time()
        rows = [{
            "status": "running",
            "repo": "acme/app",
            "sha": "abc",
            "created_at": now,
        }]
        self.assertEqual(ga.attention_fail_count(rows, now=now), 0)


class InboxTests(unittest.TestCase):
    def test_empty_without_watched_repos(self):
        self.assertEqual(ga.fetch_inbox("tok", []), [])

    def test_filters_to_watched_repos_and_dedupes(self):
        user = {"login": "mz"}
        review = {
            "total_count": 1,
            "items": [{
                "id": 11,
                "number": 7,
                "title": "Review me",
                "html_url": "https://github.com/acme/web/pull/7",
                "repository_url": "https://api.github.com/repos/acme/web",
                "pull_request": {},
                "user": {"login": "alice"},
                "updated_at": "2026-08-01T12:00:00Z",
            }],
        }
        assigned = {
            "total_count": 2,
            "items": [
                {
                    "id": 11,
                    "number": 7,
                    "title": "Review me",
                    "html_url": "https://github.com/acme/web/pull/7",
                    "repository_url": "https://api.github.com/repos/acme/web",
                    "pull_request": {},
                    "user": {"login": "alice"},
                    "updated_at": "2026-08-01T12:00:00Z",
                },
                {
                    "id": 22,
                    "number": 3,
                    "title": "Fix lint",
                    "html_url": "https://github.com/other/skip/issues/3",
                    "repository_url": "https://api.github.com/repos/other/skip",
                    "user": {"login": "bob"},
                    "updated_at": "2026-08-01T11:00:00Z",
                },
            ],
        }
        mentions = {
            "total_count": 2,
            "items": [
                {
                    # Same PR also @mentions you — review_request must win.
                    "id": 11,
                    "number": 7,
                    "title": "Review me",
                    "html_url": "https://github.com/acme/web/pull/7",
                    "repository_url": "https://api.github.com/repos/acme/web",
                    "pull_request": {},
                    "user": {"login": "alice"},
                    "updated_at": "2026-08-01T13:00:00Z",
                },
                {
                    "id": 33,
                    "number": 9,
                    "title": "Hey @mz",
                    "html_url": "https://github.com/acme/web/issues/9",
                    "repository_url": "https://api.github.com/repos/acme/web",
                    "user": {"login": "carol"},
                    "updated_at": "2026-08-01T10:00:00Z",
                },
            ],
        }

        def fake_get(path, token, query=None, timeout=12):
            if path == "/user":
                return user
            q = (query or {}).get("q") or ""
            if "review-requested" in q:
                return review
            if "assignee:" in q:
                return assigned
            if "mentions:" in q:
                return mentions
            raise AssertionError(q)

        with mock.patch.object(ga, "_get", side_effect=fake_get):
            rows = ga.fetch_inbox("tok", ["acme/web"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["reason"], "review_request")
        self.assertEqual(rows[0]["repo"], "acme/web")
        self.assertEqual(rows[0]["author"], "alice")
        self.assertEqual(rows[0]["number"], 7)
        self.assertEqual(rows[1]["reason"], "mention")
        self.assertEqual(rows[1]["number"], 9)

    def test_attention_summary_names_a_single_repo(self):
        summary = ga.attention_inbox_summary([{
            "reason": "review_request",
            "repo": "acme/web",
            "title": "x",
        }])
        self.assertEqual(summary, "web · review requested")

    def test_attention_summary_names_a_mention(self):
        summary = ga.attention_inbox_summary([{
            "reason": "mention",
            "repo": "acme/web",
            "title": "x",
            "is_pr": False,
        }])
        self.assertEqual(summary, "web · mentioned on issue")

    def test_attention_summary_counts_mixed(self):
        summary = ga.attention_inbox_summary([
            {"reason": "review_request", "repo": "acme/web"},
            {"reason": "mention", "repo": "acme/api"},
            {"reason": "mention", "repo": "acme/web"},
        ])
        self.assertEqual(summary, "1 review request · 2 mentions")


class InboxAttentionAgeTests(unittest.TestCase):
    """An assignment nobody touched in two weeks is debt, not attention."""

    def _row(self, age_s, now):
        return {"id": "1", "reason": "assigned", "repo": "acme/web",
                "created_at": now - age_s}

    def test_recent_row_still_pages(self):
        now = time.time()
        self.assertTrue(ga.inbox_is_fresh(self._row(3600, now), now=now))

    def test_aged_row_drops_out(self):
        now = time.time()
        row = self._row(ga.ATTENTION_INBOX_MAX_AGE_S + 60, now)
        self.assertFalse(ga.inbox_is_fresh(row, now=now))

    def test_row_without_timestamp_stays(self):
        # Same posture as failures: unknown age never hides a row.
        self.assertTrue(ga.inbox_is_fresh({"id": "1"}))

    def test_attention_inbox_keeps_only_fresh(self):
        now = time.time()
        fresh = self._row(60, now)
        aged = dict(self._row(ga.ATTENTION_INBOX_MAX_AGE_S + 60, now), id="2")
        self.assertEqual(ga.attention_inbox([fresh, aged], now=now), [fresh])

    def test_flatten_stamps_the_verdict(self):
        row = ga._flatten_inbox_item({
            "id": 5,
            "number": 7,
            "title": "Old one",
            "html_url": "https://github.com/acme/web/issues/7",
            "repository_url": "https://api.github.com/repos/acme/web",
            "user": {"login": "alice"},
            "updated_at": "2020-01-01T10:00:00Z",
        }, "assigned")
        self.assertFalse(row["needs_attention"])


class InboxAttentionServerTests(unittest.TestCase):
    """The age gate has to reach the rollup and the feed, not just the helper."""

    def _github(self, age_s):
        now = time.time()
        item = {
            "id": "7",
            "reason": "assigned",
            "repo": "acme/web",
            "number": 7,
            "title": "Limit headline to 200 chars",
            "created_at": now - age_s,
            "needs_attention": age_s <= ga.ATTENTION_INBOX_MAX_AGE_S,
            "ago": "1y",
        }
        return {"configured": True, "runs": [], "fail_count": 0,
                "inbox": [item], "inbox_count": 1}

    def _attention(self, github):
        doc = {"github": github, "claude_status": {}, "supabase": {},
               "vercel": {"deployments": []}, "providers": []}
        with mock.patch(
            "headroom_server.app_config.attention_ack_fingerprint",
            return_value="",
        ):
            return headroom_server._build_attention(doc)

    def test_fresh_inbox_lights_the_pip(self):
        attention = self._attention(self._github(3600))
        kinds = {r["kind"] for r in attention["reasons"]}
        self.assertIn("github-inbox", kinds)

    def test_aged_inbox_does_not(self):
        aged = self._github(ga.ATTENTION_INBOX_MAX_AGE_S + 86400)
        attention = self._attention(aged)
        kinds = {r["kind"] for r in attention["reasons"]}
        self.assertNotIn("github-inbox", kinds)

    def test_aged_row_stays_in_the_feed_unflagged(self):
        aged = self._github(ga.ATTENTION_INBOX_MAX_AGE_S + 86400)
        items = headroom_server._build_activity(
            {"deployments": []}, {"commits": []}, github=aged)
        rows = [i for i in items if i["id"].startswith("github-inbox:")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "assigned")
        self.assertFalse(rows[0]["needs_attention"])

    def test_fresh_row_keeps_its_flag(self):
        items = headroom_server._build_activity(
            {"deployments": []}, {"commits": []}, github=self._github(3600))
        rows = [i for i in items if i["id"].startswith("github-inbox:")]
        self.assertTrue(rows[0]["needs_attention"])


if __name__ == "__main__":
    unittest.main()
