#!/usr/bin/env python3
"""Gambit first-look marketplace check, using ONLY the Python standard library.

Runs directly after GitHub checkout, before pip, Ollama, cache restoration or
tests. Records each new bounty ID/version immediately in the existing durable
state. Buyer details are deliberately *not* saved unencrypted; the main worker
retrieves and encrypts them as soon as dependencies are ready.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

STATE_PATH = Path("ops/gambit/state.json")
BASE = "https://api.trybounty.ai"
MAX_PAGES = 20


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def open_listing_pages(api_key):
    """Fail closed on incomplete pagination; never mistake an error for empty."""
    found, cursor = [], None
    for _ in range(MAX_PAGES):
        endpoint = BASE + "/v1/agent/bounties?status=open&limit=100"
        if cursor:
            endpoint += "&cursor=" + urllib.parse.quote(cursor, safe="")
        request = urllib.request.Request(
            endpoint, headers={"Authorization": "Bearer " + api_key,
                               "Accept": "application/json",
                               "User-Agent": "AriOperations-Gambit/1.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            page = json.load(response)
        if not isinstance(page, dict) or not isinstance(page.get("bounties"), list):
            raise ValueError("invalid_market_page")
        found.extend(page["bounties"])
        if not page.get("has_more"):
            return found
        cursor = page.get("next_cursor")
        if not cursor:
            raise ValueError("incomplete_market_pagination")
    raise ValueError("market_page_limit")


def capture_ids(state, opened):
    """Idempotent. Never overwrite existing decisions, bids or encrypted terms."""
    state.setdefault("bounties", {})
    archive = state.setdefault("bounty_archive", {})
    changed = 0
    for item in opened:
        if not isinstance(item, dict):
            continue
        bounty_id = item.get("_id")
        if not isinstance(bounty_id, str) or not bounty_id:
            continue
        version = item.get("version")
        if bounty_id not in archive:
            archive[bounty_id] = {
                "first_detected_at": timestamp(), "last_seen_open_at": timestamp(),
                "last_known_status": "open", "version": version, "versions": {}}
            changed += 1
        # Preserve all prior bid/offer outcomes. New versions are picked up by
        # the main scan without altering earlier decisions here.
        if bounty_id not in state["bounties"]:
            state["bounties"][bounty_id] = {
                "action": "evaluation_pending", "version": version, "at": timestamp()}
            changed += 1
    return changed


def run():
    api_key = os.getenv("BOUNTY_AGENT_API_KEY")
    if not api_key:
        print(json.dumps({"early_market_scan": "not_connected"}))
        return
    opened = open_listing_pages(api_key)
    state = (json.loads(STATE_PATH.read_text(encoding="utf-8"))
             if STATE_PATH.exists() else {"schema": 1, "bounties": {}})
    if state.get("schema") != 1:
        raise ValueError("incompatible_state")
    changed = capture_ids(state, opened)
    if changed:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
    print(json.dumps({"early_market_scan": "ok", "open": len(opened),
                      "new_record_fields": changed}))


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # Preserve the ordinary full worker as a fallback; log no secret or
        # private response body on a temporary early connection failure.
        print(json.dumps({"early_market_scan": "error",
                          "reason": type(exc).__name__}))
