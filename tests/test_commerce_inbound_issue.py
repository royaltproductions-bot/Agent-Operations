import unittest
from tools.commerce_inbound_issue import reply_for_issue, PREFIX, MARKER

class InboundIssueTests(unittest.TestCase):
    def test_recognizes_only_explicit_vendor_inquiries(self):
        self.assertIsNone(reply_for_issue({"title":"Fix bug [VendorSettle inquiry]"}))
        self.assertIsNone(reply_for_issue({"title":"Unrelated inquiry"}))
        self.assertIsNone(reply_for_issue({"title":PREFIX, "pull_request":{"url":"yes"}}))
        self.assertIsNone(reply_for_issue(None))

    def test_public_acknowledgement_never_leaks_body_or_buyer_details(self):
        issue={"title":PREFIX+" Please help", "body":"Sensitive client@test.invalid +1 999 555 0133; acct 12345"}
        result=reply_for_issue(issue)
        self.assertIn(MARKER,result)
        self.assertIn("ari-operations@agentmail.to",result)
        self.assertIn("https://buy.stripe.com/",result)
        self.assertNotIn("client@test.invalid",result)
        self.assertNotIn("12345",result)
        self.assertNotIn("999",result)
        self.assertIn("public issue",result)

if __name__=="__main__":
    unittest.main()
