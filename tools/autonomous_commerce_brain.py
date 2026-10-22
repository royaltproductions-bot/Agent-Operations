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
    sent_subject_exists,
)

MODEL=os.getenv("COMMERCE_MODEL","qwen3:8b")
OLLAMA="http://127.0.0.1:11434/api/chat"
ROOT=Path("ops/enterprise-runs")
NOW=lambda: datetime.now(timezone.utc)

RISKY=re.compile(
    r"\b(cve|exploit|malware|ransomware|phishing|credential theft|weapon|"
    r"medical diagnosis|therapy|legal representation|tax advice|onsite|on-site|"
    r"security clearance|adult content|gambling|controlled substance)\b",re.I)
CAPABLE=re.compile(
    r"\b(python|javascript|typescript|script|automation|workflow|api|csv|"
    r"spreadsheet|data|report|website|frontend|html|css|integration|testing|"
    r"quality assurance|video edit|short[- ]form|animation|motion graphics|"
    r"content|marketing automation|research|documentation)\b",re.I)
CONTRACT=re.compile(
    r"\b(contract(?:or)?|freelanc(?:e|er)|consult(?:ing|ant)|project[- ]based|"
    r"paid pilot|part[- ]time|bounty|paid task|paid project)\b",re.I)
EMAIL=re.compile(r"(?<![\w.+-])[\w.+-]{1,60}@[\w.-]{3,100}\.[A-Za-z]{2,16}\b")

PLAN_SCHEMA={
    "type":"object",
    "properties":{
        "candidate_id":{"type":"string"},
        "action":{"type":"string","enum":["offer","reject"]},
        "title":{"type":"string"},
        "scope":{"type":"string"},
        "deliverable":{"type":"string"},
        "price_cents":{"type":"string"},
        "pitch":{"type":"string"},
        "reason":{"type":"string"},
    },
    "required":["candidate_id","action","title","scope","deliverable",
                "price_cents","pitch","reason"],
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

def model_json(schema,system,observation,timeout=180,num_ctx=4096,num_predict=700):
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
    with urllib.request.urlopen(req,timeout=timeout) as response:
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
    with urllib.request.urlopen(req,timeout=25) as response:
        return json.loads(response.read(500000).decode())

def github_json(endpoint,params=None):
    if os.getenv("GH_TOKEN"):
        cmd=["gh","api","--method","GET",
             "-H","Accept: application/vnd.github+json",
             "-H","X-GitHub-Api-Version: 2022-11-28",endpoint]
        for key,value in (params or {}).items():
            cmd.extend(["-f",str(key)+"="+str(value)])
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=35,check=False)
        if p.returncode==0 and p.stdout.strip():
            return json.loads(p.stdout)
    query=urllib.parse.urlencode(params or {},doseq=True)
    return public_json("https://api.github.com/"+endpoint+
                       ("?"+query if query else ""),"api.github.com")

def clean_html(raw):
    text=re.sub(r"<br\s*/?>","\n",str(raw or ""),flags=re.I)
    text=re.sub(r"<[^>]+>"," ",text)
    return re.sub(r"[ \t]+"," ",unescape(text)).strip()

def _safe_email(text):
    emails=[x.lower() for x in EMAIL.findall(text)
            if not x.lower().endswith((".png",".jpg",".jpeg",".example",".test"))]
    return sorted(set(emails))[0] if len(set(emails))==1 else None

def _source_age_ok(timestamp,days=45):
    try:
        dt=datetime.fromisoformat(str(timestamp).replace("Z","+00:00"))
        return NOW()-dt<=timedelta(days=days)
    except (TypeError,ValueError):
        return False

def hn_candidates():
    args=urllib.parse.urlencode({
        "tags":"story","query":"Ask HN: Who is hiring?","hitsPerPage":30})
    index=public_json("https://hn.algolia.com/api/v1/search_by_date?"+args,
                      "hn.algolia.com")
    stories=[]
    for row in index.get("hits") or []:
        title=str(row.get("title") or "")
        ident=str(row.get("objectID") or "")
        if ident.isdigit() and re.match(r"^Ask HN:\s*Who is hiring\?",title,re.I):
            stories.append((int(row.get("created_at_i") or 0),ident))
    if not stories:
        return []
    _,story=max(stories)
    thread=public_json("https://hn.algolia.com/api/v1/items/"+story,"hn.algolia.com")
    out=[]
    for item in thread.get("children") or []:
        ident=str(item.get("id") or "")
        text=clean_html(item.get("text") or "")[:6000]
        if (not ident.isdigit() or not text or RISKY.search(text)
                or not CONTRACT.search(text) or not CAPABLE.search(text)):
            continue
        email=_safe_email(text)
        if not email:
            continue
        out.append({
            "id":"hn:"+ident,
            "channel":"email",
            "source_url":"https://news.ycombinator.com/item?id="+ident,
            "contact":email,
            "text":text,
            "created_at":item.get("created_at"),
        })
        if len(out)>=12:
            break
    return out

def github_candidates():
    out={}
    for intent in ("bounty","paid contract","paid task","freelance"):
        data=github_json("search/issues",{
            "q":intent+" in:title,body is:issue is:open",
            "sort":"updated","order":"desc","per_page":20})
        for item in data.get("items") or []:
            if item.get("pull_request"):
                continue
            url=str(item.get("html_url") or "")
            if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+",url):
                continue
            text=(str(item.get("title") or "")+"\n"+
                  str(item.get("body") or ""))[:6000]
            if RISKY.search(text) or not CAPABLE.search(text):
                continue
            if not re.search(r"\b(bounty|paid|payment|budget|\$\s*\d|usd|compensation)\b",
                             text,re.I):
                continue
            email=_safe_email(text)
            if not email:
                continue
            out[url]={
                "id":"gh:"+hashlib.sha256(url.encode()).hexdigest()[:16],
                "channel":"email",
                "source_url":url,
                "contact":email,
                "text":text,
                "created_at":item.get("created_at"),
            }
            if len(out)>=12:
                break
        if len(out)>=12:
            break
    return list(out.values())

def candidates():
    rows=[]
    errors=[]
    for name,fn in (("hackernews",hn_candidates),("github",github_candidates)):
        try:
            found=fn()
            rows.extend(found)
        except Exception as exc:
            errors.append({"source":name,"reason":type(exc).__name__})
    rows=[r for r in rows if _source_age_ok(r.get("created_at"))]
    return rows[:20],errors

def read_source(candidate):
    url=candidate["source_url"]
    if url.startswith("https://news.ycombinator.com/item?id="):
        ident=url.rsplit("=",1)[-1]
        row=public_json("https://hn.algolia.com/api/v1/items/"+ident,"hn.algolia.com")
        if str(row.get("id"))!=ident or row.get("deleted") or row.get("dead"):
            raise ValueError("source_no_longer_active")
        return clean_html(row.get("text") or "")[:7000]
    match=re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)",url)
    if not match:
        raise ValueError("unsupported_source")
    owner,repo,num=match.groups()
    row=github_json("repos/"+owner+"/"+repo+"/issues/"+num)
    if row.get("state")!="open" or row.get("pull_request"):
        raise ValueError("source_no_longer_active")
    return (str(row.get("title") or "")+"\n"+str(row.get("body") or ""))[:7000]

def offer_marker(source_url):
    return hashlib.sha256(source_url.encode()).hexdigest()[:18]

def already_contacted(candidate,sent_subjects):
    marker=offer_marker(candidate["source_url"])
    return "Contained paid offer ["+marker+"]" in sent_subjects

def opportunity_score(row):
    """Cheap deterministic ranking before the model defines the transaction."""
    text=str(row.get("text") or "").lower()
    score=0
    positive={
        "script":8,"automation":10,"workflow":9,"api":8,"csv":8,
        "spreadsheet":7,"data":6,"report":6,"frontend":7,"html":5,
        "javascript":7,"python":8,"testing":6,"documentation":5,
        "video editing":7,"short-form":6,"research":4,"fixed":4,
        "project":4,"bounty":8,"budget":7,"paid":6,"$":5,
    }
    negative={
        "full-time":-12,"full time":-12,"salary":-10,"senior":-8,
        "staff engineer":-12,"years experience":-9,"long-term":-7,
        "long term":-7,"join our team":-10,"employment":-10,
        "equity":-5,"onsite":-12,"on-site":-12,
    }
    for token,value in positive.items():
        if token in text:
            score+=value
    for token,value in negative.items():
        if token in text:
            score+=value
    try:
        posted=datetime.fromisoformat(str(row.get("created_at")).replace("Z","+00:00"))
        age=max(0,(NOW()-posted).days)
        score+=max(0,10-min(age,10))
    except (TypeError,ValueError):
        pass
    return score

def choose_offer(rows):
    sent_subjects=recent_sent_subjects()
    ranked=sorted(
        (row for row in rows if not already_contacted(row,sent_subjects)),
        key=lambda row:(-opportunity_score(row),str(row.get("source_url") or ""))
    )
    visible=[{
        "candidate_id":row["id"],
        "source_url":row["source_url"],
        "excerpt":row["text"][:900],
    } for row in ranked[:3]]
    if not visible:
        return None,None

    system=(
        "You operate an autonomous fixed-scope digital production service. "
        "The candidates were already ranked from current public paid invitations. "
        "Choose ONE only if a useful deliverable can be produced entirely from public "
        "information plus text/code/data generation, without private-system access, "
        "human credentials, employment claims, or owner intervention. Prefer a small "
        "objective deliverable that can be completed after payment. If none fit, reject. "
        "For an offer, define a narrow scope, concrete deliverable, truthful short pitch, "
        "and one-time price from $5 to $500. Return only schema-compliant JSON."
    )
    plan=model_json(
        PLAN_SCHEMA,system,{"candidates":visible},
        timeout=150,num_ctx=3072,num_predict=420
    )
    if plan.get("action")!="offer":
        return None,plan
    chosen=next((r for r in ranked if r["id"]==plan.get("candidate_id")),None)
    if not chosen:
        raise ValueError("model_selected_unknown_candidate")
    price=int(str(plan.get("price_cents") or "0"))
    if not 500<=price<=50000:
        raise ValueError("model_price_out_of_range")
    for field in ("title","scope","deliverable","pitch"):
        if len(str(plan.get(field) or "").strip())<12:
            raise ValueError("model_offer_underspecified")
    return chosen,plan

def send_offer(candidate,plan,checkout):
    marker=offer_marker(candidate["source_url"])
    price=int(plan["price_cents"])/100
    disclosure=(
        "This is an AI-operated production service offering a contained fixed-scope "
        "digital deliverable; no employment relationship or unlisted credentials are implied.")
    body=(
        str(plan["pitch"]).strip()+"\n\n"
        "Scope: "+str(plan["scope"]).strip()+"\n"
        "Deliverable: "+str(plan["deliverable"]).strip()+"\n"
        "Fixed price: $"+format(price,".2f")+"\n"
        "Checkout: "+checkout["checkout_url"]+"\n\n"
        +disclosure+"\n"
        "Payment is treated as acceptance of this contained scope. "
        "The completed digital deliverable is sent to the email used at checkout."
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
    },timeout=300,num_ctx=6144,num_predict=1800)
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

    try:
        rows,errors=candidates()
        receipt["discovery"]={"candidate_count":len(rows),"source_errors":errors}
    except Exception as exc:
        rows=[]
        receipt["discovery"]={"candidate_count":0,
                              "source_errors":[{"reason":type(exc).__name__}]}

    if rows:
        try:
            candidate,plan=choose_offer(rows)
        except Exception as exc:
            candidate,plan=None,None
            receipt["commercial_outcomes"].append({
                "reason":"offer_selection_failed","detail":(str(exc) or type(exc).__name__)[:160]})
        if candidate and plan:
            try:
                checkout=ensure_autonomous_checkout(
                    candidate["source_url"],
                    plan["title"],plan["deliverable"],plan["scope"],
                    int(plan["price_cents"]))
                receipt["offer"]={
                    "source_url":candidate["source_url"],
                    "title":plan["title"][:160],
                    "scope":plan["scope"][:400],
                    "price_cents":int(plan["price_cents"]),
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
                "reason":"no_feasible_contained_offer"})
    else:
        receipt["commercial_outcomes"].append({"reason":"no_current_paid_demand_found"})

    fulfilled=sum(x.get("result")=="fulfilled" for x in receipt["fulfillment"])
    revenue=sum(int(x.get("amount_cents") or 0)
                for x in receipt["fulfillment"]
                if x.get("result") in {"fulfilled","already_fulfilled"})
    receipt["economic_result"]={
        "new_offers_sent":1 if (receipt.get("external_action") or {}).get("result")=="offer_sent" else 0,
        "fulfilled_orders_seen":fulfilled,
        "verified_autonomous_revenue_cents_seen":revenue,
    }
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
