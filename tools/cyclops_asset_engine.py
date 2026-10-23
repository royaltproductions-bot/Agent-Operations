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
from html import unescape, escape
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
    stripe_usd_unit_amount,
)

from tools.open_web_discovery import open_web_candidates
from tools.research_memory import ResearchMemory
from tools.cyclops_vault import (read_pending, save_pending, seal_asset,
                                 read_asset_files, asset_vault_path,
                                 read_ledger, record_order)

MODEL=os.getenv("CYCLOPS_MODEL","qwen3:8b")
OLLAMA="http://127.0.0.1:11434/api/chat"
ROOT=Path("products/cyclops")
RECEIPTS=Path("ops/cyclops-runs")
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

CONCEPT_SCHEMA={
    "type":"object",
    "properties":{
        "title":{"type":"string"},
        "description":{"type":"string"},
        "audience":{"type":"string"},
        "pitch":{"type":"string"},
        "price_usd":{"type":"string"},
        "evidence_candidate_id":{"type":"string"},
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
    "required":["title","description","audience","pitch","price_usd",
                "evidence_candidate_id","target_terms","core_points","file_specs"],
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

def model_json(schema,system,observation,num_ctx=6144,num_predict=1800):
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
    with urllib.request.urlopen(req) as response:
        raw=json.load(response).get("message",{}).get("content","")
    obj=json.loads(raw)
    if not isinstance(obj,dict):
        raise ValueError("model_not_object")
    return obj

def discovery_queries(memory):
    """Choose broad product-market research directions at runtime."""
    system=(
        "You are Cyclops, researching opportunities for useful reusable digital "
        "products that can be sold to many buyers. Invent diverse current web "
        "searches about pain points, recurring work, unmet needs, active communities, "
        "new technology, customer frustrations, emerging interests and practical "
        "problems. Explore any legitimate industry and unfamiliar market. A need "
        "need not appear as a paid job posting or contain a price or contact email. "
        "Do not restrict research to preset niches, websites, job boards, template "
        "categories or marketplaces. Include a particular site only when genuinely "
        "relevant. Seek authentic evidence of repeatedly experienced problems "
        "that people are already trying to solve, obtain or buy rather than "
        "generic advice or dictionary pages. Let the market and products emerge "
        "from findings, never from predetermined niches. Use short precise "
        "search-engine phrases, not abstract how/why questions. Continue "
        "promising earlier avenues, develop queued unexplored "
        "sources, avoid repeating recent exhausted queries, and revisit pages only "
        "when changing evidence warrants it. Return schema-compliant JSON."
    )
    result=model_json(
        SEARCH_SCHEMA,system,
        {"utc_date":NOW().date().isoformat(),
         "mission":"find viable reusable digital-product demand from anywhere on the public web",
         "research_history":memory.plan_context()},num_ctx=2048,num_predict=170)
    return [str(q).strip() for q in result.get("queries") or [] if str(q).strip()]


def demand_candidates(memory):
    """Resume actual research first. Model planning is optional, never a blocker."""
    issues=[]
    # Queued queries are independent work discovered on earlier real runs.
    # Do NOT ask the model to invent queries while ready-to-run work exists.
    queries=memory.choose_queries([])
    if not queries:
        try:
            proposals=discovery_queries(memory)
            queries=memory.choose_queries(proposals)
        except Exception as exc:
            issues.append({"source":"query_planner","reason":type(exc).__name__})
        if not queries:
            # Only derive directions from past authentic source material.
            queries=memory.choose_queries(memory.evidence_queries())
    budget=memory.budget()
    rows,errors=open_web_candidates(
        max_candidates=budget["max_candidates"],queries=queries,
        memory=memory,
        max_pages=budget["max_pages"])
    return rows,issues+errors


def normalize_price_usd(value):
    """Normalize the agent-selected price in dollars without imposing any range."""
    raw=str(value if value is not None else "").strip().lower().replace(",","")
    raw=raw.removeprefix("$").removesuffix("usd").strip()
    try:
        dollars=Decimal(raw)
    except InvalidOperation:
        raise ValueError("price_invalid") from None
    if not dollars.is_finite() or dollars<0:
        raise ValueError("price_invalid")
    if dollars*100!=(dollars*100).to_integral_value():
        raise ValueError("price_subcent_precision")
    return format(dollars,".2f")


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
        "title":str(r.get("title") or "")[:180],
        "excerpt":r["text"][:width],
    } for r in rows[:limit]]

def choose_asset_concept(rows,existing_assets=None):
    visible=demand_view(rows)
    if not visible:
        raise ValueError("no_current_demand")
    system=(
        "Choose ONE reusable digital product addressing a concrete recurring buyer "
        "problem present in the supplied REAL research pages. Ground your choice "
        "in a SPECIFIC supplied candidate_id. Put that exact value in "
        "evidence_candidate_id; never invent a citation. The title, purpose and "
        "target terms MUST all be related to the cited page's real problem. "
        "Do NOT switch to unrelated topics, idioms, or sample-content themes. "
        "If the pages are weak, choose the most concretely useful problem "
        "without pretending a customer requested a purchase. This is "
        "product selection only; do not write the finished files yet. Market pages "
        "are signals rather than necessarily paid solicitations. It must be useful "
        "to multiple similar buyers without buyer "
        "input, private data, custom work, credentials, or access. file_specs may use only "
        ".txt and .csv. core_points must contain the substantive knowledge/instructions needed "
        "to build a useful fallback asset if later generation fails. For CSV specs, columns "
        "must name the actual useful worksheet fields; for TXT specs, columns must be empty. "
        "Choose a commercially defensible one-time price for the actual deliverable. "
        "price_usd must be a decimal string in US DOLLARS (e.g. 29.00 or 49.95). "
        "No preset product price band, minimum or maximum. "
        "Build a genuinely usable product, not an abstract concept or filler. "
        "Use concrete tasks and repeatable outputs supported by the cited market "
        "evidence. Prefer a single deliverable whose purpose, instructions and "
        "completion criteria are unambiguous; do not invent customers or records. "
        "Return only schema-compliant JSON."
    )
    # One substantive concept attempt. The 45-minute GitHub job is the
    # execution boundary, not a separate inference clock.
    attempts=[(visible,4096,1050)]
    last=None
    for observation,ctx,predict in attempts:
        try:
            existing=[{
                "title":str(a.get("title") or "")[:120],
                "audience":str(a.get("audience") or "")[:160],
                "target_terms":[str(x)[:60] for x in (a.get("target_terms") or [])[:8]],
            } for a in (existing_assets or [])[:12]]
            plan=model_json(CONCEPT_SCHEMA,system,
                            {"observed_market_signals":observation,
                             "existing_catalog":existing,
                             "constraint":"Create a materially different product from the existing catalog; do not repeat or lightly rename an exhausted asset."},
                            num_ctx=ctx,num_predict=predict)
            break
        except Exception as exc:
            last=exc
    else:
        raise RuntimeError(
            "concept_generation_failed:"+
            (str(last) or type(last).__name__)[:100])

    source_id=str(plan.get("evidence_candidate_id") or "")
    allowed={r["id"]:r for r in rows[:5]}
    source=allowed.get(source_id)
    if source is None:
        raise ValueError("product_source_not_in_real_research")
    # Cheap, source-neutral grounding check: an arbitrary industry is fine;
    # an unrelated product is not. No fixed niche/market whitelist.
    from tools.open_web_discovery import root_terms
    source_terms=root_terms(str(source.get("title") or "")+" "+str(source.get("text") or "")[:1200])
    product_terms=root_terms(str(plan.get("title") or "")+" "+
                             " ".join(str(x) for x in plan.get("target_terms") or []))
    overlap=product_terms.intersection(source_terms)
    if len(overlap)<2:
        raise ValueError("product_unrelated_to_cited_public_evidence")
    plan["evidence_source_url"]=source["source_url"]
    plan["price_usd"]=normalize_price_usd(plan.get("price_usd"))
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

def _csv_document(spec,rows):
    """Encode structured AI content as RFC-compatible CSV with Python, not AI quoting."""
    import csv
    from io import StringIO
    columns=[str(x).strip() for x in spec.get("columns") or []]
    if not columns:
        raise ValueError("csv_columns_missing")
    out=StringIO(newline="")
    writer=csv.writer(out)
    writer.writerow(columns)
    useful=0
    for record in rows:
        if not isinstance(record,list) or len(record)!=len(columns):
            raise ValueError("csv_fields_do_not_match_columns")
        values=[str(v).strip() for v in record]
        if not any(values):
            continue
        writer.writerow(values)
        useful+=1
    if not useful:
        raise ValueError("csv_no_useful_rows")
    return out.getvalue()


CSV_ROWS_SCHEMA={
    "type":"object",
    "properties":{
        "rows":{
            "type":"array",
            "items":{"type":"array","items":{"type":"string"}}
        }
    },
    "required":["rows"],"additionalProperties":False,
}


def _draft_file(plan,spec,rows,feedback="",previous=""):
    is_csv=spec["filename"].endswith(".csv")
    system=(
        "Produce one genuine, finished, reusable digital product component, "
        "grounded in the actual public market evidence. Never invent customers, "
        "orders, production URLs, revenues, benchmarks, results or credentials. "
        "Generate practical, accurate substantive content, not marketing copy "
        "or meaningless filler. This is a REAL commercial deliverable. "
        "For CSV: return a JSON array of data rows, each containing EXACTLY "
        "one string per specified column, in the exact declared order; do not "
        "include the CSV header in rows. The program will encode the CSV. "
        "For TXT: return the full useful final document in content. "
        "When repairing an earlier file, preserve genuinely valid work and "
        "address the actual feedback; do not abandon its purpose."
    )
    context={
        "product":{"title":plan["title"],"audience":plan["audience"],
                   "description":plan["description"],
                   "core_points":plan["core_points"],
                   "evidence_source_url":plan.get("evidence_source_url")},
        "spec":spec,
        "public_demand":demand_view(rows,limit=3,width=540),
        "actual_review_feedback":feedback[:400],
        "previous_work":previous[:14000],
    }
    if is_csv:
        reply=model_json(CSV_ROWS_SCHEMA,system,context,num_ctx=5120,num_predict=2400)
        result=_csv_document(spec,reply.get("rows") or [])
    else:
        reply=model_json(FILE_SCHEMA,system,context,num_ctx=5120,num_predict=2400)
        result=str(reply.get("content") or "").strip()+"\n"
    if not result.strip() or len(result)>120000:
        raise RuntimeError("generated_file_incomplete")
    return result


def generate_asset_file(plan,spec,rows):
    return _draft_file(plan,spec,rows)


def revise_asset_file(plan,spec,rows,previous,feedback):
    return _draft_file(plan,spec,rows,feedback=feedback,previous=previous)

def objective_quality_floor(plan,files):
    """Reject malformed/empty deliverables; let buyer QA judge substance.

    Number of rows, columns or words is not proof of commercial usefulness.
    A valid file's practical value is evaluated by buyer_quality_check.
    """
    if not files or any(not content.strip() for _,content in files):
        raise RuntimeError("quality_floor_empty_or_incomplete")
    by_name={spec["filename"]:spec for spec in plan.get("file_specs") or []}
    for name,content in files:
        if name.endswith(".csv"):
            import csv
            from io import StringIO
            try:
                parsed=list(csv.reader(StringIO(content)))
            except csv.Error:
                raise RuntimeError("quality_floor_invalid_csv:"+name) from None
            expected=[str(c).strip() for c in by_name[name].get("columns") or []]
            if not parsed or [c.strip() for c in parsed[0]]!=expected:
                raise RuntimeError("quality_floor_csv_headers_invalid:"+name)
            if len(parsed)<2 or any(len(row)!=len(expected) for row in parsed[1:]):
                raise RuntimeError("quality_floor_csv_rows_invalid:"+name)
            if not any(any(value.strip() for value in row) for row in parsed[1:]):
                raise RuntimeError("quality_floor_csv_content_empty:"+name)
        elif name.endswith(".txt"):
            if not content.strip():
                raise RuntimeError("quality_floor_text_empty:"+name)



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
            "price_usd":plan["price_usd"],
            "target_terms":plan["target_terms"],
            "evidence_source_url":plan.get("evidence_source_url"),
        },
        "files":[{
            "filename":name,
            "content":content[:12000],
        } for name,content in files],
        "demand_context":demand_view(rows,limit=3,width=500),
    }
    result=model_json(
        QUALITY_SCHEMA,system,observation,num_ctx=3072,num_predict=170)
    if result.get("verdict")!="pass":
        raise RuntimeError(
            "buyer_quality_rejected:"+
            str(result.get("reason") or "unspecified")[:140])
    return str(result.get("reason") or "passed")[:140]

def create_asset(rows,existing_assets=None):
    """Continue the actual commercial draft from its last persisted checkpoint."""
    checkpoint=read_pending()
    if checkpoint.get("stage") in ("drafting","review","approved","sealed"):
        plan=checkpoint["plan"]
        evidence=checkpoint.get("research_rows") or rows
        files=dict(checkpoint.get("files") or [])
    else:
        if not rows:
            raise ValueError("no_public_demand_for_product_creation")
        plan=choose_asset_concept(rows,existing_assets)
        evidence=rows[:8]
        files={}
        checkpoint={"stage":"drafting","plan":plan,"files":[],
                    "research_rows":evidence,"revision":0}
        save_pending(checkpoint)

    specs=plan["file_specs"]
    for spec in specs:
        name=spec["filename"]
        if name not in files:
            files[name]=generate_asset_file(plan,spec,evidence)
            checkpoint.update({"stage":"drafting","files":list(files.items())})
            save_pending(checkpoint)

    if checkpoint["stage"] in ("approved","sealed"):
        plan["_files"]=[(spec["filename"],files[spec["filename"]]) for spec in specs]
        return plan

    # Genuine buyer review can request a revision; save each stage so an
    # interrupted GitHub job does not erase the product or its actual evidence.
    for _ in range(2):
        ordered=[(spec["filename"],files[spec["filename"]]) for spec in specs]
        try:
            objective_quality_floor(plan,ordered)
            plan["_quality_reason"]=buyer_quality_check(plan,ordered,evidence)
            plan["_files"]=ordered
            checkpoint.update({"stage":"approved","files":ordered,"feedback":""})
            save_pending(checkpoint)
            return plan
        except (RuntimeError,ValueError) as exc:
            why=(str(exc) or type(exc).__name__)[:350]
            checkpoint.update({"stage":"review","feedback":why,
                               "revision":int(checkpoint.get("revision") or 0)+1})
            save_pending(checkpoint)
            for spec in specs:
                name=spec["filename"]
                files[name]=revise_asset_file(plan,spec,evidence,files[name],why)
                checkpoint["files"]=list(files.items())
                save_pending(checkpoint)
    raise RuntimeError("draft_review_incomplete_resume_next_execution")

def cyclops_checkout(plan):
    # The agent prices in dollars. Stripe consumes minor units at the boundary.
    plan["price_cents"]=stripe_usd_unit_amount(plan["price_usd"])
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
        with urllib.request.urlopen(req) as response:
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
        with urllib.request.urlopen(req) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("github_asset_write_http_"+str(exc.code)) from None

def persist_asset(plan,checkout):
    """Publish only the PUBLIC manifest; keep paid content encrypted in vault."""
    ident=plan["asset_id"]
    vault=asset_vault_path(ident)
    manifest={
        "schema_version":2,
        "lane":"cyclops","status":"active",
        "asset_id":ident,
        "title":plan["title"],
        "description":plan["description"],
        "audience":plan["audience"],
        "pitch":plan["pitch"],
        "price_usd":plan["price_usd"],
        "price_cents":plan["price_cents"],
        "target_terms":plan["target_terms"],
        "evidence_source_url":plan.get("evidence_source_url"),
        "files":[name for name,_ in plan["_files"]],
        "quality_gate":"passed",
        "delivery":"private_encrypted_vault",
        "vault_path":vault,
        "payment_link_id":checkout["id"],
        "checkout_url":checkout["url"],
        "created_at":NOW().isoformat(),
    }
    folder=ROOT/ident
    folder.mkdir(parents=True,exist_ok=True)
    # NEVER write a public plaintext deliverable, even temporarily.
    (folder/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    github_put(f"products/cyclops/{ident}/manifest.json",
               json.dumps(manifest,indent=2)+"\n")
    manifest["_dir"]=str(folder)
    return manifest


def publish_finished_asset(plan):
    """Secure deliverables before payment link creation and public listing."""
    digest=hashlib.sha256(json.dumps({
        "title":plan["title"],"price_usd":plan["price_usd"],
        "files":plan["_files"]},sort_keys=True).encode()).hexdigest()[:20]
    plan["asset_id"]=digest
    # If vault commit fails, NO Stripe checkout may be created.
    seal_asset(digest,plan)
    checkpoint=read_pending()
    checkpoint.update({"stage":"sealed","plan":plan})
    save_pending(checkpoint)
    checkout=cyclops_checkout(plan)
    asset=persist_asset(plan,checkout)
    # Once a recoverable public manifest exists, this draft is finished.
    save_pending({"stage":"empty"})
    return asset


def publish_catalog():
    """Independently expose approved catalog offers through existing storefront."""
    manifests=active_assets()
    # Old public-file products aren't considered paid-vault ready; do not
    # accidentally advertise work that is already freely readable.
    ready=[a for a in manifests if a.get("vault_path")
           and a.get("delivery")=="private_encrypted_vault"
           and a.get("quality_gate")=="passed"
           and str(a.get("checkout_url") or "").startswith("https://")]
    cards=[]
    for asset in sorted(ready,key=lambda a:str(a.get("created_at") or ""),reverse=True):
        name=escape(str(asset.get("title") or "Digital product"))
        desc=escape(str(asset.get("description") or ""))
        audience=escape(str(asset.get("audience") or ""))
        price=escape(str(asset.get("price_usd") or ""))
        link=escape(str(asset.get("checkout_url") or ""),quote=True)
        cards.append(
            '<article><h2>'+name+'</h2><p>'+desc+'</p>'
            '<p class="muted">For: '+audience+'</p>'
            '<p class="price">$'+price+'</p>'
            '<a class="buy" href="'+link+'" rel="noopener noreferrer">'
            'Buy securely with Stripe</a></article>')
    body="".join(cards) if cards else (
        '<p>New reusable tools are being prepared. '
        'No product is offered for purchase on this page yet.</p>')
    document='''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="index,follow">
<title>Reusable Digital Tools | Cyclops</title>
<meta name="description" content="Practical reusable digital tools sold through secure Stripe checkout and delivered privately.">
<style>
body{font:16px/1.6 system-ui,-apple-system,sans-serif;background:#f5f7f8;color:#182932;margin:0}
main{max-width:980px;margin:0 auto;padding:35px 20px 70px}
nav a{color:#195d69}h1{font-size:clamp(28px,5vw,48px);line-height:1.15}
section{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:20px}
article{background:white;border:1px solid #dbe3e7;border-radius:14px;padding:24px}
.muted{color:#61717a}.price{font-size:1.4rem;font-weight:700}
.buy{display:inline-block;background:#155f65;color:white;padding:11px 17px;border-radius:8px;text-decoration:none}
</style></head><body><main>
<nav><a href="/">Home</a></nav><h1>Cyclops reusable digital tools</h1>
<p>Finished products. Pay through Stripe and receive files privately after verified payment.</p>
<section>'''+body+'''</section>
<p>Questions? Contact ari-operations@agentmail.to. Product prices and terms are shown at checkout.</p>
</main></body></html>'''
    # Avoid an unnecessary storefront commit/deploy if the catalog has not changed.
    try:
        existing=(Path("storefront/cyclops/index.html").read_text(encoding="utf-8"))
    except OSError:
        existing=""
    if existing==document:
        return {"result":"catalog_unchanged","listed_products":len(ready)}
    dest=Path("storefront/cyclops/index.html")
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(document,encoding="utf-8")
    github_put("storefront/cyclops/index.html",document)
    return {"result":"catalog_published","listed_products":len(ready)}


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

def _stripe_pages(endpoint):
    """Follow Stripe's real has_more/starting_after cursor, not first 100 only."""
    cursor=None
    seen=set()
    while True:
        params={"limit":100}
        if cursor:
            params["starting_after"]=cursor
        page=stripe_get(endpoint,params)
        records=page.get("data") or []
        for record in records:
            yield record
        if not page.get("has_more") or not records:
            break
        last=str(records[-1].get("id") or "")
        if not last or last in seen:
            raise RuntimeError("stripe_pagination_cursor_stalled")
        seen.add(last)
        cursor=last


def paid_orders():
    links={}
    for link in _stripe_pages("payment_links"):
        meta=link.get("metadata") or {}
        if meta.get("cyclops_asset")=="yes" and meta.get("cyclops_asset_id"):
            links[link.get("id")]=meta.get("cyclops_asset_id")
    if not links:
        return []
    orders=[]
    for session in _stripe_pages("checkout/sessions"):
        ident=links.get(session.get("payment_link"))
        if not ident:
            continue
        # Fail closed: only verified, settled, unrefunded real Stripe payments.
        if not verified_session(session):
            continue
        email=(session.get("customer_details") or {}).get("email") or session.get("customer_email")
        orders.append({
            "session_id":session["id"],
            "asset_id":ident,
            "customer_email":email,
            "amount_total":int(session.get("amount_total") or 0),
            "currency":session.get("currency"),
        })
    return orders


def fulfill_orders():
    results=[]
    new_paid=0
    new_delivered=0
    for order in paid_orders():
        session=order["session_id"]
        amount=order["amount_total"]
        if record_order(session,amount,"paid"):
            new_paid+=amount
        marker=hashlib.sha256(str(session).encode()).hexdigest()[:18]
        subject="Cyclops delivery ["+marker+"]"
        if (hashlib.sha256(str(session).encode()).hexdigest()
                in (read_ledger().get("fulfilled") or {})) or sent_subject_exists(subject):
            record_order(session,amount,"fulfilled")
            results.append({"result":"already_fulfilled",
                            "asset_id":order["asset_id"],"amount_cents":amount})
            continue
        email=order.get("customer_email")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",str(email or "")):
            results.append({"result":"fulfillment_failed",
                            "asset_id":order["asset_id"],
                            "detail":"paid_order_has_no_valid_delivery_email",
                            "amount_cents":amount})
            continue
        try:
            folder=ROOT/order["asset_id"]
            manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
            if manifest.get("delivery")=="private_encrypted_vault":
                # File content is authenticated/decrypted only after payment.
                files=read_asset_files(order["asset_id"])
            else:
                # Backwards compatibility for pre-vault historic paid sessions.
                files=[(name,(folder/name).read_bytes())
                       for name in manifest.get("files") or []]
            if not files:
                raise ValueError("asset_files_missing")
            body=("Thanks for your purchase. Your files are attached.\n\n"
                  +str(manifest.get("title") or "Digital asset"))
            agent_send(email,subject,body,files,
                       order_id="cyclops-delivery-"+str(session))
            record_order(session,amount,"fulfilled")
            new_delivered+=1
            results.append({"result":"fulfilled",
                            "asset_id":order["asset_id"],"amount_cents":amount})
        except Exception as exc:
            results.append({"result":"fulfillment_failed",
                            "asset_id":order["asset_id"],
                            "detail":(str(exc) or type(exc).__name__)[:160],
                            "amount_cents":amount})
    return {"orders":results,
            "newly_observed_verified_paid_cents":new_paid,
            "newly_fulfilled_orders":new_delivered}


def prospect_score(row,asset):
    text=str(row.get("text") or "").lower()
    target_hits=0
    score=0
    for term in asset.get("target_terms") or []:
        term=str(term).strip().lower()
        if term and term in text:
            target_hits+=1
            score+=8
    # The product's own model-selected terms, not a fixed list of industries
    # or paid-contract vocabulary, determine similarity to new evidence.
    return score if target_hits else 0

def asset_offer_subject(row,asset):
    marker=hashlib.sha256(
        (asset["asset_id"]+"|"+row["source_url"]).encode()).hexdigest()[:18]
    return "Useful fixed asset ["+marker+"]"

def has_uncontacted_matching_prospect(rows,asset,sent_subjects):
    for row in rows:
        if (row.get("contact") and prospect_score(row,asset)>0
                and asset_offer_subject(row,asset) not in sent_subjects):
            return True
    return False

PROSPECT_SCHEMA={
    "type":"object",
    "properties":{
        "invited":{"type":"boolean"},
        "reason":{"type":"string"},
    },
    "required":["invited","reason"],
    "additionalProperties":False,
}

def can_contact_for_asset(row,asset):
    """Assess a specific outreach action; never narrow discovery itself."""
    if not row.get("contact") or not row.get("page_fetched"):
        return False
    system=(
        "Determine if this specific page EXPLICITLY invites outside commercial "
        "providers to propose or send a product like the supplied asset to the "
        "listed contact. Be conservative. An email address in a website footer, "
        "an informational article, a general discussion of a problem or a "
        "job post is NOT permission to send a sales pitch. If no explicit "
        "relevant invitation appears in the provided evidence, invited=false. "
        "Return schema-compliant JSON, no speculation."
    )
    try:
        decision=model_json(
            PROSPECT_SCHEMA,system,
            {"source_url":row["source_url"],"evidence":row["text"][:2600],
             "asset_title":asset["title"],"asset_description":asset["description"]},num_ctx=3072,num_predict=140)
        return decision.get("invited") is True
    except Exception:
        return False

def send_asset_offer(rows,asset):
    sent=recent_sent_subjects()
    ranked=sorted(rows,key=lambda r:(-prospect_score(r,asset),r["source_url"]))
    for row in ranked:
        if prospect_score(row,asset)<=0 or not row.get("contact"):
            continue
        subject=asset_offer_subject(row,asset)
        marker=subject.rsplit("[",1)[-1].rstrip("]")
        if subject in sent or not can_contact_for_asset(row,asset):
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
    return {"result":"no_relevant_opt_in_buyer","asset_id":asset["asset_id"]}

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

    memory=ResearchMemory("cyclops")
    try:
        rows,search_errors=demand_candidates(memory)
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
            "source_errors":search_errors,
            "fetched_public_pages":sum(bool(r.get("page_fetched")) for r in rows),
            "candidates_without_email":sum(not bool(r.get("contact")) for r in rows),
            "search_queries":list(dict.fromkeys(str(r.get("search_query") or "") for r in rows))[:12],
        }
    except Exception as exc:
        rows=[]
        receipt["discovery"]={"candidate_count":0,"reason":type(exc).__name__}

    # Write true research progression before product manufacturing spends the
    # remaining runner time, so an interrupted run does not repeat the crawl.
    receipt["discovery"]["research_memory"]=memory.save()

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
    memory.note_outcome(receipt)
    receipt["discovery"]["research_memory"]=memory.save()
    write_receipt(receipt)
    print(json.dumps(receipt,indent=2))
    # Preserve the actual receipt before signaling a production error.
    # Finding no demand or customer is NOT an infrastructure failure.
    if receipt["structural_failures"]:
        raise SystemExit(2)

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
