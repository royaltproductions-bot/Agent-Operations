"""Independent model-operated commercial tool tests. Zero credentials/network calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import external_market_agent as agent

class OperatorTests(unittest.TestCase):
    def test_model_output_invalid_json_fails_closed(self):
        class Bad:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"message":{"content":"not json"}}'
        with self.assertRaises(ValueError):
            agent.llm_turn({}, urlopen=lambda req, timeout: Bad())

    def test_local_model_must_choose_an_existing_enum_option(self):
        class Response:
            def __init__(self, value):
                self.value = value
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps({"message": {"content": self.value}}).encode()
        context = {"inbound": [], "existing_site_faq_published": False,
                   "public_candidate_hints": {"hiring": [], "issues": []},
                   "previous_actions": [], "this_run": []}
        good = lambda req, timeout: Response('{"choice":"publish_faq|privacy"}')
        selected = agent.llm_turn(context, urlopen=good)
        self.assertEqual(selected["action"], "publish_faq")
        self.assertEqual(selected["target"], "privacy")
        bad = lambda req, timeout: Response('{"choice":"delete_repository"}')
        with self.assertRaisesRegex(ValueError, "model_choice_not_in_allowlist"):
            agent.llm_turn(context, urlopen=bad)

    def test_evidence_is_not_rediscovered_in_next_run(self):
        context = {"inbound": [], "existing_site_faq_published": True,
                   "public_candidate_hints": {
                       "hiring":[{"id":"49939260"},{"id":"49982803"}],
                       "issues":[]},
                   "previous_actions":[{"tool":"inspect_hiring",
                                        "target":"49939260",
                                        "result":"public_post_not_verified_paid_contract"}],
                   "this_run":[{"selected_action":"inspect_hiring",
                                "tool_result":{"id":"49982803"}}]}
        options=agent.allowed_choices(context)
        self.assertNotIn("inspect_hiring|49939260",options)
        self.assertNotIn("inspect_hiring|49982803",options)
        self.assertIn("finish",options)

    def test_invalid_targets_and_outbound_blocked(self):
        with self.assertRaises(ValueError):
            agent.inspect_hiring("12", [])
        with self.assertRaises(ValueError):
            agent.inspect_issue("https://evil.example/issues/1", [])
        with self.assertRaises(ValueError):
            agent.publish_faq("delete_repository", False)
        with self.assertRaises(ValueError):
            agent.answer_inbound(None, "price", [], True)
        with self.assertRaises(ValueError):
            agent.public_http_json("http://hn.algolia.com/api/v1/items/123", "hn.algolia.com")

    def test_inbound_restrictions_and_idempotence(self):
        item = {"id":"aabb", "from":"buyer@example.com",
                "subject":"VendorSettle refund", "text":"I demand a refund"}
        self.assertEqual(agent.answer_inbound(item, "price", [], False)["result"],
                         "manual_private_handling_required")
        item["subject"], item["text"] = "hello", "Buy our thing."
        self.assertEqual(agent.answer_inbound(item, "price", [], False)["result"],
                         "not_an_inquiry_about_owned_service")
        item["subject"] = "VendorSettle pricing question"
        self.assertEqual(agent.answer_inbound(item, "price", [], False)["result"],
                         "would_send_in_dry_run")
        history=[{"tool":"reply_inbound", "target":"aabb", "result":"sent"}]
        self.assertEqual(agent.answer_inbound(item, "price", history, True)["result"],
                         "already_replied")

    def test_model_chooses_and_executes_owned_site_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/"storefront"
            site.mkdir()
            (site/"index.html").write_text(
                '<html><nav><strong>VendorSettle Desk</strong></nav></html>')
            seq=iter([
                {"action":"publish_faq","target":"privacy","reply_kind":"none",
                 "reason":"Privacy FAQ is missing"},
                {"action":"finish","target":"","reply_kind":"none","reason":"complete"}
            ])
            with patch.object(agent,"SITE",site), \
                 patch.object(agent,"FAQ",site/"guide"/"index.html"), \
                 patch.object(agent,"get_inbound",return_value=([],"connected")), \
                 patch.object(agent,"past_actions",return_value=[]), \
                 patch.object(agent,"candidates",return_value={"hiring":[],"issues":[]}):
                report=agent.operate(llm=lambda context: next(seq))
                self.assertTrue((site/"guide"/"index.html").is_file())
                self.assertIn('href="/guide/"',(site/"index.html").read_text())
                self.assertEqual(report["owned_site_files_written"],1)
                self.assertIsNone(report["verified_revenue_usd"])
                self.assertEqual(report["engine"],"on_runner_ollama")

    def test_prevent_duplicate_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)
            (site/"index.html").write_text("<html/>")
            faq=site/"guide"/"index.html"
            faq.parent.mkdir()
            faq.write_text("existing")
            with patch.object(agent,"SITE",site),patch.object(agent,"FAQ",faq):
                result=agent.publish_faq("scope",True)
            self.assertEqual(result["result"],"already_published")
            self.assertEqual(faq.read_text(),"existing")

if __name__ == "__main__":
    unittest.main()
