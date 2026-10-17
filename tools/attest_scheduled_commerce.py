#!/usr/bin/env python3
"""Produce a privacy-safe receipt ONLY for the real GitHub schedule event."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

def main():
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event != "schedule":
        print("No schedule event; no unattended-operation proof issued.")
        return
    rid = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not rid.isdigit() or not attempt.isdigit():
        raise RuntimeError("GitHub scheduled run identity missing")
    src = Path(os.environ.get("COMMERCE_STATUS_PATH", "/tmp/commerce-live-status.json"))
    data = json.loads(src.read_text()) if src.is_file() else {}
    valid = (data.get("actor") == "native_github_actions_not_chatgpt"
             and data.get("status") == "completed"
             and data.get("stripe_ready") is True
             and data.get("agentmail_ready") is True
             and isinstance(data.get("paid_sessions_found"), int))
    if valid:
        # A completed data read is required before proof of external auth.
        commerce_status = "LIVE_PROVIDER_CHECKS_SUCCEEDED"
    else:
        commerce_status = "NOT_VERIFIED_OR_BLOCKED"
    result = {
        "schema_version": 1,
        "trigger": "schedule",
        "run_id": rid,
        "run_attempt": int(attempt),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "commerce_status": commerce_status,
        "independent_stripe_and_agentmail_auth_verified": valid,
        "paid_checkout_sessions": data.get("paid_sessions_found") if valid else None,
        "new_deliveries": data.get("new_deliveries") if valid else None,
        "buyer_followups": data.get("buyer_outreach") if valid else None,
        "verified_new_revenue_usd": None,
        "important": ("This proves an independent clock-triggered provider check, "
                      "not independent client acquisition, real buyer payment or order fulfillment.")
    }
    out = Path("ops/scheduled-commerce-attestations") / (rid + "-" + attempt + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(out), "trigger": event,
                      "commerce_status": commerce_status,
                      "provider_connections": valid,
                      "orders": result["paid_checkout_sessions"]}))

if __name__ == "__main__":
    main()
