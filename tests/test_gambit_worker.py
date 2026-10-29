"""Offline contract checks: no fake opportunities, secret leaks or duplicate bids."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from tools import gambit_worker as gambit


class GambitTests(unittest.TestCase):
    def setUp(self):
        # Worker tests must never overwrite the live committed watch state.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        location = patch.object(gambit, 'STATE_PATH', Path(temporary.name) / 'state.json')
        location.start()
        self.addCleanup(location.stop)

    def test_public_csv_sources_must_be_actually_read(self):
        data = b"company,source_url\nAcme,https://example.org/acme\n"
        evidence = {"results": [{"url": "https://example.org/acme",
                                "evidence_level": "page_read", "page_text": "Acme"}]}
        gambit.validate_csv_source_urls(data, evidence)
        with self.assertRaisesRegex(ValueError, "source URL"):
            gambit.validate_csv_source_urls(
                b"company,source_url\nAcme,https://invented.example/fake\n", evidence)

    def test_csv_source_explicitly_in_buyer_file_is_acceptable(self):
        evidence = {"results": [{"url": "https://example.org/acme",
                                "evidence_level": "page_read", "page_text": "Acme"}]}
        gambit.validate_csv_source_urls(
            b"company,source_url\nBravo,https://buyer.test/provided\n", evidence,
            [{"filename": "sources.txt", "text": "https://buyer.test/provided"}])

    def test_search_snippet_is_not_confirmed_source_for_csv(self):
        evidence = {"results": [{"url": "https://example.org/unread",
                                "evidence_level": "search_snippet", "page_text": ""},
                               {"url": "https://example.org/read",
                                "evidence_level": "page_read", "page_text": "Read"}]}
        with self.assertRaisesRegex(ValueError, "source URL"):
            gambit.validate_csv_source_urls(
                b"company,source_url\nUnknown,https://example.org/unread\n", evidence)

    def test_stable_idempotency_key(self):
        self.assertEqual(gambit.key('bid', 'x', 2, 500), gambit.key('bid', 'x', 2, 500))
        self.assertNotEqual(gambit.key('bid', 'x', 2, 500), gambit.key('bid', 'x', 3, 500))

    def test_market_api_honors_rate_limit_and_recovers(self):
        error = urllib.error.HTTPError('https://api.trybounty.ai/test', 429,
                                       'Too Many Requests', {'Retry-After': '3'}, None)
        with patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'dummy-test-key'}), \
             patch.object(gambit.urllib.request, 'urlopen', side_effect=[
                 error, io.BytesIO(b'{"bounties": []}')]) as urlopen, \
             patch.object(gambit.time, 'sleep') as sleep:
            self.assertEqual(gambit.api('/v1/agent/bounties'), {'bounties': []})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(3.0)

    def test_market_get_retries_temporary_network_failure(self):
        with patch.dict(os.environ, {"BOUNTY_AGENT_API_KEY": "dummy-test-key"}), \
             patch.object(gambit.urllib.request, "urlopen", side_effect=[
                 TimeoutError(), io.BytesIO(b'{"bounties": []}')]) as request, \
             patch.object(gambit.time, "sleep") as sleep:
            self.assertEqual(gambit.api("/v1/agent/bounties"), {"bounties": []})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_idempotent_private_message_retries_network_failure(self):
        payload = {"content": {"type": "text", "text": "Done"},
                   "idempotency_key": "stable-message"}
        with patch.dict(os.environ, {"BOUNTY_AGENT_API_KEY": "dummy-test-key"}), \
             patch.object(gambit.urllib.request, "urlopen", side_effect=[
                 urllib.error.URLError("offline"), io.BytesIO(b'{"ok":true}')]) as request, \
             patch.object(gambit.time, "sleep"):
            self.assertEqual(gambit.api("/v1/agent/bounties/a/messages",
                                        "POST", payload), {"ok": True})
        self.assertEqual(request.call_count, 2)

    def test_non_idempotent_attachment_creation_is_not_retried(self):
        with patch.dict(os.environ, {"BOUNTY_AGENT_API_KEY": "dummy-test-key"}), \
             patch.object(gambit.urllib.request, "urlopen",
                          side_effect=TimeoutError()) as request, \
             patch.object(gambit.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "network_unavailable"):
                gambit.api("/v1/agent/bounties/a/attachments", "POST",
                           {"filename": "report.pdf", "size": 100})
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_fast_decisions_use_small_model_while_delivery_keeps_large_model(self):
        answer = io.BytesIO(b'{"message":{"content":"{\\"decision\\":\\"pass\\"}"},"done":true,"done_reason":"stop"}\n')
        with patch.object(gambit.urllib.request, 'urlopen', return_value=answer) as request, \
             patch.object(gambit, 'TRIAGE_MODEL', 'qwen3:1.7b'), \
             patch.object(gambit, 'MODEL', 'qwen3:8b'):
            gambit.infer(gambit.DECIDE_SCHEMA, 'Assess', {'title': 'Example'}, fast=True)
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload['model'], 'qwen3:1.7b')
            self.assertEqual(payload['keep_alive'], '60m')
            self.assertEqual(request.call_args.kwargs['timeout'],
                             gambit.EVALUATION_TIMEOUT_SECONDS)
        with patch.object(gambit.urllib.request, 'urlopen', return_value=
                          io.BytesIO(b'{"message":{"content":"{"},"done":false}\n'
                                     b'{"message":{"content":"}"},"done":true,"done_reason":"stop"}\n')) as request, \
             patch.object(gambit, 'MODEL', 'qwen3:8b'):
            gambit.infer({'type': 'object'}, 'Review', {}, num_predict=250)
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload['model'], 'qwen3:8b')
            self.assertTrue(payload['stream'])
            self.assertEqual(request.call_args.kwargs['timeout'], 180)

    def test_streamed_model_must_finish_json_before_delivery(self):
        for response in [
            b'{"message":{"content":"{\\"approved\\":true}"},"done":false}\n',
            b'{"message":{"content":"{}"},"done":true,"done_reason":"length"}\n',
        ]:
            with patch.object(gambit.urllib.request, 'urlopen',
                              return_value=io.BytesIO(response)):
                with self.assertRaises(RuntimeError):
                    gambit.infer({'type': 'object'}, 'Review', {}, num_predict=200)

    def test_conflicting_fast_reviews_are_arbitrated_before_any_offer(self):
        proposal = {'decision': 'pass', 'reason': 'Too hard'}
        challenger = {'decision': 'bid', 'reason': 'Existing resources suffice',
                      'note': 'Sourced spreadsheet', 'payout_cents': 2500}
        with patch.object(gambit, 'infer', side_effect=[challenger, challenger]) as infer:
            result = gambit.cross_check_decision({'bounty': {'title': 'Task'}}, proposal, 5500)
        self.assertEqual(result['decision'], 'bid')
        self.assertTrue(result['_audit_disagreement'])
        self.assertEqual(infer.call_count, 2)
        self.assertTrue(all(call.kwargs['fast'] for call in infer.call_args_list))

    def test_feasible_bid_uses_single_market_decision_not_redundant_votes(self):
        details = {"bounty": {"_id": "b-new", "version": 2, "status": "open",
                              "title": "Summarize the supplied memo", "flow": "bid",
                              "description": "Write a one-page summary of the owner's memo.",
                              "verification": "Accurate prose summary of supplied text."}}
        plan = {"requires_web": False, "queries": []}
        bid = {"decision": "bid", "payout_cents": 1900, "note": "Factual summary.",
               "sample_source_url": ""}
        with patch.object(gambit, "infer", side_effect=[plan, bid]) as inference, \
             patch.object(gambit, "cross_check_decision") as repeated:
            result = gambit.assess(details)
        self.assertEqual(result["decision"], "bid")
        self.assertEqual(inference.call_count, 2)  # research planning + one actual decision
        repeated.assert_not_called()

    def test_pass_decision_keeps_recheck_for_missed_opportunities(self):
        details = {"bounty": {"_id": "b-new", "version": 1, "status": "open",
                              "title": "Write short report", "description": "Plain report",
                              "flow": "bid"}}
        plan = {"requires_web": False, "queries": []}
        negative = {"decision": "pass", "reason": "Hard"}
        reconsidered = {"decision": "bid", "payout_cents": 2000, "note": "Report"}
        with patch.object(gambit, "infer", side_effect=[plan, negative]), \
             patch.object(gambit, "cross_check_decision",
                          return_value=reconsidered) as repeated:
            result = gambit.assess(details)
        self.assertEqual(result["decision"], "bid")
        repeated.assert_called_once()


    def test_three_way_conflict_queues_a_new_evaluation(self):
        proposal = {'decision': 'bid', 'payout_cents': 1500}
        challenger = {'decision': 'pass'}
        with patch.object(gambit, 'infer', side_effect=[challenger, {'decision': 'ask'}]):
            result = gambit.cross_check_decision({'bounty': {}}, proposal, 5500)
        self.assertTrue(result['_retry_decision'])
        self.assertEqual(result['decision'], 'pass')

    def test_fulfillment_failures_back_off_and_eventually_release(self):
        state = {'bounties': {'b1': {'action': 'hired', 'claim_id': 'c1'}},
                 'recent_outcomes': []}
        gambit.schedule_fulfillment_retry(state, 'b1', TimeoutError())
        self.assertEqual(state['bounties']['b1']['action'], 'retry_fulfillment')
        self.assertFalse(gambit.fulfillment_retry_due(state['bounties']['b1']))
        for _ in range(2):
            gambit.schedule_fulfillment_retry(state, 'b1', TimeoutError())
        self.assertEqual(state['bounties']['b1']['action'], 'fulfillment_failed_final')
        self.assertEqual(state['bounties']['b1']['claim_id'], 'c1')

    def test_claimed_work_waits_for_retry_due_date(self):
        item = {'_id': 'b1', 'status': 'claimed'}
        future = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        state = {'bounties': {'b1': {'action': 'retry_fulfillment',
                                    'retry_at': future}}}
        with patch.object(gambit, 'pages', side_effect=[[], [item]]), \
             patch.object(gambit, 'relevant_events', return_value=([], None)):
            self.assertEqual(gambit.scan(state)[-1], [])
        state['bounties']['b1']['retry_at'] = (
            datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with patch.object(gambit, 'pages', side_effect=[[], [item]]), \
             patch.object(gambit, 'relevant_events', return_value=([], None)):
            self.assertEqual(gambit.scan(state)[-1], [item])

    def test_exhausted_delivery_refunds_claim_instead_of_stalling(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed'},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        state = {'bounties': {'b1': {'action': 'fulfillment_failed_final',
                                     'claim_id': 'c1'}}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', side_effect=lambda path, method='GET',
                          payload=None: details if method == 'GET' else {}) as api:
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(state['bounties']['b1']['action'], 'unclaimed_unfulfillable')
        self.assertTrue(any(call.args[0].endswith('/unclaim')
                            for call in api.call_args_list))

    def test_unresolved_decision_cannot_permanently_pass_open_bounty(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open',
                              'version': 1, 'flow': 'bid'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'pass', '_retry_decision': True}):
            gambit.handle_open({'_id': 'b1'}, state)
        self.assertEqual(state['bounties']['b1']['action'], 'retry_evaluation')
        self.assertFalse(any(call.args[1] == 'POST' for call in api.call_args_list
                             if len(call.args) > 1))

    def test_no_model_when_no_jobs(self):
        state = {'schema': 1, 'bounties': {}, 'events_cursor': None, 'recent_outcomes': [],
                 'last_bid_reconciliation': datetime.now(timezone.utc).isoformat()}
        output = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [], None, [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), redirect_stdout(output):
            gambit.run('probe')
        self.assertFalse(json.loads(output.getvalue())['needs_run'])

    def test_new_bounty_is_preserved_and_encrypted_during_probe(self):
        state = {'schema': 1, 'bounties': {}, 'events_cursor': None,
                 'recent_outcomes': [], 'last_bid_reconciliation':
                 datetime.now(timezone.utc).isoformat()}
        item = {'_id': 'new-123', 'version': 4, 'status': 'open'}
        details = {'bounty': {'_id': 'new-123', 'version': 4,
                              'status': 'open', 'title': 'Private Buyer Project',
                              'description': 'Confidential buyer instructions',
                              'budget_cents': 25000, 'flow': 'bid'}}
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=(
                 [item], [], [], None, [item], [])), \
             patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'save_state') as save, \
             patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test-secret',
                                      'GITHUB_OUTPUT': ''}), \
             redirect_stdout(io.StringIO()):
            gambit.run('probe')
        self.assertEqual(state['bounties']['new-123']['action'], 'evaluation_pending')
        archive = state['bounty_archive']['new-123']
        self.assertIn('4', archive['versions'])
        ciphertext = archive['versions']['4']['encrypted_brief']
        self.assertNotIn('Private Buyer Project', json.dumps(state))
        self.assertNotIn('Confidential buyer instructions', json.dumps(state))
        self.assertNotIn('test-secret', json.dumps(state))
        with patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test-secret'}):
            self.assertEqual(gambit.saved_brief(state, 'new-123')['title'],
                             'Private Buyer Project')
        with patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'wrong-key'}):
            self.assertIsNone(gambit.saved_brief(state, 'new-123'))
        save.assert_called()

    def test_timeout_remains_pending_and_retries_without_duplicate_bid(self):
        state = {'schema': 1, 'bounties': {}, 'events_cursor': None,
                 'recent_outcomes': [], 'last_bid_reconciliation':
                 datetime.now(timezone.utc).isoformat()}
        item = {'_id': 'slow-456', 'version': 1}
        details = {'bounty': {'_id': 'slow-456', 'version': 1,
                              'status': 'open', 'title': 'Research task',
                              'description': 'Analyze a supplied dataset',
                              'flow': 'bid'}}
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=(
                 [item], [], [], None, [item], [])), \
             patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'assess', side_effect=TimeoutError()), \
             patch.object(gambit, 'save_state'), \
             patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test-secret'}), \
             redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'failed_bounty_jobs'):
                gambit.run()
        self.assertEqual(state['bounties']['slow-456']['action'], 'retry_evaluation')
        self.assertEqual(state['bounties']['slow-456']['retry_count'], 1)
        self.assertFalse(gambit.retry_due(state['bounties']['slow-456']))
        self.assertIn('1', state['bounty_archive']['slow-456']['versions'])
        self.assertFalse(any(call.args[1] == 'POST' for call in api.call_args_list
                             if len(call.args) > 1))

    def test_missing_listing_is_rechecked_and_retained(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        state = {'schema': 1, 'bounties': {'unlisted-789': {
            'version': 2, 'action': 'retry_evaluation', 'retry_at': past}},
            'bounty_archive': {'unlisted-789': {'first_detected_at': past,
                'last_known_status': 'open', 'version': 2, 'versions': {}}},
            'events_cursor': None, 'recent_outcomes': [],
            'last_bid_reconciliation': datetime.now(timezone.utc).isoformat()}
        detail = {'bounty': {'_id': 'unlisted-789', 'version': 2,
                             'status': 'open', 'title': 'Still available', 'flow': 'bid'}}
        output = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [], None, [], [])), \
             patch.object(gambit, 'api', return_value=detail), \
             patch.object(gambit, 'save_state'), \
             patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test-secret',
                                      'GITHUB_OUTPUT': ''}), redirect_stdout(output):
            gambit.run('probe')
        self.assertTrue(json.loads(output.getvalue())['needs_model'])
        self.assertIn('2', state['bounty_archive']['unlisted-789']['versions'])
        self.assertIn('unlisted-789', state['bounty_archive'])

    def test_closed_listing_keeps_history_and_stops_bidding(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        state = {'bounties': {'b1': {'action': 'retry_evaluation', 'version': 1,
                                   'retry_at': past}},
                 'bounty_archive': {'b1': {'last_known_status': 'open',
                                          'versions': {}}}, 'recent_outcomes': []}
        detail = {'bounty': {'_id': 'b1', 'version': 1, 'status': 'claimed',
                             'title': 'Previously open'}}
        with patch.object(gambit, 'api', return_value=detail), \
             patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'test-secret'}):
            recovered = []
            self.assertTrue(gambit.recover_unlisted_evaluations(state, [], recovered))
        self.assertEqual(recovered, [])
        self.assertEqual(state['bounties']['b1']['action'], 'closed_before_evaluation')
        self.assertIn('1', state['bounty_archive']['b1']['versions'])
        self.assertEqual(state['bounty_archive']['b1']['last_known_status'], 'claimed')

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

    def test_platform_settlements_are_durable_and_not_double_counted(self):
        state = {'bounties': {'b1': {'action': 'submitted', 'payout_cents': 2200}},
                 'recent_outcomes': []}
        self.assertTrue(gambit.record_settlement(state, 'b1', 'event-123'))
        self.assertFalse(gambit.record_settlement(state, 'b1', 'event-123'))
        self.assertFalse(gambit.record_settlement(state, 'b1', 'event-replayed'))
        self.assertEqual(state['settlements']['b1']['payout_cents'], 2200)
        self.assertEqual(len(state['recent_outcomes']), 1)
        self.assertEqual(state['recent_outcomes'][0]['action'], 'work.completed')

    def test_platform_settlement_without_known_payout_never_invents_earnings(self):
        state = {'bounties': {}, 'recent_outcomes': []}
        gambit.record_settlement(state, 'b2', 'event-456')
        self.assertIsNone(state['settlements']['b2']['payout_cents'])
        self.assertNotIn('payout_cents', state['recent_outcomes'][0])

    def test_official_settlement_details_recover_lost_payout_without_guessing(self):
        state = {'bounties': {'b3': {'action': 'submitted'}}, 'recent_outcomes': []}
        self.assertTrue(gambit.record_settlement(
            state, 'b3', 'event-789', payout_cents=3250, currency='USD'))
        self.assertEqual(state['settlements']['b3']['payout_cents'], 3250)
        self.assertEqual(state['settlements']['b3']['currency'], 'USD')
        self.assertFalse(gambit.record_settlement(
            state, 'b3', 'event-replayed', payout_cents=3250))
        self.assertEqual(len(state['recent_outcomes']), 1)

    def test_invalid_event_cursor_recovers_and_rechecks_waiting_contexts(self):
        state = {'schema': 1, 'bounties': {'b1': {'version': 3, 'action': 'bid_active'}},
                 'events_cursor': 'expired', 'recent_outcomes': []}
        opened = [{'bounties': [{'_id': 'b1', 'version': 3}], 'has_more': False}]
        responses = [
            RuntimeError('bounty_api_INVALID_CURSOR'),
            {'events': [], 'next_cursor': 'fresh', 'has_more': False}
        ]
        calls = []
        def request(path, method='GET', payload=None):
            calls.append(path)
            if path.startswith('/v1/agent/events'):
                outcome = responses.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            if 'status=open' in path:
                return opened[0]
            return {'bounties': [], 'has_more': False}
        with patch.object(gambit, 'api', side_effect=request):
            open_items, _, _, cursor, waiting, _ = gambit.scan(state)
        self.assertEqual(len(open_items), 1)
        self.assertEqual([x['_id'] for x in waiting], ['b1'])
        self.assertEqual(cursor, 'fresh')
        self.assertTrue(state['_cursor_reset'])
        self.assertIn('/v1/agent/events?limit=100', calls)

    def test_changed_cursor_persists_without_starting_model(self):
        state = {'schema': 1, 'bounties': {}, 'events_cursor': 'old',
                 'recent_outcomes': [], '_cursor_reset': True}
        output = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [], 'fresh', [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), redirect_stdout(output):
            gambit.run('probe')
        result = json.loads(output.getvalue())
        self.assertTrue(result['needs_run'])
        self.assertFalse(result['needs_model'])

    def test_empty_market_does_not_commit_rotating_tail_cursor_each_poll(self):
        state = {'schema': 1, 'bounties': {}, 'events_cursor': 'old',
                 'last_check': datetime.now(timezone.utc).isoformat(),
                 'last_bid_reconciliation': datetime.now(timezone.utc).isoformat(),
                 'recent_outcomes': []}
        output = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [], 'new', [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), redirect_stdout(output):
            gambit.run('probe')
        self.assertFalse(json.loads(output.getvalue())['needs_run'])
        state['last_check'] = (datetime.now(timezone.utc) -
                               timedelta(hours=7)).isoformat()
        output = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=([], [], [], 'new', [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), redirect_stdout(output):
            gambit.run('probe')
        self.assertTrue(json.loads(output.getvalue())['needs_run'])
        self.assertFalse(json.loads(output.getvalue())['needs_model'])

    def test_existing_active_bid_never_duplicated(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 2},
                   'bid': {'status': 'active', 'bounty_version': 2}}
        state = {'bounties': {}}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'assess') as model:
            gambit.handle_open({'_id': 'b1'}, state)
            model.assert_not_called()
        self.assertEqual(state['bounties']['b1']['action'], 'bid_active')

    def test_actual_video_bounty_is_rejected_without_model_or_bid(self):
        details = {'bounty': {
            '_id': 'video1', 'status': 'open', 'version': 1, 'flow': 'bid',
            'title': 'Create a Premium AI Dining App Demo Video',
            'description': 'Make a 2.5–3 minute animated product video.',
            'verification': ('Submit one final video file in MP4 or MOV, '
                             'plus a storyboard.')}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'assess') as assess, \
             patch.object(gambit, 'infer') as infer:
            gambit.handle_open({'_id': 'video1'}, state)
        assess.assert_not_called()
        infer.assert_not_called()
        self.assertFalse(any(c.args[1] == 'POST' for c in api.call_args_list
                             if len(c.args) > 1))
        self.assertEqual(state['bounties']['video1']['action'], 'pass_unsupported')

    def test_video_script_research_is_not_blocked(self):
        self.assertIsNone(gambit.unsupported_deliverable({'bounty': {
            'title': 'Research short-form video advertising and write a script',
            'description': 'Find five public case studies.',
            'verification': 'Deliver a text report with sources and a draft script.'}}))

    def test_external_account_operation_is_blocked_but_draft_is_allowed(self):
        self.assertEqual('authenticated_account_operation',
                         gambit.unsupported_deliverable({'bounty': {
            'title': 'Manage and schedule Instagram posts',
            'description': 'Log into our business account and publish daily.',
            'verification': 'Posts live on Instagram.'}}))
        self.assertIsNone(gambit.unsupported_deliverable({'bounty': {
            'title': 'Draft three Instagram captions',
            'description': 'Write copy for approved photos.',
            'verification': 'Deliver a text document with three captions.'}}))

    def test_regulated_filing_is_blocked_but_data_analysis_is_allowed(self):
        self.assertEqual('regulated_filing',
                         gambit.unsupported_deliverable({'bounty': {
            'title': 'File IRS forms for my business',
            'description': 'Submit the 2025 tax return.',
            'verification': 'Provide proof of official submission.'}}))
        self.assertIsNone(gambit.unsupported_deliverable({'bounty': {
            'title': 'Analyze expenses from a CSV',
            'verification': 'Summarize totals in a report.'}}))

    def test_unsupported_presentation_and_deployment_are_blocked(self):
        self.assertEqual('unsupported_file_output', gambit.unsupported_deliverable({
            'bounty': {'title': 'Market analysis',
                       'verification': 'Submit a final PPTX deck.'}}))
        self.assertEqual('software_deployment', gambit.unsupported_deliverable({
            'bounty': {'title': 'Deploy a website',
                       'verification': 'Provide a live deployed website URL.'}}))
        self.assertEqual('image_production', gambit.unsupported_deliverable({
            'bounty': {'title': 'Create a professional logo',
                       'verification': 'Provide a final logo design.'}}))
        self.assertEqual('audio_production', gambit.unsupported_deliverable({
            'bounty': {'title': 'Compose a podcast jingle',
                       'verification': 'Deliver a final audio recording.'}}))
        self.assertEqual('outbound_messaging', gambit.unsupported_deliverable({
            'bounty': {'title': 'Send outreach emails to leads',
                       'verification': 'Provide sent email confirmations.'}}))

    @patch.object(gambit, "website_capable", return_value=False)
    def test_static_site_source_cannot_be_bid_until_visual_qa_exists(self, _capable):
        self.assertEqual('website_visual_quality_unverified',
                         gambit.unsupported_deliverable({'bounty': {
            'title': 'Build a static HTML landing page',
            'verification': 'Deliver a ZIP of the standalone front-end source.'}}))
        self.assertEqual('software_project_not_supported', gambit.unsupported_deliverable({
            'bounty': {'title': 'Build a SaaS application',
                       'verification': 'Implement a backend and database.'}}))
        self.assertEqual('software_deployment', gambit.unsupported_deliverable({
            'bounty': {'title': 'Build a static landing page',
                       'description': 'Deploy and host the landing page publicly.',
                       'verification': 'Deliver a URL.'}}))

    @patch.object(gambit, "website_capable", return_value=False)
    def test_hired_static_site_is_released_before_unverified_delivery(self, _capable):
        details = {'bounty': {'_id': 'web1', 'status': 'claimed',
                              'title': 'Build a static HTML landing page',
                              'verification': 'Deliver the source ZIP.'},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'produce') as produce, \
             patch.object(gambit, 'upload_file') as upload:
            gambit.handle_hired({'_id': 'web1'}, state)
        produce.assert_not_called()
        upload.assert_not_called()
        self.assertEqual(state['bounties']['web1']['action'], 'unclaimed_unfulfillable')
        self.assertIn('/v1/agent/bounties/web1/unclaim',
                      [c.args[0] for c in api.call_args_list if len(c.args) > 1])

    def test_real_document_outputs_are_supported_but_forms_are_not(self):
        from tools.gambit_artifacts import requested_output_format
        docx = {'bounty': {'title': 'Research brief',
                           'verification': 'Deliver the finished DOCX report.'}}
        pdf = {'bounty': {'title': 'Write a PDF report',
                          'verification': 'Provide a readable final PDF file.'}}
        self.assertEqual({'docx'}, requested_output_format(docx))
        self.assertEqual({'pdf'}, requested_output_format(pdf))
        self.assertIsNone(gambit.unsupported_deliverable(docx))
        self.assertIsNone(gambit.unsupported_deliverable(pdf))
        self.assertEqual('unsupported_pdf_form', gambit.unsupported_deliverable({
            'bounty': {'title': 'Make a fillable PDF form'}}))
        self.assertEqual('unsupported_multi_or_file_format',
                         gambit.unsupported_deliverable({'bounty': {
                             'title': 'Create an executive report',
                             'verification': 'Deliver a final PDF report and XLSX spreadsheet.'}}))

    def test_output_format_mismatch_for_hired_work_is_revised_not_uploaded(self):
        details = {'bounty': {'_id': 'pdf1', 'status': 'claimed',
                              'title': 'PDF research report',
                              'verification': 'Deliver a final PDF file.'},
                   'claim': {'status': 'active', 'claim_id': 'claim1'}}
        draft = {'status': 'ready', 'deliverable_title': 'Report',
                 'deliverable_text': 'This is only text.', 'deliverable_format': 'text'}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=draft), \
             patch.object(gambit, 'upload_file') as upload:
            gambit.handle_hired({'_id': 'pdf1'}, state)
        upload.assert_not_called()
        self.assertFalse(any('/submissions' in c.args[0] for c in api.call_args_list))
        self.assertIn(state['bounties']['pdf1']['action'],
                      {'quality_rejected', 'quality_rejected_final'})

    def test_failure_receipts_disclose_safe_api_code_not_sensitive_text(self):
        self.assertEqual('bounty_api_RATE_LIMITED', gambit.safe_failure_reason(
            RuntimeError('bounty_api_RATE_LIMITED')))
        self.assertEqual('RuntimeError', gambit.safe_failure_reason(
            RuntimeError('buyer secret key 12345')))

    def test_existing_unsupported_video_bid_is_withdrawn(self):
        details = {'bounty': {'_id': 'video1', 'status': 'open', 'version': 1,
                              'title': 'Produce a product video',
                              'verification': 'Deliver final MP4 video.'},
                   'bid': {'status': 'active', 'bounty_version': 1}}
        state = {'bounties': {}, 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'status': 'withdrawn'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'respond_to_buyer') as reply:
            gambit.handle_open({'_id': 'video1'}, state)
        self.assertEqual([c.args[0] for c in api.call_args_list
                          if len(c.args) > 1 and c.args[1] == 'POST'],
                         ['/v1/agent/bounties/video1/bid/withdraw'])
        reply.assert_not_called()
        self.assertEqual(state['bounties']['video1']['action'],
                         'unsupported_bid_withdrawn')

    def test_unsupported_video_buyer_reply_cannot_promise_mp4(self):
        details = {'bounty': {'title': 'Create an animated demo video',
                              'verification': 'Provide final video file (MP4).'}}
        with patch.object(gambit, 'infer') as infer:
            result = gambit.respond_to_buyer(details, [])
        self.assertEqual(result['reply_text'], '')
        infer.assert_not_called()

    def test_unsupported_hired_video_stops_before_fabricated_submission(self):
        details = {'bounty': {'_id': 'video1', 'status': 'claimed',
                              'title': 'Make a video', 'verification': 'Upload MP4.'},
                   'claim': {'status': 'active', 'claim_id': 'claim1'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'produce') as produce:
            gambit.handle_hired({'_id': 'video1'}, state)
        produce.assert_not_called()
        self.assertEqual(state['bounties']['video1']['action'], 'unclaimed_unfulfillable')
        self.assertIn('/v1/agent/bounties/video1/unclaim',
                      [c.args[0] for c in api.call_args_list if len(c.args) > 1])

    def test_existing_bid_reconciles_without_buyer_edit_or_model(self):
        state = {'bounties': {'video1': {'version': 1, 'action': 'bid_active'}},
                 'recent_outcomes': [], 'events_cursor': None,
                 'last_check': datetime.now(timezone.utc).isoformat()}
        opened = [{'_id': 'video1', 'version': 1}]
        self.assertTrue(gambit.bid_reconciliation_due(state, opened))
        details = {'bounty': {'_id': 'video1', 'version': 1,
                              'title': 'Produce a video',
                              'verification': 'Deliver final MP4.'},
                   'bid': {'status': 'active'}}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'status': 'withdrawn'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'infer') as infer:
            gambit.reconcile_active_bids(opened, state)
        infer.assert_not_called()
        self.assertEqual(state['bounties']['video1']['action'],
                         'unsupported_bid_withdrawn')
        self.assertEqual(len([c for c in api.call_args_list
                              if len(c.args) > 1 and c.args[1] == 'POST']), 1)

    def test_bid_disappearing_from_open_list_is_still_reconciled(self):
        state = {'bounties': {'missing1': {'version': 4, 'action': 'bid_active'}},
                 'recent_outcomes': [], 'events_cursor': None,
                 'last_check': datetime.now(timezone.utc).isoformat()}
        self.assertTrue(gambit.bid_reconciliation_due(state, []))
        details = {'bounty': {'_id': 'missing1', 'version': 4},
                   'bid': {'status': 'missing'}}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'infer') as infer:
            gambit.reconcile_active_bids([], state)
        self.assertEqual([c.args[0] for c in api.call_args_list], [
            '/v1/agent/bids?limit=100', '/v1/agent/bounties/missing1'])
        infer.assert_not_called()
        self.assertEqual(state['bounties']['missing1']['action'], 'bid_missing')

    def test_bid_detail_failure_does_not_falsely_close_offer(self):
        state = {'bounties': {'b1': {'version': 2, 'action': 'bid_active'}},
                 'recent_outcomes': []}
        with patch.object(gambit, 'api', side_effect=RuntimeError('bounty_api_UNAVAILABLE')):
            with self.assertRaisesRegex(RuntimeError, 'UNAVAILABLE'):
                gambit.reconcile_active_bids([], state)
        self.assertEqual(state['bounties']['b1']['action'], 'bid_active')

    def test_changed_bid_is_left_to_normal_processing(self):
        state = {'bounties': {'b1': {'version': 2, 'action': 'bid_active'}},
                 'recent_outcomes': []}
        with patch.object(gambit, 'api') as api, \
             patch.object(gambit, 'platform_active_bid_ids', return_value=set()):
            gambit.reconcile_active_bids([{'_id': 'b1', 'version': 3}], state,
                                         skip_ids={'b1'})
        api.assert_not_called()

    def test_account_wide_bid_list_recovers_orphan_active_offer(self):
        state = {'bounties': {}, 'recent_outcomes': []}
        details = {'bounty': {'_id': 'lost1', 'version': 2,
                              'title': 'Produce a video',
                              'verification': 'Deliver final MP4.'},
                   'bid': {'status': 'active'}}
        def request(path, method='GET', payload=None):
            if path.startswith('/v1/agent/bids'):
                return {'bids': [{'bounty_id': 'lost1', 'status': 'active'}],
                        'has_more': False}
            return details if method == 'GET' else {'status': 'withdrawn'}
        with patch.object(gambit, 'api', side_effect=request) as api:
            gambit.reconcile_active_bids([], state)
        self.assertEqual(state['bounties']['lost1']['action'], 'unsupported_bid_withdrawn')
        self.assertTrue(any(c.args[0].endswith('/bid/withdraw')
                            for c in api.call_args_list))

    def test_bid_sent_is_reconciled_and_recovers_as_active(self):
        state = {'bounties': {'b1': {'action': 'bid_sent', 'version': 2}},
                 'recent_outcomes': []}
        details = {'bounty': {'_id': 'b1', 'version': 2,
                              'title': 'Summarize a CSV'},
                   'bid': {'status': 'active'}}
        with patch.object(gambit, 'api', return_value=details):
            gambit.reconcile_active_bids([], state)
        self.assertEqual(state['bounties']['b1']['action'], 'bid_active')

    def test_clarification_timeout_releases_stalled_claim(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        entry = {'action': 'awaiting_info', 'claim_id': 'c1',
                 'last_message': 'none', 'payout_cents': 2000, 'at': past}
        self.assertTrue(gambit.clarification_expired(entry))
        state = {'bounties': {'b1': entry}, 'recent_outcomes': []}
        details = {'bounty': {'_id': 'b1', 'status': 'claimed'},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce') as produce:
            gambit.handle_hired({'_id': 'b1'}, state)
        produce.assert_not_called()
        self.assertEqual(state['bounties']['b1']['action'], 'unclaimed_unfulfillable')
        self.assertTrue(any(c.args[0].endswith('/unclaim')
                            for c in api.call_args_list))

    def test_failed_release_message_does_not_strand_buyers_funds(self):
        state = {'bounties': {}, 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            if path.endswith('/messages'):
                raise RuntimeError('bounty_api_UNAVAILABLE')
            return {'released': True}
        with patch.object(gambit, 'api', side_effect=request) as api:
            gambit.unclaim_unfulfillable('b1', 'c1', state, 'cannot_deliver', 'Sorry')
        self.assertEqual(state['bounties']['b1']['action'], 'unclaimed_unfulfillable')
        self.assertEqual(api.call_args_list[-1].args[0], '/v1/agent/bounties/b1/unclaim')

    def test_unchanged_bid_reconciliation_does_not_boot_model(self):
        state = {'schema': 1,
                 'bounties': {'b1': {'version': 1, 'action': 'bid_active'}},
                 'events_cursor': None, 'last_check': datetime.now(timezone.utc).isoformat(),
                 'recent_outcomes': []}
        opened = [{'_id': 'b1', 'version': 1}]
        result = io.StringIO()
        with patch.object(gambit, 'load_state', return_value=state), \
             patch.object(gambit, 'scan', return_value=(opened, [], [], None, [], [])), \
             patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), redirect_stdout(result):
            gambit.run('probe')
        outcome = json.loads(result.getvalue())
        self.assertTrue(outcome['needs_run'])
        self.assertFalse(outcome['needs_model'])

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

    def test_passed_market_listing_leaves_a_sanitized_learning_receipt(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 3,
                              'title': 'Research a local market', 'flow': 'bid'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'pass', 'reason': 'Private buyer data goes here',
                 '_research_status': 'partial', '_pages_read': 2}):
            gambit.handle_open({'_id': 'b1'}, state)
        self.assertEqual(state['recent_outcomes'][-1], {
            'at': state['recent_outcomes'][-1]['at'],
            'action': 'pass', 'bounty_id': 'b1', 'version': 3,
            'research_status': 'partial', 'pages_read': 2})
        self.assertNotIn('Private buyer data', str(state['recent_outcomes']))

    def test_bid_can_show_real_source_without_claiming_reputation(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open',
                              'version': 2, 'flow': 'bid'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        calls = []
        def request(path, method='GET', payload=None):
            if method == 'POST':
                calls.append(payload)
            return details
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'bid', 'payout_cents': 2500,
                 'note': 'I will supply a sourced company directory.',
                 'sample_source_url': 'https://example.com/about'}):
            gambit.handle_open({'_id': 'b1'}, state)
        self.assertIn('https://example.com/about', calls[0]['note'])

    def test_secret_not_persisted_in_receipt(self):
        state = {'recent_outcomes': []}
        gambit.note(state, 'bid_sent', 'b1', payout_cents=500)
        self.assertNotIn('api_key', str(state))

    def test_attachment_redirect_drops_authorization_and_requires_https(self):
        from urllib.request import Request
        original = Request('https://api.trybounty.ai/v1/agent/attachments/a1',
                           headers={'Authorization': 'Bearer private-test-secret'})
        redirect = gambit.AttachmentRedirect()
        safe = redirect.redirect_request(original, None, 302, 'Found', {},
                                         'https://storage.example.invalid/download')
        self.assertFalse(safe.has_header('Authorization'))
        with self.assertRaises(RuntimeError):
            redirect.redirect_request(original, None, 302, 'Found', {},
                                      'http://127.0.0.1/private')

    def test_text_attachments_are_supplied_to_bidding_model(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 2, 'flow': 'bid'},
                   'attachments': [{'id': 'a1', 'filename': 'numbers.csv',
                                    'content_type': 'text/csv', 'size': 15}]}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'read_text_attachment', return_value='month,sales\nJan,20'), \
             patch.object(gambit, 'assess', return_value={'decision': 'pass'}) as assess:
            gambit.handle_open({'_id': 'b1'}, state)
        source = assess.call_args.args[2][0]
        self.assertEqual(source['filename'], 'numbers.csv')
        self.assertIn('Jan,20', source['text'])

    def test_unreadable_attachment_asks_for_data_without_bidding(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 2, 'flow': 'bid'},
                   'attachments': [{'id': 'a1', 'filename': 'recording.mp4',
                                    'content_type': 'video/mp4', 'size': 35}]}
        state = {'bounties': {}, 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'msg1'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'assess') as assess:
            gambit.handle_open({'_id': 'b1'}, state)
        assess.assert_not_called()
        posts = [c.args[0] for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(posts, ['/v1/agent/bounties/b1/messages'])

    def test_hired_text_attachment_passes_to_producer_and_review(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'},
                   'attachments': [{'id': 'a1', 'filename': 'request.md',
                                    'content_type': 'text/markdown', 'size': 12}]}
        state = {'bounties': {}, 'recent_outcomes': []}
        ready = {'status': 'ready', 'deliverable_title': 'Answer',
                 'deliverable_text': 'Answer based on the file'}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'submission': {'id': 'sub1'}}
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'read_text_attachment', return_value='Source file data'), \
             patch.object(gambit, 'produce', return_value=ready) as produce, \
             patch.object(gambit, 'review', return_value={'approved': True}) as review:
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(produce.call_args.kwargs['sources'][0]['text'], 'Source file data')
        self.assertEqual(review.call_args.kwargs['sources'][0]['text'], 'Source file data')
        self.assertNotIn('Source file data', str(state))
        self.assertEqual(state['bounties']['b1']['submission_id'], 'sub1')

    def test_buyer_can_paste_data_for_unsupported_attachment(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'},
                   'attachments': [{'id': 'a1', 'filename': 'photo.png',
                                    'content_type': 'image/png', 'size': 5000}]}
        state = {'bounties': {'b1': {'action': 'awaiting_info', 'claim_id': 'c1',
                                    'last_message': 'none'}}, 'recent_outcomes': []}
        reply = {'_id': 'm1', 'author_type': 'user',
                 'content': {'text': 'The picture says the total is 45.'}}
        result = {'status': 'need_information', 'question': 'Which time period does 45 cover?'}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'm2'}
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'messages_for', return_value=[reply]), \
             patch.object(gambit, 'produce', return_value=result) as produce:
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(produce.call_count, 1)
        self.assertEqual(state['bounties']['b1']['action'], 'awaiting_info')
        self.assertEqual(state['bounties']['b1']['last_message'], 'm1')

    def test_research_planner_passes_external_evidence_to_bid_decision(self):
        details = {'bounty': {'title': 'List local suppliers', 'description': 'Find public sources'}}
        evidence = {'status': 'available', 'results': [
            {'url': 'https://example.com', 'title': 'Supplier',
             'evidence_level': 'page_read', 'page_text': 'Supplier details'}]}
        plan = {'requires_web': True, 'queries': ['suppliers town'], 'reason': 'Research'}
        decision = {'decision': 'bid', 'reason': 'I can source a few suppliers',
                    'payout_cents': 1200, 'note': 'I will provide a sourced table.',
                    'question': '', 'sample_source_url': 'https://example.com'}
        with patch.object(gambit, 'infer', side_effect=[plan, decision, decision]) as infer, \
             patch('tools.gambit_research.search_public_web', return_value=evidence) as search:
            result = gambit.assess(details)
        self.assertEqual(result['decision'], 'bid')
        self.assertEqual(result['sample_source_url'], 'https://example.com')
        self.assertIn('Supplier details', str(infer.call_args_list[1].args[2]['web_evidence']))
        search.assert_called_once_with(['suppliers town'])

    def test_unread_web_link_cannot_be_passed_as_bid_proof(self):
        details = {'bounty': {'title': 'List suppliers'}}
        plan = {'requires_web': True, 'queries': ['suppliers'], 'reason': 'Need web'}
        decision = {'decision': 'bid', 'payout_cents': 2000, 'note': 'Sourced list',
                    'sample_source_url': 'https://made-up.invalid/proof'}
        evidence = {'status': 'available', 'results': [
            {'url': 'https://example.com', 'evidence_level': 'page_read',
             'page_text': 'Verified business info'}]}
        with patch.object(gambit, 'infer', side_effect=[plan, decision, decision]), \
             patch('tools.gambit_research.search_public_web', return_value=evidence):
            result = gambit.assess(details)
        self.assertEqual(result['sample_source_url'], '')

    def test_unavailable_research_never_becomes_an_unsourced_bid(self):
        details = {'bounty': {'title': 'Find public prospects'}}
        plan = {'requires_web': True, 'queries': ['prospects'], 'reason': 'Need lookup'}
        proposal = {'decision': 'bid', 'reason': 'I can do it', 'payout_cents': 1200,
                    'note': 'A bid', 'question': ''}
        with patch.object(gambit, 'infer', side_effect=[plan, proposal]), \
             patch('tools.gambit_research.search_public_web', return_value={
                 'status': 'unavailable', 'results': []}):
            result = gambit.assess(details)
        self.assertEqual(result['decision'], 'pass')
        self.assertTrue(result['_retry_search'])

    def test_search_snippets_without_readable_pages_cannot_support_bid(self):
        details = {'bounty': {'title': 'Research firms'}}
        plan = {'requires_web': True, 'queries': ['firms'], 'reason': 'Need sources'}
        with patch.object(gambit, 'infer', side_effect=[plan, {
                'decision': 'bid', 'reason': 'Can source', 'payout_cents': 1500,
                'note': 'Deliver a source-linked report.', 'question': ''}]), \
             patch('tools.gambit_research.search_public_web', return_value={
                 'status': 'partial', 'results': [{
                     'url': 'https://example.com', 'evidence_level': 'search_snippet'}]}):
            result = gambit.assess(details)
        self.assertEqual(result['decision'], 'pass')
        self.assertTrue(result['_retry_search'])

    def test_temporary_search_outage_retries_only_when_due(self):
        now = datetime.now(timezone.utc)
        entry = {'action': 'retry_search',
                 'retry_at': (now + timedelta(minutes=10)).isoformat()}
        self.assertFalse(gambit.retry_due(entry, now))
        entry['retry_at'] = (now - timedelta(minutes=1)).isoformat()
        self.assertTrue(gambit.retry_due(entry, now))
        self.assertFalse(gambit.retry_due({'action': 'pass'}, now))

    def test_search_retry_remains_open_for_same_version(self):
        details = {'bounty': {'_id': 'b1', 'status': 'open', 'version': 3, 'flow': 'bid'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'pass', '_retry_search': True}):
            gambit.handle_open({'_id': 'b1'}, state)
        record = state['bounties']['b1']
        self.assertEqual(record['action'], 'retry_search')
        self.assertEqual(record['retry_count'], 1)
        self.assertTrue(gambit.retry_due(record, datetime.now(timezone.utc) +
                                         timedelta(minutes=11)))

    def test_csv_sanitization_and_header_validation(self):
        csv_data = gambit.prepare_csv('name,source\nAcme,https://example.com\n=2+3,source')
        self.assertIn(b"'=2+3", csv_data)
        with self.assertRaises(ValueError):
            gambit.prepare_csv('name,source\nAcme')

    def test_only_buyer_specified_list_size_becomes_a_hard_requirement(self):
        self.assertEqual(gambit.requested_list_size({
            'bounty': {'title': 'Find at least 20 qualified leads in retail'}}), 20)
        self.assertIsNone(gambit.requested_list_size({
            'bounty': {'title': 'Research local competitors and summarize trends'}}))

    def test_short_csv_gets_revised_before_any_paid_submission(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed',
                              'title': 'Find 3 qualified leads', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        short = {'status': 'ready', 'deliverable_title': 'Leads',
                 'deliverable_format': 'csv', 'deliverable_text':
                     'company,source\nA,https://example.com'}
        complete = dict(short, deliverable_text=(
            'company,source\nA,https://example.com\n'
            'B,https://example.org\nC,https://example.net'))
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value={
                'submission': {'id': 'sub1'}, **details}), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', side_effect=[short, complete]) as produce, \
             patch.object(gambit, 'review', return_value={'approved': True}) as review, \
             patch.object(gambit, 'upload_csv', return_value='attachment1'):
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(produce.call_count, 2)
        self.assertIn('3 records', produce.call_args.args[3])
        review.assert_called_once()
        self.assertEqual(state['bounties']['b1']['action'], 'submitted')

    def test_signed_csv_upload_never_sends_api_key_to_storage(self):
        state = {'bounties': {'b1': {'action': 'hired', 'payout_cents': 1200}}}
        upload = {'attachment_id': 'att_01',
                  'upload_url': 'https://storage.example.invalid/upload',
                  'headers': {'content-type': 'text/csv',
                              'content-disposition': 'attachment; filename="result.csv"'}}
        class Reply:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class Opener:
            def open(self, request, timeout=60):
                self.request = request
                return Reply()
        opener = Opener()
        with patch.object(gambit, 'api', side_effect=[upload, {'status': 'ready'}]) as api, \
             patch.object(gambit, 'save_state'), \
             patch.object(gambit.urllib.request, 'build_opener', return_value=opener):
            attachment_id = gambit.upload_csv('b1', b'name\nAcme', state, 'claim1', 'hash1')
        self.assertEqual(attachment_id, 'att_01')
        self.assertFalse(opener.request.has_header('Authorization'))
        self.assertEqual(state['bounties']['b1']['action'], 'upload_pending')
        self.assertIn('/complete', api.call_args.args[0])

    def test_untrusted_upload_url_rejected_before_file_transfer(self):
        state = {'bounties': {'b1': {'action': 'hired', 'payout_cents': 1200}}}
        upload = {'attachment_id': 'att1', 'upload_url': 'http://127.0.0.1:11434/upload',
                  'headers': {'content-type': 'text/csv'}}
        with patch.object(gambit, 'api', return_value=upload), \
             patch.object(gambit.urllib.request, 'build_opener') as opener:
            with self.assertRaises(ValueError):
                gambit.upload_csv('b1', b'x\n1', state, 'c1', 'hash')
        opener.assert_not_called()

    def test_invalid_csv_gets_revision_before_submission(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        bad = {'status': 'ready', 'deliverable_title': 'Leads',
               'deliverable_format': 'csv', 'deliverable_text': 'company,source\nMissing link'}
        good = {'status': 'ready', 'deliverable_title': 'Leads',
                'deliverable_format': 'csv',
                'deliverable_text': 'company,source\nAcme,https://example.com'}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', side_effect=[bad, good]) as produce, \
             patch.object(gambit, 'review', return_value={'approved': True}), \
             patch.object(gambit, 'upload_csv', return_value='a1'):
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(produce.call_count, 2)
        self.assertIn('CSV needs a header', produce.call_args.args[3])

    def test_hired_csv_result_submits_actual_file_deliverable(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        ready = {'status': 'ready', 'deliverable_title': 'Supplier list',
                 'deliverable_text': 'company,source\nExample,https://example.com',
                 'deliverable_format': 'csv'}
        state = {'bounties': {}, 'recent_outcomes': []}
        writes = []
        def request(path, method='GET', payload=None):
            if method == 'POST':
                writes.append((path, payload))
                return {'submission': {'id': 's1'}}
            return details
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=ready), \
             patch.object(gambit, 'review', return_value={'approved': True}), \
             patch.object(gambit, 'upload_csv', return_value='attachment1') as upload:
            gambit.handle_hired({'_id': 'b1'}, state)
        upload.assert_called_once()
        self.assertEqual(writes[0][1]['deliverables'][0]['type'], 'file')
        self.assertEqual(writes[0][1]['deliverables'][0]['data']['attachment_id'],
                         'attachment1')
        self.assertEqual(state['bounties']['b1']['submission_id'], 's1')

    @unittest.skipUnless(find_spec('openpyxl'), 'openpyxl installed when work is dispatched')
    def test_hired_excel_result_submits_real_xlsx_attachment(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed',
                              'title': 'Find 2 business leads', 'payout_cents': 2800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        result = {'status': 'ready', 'deliverable_title': 'Lead workbook',
                  'deliverable_text': ('company,source\nAcme,https://example.com\n'
                                       'Bravo,https://example.org'),
                  'deliverable_format': 'xlsx'}
        state = {'bounties': {}, 'recent_outcomes': []}
        written = []
        def request(path, method='GET', payload=None):
            if path.endswith('/submissions'):
                written.append(payload)
                return {'submission_id': 'submitted-xlsx'}
            return details
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=result), \
             patch.object(gambit, 'review', return_value={'approved': True}), \
             patch.object(gambit, 'upload_file', return_value='attachment-xlsx') as upload:
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(state['bounties']['b1']['submission_id'], 'submitted-xlsx')
        self.assertTrue(upload.call_args.args[1].startswith(b'PK'))
        self.assertEqual(upload.call_args.kwargs['filename'], 'gambit-research.xlsx')
        self.assertIn('spreadsheetml', upload.call_args.kwargs['content_type'])
        self.assertEqual(written[0]['deliverables'][0]['type'], 'file')

    def test_document_attachment_is_read_through_actual_bounty_file_path(self):
        import importlib.util
        if not importlib.util.find_spec('docx'):
            self.skipTest('python-docx not installed')
        from docx import Document
        document = Document()
        document.add_paragraph('Acme customer lead details')
        binary = io.BytesIO()
        document.save(binary)

        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, size):
                return binary.getvalue()[:size]

        class Opener:
            def open(self, request, timeout=35):
                self.request = request
                return Reply()

        opener = Opener()
        with patch.dict(os.environ, {'BOUNTY_AGENT_API_KEY': 'fake-secret'}), \
             patch.object(gambit.urllib.request, 'build_opener', return_value=opener):
            extracted = gambit.read_text_attachment({
                'id': 'attachment01', 'filename': 'brief.docx',
                'content_type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'size': len(binary.getvalue())})
        self.assertIn('Acme customer lead details', extracted)
        self.assertTrue(opener.request.has_header('Authorization'))

    def test_claim_public_question_uses_trybounty_comments_not_private_messages(self):
        details = {"bounty": {"_id": "c1", "status": "open", "version": 2,
                              "flow": "claim", "title": "Summarize an archive"}}
        state = {"bounties": {"c1": {"action": "evaluation_pending", "version": 2}},
                 "recent_outcomes": []}
        with patch.object(gambit, "api", side_effect=lambda path, method="GET", payload=None:
                          details if method == "GET" else {"comment_id": "com1"}) as api, \
             patch.object(gambit, "assess", return_value={
                 "decision": "ask", "question": "Which reporting period?"}):
            gambit.handle_open({"_id": "c1"}, state)
        posts = [call for call in api.call_args_list
                 if len(call.args) > 1 and call.args[1] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].args[0], "/v1/agent/bounties/c1/comments")
        self.assertEqual(posts[0].args[2]["body"], "Which reporting period?")
        self.assertEqual(state["bounties"]["c1"]["action"], "asked")

    def test_public_owner_reply_is_passed_into_claim_assessment(self):
        details = {"bounty": {"_id": "c1", "status": "open", "version": 2,
                              "flow": "claim", "title": "Summarize an archive"}}
        event = {"id": "e23", "type": "discussion.user_replied",
                 "data": {"bounty_id": "c1", "comment_id": "comment23",
                          "comment": {"body": "Use 2026 figures.",
                                      "created_at": 123}}}
        state = {"bounties": {"c1": {"action": "asked", "version": 2}},
                 "recent_outcomes": []}
        with patch.object(gambit, "api", return_value=details), \
             patch.object(gambit, "CURRENT_EVENTS", [event]), \
             patch.object(gambit, "assess", return_value={"decision": "pass"}) as assess:
            gambit.handle_open({"_id": "c1"}, state)
        self.assertIn("Use 2026 figures.", str(assess.call_args.args[1]))
        self.assertEqual(state["bounties"]["c1"]["action"], "pass")

    def test_worker_event_dispatch_includes_public_claim_replies(self):
        state = {"bounties": {"c1": {"action": "asked", "version": 2}},
                 "events_cursor": None}
        event = {"type": "discussion.user_replied",
                 "data": {"bounty_id": "c1"}}
        with patch.object(gambit, "pages", side_effect=[
             [{"_id": "c1", "version": 2}], []]), \
             patch.object(gambit, "relevant_events", return_value=([event], "cursor")):
            *_, waiting, active = gambit.scan(state)
        self.assertEqual(waiting[0]["_id"], "c1")
        self.assertEqual(active, [])

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

    def test_declined_bid_new_offer_has_new_idempotency_identity(self):
        details = {"bounty": {"_id": "b1", "status": "open", "version": 3,
                              "flow": "bid", "title": "Analyze customer report"},
                   "bid": {"_id": "oldbid", "status": "declined", "payout_cents": 2400}}
        state = {"bounties": {"b1": {"action": "bid_sent", "version": 3}},
                 "recent_outcomes": []}
        def request(path, method="GET", payload=None):
            if method == "GET":
                return details
            return {"bid": {"status": "active"}}
        with patch.object(gambit, "api", side_effect=request) as request_api, \
             patch.object(gambit, "messages_for", return_value=[]), \
             patch.object(gambit, "assess", return_value={
                 "decision": "bid", "payout_cents": 2400, "note": "Revised scope"}):
            gambit.handle_open({"_id": "b1"}, state)
        bids = [call for call in request_api.call_args_list
                if len(call.args) >= 2 and call.args[1] == "POST"
                and call.args[0].endswith("/bid")]
        self.assertEqual(len(bids), 1)
        sent = bids[0].args[2]
        self.assertEqual(sent["idempotency_key"],
                         gambit.key("bid", "b1", 3, 2400, "oldbid"))
        self.assertNotEqual(sent["idempotency_key"],
                            gambit.key("bid", "b1", 3, 2400, ""))
        self.assertEqual(state["bounties"]["b1"]["action"], "bid_active")

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

    def test_quality_revision_that_needs_data_asks_buyer_once(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        bad = {'status': 'ready', 'deliverable_title': 'Report', 'deliverable_text': 'Incomplete'}
        need_data = {'status': 'need_information', 'question': 'Please provide the missing totals.'}
        state = {'bounties': {}, 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'msg1'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', side_effect=[bad, need_data]), \
             patch.object(gambit, 'review', return_value={
                 'approved': False, 'reason': 'Missing actual numerical inputs'}):
            gambit.handle_hired({'_id': 'b1'}, state)
        writes = [c for c in api.call_args_list if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(writes), 1)
        self.assertIn('/messages', writes[0].args[0])
        self.assertEqual(state['bounties']['b1']['action'], 'awaiting_info')

    def test_quality_rejection_is_not_a_permanent_silent_stall(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        bad = {'status': 'ready', 'deliverable_title': 'Report',
               'deliverable_text': 'Still incomplete'}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=bad) as produce, \
             patch.object(gambit, 'review', return_value={
                 'approved': False, 'reason': 'Missing relevant sources'}):
            gambit.handle_hired({'_id': 'b1'}, state)
        entry = state['bounties']['b1']
        self.assertEqual(produce.call_count, 2)
        self.assertEqual(entry['action'], 'quality_rejected')
        self.assertEqual(entry['quality_retry_count'], 1)
        self.assertFalse(gambit.quality_retry_due(entry))
        self.assertTrue(gambit.quality_retry_due(
            entry, datetime.now(timezone.utc) + timedelta(minutes=20)))

        with patch.object(gambit, 'pages', side_effect=[[], [{'_id': 'b1', 'status': 'claimed'}]]), \
             patch.object(gambit, 'relevant_events', return_value=([], None)):
            _, _, _, _, _, not_yet = gambit.scan(state)
        self.assertFalse(not_yet)
        entry['retry_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with patch.object(gambit, 'pages', side_effect=[[], [{'_id': 'b1', 'status': 'claimed'}]]), \
             patch.object(gambit, 'relevant_events', return_value=([], None)):
            _, _, _, _, _, due = gambit.scan(state)
        self.assertEqual([item['_id'] for item in due], ['b1'])

    def test_quality_retry_uses_previous_issue_to_correct_delivery(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        state = {'bounties': {'b1': {'action': 'quality_rejected',
                                    'claim_id': 'c1', 'quality_retry_count': 1,
                                    'quality_issue': 'citations'}}, 'recent_outcomes': []}
        good = {'status': 'ready', 'deliverable_title': 'Corrected report',
                'deliverable_text': 'Corrected with actual sources'}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'submission_id': 's1'}
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=good) as produce, \
             patch.object(gambit, 'review', return_value={'approved': True, 'reason': 'correct'}):
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertIn('Only cite pages actually read', produce.call_args.args[3])
        self.assertEqual(state['bounties']['b1']['action'], 'submitted')

    def test_undeliverable_claim_requests_buyer_input_without_operator_stall(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 2000},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        state = {'bounties': {}, 'recent_outcomes': []}
        cannot = {'status': 'cannot_deliver', 'question': '',
                  'reason': 'source material unavailable'}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'request1'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=cannot) as produce:
            gambit.handle_hired({'_id': 'b1'}, state)
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(produce.call_count, 1)
        self.assertEqual(state['bounties']['b1']['action'], 'awaiting_info')
        requests = [c for c in api.call_args_list
                    if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(requests), 1)
        self.assertIn('/messages', requests[0].args[0])

    def test_repeated_quality_failure_notifies_and_refunds_buyer(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed', 'payout_cents': 1800},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        state = {'bounties': {'b1': {'action': 'quality_rejected',
                                    'claim_id': 'c1', 'quality_retry_count': 2,
                                    'quality_issue': 'review'}}, 'recent_outcomes': []}
        bad = {'status': 'ready', 'deliverable_title': 'Incomplete',
               'deliverable_text': 'Cannot verify enough sources'}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'warning1'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=bad), \
             patch.object(gambit, 'review', return_value={
                 'approved': False, 'reason': 'Not source verified'}):
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(state['bounties']['b1']['action'], 'unclaimed_unfulfillable')
        posts = [c for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertIn('/messages', posts[0].args[0])
        self.assertIn('/unclaim', posts[1].args[0])

    def test_live_market_workload_excludes_old_withdrawn_offer_from_fresh_work(self):
        opened = [{"_id": "old", "version": 1}, {"_id": "new", "version": 2},
                  {"_id": "bid", "version": 1}]
        state = {"bounties": {
            "old": {"action": "bid_missing", "version": 1},
            "new": {"action": "evaluation_pending", "version": 2},
            "bid": {"action": "bid_active", "version": 1}}}
        counts = gambit.market_workload(state, opened, [opened[1]])
        self.assertEqual(counts["released_open_total"], 3)
        self.assertEqual(counts["new_or_revised"], 1)
        self.assertEqual(counts["existing_offers"], 1)
        self.assertEqual(counts["unchanged_history_ignored"], 1)

    def test_updated_old_listing_becomes_live_work_again(self):
        opened = [{"_id": "old", "version": 2}]
        state = {"bounties": {"old": {"action": "pass_unsupported", "version": 1}}}
        counts = gambit.market_workload(state, opened, opened)
        self.assertEqual(counts["new_or_revised"], 1)
        self.assertEqual(counts["unchanged_history_ignored"], 0)


if __name__ == '__main__':
    unittest.main()
