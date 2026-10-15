#!/usr/bin/env python3
"""Independent, read-only engine health audit. GitHub Actions is NOT a ChatGPT agent."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

TERMINAL = {"completed", "blocked", "failed", "completed_with_external_action_blocked",
            "completed_no_external_action", "complete", "partial"}
TIMESTAMP_KEYS = ("ended_at_utc", "completed_at_utc", "recorded_at_utc", "timestamp_utc", "started_at_utc", "start_at_utc")


def parsed_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (ValueError, OverflowError):
        return None


def records(root, subdir, attribution, now):
    folder = root / subdir
    valid = []
    starts = []
    errors = []
    if not folder.is_dir():
        return valid, ["directory absent: " + subdir], starts
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(path.name + ": " + type(exc).__name__)
            continue
        if data.get("attribution") != attribution:
            continue
        stamp = next((parsed_timestamp(data.get(key)) for key in TIMESTAMP_KEYS
                      if parsed_timestamp(data.get(key)) is not None), None)
        if stamp is None:
            errors.append(path.name + ": no valid terminal UTC timestamp")
            continue
        state = str(data.get("status") or data.get("state") or "").lower()
        if state in ("running", "started"):
            starts.append((stamp, path.name))
            continue
        if state not in TERMINAL:
            errors.append(path.name + ": unknown status")
            continue
        if stamp > now + timedelta(minutes=5):
            errors.append(path.name + ": timestamp is in future")
            continue
        valid.append((stamp, path.name, state))
    return valid, errors, starts


def run(root, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must have a timezone")
    now = now.astimezone(timezone.utc)
    info = {"attribution": "github_actions_monitor_not_chatgpt_agent",
            "checked_at_utc": now.isoformat().replace("+00:00", "Z"),
            "money_checked": False,
            "verified_new_revenue_usd": None,
            "external_publication_checked": False}
    issues = []
    for role, dirpath, attr in (
            ("scout", "ops/scout-runs", "scheduled_scout"),
            ("executor", "ops/runs", "scheduled_hourly")):
        matches, errs, starts = records(root, dirpath, attr, now)
        recent = [x for x in matches if x[0] >= now - timedelta(hours=24)]
        fresh = [x for x in matches if x[0] >= now - timedelta(hours=2)]
        info[role + "_terminal_records_last_24h"] = len(recent)
        info[role + "_start_receipts_last_24h"] = sum(
            now - timedelta(hours=24) <= t <= now for t, _ in starts)
        stale_starts = [
            name for t, name in starts
            if t <= now - timedelta(hours=2) and
            not any(t <= finished <= t + timedelta(hours=4) for finished, _, _ in matches)]
        info[role + "_possibly_orphaned_starts"] = stale_starts
        if stale_starts:
            issues.append(role + " has " + str(len(stale_starts)) + " stale start receipts without a terminal receipt")
        info[role + "_last_terminal_utc"] = (
            max(x[0] for x in matches).isoformat().replace("+00:00", "Z")
            if matches else None)
        info[role + "_fresh_within_2h"] = bool(fresh)
        info[role + "_record_errors"] = errs
        if not fresh:
            issues.append(role + " has no verified terminal log within 2 hours")
        if errs:
            issues.append(role + " has " + str(len(errs)) + " invalid/missing log condition(s)")
    queue = root / "ops/agent-queue"
    qualified = 0
    if queue.is_dir():
        for file in queue.glob("*.json"):
            try:
                if json.loads(file.read_text()).get("status") == "qualified":
                    qualified += 1
            except (OSError, ValueError):
                issues.append("invalid queue JSON: " + file.name)
    info["qualified_queue_files"] = qualified
    triage_path = root / "ops/discovery/triage.json"
    if triage_path.is_file():
        try:
            triage = json.loads(triage_path.read_text())
            info["bounty_hints_reviewed"] = triage.get("reviewed_items")
            info["bounty_competition_counts"] = triage.get("decision_counts")
            stamp = parsed_timestamp(triage.get("reviewed_at_utc"))
            info["triage_fresh_within_3h"] = bool(stamp and now - timedelta(hours=3) <= stamp <= now)
        except (ValueError, OSError):
            info["triage_fresh_within_3h"] = False
    else:
        info["triage_fresh_within_3h"] = False
    if not info["triage_fresh_within_3h"]:
        issues.append("discovery triage file is missing or older than 3 hours")
    info["reliability_status"] = "PASS" if not issues else "NOT_VERIFIED"
    info["issues"] = issues
    info["note"] = ("A passed monitor means scheduled agent receipts exist; it does NOT prove "
                    "a buyer was contacted, a commercial post went live or money arrived. "
                    "No Stripe/Gmail/AgentMail access is performed here.")
    return info


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    args = p.parse_args()
    print(json.dumps(run(args.root), indent=2))


if __name__ == "__main__":
    main()
