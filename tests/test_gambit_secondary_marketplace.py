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
        with patch.object(secondary.urllib.request,"urlopen",return_value=response) as open_:
            result=secondary.observe(
                state, now=datetime(2026,11,11,20,0,tzinfo=timezone.utc))
        self.assertEqual(result["status"],"observed_public")
        self.assertEqual(result["open_count"],1)
        self.assertEqual(result["listings"][0]["marketplace"],"bountyboard")
        self.assertEqual(result["bidding_status"],"credential_not_connected")
        self.assertNotIn("Authorization",open_.call_args.args[0].headers)


if __name__ == "__main__":
    unittest.main()
