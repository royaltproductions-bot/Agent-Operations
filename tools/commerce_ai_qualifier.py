#!/usr/bin/env python3
"""Independent deterministic qualification of public paid-work candidates.

GitHub Models retired July 30, 2026. NEVER claim an LLM participated here:
this gate screens grounded funding/eligibility evidence, and will not message
any third party or declare prospective money to be earned revenue.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

SOURCE = Path("ops/discovery/triage.json")
REPORT = Path("/tmp/commerce-qualification-status.json")

def run():
    triage = json.loads(SOURCE.read_text(encoding="utf-8"))
    source = triage.get("results") or []
    if not isinstance(source, list):
        raise ValueError("Malformed discovery source")
    count = {"competing_or_unresolved": 0, "needs_funding_proof": 0, "verified_possible": 0}
    eligible = []
    for item in source:
        if item.get("decision") in {"competing_fix_or_pr", "incomplete_pr_pagination"}:
            count["competing_or_unresolved"] += 1
        elif not (item.get("funds_independently_verified") and item.get("qualifies_as_paid_order")):
            count["needs_funding_proof"] += 1
        else:
            count["verified_possible"] += 1
            eligible.append(item.get("url"))
    result = {
        "attribution": "github_actions_evidence_screen_not_llm",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_count": len(source),
        "screening": count,
        "candidate_urls_require_independent_claim_and_acceptance": eligible,
        "llm_available": False,
        "llm_note": "No independent AI model credentials configured; retired GitHub Models deliberately not used",
        "outreach_sent": 0,
        "new_revenue_verified": None
    }
    REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))

if __name__ == "__main__":
    run()
