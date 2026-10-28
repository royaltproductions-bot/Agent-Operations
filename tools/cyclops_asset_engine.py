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

from tools.open_web_discovery import open_web_candidates, public_request_signal
from tools.cyclops_workshop import discover_tools, manufacture, _safe_path
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
        "target_terms":{"type":"array","items":{"type":"string"}},
        "core_points":{"type":"array","items":{"type":"string"}},
        "production_goal":{"type":"string"},
        "output_files":{"type":"array","minItems":1,"items":{"type":"string"}},
    },
    "required":["title","description","audience","pitch","price_usd",
                "evidence_candidate_id","target_terms","core_points",
                "production_goal","output_files"],
    "additionalProperties":False,
}

BUILD_SCRIPT_SCHEMA={
    "type":"object",
    "properties":{"script":{"type":"string"}},
    "required":["script"],"additionalProperties":False,
}

FILE_SCHEMA={
    "type":"object",
    "properties":{"content":{"type":"string"}},
    "required":["content"],
    "additionalProperties":False,
}

def model_json(schema,system,observation,num_ctx=6144,num_predict=1800,
               temperature=0.18):
    req=urllib.request.Request(
        OLLAMA,method="POST",
        data=json.dumps({
            "model":MODEL,
            "stream":False,
            "keep_alive":"60m",
            "format":schema,
            "think":False,
            "options":{"temperature":temperature,"num_ctx":num_ctx,"num_predict":num_predict},
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
    """Plan new open-web inquiries; old results inform, never dictate, the plan."""
    system=(
        "You are Cyclops's independent market researcher, not a format or "
        "product-category recommender. Find authentic public evidence of things "
        "people struggle to do, repeatedly attempt, seek, request, or need done. "
        "Choose search inquiries from ANY legitimate industry or activity. "
        "Find what real people are doing or asking, not just dictionary definitions, "
        "generic advice, an arbitrary named product, or how to buy an item. "
        "Generate web searches for genuine first-person discussions and "
        "observable workflows, not invented hypothetical questions that "
        "merely describe a plausible customer. "
        "Your goal is concrete firsthand problems and workflows that may inspire "
        "a reusable product, WITHOUT deciding the product in advance. "
        "Historical titles and queries are diagnostic context: do NOT mechanically "
        "turn them into new search terms. Ignore stale/unproductive trails and "
        "pivot freely. Produce distinct search phrases, phrased to reach "
        "substantive discussions, needs, comparisons, requests, or workflows "
        "rather than dictionary entries and SEO aggregators. "
        "Do not invent buyers or imply a page proves willingness to pay. "
        "No predefined industries, product forms, sources, price bands or websites. "
        "Return only schema-compliant JSON."
    )
    result=model_json(
        SEARCH_SCHEMA,system,
        {"utc_date":NOW().date().isoformat(),
         "mission":"discover real public evidence of unresolved, recurring or repeated human needs",
         "research_history":memory.plan_context()},
        num_ctx=2400,num_predict=420,temperature=0.7)
    return [str(q).strip() for q in result.get("queries") or [] if str(q).strip()]


def demand_candidates(memory):
    """Search independently each new research run; history is a fallback, not a trap."""
    issues=[]
    proposals=[]
    try:
        proposals=discovery_queries(memory)
    except Exception as exc:
        issues.append({"source":"query_planner","reason":type(exc).__name__})

    # Prioritize newly selected directions; do not let yesterday's queued
    # dictionary/look-up trails override autonomous discovery every hour.
    queries=memory.choose_queries(proposals)
    if not queries:
        # A model outage does not erase real public evidence or halt the web
        # research mechanism. Use observed prior leads only as a last resort.
        queries=memory.choose_queries(memory.evidence_queries())
    budget=memory.budget()
    rows,errors=open_web_candidates(
        max_candidates=budget["max_candidates"],queries=queries,
        memory=memory,max_pages=budget["max_pages"],
        strict_relevance=True)
    if not rows:
        issues.append({
            "source":"research_coverage",
            "reason":"no_new_eligible_public_pages",
            "queries_attempted":queries,
            "prior_unvisited_links":len(memory.frontier(limit=140)),
        })
    # Prioritize firsthand current demand over generic advice in the model's
    # small evidence window. This never excludes other industries or sources.
    rows.sort(key=lambda r:(not bool(r.get("page_fetched")),
                            -public_request_signal(r),
                            str(r.get("source_url") or "")))
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
        # Include fetched firsthand evidence, not only the search result
        # headline/snippet that may be promotional or out of context.
        "excerpt":str(r.get("page_text") or r.get("text") or "")[:width],
        "source_fetched":bool(r.get("page_fetched")),
    } for r in rows[:limit]]

def choose_asset_concept(rows,existing_assets=None):
    visible=demand_view(rows)
    if not visible:
        raise ValueError("no_current_demand")
    actual_tools=discover_tools()
    system=(
        "You independently invent and select one ORIGINAL reusable digital "
        "product addressing an opportunity observed in the REAL supplied pages. "
        "The consumer decides demand; you decide what to manufacture. "
        "Cite exactly one supplied candidate_id in evidence_candidate_id. "
        "First choose the product itself without preset business models or media. "
        "Then determine what real artifact(s) achieve its purpose. "
        "Inspect the ACTUAL discovered production tools supplied in context "
        "and choose files those tools can genuinely create. "
        "The subsequent builder writes a Python orchestrator that can USE "
        "the actual installed libraries and commands within an isolated "
        "production sandbox. That orchestration language is an implementation "
        "tool, NOT a restriction on the consumer product's form. "
        "Give specific production_goal instructions grounded in the product. "
        "output_files must be the actual final output paths, not the build "
        "script or example files; use simple relative paths only. "
        "The vault's current per-file and total size limits are technical "
        "storage capacity, not commercial or media preferences. "
        "Pricing is a chosen USD-dollar decimal string without invented bands. "
        "Don't invent customer records, fake external assets or claims. "
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
                             "available_production_tools":actual_tools,
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
    requested=plan.get("output_files") or []
    if not requested or len(requested)>30:
        raise ValueError("product_output_manifest_invalid")
    # The sandbox accepts /work/name and name as the SAME artifact.
    # Canonicalize once so the builder, verifier and vault agree on its name.
    names=[str(_safe_path(name)) for name in requested]
    if len(set(names))!=len(names):
        raise ValueError("duplicate_product_output_path")
    plan["output_files"]=names
    if not str(plan.get("production_goal") or "").strip():
        raise ValueError("product_production_goal_missing")
    plan["tool_inventory_at_selection"]=actual_tools
    plan["asset_id"]=asset_id(plan)
    return plan


def model_python(system,observation,num_ctx=7168,num_predict=5600):
    """Return build source directly; long scripts must not be JSON-escaped strings."""
    request=urllib.request.Request(
        OLLAMA,method="POST",
        data=json.dumps({
            "model":MODEL,
            "stream":False,
            "keep_alive":"60m",
            "think":False,
            "options":{"temperature":0.18,"num_ctx":num_ctx,"num_predict":num_predict},
            "messages":[
                {"role":"system","content":system},
                {"role":"user","content":json.dumps(observation,ensure_ascii=False)},
            ],
        }).encode(),
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request) as response:
        reply=json.load(response)
    script=str((reply.get("message") or {}).get("content") or "").strip()
    # An occasional Markdown wrapper should not waste an otherwise valid build.
    if script.startswith("```"):
        lines=script.splitlines()
        if len(lines)>2 and lines[-1].strip()=="```":
            script="\n".join(lines[1:-1]).strip()
    if not script:
        raise ValueError("production_model_returned_empty_script")
    if reply.get("done_reason")=="length":
        # A token-budget stop does not mean the source is necessarily broken.
        # The existing Python syntax and real workshop checks will decide.
        import ast
        try:
            ast.parse(script,filename="build.py")
        except SyntaxError:
            raise ValueError("production_script_generation_truncated") from None
    return script


def _make_build_script(plan,tools,evidence,target_file,finished_files,previous="",failure=""):
    """Ask the model HOW to use discovered tools for its OWN chosen product."""
    system=(
        "You are the hands-on production operator, not a sales reviewer. "
        "Write a concise, executable Python 3.12 script that creates ONE "
        "complete output file in /work: the target_file supplied below. "
        "The product can have multiple deliverables, but this execution "
        "manufactures just one; the other files are made in other executions. "
        "You may import any INSTALLED python modules or use INSTALLED command "
        "line tools shown in the supplied live inventory. Do not assume "
        "unlisted modules, network connectivity, credentials, or external "
        "services. Choose and invoke whatever available tools accomplish "
        "the PRODUCT as designed; Python is an orchestrator, not a product "
        "format. Create parent directories as needed. Create REAL binary "
        "media or documents through rendering tools when appropriate: never "
        "pretend UTF-8 text is a binary file. Do not fabricate third-party "
        "material, experimental results, customers, sales, or credentials. "
        "Do not execute scripts downloaded from the internet. "
        "When launching a child Python script, use sys.executable rather "
        "than attempting to directly execute its file path. For local "
        "binaries that you create, set an executable mode before running "
        "them; a generated file is not executable by default. "
        "For PDFs with non-ASCII characters, load a Unicode TrueType font "
        "instead of assuming built-in PDF fonts support those glyphs. "
        "Use verified installed-library APIs, not invented method names. "
        "For ReportLab, import ListFlowable and ListItem (there is no "
        "platypus.List); pass a ListFlowable containing Paragraphs or "
        "ListItems to SimpleDocTemplate.build, never a bare ListItem. "
        "For pypdf PdfWriter, use writer.write(output_path_or_stream), "
        "not writer.open_stream. "
        "Do not merely explain or describe production: the complete source "
        "you return WILL BE EXECUTED inside an isolated container. "
        "Never assume previously completed files exist in the current /work "
        "directory. Use their descriptions to maintain coherence with the "
        "whole product, and write the target output independently. "
        "A previous attempt may be supplied with an actual error; repair "
        "that same code and preserve working functionality. Return ONLY "
        "raw executable Python source, not JSON, explanatory text, or Markdown. "
        "Keep the implementation concise while producing every required file."
    )
    observation={
        "product":{"title":plan["title"],"description":plan["description"],
                   "audience":plan["audience"],"production_goal":plan["production_goal"],
                   "core_points":plan.get("core_points") or []},
        "actual_installed_tools":tools,
        "target_file":target_file,
        "complete_product_manifest":plan["output_files"],
        "already_generated_files":[{"filename":x["filename"],
                                   "size_bytes":x["size_bytes"]}
                                  for x in finished_files],
        "source_page":plan.get("evidence_source_url"),
        "source_excerpts":demand_view(evidence,limit=2,width=380),
        "prior_build_script":previous[-6500:] if failure else "",
        "actual_execution_error":failure[-800:],
        "output_capacity_note":"The present encrypted GitHub vault supports individual output files up to 950000 bytes and total package bytes up to 1400000."
    }
    script=model_python(system,observation,num_ctx=6144,num_predict=4800)
    if not script or len(script)>150000:
        raise ValueError("production_script_empty_or_oversized")
    if script.startswith("```"):
        raise ValueError("production_script_is_markdown_not_python")
    import ast
    try:
        ast.parse(script,filename="build.py")
    except SyntaxError as exc:
        raise ValueError("production_script_syntax_invalid:"+
                         str(exc.msg)[:100]) from None
    return script


def objective_quality_floor(plan,files):
    """Prove that REAL bytes were created with the promised names and structure.

    No invented product types, price heuristics or subjective buyer verdicts.
    """
    import io
    import zipfile
    import csv
    import ast
    expected=set(plan.get("output_files") or [])
    if not files or {x.get("filename") for x in files}!=expected:
        raise ValueError("artifact_output_manifest_mismatch")
    for item in files:
        name=str(item["filename"])
        try:
            content=base64.b64decode(item["content_b64"],validate=True)
        except (ValueError,base64.binascii.Error):
            raise ValueError("artifact_invalid_encoding:"+name) from None
        if (not content or len(content)!=item.get("size_bytes")
                or hashlib.sha256(content).hexdigest()!=item.get("sha256")):
            raise ValueError("artifact_bytes_did_not_verify:"+name)
        low=name.lower()
        if low.endswith(".png") and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("artifact_not_png:"+name)
        if low.endswith((".jpg",".jpeg")) and not content.startswith(b"\xff\xd8"):
            raise ValueError("artifact_not_jpeg:"+name)
        if low.endswith(".pdf") and not content.startswith(b"%PDF-"):
            raise ValueError("artifact_not_pdf:"+name)
        if low.endswith(".gif") and not content.startswith((b"GIF87a",b"GIF89a")):
            raise ValueError("artifact_not_gif:"+name)
        if low.endswith((".mp4",".m4v",".mov")) and content[4:8]!=b"ftyp":
            raise ValueError("artifact_not_video_container:"+name)
        if low.endswith(".wav") and (content[:4]!=b"RIFF" or content[8:12]!=b"WAVE"):
            raise ValueError("artifact_not_wave_audio:"+name)
        if low.endswith((".zip",".docx",".xlsx",".pptx")):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    if archive.testzip() is not None or not archive.namelist():
                        raise ValueError("artifact_zip_corrupt:"+name)
            except zipfile.BadZipFile:
                raise ValueError("artifact_invalid_zip_container:"+name) from None
        if low.endswith((".json",".svg",".py",".csv")):
            try:
                text_content=content.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("artifact_invalid_utf8:"+name) from None
            if low.endswith(".json"):
                try:
                    json.loads(text_content)
                except ValueError:
                    raise ValueError("artifact_invalid_json:"+name) from None
            elif low.endswith(".svg"):
                import xml.etree.ElementTree as ET
                try:
                    root=ET.fromstring(text_content)
                except ET.ParseError:
                    raise ValueError("artifact_invalid_svg:"+name) from None
                if root.tag.rsplit("}",1)[-1]!="svg":
                    raise ValueError("artifact_wrong_svg_root:"+name)
            elif low.endswith(".py"):
                try:
                    ast.parse(text_content,filename=name)
                except SyntaxError:
                    raise ValueError("artifact_invalid_python:"+name) from None
            else:
                try:
                    parsed=list(csv.reader(io.StringIO(text_content),strict=True))
                except csv.Error:
                    raise ValueError("artifact_invalid_csv:"+name) from None
                if not parsed or not all(len(row)==len(parsed[0]) for row in parsed):
                    raise ValueError("artifact_inconsistent_csv:"+name)
    return "generated_output_bytes_verified"


class ProductionPending(RuntimeError):
    """Saved source and execution diagnostics remain for the next tool attempt."""


class ProductRetired(RuntimeError):
    """No indefinitely blocked drafts; evidence is preserved in the vault."""


def _retire_draft(checkpoint,reason):
    plan=checkpoint.get("plan") or {}
    ident=str(plan.get("asset_id") or hashlib.sha256(
        str(plan.get("title") or "unspecified").encode()).hexdigest()[:20])
    save_private("ops/cyclops-vault/retired/"+ident+".json",{
        "plan":plan,"files":checkpoint.get("files") or [],
        "script":checkpoint.get("script") or "",
        "reason":reason,"attempts":checkpoint.get("attempts") or {},
        "retired_at":NOW().isoformat(),
    })
    save_pending({"stage":"empty"})
    # The last traceback line carries the actual exception, while the
    # beginning is usually boilerplate. Keep the public receipt concise.
    summary=str(reason).strip().splitlines()[-1][-170:] if reason else "unknown"
    raise ProductRetired("unusable_production_archived:"+ident+":"+summary)


def create_asset(rows,existing_assets=None):
    """Manufacture one selected product artifact per run, saving real progress."""
    checkpoint=read_pending()
    stage=checkpoint.get("stage")
    plan=checkpoint.get("plan") or {}
    if stage in ("drafting","review","approved","sealed","building"):
        if not plan.get("output_files"):
            _retire_draft(checkpoint,"legacy_non_executable_product_spec")
        evidence=checkpoint.get("research_rows") or rows
    else:
        if not rows:
            raise ValueError("no_public_research_to_select_product")
        plan=choose_asset_concept(rows,existing_assets)
        evidence=rows[:8]
        checkpoint={"stage":"building","plan":plan,"research_rows":evidence,
                    "files":[],"script":"","attempts":0,"feedback":""}
        save_pending(checkpoint)

    if checkpoint.get("stage") in ("approved","sealed") and checkpoint.get("files"):
        plan["_files"]=checkpoint["files"]
        return plan

    all_names=[str(_safe_path(x)) for x in plan["output_files"]]
    if len(set(all_names))!=len(all_names):
        raise ValueError("duplicate_product_output_path")
    plan["output_files"]=all_names
    finished=list(checkpoint.get("files") or [])
    done={str(x.get("filename") or "") for x in finished}
    if not done.issubset(set(all_names)) or len(done)!=len(finished):
        raise ValueError("production_checkpoint_file_manifest_mismatch")

    missing=[name for name in all_names if name not in done]
    if not missing:
        reason=objective_quality_floor(plan,finished)
        checkpoint.update({"stage":"approved","files":finished,
                           "quality_reason":reason,"feedback":""})
        save_pending(checkpoint)
        plan["_files"]=finished
        plan["_quality_reason"]=reason
        return plan

    target=missing[0]
    # Previous multi-file source generation cannot be reused for a single
    # artifact. Retry counts and draft content now track each actual file.
    if checkpoint.get("building_file")!=target:
        checkpoint.update({"building_file":target,"attempts":0,
                           "script":"","feedback":""})
        save_pending(checkpoint)
    attempts=int(checkpoint.get("attempts") or 0)
    if attempts>=3:
        _retire_draft(checkpoint,str(checkpoint.get("feedback") or "production_attempts_exhausted"))
    # Generated code is imperfect; use the same run to repair a real failure
    # with its execution diagnostics, while checkpointing after each attempt.
    # The GitHub job (one hour) remains the only overall execution deadline.
    tools=None
    while attempts<3:
        try:
            # This image does not change while the workflow runs. The first
            # real probe is enough for all three attempts on the same file.
            if tools is None:
                tools=discover_tools()
            script=_make_build_script(
                plan,tools,evidence,target,finished,
                checkpoint.get("script") or "",
                checkpoint.get("feedback") or "")
            checkpoint.update({"stage":"building","script":script,"feedback":""})
            save_pending(checkpoint)
            produced=manufacture(script,[target])
            # Verify THIS output immediately; the full manifest is checked
            # after every deliverable is actually generated and saved.
            objective_quality_floor({"output_files":[target]},produced)
            finished.extend(produced)
            checkpoint.update({"stage":"building","files":finished,"script":"",
                               "feedback":"","attempts":0,"building_file":""})
            save_pending(checkpoint)
            break
        except (RuntimeError,ValueError,OSError) as exc:
            attempts+=1
            error=(type(exc).__name__+":"+str(exc))[-850:]
            checkpoint.update({"stage":"building","feedback":error,
                               "attempts":attempts,"building_file":target})
            save_pending(checkpoint)
            if attempts>=3:
                _retire_draft(checkpoint,error)
    else:
        raise ProductionPending(str(checkpoint.get("feedback") or "build_failed"))

    if len(finished)<len(all_names):
        # An actual finished file survives this run. Do not regenerate it.
        raise ProductionPending("artifact_created_and_saved:"+target)

    reason=objective_quality_floor(plan,finished)
    checkpoint.update({"stage":"approved","quality_reason":reason})
    save_pending(checkpoint)
    plan["_files"]=finished
    plan["_quality_reason"]=reason
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
        with urllib.request.urlopen(req,timeout=30) as response:
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
        "files":[x["filename"] for x in plan["_files"]],
        "artifact_manifest":[{
            "filename":x["filename"],"bytes":x["size_bytes"],
            "sha256":x["sha256"],"content_type":x["content_type"]
        } for x in plan["_files"]],
        "production_tool":"isolated_tool_workshop",
        "quality_gate":"verified_generated_bytes",
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
    # Preserve the ACTUAL tool-created output before creating checkout.
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
           and a.get("quality_gate")=="verified_generated_bytes"
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
        if (pending_at_start.get("stage") in ("drafting","review","approved","sealed")
                and not (pending_at_start.get("plan") or {}).get("output_files")):
            # The old subjective reviewer created a permanently stuck draft.
            # Retire it BEFORE deciding whether to search, so this same run
            # can originate a fresh deliverable rather than wasting an hour.
            try:
                _retire_draft(pending_at_start,"legacy_fixed_format_non_executable_draft")
            except ProductRetired as retirement:
                receipt["commercial_outcomes"].append({
                    "reason":"legacy_unproductive_draft_archived",
                    "detail":str(retirement)[:145]})
            pending_at_start={"stage":"empty"}
        resuming=pending_at_start.get("stage") in ("building","drafting","review","approved","sealed")
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
            "status":("deferred_for_existing_product" if resuming else
                      "new_public_evidence" if rows else "no_new_eligible_public_evidence"),
            "candidate_count":len(rows),
            "new_research_attempted":not resuming,
            "research_deferred_for_product_completion":resuming,
            "previous_product_evidence_count":(
                len(pending_at_start.get("research_rows") or []) if resuming else 0),
            "source_counts":source_counts,
            "search_provider_counts":provider_counts,
            "source_errors":search_errors,
            "fetched_public_pages":sum(bool(r.get("page_fetched")) for r in rows),
            "candidates_without_email":sum(not bool(r.get("contact")) for r in rows),
            "search_queries":list(dict.fromkeys(str(r.get("search_query") or "") for r in rows))[:12],
        }
    except Exception as exc:
        rows=[]
        receipt["discovery"]={"status":"research_runtime_error",
                              "candidate_count":0,"new_research_attempted":True,
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
            receipt["production"]={
                "result":"approved_and_published",
                "asset_id":asset["asset_id"],
                "production_tool":"isolated_tool_workshop",
                "artifact_count":len(plan["_files"]),
                "created_artifacts":[{
                    "filename":x["filename"],
                    "bytes":x["size_bytes"],
                    "sha256":x["sha256"],
                    "content_type":x["content_type"],
                } for x in plan["_files"]],
            }
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
            receipt["structural_failures"].append({
                "reason":"invalid_production_draft_retired","detail":reason})
        elif isinstance(exc,ProductionPending):
            progressed=str(exc).startswith("artifact_created_and_saved:")
            receipt["production"]={
                "result":("artifact_created_and_saved" if progressed
                          else "objective_production_repair_pending"),
                "stage":stage,
                "last_issue":("" if progressed else str(pending.get("feedback") or reason)[-500:]),
                "attempts":pending.get("attempts") or 0,
                "files_finished":len(pending.get("files") or []),
                "files_planned":len((pending.get("plan") or {}).get("output_files") or []),
            }
            if not progressed:
                receipt["structural_failures"].append({
                    "reason":"production_build_failed",
                    "detail":str(pending.get("feedback") or reason)[-220:]})
        elif stage in ("building","drafting","review","approved","sealed"):
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
                                "product_production_goal_missing","product_output_manifest_invalid",
                                "invalid_asset_spec","duplicate_product_output_path")):
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
