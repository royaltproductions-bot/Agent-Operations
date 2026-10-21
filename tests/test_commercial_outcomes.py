"""Read real Stripe money and replied buyers; never assert a sale from research."""
import unittest
from unittest.mock import patch
from tools import commercial_outcomes as o

class CommercialOutcomeTests(unittest.TestCase):
    def test_no_provider_keys_cannot_fake_sales(self):
        with patch.dict(o.os.environ,{"STRIPE_SECRET_KEY":"",
                                      "AGENTMAIL_API_KEY":""}):
            data=o.metrics([])
        self.assertIsNone(data["stripe"]["confirmed_gross_usd"])
        self.assertIsNone(data["buyer_correspondence"]["verified_buyer_replies"])

    def test_only_real_unreversed_paid_checkout_sessions_count(self):
        with patch.dict(o.os.environ,{"STRIPE_SECRET_KEY":"provider-test",
                                      "AGENTMAIL_API_KEY":""}), \
             patch.object(o,"find_payment_link",return_value="plink_live"), \
             patch.object(o,"verified_paid_sessions",return_value=[
                {"id":"cs-live-paid"},{"id":"cs-refunded"}]), \
             patch.object(o,"verified_unreversed_payment",
                          side_effect=[True,False]):
            data=o.metrics([])
        self.assertEqual(data["stripe"]["verified_paid_sessions"],1)
        self.assertEqual(data["stripe"]["confirmed_gross_usd"],49)
        self.assertIsNone(data["stripe"]["new_revenue_this_run_usd"])

    def test_only_replies_to_actual_prior_sent_invitations_count(self):
        import hashlib
        source="https://news.ycombinator.com/item?id=123"
        token=hashlib.sha256(source.encode()).hexdigest()[:18]
        hist=[{"actions":[{"source":source,"result":"sent_invited_proposal"}]}]
        messages={"messages":[
            {"subject":"Re: Regarding your public contract invitation ["+token+"]",
             "labels":["received"],"text":"real private lead","from":"lead@buyer.net"},
            {"subject":"Re: Regarding your public contract invitation [000000000000000000]",
             "labels":["received"],"from":"another@buyer.net"},
            {"subject":"Re: Regarding your public contract invitation ["+token+"]",
             "labels":["spam","received"],"from":"spam@buyer.net"}
        ]}
        with patch.dict(o.os.environ,{"STRIPE_SECRET_KEY":"",
                                      "AGENTMAIL_API_KEY":"provider-test"}), \
             patch.object(o,"agent_get",return_value=messages):
            data=o.metrics(hist)
        self.assertEqual(data["buyer_correspondence"]["verified_buyer_replies"],1)
        self.assertNotIn("lead@buyer.net",str(data))
        self.assertNotIn("real private lead",str(data))

if __name__=="__main__":
    unittest.main()
