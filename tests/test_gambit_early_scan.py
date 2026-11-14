"""Hermetic contract tests for the pre-start market observer."""
import io
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools import gambit_early_scan as early


class EarlyMarketTests(TestCase):
    def test_observes_new_ids_without_erasing_existing_bid_or_ledger(self):
        state = {
            "schema": 1,
            "bounties": {"old": {"action": "bid_active", "version": 1}},
            "bounty_archive": {"old": {"versions": {"1": {"encrypted_brief": "cipher"}}}},
            "settlements": {"paid": {"payout_cents": 100}},
        }
        changed = early.capture_ids(state, [
            {"_id": "old", "version": 1},
            {"_id": "new", "version": 3},
        ])
        self.assertEqual(changed, 4)  # two discovery records + two commercial observations
        self.assertEqual(state["bounties"]["old"]["action"], "bid_active")
        self.assertEqual(state["bounty_archive"]["old"]["versions"]["1"]["encrypted_brief"], "cipher")
        self.assertEqual(state["bounties"]["new"]["action"], "evaluation_pending")
        self.assertEqual(state["bounties"]["new"]["version"], 3)
        self.assertEqual(state["settlements"]["paid"]["payout_cents"], 100)
        self.assertEqual(early.capture_ids(state, [{"_id": "new", "version": 3}]), 0)

    def test_open_market_pagination_requires_all_pages(self):
        class Response:
            def __init__(self, data):
                self.data = data
            def __enter__(self):
                return io.BytesIO(json.dumps(self.data).encode())
            def __exit__(self, *args):
                return False
        pages = [
            {"bounties": [{"_id": "a", "version": 1}], "has_more": True, "next_cursor": "page2"},
            {"bounties": [{"_id": "b", "version": 1}], "has_more": False},
        ]
        urls = []
        def get(request, timeout):
            urls.append(request.full_url)
            return Response(pages.pop(0))
        with patch.object(early.urllib.request, "urlopen", side_effect=get):
            got = early.open_listing_pages("dummy")
        self.assertEqual([x["_id"] for x in got], ["a", "b"])
        self.assertIn("cursor=page2", urls[1])

    def test_incomplete_api_response_is_not_an_empty_market(self):
        class Response:
            def __enter__(self):
                return io.BytesIO(json.dumps({"other": []}).encode())
            def __exit__(self, *args):
                return False
        with patch.object(early.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaises(ValueError):
                early.open_listing_pages("dummy")

    def test_full_scan_records_every_fetch_without_erasing_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = {"schema": 1, "bounties": {}, "settlements": {}}
            path.write_text(json.dumps(state))
            listing = [{"_id": "new", "version": 1,
                        "created_at": "2026-11-13T20:00:00+00:00"}]
            with patch.object(early, "STATE_PATH", path), \
                 patch.object(early, "open_listing_pages", return_value=listing), \
                 patch.dict(early.os.environ, {"BOUNTY_AGENT_API_KEY": "dummy"}):
                early.run()
                first = json.loads(path.read_text())
                early.run()
                second = json.loads(path.read_text())
            self.assertEqual(second["settlements"], {})
            self.assertEqual(second["bounties"]["new"]["action"], "evaluation_pending")
            self.assertEqual(second["market_observation"]["last_open_count"], 1)
            self.assertEqual(second["market_observation"]["newest_created_at"],
                             "2026-11-13T20:00:00+00:00")
            self.assertEqual(len(first["market_observation_history"]), 1)
            self.assertEqual(len(second["market_observation_history"]), 2)
            funnel = json.loads(path.with_name("funnel.json").read_text())
            self.assertEqual(funnel["market_observation"]["last_open_count"], 1)

    def test_market_request_bypasses_intermediary_cache(self):
        class Response:
            def __enter__(self):
                return io.BytesIO(b'{"bounties":[],"has_more":false}')
            def __exit__(self, *args):
                return False
        with patch.object(early.urllib.request, "urlopen", return_value=Response()) as get:
            self.assertEqual(early.open_listing_pages("dummy"), [])
        request = get.call_args.args[0]
        self.assertEqual(request.get_header("Cache-control"), "no-cache")
        self.assertEqual(request.get_header("Pragma"), "no-cache")

    def test_idle_preflight_does_not_start_model(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        state = {"bounties": {"old": {"action": "bid_missing", "version": 1}},
                 "last_check": now.isoformat(),
                 "last_bid_reconciliation": now.isoformat()}
        result = early.admission(state, [{"_id": "old", "version": 1}], [], [], now)
        self.assertFalse(result["needs_run"])
        self.assertFalse(result["needs_model"])

    def test_prior_account_rejections_queued_once_without_touching_other_passes(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        state = {"bounties": {
            "old": {"action": "pass_unsupported", "version": 1,
                    "reason": "authenticated_account_operation"},
            "already_checked": {"action": "pass_unsupported", "version": 1,
                    "reason": "authenticated_account_operation",
                    "account_policy_review_version": early.ACCOUNT_POLICY_RECHECK_VERSION},
            "unrelated": {"action": "pass_unsupported", "version": 1,
                          "reason": "video_production"},
            "active": {"action": "bid_active", "version": 1,
                       "reason": "authenticated_account_operation"},
        }, "last_check": now.isoformat(),
           "last_bid_reconciliation": now.isoformat()}
        listed = [{"_id": bid, "version": 1} for bid in state["bounties"]]
        result = early.admission(state, listed, [], [], now)
        self.assertTrue(result["needs_run"])
        self.assertTrue(result["needs_model"])
        self.assertEqual(result["new_or_changed"], 1)
        self.assertTrue(early.account_policy_recheck_due(state["bounties"]["old"]))
        self.assertFalse(early.account_policy_recheck_due(
            state["bounties"]["already_checked"]))
        self.assertFalse(early.account_policy_recheck_due(
            state["bounties"]["unrelated"]))
        self.assertFalse(early.account_policy_recheck_due(state["bounties"]["active"]))
        state["bounties"]["old"]["account_policy_review_version"] = (
            early.ACCOUNT_POLICY_RECHECK_VERSION)
        self.assertFalse(early.admission(state, listed, [], [], now)["needs_run"])

    def test_old_asked_threads_reconcile_only_when_due(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=7)).isoformat()
        new = (now - timedelta(minutes=5)).isoformat()
        records = {'old': {'action': 'asked', 'version': 1, 'at': old},
                   'recent': {'action': 'asked', 'version': 1, 'at': new},
                   'handled': {'action': 'asked', 'version': 1, 'at': old,
                               'last_asked_review_at': new},
                   'non_asked': {'action': 'pass', 'version': 1, 'at': old}}
        state = {'bounties': records,
                 'last_check': now.isoformat(),
                 'last_bid_reconciliation': now.isoformat()}
        listing = [{'_id': k, 'version': 1} for k in records]
        decision = early.admission(state, listing, [], [], now)
        self.assertTrue(decision['needs_model'])
        self.assertEqual(decision['new_or_changed'], 1)
        self.assertTrue(early.asked_recheck_due(records['old'], now))
        self.assertFalse(early.asked_recheck_due(records['recent'], now))
        self.assertFalse(early.asked_recheck_due(records['handled'], now))
        records['old']['last_asked_review_at'] = new
        self.assertFalse(early.admission(state, listing, [], [], now)['needs_model'])

    def test_new_work_and_buyer_events_are_scheduled(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        state = {"bounties": {}, "last_check": now.isoformat(),
                 "last_bid_reconciliation": now.isoformat()}
        self.assertTrue(early.admission(state, [{"_id": "new", "version": 1}], [], [], now)["needs_model"])
        self.assertTrue(early.admission(state, [], [],
                      [{"type": "work.completed", "data": {"bounty_id": "x"}}],
                      now)["needs_run"])
        self.assertTrue(early.admission(state, [], [{"_id": "x", "status": "claimed"}],
                     [], now)["needs_production_model"])

    def test_full_preflight_records_new_work_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text(json.dumps({"schema": 1, "bounties": {},
                "last_check": early.timestamp(),
                "last_bid_reconciliation": early.timestamp()}))
            with patch.object(early, "STATE_PATH", path), \
                 patch.object(early, "open_listing_pages", return_value=[
                     {"_id": "new", "version": 1}]), \
                 patch.object(early, "other_pages", side_effect=[[], []]), \
                 patch.dict(early.os.environ, {"BOUNTY_AGENT_API_KEY": "test",
                                               "GITHUB_OUTPUT": ""}):
                early.run(probe_mode=True)
            self.assertEqual(json.loads(path.read_text())["bounties"]["new"]["action"],
                             "evaluation_pending")

    def test_preflight_network_timeout_retries_without_false_no_work(self):
        class Response:
            def __enter__(self):
                return io.BytesIO(b'{"events":[],"has_more":false}')
            def __exit__(self, *args):
                return False
        with patch.object(early.urllib.request, "urlopen",
                          side_effect=[TimeoutError(), Response()]) as call, \
             patch.object(early.time, "sleep") as sleep:
            self.assertEqual(early.other_pages(
                "key", "/v1/agent/events?limit=100", "events"), [])
        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once()


    def test_open_listing_retries_network_timeout(self):
        class Response:
            def __enter__(self):
                return io.BytesIO(b'{"bounties":[{"_id":"fresh","version":1}],"has_more":false}')
            def __exit__(self, *args):
                return False
        with patch.object(early.urllib.request, "urlopen",
                          side_effect=[TimeoutError(), Response()]) as request, \
             patch.object(early.time, "sleep") as sleep:
            self.assertEqual(early.open_listing_pages("key")[0]["_id"], "fresh")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once()

    def test_settlement_event_does_not_reevaluate_old_open_listing(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        state = {"bounties": {"old": {"action": "pass_unsupported", "version": 1}},
                 "last_check": now.isoformat(),
                 "last_bid_reconciliation": now.isoformat()}
        older = [{"_id": "old", "version": 1}]
        event = [{"type": "work.completed", "data": {"bounty_id": "old"}}]
        result = early.admission(state, older, [], event, now)
        self.assertTrue(result["needs_run"])
        self.assertFalse(result["needs_model"])
        edit = [{"type": "bounty.updated", "data": {"bounty_id": "old"}}]
        self.assertTrue(early.admission(state, older, [], edit, now)["needs_model"])

    def test_expired_cursor_distinct_from_other_http_400(self):
        import urllib.error
        def bad(code):
            return urllib.error.HTTPError(
                "https://api.trybounty.ai/v1/agent/events", 400, "Bad Request",
                {}, io.BytesIO(json.dumps({"error": {"code": code}}).encode()))
        with patch.object(early.urllib.request, "urlopen",
                          side_effect=bad("INVALID_CURSOR")):
            with self.assertRaisesRegex(ValueError, "invalid_event_cursor"):
                early.other_pages("key", "/v1/agent/events?limit=100", "events")
        with patch.object(early.urllib.request, "urlopen",
                          side_effect=bad("INVALID_FILTER")):
            with self.assertRaises(urllib.error.HTTPError):
                early.other_pages("key", "/v1/agent/events?limit=100", "events")

    def test_new_listing_checkpoint_survives_event_feed_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            output_path = Path(folder) / "output"
            output_path.write_text("")
            path.write_text(json.dumps({"schema": 1, "bounties": {}}))
            with patch.object(early, "STATE_PATH", path), \
                 patch.object(early, "open_listing_pages",
                              return_value=[{"_id": "urgent", "version": 1}]), \
                 patch.object(early, "other_pages", side_effect=[[], TimeoutError()]), \
                 patch.dict(early.os.environ, {
                     "BOUNTY_AGENT_API_KEY": "dummy",
                     "GITHUB_OUTPUT": str(output_path)}):
                with self.assertRaises(TimeoutError):
                    early.run(probe_mode=True)
            stored = json.loads(path.read_text())
            self.assertEqual(stored["bounties"]["urgent"]["action"],
                             "evaluation_pending")
            self.assertIn("changed=true", output_path.read_text())

    def test_live_check_fails_when_authentication_is_missing(self):
        with patch.dict(early.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "missing_trybounty_api_key"):
                early.run(probe_mode=True)
