"""Focused, offline checks for the independently running X-variants.

These exercise the CURRENT production code, not superseded commerce prompts.
No external website, payment, GitHub write, or email is touched.
"""
import unittest
from unittest.mock import patch
from tools import open_web_discovery as web
from tools import autonomous_commerce_brain as brain


def cyclops_or_skip(test):
    try:
        from tools import cyclops_asset_engine
        return cyclops_asset_engine
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("cryptography"):
            test.skipTest("Cyclops encryption dependency is installed by its runner")
        raise


class WebEfficiencyTests(unittest.TestCase):
    def test_market_search_keeps_first_person_phrases(self):
        self.assertEqual(
            web.compact_query('"I am struggling to reconcile invoices" help'),
            '"I am struggling to reconcile invoices" help')

    def test_fetch_unaligned_search_hits_for_real_evidence(self):
        """Topic word mismatch must not eliminate a real firsthand request."""
        hits=[{"title":"An actual request", "source_url":"https://example.com/need",
               "snippet":"Real discussion"}]
        with patch.object(web,"bing_rss",return_value=hits), \
             patch.object(web,"duckduckgo_html",return_value=[]), \
             patch.object(web,"fetch_public_text",return_value=(
                 "https://example.com/need",
                 "I need help reconciling invoices every weekend.",[])):
            rows,errors=web.open_web_candidates(
                queries=["my payroll account reconciliation workflow"],
                strict_relevance=True)
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]["page_fetched"])
        self.assertIn("I need help",rows[0]["page_text"])
        self.assertEqual(errors,[])

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
    def test_safe_relative_build_path_accepts_dot_slash_not_traversal(self):
        from tools import cyclops_workshop as workshop
        self.assertEqual(str(workshop._safe_path("./output/real.pdf")),"output/real.pdf")
        with self.assertRaises(ValueError):
            workshop._safe_path("./../outside.pdf")
        with self.assertRaises(ValueError):
            workshop._safe_path("./output/../outside.pdf")

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
    def test_dictionary_page_does_not_qualify_as_customer_problem(self):
        cyclops=cyclops_or_skip(self)
        page={"page_fetched":True,"page_text":"Start (verb). Definition: to begin an activity. Synonyms include commence.",
              "title":"START - Dictionary"}
        self.assertEqual(cyclops.firsthand_problem_excerpt(page),"")
        dictionary_example={"page_fetched":True,"title":"START - English meaning",
                            "page_text":"Example: I need help organizing my desk each Friday."}
        self.assertEqual(cyclops.firsthand_problem_excerpt(dictionary_example),"")
        request={"page_fetched":True,"page_text":"I need help reconciling my invoices every Friday because I keep losing the original transactions."}
        self.assertIn("reconciling my invoices",cyclops.firsthand_problem_excerpt(request))

    def test_legacy_bytes_only_listing_is_not_marketed_as_verified(self):
        cyclops=cyclops_or_skip(self)
        asset={"status":"active","vault_path":"private/asset.bin",
               "delivery":"private_encrypted_vault","checkout_url":"https://stripe.example/checkout",
               "quality_gate":"verified_generated_bytes"}
        self.assertFalse(cyclops.catalog_eligible(asset))
        asset["quality_gate"]="content_and_evidence_verified"
        asset["verified_market_problem_quote"]="I need help with a recurring problem."
        self.assertTrue(cyclops.catalog_eligible(asset))

    def test_only_exact_legacy_cyclops_checkout_is_deactivated(self):
        cyclops=cyclops_or_skip(self)
        legacy={"status":"active","asset_id":"old-asset",
                "payment_link_id":"plink_old",
                "quality_gate":"verified_generated_bytes"}
        links=[
            {"id":"plink_old","active":True,"metadata":{
                "cyclops_asset":"yes","cyclops_asset_id":"old-asset"}},
            {"id":"plink_other","active":True,"metadata":{
                "cyclops_asset":"yes","cyclops_asset_id":"someone-else"}},
            {"id":"plink_spoofed","active":True,"metadata":{
                "cyclops_asset":"yes","cyclops_asset_id":"old-asset"}},
        ]
        with patch.object(cyclops,"_stripe_pages",return_value=iter(links)), \
             patch.object(cyclops,"stripe_post",return_value={
                 "id":"plink_old","active":False}) as update:
            result=cyclops.retire_unverified_payment_links([legacy])
        self.assertEqual(result["disabled_payment_links"],1)
        self.assertEqual(update.call_args.args[0],"payment_links/plink_old")
        self.assertEqual(update.call_args.args[1],{"active":"false"})

    def test_commercial_review_inspects_real_pdf_contents_and_rejects_unsupported_claim(self):
        import base64
        import io
        try:
            from pypdf import PdfWriter
        except ModuleNotFoundError:
            self.skipTest("PDF inspection library belongs to the Cyclops runner")
        cyclops=cyclops_or_skip(self)
        output=io.BytesIO()
        pdf=PdfWriter()
        pdf.add_blank_page(width=600,height=800)
        pdf.write(output)
        raw=output.getvalue()
        artifact={"filename":"guide.pdf","content_b64":base64.b64encode(raw).decode(),
                  "size_bytes":len(raw)}
        preview=cyclops.artifact_content_evidence([artifact])
        self.assertEqual(preview[0]["actual_contents"],"")
        self.assertGreaterEqual(preview[0]["pages"],1)
        plan={"title":"Claimed useful tool","description":"An actual tool",
              "audience":"People","pitch":"A real working deliverable",
              "production_goal":"Must work","output_files":["guide.pdf"],
              "evidence_quote":"I need help with a recurring problem."}
        with patch.object(cyclops,"model_json",return_value={
             "fulfills_promised_function":False,
             "contains_substantive_deliverable":False,
             "does_not_claim_unimplemented_external_capabilities":True,
             "reason":"The artifact is only a generic sentence."}):
            with self.assertRaisesRegex(ValueError,"product_not_market_ready"):
                cyclops.commercial_integrity_review(plan,[artifact])

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
        cyclops=cyclops_or_skip(self)
        row={"id":"real:1","source_url":"https://example.org/request",
             "title":"Real problem","text":"Result snippet claims an entirely different issue",
             "page_text":"Our actual process takes a day each week",
             "page_fetched":True}
        view=cyclops.demand_view([row])
        self.assertIn("actual process",view[0]["excerpt"])
        self.assertTrue(view[0]["source_fetched"])


if __name__=="__main__":
    unittest.main()
