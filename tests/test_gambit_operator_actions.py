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
            row=self.state["commercial"]["mx-test"]
            self.assertEqual(row["conversation"]["message_count"],1)
            self.assertEqual(row["conversation"]["last_outgoing_at"],row["last_outgoing_at"])

    def test_conversation_replay_and_backfill_do_not_double_count(self):
        from tools import gambit_commercial as c
        at = "2026-11-25T20:00:00+00:00"
        c.record_outgoing_receipt(self.state, "mx-test", "msg-1", at)
        c.record_outgoing_receipt(self.state, "mx-test", "msg-1", at)
        c.record_conversation(self.state, "mx-test", [])
        self.assertEqual(self.state["commercial"]["mx-test"]["conversation"]["message_count"], 1)
        c.record_conversation(self.state, "mx-test", [
            {"_id":"msg-1","created_at":at,"author_type":"agent","content":{"text":"Confirmed"}}])
        self.assertEqual(self.state["commercial"]["mx-test"]["conversation"]["message_count"], 1)

    def test_explicit_buyer_reoffer_only_and_no_scope_change(self):
        for text, expected in [
            ("Can you do it for $5?", 500), ("Please lower your bid to $12.50",1250),
            ("Budget is $5",None), ("Can you do it for $5 or $8?",None),
            ("Can you do it for $5 and add a video?",None),
            ("Can you do it for $0?",0)]:
            self.assertEqual(worker.buyer_reoffer_cents({"content":{"text":text}}),expected)

    def test_generic_reoffer_uses_existing_authenticated_path(self):
        details={"bounty":{"version":2,"title":"Write a report"},
                 "bid":{"status":"active","payout_cents":1000,"note":"Original scope."}}
        msg={"_id":"buyer-1","created_at":"2026-11-25T20:00:00+00:00",
             "content":{"text":"Can you do it for $5?"}}
        with patch.object(worker,"api",side_effect=self.fake_api), patch.object(
                worker,"cold_start_bid_cap_cents",return_value=15000), patch.object(worker,"save_state"):
            self.assertTrue(worker.apply_buyer_reoffer(details,self.state,"mx-test",msg,[msg]))
            before=len(self.calls)
            self.assertFalse(worker.apply_buyer_reoffer(details,self.state,"mx-test",msg,[msg]))
            self.assertEqual(len(self.calls),before)
        self.assertEqual(self.price,500)
        self.assertEqual(self.message_count,1)
        self.assertEqual([p["note"] for path,method,p in self.calls if method=="POST" and path.endswith("/bid")],["Original scope."])

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
