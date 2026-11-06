from datetime import datetime, timezone
"""Focused regression gates for Wolverine/Cyclops firsthand sources.

No messages, bids, payments, production jobs, or public service requests are sent.
"""
import unittest
from unittest.mock import patch, MagicMock
from tools import autonomous_commerce_brain as brain
from tools import cyclops_asset_engine as cyclops
from tools import open_web_discovery as discovery


class SourceRecoveryTests(unittest.TestCase):
    def test_wolverine_keeps_open_web_and_firsthand_discussion_sources(self):
        memory=MagicMock()
        memory.choose_queries.return_value=["need external help with reports"]
        memory.budget.return_value={"max_candidates":10,"max_pages":12}
        with patch.object(brain,"discovery_queries",return_value=[
                "need external help with reports"]), \
             patch.object(brain,"open_web_candidates",return_value=([],[])) as finder, \
             patch.object(brain,"review_requester_pages",side_effect=lambda rows:rows), \
             patch.object(brain,"enrich_public_response_route",side_effect=lambda rows:rows):
            rows,issues=brain.candidates(memory)
        self.assertEqual(rows,[])
        self.assertTrue(finder.call_args.kwargs["include_discussions"])
        self.assertTrue(finder.call_args.kwargs["include_paid_issues"])
        self.assertTrue(finder.call_args.kwargs["strict_relevance"])

    def test_semantic_failure_does_not_lose_other_original_invitations(self):
        quote=("We would like outside help improving the reporting workflow, "
               "and proposals from independent developers are welcome by email.")
        lead={"id":"source-a","title":"Seeking help for a report",
              "page_fetched":True,"page_text":quote+" "+"Monthly reconciliation keeps failing. "*4,
              "source_url":"https://example.org/request-a",
              "contact":"requester@example.org", "created_at":datetime.now(timezone.utc).isoformat()}
        unrelated={"id":"source-b","title":"An operational update",
                   "page_fetched":True,
                   "page_text":"Our office has revised its documentation for customers. "*4,
                   "source_url":"https://example.org/update-b",
                   "contact":"admin@example.org"}
        sequence=[ValueError("model_invalid_json"),{
            "requests":[{"candidate_id":"source-a","invited":True,"quote":quote}]
        }, ValueError("model_invalid_json")]
        with patch.object(brain,"model_json",side_effect=sequence) as infer:
            reviewed=brain.review_requester_pages([lead,unrelated])
        self.assertEqual(infer.call_count,3)
        self.assertTrue(brain.public_buyer_invitation(reviewed[0]))
        self.assertFalse(brain.public_buyer_invitation(reviewed[1]))

    def test_cyclops_can_consider_actual_user_request_from_regular_search(self):
        excerpt=("I need help organizing recurring client invoices. "
                 "The spreadsheet takes me hours each Friday to reconcile.")
        row={"id":"original-request","title":"Client invoice problem",
             "source_url":"https://forum.example.org/help/invoices",
             "search_alignment":"keyword_aligned","page_fetched":True,
             "page_text":(excerpt+" ")*3}
        plan={"evidence_candidate_id":"original-request","title":"Invoice reconciling toolkit",
              "description":"Original usable invoice reconciler","audience":"Small companies",
              "pitch":"Save repetitive invoice processing time","price_usd":"12",
              "target_terms":["invoice","reconciliation"],
              "output_files":["reconciler.py"],"production_goal":"Create validated reconciler"}
        decision={"candidate_id":"original-request","decision":"pass",
                  "reason":"No posted transaction figures"}
        with patch.object(cyclops,"model_json",side_effect=[decision,plan]) as judge, \
             patch.object(cyclops,"discover_tools",return_value={}):
            concept=cyclops.choose_asset_concept([row])
        self.assertEqual(judge.call_count,2)
        self.assertEqual(concept["evidence_candidate_id"],"original-request")
        self.assertIn("first-person",concept["market_validation"]["reason"])

    def test_editorial_article_cannot_force_cyclops_fallback(self):
        row={"id":"editorial","title":"Guide to business invoice tools",
             "source_url":"https://example.org/blog/invoices",
             "page_fetched":True,"search_alignment":"keyword_aligned",
             "page_text":("I need help with invoices, said a reader. "
                          "Comparing the top accounting apps is challenging. ")*3}
        with patch.object(cyclops,"model_json",return_value={
                "candidate_id":"editorial","decision":"pass",
                "reason":"General article, not original user request"}):
            with self.assertRaises(cyclops.MarketDeclined):
                cyclops.choose_asset_concept([row])


if __name__=="__main__":
    unittest.main()
