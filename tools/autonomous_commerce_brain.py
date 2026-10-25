#!/usr/bin/env python3
"""Autonomous revenue engine.

Closed loop:
1. verify and fulfill paid autonomous orders;
2. discover current public paid demand;
3. choose one feasible contained digital offer;
4. create/recover Stripe checkout;
5. send the offer through the invitation's legitimate contact route;
6. repeat next run from provider-grounded outcomes.

The model chooses the work. Deterministic code owns payment, delivery, duplicate
protection, public evidence and provider truth.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from tools.connected_commerce_tools import (
    agent_send,
    autonomous_paid_orders,
    ensure_autonomous_checkout,
    recent_sent_subjects,
    stripe_usd_unit_amount,
    sent_subject_exists,
)

from tools.open_web_discovery import (
    fetch_public_text,
    open_web_candidates,
    safe_email,
)
from tools.research_memory import ResearchMemory

MODEL=os.getenv("COMMERCE_MODEL","qwen3:8b")
OLLAMA="http://127.0.0.1:11434/api/chat"
ROOT=Path("ops/enterprise-runs")
NOW=lambda: datetime.now(timezone.utc)

SEARCH_SCHEMA={
    "type":"object",
    "properties":{
        "queries":{
            "type":"array","minItems":4,"maxItems":8,
            "items":{"type":"string"},
        },
    },
    "required":["queries"],
    "additionalProperties":False,
}

PLAN_SCHEMA={
    "type":"object",
    "properties":{
        "candidate_id":{"type":"string"},
        "action":{"type":"string","enum":["offer","reject"]},
        "title":{"type":"string"},
        "scope":{"type":"string"},
        "deliverable":{"type":"string"},
        "price_usd":{"type":"string"},
        "pitch":{"type":"string"},
        "reason":{"type":"string"},
    },
    "required":["candidate_id","action","title","scope","deliverable",
                "price_usd","pitch","reason"],
    "additionalProperties":False,
}

FULFILL_SCHEMA={
    "type":"object",
    "properties":{
        "summary":{"type":"string"},
        "files":{
            "type":"array","minItems":1,"maxItems":3,
            "items":{
                "type":"object",
                "properties":{
                    "filename":{"type":"string"},
                    "content":{"type":"string"},
                },
                "required":["filename","content"],
                "additionalProperties":False,
            },
        },
    },
    "required":["summary","files"],
    "additionalProperties":False,
}

def model_json(schema,system,observation,num_ctx=4096,num_predict=700):
    req=urllib.request.Request(
        OLLAMA,method="POST",
        data=json.dumps({
            "model":MODEL,
            "stream":False,
            "format":schema,
            "think":False,
            "options":{"temperature":0.15,"num_ctx":num_ctx,"num_predict":num_predict},
            "messages":[
                {"role":"system","content":system},
                {"role":"user","content":json.dumps(observation,ensure_ascii=False)},
            ],
        }).encode(),
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req) as response:
        raw=json.load(response).get("message",{}).get("content","")
    if not isinstance(raw,str):
        raise ValueError("model_no_content")
    obj=json.loads(raw)
    if not isinstance(obj,dict):
        raise ValueError("model_not_object")
    return obj

def public_json(url,host):
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!="https" or parts.hostname!=host or parts.username or parts.password:
        raise ValueError("public_target_not_allowed")
    headers={"Accept":"application/json","User-Agent":"autonomous-revenue/1.0"}
    if host=="api.github.com" and os.getenv("GH_TOKEN"):
        headers["Authorization"]="Bearer "+os.environ["GH_TOKEN"]
        headers["X-GitHub-Api-Version"]="2022-11-28"
    req=urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read(500000).decode())

def github_json(endpoint,params=None):
    if os.getenv("GH_TOKEN"):
        cmd=["gh","api","--method","GET",
             "-H","Accept: application/vnd.github+json",
             "-H","X-GitHub-Api-Version: 2022-11-28",endpoint]
        for key,value in (params or {}).items():
            cmd.extend(["-f",str(key)+"="+str(value)])
        p=subprocess.run(cmd,capture_output=True,text=True,check=False)
        if p.returncode==0 and p.stdout.strip():
            return json.loads(p.stdout)
    query=urllib.parse.urlencode(params or {},doseq=True)
    return public_json("https://api.github.com/"+endpoint+
                       ("?"+query if query else ""),"api.github.com")

def clean_html(raw):
    text=re.sub(r"<br\s*/?>","\n",str(raw or ""),flags=re.I)
    text=re.sub(r"<[^>]+>"," ",text)
    return re.sub(r"[ \t]+"," ",unescape(text)).strip()

# Focus where active requests are visible. The seventh investigation looks
# for an actual public proposal route instead of another closed marketplace.
COMMUNITY_SITES=(
    ("Reddit","reddit.com"),
    ("Hacker News","news.ycombinator.com"),
    ("Indie Hackers","indiehackers.com"),
    ("GitHub Issues and Discussions","github.com"),
    ("Algora bounties","algora.io"),
    ("ProBlogger Jobs","problogger.com"),
)


def discovery_queries(memory):
    """Investigate active requester communities plus one reachable invitation."""
    system=(
        "Find current, genuine public requests for outside paid work or a useful "
        "finished digital deliverable. Generate SEVEN independent research queries "
        "in order: first one suited to Reddit, second Hacker News, third Indie "
        "Hackers, fourth GitHub Issues/Discussions, fifth Algora, sixth "
        "ProBlogger, and seventh a public request for quotations/proposals "
        "where a requester explicitly provides a public business email for "
        "submitting a bid. Choose fresh legitimate problems and industries "
        "without a fixed business model. Avoid aggregator-only job listings, "
        "closed job platforms requiring accounts we do not have, article roundups "
        "and vague advice searches. The system scopes the first six queries to "
        "their sites: supply natural phrases WITHOUT site: operators. The "
        "seventh is a general search with no forced host. Do not invent buyers "
        "or email addresses. Historical findings are hints, not required topics. "
        "Return JSON only."
    )
    result=model_json(
        SEARCH_SCHEMA,system,
        {"utc_date":NOW().date().isoformat(),
         "mission":"find requester demand with an executable public contact path",
         "research_history":memory.plan_context()},
        num_ctx=2500,num_predict=430)
    return [str(q).strip() for q in result.get("queries") or [] if str(q).strip()]


def candidates(memory):
    """Keep the focused sites; reserve capacity for genuinely reachable RFPs."""
    issues=[]
    try:
        proposed=discovery_queries(memory)
    except Exception as exc:
        proposed=[]
        issues.append({"source":"query_planner","reason":type(exc).__name__})

    if not proposed:
        proposed=memory.evidence_queries()
        if proposed:
            issues.append({"source":"query_planner",
                           "reason":"recovered_prior_public_research_directions"})

    scoped=[]
    for index,(_,host) in enumerate(COMMUNITY_SITES):
        if index>=len(proposed):
            break
        topic=re.sub(r"(?i)site:\\S+","",proposed[index]).strip()
        if topic:
            scoped.append("site:"+host+" "+topic)
    # The proposal/invitation search is independent of the selected communities,
    # but it is only one of seven routes, not the whole discovery strategy.
    invitation=[]
    if len(proposed)>len(COMMUNITY_SITES):
        phrase=re.sub(r"(?i)site:\\S+","",proposed[len(COMMUNITY_SITES)]).strip()
        if phrase:
            invitation=[phrase]

    fresh=memory.choose_queries(scoped+invitation)
    backlog=[str(q.get("query") or "") for q in memory.state.get("queued_queries") or []
             if isinstance(q,dict) and q.get("query")]
    queries=list(dict.fromkeys(fresh+backlog))[:7]
    memory.run_queries=queries
    memory.state["queued_queries"]=[
        {"query":q} for q in backlog if q not in queries][:25]

    hosts=tuple(domain for _,domain in COMMUNITY_SITES)
    focused=[q for q in queries if any(q.lower().startswith("site:"+h+" ") for h in hosts)]
    independent=[q for q in queries if q not in focused]
    budget=memory.budget()
    rows,errors=open_web_candidates(
        max_candidates=max(8,budget["max_candidates"]),
        queries=focused,memory=memory,
        max_pages=budget["max_pages"],strict_relevance=False,
        allowed_hosts=hosts)
    if independent:
        extra,extra_errors=open_web_candidates(
            max_candidates=8,queries=independent,memory=memory,
            max_pages=12,strict_relevance=False)
        existing={r.get("source_url") for r in rows}
        rows.extend(r for r in extra if r.get("source_url") not in existing)
        errors.extend(extra_errors)
    if not queries:
        issues.append({"source":"research_coverage","reason":"no_fresh_search_query"})
    if not rows:
        issues.append({"source":"research_coverage",
                       "reason":"no_new_public_sources",
                       "queries_attempted":queries})
    rows=enrich_public_response_route(rows)
    return rows,issues+errors


# A specific outside-work invitation may link to a separate contact page.
# This identifies possible routes; it never authorizes unsolicited outreach.
PUBLIC_INVITATION=re.compile(
    r"(?i)\b(?:request for proposals?|(?:open|accepting|seeking) (?:bids?|proposals?|vendors?|contractors?)"
    r"|(?:submit|send) (?:your |a )?(?:bid|proposal|quote|estimate)"
    r"|(?:looking for|seeking|hiring) (?:an? |someone |outside |external )?"
    r"(?:developer|designer|writer|consultant|agency|freelancer|contractor|provider|specialist)"
    r"|soliciting (?:proposals?|quotes?|bids?))\b"
)


def enrich_public_response_route(rows):
    """Look only at contact links on actual public requester invitations."""
    enriched=[]
    followed=0
    for source_row in rows:
        row=dict(source_row)
        if (row.get("contact") or not row.get("page_fetched")
                or not PUBLIC_INVITATION.search(str(row.get("text") or ""))
                or followed>=8):
            enriched.append(row)
            continue
        try:
            _,current,links=fetch_public_text(row["source_url"],with_links=True)
            if not PUBLIC_INVITATION.search(current):
                enriched.append(row)
                continue
            hostname=urllib.parse.urlsplit(row["source_url"]).hostname
            possible=[
                item for item in links
                if urllib.parse.urlsplit(item["url"]).hostname==hostname
                and re.search(r"(?i)(contact|inquir|apply|submit|proposal|bid|request|reach)",
                              str(item.get("title") or "")+" "+item["url"])
            ]
            for item in possible[:2]:
                if followed>=8:
                    break
                followed+=1
                try:
                    _,contact_page=fetch_public_text(item["url"])
                    address=safe_email(contact_page)
                    if address:
                        row["contact"]=address
                        row["contact_source_url"]=item["url"]
                        break
                except (ValueError,urllib.error.HTTPError,urllib.error.URLError):
                    continue
        except (ValueError,urllib.error.HTTPError,urllib.error.URLError):
            pass
        enriched.append(row)
    return enriched


def read_source(candidate):
    url=candidate["source_url"]
    if url.startswith("https://news.ycombinator.com/item?id="):
        ident=url.rsplit("=",1)[-1]
        row=public_json("https://hn.algolia.com/api/v1/items/"+ident,"hn.algolia.com")
        if str(row.get("id"))!=ident or row.get("deleted") or row.get("dead"):
            raise ValueError("source_no_longer_active")
        return clean_html(row.get("text") or "")[:7000]
    match=re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)",url)
    if match:
        owner,repo,num=match.groups()
        row=github_json("repos/"+owner+"/"+repo+"/issues/"+num)
        if row.get("state")!="open" or row.get("pull_request"):
            raise ValueError("source_no_longer_active")
        return (str(row.get("title") or "")+"\n"+str(row.get("body") or ""))[:7000]
    if url.startswith(("https://","http://")):
        _,text=fetch_public_text(url)
        if not text:
            raise ValueError("source_no_longer_active")
        return text[:7000]
    raise ValueError("unsupported_source")

def offer_marker(source_url):
    return hashlib.sha256(source_url.encode()).hexdigest()[:18]

def already_contacted(candidate,sent_subjects):
    marker=offer_marker(candidate["source_url"])
    return "Contained paid offer ["+marker+"]" in sent_subjects

def normalize_price_usd(value):
    """Normalize the model's freely chosen US-dollar price, without price bands."""
    raw=str(value if value is not None else "").strip().lower().replace(",","")
    raw=raw.removeprefix("$").removesuffix("usd").strip()
    try:
        dollars=Decimal(raw)
    except InvalidOperation:
        raise ValueError("model_price_invalid") from None
    if not dollars.is_finite() or dollars<0:
        raise ValueError("model_price_invalid")
    if dollars*100!=(dollars*100).to_integral_value():
        raise ValueError("model_price_subcent_precision")
    return format(dollars,".2f")


def choose_offer(rows):
    sent_subjects=recent_sent_subjects()
    # Relevance is assessed by the model using real evidence, not a fixed
    # industry/technology keyword score or a paid-ad vocabulary gate.
    researched=[row for row in rows if not already_contacted(row,sent_subjects)]
    # Keep all findings in research memory, but never spend slow CPU inference
    # trying to send a paid offer to a page with no genuine response channel.
    actionable=[row for row in researched
                if row.get("page_fetched") and row.get("contact")]
    if not actionable:
        return None,{"reason":"no_contactable_public_source"}
    ranked=actionable
    visible=[{
        "candidate_id":row["id"],
        "source_url":row["source_url"],
        "title":row.get("title") or "",
        "excerpt":row["text"][:420],
    } for row in ranked[:5]]

    system=(
        "You are Wolverine, an autonomous digital production operator researching "
        "open-web signals from any industry or community. The pages are research "
        "signals, NOT necessarily paid requests. Assess what people actually want, "
        "do not invent a buyer, a solicitation or a problem. You can develop a "
        "concrete paid service offer only when a source actually invites a relevant "
        "outside provider, has a public appropriate business contact route, and the "
        "requested work could be delivered using authorized tools. No unsolicited "
        "sales messages to unrelated site email addresses. Otherwise reject the "
        "available outreach while retaining the discovery evidence. A paid trial is "
        "reasonable when explicitly aligned with a buyer's own public request. "
        "Do not promise private access, employment, credentials, or human delivery. "
        "For any genuine offer define specific scope, usable deliverable, truthful "
        "pitch, and a commercially justifiable price chosen from the real scope. "
        "The price_usd field is a decimal string in US DOLLARS (e.g. 49.95). "
        "Do not impose a predetermined minimum, maximum, or price tier. "
        "Return only schema-compliant JSON."
    )
    plan=model_json(
        PLAN_SCHEMA,system,{"candidates":visible},num_ctx=2048,num_predict=265
    )
    if plan.get("action")!="offer":
        return None,{"reason":"no_verified_invitation:"+
                            str(plan.get("reason") or "model_rejected_candidates")[:112]}
    chosen=next((r for r in ranked if r["id"]==plan.get("candidate_id")),None)
    if not chosen:
        raise ValueError("model_selected_unknown_candidate")
    if not chosen.get("contact"):
        # Research without a public contact is still useful; it is never an
        # authorization to manufacture a customer address.
        return None,{"reason":"no_public_contact_for_selected_action"}
    price=normalize_price_usd(plan.get("price_usd"))
    for field in ("title","scope","deliverable","pitch"):
        if len(str(plan.get(field) or "").strip())<12:
            raise ValueError("model_offer_underspecified")
    plan["price_usd"]=price
    return chosen,plan

def send_offer(candidate,plan,checkout):
    if not candidate.get("contact"):
        return {"result":"no_public_contact_for_action","channel":"none"}
    marker=offer_marker(candidate["source_url"])
    price=Decimal(plan["price_usd"])
    body=(
        str(plan["pitch"]).strip()+"\n\n"
        "I can deliver: "+str(plan["deliverable"]).strip()+"\n"
        "Fixed price: $"+format(price,".2f")+"\n"
        "If useful, you can order it here: "+checkout["checkout_url"]+"\n\n"
        "I’ll send the completed deliverable to the email used at checkout."
    )
    subject="Contained paid offer ["+marker+"]"
    if sent_subject_exists(subject):
        return {"result":"already_contacted","channel":"email"}
    agent_send(candidate["contact"],subject,body,[],
               order_id="autonomous-offer-"+marker)
    return {"result":"offer_sent","channel":"email"}

def fulfillment_subject(session_id):
    marker=hashlib.sha256(str(session_id).encode()).hexdigest()[:18]
    return "Completed autonomous order ["+marker+"]"

def create_deliverable(order):
    source=read_source({"source_url":order["source_url"]})
    system=(
        "You are fulfilling a VERIFIED paid autonomous digital order. Produce the "
        "contained scope exactly, using only information in the public source and "
        "the purchased scope. Never invent access to private systems, credentials, "
        "test results, deployments, or external actions. Deliver useful finished "
        "text/code/data files, not promises or plans. Up to 3 files. Filenames may "
        "end only in .txt, .md, .csv, .json, .html, .py, .js, or .ts."
    )
    result=model_json(FULFILL_SCHEMA,system,{
        "purchased_scope":order["scope"],
        "public_source":source,
        "amount_paid_cents":order["amount_total"],
        "currency":order["currency"],
    },num_ctx=6144,num_predict=1800)
    files=[]
    for item in result.get("files") or []:
        name=str(item.get("filename") or "")
        content=str(item.get("content") or "")
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.(?:txt|md|csv|json|html|py|js|ts)",name)
                or not content or len(content)>150000):
            raise ValueError("invalid_fulfillment_file")
        files.append((name,content.encode()))
    if not files:
        raise ValueError("empty_fulfillment")
    return str(result.get("summary") or "Completed digital deliverable.")[:1200],files

def fulfill_orders():
    results=[]
    for order in autonomous_paid_orders()[:3]:
        session=order.get("session_id")
        if not session or not order.get("source_url") or not order.get("scope"):
            continue
        subject=fulfillment_subject(session)
        public_id=hashlib.sha256(str(session).encode()).hexdigest()[:18]
        if sent_subject_exists(subject):
            results.append({"order":public_id,"result":"already_fulfilled",
                            "amount_cents":order["amount_total"]})
            continue
        try:
            summary,files=create_deliverable(order)
            agent_send(order["customer_email"],subject,
                       summary+"\n\nThank you for your order.",files,
                       order_id="autonomous-fulfillment-"+public_id)
            results.append({"order":public_id,"result":"fulfilled",
                            "amount_cents":order["amount_total"],
                            "file_count":len(files)})
        except Exception as exc:
            results.append({"order":public_id,"result":"fulfillment_failed",
                            "reason":type(exc).__name__,
                            "amount_cents":order["amount_total"]})
    return results

def run():
    receipt={
        "schema_version":2,
        "engine":"autonomous_revenue_engine",
        "timestamp":NOW().isoformat(),
        "model":MODEL,
        "fulfillment":[],
        "discovery":{},
        "offer":None,
        "external_action":None,
        "structural_failures":[],
        "commercial_outcomes":[],
    }

    # Revenue already earned gets serviced first.
    try:
        receipt["fulfillment"]=fulfill_orders()
    except Exception as exc:
        receipt["structural_failures"].append({
            "reason":"paid_order_fulfillment_scan_failed",
            "detail":(str(exc) or type(exc).__name__)[:160]})

    memory=ResearchMemory("wolverine")
    try:
        rows,errors=candidates(memory)
        source_counts={}
        provider_counts={}
        for row in rows:
            source=str(row.get("source") or "supplemental")
            source_counts[source]=source_counts.get(source,0)+1
            provider=str(row.get("search_provider") or "")
            if provider:
                provider_counts[provider]=provider_counts.get(provider,0)+1
        receipt["discovery"]={
            "candidate_count":len(rows),
            "source_counts":source_counts,
            "search_provider_counts":provider_counts,
            "source_errors":errors,
            "fetched_public_pages":sum(bool(r.get("page_fetched")) for r in rows),
            "candidates_without_email":sum(not bool(r.get("contact")) for r in rows),
            "contactable_sources":sum(bool(r.get("contact")) for r in rows),
            "linked_requester_contacts":sum(bool(r.get("contact_source_url")) for r in rows),
            "potential_invitation_signals":sum(bool(PUBLIC_INVITATION.search(str(r.get("text") or ""))) for r in rows),
            "search_queries":list(dict.fromkeys(str(r.get("search_query") or "") for r in rows if r.get("search_query")))[:12],
            "attempted_search_queries":list(memory.run_queries)[:12],
        }
    except Exception as exc:
        rows=[]
        receipt["discovery"]={"candidate_count":0,
                              "source_errors":[{"reason":type(exc).__name__}]}

    # Persist real search progress before offer/payment work can consume the
    # remainder of the runner's execution window.
    receipt["discovery"]["research_memory"]=memory.save()

    if rows:
        try:
            candidate,plan=choose_offer(rows)
        except Exception as exc:
            candidate,plan=None,None
            detail=(str(exc) or type(exc).__name__)[:160]
            receipt["structural_failures"].append({
                "reason":"offer_selection_execution_failed",
                "detail":detail})
            receipt["commercial_outcomes"].append({
                "reason":"offer_selection_failed","detail":detail})
        if candidate and plan:
            try:
                checkout=ensure_autonomous_checkout(
                    candidate["source_url"],
                    plan["title"],plan["deliverable"],plan["scope"],
                    plan["price_usd"])
                receipt["offer"]={
                    "source_url":candidate["source_url"],
                    "title":plan["title"][:160],
                    "scope":plan["scope"][:400],
                    "price_usd":plan["price_usd"],
                    "price_cents":stripe_usd_unit_amount(plan["price_usd"]),
                    "offer_id":checkout["offer_id"],
                    "payment_link_id":checkout["payment_link_id"],
                }
                action=send_offer(candidate,plan,checkout)
                receipt["external_action"]={
                    "result":action["result"],
                    "channel":action["channel"],
                    "source_url":candidate["source_url"],
                }
                if action["result"]!="offer_sent":
                    receipt["commercial_outcomes"].append({"reason":action["result"]})
            except Exception as exc:
                receipt["structural_failures"].append({
                    "reason":"transaction_execution_failed",
                    "detail":(str(exc) or type(exc).__name__)[:160]})
        else:
            receipt["commercial_outcomes"].append({
                "reason":str((plan or {}).get("reason") or "no_feasible_contained_offer")[:140]})
    else:
        receipt["commercial_outcomes"].append({"reason":"no_research_results_found"})

    fulfilled=sum(x.get("result")=="fulfilled" for x in receipt["fulfillment"])
    revenue=sum(int(x.get("amount_cents") or 0)
                for x in receipt["fulfillment"]
                if x.get("result") in {"fulfilled","already_fulfilled"})
    receipt["economic_result"]={
        "new_offers_sent":1 if (receipt.get("external_action") or {}).get("result")=="offer_sent" else 0,
        "fulfilled_orders_seen":fulfilled,
        "verified_autonomous_revenue_cents_seen":revenue,
    }
    memory.note_outcome(receipt)
    receipt["discovery"]["research_memory"]=memory.save()
    return receipt

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    runid=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ID","local"))
    attempt=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ATTEMPT","1"))
    path=ROOT/(runid+"-"+attempt+".json")
    try:
        receipt=run()
    except Exception as exc:
        receipt={
            "schema_version":2,
            "engine":"autonomous_revenue_engine",
            "timestamp":NOW().isoformat(),
            "model":MODEL,
            "fulfillment":[],
            "discovery":{},
            "offer":None,
            "external_action":None,
            "structural_failures":[{"reason":"engine_runtime_failure",
                                    "detail":type(exc).__name__}],
            "commercial_outcomes":[],
            "economic_result":{"new_offers_sent":0,
                               "fulfilled_orders_seen":0,
                               "verified_autonomous_revenue_cents_seen":0},
        }
    path.write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "engine":receipt["engine"],
        "offers_sent":receipt["economic_result"]["new_offers_sent"],
        "fulfilled":receipt["economic_result"]["fulfilled_orders_seen"],
        "revenue_cents_seen":receipt["economic_result"]["verified_autonomous_revenue_cents_seen"],
        "structural_failures":len(receipt["structural_failures"]),
        "receipt":str(path),
    }))
    # The workflow persists evidence and invokes repair; the process itself does
    # not convert commercial misses into infrastructure failure.
    if receipt["structural_failures"]:
        raise SystemExit(2)

if __name__=="__main__":
    main()
