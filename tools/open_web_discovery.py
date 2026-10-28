#!/usr/bin/env python3
"""Broad open-web research shared by autonomous revenue lanes.

Search results from any public domain are retained as research signals. Market
relevance and external-action authorization are assessed by the calling agent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import hashlib
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NOW=lambda: datetime.now(timezone.utc)

EMAIL=re.compile(r"(?<![\w.+-])[\w.+-]{1,60}@[\w.-]{3,100}\.[A-Za-z]{2,16}\b")
def _public_url(url):
    parts=urllib.parse.urlsplit(str(url or ""))
    if parts.scheme not in ("http","https") or not parts.hostname:
        return False
    host=parts.hostname.lower().rstrip(".")
    if host=="localhost" or host.endswith(".local"):
        return False
    try:
        ip=ipaddress.ip_address(host)
        if not ip.is_global:
            return False
    except ValueError:
        pass
    return True

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not _public_url(newurl):
            raise ValueError("redirect_not_public")
        return super().redirect_request(req,fp,code,msg,headers,newurl)

_OPENER=urllib.request.build_opener(_SafeRedirect())

def _open(url,accept="text/html,application/xhtml+xml",limit=450000):
    if not _public_url(url):
        raise ValueError("public_url_required")
    req=urllib.request.Request(url,headers={
        "User-Agent":"Mozilla/5.0 compatible; autonomous-commerce-discovery/1.0",
        "Accept":accept,
    })
    with _OPENER.open(req,timeout=18) as response:
        final=response.geturl()
        if not _public_url(final):
            raise ValueError("final_url_not_public")
        ctype=str(response.headers.get("Content-Type") or "").lower()
        raw=response.read(limit)
    return final,ctype,raw

def clean_html(raw):
    text=raw.decode("utf-8","ignore") if isinstance(raw,(bytes,bytearray)) else str(raw or "")
    text=re.sub(r"(?is)<script\b[^>]*>.*?</script>"," ",text)
    text=re.sub(r"(?is)<style\b[^>]*>.*?</style>"," ",text)
    text=re.sub(r"(?i)<br\s*/?>","\n",text)
    text=re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>","\n",text)
    text=re.sub(r"<[^>]+>"," ",text)
    text=unescape(text)
    text=re.sub(r"[ \t]+"," ",text)
    text=re.sub(r"\n\s*\n+","\n",text)
    return text.strip()

def fetch_public_text(url,with_links=False):
    final,ctype,raw=_open(url)
    if not ("text/" in ctype or "html" in ctype or not ctype):
        raise ValueError("unsupported_public_content")
    # Preserve public email addresses that may exist only in mailto attributes.
    decoded=raw.decode("utf-8","ignore")
    mailto=" ".join(sorted(set(
        re.findall(r"(?i)mailto:([^?\"'<>\s]+)",unescape(decoded)))))
    text=clean_html(raw)
    if mailto:
        text+="\n"+mailto
    if with_links:
        return final,text[:12000],public_links(decoded,final)
    return final,text[:12000]


def public_links(html,base_url):
    """Identify relevant outgoing public links without fixed website lists."""
    found=[]
    seen=set()
    pattern=r'<a\b[^>]*\bhref\s*=\s*(["\x27])(.*?)\1[^>]*>(.*?)</a>'
    for match in re.finditer(pattern,html,flags=re.I|re.S):
        href=unescape(match.group(2).strip())
        url=urllib.parse.urljoin(base_url,href).split("#",1)[0]
        if not _public_url(url) or len(url)>1200 or url in seen:
            continue
        if re.search(r"\.(?:png|jpe?g|gif|webp|svg|ico|mp4|mp3|zip|pdf)(?:\?|$)",url,re.I):
            continue
        anchor=clean_html(match.group(3))[:160]
        if len(anchor)<5:
            continue
        seen.add(url)
        found.append({"url":url,"title":anchor})
        if len(found)>=90:
            break
    return found


# Linguistic stop words are not business filters or site restrictions.
# They prevent a query such as "How can ..." from ranking a dictionary
# definition of "can" ahead of an actual relevant market page.
GRAMMAR=set((
    "how what when where why which who whose whom are was were the this that "
    "these those you your they their have has had with from into onto about "
    "can could would should will shall may might do does did done a an and "
    "but for not nor on to of in at by is it be been being there here some "
    "than then more most best ways guide today new use using used without "
    "current latest recent look looking example examples"
).split())


def search_terms(value):
    words=re.findall(r"[a-z0-9]+",str(value or "").lower())
    return [word for word in words if (len(word)>=3 or word=="ai")
            and word not in GRAMMAR]


def root_terms(value):
    return set(word[:5] if len(word)>5 else word for word in search_terms(value))


def compact_query(query):
    """Preserve quoted firsthand language, instead of broadening to SEO terms."""
    raw=re.sub(r"\s+"," ",str(query or "")).strip()[:220]
    if len(raw)<=110:
        return raw
    quoted=re.findall(r'["\u201c]([^"\u201d]{4,80})["\u201d]',raw)
    terms=search_terms(raw)
    if quoted:
        lead='"'+quoted[0]+'"'
        quoted_terms=set(search_terms(quoted[0]))
        tail=' '.join(word for word in terms if word not in quoted_terms)
        return (lead+' '+tail[:90]).strip()
    return " ".join(terms[:13]) or raw[:140]


def public_request_signal(hit):
    """Soft ranking signal for real requests/problems; never an outreach permit.

    Only ranks already-retrieved evidence. No domain, product or industry is
    mandatory, and the original candidate remains available to the model.
    """
    excerpt=(" ".join(str(hit.get(k) or "") for k in ("title","snippet","text")))[:1900]
    return (
        4*bool(re.search(
            r"(?i)\b(?:seeking|hiring|looking for|request for proposals?|"
            r"request for quotations?|submit (?:a |your )?(?:bid|quote|proposal)|"
            r"paid (?:work|project|contract)|open (?:bounty|rfp))\b",excerpt))
        +2*bool(re.search(
            r"(?i)\b(?:i need|we need|need help|struggling (?:to|with)|"
            r"cannot (?:find|figure|manage)|can't (?:find|figure|manage)|"
            r"frustrated (?:by|with)|manual(?:ly)? (?:tracking|reconciling))\b",
            excerpt))
        +bool("?" in str(hit.get("title") or ""))
    )


def relevant_hit(query,hit):
    """Discard obvious search mismatches, independent of market or domain."""
    wanted=root_terms(query)
    if not wanted:
        return True
    excerpt=str(hit.get("title") or "")+" "+str(hit.get("snippet") or "")
    found=root_terms(excerpt)
    shared=len(wanted.intersection(found))
    # A longer market query must match more than a stray word like "tools".
    return shared>=min(2,len(wanted))


def relevant_links(links,query,limit=5):
    """Rank page links by this run's query, not fixed industry vocabulary."""
    words=root_terms(query)
    scored=[]
    for link in links:
        title=str(link.get("title") or "").lower()
        overlap=len(words.intersection(root_terms(title)))
        if overlap>=min(2,len(words)) and overlap>0:
            scored.append((overlap,link))
    scored.sort(key=lambda x:-x[0])
    return [link for _,link in scored[:limit]]


def safe_email(text):
    emails=[]
    for value in EMAIL.findall(str(text or "")):
        value=value.lower()
        if value.endswith((".png",".jpg",".jpeg",".gif",".webp",".example",".test")):
            continue
        if value.startswith(("noreply@","no-reply@","donotreply@")):
            continue
        emails.append(value)
    unique=sorted(set(emails))
    if len(unique)==1:
        return unique[0]
    # Public role inboxes are appropriate commercial contact routes even when
    # a page also contains unrelated staff/support addresses.
    priority=("projects","business","partnerships","partnership","sales",
              "hello","contact","work","info","marketing")
    for local in priority:
        matches=[e for e in unique if e.split("@",1)[0] in (local,local+"s")]
        if len(matches)==1:
            return matches[0]
    return None

def _normalize_queries(queries):
    out=[]
    seen=set()
    for raw in queries or []:
        q=re.sub(r"\s+"," ",str(raw or "")).strip()
        if not q or len(q)<8:
            continue
        q=q[:220]
        key=q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out

def bing_rss(query,limit=8):
    params=urllib.parse.urlencode({"q":query,"format":"rss","count":str(limit)})
    url="https://www.bing.com/search?"+params
    _,_,raw=_open(url,accept="application/rss+xml,application/xml,text/xml,*/*")
    root=ET.fromstring(raw)
    out=[]
    for item in root.findall(".//item"):
        title=(item.findtext("title") or "").strip()
        link=(item.findtext("link") or "").strip()
        desc=clean_html(item.findtext("description") or "")
        if not link or not _public_url(link):
            continue
        out.append({"title":title,"source_url":link,"snippet":desc})
        if len(out)>=limit:
            break
    return out

def _ddg_target(href):
    href=unescape(str(href or "")).strip()
    if href.startswith("//"):
        href="https:"+href
    parts=urllib.parse.urlsplit(href)
    if parts.hostname in ("duckduckgo.com","html.duckduckgo.com"):
        target=(urllib.parse.parse_qs(parts.query).get("uddg") or [""])[0]
        href=urllib.parse.unquote(target)
    return href if _public_url(href) else None

def duckduckgo_html(query,limit=6):
    params=urllib.parse.urlencode({"q":query})
    _,_,raw=_open("https://html.duckduckgo.com/html/?"+params)
    html=raw.decode("utf-8","ignore")
    out=[]
    seen=set()
    pattern=re.compile(
        r'<a[^>]*class=["\'][^"\']*result__a[^"\']*["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.I|re.S)
    reverse=re.compile(
        r'<a[^>]*href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*result__a[^"\']*["\'][^>]*>(.*?)</a>',
        re.I|re.S)
    for match in list(pattern.finditer(html))+list(reverse.finditer(html)):
        link=_ddg_target(match.group(1))
        if not link or link in seen:
            continue
        seen.add(link)
        out.append({
            "title":clean_html(match.group(2)),
            "source_url":link,
            "snippet":"",
        })
        if len(out)>=limit:
            break
    if out:
        return out
    # DDG's lightweight page uses different result-link markup. Recover
    # independent search hits when the standard endpoint returns no cards.
    try:
        _,_,raw=_open("https://lite.duckduckgo.com/lite/?"+params)
        lightweight=raw.decode("utf-8","ignore")
        for match in re.finditer(r'<a\b([^>]*\bclass=["\x27][^"\x27]*result-link[^"\x27]*["\x27][^>]*)>(.*?)</a>',
                                  lightweight,re.I|re.S):
            href=re.search(r'\bhref=["\x27]([^"\x27]+)',match.group(1),re.I)
            link=_ddg_target(href.group(1)) if href else None
            if not link or link in seen:
                continue
            seen.add(link)
            out.append({"title":clean_html(match.group(2)),
                        "source_url":link,"snippet":""})
            if len(out)>=limit:
                break
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
        pass
    return out


def google_html(query,limit=6):
    """Search ordinary public web results when other HTML engines return none."""
    params=urllib.parse.urlencode({"q":query,"num":str(max(limit,6))})
    _,_,raw=_open("https://www.google.com/search?"+params,limit=350000)
    html=raw.decode("utf-8","ignore")
    seen=set()
    out=[]
    for match in re.finditer(r'<a\b[^>]*\bhref=(["\x27])([^"\x27]+)\1[^>]*>(.*?)</a>',
                             html,re.I|re.S):
        inner=match.group(3)
        if not re.search(r'<h3\b',inner,re.I):
            continue
        href=unescape(match.group(2))
        if href.startswith("/url?"):
            href=(urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("q") or [""])[0]
        host=(urllib.parse.urlsplit(href).hostname or "").lower()
        if (not _public_url(href) or host.endswith("google.com")
                or href in seen):
            continue
        seen.add(href)
        out.append({"title":clean_html(inner),"source_url":href,"snippet":""})
        if len(out)>=limit:
            break
    return out


def brave_html(query,limit=6):
    """Independent public web-search fallback; no site, niche or key required."""
    url="https://search.brave.com/search?"+urllib.parse.urlencode({"q":query})
    _,_,raw=_open(url,limit=450000)
    html=raw.decode("utf-8","ignore")
    chunks=re.split(
        r'(?=<div class="snippet\b[^"<>]*"[^>]*data-type="web"[^>]*>)',
        html,flags=re.I)
    out=[]
    seen=set()
    for section in chunks[1:]:
        anchor=re.search(r'<a\s+href="(https?[^"<>]+)"\s+target="_self"',
                         section,re.I)
        heading=re.search(r'<div\s+class="title\s+[^"<>]+"\s+title="([^"<>]+)"',
                          section,re.I)
        if not anchor or not heading:
            continue
        target=unescape(anchor.group(1))
        if target in seen or not _public_url(target):
            continue
        seen.add(target)
        out.append({"title":unescape(heading.group(1)),
                    "source_url":target,"snippet":""})
        if len(out)>=limit:
            break
    return out


def hn_ask_candidates(queries, memory=None, max_candidates=8):
    """Read *original Ask HN submissions*, not search snippets or page chrome.

    The public Algolia index supplies discovery IDs; the original submission
    is fetched separately from its item endpoint before it counts as evidence.
    Searches remain model-directed, spanning whatever topics it investigates.
    This supplements unrestricted web search rather than restricting domains.
    """
    rows=[]
    seen=set()
    # Broad search terms work better for a question index than fully phrased
    # how-to searches. The terms still come from each independent model query.
    for query in queries[:4]:
        terms=search_terms(query)
        phrase=" ".join(terms[:4])
        if not phrase:
            continue
        url="https://hn.algolia.com/api/v1/search_by_date?"+urllib.parse.urlencode({
            "query":phrase,"tags":"ask_hn","hitsPerPage":"8"})
        try:
            _,_,raw=_open(url,accept="application/json",limit=350000)
            hits=json.loads(raw).get("hits") or []
        except (OSError,ValueError,TimeoutError):
            continue
        for hit in hits:
            ident=str(hit.get("objectID") or "")
            if not ident.isdigit() or ident in seen:
                continue
            seen.add(ident)
            source_url="https://news.ycombinator.com/item?id="+ident
            if memory and not memory.eligible_url(source_url):
                continue
            # Item is a separately retrieved, public first-party record.
            try:
                _,_,item_raw=_open("https://hn.algolia.com/api/v1/items/"+ident,
                                   accept="application/json",limit=120000)
                item=json.loads(item_raw)
            except (OSError,ValueError,TimeoutError):
                continue
            if str(item.get("id"))!=ident or item.get("deleted") or item.get("dead"):
                continue
            title=clean_html(item.get("title") or hit.get("title") or "")
            body=clean_html(item.get("text") or "")
            original=(title+"\n"+body).strip()
            if len(original)<45 or not relevant_hit(query,{"title":title,"snippet":body}):
                continue
            row={
                "id":"web:"+hashlib.sha256(source_url.encode()).hexdigest()[:18],
                "channel":None,"source":"open_web","search_provider":"hn_ask",
                "search_query":query,"source_url":source_url,
                "search_alignment":"first_party_question_post",
                "title":title[:500],"snippet":body[:900],
                "contact":safe_email(original),"text":original[:8000],
                "page_text":original[:8000],"page_fetched":True,
                "created_at":str(item.get("created_at") or "") or None,
                "public_links":[],
            }
            row["channel"]="email" if row["contact"] else None
            rows.append(row)
            if memory:
                memory.observe_page(row)
            if len(rows)>=max_candidates:
                return rows
    return rows

def open_web_candidates(max_candidates=24, queries=None, memory=None,
                        max_pages=32, strict_relevance=True, allowed_hosts=None,
                        include_discussions=True):
    """Search model-generated topics and progress along prior link discoveries.

    Budgets constrain this execution's page count; researched URLs
    and unfinished directions are carried forward through persistent memory.
    The returned research has no mandatory keyword, site or email requirement.
    """
    queries=_normalize_queries(queries)
    def in_scope(url):
        if not _public_url(url):
            return False
        if allowed_hosts is None:
            return True
        host=(urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
        return any(host==domain or host.endswith("."+domain)
                   for domain in allowed_hosts)

    if not queries and not (memory and memory.frontier()):
        return [], [{"source":"open_web_search","reason":"no_new_research_direction"}]

    errors=[]
    # Discussion indexes expose original first-person requests even where
    # search engines point to JavaScript-only/blocked forum pages.
    primary_discussions=[]
    if include_discussions and memory and queries:
        try:
            primary_discussions=hn_ask_candidates(
                queries,memory=memory,
                max_candidates=max(3,min(9,max_candidates//2)))
        except Exception as exc:
            errors.append({"source":"hn_ask_search","reason":type(exc).__name__})
    streams=[]
    total_provider_hits=0
    providers=(("duckduckgo",duckduckgo_html),("google",google_html),
               ("brave",brave_html),("bing",bing_rss))
    blocked_providers=set()
    # Read independent topics via the provider that actually returns topical
    # results. Bing's RSS feed has repeatedly returned dictionary definitions
    # or unrelated pages for whole human-problem queries. It remains a
    # relevance-checked fallback, never a way to fill the queue with noise.
    for query in queries:
        strong_primary=0
        for provider,search in providers:
            if provider in blocked_providers:
                continue
            if provider!="duckduckgo" and strong_primary>=3:
                continue
            try:
                hits=search(compact_query(query) if strict_relevance else query,
                            limit=6 if strict_relevance else 8)
                hits=[hit for hit in hits if in_scope(hit.get("source_url"))]
                total_provider_hits+=len(hits)
                # Relevance is a ranking hint, not an exclusion gate. An
                # arbitrary token-overlap threshold used to discard nearly
                # every search result before fetching actual page evidence.
                ordered=sorted(hits,key=lambda hit:(not relevant_hit(query,hit),-public_request_signal(hit)))
                if strict_relevance:
                    aligned=[hit for hit in ordered if relevant_hit(query,hit)]
                    other=[hit for hit in ordered if not relevant_hit(query,hit)]
                    if provider in ("duckduckgo","google","brave"):
                        strong_primary=len(aligned)
                        # Some authentic discussions use vocabulary unlike
                        # the search query. Permit a few exploratory pages.
                        ordered=aligned[:6]+other[:max(0,2-len(aligned))]
                    else:
                        # RSS has demonstrably served unrelated dictionary
                        # pages; this fallback must be explicitly aligned.
                        ordered=aligned[:6]
                if ordered:
                    streams.append((query,provider,ordered))
                if memory:
                    memory.observe_search(query,provider,len(hits))
            except Exception as exc:
                errors.append({
                    "source":"open_web_search","provider":provider,
                    "reason":type(exc).__name__,
                    "http_code":exc.code if isinstance(exc,urllib.error.HTTPError) else None,
                })
                # Rate-limited or forbidden providers will not recover a few
                # milliseconds later on the next query. Don't waste the
                # remaining run retrying the same blocked search endpoint.
                if isinstance(exc,urllib.error.HTTPError) and exc.code in (403,429,503):
                    blocked_providers.add(provider)
                if memory:
                    memory.observe_search(query,provider,0,error=type(exc).__name__)

    queue=[]
    for index in range(max((len(hits) for _,_,hits in streams),default=0)):
        for query,provider,hits in streams:
            if index<len(hits):
                queue.append((query,provider,hits[index]))

    # A few previously discovered, unvisited public links also progress each
    # run. Keep new search discoveries in rotation, not a single-site crawl.
    if memory:
        queued_followups=[]
        for front in memory.frontier(limit=12 if strict_relevance else 60):
            parent=next((p for p in reversed(memory.state.get("pages") or [])
                         if p.get("url")==front.get("from_url")),None)
            origin_query=str((parent or {}).get("search_query") or "")
            # Relevance is judged against the actual parent investigation.
            # No domain or market category is prioritized.
            if origin_query:
                matching=root_terms(origin_query).intersection(
                    root_terms(front.get("title") or ""))
                if not matching:
                    continue
            # For outreach research, don't let previously crawled dictionary or
            # shopping links revive irrelevant search topics from older runs.
            # Compare against THIS run's independently chosen search directions.
            if (strict_relevance and queries and
                    not any(relevant_hit(q,front) for q in queries)):
                continue
            if not in_scope(front.get("url")) or not memory.eligible_url(front["url"]):
                continue
            follow={"source_url":front.get("url"),"title":front.get("title"),
                    "snippet":"Linked from "+str(front.get("from_url") or "")}
            queued_followups.append(follow)
            if len(queued_followups)>=4:
                break
        # New independently selected searches lead. Older link trails
        # supplement them when capacity remains, not displace them.
        for follow in queued_followups:
            queue.append(("", "followup",follow))

    results={r["source_url"]:r for r in primary_discussions}
    visited=set()
    page_budget=max(4,int(max_pages))
    for query,provider,hit in queue:
        if len(results)>=max_candidates or len(visited)>=page_budget:
            break
        url=str(hit.get("source_url") or "")
        if not in_scope(url) or url in visited:
            continue
        if memory and not memory.eligible_url(url):
            continue
        visited.add(url)
        title=str(hit.get("title") or "")
        snippet=str(hit.get("snippet") or "")
        links=[]
        try:
            final,page,links=fetch_public_text(url,with_links=True)
            if not in_scope(final):
                continue
        except Exception as exc:
            # Search snippets still provide authentic external observations
            # when a public site rejects automated page fetches.
            final,page=url,""
            if len(errors)<25:
                errors.append({
                    "source":"public_page_fetch","reason":type(exc).__name__,
                })
        if final in results:
            continue
        evidence=(title+"\n"+snippet+"\n"+page).strip()
        if not evidence:
            continue
        # A JS shell like Reddit's one-word landing page is not a fetched
        # discussion. Search-result wording is never first-person evidence.
        if len(page.strip())<40:
            page=""
            evidence=(title+"\n"+snippet).strip()
        contact=safe_email(evidence)
        ident=hashlib.sha256(final.encode()).hexdigest()[:18]
        row={
            "id":"web:"+ident,"channel":"email" if contact else None,
            "source":"open_web","search_provider":provider,
            "search_query":query,"source_url":final,
            "search_alignment":(
                "source_followup" if not query else
                "keyword_aligned" if relevant_hit(query,hit) else "unverified"),

            "title":title[:500],"snippet":snippet[:1600],
            "contact":contact,"text":evidence[:8000],
            # Separate fetched, firsthand page content from search snippets.
            # A search engine summary is never proof of an open buyer request.
            "page_text":page[:8000],
            "page_fetched":bool(page),"created_at":None,
            "public_links":links,
        }
        results[final]=row
        if memory:
            memory.observe_page(row,relevant_links(links,query or title))
    if queries and not total_provider_hits and not primary_discussions:
        errors.append({"source":"open_web_search",
                       "reason":"providers_returned_no_results",
                       "query_count":len(queries)})
    return list(results.values()),errors
