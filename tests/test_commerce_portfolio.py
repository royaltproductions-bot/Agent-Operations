"""Commerce portfolio requirements: diversity, memory and economic proof over scans."""
import unittest
from tools import commerce_portfolio as p

class PortfolioTests(unittest.TestCase):
    def test_seven_independent_commercial_models_not_one_vendor_or_bounty(self):
        self.assertGreaterEqual(len(p.LANES),7)
        for x in ("existing_product_sales","digital_asset_licensing",
                  "creator_commissions","commissioned_services",
                  "paid_software_tasks","distribution_partnerships",
                  "conversion_and_fulfillment"):
            self.assertIn(x,p.LANES)
        self.assertIn("reddit_forhire",p.CHANNELS)
        self.assertEqual(len(set(p.CHANNELS)),len(p.CHANNELS))

    def test_lanes_rotate_across_hours_without_buyers(self):
        history=[]
        seen=[]
        for i in range(len(p.LANES)):
            lane=p.choose_lane(history)
            seen.append(lane)
            history.append({"hypotheses":[{"lane":lane,"buyer":"type of buyer",
                            "problem":"manual reconciliations","business_model":"a service",
                            "query":"different "+str(i),"channel":"hackernews"}],
                            "qualified_external_contacts":0})
        self.assertEqual(set(seen),set(p.LANES))
        self.assertEqual(len(seen),len(p.LANES))

    def test_same_hour_rotates_without_new_receipts(self):
        self.assertNotEqual(p.choose_lane([],attempted=["commissioned_services"]),
                            "commissioned_services")

    def test_channel_rotates_when_retried(self):
        lane="commissioned_services"
        old=p.choose_channel([],lane)
        nxt=p.choose_channel([],lane,attempted=[old])
        self.assertNotEqual(old,nxt)

    def test_reworded_bakery_inventory_loop_rejected(self):
        old={"hypotheses":[{"lane":"commissioned_services",
                           "buyer":"A Portland bakery wanting inventory alerts",
                           "problem":"Daily bakery inventory spoilage tracking",
                           "business_model":"Python inventory tracking service",
                           "query":"bakery inventory tracking",
                           "channel":"hackernews"}],
             "qualified_external_contacts":0}
        new={"lane":"commissioned_services",
             "buyer":"Urban cafe seeking stock monitoring",
             "customer_problem":"Automated food ingredient tracking to reduce spoilage",
             "offer":"Python stock monitoring tool",
             "search_channel":"hackernews",
             "search_query":"bread inventory spoilage"}
        self.assertIsNotNone(p.reject_repetition(new,[old]))
        self.assertEqual(p.reject_repetition(new,[]),None)

    def test_invented_specific_buyer_is_invalid(self):
        proposal={"lane":"commissioned_services",
                  "buyer":"A bakery in Portland is seeking an AI inventory system",
                  "customer_problem":"inventory","offer":"Python inventory tracker",
                  "search_channel":"hackernews","search_query":"inventory contractor"}
        self.assertEqual(p.reject_repetition(proposal,[]),"invented_localized_buyer_disallowed")

    def test_contact_is_not_earnings(self):
        score=p.scorecard({"hypotheses":[{}],
                           "actions":[{"result":"sent_invited_proposal"}]})
        self.assertEqual(score["externally_sent_verified_invitations"],1)
        self.assertIsNone(score["paid_orders_verified"])
        self.assertIsNone(score["revenue_collected_usd_verified"])
        self.assertEqual(score["buyer_replies_verified"],0)

    def test_scan_only_history_triggers_switch_warning(self):
        h=[{"hypotheses":[{"lane":"commissioned_services","channel":"hackernews",
                            "buyer":"some buyer","query":"test "+str(i)}]}
           for i in range(5)]
        d=p.diagnostics(h)
        self.assertTrue(d["research_loop_alert"])
        self.assertEqual(d["recent_unvalidated_experiments"],5)

if __name__=="__main__":
    unittest.main()
