#!/usr/bin/env python3
"""Public market observations for Ari's independent commercial judgment.

A visible paid offer is observable evidence of a market, not proof of a sale.
Published sales counters are optional context, never a precondition to research
or production. Private competitor transaction records are never required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from urllib.parse import urlsplit

SALE_COUNT=re.compile(
    r"(?i)(?<![\w])(?:(\d[\d,]*)\s+(?:sales|purchases|orders)\b|"
    r"\bsold\s+(\d[\d,]*)\s+(?:copies|units|times)\b)")
PRICE=re.compile(r"(?i)(?<![\w])(?:US\s*)?\$\s*(\d+(?:\.\d{1,2})?)\b")
BUY_CONTROL=re.compile(
    r"(?i)\b(?:add to (?:cart|basket)|cart|buy now|purchase|checkout|"
    r"subscribe|order now|buy|pay now|purchase now|hire now)\b")

EDITORIAL_TITLE=re.compile(
    r"(?i)\b(?:(?:best|top|compare|review(?:s)? of)\b.{0,60}"
    r"(?:apps?|software|tools?|products?|services?|templates?|alternatives?)|"
    r"(?:complete guide|how to|buyers? guide|comparison|roundup))\b")

def is_editorial_market_page(row):
    """Articles citing other sellers' prices are not storefront listings."""
    path=urlsplit(str(row.get("source_url") or "")).path.lower()
    title=str(row.get("title") or "")
    return bool(EDITORIAL_TITLE.search(title) or re.search(
        r"/(?:blog|blogs|articles|guides|resources|news|reviews|learn)/",path))

def observed_paid_listing(row):
    """Observe a publicly offered paid product; sales count is optional."""
    if not row.get("page_fetched") or is_editorial_market_page(row):
        return None
    body=" ".join(str(row.get("page_text") or "").split())
    if len(body)<120:
        return None
    for match in PRICE.finditer(body):
        # Require price and purchase action to appear together on a fetched
        # page; a remote snippet or a market-size claim is not an offer.
        window=body[max(0,match.start()-180):match.end()+220]
        if not BUY_CONTROL.search(window):
            continue
        price=Decimal(match.group(1))
        if price<=0:
            continue
        sale=SALE_COUNT.search(window)
        count=(int((sale.group(1) or sale.group(2)).replace(",",""))
               if sale else None)
        seller_match=re.search(r"(?i)\bby\s+([a-z0-9_.-]{3,50})\s+cart\b",window)
        return {
            "candidate_id":str(row.get("id") or ""),
            "url":str(row.get("source_url") or ""),
            "title":str(row.get("title") or "")[:180],
            "signal_type":"public_paid_offer",
            # This is a *displayed claim* when present, not a verified payment.
            "displayed_sales_count":count,
            "listed_price_usd":format(price,".2f"),
            "seller":seller_match.group(1).casefold() if seller_match else "",
            "evidence_excerpt":window[:420],
            "sale_excerpt":window[:420],
            "observed_at":datetime.now(timezone.utc).isoformat(),
        }
    return None


def paid_listing_evidence(rows):
    """Offer observed paid offers to the agent, without required sales counts."""
    found=[]
    seen=set()
    for row in rows:
        proof=observed_paid_listing(row)
        if proof and proof["url"] and proof["url"] not in seen:
            found.append(proof)
            seen.add(proof["url"])
    return found


def public_market_evidence(rows):
    """Expose fetched firsthand sources to judgment; never infer transactions.

    Direct paid offers are ranked first, while customer problems and competing
    products remain usable research signals even if sellers hide sales counts,
    prices, or checkout interfaces behind a dynamic storefront.
    """
    found=[]
    seen=set()
    for row in rows:
        url=str(row.get("source_url") or "")
        parsed=urlsplit(url)
        body=" ".join(str(row.get("page_text") or "").split())
        if (not row.get("page_fetched") or parsed.scheme not in ("http","https")
                or not parsed.hostname or len(body)<60 or url in seen):
            continue
        heading=str(row.get("title") or "")
        if re.search(r"(?i)\b(?:dictionary|word definition|meaning of|synonyms?|pronunciation)\b",heading):
            continue
        seen.add(url)
        offer=observed_paid_listing(row)
        if offer:
            source={**offer,"source_fetched":True}
        else:
            source={
                "candidate_id":str(row.get("id") or ""),
                "url":url,"title":str(row.get("title") or "")[:180],
                "signal_type":"unclassified_public_page",
                "source_fetched":True,
                "evidence_excerpt":body[:620],
                "listed_price_usd":None,
                "displayed_sales_count":None,
            }
        found.append(source)
    return sorted(found,key=lambda s:s["signal_type"]!="public_paid_offer")


def validated_market_proof(plan):
    proof=plan.get("market_validation") or {}
    if proof.get("status")=="agent_assessed_public_market":
        if proof.get("decision")!="pursue" or not str(proof.get("reason") or "").strip():
            return False
        observation=proof.get("observation") or {}
        try:
            return bool(
                str(observation.get("url") or "").startswith(("https://","http://"))
                and observation.get("evidence_excerpt")
                and (observation.get("source_fetched") is True or (
                    observation.get("signal_type")=="public_paid_offer"
                    and Decimal(str(observation.get("listed_price_usd") or 0))>0)))
        except (TypeError,ValueError,ArithmeticError):
            return False
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
