#!/usr/bin/env python3
"""Live discovery of external AI-agent commercial experiments.

No baked-in success stories or predetermined businesses. The local model
supplies a search query on each run; these source adapters retrieve CURRENT
public observations for comparison and creative recombination. Third-party
claims are not independently verified revenue or instructions to the agent.
The main commerce executor continues if discovery providers fail.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import json
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import urllib.error
from xml.etree import ElementTree as ET

MAX_ITEMS = 7


def _text(raw, limit=500):
    raw = re.sub(r"<[^>]+>", " ", str(raw or ""))
    return re.sub(r"\s+", " ", unescape(raw)).strip()[:limit]


def _get(url, expected_host, *, opener=None):
    urlparts = urlsplit(url)
    if (urlparts.scheme != "https" or urlparts.hostname != expected_host
            or urlparts.username or urlparts.password):
        raise ValueError("unexpected_public_research_host")
    request = Request(url, headers={
        "User-Agent": "independent-commerce-learning/1.0",
        "Accept": "application/xml, application/rss+xml, application/json",
    })
    with (opener or urlopen)(request, timeout=12) as response:
        return response.read(300_000)


def _utc(value):
    if not value:
        return None
    try:
        when = parsedate_to_datetime(value) if "," in str(value) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None


def _fresh(when, now, days):
    return when is not None and now - timedelta(days=days) <= when <= now + timedelta(days=1)


def _safe_link(raw):
    parts = urlsplit(str(raw or ""))
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    if parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        return None
    # Store links only; no automatic crawling of arbitrary third-party sites.
    return str(raw)[:1200]


def hackernews_examples(query, *, opener=None, now=None):
    now = now or datetime.now(timezone.utc)
    params = urlencode({"query": query[:150], "tags": "story",
                        "hitsPerPage": 25, "numericFilters": "created_at_i>"
                        + str(int((now - timedelta(days=120)).timestamp()))})
    raw = _get("https://hn.algolia.com/api/v1/search_by_date?" + params,
               "hn.algolia.com", opener=opener)
    found = []
    for hit in (json.loads(raw).get("hits") or []):
        when = _utc(hit.get("created_at"))
        if not _fresh(when, now, 120):
            continue
        ident = str(hit.get("objectID") or "")
        if not ident.isdigit():
            continue
        title = _text(hit.get("title"), 180)
        summary = _text(hit.get("story_text") or hit.get("comment_text"), 500)
        if not title:
            continue
        found.append({"title": title, "url": "https://news.ycombinator.com/item?id=" + ident,
                      "excerpt": summary, "published": when.isoformat(),
                      "origin": "hackernews_story", "evidence": "public_discussion_not_payment_proof"})
    return found


def news_examples(query, *, opener=None, now=None):
    now = now or datetime.now(timezone.utc)
    params = urlencode({"q": query[:150], "hl": "en-US", "gl": "US", "ceid": "US:en"})
    raw = _get("https://news.google.com/rss/search?" + params,
               "news.google.com", opener=opener)
    root = ET.fromstring(raw)
    found = []
    for item in root.findall("./channel/item")[:35]:
        when = _utc(item.findtext("pubDate"))
        if not _fresh(when, now, 120):
            continue
        url = _safe_link(item.findtext("link"))
        title = _text(item.findtext("title"), 180)
        if not title or not url:
            continue
        found.append({"title": title, "url": url,
                      "excerpt": _text(item.findtext("description"), 500),
                      "published": when.isoformat(),
                      "origin": "news_search_headline",
                      "evidence": "third_party_headline_not_payment_proof"})
    return found


def discover(query, *, previously_seen=(), opener=None, now=None):
    """Return a rotating sample of fresh retrieved public observations.

    Search terms come from the model, not this module; links are discovered
    at runtime. Search-provider errors are reported, not silently construed
    as proof that an idea has no commercial value.
    """
    query = re.sub(r"\s+", " ", str(query or "")).strip()[:150]
    if len(query) < 5:
        return {"status": "invalid_search", "query": query, "sources": [], "errors": []}
    now = now or datetime.now(timezone.utc)
    seen = set(previously_seen)
    candidates = []
    failures = []
    for name, provider in (("hackernews_stories", hackernews_examples),
                           ("google_news_search", news_examples)):
        try:
            candidates.extend(provider(query, opener=opener, now=now))
        except (ValueError, KeyError, TypeError, ET.ParseError, OSError,
                urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            failures.append(name + "_unavailable")
    # Prefer unseen evidence without hiding relevant older source links.
    unique = []
    observed = set()
    for item in sorted(candidates, key=lambda x: (x["url"] in seen, x["published"]), reverse=False):
        if item["url"] in observed:
            continue
        observed.add(item["url"])
        unique.append(item)
    unseen = [s for s in unique if s["url"] not in seen]
    remaining = [s for s in unique if s["url"] in seen]
    chosen = (sorted(unseen, key=lambda x: x["published"], reverse=True)
              + sorted(remaining, key=lambda x: x["published"], reverse=True))[:MAX_ITEMS]
    return {"status": "found" if chosen else "no_observations",
            "query": query, "sources": chosen, "errors": failures,
            "note": "These are public, unverified outside reports; do not copy claims as verified sales."}
