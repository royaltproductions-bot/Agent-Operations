import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tools import agent_queue as queue

BASE = {
    "schema_version": 1,
    "id": "2026-10-15-buyer-signal-sample",
    "kind": "buyer_request",
    "status": "qualified",
    "attribution": "scout",
    "buyer_signal": "Public test buyer explicitly asks for a useful vendor settlement report",
    "proposed_action": "Assess fit and respond only via an authorized direct route",
    "channel": "public community",
    "evidence_url": "https://example.com/discussion/1234",
    "created_at_utc": "2026-10-15T17:00:00Z",
    "updated_at_utc": "2026-10-15T17:00:00Z",
    "score_factors": {"buyer_intent": 5, "reachability": 4, "audience_match": 5, "offer_fit": 5, "urgency": 3},
    "priority_score": 90,
    "safety_review": "pass",
    "contains_private_data": False,
}


class QueueTests(unittest.TestCase):
    def test_good_candidate_and_deterministic_score(self):
        self.assertEqual(queue.validate(BASE)["priority_score"], 90)

    def test_fail_closed_on_unverified_score(self):
        x = copy.deepcopy(BASE)
        x["priority_score"] = 91
        with self.assertRaisesRegex(ValueError, "weighted factor"):
            queue.validate(x)

    def test_rejects_private_data_flag_and_low_priority(self):
        for key, value in (("contains_private_data", True), ("priority_score", 2)):
            x = copy.deepcopy(BASE)
            x[key] = value
            with self.assertRaises(ValueError):
                queue.validate(x)

    def test_claim_requires_expiring_lease(self):
        x = copy.deepcopy(BASE)
        x["status"] = "claimed"
        x["claimed_by"] = "executor"
        with self.assertRaisesRegex(ValueError, "claim_until_utc"):
            queue.validate(x)
        x["claim_until_utc"] = "2026-10-15T18:30:00Z"
        self.assertEqual(queue.validate(x)["status"], "claimed")

    def test_completed_requires_evidence(self):
        x = copy.deepcopy(BASE)
        x["status"] = "completed"
        with self.assertRaisesRegex(ValueError, "outcome_url"):
            queue.validate(x)
        x["outcome_url"] = "https://example.com/receipt/1"
        x["outcome_description"] = "External action succeeded with a public receipt"
        self.assertEqual(queue.validate(x)["status"], "completed")

    def test_queues_prioritize_and_match_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            x = copy.deepcopy(BASE)
            (d / (x["id"] + ".json")).write_text(json.dumps(x))
            y = copy.deepcopy(BASE)
            y["id"] = "2026-10-16-buyer-signal-sample"
            y["priority_score"] = 71
            y["score_factors"] = {"buyer_intent": 3, "reachability": 4, "audience_match": 3, "offer_fit": 4, "urgency": 4}
            # computed score is 71: 15 + 16 + 12 + 16 + 12
            (d / (y["id"] + ".json")).write_text(json.dumps(y))
            self.assertEqual([z["id"] for z in queue.rank(queue.inventory(d))],
                             [x["id"], y["id"]])
            (d / "different-id.json").write_text(json.dumps(x))
            with self.assertRaisesRegex(ValueError, "filename"):
                queue.inventory(d)

    def test_watchdog_flags_expired_claim_without_replaying(self):
        expired = copy.deepcopy(BASE)
        expired["status"] = "claimed"
        expired["claimed_by"] = "executor"
        expired["claim_until_utc"] = "2026-10-15T17:15:00Z"
        now = dt.datetime(2026, 10, 15, 18, 0, tzinfo=dt.timezone.utc)
        report = queue.health([expired], now=now)
        self.assertEqual(report["expired_claim_ids"], [expired["id"]])
        self.assertTrue(report["needs_attention"])
        self.assertEqual(expired["status"], "claimed")

    def test_append_only_outcome_prevents_duplicate_work(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            work = base / "queue"
            outcomes = base / "outcomes"
            work.mkdir()
            outcomes.mkdir()
            (work / (BASE["id"] + ".json")).write_text(json.dumps(BASE))
            (outcomes / "done.json").write_text(json.dumps({
                "lead_id": BASE["id"],
                "status": "sent",
                "external_receipt": "https://example.com/receipt"
            }))
            handled = queue.handled_lead_ids(outcomes)
            self.assertEqual(handled, {BASE["id"]})
            items = queue.inventory(work, handled_ids=handled)
            self.assertEqual(queue.rank(items, handled), [])
            self.assertEqual(queue.health(items, handled_ids=handled)["already_handled_via_immutable_outcomes"], 1)

    def test_corrupted_outcomes_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            (d / "bad.json").write_text("not-json")
            with self.assertRaisesRegex(ValueError, "Invalid immutable outcome"):
                queue.handled_lead_ids(d)

    def test_rejects_unsafe_local_url(self):
        x = copy.deepcopy(BASE)
        x["evidence_url"] = "https://localhost/path"
        with self.assertRaises(ValueError):
            queue.validate(x)


if __name__ == "__main__":
    unittest.main()
