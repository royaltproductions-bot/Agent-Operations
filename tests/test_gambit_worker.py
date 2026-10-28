"""Offline contract checks: no fake opportunities, secret leaks or duplicate bids."""
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
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [], None, [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}):
            gambit.run('probe')

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


if __name__ == '__main__':
    unittest.main()
