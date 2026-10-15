#!/usr/bin/env python3
"""Independent review gate for discovered GitHub issue bounties.

No mutation of external repos. Flag competition and claimed work using actual
repository PRs, issue comments and cross-repository PR references. Funded status
is NEVER inferred from a dollar amount alone.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.github.com"
SOURCE_LIMIT = 25
WORKERS = 5
PR_LINK = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")
CLAIM = re.compile(r"(?i)(?:^|\s)/(?:attempt|claim)\b|\b(?:working on this|submitted (?:a |my )?pr|already (?:fixed|addressed))\b")


def fetch(url, token=None):
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "api.github.com"
            or not parsed.path.startswith("/repos/")):
        raise ValueError("Only GitHub public repository API calls allowed")
    headers = {"User-Agent": "RoyalT-opportunity-verifier/1.0",
               "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
            return json.load(response)
    except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError("GitHub review API failed: " + type(exc).__name__) from None


def current_issue_ref(url):
    m = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d+)", url)
    if not m:
        raise ValueError("Invalid issue URL")
    return m.group(1), m.group(2), int(m.group(3))


def describe_pr_match(pr, number, owner, repo):
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")
    text = title + "\n" + body
    full = owner + "/" + repo
    return (bool(re.search(r"(?i)\b(?:fix(?:es)?|clos(?:es)?|resolv(?:es)?|for|issue)\s*#"
                          + str(number) + r"\b", text))
            or full + "/issues/" + str(number) in text
            or re.search(r"(?<!\d)#" + str(number) + r"\b", title) is not None)


def review(candidate, api=fetch, token=None):
    issue_url = candidate["url"]
    owner, repo, number = current_issue_ref(issue_url)
    base = API_BASE + "/repos/" + owner + "/" + repo
    issue = api(base + "/issues/" + str(number), token)
    if issue.get("state") != "open":
        return {"url": issue_url, "decision": "closed", "competing_pr_urls": [],
                "claim_signals": [], "funds_independently_verified": False}
    comments = api(base + "/issues/" + str(number) + "/comments?per_page=100", token)
    prs = api(base + "/pulls?state=open&per_page=100", token)
    if not isinstance(comments, list) or not isinstance(prs, list):
        raise ValueError("Unexpected GitHub response; do not mark uncontested")
    matched = {str(p.get("html_url")) for p in prs if describe_pr_match(p, number, owner, repo)
               and p.get("html_url")}
    signals = []
    for item in comments:
        text = str(item.get("body") or "")
        for link in PR_LINK.findall(text):
            if repo.lower() in link.lower() or "#"+str(number) in text or str(number) in text:
                matched.add(link)
        if CLAIM.search(text):
            signals.append(item.get("html_url") or "comment-with-claim")
    signals = sorted(set(signals))
    linked = sorted(matched)
    if linked:
        decision = "competing_fix_or_pr"
    elif signals:
        decision = "existing_claim_or_attempt"
    elif len(prs) == 100:
        decision = "incomplete_pr_pagination"
    else:
        decision = "uncontested_not_funding_verified"
    return {
        "url": issue_url,
        "decision": decision,
        "competing_pr_urls": linked[:15],
        "claim_signals": signals[:15],
        "open_prs_inspected": len(prs),
        "comments_inspected": len(comments),
        "funds_independently_verified": False,
        "qualifies_as_paid_order": False
    }


def run(candidates, api=fetch, token=None, now=None):
    token = os.environ.get("GITHUB_TOKEN", "") if token is None else token
    now = now or datetime.now(timezone.utc)
    unique = {}
    for c in candidates[:SOURCE_LIMIT]:
        if isinstance(c, dict) and c.get("url") not in unique and isinstance(c.get("url"), str):
            unique[c["url"]] = c
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(review, c, api, token): url for url, c in unique.items()}
        for f in as_completed(futures):
            url = futures[f]
            try:
                results.append(f.result())
            except Exception as exc:
                results.append({"url": url, "decision": "verification_error",
                                "error": type(exc).__name__ + ": " + str(exc)[:150],
                                "competing_pr_urls": [], "claim_signals": [],
                                "funds_independently_verified": False,
                                "qualifies_as_paid_order": False})
    results.sort(key=lambda r: r["url"])
    counts = {k: sum(r["decision"] == k for r in results)
              for k in sorted({r["decision"] for r in results})}
    return {
        "schema_version": 1,
        "reviewed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "source_items": len(unique),
        "reviewed_items": len(results),
        "decision_counts": counts,
        "verified_paid_orders": 0,
        "verified_funding": 0,
        "results": results,
        "note": "Competition audit only. No independent escrow verification; these are not accepted paying jobs."
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="ops/discovery/current.json")
    p.add_argument("--output", default="ops/discovery/triage.json")
    args = p.parse_args()
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    result = run(source.get("top_candidates", []))
    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed": result["reviewed_items"],
                      "decision_counts": result["decision_counts"],
                      "funded_jobs_verified": 0}))
    if any(x["decision"] == "verification_error" for x in result["results"]):
        raise SystemExit("Some live verification calls failed; reviewed data is incomplete.")


if __name__ == "__main__":
    main()
