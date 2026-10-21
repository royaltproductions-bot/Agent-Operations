#!/usr/bin/env python3
"""Real-money funnel measurements from already connected Stripe and AgentMail.

All objects are read-only. Return ONLY aggregates with provenance: never
customer names/emails/session identifiers/transaction content or API tokens.
Income is collected live Stripe gross on a known fulfillment-ready offer,
NOT profit, new run revenue, funds available to withdraw, or buyer reach.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re

from tools.commerce_operator import (
    INBOX, find_payment_link, verified_paid_sessions, verified_unreversed_payment,
    agent_get,
)
from tools import commerce_portfolio as portfolio

def metrics(history=None):
    receipts=history or []
    stripe={"status":"not_checked","verified_paid_sessions":None,
            "confirmed_gross_usd":None,"new_revenue_this_run_usd":None}
    if os.environ.get("STRIPE_SECRET_KEY"):
        try:
            link=find_payment_link()
            if not link:
                stripe["status"]="owned_checkout_unavailable_or_inactive"
            else:
                paid=0
                for s in verified_paid_sessions(link):
                    if verified_unreversed_payment(s):
                        paid+=1
                stripe={"status":"verified_live_stripe","verified_paid_sessions":paid,
                        "confirmed_gross_usd":paid*49,
                        "new_revenue_this_run_usd":None,
                        "important":"Cumulative confirmed paid orders on the existing VendorSettle link only."}
        except (RuntimeError,ValueError,KeyError,TypeError):
            stripe["status"]="stripe_provider_check_failed"
    else:
        stripe["status"]="stripe_credentials_not_exposed_to_this_run"

    inbound={"status":"not_checked","verified_buyer_replies":None}
    if os.environ.get("AGENTMAIL_API_KEY"):
        try:
            # Public source links hashed into subjects are known ONLY if our
            # own externally sent inquiry receipt already exists.
            legitimate_ids=set()
            for receipt in receipts:
                for a in receipt.get("actions",[]):
                    if a.get("result")=="sent_invited_proposal" and isinstance(a.get("source"),str):
                        legitimate_ids.add(hashlib.sha256(a["source"].encode()).hexdigest()[:18])
            messages=agent_get("messages",{"limit":75})
            found=set()
            for m in messages.get("messages",[]):
                labels=set(m.get("labels") or [])
                if "received" not in labels or labels.intersection({"spam","blocked","trash"}):
                    continue
                if m.get("agent_armor",{}).get("status") in {"pending","blocked","flagged","review"}:
                    continue
                sub=str(m.get("subject") or "")
                matches=re.findall(r"\[([a-f0-9]{18})\]",sub)
                if matches and matches[0] in legitimate_ids:
                    found.add(matches[0])
            inbound={"status":"verified_live_agentmail",
                     "verified_buyer_replies":len(found),
                     "note":"Only replies referencing independently sent invited proposals; no private content logged."}
        except (RuntimeError,ValueError,TypeError,KeyError):
            inbound["status"]="agentmail_provider_check_failed"
    else:
        inbound["status"]="agentmail_credentials_not_exposed_to_this_run"

    delivery_root=Path("ops/commerce-deliveries")
    # Private fulfillment receipts are NEVER read or copied into the public
    # opportunity model; only counts if the separate commerce operator attested.
    delivered=None
    if delivery_root.exists():
        delivered=len(list(delivery_root.glob("*.json")))
    return {
        "stripe":stripe,"buyer_correspondence":inbound,
        "fulfillment_receipt_files_present":delivered,
        "merchant_scope":"existing_owned_checkout_only",
        "all_other_businesses_revenue_verified":None,
    }
