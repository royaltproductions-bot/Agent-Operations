"""Focused, offline checks for the independently running X-variants.

These exercise the CURRENT production code, not superseded commerce prompts.
No external website, payment, GitHub write, or email is touched.
"""
import unittest
from unittest.mock import patch
from tools import open_web_discovery as web
from tools import autonomous_commerce_brain as brain


class WebEfficiencyTests(unittest.TestCase):
    def test_public_http_has_finite_transport_timeout(self):
        class Response:
            headers={"Content-Type":"text/plain"}
            def geturl(self): return "https://example.com/work"
            def read(self,limit): return b"Live public brief"
            def __enter__(self): return self
            def __exit__(self,*args): return False
        with patch.object(web._OPENER,"open",return_value=Response()) as opener:
            _,kind,body=web._open("https://example.com/work")
        self.assertEqual(body,b"Live public brief")
        self.assertIn("text/plain",kind)
        self.assertGreater(opener.call_args.kwargs["timeout"],0)

    def test_local_model_keeps_full_workflow_inference_budget(self):
        import io
        with patch.object(brain.urllib.request,"urlopen",
                          return_value=io.BytesIO(b'{"message":{"content":"{}"}}')) as opener:
            self.assertEqual(brain.model_json({"type":"object"},"system",{}),{})
        self.assertNotIn("timeout",opener.call_args.kwargs)
        import json
        self.assertEqual(json.loads(opener.call_args.args[0].data)["keep_alive"],"60m")

    def test_soft_interest_ranking_is_industry_and_site_neutral(self):
        contact={"title":"Seeking a contractor for an original workflow",
                 "snippet":"Submit your proposal for paid project work"}
        article={"title":"General advice for teams","snippet":"Definitions and history"}
        self.assertGreater(web.public_request_signal(contact),
                           web.public_request_signal(article))
        self.assertEqual(web.public_request_signal(article),0)

    def test_reuse_already_fetched_requester_page(self):
        row={"source_url":"https://example.com/request",
             "title":"Solicitation","snippet":"",
             "text":"Request for proposals: seeking a contractor for useful work.",
             "page_text":"Request for proposals: seeking a contractor for useful work.",
             "contact":None,"page_fetched":True,
             "public_links":[{"url":"https://example.com/contact",
                              "title":"Contact us for proposals"}]}
        with patch.object(brain,"fetch_public_text",
                          return_value=("https://example.com/contact",
                                        "Submit proposal to work@example.com")) as fetch:
            enriched=brain.enrich_public_response_route([row])
        self.assertEqual(enriched[0]["contact"],"work@example.com")
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(fetch.call_args.args[0],"https://example.com/contact")

    def test_invited_request_ranks_above_generic_contact(self):
        generic={"id":"web:a","source_url":"https://example.com/about",
                 "page_fetched":True,"contact":"hello@example.com",
                 "page_text":"General information.",
                 "title":"About us","snippet":"","text":"General information."}
        invited={"id":"web:b","source_url":"https://example.org/brief",
                 "page_fetched":True,"contact":"bids@example.org",
                 "page_text":"Seeking a contractor. Submit your proposal.",
                 "title":"Contract request","snippet":"",
                 "text":"Seeking a contractor. Submit your proposal."}
        seen={}
        def mock_plan(schema,system,observation,**kwargs):
            seen["candidates"]=observation["candidates"]
            return {"action":"reject","reason":"verification needed"}
        with patch.object(brain,"recent_sent_subjects",return_value=[]), \
             patch.object(brain,"model_json",side_effect=mock_plan):
            brain.choose_offer([generic,invited])
        self.assertEqual(seen["candidates"][0]["candidate_id"],"web:b")

    def test_search_snippet_and_generic_role_email_never_authorize_send(self):
        row={"id":"search-only","source_url":"https://example.com/template",
             "page_fetched":True,"contact":"info@example.com",
             "title":"Project template", "snippet":"Request for bids: need a developer",
             "text":"Request for bids: need a developer. Contact info@example.com",
             "page_text":"Download our ready-made work agreement template."}
        self.assertFalse(brain.public_buyer_invitation(row))
        with patch.object(brain,"recent_sent_subjects",return_value=[]), \
             patch.object(brain,"model_json") as model:
            chosen,result=brain.choose_offer([row])
        self.assertIsNone(chosen)
        self.assertEqual(result["reason"],"no_verified_contactable_buyer_invitation")
        model.assert_not_called()
        with patch.object(brain,"agent_send") as send:
            result=brain.send_offer(row,{"price_usd":"10","pitch":"irrelevant",
                                         "deliverable":"irrelevant"},{"checkout_url":"https://example.org/checkout"})
        self.assertEqual(result["result"],"unverified_buyer_invitation")
        send.assert_not_called()

    def test_live_source_must_have_its_own_visible_request(self):
        row={"source_url":"https://example.org/open-request","page_fetched":True,
             "contact":"bids@example.org","page_text":"We are accepting bids from outside contractors. Send your proposal by November 30.",
             "title":"Open vendor request","snippet":"","text":"Open vendor request"}
        self.assertTrue(brain.public_buyer_invitation(row))

    def test_research_searches_are_not_recycled_into_model_context(self):
        from tools import research_memory
        memory=research_memory.ResearchMemory.__new__(research_memory.ResearchMemory)
        memory.state={"queries":[{"query":"Old repetitive example topic",
                                  "new_pages":0}],
                      "pages":[],"frontier":[],"queued_queries":[{"query":"Stale side hustle advice"}]}
        memory.peer=[]
        context=memory.plan_context()
        self.assertNotIn("Old repetitive example topic",str(context))
        self.assertNotIn("Stale side hustle advice",str(context))
        with patch.object(research_memory,"NOW",return_value=research_memory.NOW()):
            new_queries=memory.choose_queries(["Unrelated independent public inquiry"])
        self.assertEqual(new_queries,["Unrelated independent public inquiry"])


class WorkshopOwnershipTests(unittest.TestCase):
    def test_container_writes_as_host_nonroot_owner(self):
        import os
        from tools import cyclops_workshop as workshop
        from pathlib import Path
        from subprocess import CompletedProcess
        with patch.object(workshop.subprocess,"run",
                          return_value=CompletedProcess([],0,"","")) as run:
            workshop._container(["python3","/work/build.py"],Path("/tmp/production-test"))
        command=run.call_args.args[0]
        self.assertEqual(command[command.index("--user")+1],
                         str(os.getuid())+":"+str(os.getgid()))
        self.assertEqual(command[command.index("--network")+1],"none")
        self.assertIn("--read-only",command)
        self.assertIn("--cap-drop",command)

    def test_private_mode_generated_file_is_still_readable(self):
        import os
        from subprocess import CompletedProcess
        from tools import cyclops_workshop as workshop
        def pretend_build(command,folder):
            target=folder/"finished.txt"
            target.write_text("Authentic finished content",encoding="utf-8")
            target.chmod(0o600)
            self.assertEqual(folder.stat().st_mode & 0o777,0o700)
            return CompletedProcess([],0,"","")
        with patch.object(workshop,"_container",side_effect=pretend_build):
            result=workshop.manufacture("print('made')",["finished.txt"])
        self.assertEqual(result[0]["filename"],"finished.txt")
        self.assertGreater(result[0]["size_bytes"],0)


class CyclopsEfficiencyTests(unittest.TestCase):
    def test_real_problem_pages_go_first_without_discarding_other_pages(self):
        try:
            from tools import cyclops_asset_engine as cyclops
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("cryptography"):
                self.skipTest("Cyclops encryption dependency is installed by its runner")
            raise
        class Memory:
            def choose_queries(self,proposals): return ["genuine need"]
            def budget(self): return {"max_candidates":10,"max_pages":14}
        rows=[
            {"id":"a","title":"Advice for professionals","snippet":"Definitions",
             "text":"A reference article","source_url":"https://one.example/a","page_fetched":True},
            {"id":"b","title":"We need help reconciling records",
             "snippet":"Trying to fix a recurring task","text":"Help wanted",
             "source_url":"https://two.example/b","page_fetched":True},
        ]
        with patch.object(cyclops,"discovery_queries",return_value=["genuine need"]), \
             patch.object(cyclops,"open_web_candidates",return_value=(rows,[])):
            output,issues=cyclops.demand_candidates(Memory())
        self.assertEqual([r["id"] for r in output],["b","a"])
        self.assertEqual(issues,[])

    def test_manufacturing_evidence_prioritizes_fetched_page(self):
        from tools import cyclops_asset_engine as cyclops
        row={"id":"real:1","source_url":"https://example.org/request",
             "title":"Real problem","text":"Result snippet claims an entirely different issue",
             "page_text":"Our actual process takes a day each week",
             "page_fetched":True}
        view=cyclops.demand_view([row])
        self.assertIn("actual process",view[0]["excerpt"])
        self.assertTrue(view[0]["source_fetched"])


if __name__=="__main__":
    unittest.main()
