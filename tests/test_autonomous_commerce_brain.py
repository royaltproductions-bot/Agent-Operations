"""Goal-directed commercial intelligence acceptance tests. No external send or network."""
import json
import unittest
from unittest.mock import patch
from tools import autonomous_commerce_brain as brain

class BrainTests(unittest.TestCase):
    def test_mission_is_not_a_fixed_vendor_product(self):
        self.assertIn("Choose your own",brain.MISSION)
        self.assertIn("business model",brain.MISSION)
        self.assertIn("distribution",brain.MISSION)
        self.assertNotIn("VendorSettle-only classifier",brain.MISSION)
        self.assertGreaterEqual(brain.MAX_SEARCHES,2)

    def test_public_invitation_requires_explicit_consent_and_one_email(self):
        good=("We are seeking a contractor for paid Python automation projects. "
              "To apply, email opportunities@buyercompany.com with a brief proposal.")
        self.assertEqual(brain.explicit_invite(good,"hackernews"),"opportunities@buyercompany.com")
        self.assertIsNone(brain.explicit_invite("Need contract Python work","hackernews"))
        self.assertIsNone(brain.explicit_invite(
            "We need volunteers for unpaid Python project. Email a@foo.net","hackernews"))
        self.assertIsNone(brain.explicit_invite(good,"github"))
        self.assertIsNone(brain.explicit_invite(
            good+" Also email b@foo.net","hackernews"))

    def test_model_cannot_select_an_unlisted_contact(self):
        strategy={"buyer":"B2B teams","customer_problem":"spreadsheet reconciliation",
                  "offer":"small Python job","delivery_using_existing_tools":"Python and CSV",
                  "route_to_buyer":"reply to an explicit contract invitation",
                  "search_channel":"hackernews","search_query":"Python automation contractor",
                  "reason":"there is public demand"}
        def planning(schema,prompt,context):
            return strategy
        def deciding(schema,prompt,context):
            return {"candidate_id":"9999","action":"contact_invited_buyer",
                    "service_scope":"Python automation","reason":"appears relevant"}
        hits=[{"id":"1001","url":"https://news.ycombinator.com/item?id=1001",
               "text":"Hiring Python contractors","created_at":"2026-10-21"}]
        with patch.object(brain,"past",return_value=[]):
            row=brain.run(propose=planning,evaluate=deciding,search=lambda p:hits,
                          original=lambda h,c:"",live=True)
        self.assertEqual(row["qualified_external_contacts"],0)
        self.assertEqual(row["actions"],[])
        self.assertTrue(row["model_invoked"])
        self.assertIn("model_selected_unlisted_candidate",
                      [x["reason"] for x in row["failures_or_blockers"]])

    def test_actual_bounded_tool_called_after_model_decision(self):
        strategy={"buyer":"small SaaS","customer_problem":"need CSV cleaning",
                  "offer":"paid CSV automation","delivery_using_existing_tools":"Python",
                  "route_to_buyer":"public invitation","search_channel":"hackernews",
                  "search_query":"CSV contractor","reason":"targeted demand"}
        hit={"id":"12345","url":"https://news.ycombinator.com/item?id=12345",
             "text":"Paid CSV contractor","created_at":"2026-10-21"}
        def planning(schema,prompt,context):return strategy
        def deciding(schema,prompt,context):
            return {"candidate_id":"12345","action":"contact_invited_buyer",
                    "service_scope":"Python automation for CSV reporting","reason":"matches tools"}
        original="Paid freelance Python contractor sought. Apply: contact@samplefirm.org"
        with patch.object(brain,"past",return_value=[]):
            row=brain.run(propose=planning,evaluate=deciding,
                          search=lambda p:[hit],
                          original=lambda h,c:original,live=False)
        self.assertEqual(row["actions"][0]["result"],"eligible_dry_run")
        self.assertEqual(row["qualified_external_contacts"],0)
        self.assertIsNone(row["verified_payment_usd"])
        self.assertNotIn("contact@samplefirm.org",json.dumps(row))

    def test_never_email_uninvited_or_unsafe_work(self):
        hit={"url":"https://news.ycombinator.com/item?id=444"}
        text="Contract web scraping work needed. Contact buyer@project.io"
        with patch.object(brain,"past",return_value=[]):
            self.assertEqual(brain.send_invited_request(hit,text,
                "Python automation",[],"hackernews",live=False)["result"],
                "invitation_or_capability_not_verified")
            text="Paid Python contractor work. Apply via buyer@project.io"
            self.assertEqual(brain.send_invited_request(hit,text,
                "licensed attorney",[],"hackernews",live=False)["result"],
                "invitation_or_capability_not_verified")

if __name__=="__main__":
    unittest.main()
