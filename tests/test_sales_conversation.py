"""Full funnel: only already-solicited real buyer messages may receive followups."""
from datetime import datetime, timezone
import hashlib
import os
import unittest
from unittest.mock import patch
from tools import sales_conversation as s

SOURCE="https://news.ycombinator.com/item?id=99000888"
TOKEN=hashlib.sha256(SOURCE.encode()).hexdigest()[:18]
HISTORY=[{"actions":[{"tool":"contact_invited_buyer","source":SOURCE,
                      "result":"sent_invited_proposal"}]}]
MESSAGE={
    "message_id":"external-inbound-message-123",
    "timestamp":datetime.now(timezone.utc).isoformat(),
    "labels":["received"],
    "from":"reply@actualbuyer.example.org",
    "subject":"Re: Regarding your public contract invitation ["+TOKEN+"]",
    "text":"We have a budget for a paid Python CSV import. Can you take a look?"
}

class FollowupTests(unittest.TestCase):
    def test_only_true_previous_invitations_may_trigger_inbound(self):
        with patch.dict(os.environ,{"AGENTMAIL_API_KEY":"test"}), \
             patch.object(s,"agent_get",return_value={"messages":[MESSAGE]}):
            self.assertFalse(s.pending_replies([]))
            found=s.pending_replies(HISTORY)
        self.assertEqual(len(found),1)
        self.assertNotIn("reply@actualbuyer.example.org",str(s.known_invitations(HISTORY)))

    def test_nonconsenting_and_sensitive_inbound_ignored(self):
        records=[
            {**MESSAGE,"labels":["spam","received"]},
            {**MESSAGE,"text":"Please sign this agreement with a contract signature"},
            {**MESSAGE,"subject":"Unrelated newsletter"},
            {**MESSAGE,"message_id":""},
            {**MESSAGE,"from":"ari-operations@agentmail.to"}
        ]
        with patch.dict(os.environ,{"AGENTMAIL_API_KEY":"test"}),\
             patch.object(s,"agent_get",return_value={"messages":records}):
            self.assertEqual(s.pending_replies(HISTORY),[])

    def test_dry_run_no_send_and_private_data_not_returned(self):
        with patch.dict(os.environ,{"AGENTMAIL_API_KEY":"test"}),\
             patch.object(s,"agent_get",return_value={"messages":[MESSAGE]}):
            incoming=s.pending_replies(HISTORY)[0]
        with patch.object(s,"agent_send") as sent:
            result=s.send_followup(incoming,"clarify_scope",HISTORY,enabled=False)
        self.assertEqual(result["result"],"eligible_dry_run")
        self.assertNotIn("actualbuyer",str(result))
        sent.assert_not_called()

    def test_model_can_select_safe_reply_and_act(self):
        with patch.dict(os.environ,{"AGENTMAIL_API_KEY":"test"}),\
             patch.object(s,"agent_get",return_value={"messages":[MESSAGE]}):
            def choose(schema,prompt,context):
                self.assertIn("clarify_budget",str(schema))
                self.assertNotIn("reply@actualbuyer",str(context))
                return {"choice":context["messages"][0]["id"]+"|clarify_budget"}
            receipt=s.followup_opportunity(HISTORY,choose,"commercial mission",enabled=False)
        self.assertEqual(receipt["result"],"eligible_dry_run")
        self.assertEqual(receipt["tool"],"reply_verified_buyer")

    def test_only_one_message_sends_and_uses_remote_dedup(self):
        with patch.dict(os.environ,{"AGENTMAIL_API_KEY":"test"}),\
             patch.object(s,"agent_get",return_value={"messages":[MESSAGE]}):
            msg=s.pending_replies(HISTORY)[0]
        with patch.object(s,"agent_get",return_value={"messages":[]}),\
             patch.object(s,"agent_send",return_value="sent-ref") as send:
            receipt=s.send_followup(msg,"clarify_scope",HISTORY,enabled=True)
        self.assertEqual(receipt["result"],"sent")
        self.assertEqual(send.call_count,1)
        updated=HISTORY+[{"actions":[{"tool":"reply_verified_buyer",
                                     "target":msg["id"],"result":"sent"}]}]
        with patch.object(s,"agent_send") as send_again:
            self.assertEqual(s.send_followup(msg,"clarify_scope",updated,enabled=True)
                             ["result"],"already_replied")
            send_again.assert_not_called()

    def test_no_arbitrary_model_written_recipient_or_body(self):
        with patch.dict(os.environ,{"AGENTMAIL_API_KEY":"test"}),\
             patch.object(s,"agent_get",return_value={"messages":[MESSAGE]}):
            with self.assertRaises(ValueError):
                s.followup_opportunity(HISTORY,lambda *args:{"choice":"another@site.com|pay_me"},
                                       "commercial",enabled=True)

if __name__=="__main__":
    unittest.main()
