"""Regression tests for owner-approved, opt-in Wolverine solicitation outreach."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from tools import autonomous_commerce_brain as brain
from tools import connected_commerce_tools as connected
from tools import open_web_discovery as discovery


class WolverineRfpChannelTests(unittest.TestCase):
    def row(self):
        text=(
            "Request for Proposals for four AI pilots. We are seeking an external "
            "consultant to deliver bounded digital work. Submission deadline "
            "November 17, 2026. Please email tenders@example.org to receive "
            "proposal forms and templates."
        )
        return {
            "id":"web:test",
            "source_url":"https://buyer.example/rfp",
            "title":"Delivery partner for four AI pilots",
            "page_text":text,
            "text":text,
            "page_fetched":True,
            "created_at":"2026-11-13T12:00:00+00:00",
            "contact":"tenders@example.org",
            "public_links":[],
        }

    def test_explicit_solicitation_email_requires_requester_route(self):
        row=self.row()
        self.assertEqual(brain.explicit_solicitation_email(row),
                         "tenders@example.org")
        footer=dict(row)
        footer["page_text"]=(
            "Request for Proposals for four AI pilots. "
            "Generic corporate footer: info@example.org")
        footer["contact"]="info@example.org"
        self.assertEqual(brain.explicit_solicitation_email(footer),"")

    def test_undated_page_can_prove_currentness_from_future_deadline(self):
        row=self.row()
        row["created_at"]=None
        now=datetime(2026,11,13,20,0,tzinfo=timezone.utc)
        self.assertTrue(brain.solicitation_deadline_current(row,now=now))

    def test_invited_rfp_email_sends_bounded_inquiry_without_checkout(self):
        row=self.row()
        plan={"title":"AI pilot delivery","scope":"bounded AI pilot review",
              "deliverable":"implementation assessment","pitch":"Relevant bounded work",
              "price_usd":"100.00"}
        with patch.object(brain,"public_buyer_invitation",return_value=True), \
             patch.object(brain,"explicit_solicitation_email",
                          return_value="tenders@example.org"), \
             patch.object(brain,"recent_solicitation_contact_count",return_value=0), \
             patch.object(brain,"sent_subject_exists",return_value=False), \
             patch.object(brain,"agent_send",return_value="message-1") as send:
            result=brain.send_offer(row,plan,checkout=None)
        self.assertEqual(result["result"],"solicitation_inquiry_sent")
        self.assertEqual(result["channel"],"email")
        self.assertEqual(send.call_args.args[0],"tenders@example.org")
        self.assertNotIn("checkout",send.call_args.args[2].lower())
        self.assertNotIn("$100",send.call_args.args[2])

    def test_daily_cap_prevents_excess_solicitation_contacts(self):
        row=self.row()
        with patch.object(brain,"public_buyer_invitation",return_value=True), \
             patch.object(brain,"explicit_solicitation_email",
                          return_value="tenders@example.org"), \
             patch.object(brain,"recent_solicitation_contact_count",return_value=3), \
             patch.object(brain,"agent_send") as send:
            result=brain.send_offer(row,{"pitch":"x"},None)
        self.assertEqual(result["result"],"solicitation_daily_contact_cap")
        send.assert_not_called()

    def test_rfp_role_inbox_wins_when_page_lists_multiple_emails(self):
        text=("Questions: general@example.org. Submit proposals to "
              "tenders@example.org before the deadline.")
        self.assertEqual(discovery.safe_email(text),"tenders@example.org")

    def test_current_solicitation_queries_are_seeded_before_model_queries(self):
        class Memory:
            state={"queries":[]}
            run_queries=[]
            def choose_queries(self,proposed):
                self.proposed=list(proposed)
                self.run_queries=self.proposed[:4]
                return self.run_queries
            def budget(self):
                return {"max_candidates":12,"max_pages":24}
            def evidence_queries(self):
                return []
        memory=Memory()
        with patch.object(brain,"discovery_queries",
                          return_value=["looking for someone to build a dashboard"]), \
             patch.object(brain,"open_web_candidates",return_value=([],[])) as search:
            rows,issues=brain.candidates(memory)
        self.assertEqual(rows,[])
        self.assertTrue(memory.proposed[0].lower().startswith("request for proposals"))
        self.assertIn("rfp",memory.proposed[1].lower())
        self.assertIn("looking for someone",memory.proposed[2].lower())
        self.assertEqual(search.call_count,2)
        first=search.call_args_list[0].kwargs
        second=search.call_args_list[1].kwargs
        self.assertTrue(all(brain.solicitation_query(q) for q in first["queries"]))
        self.assertFalse(first["include_discussions"])
        self.assertFalse(first["include_paid_issues"])
        self.assertTrue(all(not brain.solicitation_query(q) for q in second["queries"]))
        self.assertTrue(second["include_discussions"])
        self.assertTrue(second["include_paid_issues"])

    def test_solicitation_web_results_are_merged_before_general_rows(self):
        class Memory:
            state={"queries":[]}
            def choose_queries(self,proposed):
                return list(proposed[:3])
            def budget(self):
                return {"max_candidates":8,"max_pages":18}
            def evidence_queries(self):
                return []
        memory=Memory()
        official=self.row()
        official["source_url"]="https://buyer.example/official-rfp"
        general={"id":"web:general","source_url":"https://news.example/general",
                 "title":"General request","page_text":"general","text":"general",
                 "page_fetched":True,"created_at":"2026-11-13T12:00:00+00:00",
                 "contact":None,"public_links":[]}
        def search(**kwargs):
            return ([official],[]) if not kwargs["include_discussions"] else ([general],[])
        with patch.object(brain,"discovery_queries",
                          return_value=["looking for someone to build a dashboard"]), \
             patch.object(brain,"open_web_candidates",side_effect=search), \
             patch.object(brain,"review_requester_pages",side_effect=lambda rows:rows), \
             patch.object(brain,"enrich_public_response_route",side_effect=lambda rows:rows):
            rows,issues=brain.candidates(memory)
        self.assertEqual([r["source_url"] for r in rows],
                         ["https://buyer.example/official-rfp",
                          "https://news.example/general"])

    def test_solicitation_seed_rotates_away_from_recent_query(self):
        now=datetime(2026,11,14,2,0,tzinfo=timezone.utc)
        first="request for proposals artificial intelligence consulting 2026"
        class Memory:
            state={"queries":[{"query":first,"at":"2026-11-14T01:00:00+00:00"}]}
        seeds=brain.solicitation_seed_queries(Memory(),now=now)
        self.assertNotIn(first,seeds)
        self.assertEqual(len(seeds),2)

    def test_rfp_prompt_no_longer_excludes_official_solicitations(self):
        import inspect
        source=inspect.getsource(brain.discovery_queries)
        self.assertIn("Include recent official RFP",source)
        self.assertNotIn("Do not \"\n        \"include recent official RFP",source)

    def test_recent_contact_counter_includes_operator_seeded_rfps(self):
        messages=[
            {"labels":["sent"],"subject":"RFP documents request — AI pilots",
             "timestamp":"2026-11-14T01:00:00Z"},
            {"labels":["sent"],"subject":"TRS000658 — submission clarification",
             "timestamp":"2026-11-14T01:01:00Z"},
            {"labels":["sent"],"subject":"ordinary unrelated note",
             "timestamp":"2026-11-14T01:02:00Z"},
        ]
        with patch.object(connected,"agent_get",return_value={"messages":messages}):
            count=connected.recent_solicitation_contact_count(
                now=datetime(2026,11,14,2,0,tzinfo=timezone.utc))
        self.assertEqual(count,2)


if __name__=="__main__":
    unittest.main()
