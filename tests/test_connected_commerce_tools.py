"""Existing account tool bridge: no external network or provider writes."""
import os
import unittest
from unittest.mock import patch
from tools import connected_commerce_tools as provider
from tools import autonomous_commerce_brain as brain

class ConnectedToolTests(unittest.TestCase):
    def test_existing_provider_status_reports_bools_not_secret_values(self):
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY":"private-test-key",
                                      "BUFFER_API_KEY":"private-buffer-key",
                                      "AGENTMAIL_API_KEY":"private-agent-key",
                                      "GH_TOKEN":"opaque-github-token"}):
            result=provider.capability_status()
        self.assertTrue(result["stripe"])
        self.assertTrue(result["buffer"])
        self.assertTrue(result["agentmail"])
        self.assertTrue(result["github_workflow_dispatch"])
        self.assertNotIn("private-test-key",repr(result))
        self.assertNotIn("private-buffer-key",repr(result))

    def test_stripe_inventory_reads_existing_links_without_customers(self):
        with patch.dict(os.environ,{"STRIPE_SECRET_KEY":"private"}), \
             patch("tools.commerce_operator.stripe_get", return_value={
               "data":[{"id":"plink_123","url":"https://buy.stripe.com/example",
                        "active":True,"customer_email":"secret@example.org"}]}):
            result=provider.execute_tool("stripe_offers")
        self.assertEqual(result["status"],"live_api_verified")
        self.assertEqual(result["offer_count"],1)
        self.assertEqual(result["offers"][0]["active"],True)
        self.assertNotIn("secret@example.org",repr(result))
        self.assertIsNone(result["new_revenue_verified"])

    def test_unknown_or_unapproved_actions_are_rejected(self):
        with self.assertRaises(ValueError):
            provider.execute_tool("execute_shell")
        with self.assertRaises(ValueError):
            provider.dispatch_workflow("../other.yml")
        with self.assertRaises(ValueError):
            provider.dispatch_workflow("ai-handoff-dispatch.yml")

    def test_missing_credentials_do_not_invoke_providers(self):
        with patch.dict(os.environ,{"STRIPE_SECRET_KEY":""}):
            self.assertEqual(provider.inspect_stripe()["status"],"not_configured")
        with patch.dict(os.environ,{"BUFFER_API_KEY":""}):
            self.assertEqual(provider.inspect_buffer()["status"],"not_configured")

    def test_brain_can_choose_a_live_tool_before_strategy(self):
        strategy={"buyer":"local merchants","customer_problem":"CSV reconciliation",
                  "offer":"automate vendor reporting",
                  "delivery_using_existing_tools":"Python processing",
                  "route_to_buyer":"public project invitation","search_channel":"hackernews",
                  "search_query":"paid CSV automation","reason":"actual stated demand"}
        observed=[]
        def propose(schema,prompt,context):
            observed.append(context.get("connected_service_observation"))
            return strategy
        def choose(schema,prompt,context):
            self.assertIn("stripe_offers",schema["properties"]["tool"]["enum"])
            return {"tool":"stripe_offers","reason":"Inspect available checkout capability"}
        with patch.object(brain,"past",return_value=[]), \
             patch.object(brain,"capability_status", return_value={"stripe":True,"buffer":False}), \
             patch.object(brain,"execute_tool", return_value={
                "status":"live_api_verified","offer_count":2,"offers":[]}), \
             patch.object(brain,"MAX_SEARCHES",1):
            result=brain.run(propose=propose,evaluate=lambda *a:None,
                             search=lambda s:[],original=lambda *a:"",
                             live=False,tool_decider=choose)
        self.assertEqual(result["tool_actions"][0]["tool"],"stripe_offers")
        self.assertEqual(result["tool_actions"][0]["offer_count"],2)
        self.assertEqual(observed[0]["tool"],"stripe_offers")
        self.assertEqual(result["qualified_external_contacts"],0)

if __name__=="__main__":
    unittest.main()
