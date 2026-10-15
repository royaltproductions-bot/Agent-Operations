#!/usr/bin/env python3
"""Read-only discoverer for explicitly advertised HN contract opportunities.

Source: Hacker News Algolia's public "Who is hiring?" thread. No email scraping,
no automatic outreach, no stored contact details, no claims of guaranteed pay.
Used only to supply original postings for a human/Scout eligibility review.
"""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

ALGOLIA = "https://hn.algolia.com/api/v1/"
HIRING_TITLE = re.compile(r"^Ask HN:\s*Who is hiring\?\s*\(([^)]+)\)", re.I)
CONTRACT = re.compile(r"\b(contract|contractor|freelanc(?:e|er)|part[- ]time|project[- ]based|fractional|consult(?:ant|ing)|paid pilot)\b", re.I)
SKILLS = {
    "ai-video": re.compile(r"\b(ai[- ](?:video|film(?:making)?|content)|generative video|film(?:making)?|video editor|video production|creative producer)\b", re.I),
    "growth": re.compile(r"\b(go[- ]to[- ]market|gtm|growth marketing|customer acquisition|content marketing|social media|marketing operations)\b", re.I),
    "sales": re.compile(r"\b(sales|customer success|account management|business development|revops|revenue operations)\b", re.I),
    "operations": re.compile(r"\b(business operations|operations manager|workflow automation|process automation|data cleaning|spreadsheet|retail operations)\b", re.I),
    "ai-automation": re.compile(r"\b(ai agent|agentic|llm workflow|ai automation|prompt engineer|ai integration|no[- ]code automation)\b", re.I),
}
SKIP = re.compile(r"\b(unpaid|volunteer|equity only|no compensation|internship only|visa sponsorship required)\b", re.I)
MAX_RESULTS = 40


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "div", "li"):
            self.parts.append("\n")
    def handle_data(self, data):
        self.parts.append(data)
    def get_text(self):
        return re.sub(r"[ \t]+", " ", "".join(self.parts)).strip()


def clean_html(raw):
    parser = TextParser()
    parser.feed(raw or "")
    return parser.get_text()


def fetch_json(url, opener=None):
    if not url.startswith(ALGOLIA):
        raise ValueError("Only documented public HN Algolia endpoints allowed")
    get = opener or urllib.request.urlopen
    request = urllib.request.Request(url, headers={"User-Agent":"RoyalT-opportunity-research/1.0"})
    with get(request, timeout=24) as stream:
        return json.load(stream)


def find_hiring_thread(index):
    hits = index.get("hits", [])
    options = []
    for hit in hits:
        title = hit.get("title") or ""
        if not HIRING_TITLE.match(title):
            continue
        try:
            timestamp = int(hit["created_at_i"])
            ident = int(hit["objectID"])
        except (KeyError, ValueError, TypeError):
            continue
        options.append((timestamp, ident, title))
    if not options:
        raise ValueError("Current HN hiring thread not found: do not invent a source")
    return max(options)


def score_comment(comment):
    if not isinstance(comment, dict) or comment.get("deleted") or comment.get("dead"):
        return None
    text = clean_html(comment.get("text") or "")[:6000]
    if not text or SKIP.search(text):
        return None
    header = re.sub(r"\s+", " ", text.split("\n")[0]).strip()
    # A stray 'contract' anywhere in a full-time posting is not a contract offer.
    if not CONTRACT.search(header):
        return None
    if re.search(r"\b(commission[- ]only|equity[- ]only|unpaid trial)\b", header, re.I):
        return None
    if re.search(r"\b(onsite|on[- ]site|hybrid|german c1|remote\s*\((?:uk|europe|germany)|gmt[-+ ]*2)", header, re.I):
        return None
    categories = [key for key, regex in SKILLS.items() if regex.search(text[:750])]
    if not categories:
        return None
    try:
        item_id = int(comment["id"])
    except (TypeError, ValueError, KeyError):
        return None
    # Rank actual independent PUBLIC invitations, not applicant replies.
    # Items API child is a top-level comment in the employer's monthly thread.
    words = re.findall(r"\b[\w-]+\b", text)
    headline = re.sub(r"\s+", " ", text.split("\n")[0]).strip()[:165]
    if len(headline) < 15:
        headline = " ".join(words[:24])[:165]
    contract_signals = sorted({x.group(0).lower() for x in CONTRACT.finditer(text)})
    score = min(100, 45 + min(len(categories), 3) * 12
                + (10 if re.search(r"\bremote\b", text, re.I) else 0)
                + (8 if re.search(r"\b(usd|pay|salary|hourly|rate|\$\s*\d)\b", text, re.I) else 0))
    return {
        "type": "public_hiring_comment_requires_review",
        "primary_url": "https://news.ycombinator.com/item?id=" + str(item_id),
        "comment_id": item_id,
        "headline": headline,
        "skill_categories": categories,
        "work_type_signals": contract_signals[:4],
        "review_priority_score_not_a_paid_lead": score,
        "posted_at_utc": comment.get("created_at"),
        "payment_verified": False,
        "applied": False,
        "contact_information_intentionally_omitted": True,
    }


def discover(index, thread):
    timestamp, story_id, title = find_hiring_thread(index)
    if str(thread.get("id")) != str(story_id):
        raise ValueError("Fetched thread ID differs from discovery index")
    children = thread.get("children") or []
    by_comment = {}
    for item in children:
        prospect = score_comment(item)
        if prospect:
            by_comment[prospect["comment_id"]] = prospect
    ranked = sorted(by_comment.values(), key=lambda x: (
        -x["review_priority_score_not_a_paid_lead"],
        x["primary_url"]
    ))
    return {
        "schema_version": 1,
        "source_name": "Hacker News latest Who is hiring? thread",
        "thread_title": title,
        "thread_url": "https://news.ycombinator.com/item?id=" + str(story_id),
        "source_comment_count": len(children),
        "potential_contract_mention_count": len(ranked),
        "top_review_only": ranked[:MAX_RESULTS],
        "paid_orders_verified": 0,
        "direct_applications_sent": 0,
        "note": "Contract/keyskill keyword overlap, NOT independently qualified funded work. Review role fit, current availability, payer and allowed submission at original posting."
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="ops/discovery/hn-contract-invitations.json")
    args = p.parse_args()
    query = urllib.parse.urlencode({"tags":"story","query":"Ask HN: Who is hiring?", "hitsPerPage":40})
    index = fetch_json(ALGOLIA + "search_by_date?" + query)
    _, story_id, _ = find_hiring_thread(index)
    thread = fetch_json(ALGOLIA + "items/" + str(story_id))
    result = discover(index, thread)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "source": result["thread_url"],
        "top_level_posts_inspected": result["source_comment_count"],
        "potential_contract_posts_for_review": result["potential_contract_mention_count"],
        "published_to_queue": 0, "cash_collected": 0
    }))


if __name__ == "__main__":
    main()
