"""Live external agent-experiment discovery: no preloaded case library."""
import io
import json
import unittest
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import urlsplit
from unittest.mock import patch

from tools import agent_commerce_observations as learning
from tools import autonomous_commerce_brain as brain

NOW=datetime(2026,10,21,tzinfo=timezone.utc)


class Response:
    def __init__(self, content): self.content=content
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,limit=-1): return self.content


def fake_public_search(req,timeout=12):
    parts=urlsplit(req.full_url)
    if parts.hostname=="hn.algolia.com":
        return Response(json.dumps({"hits":[
            {"objectID":"12345","title":"I built an AI service and sold it",
             "story_text":"The agent tried customer interviews and a tiny delivery.",
             "created_at":"2026-10-20T10:00:00Z"},
            {"objectID":"888","title":"Old experiment",
             "story_text":"I sold many things",
             "created_at":"2024-02-01T10:00:00Z"}
        ]}).encode())
    if parts.hostname=="news.google.com":
        return Response(('''<?xml version="1.0" encoding="UTF-8"?>
<rss><channel><item><title>AI agent launch experiment</title>
<link>https://news.google.com/rss/articles/abc</link>
<description>Company says its agent earned something</description>
<pubDate>Tue, 20 Oct 2026 12:00:00 GMT</pubDate>
</item></channel></rss>''').encode())
    raise AssertionError("unexpected source")


class LiveDiscoveryTests(unittest.TestCase):
    def test_model_query_drives_runtime_search_not_static_examples(self):
        result=learning.discover("AI agent customer sales",
            opener=fake_public_search,now=NOW)
        self.assertEqual(result["status"],"found")
        self.assertEqual(result["query"],"AI agent customer sales")
        self.assertEqual(len(result["sources"]),2)
        self.assertTrue(all(x["published"].startswith("2026") for x in result["sources"]))
        self.assertTrue(all("not_payment_proof" in x["evidence"] for x in result["sources"]))
        self.assertIn("12345",json.dumps(result))
        self.assertNotIn("Old experiment",json.dumps(result))

    def test_seen_source_falls_behind_new_observation(self):
        prior=["https://news.ycombinator.com/item?id=12345"]
        result=learning.discover("AI agents selling services",
            previously_seen=prior,opener=fake_public_search,now=NOW)
        self.assertEqual(result["sources"][0]["origin"],"news_search_headline")
        self.assertEqual(result["sources"][1]["origin"],"hackernews_story")

    def test_one_source_failure_does_not_prevent_other_research(self):
        def news_blocked(request,timeout=12):
            if "news.google.com" in request.full_url:
                raise URLError("provider denied")
            return fake_public_search(request,timeout=timeout)
        result=learning.discover("AI agent revenue experiments",
            opener=news_blocked,now=NOW)
        self.assertEqual(result["status"],"found")
        self.assertIn("google_news_search_unavailable",result["errors"])
        self.assertEqual(len(result["sources"]),1)

    def test_no_query_does_not_make_network_request(self):
        result=learning.discover("",opener=lambda *a,**k: self.fail("unexpected network"),now=NOW)
        self.assertEqual(result["status"],"invalid_search")

    def test_model_selects_live_topic_and_reflects_without_claiming_real_income(self):
        calls=[]
        def model(schema,prompt,observation):
            calls.append(observation)
            if "decision" in schema["properties"]:
                return {"decision":"search","query":"independent AI earning via real paid tasks",
                        "reason":"find useful practices"}
            return {"transferable_mechanism":"Find a specific paid buyer problem before building",
                    "possible_original_experiment":"Explore different current commissioned work",
                    "limits_and_unverified_claims":"Reported income is unverified"}
        def source(query,previously_seen=()):
            self.assertEqual(query,"independent AI earning via real paid tasks")
            self.assertEqual(previously_seen,[])
            return learning.discover(query,opener=fake_public_search,now=NOW)
        result=brain.observe_other_agent_experiments([],model,source,
                                                     {"commercial_results":"no paid order"})
        self.assertEqual(result["status"],"found")
        self.assertEqual(len(calls),2)
        self.assertEqual(result["reflection"]["possible_original_experiment"],
                         "Explore different current commissioned work")

    def test_strategy_receives_optional_live_learning_and_keeps_working_on_failure(self):
        def inspiration(schema,prompt,context):
            raise ValueError("external example query unavailable")
        seen=[]
        def proposal(schema,prompt,context):
            seen.append(context["optional_live_agent_case_research"])
            return {"buyer":"software teams","customer_problem":"CSV task",
                    "offer":"data-cleaning service",
                    "delivery_using_existing_tools":"Python",
                    "route_to_buyer":"a public paid invitation",
                    "search_channel":"hackernews",
                    "search_query":"paid python contractor",
                    "reason":"test real demand"}
        with patch.object(brain,"past",return_value=[]), \
             patch.object(brain,"MAX_SEARCHES",1), \
             patch.object(brain,"commercial_metrics",return_value={
               "stripe":{"status":"not_checked"},
               "buyer_correspondence":{"status":"not_checked"}}):
            result=brain.run(propose=proposal,evaluate=lambda *a:{},
                 search=lambda q:[],original=lambda *a:"",
                 inspiration_decider=inspiration,live=False)
        self.assertEqual(len(result["hypotheses"]),1)
        self.assertEqual(result["agent_observations"]["status"],"query_decision_invalid")
        self.assertEqual(seen[0]["research_status"],"query_decision_invalid")


if __name__=="__main__":
    unittest.main()
