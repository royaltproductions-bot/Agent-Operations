import concurrent.futures
from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import patch

from tools import parallel_opportunity_radar as radar


def fake_issue(number, title, body, comments=0, repo="an-operator/real-repo"):
    return {
        "html_url": "https://github.com/" + repo + "/issues/" + str(number),
        "title": title,
        "body": body,
        "state": "open",
        "comments": comments,
        "created_at": "2026-10-15T15:00:00Z",
    }


class ParallelDiscoveryTests(unittest.TestCase):
    def test_exactly_ten_independent_lanes(self):
        self.assertEqual(len(radar.SEARCH_LANES), 10)

    def test_real_prospect_only_after_cash_and_no_required_upfront_spend(self):
        good = fake_issue(12, "Fix Python shell driver — $75 bounty",
                          "Acceptance criteria for a merged PR; paid after merge via USDC.")
        result = radar.prospect(good, "python")
        self.assertEqual(result["bounty_usd_hint_not_verified"], 75)
        self.assertIn("requires_manual", result["status"])
        bad = fake_issue(13, "Post and fund a task, bounty $100",
                         "Fund a task with your own money first; deposit required.")
        self.assertIsNone(radar.prospect(bad, "cash"))

    def test_rejects_aggregators_and_unfunded_issues(self):
        agg = fake_issue(1, "Bounty $100", "Please claim", repo="foo/bounty-plaza")
        self.assertIsNone(radar.prospect(agg, "cash"))
        free = fake_issue(2, "Improve Python docs", "Would be a nice addition.")
        self.assertIsNone(radar.prospect(free, "documentation"))

    def test_product_price_ceiling_is_not_a_bounty(self):
        issue = fake_issue(99, "Bounty: cut subscription pricing",
                           "Keep the platform price ceiling at $100,000 and a $249 plan. No cash payout.")
        self.assertIsNone(radar.prospect(issue, "cash"))

    def test_conditional_not_funded_reward_is_not_a_confirmed_bounty(self):
        issue = fake_issue(100, "Proposal: $100 bounty on gamification",
                           "I am willing to place a $100 bounty if anyone wants to work on this.")
        self.assertIsNone(radar.prospect(issue, "cash"))

    def test_large_competition_reduces_score(self):
        item = fake_issue(2, "Fix bug bounty $75", "Paid after merged PR")
        low = radar.prospect(item, "python")["score_for_review_only"]
        item["comments"] = 40
        self.assertLess(radar.prospect(item, "python")["score_for_review_only"], low)

    def test_isolates_failed_lanes_and_deduplicates_by_url(self):
        def fake_api(query, since_date, token):
            if "javascript" in query:
                raise RuntimeError("Temporary rate limit")
            return [
                fake_issue(1, "Fix paid automation bug — $75 bounty",
                           "Acceptance criteria; USDC bounty for merged PR."),
                fake_issue(2, "Need better docs", "No cash reward"),
            ]
        result = radar.run(now=datetime(2026, 10, 15, tzinfo=timezone.utc), api=fake_api)
        self.assertEqual(result["configured_lanes"], 10)
        self.assertEqual(result["successful_lanes"], 9)
        self.assertEqual(result["unique_candidates"], 1)
        self.assertEqual(len(result["top_candidates"]), 1)
        self.assertIn("javascript", result["lane_errors"])

    def test_date_scoped_search_and_no_external_mutations(self):
        u = radar.search_url(radar.SEARCH_LANES["python"], "2026-10-01")
        self.assertIn("api.github.com/search/issues?", u)
        self.assertIn("created%3A%3E%3D2026-10-01", u)


if __name__ == "__main__":
    unittest.main()
