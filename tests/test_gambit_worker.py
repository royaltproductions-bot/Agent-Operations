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
from tools import gambit_app_builder


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

    @patch.dict(os.environ, {"GAMBIT_INFERENCE_PROVIDER": "ollama"})
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
                              "budget_cents": 2500, "flow": "bid"}}
        plan = {"requires_web": False, "queries": []}
        negative = {"decision": "pass", "reason": "Hard"}
        reconsidered = {"decision": "bid", "payout_cents": 2000, "note": "Report"}
        with patch.object(gambit, "infer", side_effect=[plan, negative]), \
             patch.object(gambit, "cross_check_decision",
                          return_value=reconsidered) as repeated:
            result = gambit.assess(details)
        self.assertEqual(result["decision"], "bid")
        repeated.assert_called_once()


    def test_small_pass_uses_one_decision_without_second_opinion(self):
        details = {"bounty": {"_id": "small", "version": 1, "status": "open",
                              "budget_cents": 500, "title": "Short report",
                              "description": "Summarize public data", "flow": "bid"}}
        plan = {"requires_web": False, "queries": []}
        negative = {"decision": "pass", "reason": "No verified source"}
        with patch.object(gambit, "infer", side_effect=[plan, negative]) as inference, \
             patch.object(gambit, "cross_check_decision") as repeated:
            result = gambit.assess(details)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(inference.call_count, 2)  # free research plan + one decision
        repeated.assert_not_called()

    def test_25_dollar_pass_retains_second_opinion(self):
        details = {"bounty": {"_id": "medium", "version": 1, "status": "open",
                              "budget_cents": 2500, "title": "Short report",
                              "description": "Summarize public data", "flow": "bid"}}
        plan = {"requires_web": False, "queries": []}
        negative = {"decision": "pass", "reason": "No verified source"}
        with patch.object(gambit, "infer", side_effect=[plan, negative]), \
             patch.object(gambit, "cross_check_decision", return_value=negative) as repeated:
            gambit.assess(details)
        repeated.assert_called_once()

    def test_three_way_conflict_queues_a_new_evaluation(self):
        proposal = {'decision': 'bid', 'payout_cents': 1500}
        challenger = {'decision': 'pass'}
        with patch.object(gambit, 'infer', side_effect=[challenger, {'decision': 'ask'}]):
            result = gambit.cross_check_decision({'bounty': {}}, proposal, 5500)
        self.assertTrue(result['_retry_decision'])
        self.assertEqual(result['decision'], 'pass')

    def test_fulfillment_failures_remain_retryable_after_three_attempts(self):
        state = {'bounties': {'b1': {'action': 'hired', 'claim_id': 'c1'}},
                 'recent_outcomes': []}
        gambit.schedule_fulfillment_retry(state, 'b1', TimeoutError())
        self.assertEqual(state['bounties']['b1']['action'], 'retry_fulfillment')
        self.assertFalse(gambit.fulfillment_retry_due(state['bounties']['b1']))
        for _ in range(2):
            gambit.schedule_fulfillment_retry(state, 'b1', TimeoutError())
        self.assertEqual(state['bounties']['b1']['action'], 'retry_fulfillment')
        self.assertEqual(state['bounties']['b1']['retry_count'], 3)
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

    def test_legacy_terminal_retry_state_does_not_automatically_unclaim(self):
        details = {'bounty': {'_id': 'b1', 'status': 'claimed'},
                   'claim': {'status': 'active', 'claim_id': 'c1'}}
        state = {'bounties': {'b1': {'action': 'fulfillment_failed_final',
                                     'claim_id': 'c1'}}, 'recent_outcomes': []}
        with patch.object(gambit, 'api',
                          side_effect=lambda path, method='GET', payload=None:
                          details if method == 'GET' else {}) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value={
                 'status':'need_information', 'question':'Please provide missing input.'}):
            gambit.handle_hired({'_id': 'b1'}, state)
        self.assertEqual(state['bounties']['b1']['action'], 'awaiting_info')
        self.assertFalse(any(call.args[0].endswith('/unclaim')
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

    def test_social_platform_research_is_not_account_operation(self):
        research = [
            ('Run searches on LinkedIn for 50 decision makers',
             'Deliver sourced names and LinkedIn profile URLs as CSV.'),
            ('Research how competitors launch products on TikTok',
             'Deliver an analytical report on public marketing examples.'),
            ("We'll give you access to our analytics dashboard export",
             'Analyze our supplied CSV file and deliver a report.'),
        ]
        for title, verification in research:
            with self.subTest(title=title):
                self.assertIsNone(gambit.unsupported_deliverable({'bounty': {
                    'title': title, 'verification': verification}}))

    def test_account_policy_research_vs_real_platform_actions(self):
        # These are representative constructions, not decrypted historical
        # buyer briefs. The archived marketplace descriptions stay encrypted.
        eligible = [
            ("Schedule LinkedIn interviews and research prospects",
             "Deliver a research report with public LinkedIn URLs."),
            ("Manage a spreadsheet of Instagram post metrics",
             "Analyze an attached CSV and deliver a statistical report."),
            ("Publish a market research report on TikTok trends",
             "Provide a PDF of public examples, not a live TikTok post."),
            ("Post performance analysis for Facebook advertising",
             "Submit a sourced written overview of competitor activity."),
            ("Upload a TikTok analytics CSV for analysis",
             "Deliver a new XLSX report using the supplied file."),
            ("Summarize published Instagram posts",
             "Provide a report about already-published public posts."),
            ("Research how agencies schedule Facebook posts",
             "Deliver a documentation-only report with examples."),
        ]
        for title, verification in eligible:
            with self.subTest(title=title):
                self.assertIsNone(gambit.unsupported_deliverable({
                    "bounty": {"title": title, "verification": verification}}))
        requires_login = [
            ("Publish captions to Instagram", "Three posts live on Instagram."),
            ("Schedule Instagram posts", "Publish three posts for our page."),
            ("Manage Instagram business account", "Maintain our company feed."),
            ("Post updates on LinkedIn", "Posts must be live on LinkedIn."),
            ("Upload videos to TikTok", "Videos must appear on our account."),
            ("Manage Facebook ads", "Campaigns must be active."),
            ("Publish three posts to our social media accounts",
             "Send proof of publication."),
            ("Upload promotional videos to our Instagram business page",
             "Videos visible on the company page."),
            ("Schedule ads in Meta Ads Manager",
             "Ads appear in our account."),
            ("Log into our account dashboard", "Change our campaign settings."),
            ("Research how teams schedule Instagram posts, then publish posts to Instagram",
             "Deliver three finalized captions."),
            ("Research social campaign performance",
             "Provide evidence the posts are published on Instagram."),
        ]
        for title, verification in requires_login:
            with self.subTest(title=title):
                self.assertEqual("authenticated_account_operation",
                    gambit.unsupported_deliverable({
                        "bounty": {"title": title, "verification": verification}}))

    def test_recheck_historical_account_rejection_once_then_preserve_decision(self):
        from tools.gambit_early_scan import ACCOUNT_POLICY_RECHECK_VERSION
        bounty_id = "historical-reject"
        details = {"bounty": {"_id": bounty_id, "status": "open",
                               "version": 1, "flow": "bid",
                               "title": "Schedule LinkedIn research interviews",
                               "verification": "Deliver a sourced CSV report."}}
        state = {"bounties": {bounty_id: {
            "action": "pass_unsupported", "version": 1,
            "reason": "authenticated_account_operation"}},
            "recent_outcomes": []}
        with patch.object(gambit, "pages", side_effect=[
             [{"_id": bounty_id, "version": 1}], []]), patch.object(
             gambit, "relevant_events", return_value=([], None)):
            _, _, _, _, waiting, _ = gambit.scan(state)
        self.assertEqual([x["_id"] for x in waiting], [bounty_id])
        with patch.object(gambit, "api", return_value=details) as api, patch.object(
                gambit, "assess", return_value={"decision": "pass"}) as assess:
            gambit.handle_open(waiting[0], state)
        assess.assert_called_once()
        self.assertEqual(state["bounties"][bounty_id]["action"], "pass")
        self.assertFalse(any(len(c.args) > 1 and c.args[1] == "POST"
                             for c in api.call_args_list))
        with patch.object(gambit, "pages", side_effect=[
             [{"_id": bounty_id, "version": 1}], []]), patch.object(
             gambit, "relevant_events", return_value=([], None)):
            _, _, _, _, waiting, _ = gambit.scan(state)
        self.assertEqual(waiting, [])

    def test_recheck_still_restricted_account_job_is_not_requeued(self):
        from tools.gambit_early_scan import ACCOUNT_POLICY_RECHECK_VERSION
        bounty_id = "still-restricted"
        details = {"bounty": {"_id": bounty_id, "status": "open",
                               "version": 1, "flow": "bid",
                               "title": "Manage Instagram business account",
                               "verification": "Three posts live."}}
        state = {"bounties": {bounty_id: {
            "action": "pass_unsupported", "version": 1,
            "reason": "authenticated_account_operation"}}, "recent_outcomes": []}
        with patch.object(gambit, "api", return_value=details) as api, patch.object(
                gambit, "assess") as assess:
            gambit.handle_open({"_id": bounty_id}, state)
        assess.assert_not_called()
        self.assertEqual(state["bounties"][bounty_id]["action"], "pass_unsupported")
        self.assertEqual(state["bounties"][bounty_id]["account_policy_review_version"],
                         ACCOUNT_POLICY_RECHECK_VERSION)
        self.assertFalse(any(len(c.args) > 1 and c.args[1] == "POST"
                             for c in api.call_args_list))
        with patch.object(gambit, "pages", side_effect=[
             [{"_id": bounty_id, "version": 1}], []]), patch.object(
             gambit, "relevant_events", return_value=([], None)):
            _, _, _, _, waiting, _ = gambit.scan(state)
        self.assertEqual(waiting, [])

    def test_real_account_operations_remain_ineligible(self):
        account_actions = [
            ('Run paid ads on Facebook', 'Campaign must be live.'),
            ('Launch an ad campaign on TikTok', 'The ad must run.'),
            ('Sign in to our client CRM', 'Update customer records.'),
            ('Access a private dashboard', 'Operate the client account.'),
        ]
        for title, verification in account_actions:
            with self.subTest(title=title):
                self.assertEqual('authenticated_account_operation',
                    gambit.unsupported_deliverable({'bounty': {
                        'title': title, 'verification': verification}}))

    def test_regulated_filing_is_blocked_but_data_analysis_is_allowed(self):
        self.assertEqual('regulated_filing',
                         gambit.unsupported_deliverable({'bounty': {
            'title': 'File IRS forms for my business',
            'description': 'Submit the 2025 tax return.',
            'verification': 'Provide proof of official submission.'}}))
        self.assertIsNone(gambit.unsupported_deliverable({'bounty': {
            'title': 'Analyze expenses from a CSV',
            'verification': 'Summarize totals in a report.'}}))

    def test_live_app_scope_uses_real_app_build_resource_not_static_site(self):
        nonprofit={"bounty":{"title":"Research Nonprofit Fundraising Events and Contacts",
            "description":"Build a simple web app where users upload nonprofit domains "
                          "and the app searches public sources for events.",
            "verification":"Submit a CSV sample file and receive results from the app."}}
        prototype={"bounty":{"title":"Build an AI Business Opportunity Analysis Prototype",
            "description":"Deliver a standalone AI chat prototype using an AI model API key.",
            "verification":"The prototype includes a chat input and generated report."}}
        for details in (nonprofit,prototype):
            self.assertIsNone(gambit.unsupported_deliverable(details))
            with patch.object(gambit, "app_capable", return_value=False):
                result=gambit.assess(details)
                self.assertEqual(result["decision"],"pass")
                self.assertTrue(result["_retry_decision"])
                self.assertEqual(result["reason"],
                                 "app_build_resource_temporarily_unavailable")

    def test_functional_portal_routes_to_existing_app_builder_not_text(self):
        details = {'bounty': {'_id': 'portal1', 'status': 'claimed',
                              'title': 'Build a client portal',
                              'description': 'Implement a backend and working client dashboard.',
                              'verification': 'Deliver a tested Python source ZIP.'},
                   'claim': {'status': 'active', 'claim_id': 'claim-portal'}}
        self.assertTrue(gambit_app_builder.requested(details))
        self.assertIsNone(gambit.unsupported_deliverable(details))
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'app_capable', return_value=True), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit_app_builder, 'progress', return_value={
                 'status': 'building', 'stage': 'planned', 'completed': 0}) as progress, \
             patch.object(gambit, 'produce') as normal_produce:
            gambit.handle_hired({'_id': 'portal1'}, state)
        progress.assert_called_once()
        normal_produce.assert_not_called()
        self.assertFalse(any('/submissions' in c.args[0]
                             for c in api.call_args_list))
        self.assertIn('app_section_checkpointed',
                      [o['action'] for o in state['recent_outcomes']])

    def test_live_deployment_must_be_rejected_even_if_app_runner_offline(self):
        details = {'bounty': {
            'title': 'Build a client portal',
            'description': 'Develop a Python dashboard and deploy publicly.',
            'verification': 'Deliver publicly hosted working app URL.'}}
        self.assertTrue(gambit_app_builder.requested(details))
        self.assertEqual(gambit.unsupported_deliverable(details),
                         'software_deployment')
        with patch.object(gambit, 'app_capable', return_value=False):
            result = gambit.assess(details)
        self.assertEqual(result['reason'], 'unsupported_software_deployment')
        self.assertFalse(result.get('_retry_decision'))

    def test_buyer_defined_prospect_count_is_not_arbitrarily_rejected(self):
        for number in (10, 50, 500):
            details={"bounty":{
                "title":"Find research contacts",
                "description":"Research decision makers and their LinkedIn profiles.",
                "verification":f"Submit exactly {number} prospect records with "
                               "LinkedIn profile URLs and supporting sources."}}
            self.assertIsNone(gambit.unsupported_deliverable(details))

    def test_company_website_urls_do_not_trigger_static_site_requirements(self):
        details={"bounty":{"title":"Find 15 New York Seed-Stage Fintech Startups",
           "description":"Find 15 seed-stage fintech companies with company website URLs.",
           "verification":"15 rows with company name, website, founder and funding link."}}
        self.assertIsNone(gambit.unsupported_deliverable(details))

    def test_unsupported_presentation_and_deployment_are_blocked(self):
        self.assertIsNone(gambit.unsupported_deliverable({
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

    def test_research_title_cannot_hide_functional_app_requirements(self):
        request = {"bounty": {
            "title": "Research nonprofit fundraising events and contacts",
            "description": ("Build a web application that allows users to upload "
                            "a CSV of organization domains, performs automatic "
                            "internet searches and returns auction event dates."),
            "verification": ("Provide working app source code, setup instructions, "
                             "and results for a sample CSV.")}}
        self.assertTrue(gambit_app_builder.requested(request))
        self.assertIsNone(gambit.unsupported_deliverable(request))
        self.assertIsNone(gambit.unsupported_deliverable({"bounty": {
            "title": "Research nonprofits and summarize upcoming auctions",
            "description": "Research public nonprofit auction dates from supplied CSV.",
            "verification": "Deliver a sourced XLSX report, not a web application."}}))

    @patch.object(gambit, "website_capable", return_value=False)
    def test_static_site_source_cannot_be_bid_until_visual_qa_exists(self, _capable):
        self.assertEqual('website_visual_quality_unverified',
                         gambit.unsupported_deliverable({'bounty': {
            'title': 'Build a static HTML landing page',
            'verification': 'Deliver a ZIP of the standalone front-end source.'}}))
        self.assertIsNone(gambit.unsupported_deliverable({
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
        self.assertIsNone(gambit.unsupported_deliverable({'bounty': {
            'title': 'Create an executive report',
            'verification': 'Deliver a final PDF report and XLSX spreadsheet.'}}))
        self.assertIsNone(gambit.unsupported_deliverable({'bounty': {
            'title': 'Customer analysis',
            'verification': 'Deliver a final DOCX document and CSV file.'}}))
        self.assertIsNone(
            gambit.unsupported_deliverable({'bounty': {
                'title': 'Create an executive presentation',
                'verification': 'Deliver final PDF and PPTX files.'}}))

    def test_multifile_model_receives_real_formats_without_new_tools(self):
        details = {'bounty': {'title': 'Analyze quarterly metrics',
                              'verification': 'Deliver final PDF report and XLSX table.'}}
        response = {'status': 'ready', 'reason': '', 'question': '',
                    'deliverables': [{'format': 'pdf', 'title': 'Analysis',
                                      'text': '# Summary\nSales increased'},
                                     {'format': 'xlsx', 'title': 'Metrics',
                                      'text': 'month,sales\nOct,25'}]}
        with patch.object(gambit, 'research_for', return_value={'results': []}), \
             patch.object(gambit, 'infer', return_value=response) as infer:
            result = gambit.produce(details, [])
        self.assertIs(infer.call_args.args[0], gambit.MULTI_WORK_SCHEMA)
        self.assertIn('xlsx', infer.call_args.args[1])
        self.assertEqual(result['deliverables'], response['deliverables'])

    @unittest.skipUnless(find_spec('openpyxl') and find_spec('pypdf')
                         and find_spec('docx'), 'document adapters available')
    def test_multifile_adapters_generate_real_document_and_table(self):
        from pypdf import PdfReader
        from openpyxl import load_workbook
        from docx import Document
        details = {'bounty': {'title': 'Business analysis',
                              'verification': 'Deliver PDF, DOCX, CSV, and XLSX files.'}}
        work = {'deliverables': [
            {'format': 'xlsx', 'title': 'Workbook',
             'text': 'metric,value\nsales,34\norders,2'},
            {'format': 'docx', 'title': 'Document',
             'text': '# Analysis\nSales increased by 34 units.'},
            {'format': 'pdf', 'title': 'Report',
             'text': '# Executive analysis\nSales increased by 34 units.'},
            {'format': 'csv', 'title': 'Data',
             'text': 'metric,value\nsales,34\norders,2'}]}
        prepared = gambit.prepare_multi_deliverables(details, work)
        self.assertEqual([x['format'] for x in prepared],
                         ['csv', 'docx', 'pdf', 'xlsx'])
        by_format = {x['format']: x['data'] for x in prepared}
        self.assertTrue(by_format['csv'].startswith(b'metric,value'))
        self.assertIn('Sales increased', '\n'.join(
            p.text for p in Document(io.BytesIO(by_format['docx'])).paragraphs))
        self.assertIn('Sales increased', PdfReader(io.BytesIO(
            by_format['pdf'])).pages[0].extract_text())
        self.assertEqual(load_workbook(io.BytesIO(
            by_format['xlsx']), read_only=True).active['B2'].value, '34')

    def test_multifile_manifest_rejects_missing_duplicates_and_invalid_csv(self):
        details = {'bounty': {'title': 'Business analysis',
                              'verification': 'Deliver PDF report and XLSX table.'}}
        good_pdf = {'format': 'pdf', 'title': 'Report', 'text': 'Report text'}
        good_xlsx = {'format': 'xlsx', 'title': 'Table',
                     'text': 'metric,value\nsales,12'}
        for manifest in [[good_pdf], [good_pdf, good_pdf],
                         [good_pdf, {'format': 'csv', 'title': 'Data',
                                     'text': 'metric,value\nsales,12'}],
                         [good_pdf, {'format': 'xlsx', 'title': 'Broken table',
                                     'text': 'metric,value\nmissing'}]]:
            with self.subTest(manifest=manifest):
                with self.assertRaises(ValueError):
                    gambit.prepare_multi_deliverables(
                        details, {'deliverables': manifest})

    @unittest.skipUnless(find_spec('openpyxl') and find_spec('pypdf'),
                         'document adapters available')
    def test_hired_multifile_submits_every_format_after_validation(self):
        from pypdf import PdfReader
        from openpyxl import load_workbook
        details = {'bounty': {'_id': 'multi1', 'status': 'claimed',
                              'title': 'Analyze quarterly operations',
                              'verification': 'Deliver a PDF report and XLSX table.',
                              'payout_cents': 5500},
                   'claim': {'status': 'active', 'claim_id': 'c-multi'}}
        work = {'status': 'ready', 'reason': '', 'question': '',
                'deliverables': [
                    {'format': 'pdf', 'title': 'Written analysis',
                     'text': '# Quarterly operations\nSales totalled 25.'},
                    {'format': 'xlsx', 'title': 'Supporting figures',
                     'text': 'metric,value\nsales,25\ncustomers,5'}]}
        state = {'bounties': {}, 'recent_outcomes': []}
        uploaded, submissions = [], []
        def api(path, method='GET', payload=None):
            if path.endswith('/submissions'):
                submissions.append(payload)
                return {'submission_id': 's-multi'}
            return details
        def upload(bid, content, state, claim, digest, *, filename, content_type):
            uploaded.append((filename, content, digest))
            return 'att-' + filename
        with patch.object(gambit, 'api', side_effect=api), \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=work), \
             patch.object(gambit, 'review', return_value={'approved': True}) as review, \
             patch.object(gambit, 'upload_file', side_effect=upload):
            gambit.handle_hired({'_id': 'multi1'}, state)
        self.assertEqual(len(submissions), 1)
        self.assertEqual(len(uploaded), 2)
        self.assertEqual({x[0] for x in uploaded},
                         {'gambit-report.pdf', 'gambit-research.xlsx'})
        self.assertEqual([x['key'] for x in submissions[0]['deliverables']],
                         ['result_pdf', 'result_xlsx'])
        self.assertEqual(len({x['data']['attachment_id'] for x in
                              submissions[0]['deliverables']}), 2)
        self.assertIn('Sales totalled 25', PdfReader(io.BytesIO(
            next(x[1] for x in uploaded if x[0].endswith('.pdf'))))
            .pages[0].extract_text())
        self.assertEqual(load_workbook(io.BytesIO(next(
            x[1] for x in uploaded if x[0].endswith('.xlsx'))),
            read_only=True).active['B2'].value, '25')
        review.assert_called_once()
        self.assertEqual(state['bounties']['multi1']['action'], 'submitted')
        self.assertEqual(state['bounties']['multi1']['submission_id'], 's-multi')

    def test_hired_multifile_incomplete_result_does_not_upload_or_submit(self):
        details = {'bounty': {'_id': 'multi1', 'status': 'claimed',
                              'title': 'Quarterly analysis',
                              'verification': 'Deliver final PDF and XLSX files.',
                              'payout_cents': 4400},
                   'claim': {'status': 'active', 'claim_id': 'c-multi'}}
        bad = {'status': 'ready', 'reason': '', 'question': '',
               'deliverables': [{'format': 'pdf', 'title': 'Report',
                                 'text': 'Only one required file.'}]}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'produce', return_value=bad), \
             patch.object(gambit, 'upload_file') as upload:
            gambit.handle_hired({'_id': 'multi1'}, state)
        upload.assert_not_called()
        self.assertFalse(any('/submissions' in c.args[0] for c in api.call_args_list))
        self.assertEqual(state['bounties']['multi1']['action'], 'quality_rejected')

    def test_multifile_upload_reuses_checkpoint_after_partial_failure(self):
        bid = 'multi1'
        state = {'bounties': {bid: {'action': 'hired', 'claim_id': 'c-multi'}},
                 'recent_outcomes': []}
        replies = iter(['att-pdf', 'att-xlsx'])
        def api(path, method='GET', payload=None):
            if path.endswith('/attachments'):
                return {'attachment_id': next(replies),
                        'upload_url': 'https://uploads.example.org/signed',
                        'headers': {'Content-Type': 'application/octet-stream'}}
            return {}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Opener:
            def open(self, *args, **kwargs): return Response()
        with patch.object(gambit, 'api', side_effect=api) as api_mock, \
             patch.object(gambit, 'save_state'), \
             patch('urllib.request.build_opener', return_value=Opener()):
            gambit.upload_file(bid, b'pdfbinary', state, 'c-multi', 'digest-pdf',
                               filename='gambit-report.pdf', content_type='application/pdf')
            gambit.upload_file(bid, b'xlsxfile', state, 'c-multi', 'digest-xlsx',
                               filename='gambit-research.xlsx',
                               content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            gambit.upload_file(bid, b'pdfbinary', state, 'c-multi', 'digest-pdf',
                               filename='gambit-report.pdf', content_type='application/pdf')
        self.assertEqual(state['bounties'][bid]['uploaded_files'],
                         {'digest-pdf': 'att-pdf', 'digest-xlsx': 'att-xlsx'})
        created = [c for c in api_mock.call_args_list if
                   c.args[0].endswith('/attachments')]
        self.assertEqual(len(created), 2)

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

    def test_existing_unsupported_video_bid_is_preserved_for_review(self):
        details = {'bounty': {'_id': 'video1', 'status': 'open', 'version': 1,
                              'title': 'Produce a product video',
                              'verification': 'Deliver final MP4 video.'},
                   'bid': {'status': 'active', 'bounty_version': 1}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'respond_to_buyer') as reply:
            gambit.handle_open({'_id': 'video1'}, state)
        self.assertFalse(any(len(c.args) > 1 and c.args[1] == 'POST'
                             for c in api.call_args_list))
        reply.assert_not_called()
        self.assertEqual(state['bounties']['video1']['action'], 'bid_active')
        self.assertEqual(state['bounties']['video1']['capability_review_reason'],
                         'video_production')

    def test_unsupported_video_buyer_reply_cannot_promise_mp4(self):
        details = {'bounty': {'title': 'Create an animated demo video',
                              'verification': 'Provide final video file (MP4).'}}
        with patch.object(gambit, 'infer') as infer:
            result = gambit.respond_to_buyer(details, [])
        self.assertIn('full requested deliverable', result['reply_text'])
        self.assertIn("can't yet confirm", result['reply_text'])
        self.assertNotIn('I can provide an MP4', result['reply_text'])
        infer.assert_not_called()

    def test_flagged_active_bid_answers_buyer_once_without_withdrawing(self):
        details = {'bounty': {'_id': 'video1', 'status': 'open',
                              'version': 2, 'flow': 'bid',
                              'title': 'Produce a product video',
                              'verification': 'Deliver final MP4 video.'},
                   'bid': {'status': 'active', 'bounty_version': 1}}
        latest = {'_id': 'buyer-msg-8', 'author_type': 'user',
                  'content': {'type': 'text',
                              'text': 'Can you confirm the video format?'}}
        event = {'type': 'work.message.created',
                 'data': {'bounty_id': 'video1'}}
        state = {'bounties': {'video1': {'action': 'bid_active',
                                        'last_owner_reply_id': 'buyer-msg-7'}},
                 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'agent-reply'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'CURRENT_EVENTS', [event]), \
             patch.object(gambit, 'messages_for', return_value=[latest]), \
             patch.object(gambit, 'infer') as infer:
            gambit.handle_open({'_id': 'video1'}, state)
            gambit.handle_open({'_id': 'video1'}, state)
        sent = [c for c in api.call_args_list
                if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].args[0].endswith('/messages'))
        self.assertIn("can't yet confirm",
                      sent[0].args[2]['content']['text'])
        self.assertFalse(any('/bid' in c.args[0] and c.args[1] == 'POST'
                             for c in sent))
        infer.assert_not_called()
        self.assertEqual(state['bounties']['video1']['action'], 'bid_active')
        self.assertEqual(state['bounties']['video1']['capability_review_reason'],
                         'video_production')
        self.assertEqual(state['bounties']['video1']['last_owner_reply_id'],
                         'buyer-msg-8')
        self.assertIn('agent_replied_to_buyer',
                      [r['action'] for r in state['recent_outcomes']])

    def test_flagged_bid_without_buyer_message_stays_silent(self):
        details = {'bounty': {'_id': 'video1', 'status': 'open',
                              'version': 2, 'flow': 'bid',
                              'title': 'Produce a product video',
                              'verification': 'Deliver final MP4 video.'},
                   'bid': {'status': 'active', 'bounty_version': 2}}
        state = {'bounties': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'CURRENT_EVENTS', []), \
             patch.object(gambit, 'messages_for') as messages:
            gambit.handle_open({'_id': 'video1'}, state)
        messages.assert_not_called()
        self.assertFalse(any(len(c.args)>1 and c.args[1]=='POST'
                             for c in api.call_args_list))

    def test_prebid_buyer_identity_is_answered_before_qualification(self):
        # Synthetic question representing a customer identity inquiry, not a
        # verbatim extract of any private marketplace conversation.
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid',
                              'title': 'Research regional ecommerce brands',
                              'verification': '100 source-backed records.'}}
        incoming = {'_id': 'inbound-1', 'author_type': 'user',
                    'content': {'type': 'text', 'text': 'Who is ARI?'},
                    'created_at': '2026-11-01T12:00:00+00:00'}
        earlier = {'_id': 'outbound-1', 'author_type': 'agent',
                   'content': {'type': 'text', 'text': 'Please clarify market.'},
                   'created_at': '2026-11-01T11:00:00+00:00'}
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1,
                    'at': '2026-11-01T11:00:00+00:00'}}, 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'agent-identity'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'messages_for', return_value=[earlier, incoming]), \
             patch.object(gambit, 'assess') as assess, \
             patch.object(gambit, 'infer') as infer:
            gambit.handle_open({'_id': 'buyer-job'}, state)
            gambit.handle_open({'_id': 'buyer-job'}, state)
        posts = [c for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].args[0].endswith('/messages'))
        self.assertIn(gambit.gambit_agent_card.card()['identity'],
                      posts[0].args[2]['content']['text'])
        self.assertIn('Agentic Resource Integration',
                      posts[0].args[2]['content']['text'])
        self.assertNotIn('currency', posts[0].args[2]['content']['text'].lower())
        self.assertEqual(state['bounties']['buyer-job']['action'], 'asked')
        self.assertEqual(state['bounties']['buyer-job']['last_owner_reply_id'],
                         gambit.key("prebid-owner-message", "buyer-job", "inbound-1"))
        self.assertEqual(state['bounties']['buyer-job']['last_question_version'], 1)
        assess.assert_not_called()
        infer.assert_not_called()

    def test_profane_and_colloquial_identity_questions_get_calm_introduction(self):
        examples = [
            "Who the fuck are you?!", "who tf are you?",
            "Who the hell is Ari?", "Who the fuck is this Gambit?",
            "Who is this?", "who this ari?", "Who am I talking to?",
            "What the fuck is ARI?", "Are you a bot?",
        ]
        for question in examples:
            with self.subTest(question=question):
                buyer = {"content": {"type": "text", "text": question}}
                self.assertTrue(gambit.buyer_identity_question(buyer))
                with patch.object(gambit, "respond_to_buyer") as model:
                    answer = gambit.prebid_buyer_reply(
                        {"bounty": {}}, [buyer], buyer)
                self.assertIn(gambit.gambit_agent_card.card()['identity'], answer)
                self.assertIn("Agentic Resource Integration", answer)
                self.assertNotIn("fuck", answer.lower())
                self.assertNotIn("payment", answer.lower())
                model.assert_not_called()
        for question in ["Who are you targeting?", "Who is the buyer?",
                         "Who are the 100 ecommerce companies?",
                         "What is the cost per lead?"]:
            with self.subTest(non_identity=question):
                self.assertFalse(gambit.buyer_identity_question(
                    {"content": {"text": question}}))

    def test_prebid_buyer_new_question_uses_reply_handler_not_assess(self):
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid',
                              'title': 'Analyze buyer-supplied data'}}
        incoming = {'_id': 'inbound-2', 'author_type': 'user',
                    'content': {'type': 'text',
                                'text': 'Can you explain your research method?'}}
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1}},
                 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'messages_for', return_value=[incoming]), \
             patch.object(gambit, 'respond_to_buyer', return_value={
                 'reply_text': 'I will check cited public sources.'}) as reply, \
             patch.object(gambit, 'assess') as assess:
            gambit.handle_open({'_id': 'buyer-job'}, state)
        reply.assert_called_once()
        assess.assert_not_called()
        posts = [c for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertIn('cited public sources', posts[0].args[2]['content']['text'])

    def test_prebid_buyer_answer_allows_bid_not_second_question(self):
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid',
                              'title': 'Build a sourced report'}}
        incoming = {'_id': 'inbound-3', 'author_type': 'user',
                    'content': {'type': 'text', 'text': 'USD, total company revenue.'}}
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1}},
                 'recent_outcomes': []}
        note = ('I propose to research the specified companies using public '
                'evidence, document revenue estimates, and deliver a sourced '
                'CSV that meets the buyer criteria after hiring.')
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'messages_for', return_value=[incoming]), \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'bid', 'payout_cents': 4500, 'note': note}):
            gambit.handle_open({'_id': 'buyer-job'}, state)
        posts = [c for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].args[0].endswith('/bid'))
        self.assertEqual(state['bounties']['buyer-job']['action'], 'bid_active')
        self.assertEqual(state['bounties']['buyer-job']['last_owner_reply_id'],
                         gambit.key("prebid-owner-message", "buyer-job", "inbound-3"))

    def test_prebid_clarification_loop_is_suppressed_after_buyer_response(self):
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid',
                              'title': 'Compile market report'}}
        incoming = {'_id': 'inbound-3', 'author_type': 'user',
                    'content': {'type': 'text', 'text': 'Use company revenue.'}}
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1}},
                 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'messages_for', return_value=[incoming]), \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'ask', 'question': 'Can you clarify company revenue?'}) as assess:
            gambit.handle_open({'_id': 'buyer-job'}, state)
            gambit.handle_open({'_id': 'buyer-job'}, state)
        assess.assert_called_once()
        self.assertFalse(any(len(c.args) > 1 and c.args[1] == 'POST'
                             for c in api.call_args_list))
        self.assertEqual(state['bounties']['buyer-job']['action'], 'asked')
        self.assertEqual(state['bounties']['buyer-job']['last_owner_reply_id'],
                         gambit.key("prebid-owner-message", "buyer-job", "inbound-3"))
        self.assertIn('duplicate_clarification_suppressed',
                      [x['action'] for x in state['recent_outcomes']])

    def test_prebid_reply_post_failure_does_not_mark_buyer_answered(self):
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid'}}
        incoming = {'_id': 'inbound-4', 'author_type': 'user',
                    'content': {'type': 'text', 'text': 'Who is Gambit?'}}
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1}},
                 'recent_outcomes': []}
        sent = []
        def request(path, method='GET', payload=None):
            if method == 'POST':
                sent.append(payload)
                if len(sent) == 1:
                    raise RuntimeError('bounty_api_UNAVAILABLE')
            return details
        with patch.object(gambit, 'api', side_effect=request), \
             patch.object(gambit, 'messages_for', return_value=[incoming]), \
             patch.object(gambit, 'infer') as infer:
            with self.assertRaisesRegex(RuntimeError, 'UNAVAILABLE'):
                gambit.handle_open({'_id': 'buyer-job'}, state)
            self.assertIsNone(state['bounties']['buyer-job'].get('last_owner_reply_id'))
            gambit.schedule_evaluation_retry(
                state, 'buyer-job', RuntimeError('bounty_api_UNAVAILABLE'))
            gambit.handle_open({'_id': 'buyer-job'}, state)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]['idempotency_key'], sent[1]['idempotency_key'])
        self.assertEqual(state['bounties']['buyer-job']['action'], 'asked')
        self.assertEqual(state['bounties']['buyer-job']['last_owner_reply_id'],
                         gambit.key("prebid-owner-message", "buyer-job", "inbound-4"))
        infer.assert_not_called()

    def test_historical_unanswered_bid_requeued_and_replied_once(self):
        item = {'_id': 'buyer-job', 'status': 'open', 'version': 1}
        state = {'bounties': {'buyer-job': {'action': 'bid_active', 'version': 1}},
                 'commercial': {'buyer-job': {'conversation': {'unanswered': True}}},
                 'recent_outcomes': []}
        with patch.object(gambit, 'pages', side_effect=[[item], []]), \
             patch.object(gambit, 'relevant_events', return_value=([], None)):
            _, _, _, _, waiting, _ = gambit.scan(state)
        self.assertEqual([x['_id'] for x in waiting], ['buyer-job'])
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid'},
                   'bid': {'status': 'active', 'bounty_version': 1}}
        incoming = {'_id': 'old-inbound', 'author_type': 'user',
                    'content': {'type': 'text', 'text': 'Who is ARI?'},
                    'created_at': '2026-11-06T15:37:00+00:00'}
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'sent-once'}
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'messages_for', return_value=[incoming]):
            gambit.handle_open(item, state)
            gambit.handle_open(item, state)
        posts = [c for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].args[2].get('idempotency_key'))
        self.assertTrue(state['commercial']['buyer-job'].get('last_outgoing_at'))

    def test_scan_requeues_old_asked_thread_without_customer_event(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1,
                                            'at': old}}, 'recent_outcomes': []}
        item = {'_id': 'buyer-job', 'status': 'open', 'version': 1}
        with patch.object(gambit, 'pages', side_effect=[[item], []]), \
             patch.object(gambit, 'relevant_events', return_value=([], None)):
            _, _, _, _, waiting, _ = gambit.scan(state)
        self.assertEqual([x['_id'] for x in waiting], ['buyer-job'])

    def test_prebid_recheck_without_new_message_does_not_regenerate_question(self):
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid'}}
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1, 'feasible_ask_review_version': 1}},
                 'recent_outcomes': []}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'assess') as assess:
            gambit.handle_open({'_id': 'buyer-job'}, state)
        assess.assert_not_called()
        self.assertEqual(state['bounties']['buyer-job']['action'], 'asked')
        self.assertFalse(any(len(c.args) > 1 and c.args[1] == 'POST'
                             for c in api.call_args_list))

    def test_stale_ask_without_gap_becomes_discounted_bid(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        details = {'bounty': {'_id': 'buyer-job', 'status': 'open',
                              'version': 1, 'flow': 'bid',
                              'budget_cents': 3500,
                              'title': 'Compile a market report'}}
        state = {'bounties': {'buyer-job': {'action': 'asked', 'version': 1,
                                            'at': old,
                                            'feasible_ask_review_version': 1}},
                 'commercial': {'buyer-job': {}},
                 'settlements': {}, 'recent_outcomes': []}
        def request(path, method='GET', payload=None):
            if method == 'POST':
                return {'bid': {'status': 'active',
                                'payout_cents': payload['payout_cents']}}
            return details
        with patch.object(gambit, 'api', side_effect=request) as api, \
             patch.object(gambit, 'messages_for', return_value=[]), \
             patch.object(gambit, 'assess', return_value={
                 'decision': 'ask', 'reason': 'needs detail',
                 'pass_reason': 'none', 'question': 'Q?',
                 'feasible': False, 'payout_cents': 0,
                 'note': '', 'assumptions': 'scope as described'}):
            gambit.handle_open({'_id': 'buyer-job'}, state)
        posts = [c for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST'
                 and c.args[0].endswith('/bid')]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].args[2]['payout_cents'], 2800)
        self.assertIn('Assumptions:', posts[0].args[2]['note'])
        self.assertIn('introductory rate', posts[0].args[2]['note'])

    def test_fresh_budget_wait_is_requeued(self):
        now = datetime.now(timezone.utc)
        item = {'_id': 'fresh-job', 'status': 'open', 'version': 1,
                'created_at': now.isoformat()}
        state = {'bounties': {'fresh-job': {'action': 'budget_wait',
                                            'version': 1,
                                            'retry_at': (now + timedelta(days=1)).isoformat()}},
                 'commercial': {}, 'recent_outcomes': []}
        with patch.object(gambit, 'pages', side_effect=[[item], []]), \
             patch.object(gambit, 'relevant_events', return_value=([], None)):
            _, _, _, _, waiting, _ = gambit.scan(state)
        self.assertEqual([x['_id'] for x in waiting], ['fresh-job'])

    def test_decision_schema_requires_accountable_pass_reason(self):
        self.assertIn('pass_reason', gambit.DECIDE_SCHEMA['required'])
        self.assertIn('capability_gap',
                      gambit.DECIDE_SCHEMA['properties']['pass_reason']['enum'])

    def test_captioned_explainer_mp4_is_supported_but_live_action_is_not(self):
        eligible = {'bounty': {'title': 'Create a captioned explainer slideshow',
                               'description': 'Text-led narrated presentation',
                               'verification': 'Deliver final MP4 explainer.'}}
        self.assertTrue(gambit.limited_explainer_video_eligible(eligible))
        self.assertIsNone(gambit.unsupported_deliverable(eligible))
        live = {'bounty': {'title': 'Create a live-action explainer video',
                           'description': 'Actors and camera footage',
                           'verification': 'Deliver final MP4.'}}
        self.assertFalse(gambit.limited_explainer_video_eligible(live))
        self.assertEqual(gambit.unsupported_deliverable(live), 'video_production')

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

    def test_existing_bid_reconciles_without_withdrawal_or_model(self):
        state = {'bounties': {'video1': {'version': 1, 'action': 'bid_active'}},
                 'recent_outcomes': [], 'events_cursor': None,
                 'last_check': datetime.now(timezone.utc).isoformat()}
        opened = [{'_id': 'video1', 'version': 1}]
        self.assertTrue(gambit.bid_reconciliation_due(state, opened))
        details = {'bounty': {'_id': 'video1', 'version': 1,
                              'title': 'Produce a video',
                              'verification': 'Deliver final MP4.'},
                   'bid': {'status': 'active'}}
        with patch.object(gambit, 'api', return_value=details) as api, \
             patch.object(gambit, 'infer') as infer, \
             patch.object(gambit, 'platform_active_bid_ids', return_value={'video1'}):
            gambit.reconcile_active_bids(opened, state)
        infer.assert_not_called()
        self.assertEqual(state['bounties']['video1']['action'], 'bid_active')
        self.assertEqual(state['bounties']['video1']['capability_review_reason'],
                         'video_production')
        self.assertFalse(any(len(c.args) > 1 and c.args[1] == 'POST'
                             for c in api.call_args_list))

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
            return details
        with patch.object(gambit, 'api', side_effect=request) as api:
            gambit.reconcile_active_bids([], state)
        self.assertEqual(state['bounties']['lost1']['action'], 'bid_active')
        self.assertEqual(state['bounties']['lost1']['capability_review_reason'],
                         'video_production')
        self.assertFalse(any(c.args[0].endswith('/bid/withdraw')
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
                 'decision': 'bid', 'payout_cents': 1700, 'note': ('I propose to review the supplied data, organize the findings in a clear report, and provide a final file with each finding checked against the brief.')}):
            gambit.handle_open({'_id': 'b1'}, state)
        sent = [c for c in calls if c[1] == 'POST']
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2]['expected_version'], 6)
        self.assertEqual(sent[0][2]['payout_cents'], 1400)

    def test_bid_note_requires_future_intent_and_specific_scope(self):
        good = ("I propose to review the supplied nonprofit domains, research publicly "
                "confirmed auction events, and deliver a sourced CSV with unknown "
                "fields clearly marked after completing source checks.")
        self.assertIsNone(gambit.bid_note_issue(good))
        self.assertEqual(gambit.bid_note_issue(
            "The app includes a CSV sample. Results are verified and the website "
            "is built using a suitable model and available tools; setup is included."),
            "bid_note_unverified_completed_work")
        self.assertEqual(gambit.bid_note_issue(
            "The proposal includes a sample CSV and results file; the app will be "
            "built with public sources and an appropriate AI model after award."),
            "bid_note_missing_prospective_intent")
        self.assertEqual(gambit.bid_note_issue(
            "I have completed the research and delivered a fully working research "
            "application with tested results for all nonprofits."),
            "bid_note_unverified_completed_work")
        self.assertEqual(gambit.bid_note_issue(
            "I researched the organizations, compiled a verified directory, "
            "and finished the application with documented setup instructions. "
            "I propose to hand over this work after acceptance."),
            "bid_note_unverified_completed_work")
        self.assertEqual(gambit.bid_note_issue(
            "The report has been prepared and the application has been built. "
            "I will deliver these completed artifacts after the buyer hires me."),
            "bid_note_unverified_completed_work")
        self.assertEqual(gambit.bid_note_issue("I can do it."),
                         "bid_note_missing_specific_scope")

    def test_past_tense_or_generic_bid_is_never_submitted(self):
        details = {"bounty": {"_id": "b1", "status": "open", "flow": "bid",
                              "version": 1, "title": "Compile a sourced report"}}
        state = {"bounties": {}, "recent_outcomes": []}
        with patch.object(gambit, "api", return_value=details) as request, \
             patch.object(gambit, "assess", return_value={
                 "decision": "bid", "payout_cents": 2200,
                 "note": "The report has been completed, all facts verified and the "
                         "final spreadsheet is ready for delivery."}), \
             patch.object(gambit, "repair_bid_note", return_value="I can do it."):
            gambit.handle_open({"_id": "b1"}, state)
        self.assertFalse(any(len(call.args) > 1 and call.args[1] == "POST"
                             for call in request.call_args_list))
        self.assertEqual(state["bounties"]["b1"]["action"], "retry_evaluation")

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
                 'note': ('I will research the requested companies, verify the public sources, and deliver a structured directory with clear source links and missing fields marked.'),
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
                 "decision": "bid", "payout_cents": 2400, "note": ("I propose to analyze the customer report, identify the key findings, and deliver a documented summary with data-backed conclusions.")}):
            gambit.handle_open({"_id": "b1"}, state)
        bids = [call for call in request_api.call_args_list
                if len(call.args) >= 2 and call.args[1] == "POST"
                and call.args[0].endswith("/bid")]
        self.assertEqual(len(bids), 1)
        sent = bids[0].args[2]
        self.assertEqual(sent["idempotency_key"],
                         gambit.key("bid", "b1", 3, 1900, "oldbid"))
        self.assertNotEqual(sent["idempotency_key"],
                            gambit.key("bid", "b1", 3, 1900, ""))
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

    def test_flagged_bid_event_enters_probe_and_survives_reconciliation(self):
        from tools import gambit_early_scan as early
        item = {'_id': 'video1', 'version': 2, 'status': 'open'}
        event = {'id': 'evt-9', 'type': 'work.message.created',
                 'data': {'bounty_id': 'video1'}}
        details = {'bounty': {'_id': 'video1', 'status': 'open',
                              'version': 2, 'flow': 'bid',
                              'title': 'Produce a product video',
                              'verification': 'Deliver final MP4 video.'},
                   'bid': {'status': 'active', 'bounty_version': 1}}
        latest = {'_id': 'buyer-msg-8', 'author_type': 'user',
                  'content': {'type': 'text',
                              'text': 'Can you confirm an MP4?'}}
        state = {'bounties': {'video1': {'action': 'bid_active', 'version': 2,
                                        'at': '2026-11-01T12:00:00+00:00'}},
                 'recent_outcomes': []}
        admission = early.admission(state, [item], [], [event])
        self.assertTrue(admission['needs_run'])
        self.assertTrue(admission['needs_model'])
        with patch.object(gambit, 'pages', side_effect=[[item], []]), patch.object(
                gambit, 'relevant_events', return_value=([event], 'cursor')):
            opened, _, events, _, waiting, _ = gambit.scan(state)
        self.assertEqual([x['_id'] for x in waiting], ['video1'])
        def request(path, method='GET', payload=None):
            return details if method == 'GET' else {'message_id': 'agent-reply'}
        with patch.object(gambit, 'api', side_effect=request) as api, patch.object(
                gambit, 'CURRENT_EVENTS', events), patch.object(
                gambit, 'messages_for', return_value=[latest]), patch.object(
                gambit, 'platform_active_bid_ids', return_value={'video1'}), patch.object(
                gambit, 'infer') as infer:
            gambit.handle_open(waiting[0], state)
            gambit.reconcile_active_bids(opened, state)
            gambit.handle_open(waiting[0], state)
        posts = [call for call in api.call_args_list
                 if len(call.args) > 1 and call.args[1] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].args[0].endswith('/messages'))
        self.assertNotIn('/bid/withdraw', str(api.call_args_list))
        self.assertEqual(state['bounties']['video1']['action'], 'bid_active')
        self.assertEqual(state['bounties']['video1']['last_owner_reply_id'],
                         'buyer-msg-8')
        self.assertEqual(state['bounties']['video1']['capability_review_reason'],
                         'video_production')
        infer.assert_not_called()

    def test_flagged_bid_reply_failure_preserves_retry_identity(self):
        details = {'bounty': {'_id': 'video1', 'status': 'open',
                              'version': 1, 'flow': 'bid',
                              'title': 'Produce a product video',
                              'verification': 'Deliver final MP4 video.'},
                   'bid': {'status': 'active', 'bounty_version': 1}}
        event = {'type': 'work.message.created',
                 'data': {'bounty_id': 'video1'}}
        latest = {'_id': 'buyer-msg-8', 'author_type': 'user',
                  'content': {'type': 'text', 'text': 'Is MP4 ready?'}}
        state = {'bounties': {'video1': {'action': 'bid_active', 'version': 1}},
                 'recent_outcomes': []}
        attempts = []
        def request(path, method='GET', payload=None):
            if method == 'POST':
                attempts.append(payload)
                if len(attempts) == 1:
                    raise RuntimeError('bounty_api_TEMPORARY_ERROR')
                return {'message_id': 'agent-reply'}
            return details
        with patch.object(gambit, 'api', side_effect=request), patch.object(
                gambit, 'CURRENT_EVENTS', [event]), patch.object(
                gambit, 'messages_for', return_value=[latest]), patch.object(
                gambit, 'infer') as infer:
            with self.assertRaisesRegex(RuntimeError, 'TEMPORARY_ERROR'):
                gambit.handle_open({'_id': 'video1'}, state)
            self.assertIsNone(state['bounties']['video1'].get('last_owner_reply_id'))
            gambit.handle_open({'_id': 'video1'}, state)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]['idempotency_key'],
                         attempts[1]['idempotency_key'])
        self.assertEqual(state['bounties']['video1']['last_owner_reply_id'],
                         'buyer-msg-8')
        infer.assert_not_called()

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

    def test_repeated_quality_failure_schedules_new_retry_without_refund(self):
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
        self.assertEqual(state['bounties']['b1']['action'], 'quality_rejected')
        self.assertEqual(state['bounties']['b1']['quality_retry_count'], 3)
        self.assertTrue(state['bounties']['b1'].get('retry_at'))
        posts = [c for c in api.call_args_list
                 if len(c.args) > 1 and c.args[1] == 'POST']
        self.assertFalse(any('/unclaim' in c.args[0] for c in posts))

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


    def test_evaluation_retry_exhaustion_is_terminal_and_preserves_reason(self):
        state = {"bounties": {"b1": {"action": "retry_evaluation", "version": 3,
                                      "retry_count": 3}}, "recent_outcomes": []}
        gambit.schedule_evaluation_retry(state, "b1", ValueError("transient_failure"))
        record = state["bounties"]["b1"]
        self.assertEqual(record["action"], "evaluation_abandoned")
        self.assertEqual(record["retry_count"], 4)
        self.assertIsNone(record["retry_at"])
        self.assertEqual(record["last_error"], "ValueError")
        self.assertFalse(gambit.retry_due(record))
        self.assertEqual(state["recent_outcomes"][-1]["action"], "evaluation_abandoned")

    def test_evaluation_retry_below_limit_remains_scheduled(self):
        state = {"bounties": {"b1": {"action": "retry_evaluation", "version": 3,
                                      "retry_count": 0}}, "recent_outcomes": []}
        gambit.schedule_evaluation_retry(state, "b1", RuntimeError("transient"))
        record = state["bounties"]["b1"]
        self.assertEqual(record["action"], "retry_evaluation")
        self.assertEqual(record["retry_count"], 1)
        self.assertTrue(record["retry_at"])

    def test_safe_failure_reason_never_persists_unrecognized_buyer_text(self):
        self.assertEqual(gambit.safe_failure_reason(ValueError("proposal_samples_missing")),
                         "proposal_samples_missing")
        self.assertEqual(gambit.safe_failure_reason(ValueError("buyer private content")),
                         "ValueError")



    def test_budget_wait_future_retry_does_not_churn_receipts(self):
        from datetime import datetime, timedelta, timezone
        state = {"bounties": {"b1": {"action": "budget_wait",
            "last_error": "openai_daily_pace_exceeded",
            "retry_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()}},
            "recent_outcomes": []}
        before = dict(state["bounties"]["b1"])
        gambit.schedule_evaluation_retry(state, "b1",
                                          RuntimeError("openai_daily_pace_exceeded"))
        self.assertEqual(state["bounties"]["b1"], before)
        self.assertEqual(state["recent_outcomes"], [])



    def test_buyer_reply_budget_guard_uses_safe_acknowledgment_without_model_spend(self):
        from unittest.mock import patch
        message = {"author_type": "user", "_id": "buyer1",
                   "content": {"type": "text", "text": "Can you confirm the timing?"}}
        with patch.object(gambit, "infer", side_effect=RuntimeError("openai_daily_pace_exceeded")):
            result = gambit.respond_to_buyer({"bounty": {"title": "Research"}}, [message])
        self.assertIn("received", result["reply_text"])
        self.assertNotIn("shortly", result["reply_text"])

    def test_buyer_reply_nonbudget_failure_is_not_masked(self):
        from unittest.mock import patch
        message = {"author_type": "user", "_id": "buyer1",
                   "content": {"type": "text", "text": "What is the timeline?"}}
        with patch.object(gambit, "infer", side_effect=RuntimeError("openai_http_500")):
            with self.assertRaisesRegex(RuntimeError, "openai_http_500"):
                gambit.respond_to_buyer({"bounty": {"title": "Research"}}, [message])


if __name__ == '__main__':
    unittest.main()
