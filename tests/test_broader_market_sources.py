"""The model can discover verified paid briefs beyond HN, not scrape DM targets."""
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from tools import autonomous_commerce_brain as brain

class MarketSourceTests(unittest.TestCase):
    def test_public_reddit_search_only_hiring_posts(self):
        now=datetime.now(timezone.utc).timestamp()
        response={"data":{"children":[
            {"data":{"id":"abc123","title":"[Hiring] Paid CSV cleanup",
              "selftext":"Paid $150 project, apply via contact@client.com with work scope.",
              "subreddit":"forhire","created_utc":now}},
            {"data":{"id":"abc456","title":"[For Hire] I offer CSV cleanup",
              "selftext":"Please hire me and contact@example.com",
              "subreddit":"forhire","created_utc":now}},
            {"data":{"id":"abc789","title":"[Hiring] Removing request",
              "selftext":"This role was removed from the platform and not available",
              "subreddit":"forhire","removed_by_category":"moderator","created_utc":now}},
        ]}}
        plan={"search_channel":"reddit_forhire","search_query":"CSV projects"}
        with patch.object(brain,"get_public_json",return_value=response):
            found=brain.search_demand(plan)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["id"],"abc123")
        self.assertIn("https://www.reddit.com/r/forhire/comments/abc123/",found[0]["url"])

    def test_original_reditt_post_verified_again_before_contact(self):
        now=datetime.now(timezone.utc).timestamp()
        obj=[{"data":{"children":[{"data":{
            "id":"abc123","title":"[Hiring] Python CSV project",
            "selftext":"Paid contractor needed, email project@client.org",
            "subreddit":"forhire","created_utc":now
        }}]}}]
        with patch.object(brain,"get_public_json",return_value=obj):
            text=brain.read_original({"id":"abc123"},"reddit_forhire")
        self.assertIn("project@client.org",text)
        self.assertEqual(brain.explicit_invite(text,"reddit_forhire"),"project@client.org")

    def test_unsolicited_reddit_contact_blocked(self):
        post="[For Hire] Paid Python contractor. Email project@client.org"
        self.assertIsNone(brain.explicit_invite(post,"reddit_forhire"))
        self.assertIsNone(brain.explicit_invite(
            "[Hiring] Paid Python contractor. Message me on Reddit instead","reddit_forhire"))
        self.assertIsNone(brain.explicit_invite(
            "[Hiring] Unpaid contractor, budget none. Email project@client.org",
            "reddit_forhire"))

    def test_creative_scope_is_allowed_only_with_owned_actual_media(self):
        from pathlib import Path
        # Generic prohibited work is always rejected regardless of creative wording.
        self.assertFalse(brain.can_execute("licensed attorney","paid video editor"))
        self.assertTrue(brain.can_execute("Python CSV automation","paid contractor job"))

    def test_new_commercial_category_and_alternate_source_are_not_vetoed(self):
        def propose(schema,prompt,context):
            # A legitimate new plan must not be rejected just because a
            # scheduler preferred a different lane and research source.
            self.assertNotIn("enum",schema["properties"]["lane"])
            self.assertGreater(len(schema["properties"]["search_channel"]["enum"]),1)
            return {"lane":"custom_business_research","buyer":"companies",
                    "customer_problem":"incomplete reporting",
                    "offer":"one-time financial dashboard",
                    "delivery_using_existing_tools":"Python CSV",
                    "route_to_buyer":"publicly announced paid project",
                    "search_channel":"github",
                    "search_query":"paid data dashboard contractor",
                    "reason":"new commercial segment"}
        with patch.object(brain,"past",return_value=[]), \
             patch.object(brain,"commercial_metrics",return_value={
                "stripe":{"status":"not_checked"},
                "buyer_correspondence":{"status":"not_checked"}}), \
             patch.object(brain,"MAX_SEARCHES",1):
            row=brain.run(propose=propose,evaluate=lambda *a:{},
                          search=lambda s:[],original=lambda *a:"",live=False)
        self.assertEqual(len(row["hypotheses"]),1)
        self.assertEqual(row["hypotheses"][0]["lane"],"custom_business_research")
        self.assertEqual(row["hypotheses"][0]["channel"],"github")

    def test_portfolio_lanes_are_suggestions_not_forced_decisions(self):
        record=[]
        def propose(schema,prompt,context):
            lane=context["suggested_commercial_lane"]
            channel=context["suggested_public_demand_channel"]
            record.append((lane,channel))
            return {"lane":lane,"buyer":"independent enterprises",
                    "customer_problem":"manual invoices",
                    "offer":"data reconciliation","delivery_using_existing_tools":"Python",
                    "route_to_buyer":"public brief","search_channel":channel,
                    "search_query":lane.replace("_"," ")+" reporting",
                    "reason":"checking actual demand"}
        with patch.object(brain,"past",return_value=[]),\
             patch.object(brain,"commercial_metrics",return_value={
               "stripe":{"status":"not_checked"},
               "buyer_correspondence":{"status":"not_checked"}}),\
             patch.object(brain,"MAX_SEARCHES",3):
            result=brain.run(propose=propose,evaluate=lambda *a:{},
                             search=lambda plan:[],original=lambda *a:"",
                             live=False)
        self.assertEqual(len({a[0] for a in record}),3)
        self.assertEqual(len(result["hypotheses"]),3)
        self.assertIn("portfolio_scope",result)
        self.assertIn("economic_scorecard",result)
        self.assertIsNone(result["economic_scorecard"]["revenue_collected_usd_verified"])

if __name__=="__main__":
    unittest.main()
