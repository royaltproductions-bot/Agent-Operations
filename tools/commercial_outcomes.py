#!/usr/bin/env python3
"""Provider-grounded commercial outcomes for the autonomous commerce engine."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

from tools.connected_commerce_tools import agent_get, verified_paid_totals

def metrics(history=None):
    receipts=history or []

    stripe={"status":"not_checked","verified_paid_sessions":None,
            "confirmed_gross_usd":None}
    if os.environ.get("STRIPE_SECRET_KEY"):
        try:
            totals=verified_paid_totals()
            stripe={"status":"verified_live_stripe",**totals}
        except (RuntimeError,ValueError,KeyError,TypeError):
            stripe["status"]="stripe_provider_check_failed"
    else:
        stripe["status"]="stripe_credentials_not_exposed_to_this_run"

    inbound={"status":"not_checked","verified_buyer_replies":None}
    if os.environ.get("AGENTMAIL_API_KEY"):
        try:
            legitimate_ids=set()
            for receipt in receipts:
                for action in receipt.get("actions",[]):
                    if (action.get("result")=="sent_invited_proposal"
                            and isinstance(action.get("source"),str)):
                        legitimate_ids.add(
                            hashlib.sha256(action["source"].encode()).hexdigest()[:18]
                        )
            messages=agent_get("messages",{"limit":75})
            found=set()
            for msg in messages.get("messages") or []:
                labels=set(msg.get("labels") or [])
                if "received" not in labels or labels.intersection({"spam","blocked","trash"}):
                    continue
                if (msg.get("agent_armor") or {}).get("status") in {
                    "pending","blocked","flagged","review"
                }:
                    continue
                subject=str(msg.get("subject") or "")
                match=re.search(r"\[([a-f0-9]{18})\]",subject)
                if match and match.group(1) in legitimate_ids:
                    found.add(match.group(1))
            inbound={"status":"verified_live_agentmail",
                     "verified_buyer_replies":len(found)}
        except (RuntimeError,ValueError,TypeError,KeyError):
            inbound["status"]="agentmail_provider_check_failed"
    else:
        inbound["status"]="agentmail_credentials_not_exposed_to_this_run"

    delivery_root=Path("ops/commerce-deliveries")
    delivered=len(list(delivery_root.glob("*.json"))) if delivery_root.is_dir() else None

    return {
        "stripe":stripe,
        "buyer_correspondence":inbound,
        "fulfillment_receipt_files_present":delivered,
    }
