"""Offline commercial controls: provenance, privacy, freshness, approval holds."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from tools import gambit_commercial as c, gambit_worker as w

NOW = datetime(2026, 11, 3, 19, tzinfo=timezone.utc)

class CommercialTests(unittest.TestCase):
    def test_observation_preserves_first_seen_and_raw_timestamp_without_private_text(self):
        state = {'bounty_archive': {'a': {'first_detected_at': '2026-10-30T16:10:00+00:00'}}}
        item = {'_id':'a', 'created_at':int(NOW.timestamp()*1000), 'budget_cents':2200,
                'status':'open', 'description':'private buyer brief'}
        self.assertTrue(c.observe(state,item,NOW))
        self.assertFalse(c.observe(state,item,NOW+timedelta(hours=1)))
        row = state['commercial']['a']
        self.assertEqual(row['posted_at'],NOW.isoformat())
        self.assertEqual(row['posted_time_field'],'created_at')
        self.assertEqual(row['first_detected_at'],'2026-10-30T16:10:00+00:00')
        self.assertNotIn('private buyer brief',json.dumps(state))
        self.assertIsNone(row['bid_at'])

    def test_fresh_recent_unknown_stale_order_and_activity(self):
        rows = [{'_id':'stale','created_at':(NOW-timedelta(days=10)).isoformat()},
                {'_id':'unknown'}, {'_id':'recent','created_at':(NOW-timedelta(days=2)).isoformat()},
                {'_id':'fresh','created_at':NOW.isoformat()}]
        self.assertEqual([r['_id'] for r in sorted(rows,key=lambda r:c.priority(r,{},NOW))],
                         ['fresh','recent','unknown','stale'])
        rows[0]['owner_last_active_at']=NOW.isoformat()
        self.assertEqual(c.priority(rows[0],{},NOW)[0],1)

    def test_introductory_price_first_five_completed_jobs(self):
        state={'settlements':{}}
        price,meta=c.introductory_price(state,{'budget_cents':3500},3500)
        self.assertEqual(price,2800)
        self.assertEqual(meta['discount_percent'],20)
        state['settlements']={str(i):{} for i in range(5)}
        price,meta=c.introductory_price(state,{'budget_cents':3500},3500)
        self.assertEqual(price,3500)
        self.assertIsNone(meta)

    def test_one_followup_after_24h_only_without_buyer_activity(self):
        record={'bid_at':(NOW-timedelta(hours=25)).isoformat(),
                'conversation':{}}
        self.assertTrue(c.followup_due(record,NOW))
        record['followup_sent_at']=NOW.isoformat()
        self.assertFalse(c.followup_due(record,NOW))
        record={'bid_at':(NOW-timedelta(hours=25)).isoformat(),
                'conversation':{'last_incoming_at':NOW.isoformat()}}
        self.assertFalse(c.followup_due(record,NOW))

    def test_sample_and_closed_excluded_from_daily_volume(self):
        state={}
        for id,status,description in [('a','open','real'),('b','open','Sample Bounty for review'),('c','completed','real')]:
            c.observe(state,{'_id':id,'status':status,'description':description,'created_at':NOW.isoformat()},NOW)
        c.observe(state,{'_id':'d','status':'open'},NOW)
        result=c.volume(state,NOW)
        self.assertEqual(sum(result['open_non_sample_by_posted_utc_day'].values()),1)
        self.assertEqual(result['open_non_sample_unknown_posted_time'],1)

    def test_samples_require_read_page_and_literal_fact(self):
        row={'name':'Acme','fact':'Acme supports seed stage founders.','source_url':'https://example.com'}
        evidence={'results':[{'url':row['source_url'],'page_text':row['fact'],'evidence_level':'search_snippet'}]}
        self.assertEqual(c.verified_samples([row],evidence),[])
        evidence['results'][0]['evidence_level']='page_read'
        self.assertEqual(len(c.verified_samples([row,row],evidence)),1)
        self.assertEqual(c.verified_samples([{**row,'fact':'Acme invests five million dollars.'}],evidence),[])

    def test_proposal_encrypted_and_stable_on_reobservation(self):
        state={}
        with patch.dict(os.environ,{'BOUNTY_AGENT_API_KEY':'offline-test'}):
            w.record_bid_measurement(state,'a',{'id':'bid-1','created_at':123,'note':'private proposal','payout_cents':22})
            encoded=json.dumps(state)
            self.assertNotIn('private proposal',encoded)
            w.record_bid_measurement(state,'a',{'id':'bid-1','created_at':123,'note':'private proposal','payout_cents':22})
            self.assertEqual(json.dumps(state),encoded)
        secret=hashlib.sha256(b'gambit-commercial-v1:offline-test').digest()
        self.assertEqual(Fernet(base64.urlsafe_b64encode(secret)).decrypt(state['commercial']['a']['proposal_encrypted'].encode()),b'private proposal')

    def test_held_buyers_never_reach_assessment_or_outbound_api(self):
        for id in c.APPROVAL_HOLD_IDS:
            with patch.object(w,'api',return_value={'bounty':{'_id':id,'status':'open','version':1}}) as api, patch.object(w,'assess') as assess, patch.object(w,'note'):
                w.handle_open({'_id':id},{'bounties':{}})
                self.assertEqual(api.call_count,1)
                assess.assert_not_called()
            with self.assertRaisesRegex(ValueError,'awaiting_owner_approval'):
                w.send_proposal_preview(id,1,b'ignored',{})

    def test_preview_replay_does_not_send_twice(self):
        state={'commercial':{'x':{'preview_version':1,'preview_sent':True}}}
        with patch.object(w,'api') as api:
            w.send_proposal_preview('x',1,b'\x89PNG\r\n\x1a\n',state)
            api.assert_not_called()

    def test_fully_required_figma_is_not_silently_substituted(self):
        required={'bounty':{'description':'Design landing page in Figma','verification':'Deliver editable Figma file'}}
        self.assertEqual(w.unsupported_deliverable(required),'native_figma_unavailable')
