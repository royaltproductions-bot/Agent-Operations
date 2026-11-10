"""Offline acceptance tests for Claude November 4–5 engineering directives."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from tools import gambit_worker as w, gambit_commercial as c, gambit_openai as o
from tools import autonomous_commerce_brain as brain, cyclops_asset_engine as cyclops

NOW = datetime(2026, 11, 5, 15, tzinfo=timezone.utc)

class Corrections(unittest.TestCase):
    def details(self, ident='mx7dartnjzsphksgveek0qxs2h8hkcfn'):
        return {'bounty':{'_id':ident,'title':'Research suppliers', 'description':'Research three suppliers in CSV.',
            'status':'open','flow':'bid','version':1,'budget_cents':500},'bid':{}}

    def test_evidence_fallback_third_pass_no_more_inference(self):
        details=self.details(); ident=details['bounty']['_id']
        state={'bounties':{ident:{'version':1}},'recent_outcomes':[]}
        decision={'decision':'bid','payout_cents':500,
            'note':'I will deliver a source-linked CSV of three suppliers within 24 hours after hire.',
            '_requires_web':True,'_sample_rows':['only one']}
        def api(path,*args):
            return {'bid':{'id':'b1','status':'active'}} if args else details
        with patch.dict(os.environ,{'BOUNTY_AGENT_API_KEY':'offline-test'}), \
             patch.object(w,'api',side_effect=api) as calls, patch.object(w,'assess',return_value=decision.copy()) as assess, \
             patch.object(w,'text_sources',return_value=([],[])),patch.object(w,'messages_for',return_value=[]), \
             patch.object(w,'unsupported_deliverable',return_value=None),patch.object(w,'bid_note_issue',return_value=None):
            w.handle_open({'_id':ident},state)
            self.assertEqual(state['bounties'][ident]['last_error'],'proposal_samples_missing')
            w.handle_open({'_id':ident},state)
            self.assertEqual(state['bounties'][ident]['evidence_failures'],2)
            self.assertNotIn(decision['note'],json.dumps(state))
            w.handle_open({'_id':ident},state)
            self.assertEqual(assess.call_count,2)
            posts=[x for x in calls.call_args_list if len(x.args)>1 and x.args[1]=='POST']
            self.assertEqual(len(posts),1)
            self.assertNotIn('only one',posts[0].args[2]['note'])
            self.assertEqual(state['bounties'][ident]['action'],'bid_active')
            self.assertTrue(any(x['action']=='proposal_sent_without_sample' for x in state['recent_outcomes']))

    def test_retry_abandoned_without_model_at_two(self):
        details=self.details('retry');state={'bounties':{'retry':{'version':1,'retry_count':2,'action':'retry_evaluation'}},'recent_outcomes':[]}
        with patch.object(w,'api',return_value=details),patch.object(w,'assess') as assess:
            w.handle_open({'_id':'retry'},state)
        assess.assert_not_called();self.assertEqual(state['bounties']['retry']['action'],'evaluation_abandoned')

    def test_new_version_reactivates_abandoned_evaluation(self):
        details=self.details('new');state={'bounties':{'new':{'version':0,'retry_count':4,'action':'evaluation_abandoned'}},'recent_outcomes':[]}
        with patch.object(w,'api',return_value=details),patch.object(w,'assess',return_value={'decision':'pass'}) as assess, \
             patch.object(w,'text_sources',return_value=([],[])),patch.object(w,'unsupported_deliverable',return_value=None):
            w.handle_open({'_id':'new'},state)
        assess.assert_called_once()

    def test_feasible_ask_submits_bid_with_question(self):
        details=self.details('ask');state={'bounties':{},'recent_outcomes':[]}
        decision={'decision':'ask','feasible':True,'question':'Is CSV suitable?', 'assumptions':'public company sources',
            'note':'I will deliver a researched CSV within 24 hours after hire.', 'payout_cents':500}
        with patch.object(w,'api',side_effect=lambda path,*args: {} if args else details) as api, \
             patch.object(w,'assess',return_value=decision),patch.object(w,'text_sources',return_value=([],[])), \
             patch.object(w,'unsupported_deliverable',return_value=None),patch.object(w,'bid_note_issue',return_value=None):
            w.handle_open({'_id':'ask'},state)
        posts=[x for x in api.call_args_list if len(x.args)>1]
        self.assertEqual(len(posts),1); self.assertTrue(posts[0].args[0].endswith('/bid'))
        self.assertIn('Is CSV suitable?',posts[0].args[2]['note'])
        self.assertEqual(state['bounties']['ask']['action'],'bid_active')

    def test_legacy_asked_gets_one_feasibility_review(self):
        details=self.details('ask');state={'bounties':{'ask':{'action':'asked','version':1}},'recent_outcomes':[]}
        decision={'decision':'ask','feasible':True,'question':'Is CSV suitable?', 'assumptions':'public company sources',
            'note':'I will deliver a researched CSV within 24 hours after hire.', 'payout_cents':500}
        with patch.object(w,'api',side_effect=lambda path,*args: {} if args else details) as api, \
             patch.object(w,'assess',return_value=decision),patch.object(w,'text_sources',return_value=([],[])), \
             patch.object(w,'unsupported_deliverable',return_value=None),patch.object(w,'bid_note_issue',return_value=None):
            w.handle_open({'_id':'ask'},state)
        posts=[x for x in api.call_args_list if len(x.args)>1]
        self.assertEqual(len(posts),1); self.assertTrue(posts[0].args[0].endswith('/bid'))
        self.assertIn('Is CSV suitable?',posts[0].args[2]['note'])
        self.assertEqual(state['bounties']['ask']['action'],'bid_active')

    def test_pace_november_fifth_prior_spend_does_not_freeze_new_day(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(o,'BUDGET_PATH',Path(folder)/'budget.json'),patch.object(o,'datetime') as dt:
            dt.now.return_value=NOW
            o.BUDGET_PATH.write_text(json.dumps({'month':'2026-11','reserved_micro_usd':6600000,'call_count':100}))
            result=o.reserve_monthly_budget(.01)
            self.assertEqual(result['reserved_micro_usd'],6610000)
            self.assertEqual(result['day_start_micro_usd'],6600000)

    def test_daily_growth_limited_to_seventy_cents(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ, {'GAMBIT_OPENAI_MONTHLY_LIMIT_USD':'20'}),patch.object(o,'BUDGET_PATH',Path(folder)/'b.json'),patch.object(o,'datetime') as dt:
            dt.now.return_value=NOW
            o.reserve_monthly_budget(.60)
            with self.assertRaisesRegex(RuntimeError,'openai_daily_pace_exceeded'):o.reserve_monthly_budget(.20)

    def test_shapes_outcomes_and_missing_not_rejection(self):
        state={}
        for idx,kind in enumerate(['work.message.created','submission.accepted','bid.declined']):
            event={'id':str(idx),'type':kind,'data':{'bounty_id':'x','private_text':'SECRET'}}
            c.record_event(state,event);c.record_event(state,event)
        self.assertEqual(state['analytics']['event_types']['bid.declined']['count'],1)
        self.assertNotIn('SECRET',json.dumps(state))
        rows=[{'bounty_id':s,'status':s,'payout_cents':500,'created_at':NOW.isoformat()} for s in ['active','declined','withdrawn','hired']]
        with patch.object(w,'pages',return_value=rows):self.assertEqual(w.platform_active_bid_ids(state),{'active'})
        c.record_bid_snapshot(state,rows[1:])
        self.assertEqual(state['commercial']['active']['bid_status'],'missing_unconfirmed')
        self.assertEqual(set(state['analytics']['bid_statuses_seen']),{'active','declined','withdrawn','hired'})

    def test_conversation_privacy_response_lag(self):
        state={};messages=[{'author_type':role,'created_at':(NOW+timedelta(minutes=n)).isoformat(),'content':'SECRET'} for n,role in [(0,'user'),(30,'agent'),(60,'user')]]
        c.record_conversation(state,'x',messages);row=state['commercial']['x']['conversation']
        self.assertTrue(row['unanswered']);self.assertEqual(row['response_lag_minutes'],[30]);self.assertNotIn('SECRET',json.dumps(state))

    def test_settlement_replay_and_funnel_no_network(self):
        state={'bounties':{},'recent_outcomes':[]}
        w.record_settlement(state,'x','evt',payout_cents=500,currency='USD');w.record_settlement(state,'x','evt',payout_cents=500,currency='USD')
        self.assertEqual(len(state['settlements']),1);self.assertIsNone(state['settlements']['x']['disbursed_at'])
        with tempfile.TemporaryDirectory() as folder,patch.object(w,'STATE_PATH',Path(folder)/'state.json'),patch.object(w,'api') as api,patch.object(w,'assess') as model:
            w.save_state(state);data=json.loads((Path(folder)/'funnel.json').read_text())
            self.assertEqual(data['cumulative']['settled'],1);api.assert_not_called();model.assert_not_called()

    def test_wolverine_eleven_negative_sources_and_fresh_positive(self):
        urls=['https://thedevs.org/hire/hire-developer-for-whatsapp-bot','https://kenzabyte.com/telegram-bulk-account-creator/',
          'https://news.ycombinator.com/item?id=44423410','https://galaxydigital.com/features/volunteer-management-dashboard',
          'https://contra.com/hire/stripe-freelancers','https://news.ycombinator.com/item?id=17540190',
          'https://techvinta.com/blog/jotform-pipedrive-crm-integration','https://news.ycombinator.com/item?id=19963435',
          'https://techearl.com/host-v3-onion-hidden-service','https://github.com/useagenthq/useagent/issues/113',
          'https://anubizhost.com/en/tor-hidden-service-static-site-hosting-2026']
        for url in urls:
            self.assertFalse(brain.public_buyer_invitation({'source_url':url,'page_fetched':True,'page_text':'Vendor product or bug report; contact support@example.org','contact':'support@example.org'}))
        row={'source_url':'https://example.org/request','page_fetched':True,'page_text':'We are looking for a freelancer to build our CSV report. Submit your proposal.',
             'contact':'buyer@example.org','created_at':datetime.now(timezone.utc).isoformat()}
        self.assertTrue(brain.public_buyer_invitation(row))
        with patch.object(brain,'agent_send') as send:
            self.assertEqual(brain.send_offer(row,{},{} )['result'],'offer_would_send');send.assert_not_called()
        row['created_at']=(datetime.now(timezone.utc)-timedelta(days=61)).isoformat()
        self.assertFalse(brain.public_buyer_invitation(row))

    def test_cyclops_truncated_json_retries_once_same_limit(self):
        replies=[io.BytesIO(json.dumps({'message':{'content':'{"bad'}}).encode()),io.BytesIO(json.dumps({'message':{'content':'{"good":true}'}}).encode())]
        with patch.object(cyclops.urllib.request,'urlopen',side_effect=replies) as call:
            self.assertEqual(cyclops.model_json({},'system',{},num_predict=1800),{'good':True})
        payloads=[json.loads(x.args[0].data) for x in call.call_args_list]
        self.assertEqual([x['options']['num_predict'] for x in payloads],[1800,1800])
        self.assertIn('Be concise',payloads[1]['messages'][0]['content'])

    def test_cyclops_import_precheck(self):
        with patch('importlib.util.find_spec',return_value=None):self.assertEqual(cyclops.missing_import('import pandas\nprint(1)'), 'pandas')
        self.assertIsNone(cyclops.missing_import('import json\nfrom pathlib import Path'))
        self.assertEqual(cyclops.CONCEPT_SCHEMA['properties']['production_goal']['maxLength'],800)
        self.assertEqual(cyclops.CONCEPT_SCHEMA['properties']['output_files']['maxItems'],6)

    def test_past_pass_remains_in_funnel_after_bid(self):
        state={'bounties':{'b':{'action':'bid_active'}},'commercial':{'b':{}}}
        c.record_action(state,'pass','b',NOW.isoformat(),'unsupported')
        self.assertEqual(c.summarize(state,NOW)['cumulative']['passed_by_reason'],{'unsupported':1})

    def test_detail_status_transition_records_time(self):
        state={'commercial':{'b':{'bid_status':'active'}}}
        with patch.object(w,'timestamp',return_value=NOW.isoformat()):
            w.record_bid_measurement(state,'b',{'status':'declined'})
        self.assertEqual(state['commercial']['b']['bid_status_changed_at'],NOW.isoformat())

    def test_load_abandons_legacy_retry_ten_before_due_time(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(w,'STATE_PATH',Path(folder)/'state.json'):
            w.STATE_PATH.write_text(json.dumps({'schema':1,'bounties':{'b':{'action':'retry_evaluation','retry_count':10,'retry_at':'2099-01-01T00:00:00+00:00'}}}))
            entry=w.load_state()['bounties']['b']
        self.assertEqual(entry['action'],'evaluation_abandoned')
        self.assertEqual(entry['retry_count'],4)
        self.assertIsNone(entry['retry_at'])
