"""Focused, offline checks for the independently running X-variants.

These exercise the CURRENT production code, not superseded commerce prompts.
No external website, payment, GitHub write, or email is touched.
"""
import unittest
import json
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
    def test_ask_hn_direct_post_is_verified_firsthand_page(self):
        """Use the original public submission, never an index-only snippet."""
        hit={"objectID":"12345","title":"Ask HN: Workflow errors?"}
        original={"id":12345,"title":"Ask HN: Workflow errors?",
                  "text":"<p>I need help untangling this recurring invoice workflow.</p>",
                  "created_at":"2026-10-27T00:00:00Z"}
        def fake_open(url,**kwargs):
            data={"hits":[hit]} if "search_by_date" in url else original
            return url,"application/json",json.dumps(data).encode()
        with patch.object(web,"_open",side_effect=fake_open):
            rows=web.hn_ask_candidates(["recurring invoice workflow errors"])
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]["page_fetched"])
        self.assertEqual(rows[0]["search_provider"],"hn_ask")
        self.assertIn("I need help untangling",rows[0]["page_text"])
        self.assertEqual(rows[0]["source_url"],
                         "https://news.ycombinator.com/item?id=12345")

    def test_ask_hn_index_snippet_does_not_replace_missing_original(self):
        hit={"objectID":"12345","title":"Need a tool to handle reports"}
        def fake_open(url,**kwargs):
            data={"hits":[hit]} if "search_by_date" in url else {
                "id":12345,"title":"Need a tool to handle reports","text":""}
            return url,"application/json",json.dumps(data).encode()
        with patch.object(web,"_open",side_effect=fake_open):
            rows=web.hn_ask_candidates(["need a tool to handle reports"])
        self.assertEqual(rows,[])

    def test_discussion_feed_supplements_open_web_without_host_whitelist(self):
        class Memory:
            def eligible_url(self,url): return True
            def observe_search(self,*args,**kwargs): return None
            def frontier(self,limit=5): return []
        direct={"id":"web:hn","source_url":"https://news.ycombinator.com/item?id=12345",
                "title":"Ask HN: Data reconciliation?", "page_fetched":True,
                "page_text":"I need help reconciling invoices each weekend."}
        with patch.object(web,"hn_ask_candidates",return_value=[direct]), \
             patch.object(web,"duckduckgo_html",return_value=[]), \
             patch.object(web,"brave_html",return_value=[]), \
             patch.object(web,"bing_rss",return_value=[]):
            rows,errors=web.open_web_candidates(
                queries=["invoice reconciliation"],memory=Memory())
        self.assertEqual(rows,[direct])
        self.assertEqual(errors,[])

    def test_market_search_keeps_first_person_phrases(self):
        self.assertEqual(
            web.compact_query('"I am struggling to reconcile invoices" help'),
            '"I am struggling to reconcile invoices" help')

    def test_fetch_unaligned_search_hits_for_real_evidence(self):
        """Topic word mismatch must not eliminate a real firsthand request."""
        hits=[{"title":"An actual request", "source_url":"https://example.com/need",
               "snippet":"Real discussion"}]
        with patch.object(web,"bing_rss",return_value=[]), \
             patch.object(web,"duckduckgo_html",return_value=hits), \
             patch.object(web,"brave_html",return_value=[]), \
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

    def test_unrelated_bing_rss_results_do_not_pollute_market_research(self):
        unrelated=[{"title":"Why - dictionary definition",
                    "source_url":"https://example.com/dictionary/why",
                    "snippet":"What does the word why mean?"}]
        topic="receipt organization for freelance illustrators"
        with patch.object(web,"bing_rss",return_value=unrelated), \
             patch.object(web,"duckduckgo_html",return_value=[]), \
             patch.object(web,"brave_html",return_value=[]), \
             patch.object(web,"fetch_public_text") as fetch:
            rows,errors=web.open_web_candidates(queries=[topic],strict_relevance=True)
        self.assertEqual(rows,[])
        self.assertEqual(errors,[])
        fetch.assert_not_called()

    def test_brave_fallback_parses_real_outbound_listing_links(self):
        html='''<div class="snippet svelte-x" data-pos="0" data-type="web">
          <div class="result-content"><a href="https://example.org/item/real"
          target="_self" class="link"><div class="title search-snippet-title"
          title="Verified Dashboard Template by Seller">Verified Dashboard Template</div>
          </a></div></div>'''
        with patch.object(web,"_open",return_value=(
            "https://search.brave.com/search?q=x","text/html",html.encode())):
            rows=web.brave_html("dashboard sales")
        self.assertEqual(rows,[{"title":"Verified Dashboard Template by Seller",
                                "source_url":"https://example.org/item/real",
                                "snippet":""}])

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
    def test_workshop_builds_only_when_real_product_tools_requested(self):
        from tools import cyclops_workshop as workshop
        from subprocess import CompletedProcess
        with patch.object(workshop,"_IMAGE_READY",False), \
             patch.object(workshop.subprocess,"run",side_effect=[
                 CompletedProcess([],1,"","not found"),
                 CompletedProcess([],0,"built",""),
             ]) as run:
            workshop._ensure_workshop_image()
            workshop._ensure_workshop_image()
        self.assertEqual(run.call_count,2)
        self.assertEqual(run.call_args_list[0].args[0][:3],
                         ["docker","image","inspect"])
        self.assertEqual(run.call_args_list[1].args[0][:2],["docker","build"])

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
    def test_model_judges_one_sold_product_before_workshop(self):
        cyclops=cyclops_or_skip(self)
        rows=[{
            "id":"web:"+seller,"source_url":"https://market.example/item/"+seller,
            "title":title,"page_fetched":True,
            "page_text":title+" $19 By "+seller+" Cart "+str(sales)+
                        " sales Item Details Reviews. "*5,
        } for seller,title,sales in [
            ("makerone","Web Analytics Dashboard Template",95),
            ("makertwo","Business Analytics Dashboard UI Template",120),
        ]]
        plan={"title":"Analytics Dashboard Starter","description":"Useful analytics dashboard",
              "audience":"Small teams","pitch":"Working dashboard starter",
              "price_usd":"15.00","evidence_candidate_id":"web:makerone",
              "evidence_quote":"",
              "target_terms":["analytics","dashboard"],"core_points":["Working charts"],
              "production_goal":"Build a functional analytics dashboard",
              "output_files":["dashboard.html"]}
        with patch.object(cyclops,"discover_tools",return_value={"modules":["json"]}) as tools, \
             patch.object(cyclops,"model_json",side_effect=[
                {"decision":"pursue","candidate_id":"web:makerone",
                 "reason":"Observable paid demand and a deliverable we can build"},
                plan]):
            picked=cyclops.choose_asset_concept(rows)
        tools.assert_called_once()
        self.assertEqual(picked["market_validation"]["listing"]["completed_sales"],95)
        self.assertEqual(picked["market_validation"]["decision"],"pursue")
        self.assertIn("95 sales",picked["evidence_quote"])

        # A model that declines the opportunity must not load the workshop.
        with patch.object(cyclops,"discover_tools") as tools:
            with patch.object(cyclops,"model_json",return_value={
                "decision":"pass","candidate_id":"web:makerone",
                "reason":"Too difficult to reach buyers"}):
                with self.assertRaisesRegex(ValueError,"market_opportunity_declined"):
                    cyclops.choose_asset_concept(rows)
        tools.assert_not_called()

    def test_paid_listings_are_independent_evidence_not_numeric_gates(self):
        from tools.paid_market import observed_paid_listing,paid_listing_evidence
        def sold(url,title,seller,sales):
            return {"id":"web:"+seller,"source_url":url,"title":title,
                    "page_fetched":True,
                    "page_text":(title+" ")+"$16 $16 By "+seller+
                    " Cart "+str(sales)+" sales Item Details Reviews (5) "+
                    "This is a real downloadable product with a buyer-facing checkout. "*3}
        one=sold("https://market.example/item/one",
                 "Metoxi Bootstrap Admin Dashboard Template","codervent",81)
        two=sold("https://market.example/item/two",
                 "Cartiy Admin Dashboard HTML Template","otherseller",143)
        unrelated=sold("https://market.example/item/three",
                       "Ambient Drum Loop Audio Pack","musicmaker",900)
        self.assertEqual(observed_paid_listing(one)["completed_sales"],81)
        self.assertEqual(len(paid_listing_evidence([one,two,unrelated])),3)
        self.assertEqual(len(paid_listing_evidence([one,one])),1)
        self.assertEqual(len(paid_listing_evidence([one,unrelated])),2)
        weak=sold("https://market.example/item/weak",
                  "Metoxi Admin Dashboard Template","thirdseller",1)
        self.assertEqual(len(paid_listing_evidence([weak])),1)
        snippet_only={**one,"page_fetched":False}
        self.assertIsNone(observed_paid_listing(snippet_only))
        only_ad={**one,"page_text":"I am struggling and wish I had a tool. "*8}
        self.assertIsNone(observed_paid_listing(only_ad))

    def test_unproven_product_cannot_reach_stripe_checkout(self):
        cyclops=cyclops_or_skip(self)
        with patch.object(cyclops,"stripe_get") as stripe:
            with self.assertRaisesRegex(ValueError,"without_verified_paid_market"):
                cyclops.cyclops_checkout({"title":"Generic made-up guide",
                                          "price_usd":"9.00","asset_id":"bad"})
        stripe.assert_not_called()

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
        forum={"page_fetched":True,"title":"Printer support discussion",
               "page_text":"A relative's printer started printing only in black. "
                           "I reinstalled its drivers, but it didn't cure the problem. "
                           "Would anyone have any ideas?"}
        self.assertIn("didn't cure the problem",cyclops.firsthand_problem_excerpt(forum))

    def test_legacy_bytes_only_listing_is_not_marketed_as_verified(self):
        cyclops=cyclops_or_skip(self)
        from tools.paid_market import validated_market_proof
        asset={"status":"active","vault_path":"private/asset.bin",
               "delivery":"private_encrypted_vault","checkout_url":"https://stripe.example/checkout",
               "quality_gate":"verified_generated_bytes"}
        self.assertFalse(cyclops.catalog_eligible(asset))
        asset["quality_gate"]="content_and_evidence_verified"
        asset["verified_market_problem_quote"]="I need help with a recurring problem."
        self.assertFalse(cyclops.catalog_eligible(asset))
        comps=[{"url":f"https://example.com/item/{i}",
                "completed_sales":50,"listed_price_usd":"12.00",
                "sale_excerpt":"$12 Cart 50 sales"} for i in (1,2)]
        asset["market_validation"]={"status":"two_paid_comparables_verified",
                                    "comparables":comps}
        self.assertTrue(validated_market_proof(asset))
        self.assertTrue(cyclops.catalog_eligible(asset))

    def test_ari_checkout_uses_existing_merchant_with_clear_brand(self):
        from tools import connected_commerce_tools as provider
        outcome=[{"id":"prod_new"},{"id":"price_new"},
                 {"id":"plink_new","url":"https://buy.stripe.com/example","active":True}]
        with patch.object(provider,"existing_autonomous_offer",return_value=None), \
             patch.object(provider,"stripe_post",side_effect=outcome) as post:
            checkout=provider.ensure_autonomous_checkout(
                "https://example.org/request","Useful tool","An actual deliverable",
                "Finished artifact","12.00")
        self.assertTrue(checkout["active"])
        self.assertTrue(post.call_args_list[0].args[1]["name"].startswith("Ari Operations | "))
        fields=post.call_args_list[2].args[1]
        self.assertEqual(fields["metadata[customer_brand]"],"Ari Operations")
        self.assertIn("Ari Operations",fields["custom_text[submit][message]"])
        self.assertIn("Sirius Sage",fields["custom_text[submit][message]"])

    def test_retired_wolverine_checkout_is_not_reactivated(self):
        from tools import connected_commerce_tools as provider
        with patch.object(provider,"existing_autonomous_offer",return_value={
                "checkout_url":"https://buy.stripe.com/retired","active":False}), \
             patch.object(provider,"stripe_post") as post:
            with self.assertRaisesRegex(RuntimeError,"previous_autonomous_offer_retired"):
                provider.ensure_autonomous_checkout(
                    "https://example.org/request","Old tool","Old details","Old scope","12.00")
        post.assert_not_called()
        with patch.object(provider,"existing_autonomous_offer",return_value={
                "checkout_url":"https://buy.stripe.com/unbranded","active":True,
                "customer_brand":None}), \
             patch.object(provider,"stripe_post") as post:
            with self.assertRaisesRegex(RuntimeError,"previous_autonomous_offer_retired"):
                provider.ensure_autonomous_checkout(
                    "https://example.org/request","Old tool","Old details","Old scope","12.00")
        post.assert_not_called()

    def test_cyclops_checkout_branded_without_separate_stripe_account(self):
        cyclops=cyclops_or_skip(self)
        outcomes=[{"id":"prod_new"},{"id":"price_new"},
                  {"id":"plink_new","url":"https://buy.stripe.com/new"}]
        with patch.object(cyclops,"stripe_get",return_value={"data":[]}), \
             patch.object(cyclops,"stripe_post",side_effect=outcomes) as post:
            checkout=cyclops.cyclops_checkout({"asset_id":"new-asset",
                 "title":"A real working tool","description":"Functional deliverable",
                 "price_usd":"10.00", "market_validation":{
                    "status":"two_paid_comparables_verified",
                    "comparables":[{"url":f"https://example.com/item/{i}",
                                    "completed_sales":70,"listed_price_usd":"16.00",
                                    "sale_excerpt":"$16 Cart 70 sales"} for i in (1,2)]}})
        self.assertEqual(checkout["id"],"plink_new")
        self.assertTrue(post.call_args_list[0].args[1]["name"].startswith("Ari Operations | "))
        self.assertIn("Sirius Sage",post.call_args_list[2].args[1][
            "custom_text[submit][message]"])

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
            {"id":"c","title":"Completed-sales dashboard template",
             "snippet":"A real paid product","text":"Market offer",
             "source_url":"https://three.example/item/c","page_fetched":True,
             "page_text":"Completed-sales dashboard template $16 By vendor Cart 81 sales Item Details "*4},
        ]
        with patch.object(cyclops,"discovery_queries",return_value=["genuine need"]), \
             patch.object(cyclops,"open_web_candidates",return_value=(rows,[])):
            output,issues=cyclops.demand_candidates(Memory())
        self.assertEqual(output[0]["id"],"c")
        self.assertEqual({r["id"] for r in output},{"a","b","c"})
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
