import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import commerce_operator as op

SAMPLE = (
    "date,vendor,item,transaction_id,gross,refund,fee,vendor_rate\n"
    "2026-10-17,Vendor-A,Candle,001,20.00,0.00,0.50,60%\n"
    "2026-10-17,Vendor-B,Sage,002,10.00,0.00,0.25,70%\n"
)

class CommerceOperatorTests(unittest.TestCase):
    def test_settlement_produces_two_actual_reports(self):
        rows, count = op.settlement(op.validate_input(SAMPLE.encode()))
        self.assertEqual(count, 2)
        self.assertIn(b"Vendor-A", rows["vendor-summary.csv"])
        self.assertIn(b"Vendor-B", rows["transaction-audit.csv"])

    def test_does_not_process_email_or_card_data(self):
        with self.assertRaises(RuntimeError):
            op.validate_input((SAMPLE + "2026-10-17,x,x,x,alice@example.com,0,0,60%\n").encode())
        with self.assertRaises(RuntimeError):
            op.validate_input(SAMPLE.replace("Vendor-A", "4111 1111 1111 1111").encode())

    def test_only_paid_live_sessions_match_existing_product(self):
        valid={"id":"cs_live_one", "payment_link":"plink_1", "payment_status":"paid",
               "status":"complete", "mode":"payment", "currency":"usd",
               "amount_total":4900}
        samples=[valid,{**valid,"id":"cs_test_no"},{**valid,"payment_status":"unpaid"},
                 {**valid,"amount_total":1000},{**valid,"payment_link":"plink_other"}]
        with patch.object(op,"stripe_get",return_value={"data":samples,"has_more":False}):
            self.assertEqual([x["id"] for x in op.verified_paid_sessions("plink_1")],["cs_live_one"])

    def test_one_paid_order_one_delivery_no_duplicate(self):
        session={"id":"cs_live_abc", "created":1792200000,
                 "customer_details":{"email":"customer@example.test"}}
        incoming={"message_id":"m1","timestamp":"2026-10-17T04:00:00Z",
                  "from":"customer@example.test","subject":"CSV",
                  "attachments":[{"attachment_id":"a1","filename":"data.csv","size":len(SAMPLE)}]}
        sends=[]
        def send(*args,**kwargs):
            sends.append((args,kwargs))
            return "m-sent-01"
        with TemporaryDirectory() as tmp:
            with patch.object(op,"ROOT",Path(tmp)),patch.object(op,"DRY_RUN",False),\
                 patch.dict(os.environ,{"STRIPE_SECRET_KEY":"sk_live_fake","AGENTMAIL_API_KEY":"fake"}),\
                 patch.object(op,"find_payment_link",return_value="plink_1"),\
                 patch.object(op,"verified_paid_sessions",return_value=iter([session])),\
                 patch.object(op,"read_inbound",return_value=[incoming]),\
                 patch.object(op,"agent_get",return_value={"messages":[incoming]}),\
                 patch.object(op,"download_csv",return_value=SAMPLE.encode()),\
                 patch.object(op,"verified_unreversed_payment",return_value=True),\
                 patch.object(op,"agent_send",side_effect=send),\
                 patch("sys.stdout",new_callable=io.StringIO) as output:
                op.run()
                op.run()
                self.assertEqual(len(sends),1)
                self.assertTrue(op.receipt_path(session["id"]).is_file())
                data=json.loads(op.receipt_path(session["id"]).read_text())
                self.assertEqual(data["sales_rows_processed"],2)
                self.assertTrue(data["delivery_confirmed_by_agentmail"])
                self.assertNotIn("customer@example.test",output.getvalue())
                self.assertNotIn("customer@example.test",op.receipt_path(session["id"]).read_text())


    def test_refunded_charge_is_not_fulfillable(self):
        session={"payment_intent":"pi_live_abc"}
        pi={"status":"succeeded","currency":"usd","amount_received":4900,"amount":4900,"latest_charge":"ch_abc"}
        charge={"payment_intent":"pi_live_abc","paid":True,"refunded":True,
                "status":"succeeded","amount_refunded":4900}
        def fake_get(path, params=None):
            return pi if path.startswith("payment_intents/") else charge
        with patch.object(op,"stripe_get",side_effect=fake_get):
            self.assertFalse(op.verified_unreversed_payment(session))
            charge["refunded"]=False
            charge["amount_refunded"]=0
            self.assertTrue(op.verified_unreversed_payment(session))


    def test_api_contract_payment_link_has_verified_identity(self):
        with patch.object(op, "stripe_get", return_value={
                "id":op.CHECKOUT_LINK_ID,"url":op.CHECKOUT_URL,"active":True}):
            self.assertEqual(op.find_payment_link(), op.CHECKOUT_LINK_ID)
        with patch.object(op, "stripe_get", return_value={
                "id":op.CHECKOUT_LINK_ID,"url":"https://wrong.example","active":True}):
            self.assertIsNone(op.find_payment_link())

    def test_sent_mail_filter_and_safe_order_idempotency(self):
        msgs=[{"subject":"VendorSettle report — order abc", "from":op.INBOX,
               "labels":["sent"]}]
        with patch.object(op,"agent_get",return_value={"messages":msgs}) as get:
            self.assertTrue(op.previously_sent("VendorSettle report — order abc"))
            self.assertEqual(get.call_args.args[1]["labels"],["sent"])
            self.assertEqual(get.call_args.args[1]["subject"],["VendorSettle report — order abc"])
        with patch.object(op,"request",return_value={"message_id":"<example@agentmail.to>"}) as call,\
             patch.object(op,"DRY_RUN",False),\
             patch.dict(os.environ,{"AGENTMAIL_API_KEY":"mock-only"}):
            op.agent_send("client@example.test","Report","Private attached",
                         [("summary.csv",b"a,b\n1,2\n")],order_id="cs_live_xyz")
            kw=call.call_args.kwargs
            self.assertTrue(kw["extra_headers"]["Idempotency-Key"].startswith("vendorsettle-"))
            self.assertEqual(kw["body"]["attachments"][0]["content_type"],"text/csv")
            self.assertEqual(kw["body"]["attachments"][0]["content"],"YSxiCjEsMgo=")
            self.assertNotIn("cs_live_xyz",kw["extra_headers"]["Idempotency-Key"])

    def test_attachment_lookup_uses_message_id_not_thread_id(self):
        message={"message_id":"<message@agentmail.to>"}
        attachment={"attachment_id":"att123"}
        with patch.object(op,"agent_get",return_value={
            "download_url":"https://cdn.example.test/file.csv"}) as mocked,\
             patch.object(op.urllib.request,"urlopen") as dl:
            dl.return_value.__enter__.return_value.read.return_value=b"vendor,gross\nx,2\n"
            op.download_csv(message, attachment)
            self.assertEqual(mocked.call_args.args[0],
                "messages/%3Cmessage%40agentmail.to%3E/attachments/att123")


    def test_missing_customer_file_triggers_one_paid_buyer_followup(self):
        session={"id":"cs_live_followup", "created":1792200000,
                 "customer_details":{"email":"buyer@example.test"}}
        with TemporaryDirectory() as tmp:
            with patch.object(op,"ROOT",Path(tmp)), patch.object(op,"DRY_RUN",False),\
                 patch.dict(os.environ,{"STRIPE_SECRET_KEY":"sk_live_fake","AGENTMAIL_API_KEY":"mock"}),\
                 patch.object(op,"find_payment_link",return_value="plink_1"),\
                 patch.object(op,"verified_paid_sessions",side_effect=lambda link: iter([session])),\
                 patch.object(op,"verified_unreversed_payment",return_value=True),\
                 patch.object(op,"read_inbound",return_value=[]),\
                 patch.object(op,"previously_sent",side_effect=[False,True]),\
                 patch.object(op,"agent_send",return_value="<accepted@agentmail.to>") as sent,\
                 patch("sys.stdout",new_callable=io.StringIO) as output:
                op.run()
                op.run()
                self.assertEqual(sent.call_count,1)
                self.assertEqual(sent.call_args.args[0],"buyer@example.test")
                self.assertEqual(sent.call_args.kwargs["order_id"],"cs_live_followup-intake-request")
                self.assertFalse(op.receipt_path(session["id"]).exists())
                self.assertNotIn("buyer@example.test",output.getvalue())

    def test_unverified_payment_cannot_trigger_buyer_followup(self):
        session={"id":"cs_live_unverified", "created":1792200000,
                 "customer_details":{"email":"buyer@example.test"}}
        with TemporaryDirectory() as tmp:
            with patch.object(op,"ROOT",Path(tmp)),patch.object(op,"DRY_RUN",False),\
                 patch.dict(os.environ,{"STRIPE_SECRET_KEY":"sk_live_fake","AGENTMAIL_API_KEY":"mock"}),\
                 patch.object(op,"find_payment_link",return_value="plink_1"),\
                 patch.object(op,"verified_paid_sessions",return_value=iter([session])),\
                 patch.object(op,"verified_unreversed_payment",return_value=False),\
                 patch.object(op,"read_inbound",return_value=[]),\
                 patch.object(op,"agent_send") as sent,\
                 patch("sys.stdout",new_callable=io.StringIO):
                op.run()
                sent.assert_not_called()

    def test_no_credentials_means_no_emails(self):
        with patch.dict(os.environ,{},clear=True),patch.object(op,"DRY_RUN",True),\
             patch("sys.stdout",new_callable=io.StringIO) as output:
            op.run()
            self.assertEqual(json.loads(output.getvalue())["status"],"credentials_missing")

if __name__ == "__main__":
    unittest.main()
