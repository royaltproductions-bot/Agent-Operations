"""Hourly public or authenticated discovery on the approved free marketplace.

Agent keys can read suggested work and submit bids, but own-bid reconciliation
requires an operator session. Until that is available, keep submissions manual.
No model calls, account creation, or automatic buyer commitments occur here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from tools import gambit_credentials
import urllib.error
import urllib.request

URL = "https://www.bountyboard.work/api/bounties?status=OPEN&limit=20"
MARKETPLACE = "bountyboard"
REFRESH = timedelta(hours=1)


def _now():
    return datetime.now(timezone.utc)


def _due(record, now):
    try:
        last = datetime.fromisoformat(str(record.get("checked_at") or ""))
        return last.tzinfo is None or now - last >= REFRESH
    except (TypeError, ValueError):
        return True


def observe(state, opener=None, now=None):
    now = now or _now()
    market = state.setdefault("marketplaces", {}).setdefault(MARKETPLACE, {})
    if not _due(market, now):
        return market
    try:
        key = gambit_credentials.load()
    except Exception:
        # Do not disclose cryptographic material or exception content to public state.
        market.update(checked_at=now.isoformat(), status="credential_unavailable",
                      bidding_status="credential_unavailable")
        return market
    headers = {"Accept": "application/json", "User-Agent": "ari-gambit/1.0"}
    url = URL
    if key:
        url = f"https://www.bountyboard.work/api/agents/{gambit_credentials.AGENT_ID}/suggested"
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            payload = json.loads(response.read(500_000).decode("utf-8"))
        rows = payload if isinstance(payload, list) else (
            payload.get("bounties") or payload.get("data") or payload.get("items") or [])
        if not isinstance(rows, list):
            raise ValueError("bountyboard_invalid_payload")
        observed = []
        for item in rows[:20]:
            if not isinstance(item, dict):
                continue
            ident = item.get("id") or item.get("_id")
            if ident is None:
                continue
            observed.append({
                "marketplace": MARKETPLACE,
                "id": str(ident)[:120],
                "title": str(item.get("title") or "")[:180],
                "status": str(item.get("status") or "OPEN")[:40],
                "budget_cents": item.get("budgetCents") or item.get("budget_cents")
                    or item.get("amount") or item.get("reward"),
                "currency": str(item.get("currency") or "USD")[:10],
                "posted_at": item.get("createdAt"),
                "allow_agents": item.get("allowAgents"),
            })
        market.update(
            checked_at=now.isoformat(), status="observed_authenticated" if key else "observed_public",
            agent_id=gambit_credentials.AGENT_ID if key else None,
            open_count=len(observed), listings=observed,
            bidding_status="operator_reconciliation_required" if key else "credential_not_connected")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, json.JSONDecodeError):
        market.update(checked_at=now.isoformat(), status="observation_failed",
                      bidding_status="operator_reconciliation_required" if key else "credential_not_connected")
    return market
