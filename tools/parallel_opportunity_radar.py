#!/usr/bin/env python3
"""Ten-lane, read-only public GitHub commercial opportunity radar.

One GitHub Actions runner, ten concurrent HTTP searches, zero purchased APIs.
Produces DISCOVERY ONLY; never promises a funded or exclusive bounty. No emails,
posts, checkouts, job claims, or other external mutations occur here.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

SEARCH_LANES = {
    "python": 'is:issue is:open bounty python',
    "javascript": 'is:issue is:open bounty javascript',
    "testing": 'is:issue is:open bounty testing',
    "documentation": 'is:issue is:open bounty documentation',
    "automation": 'is:issue is:open bounty automation',
    "data": 'is:issue is:open bounty csv data',
    "frontend": 'is:issue is:open bounty frontend',
    "integration": 'is:issue is:open bounty api integration',
    "cash": 'is:issue is:open "$100" bounty',
    "open-paid": 'is:issue is:open "paid" "task"',
}
DENY_REPOS = (
    "bounty-plaza", "opportunity-scanner", "opportunities", "bounty-radar",
    "jobs-index", "jobs-feed", "test",
)
CURRENCY_RE = re.compile(r'(?<!\w)\$\s*(\d{1,7}(?:,\d{3})*(?:\.\d{1,2})?)\b')
BIDDING_RE = re.compile(r'\b(bounty|reward|paid|funded|payout|usd|usdc|bounties)\b', re.I)
REQUIRED_SPEND_RE = re.compile(
    r'\b(fund (?:a |the |your )?task|pay (?:a |the )?fee|'
    r'entry fee|deposit required|buy (?:a |the )?subscription|'
    r'wallet(?: needs| must)|pay upfront)\b', re.I
)


def search_url(query, since_date):
    q = query + " created:>=" + since_date
    params = urllib.parse.urlencode({"q": q, "sort": "created", "order": "desc", "per_page": 25})
    return "https://api.github.com/search/issues?" + params


def github_search(query, since_date, token, opener=None):
    url = search_url(query, since_date)
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "RoyalT-Revenue-Radar/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    get = opener or urllib.request.urlopen
    try:
        with get(request, timeout=18) as response:
            data = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("GitHub search unavailable: " + type(exc).__name__) from None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("GitHub API returned unexpected search shape")
    return data["items"]


def amount_hint(text):
    vals = []
    for found in CURRENCY_RE.findall(text):
        amount = float(found.replace(",", ""))
        if 10 <= amount <= 250_000:
            vals.append(amount)
    return max(vals) if vals else None


def prospect(issue, lane):
    if not isinstance(issue, dict) or issue.get("pull_request"):
        return None
    url = str(issue.get("html_url") or "")
    if not re.fullmatch(r'https://github\.com/[^/\s]+/[^/\s]+/issues/\d+', url):
        return None
    repo = url.split("/")[3] + "/" + url.split("/")[4]
    if any(word in repo.lower() for word in DENY_REPOS):
        return None
    title = str(issue.get("title") or "").strip()
    body = str(issue.get("body") or "")[:15000]
    combined = title + "\n" + body
    if not BIDDING_RE.search(combined) or REQUIRED_SPEND_RE.search(combined):
        return None
    amount = amount_hint(combined)
    if amount is None:
        return None
    comments = max(0, int(issue.get("comments") or 0))
    created = issue.get("created_at")
    score = 40
    score += 15 if amount >= 100 else 10 if amount >= 50 else 5
    score += 12 if re.search(r'\b(acceptance criteria|merged pr|merge(?:d)? pull request)\b', combined, re.I) else 0
    score += 8 if re.search(r'\b(usdc|paypal|stripe|paid upon|pay after)\b', combined, re.I) else 0
    score += 8 if comments < 3 else 3 if comments < 8 else -12
    score += 7 if issue.get("state") == "open" else -50
    score = max(0, min(100, score))
    return {
        "source": "github_public_issue",
        "repo": repo,
        "title": title[:180],
        "url": url,
        "bounty_usd_hint_not_verified": amount,
        "score_for_review_only": score,
        "competition_indicator_comments": comments,
        "created_at": created,
        "status": "requires_manual_payment_and_claim_verification",
        "discovered_lane": lane,
    }


def run(now=None, api=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=14)).date().isoformat()
    token = os.environ.get("GITHUB_TOKEN", "")
    find = api or github_search
    outcomes = {}
    candidates = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        jobs = {
            pool.submit(find, search, since, token): lane
            for lane, search in SEARCH_LANES.items()
        }
        for future in as_completed(jobs):
            lane = jobs[future]
            try:
                issues = future.result()
                outcomes[lane] = len(issues)
                for issue in issues:
                    item = prospect(issue, lane)
                    if item and (item["url"] not in candidates or
                                 item["score_for_review_only"] > candidates[item["url"]]["score_for_review_only"]):
                        candidates[item["url"]] = item
            except Exception as exc:
                # Each independent lane is isolated; errors never become paid-lead claims.
                errors[lane] = type(exc).__name__ + ": " + str(exc)[:140]
    ranked = sorted(candidates.values(),
                    key=lambda item: (-item["score_for_review_only"],
                                      -item["bounty_usd_hint_not_verified"], item["url"]))
    return {
        "schema_version": 1,
        "description": "Ten parallel search streams. Hints, not prequalified paid jobs or buyer contacts.",
        "configured_lanes": len(SEARCH_LANES),
        "successful_lanes": len(outcomes),
        "lane_results": dict(sorted(outcomes.items())),
        "lane_errors": dict(sorted(errors.items())),
        "unique_candidates": len(candidates),
        "top_candidates": ranked[:30],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="ops/discovery/current.json")
    args = parser.parse_args()
    results = run()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(json.dumps({"configured_search_lanes": results["configured_lanes"],
                      "successful_lanes": results["successful_lanes"],
                      "unique_candidates": results["unique_candidates"],
                      "top_candidates": len(results["top_candidates"]),
                      "failed_lanes": results["lane_errors"]}))
    if results["successful_lanes"] == 0:
        raise SystemExit("All ten lanes failed; do not treat this run as opportunity discovery.")


if __name__ == "__main__":
    main()
