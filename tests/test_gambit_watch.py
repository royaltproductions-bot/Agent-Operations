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
             patch.object(watch, 'checkpoint'), \
             patch.object(watch, 'relevant_main_change', return_value=False):
            watch.watch(seconds=90, clock=lambda: tick[0], sleep=sleep)
        self.assertEqual(cycle.call_count, 3)

    def test_resident_yields_after_checkpoint_when_relevant_main_changes(self):
        tick=[0]
        with patch.object(watch,'relevant_main_change',return_value=True), \
             patch.object(watch,'record_health') as health, \
             patch.object(watch,'cycle') as cycle:
            result=watch.watch(seconds=90,clock=lambda:tick[0],sleep=lambda _:None)
        self.assertEqual(result,(0,0))
        health.assert_called_once_with(0,0)
        cycle.assert_not_called()

    def test_relevant_change_refreshes_origin_main_explicitly(self):
        fetch_result=type("R",(),{"returncode":0})()
        diff_result=type("R",(),{"returncode":1})()
        with patch.object(watch.subprocess,'run',
                          side_effect=[fetch_result,diff_result]) as run:
            self.assertTrue(watch.relevant_main_change())
        self.assertIn("main:refs/remotes/origin/main",run.call_args_list[0].args[0])
        self.assertIn("origin/main",run.call_args_list[1].args[0])

    def test_relevant_change_check_ignores_fetch_failure(self):
        with patch.object(watch.subprocess,'run') as run:
            run.return_value.returncode=1
            self.assertFalse(watch.relevant_main_change())
        self.assertEqual(run.call_count,1)

    def test_housekeeping_uses_no_model_and_saves_state(self):
        with patch.object(watch, 'probe', side_effect=[{'needs_run':'true'},
             {'needs_run':'true', 'needs_model':'false'}]), \
             patch.object(watch, 'checkpoint') as checkpoint, \
             patch.object(watch, 'prepare_models') as models, \
             patch.object(watch, 'prepare_video_runtime') as video, \
             patch.object(watch, 'command') as command:
            watch.cycle()
        models.assert_not_called()
        video.assert_not_called()
        self.assertEqual(checkpoint.call_count, 3)
        self.assertIn('tools.gambit_worker', command.call_args.args[0])

    def test_missing_openai_key_keeps_market_watch_but_blocks_qwen_bids(self):
        with patch.dict(watch.os.environ, {
                 "GAMBIT_INFERENCE_PROVIDER": "openai", "OPENAI_API_KEY": ""}), \
             patch.object(watch, 'probe', side_effect=[
                 {'needs_run': 'true'},
                 {'needs_run': 'true', 'needs_model': 'true',
                  'needs_production_model': 'false'}]), \
             patch.object(watch, 'checkpoint') as checkpoint, \
             patch.object(watch, 'prepare_models') as local_model, \
             patch.object(watch, 'command') as command:
            watch.cycle()
        self.assertEqual(checkpoint.call_count, 2)
        local_model.assert_not_called()
        command.assert_not_called()

    def test_cycle_prepares_video_runtime_only_for_mp4_work(self):
        with patch.object(watch, 'probe', side_effect=[
                 {'needs_run':'true'},
                 {'needs_run':'true', 'needs_model':'false',
                  'needs_video_runtime':'true'}]), \
             patch.object(watch, 'checkpoint'), \
             patch.object(watch, 'prepare_video_runtime') as video, \
             patch.object(watch, 'command'):
            watch.cycle()
        video.assert_called_once_with()

    def test_video_runtime_install_is_lazy_and_capability_checked(self):
        from tools import gambit_artifacts
        with patch.object(gambit_artifacts, 'video_capable',
                          side_effect=[False, True]) as capable, \
             patch.object(watch, 'command') as command:
            watch.prepare_video_runtime()
        self.assertEqual(capable.call_count, 2)
        commands=[call.args[0] for call in command.call_args_list]
        self.assertIn(['sudo','apt-get','update','-qq'], commands)
        self.assertIn(['sudo','apt-get','install','-y','--no-install-recommends','ffmpeg'], commands)

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

    def test_scheduled_watch_is_resident_without_self_dispatch(self):
        workflow=Path('.github/workflows/gambit-bounty.yml').read_text()
        self.assertIn("GAMBIT_WATCH_SECONDS: '13200'", workflow)
        self.assertIn("GAMBIT_WATCH_INTERVAL_SECONDS: '300'", workflow)
        self.assertIn("cron: '17 0,4,8,12,16,20 * * *'", workflow)
        self.assertNotIn('gh workflow run gambit-bounty.yml', workflow)
        self.assertNotIn('actions: write', workflow)
        self.assertEqual(watch.watch.__defaults__[0], 240)
        self.assertEqual(watch.watch.__defaults__[1], 30)
