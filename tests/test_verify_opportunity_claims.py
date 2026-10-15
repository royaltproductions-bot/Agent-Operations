import unittest
from datetime import datetime, timezone
from tools import verify_opportunity_claims as gate

ISSUE = "https://github.com/owner/proj/issues/42"


class TestCompetitionGate(unittest.TestCase):
    def test_catches_competing_pr_same_repo(self):
        def fake(url, token):
            if url.endswith("/issues/42"):
                return {"state": "open"}
            if "/comments?" in url:
                return []
            if "/pulls?" in url:
                return [{"title": "Fixes #42", "body": "", "html_url": "https://github.com/owner/proj/pull/12"}]
            raise AssertionError(url)
        r = gate.review({"url": ISSUE}, fake, "")
        self.assertEqual(r["decision"], "competing_fix_or_pr")
        self.assertEqual(len(r["competing_pr_urls"]), 1)
        self.assertFalse(r["funds_independently_verified"])

    def test_catches_cross_repo_pr_in_comments(self):
        def fake(url, token):
            if url.endswith("/issues/42"):
                return {"state": "open"}
            if "/comments?" in url:
                return [{"body": "Earlier in #42 PR https://github.com/owner/other/pull/22 fixed this",
                         "html_url": "https://github.com/owner/proj/issues/42#issuecomment-1"}]
            if "/pulls?" in url:
                return []
            raise AssertionError(url)
        r = gate.review({"url": ISSUE}, fake, "")
        self.assertEqual(r["decision"], "competing_fix_or_pr")
        self.assertIn("https://github.com/owner/other/pull/22", r["competing_pr_urls"])

    def test_claimed_without_pr_not_ready(self):
        def fake(url, token):
            if url.endswith("/issues/42"):
                return {"state": "open"}
            if "/comments?" in url:
                return [{"body": "/attempt #42", "html_url": "https://github.com/owner/proj/issues/42#issuecomment-1"}]
            if "/pulls?" in url:
                return []
            raise AssertionError(url)
        r = gate.review({"url": ISSUE}, fake, "")
        self.assertEqual(r["decision"], "existing_claim_or_attempt")

    def test_uncontested_is_not_funded(self):
        def fake(url, token):
            if url.endswith("/issues/42"):
                return {"state": "open"}
            return []
        r = gate.review({"url": ISSUE}, fake, "")
        self.assertEqual(r["decision"], "uncontested_not_funding_verified")
        self.assertFalse(r["qualifies_as_paid_order"])

    def test_fail_closed_on_remote_error_and_dedup(self):
        def fake(url, token):
            raise RuntimeError("read prohibited")
        r = gate.run([{"url": ISSUE}, {"url": ISSUE}], fake, "", datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.assertEqual(r["reviewed_items"], 1)
        self.assertEqual(r["decision_counts"], {"verification_error": 1})
        self.assertEqual(r["verified_paid_orders"], 0)

    def test_closed_issue_and_invalid_input(self):
        def fake(url, token):
            return {"state": "closed"}
        self.assertEqual(gate.review({"url": ISSUE}, fake, "")["decision"], "closed")
        with self.assertRaises(ValueError):
            gate.current_issue_ref("http://localhost/issues/42")


if __name__ == "__main__":
    unittest.main()
