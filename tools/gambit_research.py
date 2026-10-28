"""Bounded public-web discovery for Gambit paid research assignments.

No API keys or paid search providers. The result explicitly distinguishes a
search-engine snippet from first-hand page content. Buyer text is never logged.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


MAX_QUERIES = 7
MAX_RESULTS_PER_QUERY = 12
MAX_RESULTS = 55
MAX_FETCHED_PAGES = 15
MAX_RESULT_TEXT = 1000
MAX_TOTAL_TEXT = 14000


def relevant_excerpt(content, hints, limit=MAX_RESULT_TEXT):
    """Preserve site identity and the strongest query-matching passage.

    Search extractors frequently return navigation and policy boilerplate first;
    first-N-character truncation can hide the only actual company details.
    """
    clean = re.sub(r"\s+", " ", str(content or "")).strip()
    if len(clean) <= limit:
        return clean
    prefix = clean[:min(180, limit // 4)]
    budget = max(1, limit - len(prefix) - 7)
    tokens = {term.lower() for term in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}",
                                                   str(hints or ""))
              if term.lower() not in {"https", "http", "www", "about", "company",
                                      "business", "contact", "information", "public"}}
    # Evaluate the full page in bounded overlapping windows; don't trust the
    # search snippet as fact, only the content the page extractor actually read.
    best = clean[len(prefix):len(prefix) + budget]
    highest = -1
    for start in range(len(prefix), len(clean), max(1, budget // 2)):
        window = clean[start:start + budget]
        lower = window.lower()
        score = sum(1 for token in tokens if token in lower)
        if score > highest:
            highest, best = score, window
    return prefix + " [...] " + best


def public_url(raw):
    """Reject local and non-web URLs before handing them to the search extractor."""
    if not isinstance(raw, str) or len(raw) > 2000:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if (host in {"localhost", "metadata.google.internal"} or
            host.endswith((".localhost", ".local", ".internal")) or
            parsed.username is not None or parsed.password is not None):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port not in {None, 80, 443}:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def search_public_web(queries, *, ddgs=None, max_queries=MAX_QUERIES):
    """Find public, inspectable evidence; failures never become fabricated results."""
    if ddgs is None:
        try:
            from ddgs import DDGS
        except ImportError:
            return {"status": "unavailable", "results": [], "attempted": 0,
                    "pages_read": 0, "reason": "search_package_missing"}
        ddgs = DDGS(timeout=12)
    found, seen, attempted, failures = [], set(), 0, 0
    query_buckets = []
    for query in queries[:min(max_queries, MAX_QUERIES)]:
        if not isinstance(query, str) or not query.strip():
            continue
        attempted += 1
        bucket = []
        try:
            hits = ddgs.text(query.strip()[:180], max_results=MAX_RESULTS_PER_QUERY,
                             backend="auto") or []
        except Exception:
            failures += 1
            continue
        for item in hits:
            url = public_url(item.get("href") or item.get("url"))
            if not url or url in seen:
                continue
            seen.add(url)
            result = {"url": url, "title": str(item.get("title") or "")[:180],
                      "snippet": str(item.get("body") or "")[:330],
                      "search_query": query.strip()[:180],
                      "page_text": "", "evidence_level": "search_snippet"}
            found.append(result)
            bucket.append(result)
        if bucket:
            query_buckets.append(bucket)
    # Search ranking is per query. Reading the first N results in a single flat
    # list accidentally spends every page visit on query one. Take one result
    # from each query per round and favor fresh domains before repeat domains.
    prioritized = []
    for rank in range(max((len(bucket) for bucket in query_buckets), default=0)):
        prioritized.extend(bucket[rank] for bucket in query_buckets
                           if len(bucket) > rank)
    found = prioritized[:MAX_RESULTS]
    diversified, repeated, domains = [], [], set()
    for item in found:
        domain = urlsplit(item["url"]).hostname
        if domain in domains:
            repeated.append(item)
        else:
            domains.add(domain)
            diversified.append(item)
    pages_read, text_budget = 0, MAX_TOTAL_TEXT
    for item in diversified + repeated:
        if pages_read >= MAX_FETCHED_PAGES or text_budget <= 0:
            break
        try:
            extracted = ddgs.extract(item["url"], fmt="text_plain")
            content = str((extracted or {}).get("content") or "").strip()
        except Exception:
            continue
        if not content:
            continue
        body = relevant_excerpt(content,
                                " ".join((item["search_query"], item["title"],
                                          item["snippet"])),
                                min(MAX_RESULT_TEXT, text_budget))
        item["page_text"] = body
        item["evidence_level"] = "page_read"
        text_budget -= len(body)
        pages_read += 1
    status = "available" if found and pages_read else ("partial" if found else "unavailable")
    return {"status": status, "results": found, "attempted": attempted,
            "pages_read": pages_read, "search_failures": failures}
