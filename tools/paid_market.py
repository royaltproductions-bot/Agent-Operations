#!/usr/bin/env python3
"""Conservative, source-grounded evidence that comparable digital goods sold.

An expressed frustration, product advertisement, view count, or unverified
seller testimonial never qualifies. Only a fetched, purchasable item page
with an explicit item-level completed-sales count enters the product gate.
The market, product form, and source are not predetermined.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re

SALE_COUNT=re.compile(
    r"(?i)(?<![\w])(?:(\d[\d,]*)\s+(?:sales|purchases|orders)\b|"
    r"\bsold\s+(\d[\d,]*)\s+(?:copies|units|times)\b)")
PRICE=re.compile(r"(?i)(?<![\w])(?:US\s*)?\$\s*(\d+(?:\.\d{1,2})?)\b")
BUY_CONTROL=re.compile(r"(?i)\b(?:add to cart|cart|buy now|purchase|checkout)\b")
def observed_paid_listing(row):
    """Return verifiable listing facts, or None. Search snippets never qualify."""
    if not row.get("page_fetched"):
        return None
    body=" ".join(str(row.get("page_text") or "").split())
    if len(body)<120 or not BUY_CONTROL.search(body):
        return None
    for match in SALE_COUNT.finditer(body):
        number=int((match.group(1) or match.group(2)).replace(",",""))
        if number<=0:
            continue
        # Price, purchase mechanism, and sales must refer to the *same item*.
        # A blog claiming an industry had 1M sales does not meet this test.
        window=body[max(0,match.start()-250):match.end()+160]
        price_matches=list(PRICE.finditer(window))
        if not price_matches or not BUY_CONTROL.search(window):
            continue
        price=Decimal(price_matches[-1].group(1))
        if price<=0:
            continue
        seller_match=re.search(r"(?i)\bby\s+([a-z0-9_.-]{3,50})\s+cart\b",
                               window)
        return {
            "candidate_id":str(row.get("id") or ""),
            "url":str(row.get("source_url") or ""),
            "title":str(row.get("title") or "")[:180],
            "completed_sales":number,
            "listed_price_usd":format(price,".2f"),
            "seller":seller_match.group(1).casefold() if seller_match else "",
            "sale_excerpt":window[:420],
            "observed_at":datetime.now(timezone.utc).isoformat(),
        }
    return None


def paid_listing_evidence(rows):
    """Offer every authentic paid listing to the model for market judgment."""
    found=[]
    seen=set()
    for row in rows:
        proof=observed_paid_listing(row)
        if proof and proof["url"] and proof["url"] not in seen:
            found.append(proof)
            seen.add(proof["url"])
    return found


def validated_market_proof(plan):
    proof=plan.get("market_validation") or {}
    if proof.get("status")=="agent_assessed_paid_listing":
        if proof.get("decision")!="pursue" or not proof.get("reason"):
            return False
        listings=[proof.get("listing") or {}]
    elif proof.get("status")=="two_paid_comparables_verified":
        # Previously completed, more restrictive validations remain valid.
        listings=proof.get("comparables") or []
    else:
        return False
    try:
        return bool(listings) and all(
            c.get("url") and int(c.get("completed_sales") or 0)>0
            and Decimal(str(c.get("listed_price_usd") or 0))>0
            and c.get("sale_excerpt") for c in listings)
    except (TypeError,ValueError,ArithmeticError):
        return False
