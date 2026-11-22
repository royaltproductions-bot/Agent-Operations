import io
import json
import unittest
from datetime import datetime, timezone
from tools import gambit_github_bounties as g


class GithubBountyDiscoveryTests(unittest.TestCase):
    def test_reward_labels_allow_discovery_to_300_without_bidding(self):
        self.assertEqual(g.parse_reward_cents({"labels": [{"name": "bounty:$20"}]}), 2000)
        self.assertEqual(g.parse_reward_cents({"labels": [{"name": "bounty:$150"}]}), 15000)
        self.assertEqual(g.parse_reward_cents({"labels": [{"name": "bounty:$151"}]}), 15100)
        self.assertIsNone(g.parse_reward_cents({"labels": [{"name": "bounty:$500"}]}))
        self.assertIsNone(g.parse_reward_cents({"labels": [{"name": "bounty"}]}))

    def test_observation_is_read_only_and_idempotent(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n):
                return json.dumps({"items": [
                    {"id": 123, "html_url": "https://github.com/example/repo/issues/1",
                     "title": "Research task", "comments_url": "https://api.github.com/repos/example/repo/issues/1/comments", "created_at": "2026-11-19T16:00:00Z",
                     "labels": [{"name": "bounty:$20"}], "updated_at": "2026-11-19T16:00:00Z"},
                    {"id": 124, "html_url": "https://github.com/example/repo/issues/2",
                     "title": "Too expensive", "labels": [{"name": "bounty:$500"}], "updated_at": "2026-11-19T16:00:00Z"},
                ]}).encode()
        calls = []
        class CommentResponse:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n):
                return json.dumps([{"user":{"login":"algora-pbc[bot]"},
                                    "body":"Algora bounty reward $20"}]).encode()
        def opener(request, timeout):
            calls.append(request.full_url)
            return CommentResponse() if "/comments?" in request.full_url else Response()
        state = {}
        now = datetime(2026, 11, 19, tzinfo=timezone.utc)
        result = g.observe(state, opener=opener, now=now)
        self.assertEqual(result["open_count"], 1)
        self.assertEqual(result["listings"][0]["budget_cents"], 2000)
        self.assertEqual(result["bidding_status"], "platform_native_claim_not_verified")
        g.observe(state, opener=opener, now=now)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["rejected"][0]["reason"], "no_explicit_20_to_300_usd_reward")
        self.assertIn("updated%3A%3E%3D2026-11-05", calls[0])
        self.assertEqual(result["listings"][0]["payer_mechanism"], "algora")

    def test_unrelated_money_and_unverified_payer_rejected(self):
        self.assertIsNone(g.parse_reward_cents({
            "title": "Technical proposal", "body": "Our treasury has $24.58M in assets."}))
        item = {"id": 5, "html_url":"https://github.com/example/repo/issues/5",
                "title":"$25 bounty", "state":"open",
                "updated_at":"2026-11-22T12:00:00Z"}
        self.assertEqual(g.payer_mechanism(item), "unverified")
        self.assertIsNone(g.rejection_reason(item, datetime(
            2026,11,22,tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
