#!/usr/bin/env python3
"""Persistent, production-only open-web research state for Wolverine and Cyclops.

No fabricated candidates or simulation layer. Each independent GitHub Actions
execution reads previous real observations, explores new directions, and persists
its own state using the runner's existing GH_TOKEN. Transaction paths are separate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

NOW=lambda: datetime.now(timezone.utc)
LANES={"wolverine","cyclops"}
BASE="ops/research-state"


def _datetime(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (ValueError,TypeError):
        return datetime(1970,1,1,tzinfo=timezone.utc)


def _key(value):
    return re.sub(r"\s+"," ",str(value or "").strip()).casefold()


def _latest_unique(items,key,limit):
    by_key={}
    for item in items:
        if not isinstance(item,dict):
            continue
        val=str(item.get(key) or "").strip()
        if val:
            by_key[_key(val)]=item
    return list(by_key.values())[-limit:]


def _merge(old,new,lane):
    """Merge separately persisted observations, rather than overwrite a newer run."""
    merged={"schema_version":1,"lane":lane}
    for key,field,limit in (
        ("queries","query",800),
        ("pages","url",3500),
        ("frontier","url",140),
        ("observations","url",60),
        ("outcomes","run_id",100),
    ):
        values=list(old.get(key) or [])+list(new.get(key) or [])
        # Keep recent records, but preserve earlier visit counts when available.
        if key=="pages":
            combined={}
            for item in values:
                if not isinstance(item,dict) or not item.get("url"):
                    continue
                k=_key(item["url"])
                before=combined.get(k)
                if not before or _datetime(item.get("last_at"))>=_datetime(before.get("last_at")):
                    combined[k]=item
            merged[key]=sorted(combined.values(),key=lambda r:str(r.get("last_at") or ""))[-limit:]
        elif key=="frontier":
            # Remove links explored since they were first queued.
            visited={_key(p.get("url")) for p in list(old.get("pages") or [])+list(new.get("pages") or [])}
            merged[key]=[i for i in _latest_unique(values,field,limit) if _key(i.get("url")) not in visited]
        else:
            merged[key]=_latest_unique(values,field,limit)
    # Preserve unexecuted plans through load/read and conflict merging.
    merged["queued_queries"]=_latest_unique(
        list(old.get("queued_queries") or [])+list(new.get("queued_queries") or []),
        "query",30)
    merged["updated_at"]=NOW().isoformat()
    merged["executions"]=max(int(old.get("executions") or 0),int(new.get("executions") or 0))
    return merged


def _remote(lane):
    repo=os.getenv("GITHUB_REPOSITORY","")
    token=os.getenv("GH_TOKEN","")
    if lane not in LANES or "/" not in repo or not token:
        raise RuntimeError("research_memory_github_credentials_unavailable")
    path=f"{BASE}/{lane}.json"
    url=f"https://api.github.com/repos/{repo}/contents/{path}"
    headers={
        "Authorization":"Bearer "+token,
        "Accept":"application/vnd.github+json",
        "X-GitHub-Api-Version":"2022-11-28",
        "User-Agent":"xmen-research-memory/1.0",
    }
    return path,url,headers


def _get(lane):
    path,url,headers=_remote(lane)
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=20) as response:
            obj=json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code==404:
            return {},None
        raise
    raw=base64.b64decode(obj["content"].replace("\n",""))
    state=json.loads(raw.decode("utf-8"))
    if not isinstance(state,dict) or state.get("lane")!=lane:
        raise ValueError("research_state_lane_mismatch")
    return state,obj["sha"]


class ResearchMemory:
    def __init__(self,lane):
        if lane not in LANES:
            raise ValueError("unknown_research_lane")
        self.lane=lane
        self.errors=[]
        self.enabled=True
        self.last_persisted=False
        try:
            self.state,_=_get(lane)
        except Exception as exc:
            self.enabled=False  # Never overwrite unreadable earlier research.
            self.errors.append("load:"+type(exc).__name__)
            self.state={}
        self.state=_merge({},self.state,lane)
        self.state["executions"]=int(self.state.get("executions") or 0)+1
        self.peer=[]
        other="cyclops" if lane=="wolverine" else "wolverine"
        try:
            other_state,_=_get(other)
            self.peer=(other_state.get("observations") or [])[-5:]
        except Exception:
            pass
        self.run_queries=[]
        self.new_pages=0
        self.skipped_pages=0
        self.search_errors=0

    def plan_context(self):
        """Only external market research belongs in the search-planning prompt.

        Injecting engine outcome fields into the research prompt previously
        made Cyclops search for its own 'asset_creation_failed' error instead
        of actual market demand. Keep those real diagnostics in durable state,
        but out of external-inquiry planning.
        """
        return {
            "prior_market_searches":[
                {"query":x.get("query"),"new_public_pages":x.get("new_pages",0)}
                for x in (self.state.get("queries") or [])[-20:]
                if not self._is_internal_query(x.get("query"))],
            "recent_public_evidence_for_context_not_new_query_templates":[
                {"title":x.get("title"),"url":x.get("url"),"snippet":x.get("snippet")}
                for x in (self.state.get("observations") or [])[-6:]],
            "unvisited_public_links":[
                {"title":x.get("title"),"url":x.get("url")}
                for x in (self.state.get("frontier") or [])[-6:]],
            "peer_public_pages":[
                {"title":x.get("title"),"url":x.get("url")}
                for x in self.peer[-3:]],
            "directive":"Choose independent new investigations of real-world needs. Previous page titles are not tasks or prompts to copy. Ignore unrelated dictionary/SEO pages and abandon trails that only produce lookups, not substantive buyer problems.",
        }

    @staticmethod
    def _is_internal_query(value):
        """Exclude exposed internal bookkeeping, never legitimate market niches."""
        query=str(value or "").lower()
        return bool(re.search(
            r"asset_creation_failed|related_peer_findings|"
            r"\b(?:provided data|these records|these entries)\b|"
            r"\b(?:discovered|candidate_count) field\b|"
            r"\b(?:runtime|structural) (?:failures|errors) in (?:this|the) (?:data|record)",
            query,
        ))

    def budget(self):
        recent=(self.state.get("queries") or [])[-18:]
        yield_rates=[int(q.get("new_pages") or 0) for q in recent if isinstance(q,dict)]
        avg=(sum(yield_rates)/len(yield_rates)) if yield_rates else 2
        if avg<=0.5:
            return {"max_queries":5,"max_candidates":12,"max_pages":24}
        if avg>=3:
            return {"max_queries":4,"max_candidates":24,"max_pages":36}
        return {"max_queries":4,"max_candidates":16,"max_pages":30}

    def choose_queries(self,proposed):
        budget=self.budget()["max_queries"]
        recent={_key(q.get("query")):q for q in (self.state.get("queries") or [])}
        queued=[q.get("query") for q in self.state.get("queued_queries",[]) if isinstance(q,dict)]
        fresh=[]
        seen=set()
        # Model-authored current investigation takes priority over accumulated
        # queued followups. The queue is a backup, never a compulsory itinerary.
        for q in list(proposed or [])+list(queued):
            q=re.sub(r"\s+"," ",str(q or "").strip())[:220]
            k=_key(q)
            if len(q)<4 or k in seen or self._is_internal_query(q):
                continue
            seen.add(k)
            past=recent.get(k)
            # Any topic may be revisited after a cooling period; no permanent blacklist.
            if past and NOW()-_datetime(past.get("at"))<timedelta(days=1):
                continue
            fresh.append(q)
        # Alternate queued continuations with newly generated research ideas.
        self.run_queries=fresh[:budget]
        self.state["queued_queries"]=[{"query":q} for q in fresh[budget:budget+30]]
        return self.run_queries

    def evidence_queries(self):
        """Recover fresh searches from *real* recorded topics when the model stalls.

        Derived phrases are navigational continuations, never invented buyers
        or fabricated demand. Avoid hard-wired categories or preferred websites.
        """
        stop={"how","can","what","where","when","why","which","are","there",
              "is","the","a","an","to","of","for","in","on","by","and",
              "with","be","being","used","use","from","that","these","this",
              "it","do","does","their","could","would","should","best",
              "practices","ways","about","into","who"}
        known={_key(q.get("query")) for q in self.state.get("queries") or []}
        known.update(_key(q.get("query")) for q in self.state.get("queued_queries") or [])
        proposed=[]
        evidence=list(reversed(self.state.get("queries") or []))
        for row in evidence[:24]:
            words=re.findall(r"[\w-]+",str(row.get("query") or ""),re.UNICODE)
            kept=[w for w in words if len(w)>2 and w.lower() not in stop]
            q=" ".join(kept[:10]).strip()
            if len(kept)>=3 and _key(q) not in known and not self._is_internal_query(q):
                known.add(_key(q))
                proposed.append(q)
        # Public pages and queued links are actual observed evidence, not a
        # templated business model. Relevant names may open new directions.
        topics=list(reversed(self.state.get("frontier") or []))
        for item in topics[:12]:
            title=re.sub(r"[^\w\s-]"," ",str(item.get("title") or ""))
            words=title.split()
            q=" ".join(words[:9])
            if len(words)>=3 and _key(q) not in known:
                known.add(_key(q))
                proposed.append(q)
        return proposed[:12]

    def eligible_url(self,url):
        match=next((p for p in reversed(self.state.get("pages") or [])
                    if _key(p.get("url"))==_key(url)),None)
        if not match:
            return True
        if NOW()>=_datetime(match.get("retry_at")):
            return True
        self.skipped_pages+=1
        return False

    def observe_search(self,query,provider,hits,error=None):
        entries=self.state.setdefault("queries",[])
        qkey=_key(query)
        match=next((q for q in reversed(entries) if _key(q.get("query"))==qkey),None)
        if match:
            match["at"]=NOW().isoformat()
            match["attempts"]=int(match.get("attempts") or 0)+1
            match["hit_count"]=int(match.get("hit_count") or 0)+int(hits)
            if error:
                match["errors"]=int(match.get("errors") or 0)+1
        else:
            entries.append({
                "query":query,"at":NOW().isoformat(),"attempts":1,
                "hit_count":int(hits),"new_pages":0,"errors":1 if error else 0,
            })
        if error:
            self.search_errors+=1

    def observe_page(self,row,links=None):
        url=str(row.get("source_url") or "")
        if not url:
            return
        pages=self.state.setdefault("pages",[])
        prior=next((p for p in reversed(pages) if _key(p.get("url"))==_key(url)),None)
        old_hash=(prior or {}).get("content_hash")
        content=str(row.get("text") or "")
        digest=hashlib.sha256(content.encode()).hexdigest()[:16]
        unchanged=bool(old_hash and old_hash==digest)
        fetched=bool(row.get("page_fetched"))
        delay=timedelta(days=(21 if unchanged else 7) if fetched else 1)
        entry={
            "url":url[:1200],"title":str(row.get("title") or "")[:180],
            "last_at":NOW().isoformat(),
            "retry_at":(NOW()+delay).isoformat(),
            "visits":int((prior or {}).get("visits") or 0)+1,
            "content_hash":digest,"page_fetched":fetched,
            "search_query":str(row.get("search_query") or "")[:220],
        }
        if prior:
            pages.remove(prior)
        pages.append(entry)
        if not prior:
            self.new_pages+=1
            for q in reversed(self.state.get("queries") or []):
                if _key(q.get("query"))==_key(row.get("search_query")):
                    q["new_pages"]=int(q.get("new_pages") or 0)+1
                    break
        obs=self.state.setdefault("observations",[])
        if not unchanged and fetched:
            obs[:]=[x for x in obs if _key(x.get("url"))!=_key(url)]
            obs.append({"url":url[:1200],"title":entry["title"],
                        "snippet":str(row.get("snippet") or content[:220])[:300],
                        "at":entry["last_at"]})
        if links:
            frontier=self.state.setdefault("frontier",[])
            already={_key(p.get("url")) for p in pages}
            in_queue={_key(p.get("url")) for p in frontier}
            for link in links[:5]:
                target=str(link.get("url") or "")[:1200]
                if not target or _key(target) in already or _key(target) in in_queue:
                    continue
                frontier.append({"url":target,"title":str(link.get("title") or "")[:160],
                                 "from_url":url[:1200],"at":entry["last_at"]})
                in_queue.add(_key(target))
        self.state["frontier"]=[item for item in self.state.get("frontier",[])
                               if _key(item.get("url"))!=_key(url)][-140:]

    def frontier(self,limit=5):
        return list((self.state.get("frontier") or [])[-limit:])

    def note_outcome(self,receipt):
        econ=receipt.get("economic_result") or {}
        action=receipt.get("external_action") or {}
        row={"run_id":str(os.getenv("GITHUB_RUN_ID") or NOW().isoformat()),
             "at":NOW().isoformat(),
             "external_action":str(action.get("result") or "")[:70],
             "offers":int(econ.get("new_offers_sent") or econ.get("new_product_offers_sent") or 0),
             "verified_revenue_cents":int(econ.get("verified_autonomous_revenue_cents_seen") or econ.get("verified_cyclops_revenue_cents_seen") or 0),
             "failure_reasons":[str(x.get("reason"))[:75] for x in receipt.get("structural_failures") or []][:4],
             "discovered":int((receipt.get("discovery") or {}).get("candidate_count") or 0)}
        self.state.setdefault("outcomes",[]).append(row)

    def save(self):
        if not self.enabled:
            return {"persisted":False,"reason":"previous_research_unreadable",
                    "errors":self.errors[-3:]}
        try:
            path,url,headers=_remote(self.lane)
            for attempt in range(3):
                existing,sha=_get(self.lane)
                current=_merge(existing,self.state,self.lane)
                current["queued_queries"]=self.state.get("queued_queries",[])
                current["executions"]=max(int(existing.get("executions") or 0),
                                          int(self.state.get("executions") or 0))
                data={"message":"Persist "+self.lane+" research progression",
                      "branch":"main",
                      "content":base64.b64encode(
                          (json.dumps(current,separators=(",",":"),ensure_ascii=False)+"\n").encode()
                      ).decode()}
                if sha:
                    data["sha"]=sha
                request=urllib.request.Request(
                    url,data=json.dumps(data).encode(),headers={
                        **headers,"Content-Type":"application/json"
                    },method="PUT")
                try:
                    with urllib.request.urlopen(request,timeout=20):
                        pass
                    self.state=current
                    self.last_persisted=True
                    return {"persisted":True,"path":path,
                            "new_pages_this_run":self.new_pages,
                            "skipped_revisits":self.skipped_pages,
                            "executed_queries":self.run_queries,
                            "next_query_queue":len(current.get("queued_queries") or []),
                            "frontier_size":len(current.get("frontier") or []),
                            "known_pages":len(current.get("pages") or [])}
                except urllib.error.HTTPError as exc:
                    if exc.code in (409,422) and attempt<2:
                        continue
                    raise
        except Exception as exc:
            self.errors.append("save:"+type(exc).__name__)
            return {"persisted":False,"reason":type(exc).__name__,
                    "errors":self.errors[-3:]}
