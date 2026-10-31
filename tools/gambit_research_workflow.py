"""Checkpointed multi-session public-source research.

Builds verified source-backed records in bounded batches. No maximum buyer list
size, invented candidate profiles, or source snippets presented as read pages.
Buyer-specific working rows are encrypted in the public Git repository state.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import zlib

from cryptography.fernet import Fernet

PLAN_SCHEMA={"type":"object","properties":{
    "columns":{"type":"array","minItems":2,"maxItems":18,
               "items":{"type":"string"}},
    "queries":{"type":"array","minItems":2,"maxItems":8,
               "items":{"type":"string"}}},
    "required":["columns","queries"],"additionalProperties":False}
ROWS_SCHEMA={"type":"object","properties":{
    "rows":{"type":"array","maxItems":10,"items":{
        "type":"object","properties":{
            "cells":{"type":"array","items":{"type":"string"}},
            "evidence_quote":{"type":"string"},
            "source_url":{"type":"string"}},
        "required":["cells","evidence_quote","source_url"],
        "additionalProperties":False}}},
    "required":["rows"],"additionalProperties":False}


def _cipher():
    credential=os.getenv("BOUNTY_AGENT_API_KEY")
    if not credential:
        raise RuntimeError("research_checkpoint_key_missing")
    secret=hashlib.sha256(("gambit-research-progress-v1:"+credential).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(secret))


def _seal(obj):
    raw=json.dumps(obj,ensure_ascii=False).encode()
    return _cipher().encrypt(zlib.compress(raw,level=7)).decode()


def _unseal(raw):
    return json.loads(zlib.decompress(_cipher().decrypt(raw.encode())).decode())


def qualifies(details, requested_count, requested_formats):
    """The iterative path is for larger explicit sourced tables, not every task.

    Fifteen rows is a routing threshold for preserving work across calls,
    NEVER an acceptance/quantity limit.
    """
    if not requested_count or requested_count < 15:
        return False
    if not ({"csv","xlsx"} & set(requested_formats)):
        return False
    bounty=details.get("bounty") or {}
    text=" ".join(str(bounty.get(x) or "") for x in
                  ("title","description","verification")).lower()
    return bool(re.search(
        r"\b(?:research|find|source|identify|compile|list)\b",text))


def _clean_columns(columns):
    cleaned=[str(c).strip()[:90] for c in columns]
    if len(set(c.casefold() for c in cleaned))!=len(cleaned):
        raise ValueError("research_duplicate_columns")
    if not 2 <= len(cleaned) <= 18 or any(not c for c in cleaned):
        raise ValueError("research_invalid_columns")
    if not any(c.lower().replace(" ","_")=="source_url" for c in cleaned):
        cleaned.append("source_url")
    return cleaned


def _verified(entry,source,columns):
    url=str(source.get("url") or "")
    body=str(source.get("page_text") or "")
    quote=str(entry.get("evidence_quote") or "").strip()
    cells=entry.get("cells")
    if (not url or source.get("evidence_level")!="page_read" or
            not isinstance(cells,list) or len(cells)!=len(columns)-1 or
            len(quote)<20 or quote not in body):
        return None
    cleaned=[str(cell).strip()[:450] for cell in cells]
    if not cleaned or not cleaned[0] or not cleaned[0].casefold() in quote.casefold():
        return None
    # Names, cited URLs and LinkedIn profiles require source-page evidence.
    for name,value in zip(columns,cleaned):
        if re.search(r"(?:linkedin|profile).*(?:url|link)",name,re.I):
            if value and value not in body and value!=url:
                return None
        if value.startswith(("http://","https://")) and value not in body and value!=url:
            return None
    return {"cells":cleaned,"source_url":url,"evidence_quote":quote[:650]}


def _csv(rows,columns):
    out=io.StringIO(newline="")
    writer=csv.writer(out)
    writer.writerow(columns)
    for item in rows:
        writer.writerow(item["cells"]+[item["source_url"]])
    return out.getvalue()


def progress(details,state,bounty_id,claim_id,needed,infer,search):
    token=state.setdefault("research_projects",{}).get(bounty_id)
    project=_unseal(token) if token else {}
    if project.get("claim_id")!=claim_id:
        project={"claim_id":claim_id,"columns":[],"queries":[],"searched":[],
                 "rows":[],"rounds":0,"feedback":""}
    bounty=details.get("bounty") or {}
    brief={k:str(bounty.get(k) or "")[:8500]
           for k in ("title","description","verification")}
    if not project["columns"]:
        plan=infer(PLAN_SCHEMA,
            "Plan real PUBLIC evidence research for the buyer. Choose CSV "
            "headers that meet every required field, excluding source_url "
            "(added automatically). Generate varied targeted web queries that "
            "can find the exact requested real people/companies/records. "
            "Never fabricate profiles, people, results or contact data. "
            "Do not require a LinkedIn login or scrape restricted pages. JSON only.",
            {"brief":brief,"target_records":needed},
            num_predict=900,num_ctx=7500,fast=True)
        project["columns"]=_clean_columns(plan["columns"])
        project["queries"]=[q.strip()[:160] for q in plan["queries"]
                            if isinstance(q,str) and len(q.strip())>=8]
        if not project["queries"]:
            raise ValueError("research_queries_empty")
        state["research_projects"][bounty_id]=_seal(project)
        return {"status":"building","stage":"research_plan","completed":0,"total":needed}
    columns=project["columns"]
    if len(project["rows"])>=needed:
        data=_csv(project["rows"][:needed],columns)
        source_results=[{"url":r["source_url"],
                         "page_text":r["evidence_quote"],
                         "evidence_level":"page_read"}
                        for r in project["rows"][:needed]]
        fmt="xlsx" if "xlsx" in str(bounty.get("verification") or "").lower() else "csv"
        return {"status":"ready","reason":"","question":"",
                "deliverable_title":str(bounty.get("title") or "Research records")[:110],
                "deliverable_format":fmt,"deliverable_text":data,
                "_web_evidence":{"status":"verified","results":source_results}}
    remaining=[q for q in project["queries"] if q not in project["searched"]]
    if not remaining:
        previous=project["queries"][-8:]
        repl=infer(PLAN_SCHEMA,
            "Continue research with DIFFERENT public queries. The prior "
            "searches were insufficient. Do not fabricate names or claim "
            "sources that cannot be fetched. Preserve the original columns "
            "exactly. Return JSON.",
            {"brief":brief,"columns":columns[:-1],"previous_queries":previous,
             "verified_count":len(project["rows"]),"target":needed},
            num_predict=650,fast=True)
        project["queries"]=[q.strip()[:160] for q in repl.get("queries",[])
                            if isinstance(q,str) and len(q.strip())>=8
                            and q.strip() not in project["searched"]]
        if not project["queries"]:
            project["queries"]=previous
            project["searched"]=[]
        remaining=project["queries"]
    selected=remaining[:2]
    evidence=search(selected)
    project["searched"].extend(selected)
    sources=[source for source in evidence.get("results",[])
             if source.get("evidence_level")=="page_read" and
             source.get("url") and source.get("page_text")]
    known={tuple(row["cells"]) for row in project["rows"]}
    for source in sources[:3]:
        if len(project["rows"])>=needed:
            break
        response=infer(ROWS_SCHEMA,
            "Extract ONLY independently visible facts from THIS ACTUALLY "
            "READ public webpage. Never infer or fabricate identity, "
            "LinkedIn URL, job title, location, company, sales or contact. "
            "Return a candidate record only if each required field is "
            "present. cells must match the supplied columns, excluding "
            "source_url. Quote an EXACT passage containing the first column "
            "value and at least 20 characters. Every source_url must be "
            "the exact supplied public page URL. JSON only.",
            {"columns":columns[:-1],"source_url":source["url"],
             "page_text":str(source["page_text"])[:5800],
             "requested_count":needed-len(project["rows"])},
            num_predict=1300,num_ctx=7500,fast=True)
        for row in response.get("rows",[]):
            verified=_verified(row,source,columns)
            if not verified:
                continue
            fingerprint=tuple(verified["cells"])
            if fingerprint in known:
                continue
            known.add(fingerprint)
            project["rows"].append(verified)
            if len(project["rows"])>=needed:
                break
    project["rounds"]=int(project["rounds"])+1
    state["research_projects"][bounty_id]=_seal(project)
    return {"status":"building","stage":"source_verified_batch",
            "completed":len(project["rows"]),"total":needed,
            "pages_read":len(sources),"rounds":project["rounds"]}


def request_revision(state,bounty_id,feedback):
    token=state.get("research_projects",{}).get(bounty_id)
    if not token:
        return
    obj=_unseal(token)
    # Keep verified rows, revisit the failed acceptance criterion next cycle.
    obj["feedback"]=str(feedback)[:500]
    state["research_projects"][bounty_id]=_seal(obj)
