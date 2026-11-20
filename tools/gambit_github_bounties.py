"""Public GitHub bounty discovery; never claim without verified platform terms.

This is a second-source observer, not a claim/submission client.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import re
import urllib.parse
import urllib.request

MARKETPLACE = "github_bounty"
URL = ("https://api.github.com/search/issues?q="
       + urllib.parse.quote("is:issue is:open (bounty OR reward OR paid) ", safe="")
       + "&sort=created&order=desc&per_page=30")
REFRESH = timedelta(hours=2)
MAX_CENTS = 30000
MIN_CENTS = 2000
MAX_AGE = timedelta(days=14)


def parse_reward_cents(item):
    """Parse explicit dollar offers; never infer a payout from vague 'bounty' labels."""
    labels = item.get("labels") or []
    candidates = []
    for label in labels:
        name = str(label.get("name") or "") if isinstance(label, dict) else str(label)
        candidates.extend(re.findall(r"(?i)(?:bounty|reward)[: ]*\$\s*(\d+(?:\.\d{1,2})?)", name))
    text = str(item.get("title") or "") + "\n" + str(item.get("body") or "")[:12000]
    candidates.extend(re.findall(r"(?<![\w])\$\s*(\d+(?:\.\d{1,2})?)(?![\d])", text))
    values = [round(float(v) * 100) for v in candidates]
    eligible = [v for v in values if MIN_CENTS <= v <= MAX_CENTS]
    return min(eligible) if eligible else None


def rejection_reason(item, now):
    if not isinstance(item, dict):
        return "invalid_result"
    if item.get("state") not in (None, "open"):
        return "closed"
    if not str(item.get("html_url") or "").startswith("https://github.com/"):
        return "invalid_url"
    try:
        updated = datetime.fromisoformat(str(item.get("updated_at") or "").replace("Z", "+00:00"))
        if updated.tzinfo is None or now - updated > MAX_AGE:
            return "stale_over_14_days"
    except (ValueError, TypeError):
        return "missing_updated_at"
    if parse_reward_cents(item) is None:
        return "no_explicit_20_to_300_usd_reward"
    return None


def observe(state, opener=None, now=None):
    now = now or datetime.now(timezone.utc)
    market = state.setdefault("marketplaces", {}).setdefault(MARKETPLACE, {})
    try:
        last = datetime.fromisoformat(market.get("checked_at") or "")
        if last.tzinfo and now - last < REFRESH:
            return market
    except (ValueError, TypeError):
        pass
    search_url = URL.replace("&sort=", urllib.parse.quote(" updated:>=" + (now - MAX_AGE).date().isoformat(), safe="") + "&sort=")
    request = urllib.request.Request(search_url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ARI-Gambit-bounty-observer/1.0"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            data = json.loads(response.read(800000).decode("utf-8"))
        items = data.get("items") or []
        if not isinstance(items, list):
            raise ValueError("invalid_github_search")
        listings = []
        rejected = []
        for item in items:
            reason = rejection_reason(item, now)
            if reason:
                rejected.append({"id": str(item.get("id") or "")[:40] if isinstance(item, dict) else "",
                                 "reason": reason})
                continue
            cents = parse_reward_cents(item)
            url = str(item.get("html_url") or "")
            listings.append({
                "marketplace": MARKETPLACE,
                "id": str(item.get("id") or "")[:40],
                "url": url[:350],
                "title": str(item.get("title") or "")[:180],
                "budget_cents": cents,
                "currency": "USD",
                "posted_at": item.get("created_at"),
                "status": "open",
                "claim_status": "unverified_platform_terms",
            })
        market.update(checked_at=now.isoformat(), status="observed_public",
                      search_result_count=len(items), open_count=len(listings),
                      listings=listings, rejected=rejected,
                      bidding_status="platform_native_claim_not_verified")
    except (OSError, ValueError, TimeoutError):
        market.update(checked_at=now.isoformat(), status="observation_failed",
                      bidding_status="platform_native_claim_not_verified")
    return market
