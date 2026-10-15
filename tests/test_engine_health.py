import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from tools import engine_health

NOW = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)


class TestEngineHealth(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        for name in ("ops/runs", "ops/scout-runs", "ops/discovery", "ops/agent-queue"):
            (self.root / name).mkdir(parents=True)
        self.triage = self.root / "ops/discovery/triage.json"
        self.triage.write_text(json.dumps({
            "reviewed_at_utc": "2026-10-15T19:55:00Z",
            "reviewed_items": 4,
            "decision_counts": {"competing_fix_or_pr": 4}}))

    def tearDown(self):
        self.t.cleanup()

    def put(self, directory, fname, attr, time="2026-10-15T19:50:00Z",
            status="completed"):
        (self.root / directory / fname).write_text(json.dumps({
            "attribution": attr, "recorded_at_utc": time, "status": status}))

    def test_empty_workers_are_not_healthy(self):
        report = engine_health.run(self.root, NOW)
        self.assertEqual(report["reliability_status"], "NOT_VERIFIED")
        self.assertEqual(report["scout_terminal_records_last_24h"], 0)
        self.assertEqual(report["executor_terminal_records_last_24h"], 0)
        self.assertIsNone(report["verified_new_revenue_usd"])

    def test_two_real_terminal_agent_receipts_pass(self):
        self.put("ops/scout-runs", "s.json", "scheduled_scout")
        self.put("ops/runs", "e.json", "scheduled_hourly")
        self.put("ops/runs", "live.json", "live_chat")
        self.put("ops/runs", "accept.json", "one_time_scheduled_full_cycle")
        report = engine_health.run(self.root, NOW)
        self.assertEqual(report["reliability_status"], "PASS")
        self.assertEqual(report["scout_terminal_records_last_24h"], 1)
        self.assertEqual(report["executor_terminal_records_last_24h"], 1)
        self.assertFalse(report["money_checked"])

    def test_running_start_is_counted_not_misclassified_as_terminal_error(self):
        self.put("ops/scout-runs", "s-start.json", "scheduled_scout", status="running")
        self.put("ops/scout-runs", "s.json", "scheduled_scout", status="completed")
        self.put("ops/runs", "e-start.json", "scheduled_hourly", status="running")
        self.put("ops/runs", "e.json", "scheduled_hourly", status="completed")
        report = engine_health.run(self.root, NOW)
        self.assertEqual(report["reliability_status"], "PASS")
        self.assertEqual(report["scout_start_receipts_last_24h"], 1)
        self.assertEqual(report["executor_start_receipts_last_24h"], 1)
        self.assertEqual(report["scout_record_errors"], [])
        self.assertEqual(report["executor_record_errors"], [])

    def test_stale_receipts_do_not_pass(self):
        self.put("ops/scout-runs", "s.json", "scheduled_scout", "2026-10-14T19:00:00Z")
        self.put("ops/runs", "e.json", "scheduled_hourly", "2026-10-15T17:00:00Z")
        report = engine_health.run(self.root, NOW)
        self.assertEqual(report["reliability_status"], "NOT_VERIFIED")
        self.assertFalse(report["scout_fresh_within_2h"])
        self.assertFalse(report["executor_fresh_within_2h"])

    def test_run_without_utc_timestamp_is_not_fabricated(self):
        self.put("ops/scout-runs", "s.json", "scheduled_scout")
        (self.root / "ops/runs/no-timestamp.json").write_text(json.dumps({
            "attribution": "scheduled_hourly", "status": "completed"}))
        report = engine_health.run(self.root, NOW)
        self.assertEqual(report["executor_terminal_records_last_24h"], 0)
        self.assertTrue(report["executor_record_errors"])

    def test_incomplete_terminal_and_stale_triage(self):
        self.put("ops/scout-runs", "s.json", "scheduled_scout")
        self.put("ops/runs", "e.json", "scheduled_hourly", status="running")
        self.triage.write_text(json.dumps({"reviewed_at_utc": "2026-10-15T15:00:00Z"}))
        report = engine_health.run(self.root, NOW)
        self.assertEqual(report["reliability_status"], "NOT_VERIFIED")
        self.assertFalse(report["triage_fresh_within_3h"])


if __name__ == "__main__":
    unittest.main()
