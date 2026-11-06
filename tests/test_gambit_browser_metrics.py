import os
from pathlib import Path
import unittest
from unittest.mock import patch
from tools import gambit_browser_metrics as b

FIXTURES=Path(__file__).parent/'fixtures/gambit-dashboard'
class BrowserMetricsTests(unittest.TestCase):
    def test_verified_empty_queue_fixtures(self):
        for name,kind,field in [('unread','unread','unread_count'),('not-hired','not_hired','not_hired_count')]:
            value=b.parse_page((FIXTURES/(name+'.html')).read_text(),kind)[field]
            self.assertEqual(value['value'],0);self.assertEqual(value['source'],'browser');self.assertIsNone(value['scrape_error'])
    def test_verified_payout_fixture_not_transfer_inference(self):
        result=b.parse_page((FIXTURES/'payouts.html').read_text(),'payouts')
        self.assertEqual(result['operator_balance_cents']['value'],0)
        self.assertEqual(result['earnings_count']['value'],0)
        self.assertIsNone(result['transfer_count']['value'])
        self.assertEqual(result['transfer_count']['scrape_error'],'not_displayed')
    def test_wrong_agent_never_counts_zero(self):
        html=(FIXTURES/'unread.html').read_text().replace('Gambit','Other')
        self.assertIsNone(b.parse_page(html,'unread')['unread_count']['value'])
    def test_missing_or_changed_layout_is_unknown(self):
        for html in ['<html></html>','<p>BUYER TEXT PLACEHOLDER</p>','<p>No unread updates</p>']:
            for kind in ['unread','not_hired','payouts']:
                for value in b.parse_page(html,kind).values():
                    self.assertIsNone(value['value']);self.assertTrue(value['scrape_error'])
    def test_missing_credentials_no_browser_or_network(self):
        with patch.dict(os.environ,{},clear=True):result=b.collect()
        self.assertTrue(all(v['value'] is None and v['scrape_error']=='authentication_not_configured' for v in result.values()))
    def test_session_domains_limited_to_trybounty(self):
        with self.assertRaises(ValueError):b.validated_session('{"cookies":[{"domain":"other.example"}]}')
    def test_no_buyer_text_in_result(self):
        import json
        result=b.parse_page('<p>BUYER TEXT PLACEHOLDER</p>','unread')
        self.assertNotIn('BUYER',json.dumps(result))
