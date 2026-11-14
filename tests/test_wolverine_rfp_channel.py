"""Regression tests for owner-approved, opt-in Wolverine solicitation outreach."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from tools import autonomous_commerce_brain as brain
from tools import connected_commerce_tools as connected


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
