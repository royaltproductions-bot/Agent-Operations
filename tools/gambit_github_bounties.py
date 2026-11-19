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
       + urllib.parse.quote("is:issue is:open label:bounty", safe="")
       + "&sort=created&order=desc&per_page=30")
REFRESH = timedelta(hours=2)
MAX_CENTS = 15000


def parse_reward_cents(item):
    labels = item.get("labels") or []
    for label in labels:
        name = str(label.get("name") or "") if isinstance(label, dict) else str(label)
        match = re.fullmatch(r"(?i)bounty:\s*\$(\d+(?:\.\d{1,2})?)", name)
        if match:
            cents = round(float(match.group(1)) * 100)
            return cents if 0 < cents <= MAX_CENTS else None
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
    request = urllib.request.Request(URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ARI-Gambit-bounty-observer/1.0"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            data = json.loads(response.read(800000).decode("utf-8"))
        items = data.get("items") or []
        if not isinstance(items, list):
            raise ValueError("invalid_github_search")
        listings = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cents = parse_reward_cents(item)
            url = str(item.get("html_url") or "")
            if cents is None or not url.startswith("https://github.com/"):
                continue
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
                      listings=listings,
                      bidding_status="platform_native_claim_not_verified")
    except (OSError, ValueError, TimeoutError):
        market.update(checked_at=now.isoformat(), status="observation_failed",
                      bidding_status="platform_native_claim_not_verified")
    return market
