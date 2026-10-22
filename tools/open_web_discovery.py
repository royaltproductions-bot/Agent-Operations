#!/usr/bin/env python3
"""Open-web commercial discovery shared by autonomous revenue lanes.

Search is intentionally domain-agnostic. Search-engine results may point to any
public website; deterministic qualification decides whether a result is usable.
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
PAID=re.compile(
    r"\b(paid|budget|compensation|fee|rate|fixed[- ]price|bounty|contract)\b|"
    r"\$\s?\d|\bUSD\b",re.I)
BUYER_INTENT=re.compile(
    r"\b(looking for|seeking|need(?:ing)?|wanted|request(?:ing)?|"
    r"hire|hiring|contractor|freelancer|consultant|project|"
    r"request for proposal|\brfp\b|bounty)\b",re.I)
EMPLOYMENT=re.compile(
    r"\b(full[- ]time|salary|annual compensation|annual salary|benefits|"
    r"employee|employment|equity|stock options|401k|pto|visa sponsorship)\b",re.I)
RISKY=re.compile(
    r"\b(cve|exploit|malware|ransomware|phishing|credential theft|weapon|"
    r"medical diagnosis|therapy|legal representation|tax advice|security clearance|"
    r"adult content|gambling|controlled substance)\b",re.I)

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
    return final,clean_html(raw)[:12000]

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
    return unique[0] if len(unique)==1 else None

def _search_queries():
    year=str(NOW().year)
    pool=[
        f'"looking for" freelancer budget {year}',
        f'"seeking" contractor project budget {year}',
        f'"paid project" freelancer contact {year}',
        f'"need a consultant" project budget {year}',
        f'"request for proposal" budget contact {year}',
        f'bounty paid project contact {year}',
        f'"freelance project" email budget {year}',
        f'"contract project" email paid {year}',
        f'"looking to hire" contractor fixed price {year}',
        f'"project-based" consultant contact budget {year}',
        f'"seeking freelancer" fixed price {year}',
        f'"paid task" contact project {year}',
    ]
    # Rotate four queries by UTC hour so consecutive runs explore different slices.
    start=NOW().hour % len(pool)
    return [pool[(start+i*3)%len(pool)] for i in range(4)]

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

def open_web_paid_candidates(max_candidates=18):
    results={}
    errors=[]
    for query in _search_queries():
        try:
            hits=bing_rss(query,limit=8)
        except Exception as exc:
            errors.append({"source":"open_web_search","reason":type(exc).__name__})
            continue
        for hit in hits:
            url=hit["source_url"]
            if url in results:
                continue
            try:
                final,text=fetch_public_text(url)
            except Exception:
                continue
            combined=(hit.get("title","")+"\n"+hit.get("snippet","")+"\n"+text)[:16000]
            if RISKY.search(combined) or EMPLOYMENT.search(combined):
                continue
            if not BUYER_INTENT.search(combined) or not PAID.search(combined):
                continue
            contact=safe_email(combined)
            if not contact:
                continue
            ident=hashlib.sha256(final.encode()).hexdigest()[:18]
            results[final]={
                "id":"web:"+ident,
                "channel":"email",
                "source":"open_web",
                "source_url":final,
                "contact":contact,
                "text":text[:8000],
                "created_at":None,
            }
            if len(results)>=max_candidates:
                return list(results.values()),errors
    return list(results.values()),errors
