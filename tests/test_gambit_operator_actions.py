import unittest
from unittest.mock import patch
from tools import gambit_worker as worker


class OperatorRepriceTests(unittest.TestCase):
    def setUp(self):
        self.action = {"id":"hg6-test","type":"reprice","bounty_id":"mx-test",
                       "payout_cents":500,"note":"PDF design only, within 48 hours.",
                       "confirm_message":"Updated: my bid now shows $5."}
        self.state = {"schema":1,"bounties":{},"commercial":{},"recent_outcomes":[]}
        self.calls = []
        self.price = 1000
        self.message_count = 0

    def fake_api(self, path, method="GET", payload=None):
        self.calls.append((path,method,payload))
        if method == "GET":
            return {"bounty":{"version":2},
                    "bid":{"_id":"bid-test","status":"active","payout_cents":self.price}}
        if path.endswith("/bid"):
            self.price = payload["payout_cents"]
            return {"bid":{"_id":"bid-test","status":"active","payout_cents":self.price}}
        if path.endswith("/messages"):
            self.message_count += 1
            return {"message":{"_id":"msg-test"}}
        raise AssertionError(path)

    def test_one_reprice_one_message_and_second_run_no_calls(self):
        with patch.object(worker,"api",side_effect=self.fake_api), patch.object(
                worker,"cold_start_bid_cap_cents",return_value=15000):
            worker.process_operator_actions(self.state,[self.action])
            self.assertEqual(self.price,500)
            self.assertEqual(self.message_count,1)
            self.assertEqual(self.state["operator_actions_done"]["hg6-test"]["status"],"complete")
            before=len(self.calls)
            worker.process_operator_actions(self.state,[self.action])
            self.assertEqual(len(self.calls),before)

    def test_readback_mismatch_sends_no_message(self):
        def mismatch(path, method="GET", payload=None):
            if method=="GET":
                return {"bounty":{"version":2},
                        "bid":{"_id":"bid-test","status":"active","payout_cents":1000}}
            return {"bid":{"_id":"bid-test","status":"active","payout_cents":500}}
        with patch.object(worker,"api",side_effect=mismatch), patch.object(
                worker,"cold_start_bid_cap_cents",return_value=15000):
            with self.assertRaisesRegex(RuntimeError,"readback_mismatch"):
                worker.process_operator_actions(self.state,[self.action])
        self.assertNotIn("hg6-test",self.state.get("operator_actions_done",{}))

    def test_higher_price_rejected(self):
        action={**self.action,"payout_cents":1200}
        with patch.object(worker,"api",side_effect=self.fake_api), patch.object(
                worker,"cold_start_bid_cap_cents",return_value=15000):
            with self.assertRaisesRegex(ValueError,"not_lower_price"):
                worker.process_operator_actions(self.state,[action])
        self.assertEqual(self.message_count,0)


if __name__=="__main__":
    unittest.main()
