"""Resident-loop contracts: housekeeping and failures must not end market coverage."""
from unittest import TestCase
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import tempfile
from pathlib import Path
from tools import gambit_watch as watch, gambit_worker as worker, gambit_early_scan as early


class WatchTests(TestCase):
    def test_watch_continues_after_a_failed_cycle(self):
        tick = [0]
        def sleep(seconds):
            tick[0] += seconds
        with patch.object(watch, 'cycle', side_effect=[None, RuntimeError('temporary'), None]) as cycle, \
             patch.object(watch, 'checkpoint'):
            watch.watch(seconds=90, clock=lambda: tick[0], sleep=sleep)
        self.assertEqual(cycle.call_count, 3)

    def test_housekeeping_uses_no_model_and_saves_state(self):
        with patch.object(watch, 'probe', side_effect=[{'needs_run':'true'},
             {'needs_run':'true', 'needs_model':'false'}]), \
             patch.object(watch, 'checkpoint') as checkpoint, \
             patch.object(watch, 'prepare_models') as models, \
             patch.object(watch, 'command') as command:
            watch.cycle()
        models.assert_not_called()
        self.assertEqual(checkpoint.call_count, 3)
        self.assertIn('tools.gambit_worker', command.call_args.args[0])

    def test_probe_outputs_do_not_leak_between_cycles(self):
        def command(args, env):
            Path(env['GITHUB_OUTPUT']).write_text('needs_run=false\n')
        with patch.object(watch, 'command', side_effect=command):
            self.assertEqual(watch.probe('ignored'), {'needs_run':'false'})

    def test_delisted_research_retry_is_recovered_by_id(self):
        now = datetime.now(timezone.utc)
        state = {'schema':1, 'bounties':{'job':{'action':'retry_search', 'version':1,
            'retry_at':(now-timedelta(minutes=1)).isoformat()}}, 'recent_outcomes':[],
            'last_check':now.isoformat(), 'last_bid_reconciliation':now.isoformat()}
        self.assertTrue(early.admission(state, [], [], [], now)['recovery_due'])
        waiting = []
        with patch.object(worker, 'api', return_value={'bounty':{'status':'open', 'version':1}}), \
             patch.object(worker, 'remember_details'):
            state['bounty_archive'] = {'job': {}}
            self.assertTrue(worker.recover_unlisted_evaluations(state, [], waiting))
        self.assertEqual(waiting, [{'_id':'job', 'version':1}])

    def test_delisted_research_cooldown_does_not_repeat_early(self):
        now = datetime.now(timezone.utc)
        state = {'bounties':{'job':{'action':'retry_search',
            'retry_at':(now+timedelta(minutes=1)).isoformat()}},
            'last_check':now.isoformat(), 'last_bid_reconciliation':now.isoformat()}
        self.assertFalse(early.admission(state, [], [], [], now)['needs_run'])
        with patch.object(worker, 'api') as api:
            self.assertFalse(worker.recover_unlisted_evaluations(state, [], []))
        api.assert_not_called()
