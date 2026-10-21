"""Local-model failures must be observable, not crash entire scheduled operations."""
import json
import unittest
from unittest.mock import patch
from tools import autonomous_commerce_brain as brain

class RecoveryTests(unittest.TestCase):
    def test_rephrased_prior_market_warning_does_not_block_new_evidence(self):
        prior=[{"hypotheses":[{
            "lane":"commissioned_services","buyer":"A Portland bakery seeking inventory tracking",
            "problem":"Bakery stock spoilage","business_model":"Python inventory system",
            "query":"bakery inventory automation","channel":"hackernews"}]}]
        def propose(schema,prompt,context):
            return {"lane":"commissioned_services","buyer":"Independent cafes",
                    "customer_problem":"Bakery inventory stock spoilage",
                    "offer":"Python inventory monitoring system",
                    "delivery_using_existing_tools":"Python CSV",
                    "route_to_buyer":"public paid invitation",
                    "search_channel":"github",
                    "search_query":"bakery stock inventory tool",
                    "reason":"Check a different independently evidenced source"}
        with patch.object(brain,"past",return_value=prior), \
             patch.object(brain,"MAX_SEARCHES",1):
            row=brain.run(propose=propose,evaluate=lambda *a:None,
                          search=lambda s:[],original=lambda *a:"",
                          live=False)
        self.assertEqual(len(row["hypotheses"]),1)
        self.assertIn("repetition_warning",
                      [f["reason"] for f in row["failures_or_blockers"]])

    def test_model_json_accepts_ignored_extra_fields_and_missing_optional_lane(self):
        class FakeResponse:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n=-1):
                return json.dumps({"message":{"content":json.dumps({
                    "buyer":"businesses","customer_problem":"manual reports",
                    "offer":"paid Python script",
                    "delivery_using_existing_tools":"Python",
                    "route_to_buyer":"public buyer invitation",
                    "search_channel":"hackernews",
                    "search_query":"paid python contract",
                    "reason":"real demand hypothesis",
                    "unexpected_comment":"harmless extra output"})}}).encode()
        obj=brain.model_json(brain.STRATEGY_SCHEMA,brain.MISSION,
                             {},opener=lambda req,timeout:FakeResponse())
        self.assertNotIn("unexpected_comment",obj)
        self.assertNotIn("lane",obj)
        self.assertEqual(obj["search_channel"],"hackernews")

    def test_invalid_provider_selection_does_not_abort_business_loop(self):
        proposal={
            "buyer":"shops","customer_problem":"manual CSV processing",
            "offer":"Python report service","delivery_using_existing_tools":"Python",
            "route_to_buyer":"public contract invitation",
            "search_channel":"hackernews","search_query":"Python CSV services",
            "reason":"current public demand"
        }
        def invalid_tool(*args):
            raise ValueError("model_invalid_field")
        with patch.object(brain,"past",return_value=[]), \
             patch.object(brain,"render_status",return_value={"needs_deployment":False}), \
             patch.object(brain,"capability_status",return_value={"stripe":True,"buffer":False}), \
             patch.object(brain,"MAX_SEARCHES",1):
            result=brain.run(propose=lambda *a:proposal,
                             evaluate=lambda *a:None,
                             search=lambda *a:[],
                             original=lambda *a:"",
                             tool_decider=invalid_tool,
                             live=False)
        self.assertEqual(result["tool_actions"][0]["status"],"invalid_model_choice_skipped")
        self.assertEqual(len(result["hypotheses"]),1)
        self.assertEqual(result["qualified_external_contacts"],0)

    def test_invalid_planner_strategy_is_recorded_not_silently_replaced(self):
        with patch.object(brain,"past",return_value=[]), \
             patch.object(brain,"MAX_SEARCHES",2):
            result=brain.run(propose=lambda *a: (_ for _ in ()).throw(ValueError("invalid")),
                             evaluate=lambda *a:None,
                             search=lambda *a:[],
                             original=lambda *a:"",
                             live=False)
        self.assertEqual(len(result["hypotheses"]),0)
        self.assertTrue(result["model_invoked"])
        self.assertEqual([x["reason"] for x in result["failures_or_blockers"]],
                         ["model_strategy_invalid","model_strategy_invalid"])
        self.assertIsNone(result["verified_payment_usd"])

    def test_invalid_evaluation_does_not_qualify_a_lead(self):
        plan={"buyer":"buyer","customer_problem":"CSV",
              "offer":"script","delivery_using_existing_tools":"Python",
              "route_to_buyer":"explicit publicly invited project",
              "search_channel":"hackernews","search_query":"Python contract paid",
              "reason":"high relevance"}
        hit={"id":"1234","url":"https://news.ycombinator.com/item?id=1234",
             "text":"Paid Python contractor email foo@bar.org","created_at":"2026-10-21"}
        with patch.object(brain,"past",return_value=[]),patch.object(brain,"MAX_SEARCHES",1):
            result=brain.run(propose=lambda *a:plan,
                   evaluate=lambda *a:(_ for _ in ()).throw(ValueError("invalid")),
                   search=lambda *a:[hit],original=lambda *a:"",live=True)
        self.assertFalse(result["actions"])
        self.assertEqual(result["failures_or_blockers"][0]["reason"],
                         "model_candidate_evaluation_invalid")

if __name__=="__main__":
    unittest.main()
