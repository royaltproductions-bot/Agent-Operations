#!/usr/bin/env python3
"""Cyclops: reusable digital-asset revenue lane.

One beam, many buyers:
1. fulfill verified purchases of the active reusable asset;
2. if no active asset exists, derive one from current explicit paid-demand signals;
3. persist the finished asset before any outreach;
4. create/recover one live Stripe checkout for that asset;
5. aim the same product at a new matching public contact each cycle;
6. leave a provider-grounded receipt.

This file is intentionally isolated from the bespoke Commerce Engine.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from tools.connected_commerce_tools import (
    agent_send,
    recent_sent_subjects,
    sent_subject_exists,
    stripe_get,
    stripe_post,
)

from tools.open_web_discovery import open_web_paid_candidates

MODEL=os.getenv("CYCLOPS_MODEL","qwen3:8b")
OLLAMA="http://127.0.0.1:11434/api/chat"
ROOT=Path("products/cyclops")
RECEIPTS=Path("ops/cyclops-runs")
NOW=lambda: datetime.now(timezone.utc)

EMAIL=re.compile(r"(?<![\w.+-])[\w.+-]{1,60}@[\w.-]{3,100}\.[A-Za-z]{2,16}\b")
RISKY=re.compile(
    r"\b(cve|exploit|malware|ransomware|phishing|credential theft|weapon|"
    r"medical diagnosis|therapy|legal representation|tax advice|onsite|on-site|"
    r"security clearance|adult content|gambling|controlled substance)\b",re.I)
CAPABLE=re.compile(
    r"\b(python|javascript|typescript|script|automation|workflow|api|csv|"
    r"spreadsheet|data|report|website|frontend|html|css|integration|testing|"
    r"quality assurance|content|marketing automation|research|documentation|"
    r"operations|process|template|checklist|planning)\b",re.I)
CONTRACT=re.compile(
    r"\b(contract(?:or)?(?:\s+(?:role|project|work|position))?|"
    r"freelanc(?:e|er)|consult(?:ing|ant)|project[- ]based|"
    r"paid pilot|paid task|paid project)\b",re.I)
EMPLOYMENT=re.compile(
    r"\b(full[- ]time|salary|annual compensation|annual salary|benefits|"
    r"employee|employment|equity|stock options|401k|pto|visa sponsorship)\b",re.I)

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

CONCEPT_SCHEMA={
    "type":"object",
    "properties":{
        "title":{"type":"string"},
        "description":{"type":"string"},
        "audience":{"type":"string"},
        "pitch":{"type":"string"},
        "price_cents":{"type":"string"},
        "target_terms":{
            "type":"array","minItems":3,"maxItems":8,
            "items":{"type":"string"},
        },
        "core_points":{
            "type":"array","minItems":5,"maxItems":12,
            "items":{"type":"string"},
        },
        "file_specs":{
            "type":"array","minItems":1,"maxItems":2,
            "items":{
                "type":"object",
                "properties":{
                    "filename":{"type":"string"},
                    "purpose":{"type":"string"},
                    "columns":{
                        "type":"array","maxItems":10,
                        "items":{"type":"string"},
                    },
                },
                "required":["filename","purpose","columns"],
                "additionalProperties":False,
            },
        },
    },
    "required":["title","description","audience","pitch","price_cents",
                "target_terms","core_points","file_specs"],
    "additionalProperties":False,
}

FILE_SCHEMA={
    "type":"object",
    "properties":{"content":{"type":"string"}},
    "required":["content"],
    "additionalProperties":False,
}

QUALITY_SCHEMA={
    "type":"object",
    "properties":{
        "verdict":{"type":"string","enum":["pass","reject"]},
        "reason":{"type":"string"},
    },
    "required":["verdict","reason"],
    "additionalProperties":False,
}

def model_json(schema,system,observation,timeout=240,num_ctx=6144,num_predict=1800):
    req=urllib.request.Request(
        OLLAMA,method="POST",
        data=json.dumps({
            "model":MODEL,
            "stream":False,
            "format":schema,
            "think":False,
            "options":{"temperature":0.18,"num_ctx":num_ctx,"num_predict":num_predict},
            "messages":[
                {"role":"system","content":system},
                {"role":"user","content":json.dumps(observation,ensure_ascii=False)},
            ],
        }).encode(),
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        raw=json.load(response).get("message",{}).get("content","")
    obj=json.loads(raw)
    if not isinstance(obj,dict):
        raise ValueError("model_not_object")
    return obj

def public_json(url,host):
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!="https" or parts.hostname!=host or parts.username or parts.password:
        raise ValueError("public_target_not_allowed")
    req=urllib.request.Request(url,headers={
        "Accept":"application/json","User-Agent":"cyclops-commerce/1.0"})
    with urllib.request.urlopen(req,timeout=25) as response:
        return json.loads(response.read(500000).decode())

def clean_html(raw):
    text=re.sub(r"<br\s*/?>","\n",str(raw or ""),flags=re.I)
    text=re.sub(r"<[^>]+>"," ",text)
    return re.sub(r"[ \t]+"," ",unescape(text)).strip()

def safe_email(text):
    emails=[x.lower() for x in EMAIL.findall(text)
            if not x.lower().endswith((".png",".jpg",".jpeg",".example",".test"))]
    unique=sorted(set(emails))
    return unique[0] if len(unique)==1 else None

def source_age_ok(timestamp,days=45):
    try:
        dt=datetime.fromisoformat(str(timestamp).replace("Z","+00:00"))
        return NOW()-dt<=timedelta(days=days)
    except (TypeError,ValueError):
        return False

def discovery_queries():
    """Generate fresh web-search language for reusable-product demand."""
    system=(
        "Generate diverse public-web search queries for CURRENT explicit paid needs "
        "where a reusable digital asset, toolkit, template, worksheet, reference, data "
        "resource, or similar finished product might solve a recurring problem for more "
        "than one buyer. Do not limit yourself to named platforms, industries, or a fixed "
        "menu of product types. Search for real buyer intent and budget/payment language, "
        "not generic product ideas. Vary the wording substantially so unrelated public "
        "domains and communities can surface. Include current date language when useful. "
        "Return only schema-compliant JSON."
    )
    result=model_json(
        SEARCH_SCHEMA,system,
        {"utc_date":NOW().date().isoformat(),
         "mission":"discover broad reusable-product demand across the public web"},
        timeout=90,num_ctx=2048,num_predict=260)
    return [str(q).strip() for q in result.get("queries") or [] if str(q).strip()]

def demand_candidates():
    rows=[]
    seen=set()

    # General open web first: arbitrary public domains returned by fresh model searches.
    queries=None
    try:
        queries=discovery_queries()
    except Exception:
        pass
    try:
        web_rows,_=open_web_paid_candidates(max_candidates=24,queries=queries)
        for row in web_rows:
            if row.get("source_url") not in seen:
                rows.append(row)
                seen.add(row.get("source_url"))
    except Exception:
        pass

    # HN remains one supplemental public source rather than the whole market.
    try:
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
        if stories:
            _,story=max(stories)
            thread=public_json(
                "https://hn.algolia.com/api/v1/items/"+story,"hn.algolia.com")
            for item in thread.get("children") or []:
                ident=str(item.get("id") or "")
                text=clean_html(item.get("text") or "")[:6000]
                if (not ident.isdigit() or not text or RISKY.search(text)
                        or not CONTRACT.search(text) or not CAPABLE.search(text)
                        or EMPLOYMENT.search(text)
                        or not re.search(
                            r"\b(apply|email|contact|reach out|send|write to|"
                            r"interested|inquire)\b",text,re.I)):
                    continue
                contact=safe_email(text)
                url="https://news.ycombinator.com/item?id="+ident
                if not contact or not source_age_ok(item.get("created_at")) or url in seen:
                    continue
                rows.append({
                    "id":"hn:"+ident,
                    "source":"hackernews",
                    "source_url":url,
                    "contact":contact,
                    "text":text,
                    "created_at":item.get("created_at"),
                })
                seen.add(url)
                if len(rows)>=24:
                    break
    except Exception:
        pass
    return rows[:24]

def normalize_price(value):
    raw=str(value or "").strip().lower().replace(",","")
    dollars=False
    if raw.startswith("$"):
        dollars=True
        raw=raw[1:].strip()
    if raw.endswith("usd"):
        dollars=True
        raw=raw[:-3].strip()
    try:
        number=Decimal(raw)
    except InvalidOperation:
        raise ValueError("price_invalid") from None
    if dollars or "." in raw or number<500:
        cents=int((number*100).quantize(Decimal("1")))
    else:
        cents=int(number)
    if not 500<=cents<=9900:
        raise ValueError("price_out_of_range")
    return cents

def asset_id(plan):
    seed=(str(plan["title"])+"|"+
          "|".join(sorted(str(x).lower() for x in plan["target_terms"]))).encode()
    return hashlib.sha256(seed).hexdigest()[:20]

def active_assets():
    assets=[]
    if not ROOT.is_dir():
        return assets
    for path in sorted(ROOT.glob("*/manifest.json")):
        try:
            row=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            continue
        if row.get("status")=="active" and row.get("asset_id"):
            row["_dir"]=str(path.parent)
            assets.append(row)
    return assets

def demand_view(rows,limit=5,width=700):
    return [{
        "candidate_id":r["id"],
        "source_url":r["source_url"],
        "excerpt":r["text"][:width],
    } for r in rows[:limit]]

def choose_asset_concept(rows,existing_assets=None):
    visible=demand_view(rows)
    if not visible:
        raise ValueError("no_current_demand")
    system=(
        "Choose ONE reusable digital product from recurring practical needs visible in the "
        "current independent-contract demand. This is product selection only; do not write "
        "the finished files yet. It must be useful to multiple similar buyers without buyer "
        "input, private data, custom work, credentials, or access. file_specs may use only "
        ".txt and .csv. core_points must contain the substantive knowledge/instructions needed "
        "to build a useful fallback asset if later generation fails. For CSV specs, columns "
        "must name the actual useful worksheet fields; for TXT specs, columns must be empty. "
        "Choose a one-time price from $5 to $99. price_cents must be digits only. Keep every "
        "field concise. Return only schema-compliant JSON."
    )
    attempts=[
        (visible,120,3072,650),
        (demand_view(rows,limit=3,width=450),180,2560,520),
    ]
    last=None
    for observation,timeout,ctx,predict in attempts:
        try:
            existing=[{
                "title":str(a.get("title") or "")[:120],
                "audience":str(a.get("audience") or "")[:160],
                "target_terms":[str(x)[:60] for x in (a.get("target_terms") or [])[:8]],
            } for a in (existing_assets or [])[:12]]
            plan=model_json(CONCEPT_SCHEMA,system,
                            {"current_paid_demand":observation,
                             "existing_catalog":existing,
                             "constraint":"Create a materially different product from the existing catalog; do not repeat or lightly rename an exhausted asset."},
                            timeout=timeout,num_ctx=ctx,num_predict=predict)
            break
        except Exception as exc:
            last=exc
    else:
        raise RuntimeError(
            "concept_generation_failed:"+
            (str(last) or type(last).__name__)[:100])

    plan["price_cents"]=normalize_price(plan.get("price_cents"))
    for field in ("title","description","audience","pitch"):
        if len(str(plan.get(field) or "").strip())<12:
            raise ValueError("asset_underspecified")
    specs=[]
    for spec in plan.get("file_specs") or []:
        name=str(spec.get("filename") or "")
        purpose=str(spec.get("purpose") or "").strip()
        columns=[str(x).strip() for x in (spec.get("columns") or []) if str(x).strip()]
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,72}\.(?:txt|csv)",name)
                or len(purpose)<8):
            raise ValueError("invalid_asset_spec")
        if name.endswith(".csv") and not columns:
            raise ValueError("csv_columns_missing")
        if name.endswith(".txt"):
            columns=[]
        specs.append({"filename":name,"purpose":purpose,"columns":columns})
    if not specs:
        raise ValueError("empty_asset_spec")
    plan["file_specs"]=specs
    plan["asset_id"]=asset_id(plan)
    return plan

def generate_asset_file(plan,spec,rows):
    system=(
        "Write the finished contents of exactly one reusable digital asset file. "
        "Use the supplied product concept, core points, file purpose, and current demand "
        "only as context. Do not write commentary around the file. Do not claim results, "
        "experience, credentials, private access, or buyer-specific work. For .csv output "
        "valid CSV beginning with exactly the requested columns and include a few generic "
        "template rows that are numerous and substantive enough to make the CSV useful on its own. "
        "Never invent production-ready configuration values for real systems. For .txt output "
        "create a detailed practical standalone toolkit, guide, worksheet, or template that a "
        "buyer can use immediately. The paid file must contain real working substance, not a "
        "demo or outline. Keep it compact enough to finish reliably."
    )
    observation={
        "product":{
            "title":plan["title"],
            "description":plan["description"],
            "audience":plan["audience"],
            "core_points":plan["core_points"],
        },
        "file":spec,
        "demand_context":demand_view(rows,limit=3,width=350),
    }
    attempts=[(120,3072,850),(180,2560,600)]
    for timeout,ctx,predict in attempts:
        try:
            obj=model_json(FILE_SCHEMA,system,observation,
                           timeout=timeout,num_ctx=ctx,num_predict=predict)
            content=str(obj.get("content") or "").strip()
            if content and len(content)<=80000:
                if spec["filename"].endswith(".csv"):
                    header=content.splitlines()[0] if content.splitlines() else ""
                    expected=",".join(spec.get("columns") or [])
                    if expected and header.replace(" ","")!=expected.replace(" ",""):
                        continue
                return content+"\n"
        except Exception:
            pass
    raise RuntimeError("asset_file_generation_failed:"+str(spec["filename"]))

def objective_quality_floor(plan,files):
    price=int(plan["price_cents"])
    total_chars=sum(len(content.strip()) for _,content in files)
    # Higher prices must carry visibly more substance.
    minimum_chars=1400 if price<2000 else 2400 if price<5000 else 3600
    if total_chars<minimum_chars:
        raise RuntimeError(
            f"quality_floor_too_thin:{total_chars}<{minimum_chars}")

    for name,content in files:
        lines=[line for line in content.splitlines() if line.strip()]
        if name.endswith(".csv"):
            # A paid standalone CSV should be more than a header plus a few examples.
            if len(lines)<10:
                raise RuntimeError(
                    f"quality_floor_csv_too_shallow:{name}:{len(lines)-1}_rows")
            columns=[x.strip() for x in lines[0].split(",") if x.strip()]
            if len(columns)<4:
                raise RuntimeError(
                    f"quality_floor_csv_too_narrow:{name}:{len(columns)}_columns")
        elif name.endswith(".txt"):
            words=len(re.findall(r"\b\w+\b",content))
            if words<250:
                raise RuntimeError(
                    f"quality_floor_text_too_short:{name}:{words}_words")

def buyer_quality_check(plan,files,rows):
    system=(
        "You are the final buyer-side quality check for a paid reusable digital product. "
        "Reject it unless a reasonable buyer in the stated audience could use the delivered "
        "files immediately and feel the price is defensible. Reject thin examples, generic "
        "filler, made-up production configuration values, vague checklists, or marketing "
        "claims stronger than what the files actually contain. The product must directly fit "
        "the observed demand, stand alone without custom service or private access, and the "
        "description/pitch must truthfully match the actual files. Do not rewrite the product. "
        "Return pass or reject with one concise reason."
    )
    observation={
        "product":{
            "title":plan["title"],
            "description":plan["description"],
            "audience":plan["audience"],
            "pitch":plan["pitch"],
            "price_cents":int(plan["price_cents"]),
            "target_terms":plan["target_terms"],
        },
        "files":[{
            "filename":name,
            "content":content[:12000],
        } for name,content in files],
        "demand_context":demand_view(rows,limit=3,width=500),
    }
    result=model_json(
        QUALITY_SCHEMA,system,observation,
        timeout=120,num_ctx=4096,num_predict=180)
    if result.get("verdict")!="pass":
        raise RuntimeError(
            "buyer_quality_rejected:"+
            str(result.get("reason") or "unspecified")[:140])
    return str(result.get("reason") or "passed")[:140]

def create_asset(rows,existing_assets=None):
    # If manufacturing fails at any point, restart the entire economic product
    # decision from current demand rather than shipping a generic fallback.
    failures=[]
    for attempt in range(1,4):
        try:
            plan=choose_asset_concept(rows,existing_assets)
            files=[]
            for spec in plan["file_specs"]:
                content=generate_asset_file(plan,spec,rows)
                if not content.strip():
                    raise RuntimeError("empty_generated_asset_file")
                files.append((spec["filename"],content))
            objective_quality_floor(plan,files)
            plan["_quality_reason"]=buyer_quality_check(plan,files,rows)
            plan["_files"]=files
            plan["_manufacturing_attempt"]=attempt
            return plan
        except Exception as exc:
            failures.append((str(exc) or type(exc).__name__)[:120])
    raise RuntimeError(
        "manufacturing_exhausted_after_3_full_restarts:"+" | ".join(failures))

def cyclops_checkout(plan):
    ident=plan["asset_id"]
    page=stripe_get("payment_links",{"limit":100})
    for link in page.get("data") or []:
        meta=link.get("metadata") or {}
        if meta.get("cyclops_asset_id")==ident and link.get("active") is True:
            return {"id":link.get("id"),"url":link.get("url")}
    product=stripe_post("products",{
        "name":str(plan["title"])[:120],
        "description":str(plan["description"])[:400],
        "metadata[cyclops_asset]":"yes",
        "metadata[cyclops_asset_id]":ident,
    },"cyclops-product-"+ident)
    price=stripe_post("prices",{
        "unit_amount":int(plan["price_cents"]),
        "currency":"usd",
        "product":product["id"],
    },"cyclops-price-"+ident)
    link=stripe_post("payment_links",{
        "line_items[0][price]":price["id"],
        "line_items[0][quantity]":"1",
        "metadata[cyclops_asset]":"yes",
        "metadata[cyclops_asset_id]":ident,
    },"cyclops-link-"+ident)
    if not str(link.get("url") or "").startswith("https://"):
        raise RuntimeError("cyclops_checkout_unconfirmed")
    return {"id":link.get("id"),"url":link.get("url")}

def github_put(path,text):
    token=os.getenv("GH_TOKEN","")
    repo=os.getenv("GITHUB_REPOSITORY","")
    if not token or "/" not in repo:
        raise RuntimeError("github_write_unavailable")
    url="https://api.github.com/repos/"+repo+"/contents/"+urllib.parse.quote(path,safe="/")
    headers={
        "Authorization":"Bearer "+token,
        "Accept":"application/vnd.github+json",
        "X-GitHub-Api-Version":"2022-11-28",
        "Content-Type":"application/json",
        "User-Agent":"cyclops-commerce/1.0",
    }
    sha=None
    try:
        req=urllib.request.Request(url,headers=headers)
        with urllib.request.urlopen(req,timeout=20) as response:
            current=json.load(response)
            sha=current.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code!=404:
            raise RuntimeError("github_asset_read_http_"+str(exc.code)) from None

    body={
        "message":"Publish Cyclops reusable asset",
        "content":base64.b64encode(text.encode()).decode(),
        "branch":"main",
    }
    if isinstance(sha,str) and sha:
        body["sha"]=sha
    req=urllib.request.Request(url,data=json.dumps(body).encode(),method="PUT",
                               headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("github_asset_write_http_"+str(exc.code)) from None

def persist_asset(plan,checkout):
    folder=ROOT/plan["asset_id"]
    folder.mkdir(parents=True,exist_ok=True)
    for name,content in plan["_files"]:
        (folder/name).write_text(content,encoding="utf-8")
    manifest={
        "schema_version":1,
        "lane":"cyclops",
        "status":"active",
        "asset_id":plan["asset_id"],
        "title":plan["title"],
        "description":plan["description"],
        "audience":plan["audience"],
        "pitch":plan["pitch"],
        "price_cents":plan["price_cents"],
        "target_terms":plan["target_terms"],
        "files":[name for name,_ in plan["_files"]],
        "quality_gate":"passed",
        "payment_link_id":checkout["id"],
        "checkout_url":checkout["url"],
        "created_at":NOW().isoformat(),
    }
    (folder/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    # Persist files before outreach so any buyer can always be fulfilled later.
    for name,content in plan["_files"]:
        github_put(f"products/cyclops/{plan['asset_id']}/{name}",content)
    github_put(
        f"products/cyclops/{plan['asset_id']}/manifest.json",
        json.dumps(manifest,indent=2)+"\n")
    manifest["_dir"]=str(folder)
    return manifest

def verified_session(session):
    if (session.get("status")!="complete" or session.get("payment_status")!="paid"
            or session.get("mode")!="payment"):
        return False
    intent=session.get("payment_intent")
    if not isinstance(intent,str) or not intent.startswith("pi_"):
        return False
    pi=stripe_get("payment_intents/"+urllib.parse.quote(intent,safe=""))
    charge_id=pi.get("latest_charge")
    if pi.get("status")!="succeeded" or not isinstance(charge_id,str):
        return False
    charge=stripe_get("charges/"+urllib.parse.quote(charge_id,safe=""))
    return bool(charge.get("paid") and charge.get("status")=="succeeded"
                and not charge.get("refunded")
                and int(charge.get("amount_refunded") or 0)==0)

def paid_orders():
    links={}
    page=stripe_get("payment_links",{"limit":100})
    for link in page.get("data") or []:
        meta=link.get("metadata") or {}
        if meta.get("cyclops_asset")=="yes" and meta.get("cyclops_asset_id"):
            links[link.get("id")]=meta.get("cyclops_asset_id")
    if not links:
        return []
    orders=[]
    sessions=stripe_get("checkout/sessions",{"limit":100})
    for session in sessions.get("data") or []:
        ident=links.get(session.get("payment_link"))
        if not ident:
            continue
        try:
            if not verified_session(session):
                continue
        except Exception:
            continue
        email=(session.get("customer_details") or {}).get("email") or session.get("customer_email")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",str(email or "")):
            continue
        orders.append({
            "session_id":session.get("id"),
            "asset_id":ident,
            "customer_email":email,
            "amount_total":int(session.get("amount_total") or 0),
            "currency":session.get("currency"),
        })
    return orders

def fulfill_orders():
    results=[]
    for order in paid_orders():
        marker=hashlib.sha256(str(order["session_id"]).encode()).hexdigest()[:18]
        subject="Cyclops delivery ["+marker+"]"
        if sent_subject_exists(subject):
            results.append({"result":"already_fulfilled","asset_id":order["asset_id"],
                            "amount_cents":order["amount_total"]})
            continue
        folder=ROOT/order["asset_id"]
        try:
            manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
            files=[]
            for name in manifest.get("files") or []:
                p=folder/name
                files.append((name,p.read_bytes()))
            if not files:
                raise ValueError("asset_files_missing")
            body=(
                "Thanks for your purchase. Your files are attached.\n\n"
                +str(manifest.get("title") or "Digital asset")
            )
            agent_send(order["customer_email"],subject,body,files,
                       order_id="cyclops-delivery-"+str(order["session_id"]))
            results.append({"result":"fulfilled","asset_id":order["asset_id"],
                            "amount_cents":order["amount_total"]})
        except Exception as exc:
            results.append({"result":"fulfillment_failed","asset_id":order["asset_id"],
                            "detail":(str(exc) or type(exc).__name__)[:160],
                            "amount_cents":order["amount_total"]})
    return results

def prospect_score(row,asset):
    text=str(row.get("text") or "").lower()
    target_hits=0
    score=0
    for term in asset.get("target_terms") or []:
        term=str(term).strip().lower()
        if term and term in text:
            target_hits+=1
            score+=8
    # Generic commerce words are only tie-breakers after a real product-term match.
    # They must never make an unrelated prospect look like a match for an old asset.
    if target_hits==0:
        return 0
    for token in ("project","contract","consult","freelance","automation","data",
                  "documentation","report","workflow","template"):
        if token in text:
            score+=2
    return score

def asset_offer_subject(row,asset):
    marker=hashlib.sha256(
        (asset["asset_id"]+"|"+row["source_url"]).encode()).hexdigest()[:18]
    return "Useful fixed asset ["+marker+"]"

def has_uncontacted_matching_prospect(rows,asset,sent_subjects):
    for row in rows:
        if prospect_score(row,asset)>0 and asset_offer_subject(row,asset) not in sent_subjects:
            return True
    return False

def send_asset_offer(rows,asset):
    sent=recent_sent_subjects()
    ranked=sorted(rows,key=lambda r:(-prospect_score(r,asset),r["source_url"]))
    for row in ranked:
        if prospect_score(row,asset)<=0:
            continue
        subject=asset_offer_subject(row,asset)
        marker=subject.rsplit("[",1)[-1].rstrip("]")
        if subject in sent:
            continue
        price=int(asset["price_cents"])/100
        body=(
            str(asset["pitch"]).strip()+"\n\n"
            "Product: "+str(asset["title"]).strip()+"\n"
            "One-time price: $"+format(price,".2f")+"\n"
            "Get it here: "+str(asset["checkout_url"])+"\n\n"
            "The purchase is the finished reusable asset, not a custom engagement."
        )
        agent_send(row["contact"],subject,body,[],
                   order_id="cyclops-offer-"+marker)
        return {"result":"offer_sent","source_url":row["source_url"],
                "asset_id":asset["asset_id"],"score":prospect_score(row,asset)}
    return {"result":"no_new_matching_contact","asset_id":asset["asset_id"]}

def write_receipt(receipt):
    RECEIPTS.mkdir(parents=True,exist_ok=True)
    rid=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ID","local"))
    attempt=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ATTEMPT","1"))
    path=RECEIPTS/f"{rid}-{attempt}.json"
    path.write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")

def run():
    receipt={
        "schema_version":1,
        "lane":"cyclops_reusable_assets",
        "timestamp":NOW().isoformat(),
        "model":MODEL,
        "asset":None,
        "discovery":{},
        "external_action":None,
        "fulfillment":[],
        "structural_failures":[],
        "economic_result":{
            "new_asset_created":0,
            "new_product_offers_sent":0,
            "fulfilled_orders_seen":0,
            "verified_cyclops_revenue_cents_seen":0,
        },
    }

    try:
        receipt["fulfillment"]=fulfill_orders()
    except Exception as exc:
        receipt["structural_failures"].append({
            "reason":"fulfillment_scan_failed",
            "detail":(str(exc) or type(exc).__name__)[:160]})

    try:
        rows=demand_candidates()
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
        }
    except Exception as exc:
        rows=[]
        receipt["discovery"]={"candidate_count":0,"reason":type(exc).__name__}

    assets=active_assets()
    asset=None
    if rows and assets:
        sent_subjects=recent_sent_subjects()
        ranked_assets=[]
        for existing in assets:
            if not has_uncontacted_matching_prospect(rows,existing,sent_subjects):
                continue
            best=max((prospect_score(row,existing) for row in rows),default=0)
            ranked_assets.append((best,existing))
        ranked_assets.sort(
            key=lambda item:(-item[0],str(item[1].get("asset_id") or "")))
        if ranked_assets and ranked_assets[0][0]>0:
            asset=ranked_assets[0][1]

    # No current product fits this demand: manufacture a new reusable asset.
    if rows and asset is None:
        try:
            plan=create_asset(rows,assets)
            checkout=cyclops_checkout(plan)
            asset=persist_asset(plan,checkout)
            receipt["economic_result"]["new_asset_created"]=1
        except Exception as exc:
            receipt["structural_failures"].append({
                "reason":"asset_creation_failed",
                "detail":(str(exc) or type(exc).__name__)[:160]})

    if asset:
        receipt["asset"]={
            "asset_id":asset["asset_id"],
            "title":asset["title"],
            "price_cents":int(asset["price_cents"]),
            "payment_link_id":asset["payment_link_id"],
        }
        if rows:
            try:
                action=send_asset_offer(rows,asset)
                receipt["external_action"]=action
                if action.get("result")=="offer_sent":
                    receipt["economic_result"]["new_product_offers_sent"]=1
            except Exception as exc:
                receipt["structural_failures"].append({
                    "reason":"product_distribution_failed",
                    "detail":(str(exc) or type(exc).__name__)[:160]})

    fulfilled=sum(x.get("result") in ("fulfilled","already_fulfilled")
                  for x in receipt["fulfillment"])
    revenue=sum(int(x.get("amount_cents") or 0)
                for x in receipt["fulfillment"]
                if x.get("result") in ("fulfilled","already_fulfilled"))
    receipt["economic_result"]["fulfilled_orders_seen"]=fulfilled
    receipt["economic_result"]["verified_cyclops_revenue_cents_seen"]=revenue
    write_receipt(receipt)
    print(json.dumps(receipt,indent=2))

if __name__=="__main__":
    try:
        run()
    except Exception as exc:
        write_receipt({
            "schema_version":1,
            "lane":"cyclops_reusable_assets",
            "timestamp":NOW().isoformat(),
            "structural_failures":[{
                "reason":"cyclops_runtime_failure",
                "detail":(str(exc) or type(exc).__name__)[:160],
            }],
            "economic_result":{
                "new_asset_created":0,
                "new_product_offers_sent":0,
                "fulfilled_orders_seen":0,
                "verified_cyclops_revenue_cents_seen":0,
            },
        })
        raise
