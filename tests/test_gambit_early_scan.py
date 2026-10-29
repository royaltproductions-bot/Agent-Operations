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
        self.assertEqual(changed, 2)
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

    def test_full_scan_updates_only_if_new_and_keeps_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = {"schema": 1, "bounties": {}, "settlements": {}}
            path.write_text(json.dumps(state))
            with patch.object(early, "STATE_PATH", path), \
                 patch.object(early, "open_listing_pages", return_value=[{"_id": "new", "version": 1}]), \
                 patch.dict(early.os.environ, {"BOUNTY_AGENT_API_KEY": "dummy"}):
                early.run()
                original = path.read_text()
                early.run()
                self.assertEqual(original, path.read_text())
                self.assertEqual(json.loads(original)["settlements"], {})

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
