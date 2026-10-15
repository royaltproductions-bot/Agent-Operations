import unittest
from tools import hn_contract_invitations as h

BASE = {
    "hits": [
        {"title": "Ask HN: Who is hiring? (September 2026)", "created_at_i": 10, "objectID": "100"},
        {"title": "Ask HN: Who is hiring? (October 2026)", "created_at_i": 20, "objectID": "200"},
        {"title": "Random who is hiring not official", "created_at_i": 999, "objectID": "300"},
    ]
}

def comment(ident, raw, **additional):
    return {"id": ident, "text": raw, "created_at": "2026-10-15T14:00:00Z", **additional}


class HNContractTests(unittest.TestCase):
    def test_select_newest_official_thread_only(self):
        self.assertEqual(h.find_hiring_thread(BASE)[1], 200)
        with self.assertRaisesRegex(ValueError, "not found"):
            h.find_hiring_thread({"hits": []})

    def test_contract_and_matching_skill_are_both_required(self):
        a = comment(10, "<p>Studio | AI Video Content Creator | Remote | Contract</p><p>Paid freelance AI filmmaking videos.</p>")
        result = h.score_comment(a)
        self.assertIsNotNone(result)
        self.assertIn("ai-video", result["skill_categories"])
        self.assertFalse(result["payment_verified"])
        self.assertEqual(result["primary_url"], "https://news.ycombinator.com/item?id=10")
        self.assertNotIn("Paid freelance AI filmmaking videos", str(result))
        self.assertIsNone(h.score_comment(comment(11, "<p>Senior remote engineer, full time salary.</p>")))
        self.assertIsNone(h.score_comment(comment(12, "<p>Freelance work moving boxes.</p>")))

    def test_protects_against_unpaid_or_deleted(self):
        self.assertIsNone(h.score_comment(comment(13, "Voluntary unpaid AI video contract")))
        self.assertIsNone(h.score_comment(comment(14, "Contract AI video paid", deleted=True)))
        self.assertIsNone(h.score_comment(comment(15, "", dead=True)))

    def test_computes_sorted_deduplicated_output(self):
        thread = {"id": 200, "children": [
            comment(44, "Company A | Freelance customer success contract | Remote"),
            comment(45, "Company B | Content marketing consulting | Remote. Hourly paid projects"),
            comment(45, "Company B | Content marketing consulting | Remote. Hourly paid projects"),
        ]}
        result = h.discover(BASE, thread)
        self.assertEqual(result["source_comment_count"], 3)
        self.assertEqual(result["potential_contract_mention_count"], 2)
        self.assertEqual(len(result["top_review_only"]), 2)
        self.assertEqual(result["paid_orders_verified"], 0)
        with self.assertRaisesRegex(ValueError, "differs"):
            h.discover(BASE, {"id": 999, "children": []})

    def test_safe_remote_source_constraint(self):
        with self.assertRaisesRegex(ValueError, "Only documented"):
            h.fetch_json("http://localhost:8000/secret")


if __name__ == "__main__":
    unittest.main()
