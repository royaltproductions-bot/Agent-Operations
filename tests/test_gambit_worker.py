"""Offline contract checks: no fake opportunities, secret leaks or duplicate bids."""
from contextlib import redirect_stdout
import io
import json
import os
import unittest
from unittest.mock import patch

from tools import gambit_worker as gambit


class GambitTests(unittest.TestCase):
    def test_stable_idempotency_key(self):
        self.assertEqual(gambit.key('bid', 'x', 2, 500), gambit.key('bid', 'x', 2, 500))
        self.assertNotEqual(gambit.key('bid', 'x', 2, 500), gambit.key('bid', 'x', 3, 500))

    def test_no_model_when_no_jobs(self):
        state = {'schema': 1, 'bounties': {}, 'events_cursor': None, 'recent_outcomes': []}
        output = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [], None, [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), redirect_stdout(output):
            gambit.run('probe')
        self.assertFalse(json.loads(output.getvalue())['needs_run'])

    def test_settlement_event_runs_without_loading_model(self):
        state = {'schema': 1, 'bounties': {}, 'events_cursor': None, 'recent_outcomes': []}
        event = {'id': 'e1', 'type': 'work.completed', 'data': {'bounty_id': 'b1'}}
        output = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [event], 'cursor1', [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), redirect_stdout(output):
            gambit.run('probe')
        result = json.loads(output.getvalue())
        self.assertTrue(result['needs_run'])
        self.assertFalse(result['needs_model'])

    def test_existing_active_bid_never_duplicated(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 2},
                   'bid': {'status': 'active', 'bounty_version': 2}}
        state = {'bounties': {}}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'assess') as model:
            gambit.handle_open({'_id': 'b1'}, state)
            model.assert_not_called()
        self.assertEqual(state['bounties']['b1']['action'], 'bid_active')

    def test_fundable_bid_uses_exact_current_version(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 6, 'flow': 'bid'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        calls = []
        def request(path, method='GET', payload=None):
            calls.append((path, method, payload))
            return details if method == 'GET' else {'bid': {'id': 'bid1'}}
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'bid', 'payout_cents': 1700, 'note': 'I can deliver the report.'}):
            gambit.handle_open({'_id': 'b1'}, state)
        sent = [c for c in calls if c[1] == 'POST']
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2]['expected_version'], 6)
        self.assertEqual(sent[0][2]['payout_cents'], 1700)

    def test_secret_not_persisted_in_receipt(self):
        state = {'recent_outcomes': []}
        gambit.note(state, 'bid_sent', 'b1', payout_cents=500)
        self.assertNotIn('api_key', str(state))

    def test_declined_bid_passes_owner_feedback_into_reassessment(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 3, 'flow': 'bid'},
                   'bid': {'_id': 'oldbid', 'status': 'declined', 'payout_cents': 2400}}
        state = {'bounties': {'b1': {'action': 'bid_sent'}}, 'recent_outcomes': []}
        owner_reply = {'author_type': 'user', 'content': {'type': 'text', 'text': 'Can you narrow scope?'}}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'messages_for', return_value=[owner_reply]), \
             patch.object(gambit, 'assess', return_value={'decision': 'pass'}) as assess:
            gambit.handle_open({'_id': 'b1'}, state)
        self.assertEqual(assess.call_args.args[1], [owner_reply])
        self.assertEqual(state['bounties']['b1']['last_declined_bid'], 'oldbid')
        self.assertIn('bid_declined', [x['action'] for x in state['recent_outcomes']])

    def test_active_bid_answers_new_private_buyer_message_once(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 3, 'flow': 'bid'},
                   'bid': {'status': 'active', 'bounty_version': 3}}
        state = {'bounties': {'b1': {'action': 'bid_sent'}}, 'recent_outcomes': []}
        owner_reply = {'_id': 'msg1', 'author_type': 'user',
                       'content': {'type': 'text', 'text': 'Can you format as a table?'}}
        event = {'type': 'work.message.created', 'data': {'bounty_id': 'b1'}}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'msg2'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'CURRENT_EVENTS', [event]), \
             patch.object(gambit, 'messages_for', return_value=[owner_reply]), \
             patch.object(gambit, 'respond_to_buyer', return_value={
                 'reply_text': 'Yes, I can provide a plain-text table.'}) as respond:
            gambit.handle_open({'_id': 'b1'}, state)
            gambit.handle_open({'_id': 'b1'}, state)
        self.assertEqual(respond.call_count, 1)
        self.assertEqual(state['bounties']['b1']['last_owner_reply_id'], 'msg1')
        self.assertEqual(sum(1 for c in api.call_args_list
                             if len(c.args) > 1 and c.args[1] == 'POST'), 1)

    def test_quality_failure_gets_one_revision_before_submission(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        bad = {'status': 'ready', 'deliverable_title': 'Report', 'deliverable_text': 'Incomplete'}
        good = {'status': 'ready', 'deliverable_title': 'Report', 'deliverable_text': 'Complete report'}
        state = {'bounties': {}, 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'submission_id': 'submission-1'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', side_effect=[bad, good]) as produce, \
             patch.object(gambit, 'review', side_effect=[
                 {'approved': False, 'reason': 'Missing required summary'},
                 {'approved': True, 'reason': 'Complete'}]):
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(produce.call_count, 2)
        self.assertIn('Missing required summary', produce.call_args.args[3])
        submitted = [c for c in api.call_args_list
                     if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(submitted), 1)
        self.assertEqual(state['bounties']['b1']['action'], 'submitted')
        self.assertIn('quality_revision_attempt', [x['action'] for x in state['recent_outcomes']])


if __name__ == '__main__':
    unittest.main()
