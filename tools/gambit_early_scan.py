#!/usr/bin/env python3
"""Gambit first-look marketplace check, using ONLY the Python standard library.

Runs directly after GitHub checkout, before pip, Ollama, cache restoration or
tests. Records each new bounty ID/version immediately in the existing durable
state. Buyer details are deliberately *not* saved unencrypted; the main worker
retrieves and encrypts them as soon as dependencies are ready.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
import sys
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from tools import gambit_commercial as commercial

STATE_PATH = Path("ops/gambit/state.json")
BASE = "https://api.trybounty.ai"
MAX_PAGES = 20
ACCOUNT_POLICY_RECHECK_VERSION = "account-research-v2"


def account_policy_recheck_due(entry):
    """Reconsider old account-rule rejects once after the improved classifier.

    Never revisit all historical passes or interfere with active bids.
    """
    return (entry.get("action") == "pass_unsupported" and
            entry.get("reason") == "authenticated_account_operation" and
            entry.get("account_policy_review_version") != ACCOUNT_POLICY_RECHECK_VERSION)



def asked_recheck_due(entry, now=None):
    """Occasionally inspect a pending pre-bid buyer thread without inference.

    No private messages are stored here; the worker reads them through the
    authenticated marketplace and records only the last handled message ID.
    """
    if entry.get("action") != "asked":
        return False
    checked = entry.get("last_asked_review_at") or entry.get("at")
    return minutes_since({"checked": checked}, "checked",
                         now or datetime.now(timezone.utc)) >= 360


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def open_listing_pages(api_key):
    """Use the retry-capable client for all live marketplace listings."""
    return other_pages(api_key, "/v1/agent/bounties?status=open&limit=100",
                       "bounties")


def record_market_observation(state, opened):
    """Persist every real open-list fetch, even when the market is unchanged."""
    fetched_at = timestamp()
    created = []
    for item in opened:
        if not isinstance(item, dict):
            continue
        for field in commercial.TIME_FIELDS:
            parsed = commercial.utc_time(item.get(field))
            if parsed:
                created.append(parsed)
                break
    observation = {
        "last_open_count": len(opened),
        "newest_created_at": max(created).isoformat() if created else None,
        "fetched_at": fetched_at,
    }
    state["market_observation"] = observation
    history = state.setdefault("market_observation_history", [])
    history.append(observation)
    state["market_observation_history"] = history[-288:]
    return observation


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
        changed += commercial.observe(state, item)
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



def other_pages(api_key, endpoint, collection, initial_cursor=None):
    result, cursor = [], initial_cursor
    for _ in range(MAX_PAGES):
        suffix = ("&" if "?" in endpoint else "?") + "cursor=" + urllib.parse.quote(cursor, safe="") if cursor else ""
        request = urllib.request.Request(
            BASE + endpoint + suffix,
            headers={"Authorization": "Bearer " + api_key, "Accept": "application/json",
                     "Cache-Control": "no-cache", "Pragma": "no-cache",
                     "User-Agent": "AriOperations-Gambit/1.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=35) as response:
                    page = json.load(response)
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 410}:
                    try:
                        payload = json.load(exc)
                    except (OSError, ValueError, TypeError):
                        payload = {}
                    if (payload.get("error") or {}).get("code") == "INVALID_CURSOR":
                        raise ValueError("invalid_event_cursor") from None
                retryable = (not isinstance(exc, urllib.error.HTTPError) or
                             exc.code in {429, 502, 503, 504})
                if retryable and attempt < 2:
                    if isinstance(exc, urllib.error.HTTPError):
                        try:
                            delay = float(exc.headers.get("Retry-After", "1"))
                        except (ValueError, TypeError):
                            delay = 2 ** attempt
                    else:
                        delay = 2 ** attempt
                    time.sleep(max(1, delay))
                    continue
                raise
        if not isinstance(page, dict) or not isinstance(page.get(collection), list):
            raise ValueError("invalid_live_response")
        result.extend(page[collection])
        if not page.get("has_more"):
            return result
        cursor = page.get("next_cursor")
        if not cursor:
            raise ValueError("incomplete_pagination")
    raise ValueError("page_limit_reached")


def minutes_since(state, field, now):
    try:
        date = datetime.fromisoformat(state.get(field) or "")
        return (now - date).total_seconds() / 60 if date.tzinfo else float("inf")
    except (ValueError, TypeError):
        return float("inf")


def retry_ready(entry, now):
    if entry.get("action") not in {"retry_evaluation", "retry_search", "proposal_fallback_pending", "budget_wait", "retry_fulfillment", "quality_rejected"}:
        return False
    return bool(entry.get("retry_at")) and minutes_since(
        {"at": entry["retry_at"]}, "at", now) >= 0


def fulfillment_cooling(entry, now=None):
    """Replayed events must not consume all delivery retries immediately."""
    if entry.get("action") not in {"retry_fulfillment", "quality_rejected"}:
        return False
    return minutes_since({"at": entry.get("retry_at")}, "at",
                         now or datetime.now(timezone.utc)) < 0


def commercial_guardrail_reconciliation_due(state):
    """Wake the full worker immediately for known owner-guardrail violations."""
    records=state.get("bounties") or {}
    commercial_rows=state.get("commercial") or {}
    verified_paid=bool(state.get("settlements")) or any(
        (entry or {}).get("action") in {"hired","submitted","accepted","settled"}
        for entry in records.values())
    cap=None if verified_paid else 15_000
    for bounty_id,entry in records.items():
        if (entry or {}).get("action") not in {"bid_sent","bid_active"}:
            continue
        row=commercial_rows.get(bounty_id) or {}
        if row.get("platform_status") in {"canceled","closed"}:
            return True
        amount=row.get("bid_amount_cents")
        if (cap is not None and isinstance(amount,int)
                and not isinstance(amount,bool) and amount>cap):
            return True
    return False


def admission(state, opened, held, events, now=None):
    """TryBounty is the authority; local state only deduplicates known work."""
    now = now or datetime.now(timezone.utc)
    records = state.get("bounties") or {}
    awake_events = [event for event in events if not fulfillment_cooling(
        records.get(str((event.get("data") or {}).get("bounty_id"))) or {}, now)]
    actionable_events = {"bounty.available", "bounty.updated", "bounty.claimed",
                         "work.message.created", "submission.verification_failed",
                         "discussion.user_replied"}
    triggered = {str(e.get("data", {}).get("bounty_id"))
                 for e in awake_events if isinstance(e, dict)
                 and e.get("type") in actionable_events
                 and isinstance(e.get("data"), dict) and e["data"].get("bounty_id")}
    pending_due = {bid for bid in state.get("deferred_bounty_ids") or []
                   if not fulfillment_cooling(records.get(bid) or {}, now)}
    triggered.update(pending_due)
    new = [item for item in opened if isinstance(item, dict) and not commercial.is_sample(
        item, state.get("commercial", {}).get(str(item.get("_id")))) and (
        str(item.get("_id")) in triggered or
        (records.get(str(item.get("_id"))) or {}).get("version") != item.get("version") or
        (records.get(str(item.get("_id"))) or {}).get("action") == "evaluation_pending" or
        account_policy_recheck_due(records.get(str(item.get("_id"))) or {}) or
        asked_recheck_due(records.get(str(item.get("_id"))) or {}, now) or
        retry_ready(records.get(str(item.get("_id"))) or {}, now))]
    active = [item for item in held if isinstance(item, dict) and
              not fulfillment_cooling(records.get(str(item.get("_id"))) or {}, now) and
              item.get("status") == "claimed" and (
              str(item.get("_id")) in triggered or
              (records.get(str(item.get("_id"))) or {}).get("action") not in {
                  "submitted", "awaiting_info", "quality_rejected", "retry_fulfillment",
                  "needs_operator_review"} or
              retry_ready(records.get(str(item.get("_id"))) or {}, now) or
              (records.get(str(item.get("_id"))) or {}).get("action") == "awaiting_info" and
              minutes_since(records.get(str(item.get("_id"))) or {}, "at", now) >= 24*60 or
              (records.get(str(item.get("_id"))) or {}).get("reason") ==
              "website_visual_quality_unverified")]
    has_bid = any(x.get("action") in {"bid_sent", "bid_active"} for x in records.values())
    listed = {str(item.get("_id")) for item in opened + held if isinstance(item, dict)}
    recovery_due = any(bid not in listed and (
        entry.get("action") == "evaluation_pending" or
        entry.get("action") in {"retry_evaluation", "retry_search"} and retry_ready(entry, now))
        for bid, entry in records.items())
    guardrail_due = commercial_guardrail_reconciliation_due(state)
    ledger_due = minutes_since(state, "last_bid_reconciliation", now) >= (15 if has_bid else 55)
    cursor_due = minutes_since(state, "last_check", now) >= 360
    return {"needs_run": bool(new or active or awake_events or recovery_due or
                               pending_due or guardrail_due or ledger_due or cursor_due),
            "needs_model": bool(new or active),
            "needs_production_model": bool(active),
            "new_or_changed": len(new), "claimed_ready": len(active),
            "pending_events": len(events), "recovery_due": recovery_due,
            "commercial_guardrail_due": guardrail_due}


def output(fields):
    if not os.environ.get("GITHUB_OUTPUT"):
        return
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        for name, value in fields.items():
            handle.write(name + "=" + (str(value).lower() if isinstance(value, bool)
                                        else str(value)) + "\n")



def run(probe_mode=False):
    api_key = os.getenv("BOUNTY_AGENT_API_KEY")
    if not api_key:
        if probe_mode:
            # Missing credentials are an operational failure, not an empty market.
            raise RuntimeError("missing_trybounty_api_key")
        print(json.dumps({"early_market_scan": "not_connected"}))
        return
    opened = open_listing_pages(api_key)
    state = (json.loads(STATE_PATH.read_text(encoding="utf-8"))
             if STATE_PATH.exists() else {"schema": 1, "bounties": {}})
    if state.get("schema") != 1:
        raise ValueError("incompatible_state")
    changed = capture_ids(state, opened)
    observation = record_market_observation(state, opened)
    # Every market fetch is an operational measurement, not a no-op. Persist
    # it even when there are no newly discovered listings.
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(STATE_PATH)
    funnel = STATE_PATH.with_name("funnel.json")
    temp_funnel = funnel.with_suffix(".tmp")
    temp_funnel.write_text(
        json.dumps(commercial.summarize(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    temp_funnel.replace(funnel)
    # Expose newly discovered IDs separately from the observation checkpoint.
    if probe_mode and changed:
        output({"changed": True})
    if probe_mode:
        held = other_pages(api_key,
                           "/v1/agent/bounties?status=claimed,submitted,reviewing,settling,disputed&limit=100",
                           "bounties")
        try:
            events = other_pages(api_key, "/v1/agent/events?limit=100",
                                 "events", state.get("events_cursor"))
        except ValueError as error:
            # Expired cursors are recoverable; invalid requests are not empty work.
            if str(error) != "invalid_event_cursor":
                raise
            events = [{"type": "cursor_recovery", "data": {}}]
        status = admission(state, opened, held, events)
        output({"connected": True, "changed": bool(changed), **status})
        print(json.dumps({"market_probe": "live", "checked_at": timestamp(),
                          "released_open_total": len(opened),
                          "market_observation": observation, **status}))
        return
    print(json.dumps({"early_market_scan": "ok", "checked_at": timestamp(),
                      "source": "trybounty_live_api", "released_open_total": len(opened),
                      "new_record_fields": changed,
                      "queued_for_evaluation": sum(
                          (state["bounties"].get(str(item.get("_id"))) or {}).get("action")
                          == "evaluation_pending" for item in opened),
                      "known_unchanged": sum(
                          (state["bounties"].get(str(item.get("_id"))) or {}).get("action")
                          not in ("evaluation_pending", "retry_evaluation") for item in opened)}))


if __name__ == "__main__":
    probe_mode = "--probe" in sys.argv
    try:
        run(probe_mode=probe_mode)
    except Exception as exc:
        print(json.dumps({"early_market_scan": "error",
                          "reason": type(exc).__name__}))
        if probe_mode:
            sys.exit(1)
