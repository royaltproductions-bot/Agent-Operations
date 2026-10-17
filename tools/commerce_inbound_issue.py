#!/usr/bin/env python3
"""Respond to explicitly opt-in PUBLIC GitHub issue inquiries, with no secrets.

This is not prospecting, spam, payment collection or delivery.
The public reply never asks for customer data to be posted to the issue.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys

PREFIX = "[VendorSettle inquiry]"
INBOX = "ari-operations@agentmail.to"
CHECKOUT = "https://buy.stripe.com/eVq9AS63KcrdfUe3AP7Re00"
MARKER = "<!-- automated-vendorsettle-inquiry-response-v1 -->"

def reply_for_issue(issue):
    if not isinstance(issue, dict) or issue.get("pull_request"):
        return None
    title = str(issue.get("title") or "")
    if not title.startswith(PREFIX):
        return None
    # A generic public response avoids echoing arbitrary customer-entered text.
    # A user can ask about terms privately using the already-published inbox.
    return (MARKER + "\n"
        "Thanks for asking about VendorSettle Desk. This is an automatically "
        "generated acknowledgement, not a claim that an order was placed.\n\n"
        "The existing service prepares a vendor settlement summary plus a "
        "transaction-level audit CSV for **$49 per report**, covering one "
        "reporting period with up to **500 anonymized sales rows**. "
        "Typical delivery is within three business days **after receipt of usable data**. "
        "It does not transfer money or provide tax/accounting advice.\n\n"
        "To proceed, see the [scope, required CSV format, and complete terms]"
        "(https://github.com/royaltproductions-bot/Agent-Operations/blob/main/products/vendorsettle-desk/README.md). "
        "The [Stripe checkout](" + CHECKOUT + ") is an existing secure payment link; "
        "buy only after reviewing the published scope. "
        "After paying, send your *anonymized* CSV with a payment confirmation ID "
        "to **" + INBOX + "**. **Do not post customer files, addresses, "
        "account numbers, or other sensitive details in this public issue.**\n\n"
        "For questions or unusual files, email the same address before paying."
    )

def main():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).is_file():
        raise SystemExit("GITHUB_EVENT_PATH missing; no external reply")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    result = reply_for_issue(event.get("issue"))
    if not result:
        print("Not an opted-in VendorSettle inquiry; no response.")
        return
    destination = Path(os.environ.get("COMMERCE_REPLY_PATH", "/tmp/commerce-issue-reply.md"))
    destination.write_text(result, encoding="utf-8")
    print("Opt-in commerce inquiry response generated; no personal data exposed.")

if __name__ == "__main__":
    main()
