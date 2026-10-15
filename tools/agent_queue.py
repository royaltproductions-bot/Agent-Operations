#!/usr/bin/env python3
"""Validate and rank cross-agent commercial opportunities. Standard library only.

Repository files are the persistent queue. GitHub's file blob SHA must be used
as optimistic concurrency control on writes; this script does not post content,
send messages, or access customer/private data.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse

VALID_STATES = {"qualified", "claimed", "completed", "blocked", "discarded"}
VALID_WORKERS = {"scout", "executor", "live_chat"}
VALID_KINDS = {"buyer_request", "channel_opportunity", "paid_job", "inbound_order"}
SCORE_WEIGHTS = {
    "buyer_intent": 5,
    "reachability": 4,
    "audience_match": 4,
    "offer_fit": 4,
    "urgency": 3,
}
MAX_READY = 10


def parse_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(field + " must be an ISO-8601 UTC timestamp ending in Z")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(field + " invalid timestamp") from exc


def public_url(value, field):
    if not isinstance(value, str):
        raise ValueError(field + " must be a public HTTPS URL")
    p = urlparse(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError(field + " must be a public HTTPS URL")
    if p.hostname.lower() in ("localhost", "127.0.0.1", "::1") or p.hostname.lower().endswith(".local"):
        raise ValueError(field + " may not be local")
    return value


def validate(obj, filename=None):
    if not isinstance(obj, dict) or obj.get("schema_version") != 1:
        raise ValueError("Expected schema_version 1 JSON object")
    ident = obj.get("id")
    if not isinstance(ident, str) or not 8 <= len(ident) <= 90 or not all(
            x.islower() or x.isdigit() or x == "-" for x in ident):
        raise ValueError("id must be a lowercase dash-separated stable identifier")
    if filename is not None and Path(filename).stem != ident:
        raise ValueError("filename must match immutable id")
    if obj.get("kind") not in VALID_KINDS or obj.get("status") not in VALID_STATES:
        raise ValueError("invalid kind or status")
    if obj.get("attribution") not in VALID_WORKERS:
        raise ValueError("attribution must identify actual creator")
    for field, length in (("buyer_signal", 25), ("proposed_action", 20), ("channel", 3)):
        if not isinstance(obj.get(field), str) or len(obj[field].strip()) < length:
            raise ValueError(field + " is missing or vague")
    public_url(obj.get("evidence_url"), "evidence_url")
    created = parse_utc(obj.get("created_at_utc"), "created_at_utc")
    updated = parse_utc(obj.get("updated_at_utc"), "updated_at_utc")
    if updated < created:
        raise ValueError("updated_at_utc predates creation")
    scores = obj.get("score_factors")
    if not isinstance(scores, dict) or set(scores) != set(SCORE_WEIGHTS):
        raise ValueError("score_factors must include the five explicit factors")
    if any(type(v) is not int or v < 0 or v > 5 for v in scores.values()):
        raise ValueError("score factors must be integer 0..5")
    expected = sum(scores[k] * w for k, w in SCORE_WEIGHTS.items())
    if type(obj.get("priority_score")) is not int or obj["priority_score"] != expected:
        raise ValueError("priority_score must equal weighted factor score")
    if obj["status"] in ("qualified", "claimed") and expected < 70:
        raise ValueError("qualified/claimed work requires score >=70")
    if obj.get("safety_review") != "pass":
        raise ValueError("safety_review must be pass after checking permissions and claims")
    if obj.get("contains_private_data") is not False:
        raise ValueError("queue may not contain private customer information")
    if obj["status"] == "claimed":
        if obj.get("claimed_by") != "executor":
            raise ValueError("claimed work requires claimed_by=executor")
        parse_utc(obj.get("claim_until_utc"), "claim_until_utc")
    if obj["status"] == "completed":
        public_url(obj.get("outcome_url"), "outcome_url")
        if not isinstance(obj.get("outcome_description"), str) or len(obj["outcome_description"]) < 20:
            raise ValueError("completed item needs clear outcome_description")
    if obj["status"] == "blocked":
        if not isinstance(obj.get("blocked_reason"), str) or len(obj["blocked_reason"]) < 12:
            raise ValueError("blocked item requires specific reason")
    if obj.get("money_collected_usd") is not None and obj.get("payment_evidence_url") is None:
        raise ValueError("money claimed requires payment evidence")
    return obj


def handled_lead_ids(folder):
    """Immutable executor outcome events supersede unchanged qualified lead files."""
    found = set()
    base = Path(folder)
    if not base.exists():
        return found
    for path in sorted(base.glob("*.json")):
        try:
            outcome = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Invalid immutable outcome " + path.name) from exc
        ident = outcome.get("lead_id")
        if not isinstance(ident, str) or not ident:
            raise ValueError("Missing lead_id in outcome " + path.name)
        if outcome.get("status") in ("completed", "blocked", "sent", "accepted", "paid", "attempted"):
            found.add(ident)
    return found


def inventory(folder, handled_ids=None):
    files = sorted(Path(folder).glob("*.json"))
    items = []
    for path in files:
        item = validate(json.loads(path.read_text(encoding="utf-8")), filename=path)
        items.append(item)
    open_work = [x for x in items if x["status"] in ("qualified", "claimed")
                 and x["id"] not in (handled_ids or set())]
    if len(open_work) > MAX_READY:
        raise ValueError("too many active opportunities; prioritize before adding more")
    return items


def rank(items, handled_ids=None):
    active = [x for x in items if x["status"] == "qualified"
              and x["id"] not in (handled_ids or set())]
    return sorted(active, key=lambda x: (-x["priority_score"], x["created_at_utc"], x["id"]))


def health(items, now=None, handled_ids=None):
    """Read-only queue watchdog. Expired claims need remote reconciliation, never blind replay."""
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    handled_ids = handled_ids or set()
    stale = sorted([
        x["id"] for x in items if x["id"] not in handled_ids and x["status"] == "claimed"
        and parse_utc(x["claim_until_utc"], "claim_until_utc") <= now
    ])
    return {
        "qualified": sum(x["status"] == "qualified" and x["id"] not in handled_ids for x in items),
        "already_handled_via_immutable_outcomes": sum(x["id"] in handled_ids for x in items),
        "claimed": sum(x["status"] == "claimed" and x["id"] not in handled_ids for x in items),
        "completed": sum(x["status"] == "completed" for x in items),
        "blocked": sum(x["status"] == "blocked" for x in items),
        "expired_claim_ids": stale,
        "needs_attention": bool(stale),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default="ops/agent-queue")
    parser.add_argument("--outcomes", default="ops/outcomes")
    parser.add_argument("--mode", choices=("validate", "rank", "health"), default="validate")
    args = parser.parse_args()
    handled = handled_lead_ids(args.outcomes)
    items = inventory(args.queue, handled_ids=handled)
    if args.mode == "rank":
        print(json.dumps([{"id": x["id"], "priority_score": x["priority_score"],
                           "proposed_action": x["proposed_action"],
                           "evidence_url": x["evidence_url"]} for x in rank(items, handled)], indent=2))
    elif args.mode == "health":
        print(json.dumps(health(items, handled_ids=handled), indent=2))
    else:
        print(json.dumps({"result": "valid", "items": len(items),
                          "ready": len(rank(items, handled))}))


if __name__ == "__main__":
    main()
