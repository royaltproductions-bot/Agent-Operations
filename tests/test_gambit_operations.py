"""Regression scenarios for marketplace continuity, queueing, and recovery."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from tools import gambit_worker as worker
from tools import gambit_early_scan as early


class OperationsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        context = patch.object(worker, 'STATE_PATH', Path(temporary.name) / 'state.json')
        context.start()
        self.addCleanup(context.stop)

    def state(self):
        return {'schema': 1, 'bounties': {}, 'events_cursor': 'old',
                'recent_outcomes': [], 'settlements': {}}

    def event(self, bounty, number=1):
        return {'id': f'event-{number}', 'type': 'work.message.created',
                'data': {'bounty_id': bounty}}

    def test_off_feed_buyer_reply_is_resolved_and_queued(self):
        state = self.state()
        state['bounties']['old'] = {'action': 'bid_active', 'version': 1}
        detail = {'bounty': {'status': 'open', 'version': 1},
                  'bid': {'status': 'active', 'bounty_version': 1}}
        with patch.object(worker, 'pages', side_effect=[[], []]), \
             patch.object(worker, 'relevant_events', return_value=([self.event('old')], 'next')), \
             patch.object(worker, 'api', return_value=detail) as api:
            *_, waiting, active = worker.scan(state)
        self.assertEqual([item['_id'] for item in waiting], ['old'])
        self.assertFalse(active)
        api.assert_called_once_with('/v1/agent/bounties/old')

    def test_off_feed_hire_is_recovered_as_paid_work(self):
        state = self.state()
        detail = {'bounty': {'status': 'claimed', 'version': 1},
                  'claim': {'status': 'active', 'claim_id': 'ours'}}
        event = {'id': 'hired', 'type': 'bounty.claimed', 'data': {'bounty_id': 'job'}}
        with patch.object(worker, 'pages', side_effect=[[], []]), \
             patch.object(worker, 'relevant_events', return_value=([event], 'next')), \
             patch.object(worker, 'api', return_value=detail):
            *_, waiting, active = worker.scan(state)
        self.assertFalse(waiting)
        self.assertEqual([item['_id'] for item in active], ['job'])

    def test_deleted_event_target_does_not_block_other_work(self):
        state = self.state()
        opened = [{'_id': 'new', 'version': 1}]
        with patch.object(worker, 'pages', side_effect=[opened, []]), \
             patch.object(worker, 'relevant_events', return_value=([self.event('deleted')], 'next')), \
             patch.object(worker, 'api', side_effect=RuntimeError('bounty_api_NOT_FOUND')):
            *_, waiting, active = worker.scan(state)
        self.assertEqual([item['_id'] for item in waiting], ['new'])
        self.assertEqual(state['recent_outcomes'][-1]['action'], 'event_target_unavailable')

    def test_unlisted_retry_wakes_watcher_when_due(self):
        now = datetime.now(timezone.utc)
        state = self.state()
        state.update(last_check=now.isoformat(), last_bid_reconciliation=now.isoformat())
        state['bounties']['gone'] = {'action': 'retry_evaluation',
            'retry_at': (now - timedelta(seconds=1)).isoformat()}
        self.assertTrue(early.admission(state, [], [], [], now)['needs_run'])
        state['bounties']['gone']['retry_at'] = (now + timedelta(minutes=3)).isoformat()
        self.assertFalse(early.admission(state, [], [], [], now)['needs_run'])

    def test_buyer_followup_precedes_fresh_opportunity(self):
        state = self.state()
        state['bounties']['buyer'] = {'action': 'bid_active', 'version': 1}
        opened = [{'_id': 'new', 'version': 1}, {'_id': 'buyer', 'version': 1}]
        with patch.object(worker, 'pages', side_effect=[opened, []]), \
             patch.object(worker, 'relevant_events', return_value=([self.event('buyer')], 'next')):
            *_, waiting, _ = worker.scan(state)
        self.assertEqual([item['_id'] for item in waiting], ['buyer', 'new'])

    def run_batch(self, state, waiting, active, events, calls):
        with patch.object(worker, 'load_state', return_value=state), \
             patch.object(worker, 'scan', return_value=(waiting, active, events, 'next', waiting, active)), \
             patch.object(worker, 'remember_open_bounties', return_value=False), \
             patch.object(worker, 'recover_unlisted_evaluations', return_value=False), \
             patch.object(worker, 'capture_new_details'), \
             patch.object(worker, 'bid_reconciliation_due', return_value=False), \
             patch.object(worker, 'handle_open', side_effect=lambda item, s: calls.append(('open', item['_id']))), \
             patch.object(worker, 'handle_hired', side_effect=lambda item, s: calls.append(('hired', item['_id']))), \
             redirect_stdout(io.StringIO()):
            worker.run()

    def test_hired_work_precedes_any_new_bids(self):
        calls = []
        self.run_batch(self.state(), [{'_id': 'new', 'version': 1}],
                       [{'_id': 'paid', 'status': 'claimed'}], [], calls)
        self.assertEqual(calls, [('hired', 'paid'), ('open', 'new')])

    def test_ninth_buyer_reply_is_not_lost_or_starved(self):
        state = self.state()
        waiting = [{'_id': f'b{i}', 'version': 1} for i in range(9)]
        events = [self.event(f'b{i}', i) for i in range(9)]
        self.run_batch(state, waiting, [], events, [])
        self.assertEqual(state['events_cursor'], 'old')
        self.assertEqual(len(state['handled_event_ids']), 8)
        with patch.object(worker, 'api', return_value={'events': events, 'next_cursor': 'next', 'has_more': False}):
            remaining, _ = worker.relevant_events(state)
        self.assertEqual(remaining, [events[-1]])
        self.run_batch(state, waiting[-1:], [], remaining, [])
        self.assertEqual(state['events_cursor'], 'next')
        self.assertNotIn('handled_event_ids', state)

    def test_upload_checkpoint_survives_delivery_retry(self):
        state = self.state()
        state['bounties']['job'] = {'action': 'upload_pending', 'claim_id': 'claim',
            'content_digest': 'digest', 'attachment_id': 'uploaded'}
        worker.schedule_fulfillment_retry(state, 'job', TimeoutError())
        self.assertEqual(state['bounties']['job']['attachment_id'], 'uploaded')
        with patch.object(worker, 'api', return_value={}) as api:
            result = worker.upload_csv('job', b'name\nA\n', state, 'claim', 'digest')
        self.assertEqual(result, 'uploaded')
        api.assert_called_once_with('/v1/agent/attachments/uploaded/complete', 'POST')

    def test_state_write_keeps_previous_file_if_replacement_fails(self):
        state = self.state()
        worker.save_state(state)
        before = worker.STATE_PATH.read_bytes()
        state['events_cursor'] = 'new'
        with patch.object(Path, 'replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                worker.save_state(state)
        self.assertEqual(worker.STATE_PATH.read_bytes(), before)

    def test_gateway_failure_retries_reads(self):
        error = urllib.error.HTTPError('https://example.test', 502, 'Bad Gateway', {}, io.BytesIO(b'{}'))
        with patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test'}), \
             patch.object(worker.urllib.request, 'urlopen', side_effect=[error, io.BytesIO(b'{"bounties":[]}')]) as api, \
             patch.object(worker.time, 'sleep'):
            self.assertEqual(worker.api('/v1/agent/bounties'), {'bounties': []})
        self.assertEqual(api.call_count, 2)

    def test_gateway_failure_does_not_duplicate_upload_reservation(self):
        error = urllib.error.HTTPError('https://example.test', 503, 'Unavailable', {}, io.BytesIO(b'{}'))
        with patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test'}), \
             patch.object(worker.urllib.request, 'urlopen', side_effect=error) as api, \
             patch.object(worker.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError):
                worker.api('/v1/agent/bounties/job/attachments', 'POST', {'filename': 'a.csv'})
        self.assertEqual(api.call_count, 1)
        sleep.assert_not_called()

    def test_rate_limit_wait_is_not_shortened(self):
        error = urllib.error.HTTPError('https://example.test', 429, 'Rate limited', {'Retry-After': '120'}, io.BytesIO(b'{}'))
        with patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test'}), \
             patch.object(worker.urllib.request, 'urlopen', side_effect=[error, io.BytesIO(b'{}')]), \
             patch.object(worker.time, 'sleep') as sleep:
            worker.api('/v1/agent/bounties')
        sleep.assert_called_once_with(120)

    def test_replayed_event_respects_paid_retry_delay_in_both_scanners(self):
        now = datetime.now(timezone.utc)
        state = self.state()
        state.update(last_check=now.isoformat(), last_bid_reconciliation=now.isoformat())
        state['bounties']['paid'] = {'action': 'retry_fulfillment',
            'retry_at': (now + timedelta(minutes=3)).isoformat()}
        held = [{'_id': 'paid', 'status': 'claimed'}]
        event = {'id': 'feedback', 'type': 'submission.verification_failed',
                 'data': {'bounty_id': 'paid', 'reason': 'Correct the totals'}}
        self.assertFalse(early.admission(state, [], held, [event], now)['needs_production_model'])
        self.assertFalse(early.admission(state, [], held, [event], now)['needs_run'])
        with patch.object(worker, 'pages', side_effect=[[], held]), \
             patch.object(worker, 'relevant_events', return_value=([event], 'next')):
            *_, active = worker.scan(state)
        self.assertFalse(active)
        self.run_batch(state, [], [], [event], [])
        self.assertEqual(state['events_cursor'], 'old')
        self.assertEqual(state['deferred_bounty_ids'], ['paid'])
        self.assertNotIn('feedback', state['handled_event_ids'])
        state['bounties']['paid']['retry_at'] = (now - timedelta(seconds=1)).isoformat()
        with patch.object(worker, 'pages', side_effect=[[], held]), \
             patch.object(worker, 'relevant_events', return_value=([event], 'next')):
            *_, active = worker.scan(state)
        self.assertEqual(active, held)
