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
                     "title": "Research task", "created_at": "2026-11-19T16:00:00Z",
                     "labels": [{"name": "bounty:$20"}], "updated_at": "2026-11-19T16:00:00Z"},
                    {"id": 124, "html_url": "https://github.com/example/repo/issues/2",
                     "title": "Too expensive", "labels": [{"name": "bounty:$500"}], "updated_at": "2026-11-19T16:00:00Z"},
                ]}).encode()
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response()
        state = {}
        now = datetime(2026, 11, 19, tzinfo=timezone.utc)
        result = g.observe(state, opener=opener, now=now)
        self.assertEqual(result["open_count"], 1)
        self.assertEqual(result["listings"][0]["budget_cents"], 2000)
        self.assertEqual(result["bidding_status"], "platform_native_claim_not_verified")
        g.observe(state, opener=opener, now=now)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["rejected"][0]["reason"], "no_explicit_20_to_300_usd_reward")
        self.assertIn("updated%3A%3E%3D2026-11-05", calls[0])


if __name__ == "__main__":
    unittest.main()
