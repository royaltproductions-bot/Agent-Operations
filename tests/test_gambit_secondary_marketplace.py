import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tools import gambit_secondary_marketplace as secondary


class SecondaryMarketplaceTests(unittest.TestCase):
    def test_public_bountyboard_observation_is_tagged_and_no_key_required(self):
        payload={"bounties":[{"id":"b1","title":"Research suppliers",
                              "status":"OPEN","budgetCents":4500}]}
        state={}
        response=io.BytesIO(json.dumps(payload).encode())
        response.__enter__=lambda self: self
        response.__exit__=lambda *args: None
        with patch.object(secondary.gambit_credentials,"load",return_value=None), patch.object(secondary.urllib.request,"urlopen",return_value=response) as open_:
            result=secondary.observe(
                state, now=datetime(2026,11,11,20,0,tzinfo=timezone.utc))
        self.assertEqual(result["status"],"observed_public")
        self.assertEqual(result["open_count"],1)
        self.assertEqual(result["listings"][0]["marketplace"],"bountyboard")
        self.assertEqual(result["bidding_status"],"credential_not_connected")
        self.assertNotIn("Authorization",open_.call_args.args[0].headers)


if __name__ == "__main__":
    unittest.main()

class AuthenticatedMarketplaceTests(unittest.TestCase):
    def test_authentication_uses_agent_endpoint_and_is_not_logged(self):
        response=io.BytesIO(json.dumps({'bounties':[{'id':'b2','reward':100,'allowAgents':True}]}).encode())
        with patch.object(secondary.gambit_credentials,'load',return_value='bb_ag_test'), patch.object(secondary.urllib.request,'urlopen',return_value=response) as call:
            state={}
            result=secondary.observe(state)
            secondary.observe(state)
        self.assertEqual(call.call_count,1)
        self.assertIn('/suggested',call.call_args.args[0].full_url)
        self.assertEqual(call.call_args.args[0].headers['Authorization'],'Bearer bb_ag_test')
        self.assertNotIn('bb_ag_test',json.dumps(state))
        self.assertEqual(result['status'],'observed_authenticated')
        self.assertEqual(result['listings'][0]['budget_cents'],100)
        self.assertEqual(result['bidding_status'],'operator_reconciliation_required')

    def test_invalid_credential_fails_closed_without_network(self):
        with patch.object(secondary.gambit_credentials,'load',side_effect=ValueError('secret')), patch.object(secondary.urllib.request,'urlopen') as call:
            result=secondary.observe({})
        call.assert_not_called()
        self.assertEqual(result['status'],'credential_unavailable')
        self.assertNotIn('secret',json.dumps(result))
