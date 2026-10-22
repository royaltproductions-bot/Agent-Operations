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
import os
import re
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

def _open(url,accept="text/html,application/xhtml+xml",timeout=18,limit=450000):
    if not _public_url(url):
        raise ValueError("public_url_required")
    req=urllib.request.Request(url,headers={
        "User-Agent":"Mozilla/5.0 compatible; autonomous-commerce-discovery/1.0",
        "Accept":accept,
    })
    with _OPENER.open(req,timeout=timeout) as response:
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

def fetch_public_text(url):
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
    return final,text[:12000]

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
        if len(out)>=8:
            break
    return out

def bing_rss(query,limit=8):
    params=urllib.parse.urlencode({"q":query,"format":"rss","count":str(limit)})
    url="https://www.bing.com/search?"+params
    _,_,raw=_open(url,accept="application/rss+xml,application/xml,text/xml,*/*",timeout=20)
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
    _,_,raw=_open("https://html.duckduckgo.com/html/?"+params,timeout=20)
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
    return out

def open_web_candidates(max_candidates=24, queries=None):
    """Discover relevant public pages across the open web, with no topic/contact gate.

    Query planning belongs to each agent. Search engines are discovery transports,
    not preferred markets or a hard-coded set of buyer websites. Pages without
    prices, buyer language, or email addresses remain legitimate research signals.
    Public-source and contact validation belong to downstream commercial actions.
    """
    queries=_normalize_queries(queries)
    if not queries:
        return [], [{"source":"open_web_search","reason":"no_model_queries"}]

    errors=[]
    streams=[]
    providers=(("bing",bing_rss),("duckduckgo",duckduckgo_html))
    # Alternate research directions before exhausting one query's providers.
    for provider,search in providers:
        for query in queries:
            try:
                hits=search(query,limit=6)
                if hits:
                    streams.append((query,provider,hits))
            except Exception as exc:
                errors.append({
                    "source":"open_web_search",
                    "provider":provider,
                    "reason":type(exc).__name__,
                })

    # Interleave searches so one query or search provider cannot consume the
    # entire result budget before other ideas have been investigated.
    queue=[]
    for index in range(max((len(hits) for _,_,hits in streams),default=0)):
        for query,provider,hits in streams:
            if index<len(hits):
                queue.append((query,provider,hits[index]))

    results={}
    visited=set()
    page_budget=min(72,max(24,max_candidates*3))
    for query,provider,hit in queue:
        if len(results)>=max_candidates or len(visited)>=page_budget:
            break
        url=str(hit.get("source_url") or "")
        if not _public_url(url) or url in visited:
            continue
        visited.add(url)
        title=str(hit.get("title") or "")
        snippet=str(hit.get("snippet") or "")
        try:
            final,page=fetch_public_text(url)
        except Exception as exc:
            # Preserve the search-engine observation when a site refuses
            # automated fetching, rather than erasing an otherwise useful lead.
            final,page=url,""
            if len(errors)<18:
                errors.append({
                    "source":"public_page_fetch",
                    "reason":type(exc).__name__,
                })
        if final in results:
            continue
        evidence=(title+"\n"+snippet+"\n"+page).strip()
        if not evidence:
            continue
        contact=safe_email(evidence)
        ident=hashlib.sha256(final.encode()).hexdigest()[:18]
        results[final]={
            "id":"web:"+ident,
            "channel":"email" if contact else None,
            "source":"open_web",
            "search_provider":provider,
            "search_query":query,
            "source_url":final,
            "title":title[:500],
            "snippet":snippet[:1600],
            "contact":contact,
            "text":evidence[:8000],
            "page_fetched":bool(page),
            "created_at":None,
        }
    return list(results.values()),errors
