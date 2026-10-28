"""Offline contract checks: no fake opportunities, secret leaks or duplicate bids."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
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
                    'question': ''}
        with patch.object(gambit, 'infer', side_effect=[plan, decision]) as infer, \
             patch('tools.gambit_research.search_public_web', return_value=evidence) as search:
            result = gambit.assess(details)
        self.assertEqual(result['decision'], 'bid')
        self.assertIn('Supplier details', str(infer.call_args.args[2]['web_evidence']))
        search.assert_called_once_with(['suppliers town'])

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


if __name__ == '__main__':
    unittest.main()
