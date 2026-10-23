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
                                 read_ledger, record_order,
                                 read_private, save_private)

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
            "type":"array","minItems":1,"maxItems":8,
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
        ".txt, .md, .csv, .json, .html, .css, .svg, .py, .js or .ts. "
        "Choose the format suitable for the actual product rather than a preset "
        "product category. core_points must contain the substantive instructions needed "
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
    # Verify that the cited source actually exists. Topic relevance is the
    # model's creative decision, not an arbitrary keyword-overlap quota.
    plan["evidence_source_url"]=source["source_url"]
    plan["price_usd"]=normalize_price_usd(plan.get("price_usd"))
    for field in ("title","description","audience","pitch"):
        if not str(plan.get(field) or "").strip():
            raise ValueError("required_product_field_empty:"+field)
    specs=[]
    for spec in plan.get("file_specs") or []:
        name=str(spec.get("filename") or "")
        purpose=str(spec.get("purpose") or "").strip()
        columns=[str(x).strip() for x in (spec.get("columns") or []) if str(x).strip()]
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,72}\.(?:txt|md|csv|json|html|css|svg|py|js|ts)",name)
                or not purpose.strip()):
            raise ValueError("invalid_asset_spec")
        if name.endswith(".csv") and not columns:
            raise ValueError("csv_columns_missing")
        if not name.endswith(".csv"):
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
        "For other text-based products: return the complete valid source/content "
        "in content, not an explanation. Never execute generated code. "
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
        # Constrained decoding must know the actual worksheet width.
        # Python still performs the definitive CSV serialization/validation.
        width=len(spec.get("columns") or [])
        schema={
            "type":"object","properties":{
                "rows":{"type":"array","minItems":1,
                        "items":{"type":"array","minItems":width,
                                 "maxItems":width,"items":{"type":"string"}}}},
            "required":["rows"],"additionalProperties":False,
        }
        reply=model_json(schema,system,context,num_ctx=5120,num_predict=2400)
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
    """Verify actual deliverable integrity, not projected buyer enthusiasm.

    Content quality is created by the production model. The publisher checks
    what software can objectively check: files exist, declared formats parse,
    and packages contain their promised components. No price, row-count,
    arbitrary word-count, or subjective consumer-demand tests.
    """
    if not files:
        raise ValueError("missing_product_files")
    specs={str(x["filename"]):x for x in plan.get("file_specs") or []}
    delivered={name for name,_ in files}
    if delivered!=set(specs):
        raise ValueError("product_file_manifest_mismatch")
    for name,content in files:
        if not isinstance(content,str) or not content.strip():
            raise ValueError("empty_deliverable:"+name)
        if name.endswith(".csv"):
            import csv
            from io import StringIO
            try:
                reader=csv.reader(StringIO(content),strict=True)
                records=list(reader)
            except csv.Error:
                raise ValueError("invalid_csv:"+name) from None
            expected=[str(c).strip() for c in specs[name].get("columns") or []]
            if not expected or not records or [c.strip() for c in records[0]]!=expected:
                raise ValueError("csv_columns_mismatch:"+name)
            if len(records)<2 or any(len(row)!=len(expected) for row in records[1:]):
                raise ValueError("csv_records_invalid:"+name)
            if not any(any(c.strip() for c in row) for row in records[1:]):
                raise ValueError("csv_content_empty:"+name)
        elif name.endswith(".json"):
            try:
                json.loads(content)
            except ValueError:
                raise ValueError("invalid_json:"+name) from None
        elif name.endswith(".svg"):
            import xml.etree.ElementTree as ET
            try:
                root=ET.fromstring(content)
            except ET.ParseError:
                raise ValueError("invalid_svg:"+name) from None
            if root.tag.rsplit("}",1)[-1]!="svg":
                raise ValueError("not_an_svg_document:"+name)
        elif name.endswith(".py"):
            import ast
            try:
                ast.parse(content,filename=name)
            except SyntaxError:
                raise ValueError("invalid_python_syntax:"+name) from None
        elif name.endswith(".js"):
            # Parsing is safe: --check does not run AI-produced JavaScript.
            import subprocess
            parsed=subprocess.run(["node","--check","-"],input=content,
                                  text=True,capture_output=True,check=False)
            if parsed.returncode!=0:
                raise ValueError("invalid_javascript_syntax:"+name)
        elif name.endswith(".html"):
            from html.parser import HTMLParser
            parser=HTMLParser(convert_charrefs=True)
            try:
                parser.feed(content)
                parser.close()
            except ValueError:
                raise ValueError("invalid_html:"+name) from None
        # Markdown, TXT, CSS and TS can contain valid non-executable data.
        # Do not claim they have undergone dynamic functional testing.
    return "required_files_present_and_format_checks_passed"


class ProductionPending(RuntimeError):
    """Durable draft has work left; not proof that product quality passed."""


class ProductRetired(RuntimeError):
    """Draft repeatedly failed objective checks and was archived securely."""


def _retire_draft(checkpoint,reason):
    """Release a permanently failing checkpoint without erasing evidence."""
    from tools.cyclops_vault import save_private
    plan=checkpoint.get("plan") or {}
    ident=str(plan.get("asset_id") or hashlib.sha256(
        str(plan.get("title") or "unspecified").encode()).hexdigest()[:20])
    save_private("ops/cyclops-vault/retired/"+ident+".json",{
        "plan":plan,"files":checkpoint.get("files") or [],
        "reason":reason,"attempts":checkpoint.get("attempts") or {},
        "revisions":checkpoint.get("revision") or 0,
        "retired_at":NOW().isoformat(),
    })
    save_pending({"stage":"empty"})
    raise ProductRetired("unusable_draft_retired:"+ident+":"+reason[:110])


def create_asset(rows,existing_assets=None):
    """Create an artifact, verify file integrity, persist, and return it.

    Technical defects receive one focused repair per execution and at most two
    repairs total. Model/API failures are recorded and bounded as well. Neither
    opinion about sales potential nor endless reviewer votes can block output.
    """
    checkpoint=read_pending()
    stage=checkpoint.get("stage")
    if stage in ("drafting","review","approved","sealed"):
        plan=checkpoint["plan"]
        evidence=checkpoint.get("research_rows") or rows
        files=dict(checkpoint.get("files") or [])
    else:
        if not rows:
            raise ValueError("no_public_research_to_select_product")
        plan=choose_asset_concept(rows,existing_assets)
        evidence=rows[:8]
        files={}
        checkpoint={"stage":"drafting","plan":plan,"files":[],
                    "research_rows":evidence,"revision":0,"attempts":{}}
        save_pending(checkpoint)

    # Retire the old draft trapped by the previous subjective review policy.
    if int(checkpoint.get("revision") or 0)>2:
        _retire_draft(checkpoint,"legacy_repeated_subjective_review")

    specs=plan["file_specs"]
    attempts=dict(checkpoint.get("attempts") or {})
    for spec in specs:
        name=spec["filename"]
        if name in files:
            continue
        try:
            content=generate_asset_file(plan,spec,evidence)
        except Exception as exc:
            why="generation_failed:"+type(exc).__name__+":"+str(exc)[:95]
            attempts[name]=int(attempts.get(name) or 0)+1
            checkpoint.update({"stage":"drafting","attempts":attempts,
                               "feedback":why,"files":list(files.items())})
            save_pending(checkpoint)
            if attempts[name]>=3:
                _retire_draft(checkpoint,why)
            raise ProductionPending(why) from exc
        files[name]=content
        checkpoint.update({"stage":"drafting","files":list(files.items()),
                           "feedback":""})
        save_pending(checkpoint)

    ordered=[(spec["filename"],files[spec["filename"]]) for spec in specs]
    try:
        result=objective_quality_floor(plan,ordered)
    except (ValueError,RuntimeError) as exc:
        why=str(exc)[:200]
        revisions=int(checkpoint.get("revision") or 0)
        if revisions>=2:
            _retire_draft(checkpoint,why)
        # Only repair the actual defective file, not every completed component.
        faulty=next((spec for spec in specs if spec["filename"] in why),None)
        if faulty is None:
            _retire_draft(checkpoint,why)
        checkpoint.update({"stage":"review","revision":revisions+1,
                           "feedback":why,"files":ordered})
        save_pending(checkpoint)
        try:
            files[faulty["filename"]]=revise_asset_file(
                plan,faulty,evidence,files[faulty["filename"]],why)
        except Exception as repair_exc:
            checkpoint["feedback"]="repair_failed:"+type(repair_exc).__name__
            save_pending(checkpoint)
            raise ProductionPending(checkpoint["feedback"]) from repair_exc
        ordered=[(spec["filename"],files[spec["filename"]]) for spec in specs]
        checkpoint.update({"stage":"review","files":ordered})
        save_pending(checkpoint)
        try:
            result=objective_quality_floor(plan,ordered)
        except (ValueError,RuntimeError) as check_exc:
            checkpoint["feedback"]=str(check_exc)[:200]
            save_pending(checkpoint)
            raise ProductionPending(checkpoint["feedback"]) from check_exc

    plan["_files"]=ordered
    plan["_quality_reason"]=result
    checkpoint.update({"stage":"approved","plan":plan,
                       "files":ordered,"feedback":""})
    save_pending(checkpoint)
    return plan

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
    completed=set((read_ledger().get("fulfilled") or {}).keys())
    for session in _stripe_pages("checkout/sessions"):
        ident=links.get(session.get("payment_link"))
        if not ident:
            continue
        marker=hashlib.sha256(str(session.get("id") or "").encode()).hexdigest()
        if marker in completed:
            # Already delivered, not a newly paid order. Avoid expensive
            # repeat payment-intent/charge lookups each hourly execution.
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


def durable_distribution_leads(new_rows):
    """Remember authentic, publicly invited contact possibilities across runs.

    Contact details and source evidence remain encrypted in the Cyclops vault,
    never in public campaign files. Source relevance and explicit invitation
    are still checked before any commercial message is sent.
    """
    path="ops/cyclops-vault/distribution.json"
    previous=read_private(path) or []
    if not isinstance(previous,list):
        raise RuntimeError("cyclops_distribution_queue_unreadable")
    by_url={}
    for item in previous:
        if isinstance(item,dict) and item.get("source_url"):
            by_url[item["source_url"]]=item
    changed=False
    for row in new_rows:
        if not row.get("contact") or not row.get("page_fetched"):
            continue
        url=str(row.get("source_url") or "")
        if not url:
            continue
        by_url[url]={
            "id":row["id"],"source_url":url,"source":"open_web",
            "title":str(row.get("title") or "")[:180],
            "text":str(row.get("text") or "")[:6000],
            "contact":row["contact"],"page_fetched":True,
            "search_query":str(row.get("search_query") or "")[:220],
        }
        changed=True
    # Old leads aren't permanently excluded; inbox-level duplicate checks
    # and source invitation verification protect against repeated messages.
    pool=list(by_url.values())[-320:]
    if changed:
        save_private(path,pool)
    return pool


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
        "schema_version":2,
        "lane":"cyclops_reusable_assets",
        "timestamp":NOW().isoformat(),
        "model":MODEL,
        "asset":None,
        "discovery":{},
        "production":{},
        "catalog":{},
        "external_action":None,
        "fulfillment":[],
        "structural_failures":[],
        "commercial_outcomes":[],
        "economic_result":{
            "new_asset_created":0,
            "new_product_offers_sent":0,
            "fulfilled_orders_seen":0,
            "verified_cyclops_revenue_cents_seen":0,
            "newly_observed_verified_paid_cents":0,
        },
    }

    # These are independent business activities, not a mandatory single funnel.
    # Payment verification and delivery always get first access to the runner.
    try:
        fulfillment=fulfill_orders()
        receipt["fulfillment"]=fulfillment["orders"]
        receipt["economic_result"]["fulfilled_orders_seen"]=fulfillment["newly_fulfilled_orders"]
        receipt["economic_result"]["newly_observed_verified_paid_cents"]=(
            fulfillment["newly_observed_verified_paid_cents"])
        receipt["economic_result"]["verified_cyclops_revenue_cents_seen"]=(
            fulfillment["newly_observed_verified_paid_cents"])
        if any(x.get("result")=="fulfillment_failed" for x in fulfillment["orders"]):
            receipt["structural_failures"].append({
                "reason":"verified_customer_fulfillment_incomplete"})
    except Exception as exc:
        receipt["structural_failures"].append({
            "reason":"fulfillment_scan_failed",
            "detail":(str(exc) or type(exc).__name__)[:160]})

    # Catalog distribution does not require a new product or a new email lead.
    try:
        receipt["catalog"]=publish_catalog()
    except Exception as exc:
        receipt["structural_failures"].append({
            "reason":"catalog_publication_failed",
            "detail":(str(exc) or type(exc).__name__)[:160]})

    memory=ResearchMemory("cyclops")
    try:
        # Drafts already carry their original source evidence. Continuing one
        # must not spend the runner's Qwen budget on unrelated new research.
        pending_at_start=read_pending()
        if (pending_at_start.get("stage")=="review"
                and int(pending_at_start.get("revision") or 0)>2):
            # The old subjective reviewer created a permanently stuck draft.
            # Retire it BEFORE deciding whether to search, so this same run
            # can originate a fresh deliverable rather than wasting an hour.
            try:
                _retire_draft(pending_at_start,"legacy_subjective_review_loop")
            except ProductRetired as retirement:
                receipt["commercial_outcomes"].append({
                    "reason":"legacy_unproductive_draft_archived",
                    "detail":str(retirement)[:145]})
            pending_at_start={"stage":"empty"}
        resuming=pending_at_start.get("stage") in ("drafting","review","approved","sealed")
        if resuming:
            rows,search_errors=[],[]
        else:
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
            "research_deferred_for_product_completion":resuming,
            "source_counts":source_counts,
            "search_provider_counts":provider_counts,
            "source_errors":search_errors,
            "fetched_public_pages":sum(bool(r.get("page_fetched")) for r in rows),
            "candidates_without_email":sum(not bool(r.get("contact")) for r in rows),
            "search_queries":list(dict.fromkeys(str(r.get("search_query") or "") for r in rows))[:12],
        }
    except Exception as exc:
        rows=[]
        receipt["discovery"]={"candidate_count":0,
                              "reason":(str(exc) or type(exc).__name__)[:160]}
    receipt["discovery"]["research_memory"]=memory.save()
    try:
        distribution_rows=durable_distribution_leads(rows)
        receipt["discovery"]["durable_contactable_sources"]=len(distribution_rows)
    except Exception as exc:
        distribution_rows=rows
        receipt["structural_failures"].append({
            "reason":"distribution_history_failed",
            "detail":(str(exc) or type(exc).__name__)[:160]})

    # Existing products are marketed independently of new manufacturing.
    assets=active_assets()
    marketable=[a for a in assets if a.get("status")=="active"
                and a.get("vault_path")
                and a.get("delivery")=="private_encrypted_vault"]
    for existing in marketable:
        if not distribution_rows:
            break
        try:
            action=send_asset_offer(distribution_rows,existing)
            if action.get("result")=="offer_sent":
                receipt["external_action"]=action
                receipt["economic_result"]["new_product_offers_sent"]+=1
                break
        except Exception as exc:
            receipt["structural_failures"].append({
                "reason":"catalog_distribution_failed",
                "detail":(str(exc) or type(exc).__name__)[:160]})

    # Product work is checkpointed and resumable across runs. A rejected draft
    # is unfinished work, not a reason to erase it or to stop existing sales.
    try:
        pending=read_pending()
        should_produce=bool(rows) or pending.get("stage") not in (None,"empty")
        if should_produce:
            plan=create_asset(rows,assets)
            asset=publish_finished_asset(plan)
            receipt["economic_result"]["new_asset_created"]=1
            receipt["asset"]={
                "asset_id":asset["asset_id"],"title":asset["title"],
                "price_usd":asset["price_usd"],"price_cents":asset["price_cents"],
                "payment_link_id":asset["payment_link_id"],
            }
            receipt["production"]={"result":"approved_and_published",
                                   "asset_id":asset["asset_id"]}
            # The store listing and checkout can now progress even without email.
            receipt["catalog"]=publish_catalog()
            if distribution_rows and receipt["external_action"] is None:
                action=send_asset_offer(distribution_rows,asset)
                receipt["external_action"]=action
                if action.get("result")=="offer_sent":
                    receipt["economic_result"]["new_product_offers_sent"]+=1
        else:
            receipt["production"]={"result":"no_current_product_opportunity"}
    except (ValueError,RuntimeError) as exc:
        reason=(str(exc) or type(exc).__name__)[:220]
        try:
            pending=read_pending()
            stage=pending.get("stage") or "empty"
        except Exception:
            pending={}
            stage="unreadable"
        if isinstance(exc,ProductRetired):
            receipt["production"]={"result":"invalid_draft_retired",
                                   "reason":reason,
                                   "new_concept_eligible_next_run":True}
        elif isinstance(exc,ProductionPending):
            receipt["production"]={
                "result":"objective_production_repair_pending",
                "stage":stage,
                "last_issue":str(pending.get("feedback") or reason)[:250],
                "attempts":pending.get("attempts") or {},
                "revisions":int(pending.get("revision") or 0),
                "files_finished":len(pending.get("files") or []),
                "files_planned":len((pending.get("plan") or {}).get("file_specs") or []),
            }
        elif stage in ("drafting","review","approved","sealed"):
            receipt["production"]={
                "result":"production_blocked",
                "stage":stage,"last_issue":reason,
                "revisions":int(pending.get("revision") or 0),
            }
            receipt["structural_failures"].append({
                "reason":"production_stage_error","detail":reason})
        elif reason.startswith(("no_public_research_to_select_product",
                                "product_unrelated_to_cited_public_evidence",
                                "product_source_not_in_real_research",
                                "asset_underspecified","csv_columns_missing",
                                "invalid_asset_spec","empty_asset_spec")):
            receipt["production"]={"result":"concept_not_producible",
                                   "reason":reason}
        else:
            receipt["production"]={"result":"production_error","reason":reason}
            receipt["structural_failures"].append({
                "reason":"production_or_publication_failed","detail":reason})
    except Exception as exc:
        receipt["structural_failures"].append({
            "reason":"production_unhandled_failure",
            "detail":(str(exc) or type(exc).__name__)[:160]})

    memory.note_outcome(receipt)
    receipt["discovery"]["research_memory"]=memory.save()
    write_receipt(receipt)
    print(json.dumps(receipt,indent=2))
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
