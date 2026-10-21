#!/usr/bin/env python3
"""Continuous evidence-driven refiner for the autonomous commerce engine.

The refiner is a control loop, not a business-model menu:
observe real operational receipts -> diagnose the current SYSTEM bottleneck ->
ask the local model for one bounded implementation -> validate -> integrate.
Diagnosis and final approval are deterministic so the hosted CPU runner spends
its model budget on implementation instead of three long inference calls.
"""
from __future__ import annotations

from collections import Counter
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

ROOT=Path.cwd()
MODEL=os.getenv("LOCAL_MODEL","qwen3:4b-instruct-2507-q4_K_M")
SAFE_ROOTS=("tools","tests","products","storefront","ventures")
FORBIDDEN_PARTS=(".git",".github","node_modules","__pycache__",".venv","venv")
CONTROL_PLANE={"tools/continuous_commerce_refiner.py"}
MAX_CHANGES=2
MAX_NEW_FILE_BYTES=32000
MAX_CONTEXT_CHARS=19000

def run(*cmd,timeout=300):
    return subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout)

def safe_path(raw):
    p=Path(str(raw))
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] not in SAFE_ROOTS:
        return False
    if any(part in FORBIDDEN_PARTS for part in p.parts):
        return False
    if p.suffix.lower() not in {".py",".js",".json",".md",".html",".css",".txt",".csv"}:
        return False
    try:
        (ROOT/p).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True

def inventory():
    rows=[]
    for root_name in SAFE_ROOTS:
        root=ROOT/root_name
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or any(x in p.parts for x in FORBIDDEN_PARTS):
                continue
            rel=str(p.relative_to(ROOT))
            if safe_path(rel):
                try:
                    rows.append({"path":rel,"bytes":p.stat().st_size})
                except OSError:
                    pass
    return sorted(rows,key=lambda x:x["path"])[:260]

def read_jsons(folder,limit):
    root=ROOT/folder
    rows=[]
    if not root.is_dir():
        return rows
    for p in sorted(root.glob("*.json"),reverse=True)[:limit]:
        try:
            row=json.loads(p.read_text(encoding="utf-8"))
        except (OSError,ValueError,TypeError):
            continue
        if isinstance(row,dict):
            rows.append(row)
    return rows

def evidence_state():
    enterprise=read_jsons("ops/enterprise-runs",12)
    refiner=read_jsons("ops/refiner-runs",12)
    scheduled=read_jsons("ops/scheduled-commerce-attestations",6)
    blockers=[]
    contacts=replies=paid=fulfilled=0
    latest_scorecard={}
    latest_provider={}
    research_loop=False
    for row in enterprise:
        latest_scorecard=latest_scorecard or (row.get("economic_scorecard") or {})
        latest_provider=latest_provider or (row.get("verified_provider_outcomes") or {})
        contacts+=int(row.get("qualified_external_contacts") or 0)
        diagnostic=row.get("portfolio_diagnostics") or {}
        research_loop=research_loop or diagnostic.get("research_loop_alert") is True
        for b in (row.get("failures_or_blockers") or [])[:12]:
            if isinstance(b,dict) and b.get("reason"):
                blockers.append(str(b["reason"])[:100])
    if latest_scorecard:
        replies=int(latest_scorecard.get("buyer_replies_verified") or
                    latest_scorecard.get("verified_reply_threads_to_sent_invitations") or 0)
        paid=int(latest_scorecard.get("paid_orders_verified_on_existing_link") or
                 latest_scorecard.get("paid_orders_verified") or 0)
        fulfilled=int(latest_scorecard.get("paid_deliverables_sent_verified") or 0)
    delivery=ROOT/"ops/commerce-deliveries"
    if delivery.is_dir():
        fulfilled=max(fulfilled,len(list(delivery.glob("*.json"))))
    refiner_failures=[str(x.get("reason",""))[:180] for x in refiner
                      if x.get("status")=="refinement_failed_or_rejected"][:6]
    return {
      "enterprise_runs_seen":len(enterprise),
      "blocker_counts":dict(Counter(blockers).most_common(10)),
      "recent_blockers":blockers[:14],
      "research_loop_alert":research_loop,
      "qualified_external_contacts_recent":contacts,
      "buyer_replies_verified":replies,
      "paid_orders_verified":paid,
      "fulfillment_receipts_verified":fulfilled,
      "latest_scorecard":latest_scorecard,
      "latest_provider_outcomes":latest_provider,
      "scheduled_provider_checks":[{
          "commerce_status":x.get("commerce_status"),
          "independent_auth_verified":x.get("independent_stripe_and_agentmail_auth_verified"),
          "paid_checkout_sessions":x.get("paid_checkout_sessions"),
          "new_deliveries":x.get("new_deliveries")} for x in scheduled[:3]],
      "recent_refiner_failures":refiner_failures,
    }

def diagnose(state):
    counts=state["blocker_counts"]
    invalid=sum(counts.get(x,0) for x in
                ("model_strategy_invalid","model_candidate_evaluation_invalid",
                 "model_selected_unlisted_candidate","unusable_search_term"))
    missing_distribution=counts.get("distribution_connector_not_yet_available",0)
    rejected=counts.get("model_rejected_candidates",0)
    no_demand=counts.get("no_matching_public_demand",0)
    contacts=state["qualified_external_contacts_recent"]
    replies=state["buyer_replies_verified"]
    paid=state["paid_orders_verified"]
    fulfilled=state["fulfillment_receipts_verified"]

    if invalid or state["research_loop_alert"] or (contacts==0 and rejected+no_demand>0):
        return {
          "bottleneck":"Commercial reasoning is stalling before verified buyer reach.",
          "evidence":f"invalid={invalid}; rejected={rejected}; no_demand={no_demand}; contacts={contacts}; research_loop={state['research_loop_alert']}",
          "goal":"Improve the open-ended commerce brain so it produces concrete executable strategies, pivots from dead searches, and reaches genuinely qualified external demand instead of placeholder or research-loop output.",
          "target":"tools/autonomous_commerce_brain.py",
          "measurable_effect":"Fewer invalid/repeated strategies and at least one evidence-qualified external action when legitimate demand exists."
        }
    if missing_distribution:
        return {
          "bottleneck":"Open-ended strategies are proposing distribution routes the runtime cannot execute.",
          "evidence":f"distribution_connector_not_yet_available={missing_distribution}",
          "goal":"Expand reusable connected-commerce execution so novel strategies can reach real external markets through already authorized accounts without hardcoding a business model.",
          "target":"tools/connected_commerce_tools.py",
          "measurable_effect":"A larger share of model-selected distribution routes become executable and provider-verifiable."
        }
    if contacts>0 and replies==0:
        return {
          "bottleneck":"Verified buyer reach is not converting to buyer replies.",
          "evidence":f"contacts={contacts}; replies={replies}",
          "goal":"Improve truthful offer framing, qualification, and follow-up behavior for already-authorized buyer contact while preserving anti-spam and invitation checks.",
          "target":"tools/sales_conversation.py",
          "measurable_effect":"Verified reply threads increase without increasing uninvited contact."
        }
    if replies>0 and paid==0:
        return {
          "bottleneck":"Buyer engagement is not converting to a verified paid transaction.",
          "evidence":f"replies={replies}; paid={paid}",
          "goal":"Improve general checkout/offer handoff so an accepted legitimate offer can receive a correctly scoped dynamic payment path and remain auditable.",
          "target":"tools/connected_commerce_tools.py",
          "measurable_effect":"Verified paid checkout conversion becomes possible for more than one hardcoded offer."
        }
    if paid>fulfilled:
        return {
          "bottleneck":"Verified paid demand exceeds verified fulfillment.",
          "evidence":f"paid={paid}; fulfilled={fulfilled}",
          "goal":"Generalize paid-order fulfillment so supported original digital deliverables can be produced and delivered safely beyond one fixed product.",
          "target":"tools/commerce_operator.py",
          "measurable_effect":"Every supported verified paid order reaches an auditable fulfillment receipt."
        }
    return {
      "bottleneck":"The engine lacks enough verified commercial outcome evidence to optimize a later funnel stage.",
      "evidence":f"contacts={contacts}; replies={replies}; paid={paid}; fulfilled={fulfilled}",
      "goal":"Strengthen the general commerce brain's ability to convert open-ended market hypotheses into an executable buyer-reaching experiment using existing authorized infrastructure.",
      "target":"tools/autonomous_commerce_brain.py",
      "measurable_effect":"A concrete provider-verifiable commercial action replaces another research-only cycle."
    }

def lexical_tokens(text):
    return {x for x in re.findall(r"[a-z0-9_]{4,}",text.lower())
            if x not in {"this","that","with","from","into","when","than","true","false",
                         "engine","commerce","system","verified","actual","current"}}

def compact_source(path,goal):
    p=ROOT/path
    if not p.is_file():
        return ""
    text=p.read_text(encoding="utf-8")
    if len(text)<=MAX_CONTEXT_CHARS:
        return text
    lines=text.splitlines(True)
    chunks=[]
    size=90
    terms=lexical_tokens(goal)
    for start in range(0,len(lines),size):
        block="".join(lines[start:start+size])
        score=len(terms & lexical_tokens(block))
        chunks.append((score,start,block))
    selected={0}
    for _,start,_ in sorted(chunks,key=lambda x:(x[0],-x[1]),reverse=True)[:5]:
        selected.add(start)
    out=[]
    for _,start,block in sorted((x for x in chunks if x[1] in selected),key=lambda x:x[1]):
        out.append(f"\n# --- source lines {start+1}-{start+block.count(chr(10))} ---\n")
        out.append(block)
    return "".join(out)[:MAX_CONTEXT_CHARS]

def infer_implementation(diagnosis,state,source):
    schema={"type":"object","properties":{
      "changes":{"type":"array","minItems":1,"maxItems":MAX_CHANGES,"items":{
        "type":"object","properties":{
          "path":{"type":"string"},"reason":{"type":"string"},
          "content":{"type":"string"},
          "replacements":{"type":"array","maxItems":4,"items":{
            "type":"object","properties":{"old":{"type":"string"},"new":{"type":"string"}},
            "required":["old","new"],"additionalProperties":False}},
        },"required":["path","reason","content","replacements"],"additionalProperties":False}},
      "verification":{"type":"string"}},
      "required":["changes","verification"],"additionalProperties":False}
    prompt="""You are the implementation engineer inside a continuous autonomous-commerce
refiner. Implement the diagnosed SYSTEM improvement, not a plan or business-model menu.
For the supplied existing target, return exact old/new replacements copied from the
source excerpts; leave content empty. You may additionally create ONE new reusable file
under tools/, products/, storefront/, or ventures/ by supplying complete content and an
empty replacements array. Do not edit the refiner control plane or existing tests.
Preserve security checks, privacy, truthful evidence standards, anti-spam rules, and
backward-compatible public interfaces. Do not fabricate buyers, revenue, provider access,
credentials, or completed work. Do not add spending, account creation, restriction
bypasses, destructive behavior, or bulk unsolicited messaging. Prefer executable code
that moves the measured funnel over prompt-only commentary."""
    system=prompt+"\nReturn ONLY JSON matching: "+json.dumps(schema,separators=(",",":"))
    observation={"diagnosis":diagnosis,
                 "operational_evidence":{
                    "blocker_counts":state["blocker_counts"],
                    "research_loop_alert":state["research_loop_alert"],
                    "contacts":state["qualified_external_contacts_recent"],
                    "replies":state["buyer_replies_verified"],
                    "paid":state["paid_orders_verified"],
                    "fulfilled":state["fulfillment_receipts_verified"]},
                 "target_path":diagnosis["target"],
                 "target_source_excerpts":source}
    payload=json.dumps({"model":MODEL,
      "messages":[{"role":"system","content":system},
                  {"role":"user","content":json.dumps(observation,ensure_ascii=False)}],
      "stream":False,"format":"json","keep_alive":"20m",
      "options":{"temperature":0.05,"num_ctx":8192,"num_predict":1700}}).encode()
    req=urllib.request.Request("http://127.0.0.1:11434/api/chat",data=payload,
                               headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=600) as response:
            raw=json.load(response).get("message",{}).get("content","")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("ollama_http_"+str(exc.code)) from None
    except TimeoutError:
        raise TimeoutError("implementation_inference_timeout") from None
    obj=json.loads(raw)
    if not isinstance(obj,dict):
        raise ValueError("model_output_not_object")
    return obj

def guard_content(path,content):
    if not safe_path(path) or path in CONTROL_PLANE:
        raise ValueError("unsafe_path")
    if not isinstance(content,str) or not content.strip():
        raise ValueError("empty_content")
    if len(content.encode())>MAX_NEW_FILE_BYTES and not (ROOT/path).is_file():
        raise ValueError("new_file_too_large")
    if re.search(r"(?:sk_live_|rk_live_|ghp_|github_pat_|AKIA)[A-Za-z0-9_\-]{8,}",content):
        raise ValueError("credential_literal_detected")
    if re.search(r"(?i)(password|secret|api[_-]?key)\s*=\s*['\"][^'\"]{8,}['\"]",content):
        raise ValueError("embedded_secret_detected")

def validation():
    checks=[]
    for name,cmd in (
      ("compile",("python3","-m","compileall","-q","tools","tests")),
      ("tests",("python3","-m","unittest","discover","-s","tests","-v")),
      ("capability_smoke",("python3","tools/connected_commerce_tools.py")),
    ):
        p=run(*cmd,timeout=420)
        checks.append({"name":name,"ok":p.returncode==0,
                       "tail":(p.stdout+p.stderr)[-1200:]})
    return checks

def substantive_change(path,old,new):
    if old==new:
        return False
    if path.endswith(".py"):
        try:
            return ast.dump(ast.parse(old or ""),include_attributes=False) != \
                   ast.dump(ast.parse(new),include_attributes=False)
        except SyntaxError:
            return False
    return True

def apply_changes(implementation,diagnosis):
    changes=implementation.get("changes")
    if not isinstance(changes,list) or not 1<=len(changes)<=MAX_CHANGES:
        raise ValueError("invalid_change_count")
    target=diagnosis["target"]
    originals={}
    finals={}
    seen=set()
    touched_target=False
    for change in changes:
        if not isinstance(change,dict):
            raise ValueError("invalid_change")
        path=str(change.get("path") or "")
        if path in seen or not safe_path(path) or path in CONTROL_PLANE:
            raise ValueError("unsafe_or_duplicate_change")
        seen.add(path)
        p=ROOT/path
        old=p.read_text(encoding="utf-8") if p.is_file() else None
        if old is not None and path.startswith("tests/"):
            raise ValueError("existing_tests_immutable")
        if old is not None and path!=target:
            raise ValueError("existing_change_outside_diagnosed_target")
        if old is None:
            if path.startswith("tests/"):
                raise ValueError("model_may_not_author_its_own_acceptance_test")
            if change.get("replacements") not in ([],None):
                raise ValueError("new_file_with_replacements")
            final=change.get("content")
        else:
            touched_target=True
            if change.get("content") not in ("",None):
                raise ValueError("existing_file_requires_patch_mode")
            reps=change.get("replacements")
            if not isinstance(reps,list) or not 1<=len(reps)<=4:
                raise ValueError("invalid_replacement_count")
            final=old
            for rep in reps:
                old_text=rep.get("old") if isinstance(rep,dict) else None
                new_text=rep.get("new") if isinstance(rep,dict) else None
                if not isinstance(old_text,str) or not old_text or not isinstance(new_text,str):
                    raise ValueError("invalid_replacement")
                if final.count(old_text)!=1:
                    raise ValueError("replacement_anchor_not_unique")
                final=final.replace(old_text,new_text,1)
        guard_content(path,final)
        if not substantive_change(path,old,final):
            raise ValueError("non_substantive_change")
        originals[path]=old
        finals[path]=final
    if not touched_target:
        raise ValueError("diagnosed_target_not_improved")
    for path,final in finals.items():
        p=ROOT/path
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(final,encoding="utf-8")
    return originals,finals

def restore(originals):
    for path,old in originals.items():
        p=ROOT/path
        if old is None:
            if p.exists():
                p.unlink()
        else:
            p.write_text(old,encoding="utf-8")

def write_output(name,value):
    out=os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out,"a",encoding="utf-8") as f:
            f.write(f"{name}={value}\n")

def receipt_path():
    runid=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ID","local"))
    attempt=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ATTEMPT","1"))
    root=ROOT/"ops/refiner-runs"
    root.mkdir(parents=True,exist_ok=True)
    return root/f"{runid}-{attempt}.json"

def main():
    phase="startup"
    if run("git","status","--porcelain","--untracked-files=no").stdout.strip():
        raise SystemExit("tracked_worktree_not_clean")
    phase="baseline_validation"
    baseline=validation()
    if not all(x["ok"] for x in baseline):
        raise RuntimeError("baseline_validation_failed")
    phase="observe_and_diagnose"
    state=evidence_state()
    diagnosis=diagnose(state)
    target=diagnosis["target"]
    if not safe_path(target) or not (ROOT/target).is_file():
        raise RuntimeError("diagnosed_target_unavailable")
    source=compact_source(target,diagnosis["goal"]+" "+diagnosis["evidence"])
    if not source:
        raise RuntimeError("target_context_empty")
    phase="implementation_inference"
    implementation=infer_implementation(diagnosis,state,source)
    phase="apply_candidate"
    originals,finals=apply_changes(implementation,diagnosis)
    try:
        phase="candidate_validation"
        after=validation()
        if not all(x["ok"] for x in after):
            raise RuntimeError("candidate_validation_failed")
        diff=run("git","diff","--",*sorted(finals),timeout=90).stdout
        new_files=[p for p,o in originals.items() if o is None]
        if not diff.strip() and not new_files:
            raise RuntimeError("no_material_diff")
    except Exception:
        restore(originals)
        raise
    phase="receipt"
    receipt={
      "actor":"continuous_autonomous_commerce_refiner",
      "status":"validated_refinement",
      "bottleneck":diagnosis["bottleneck"],
      "evidence":diagnosis["evidence"],
      "refinement_goal":diagnosis["goal"],
      "expected_measurable_effect":diagnosis["measurable_effect"],
      "changed_files":sorted(finals),
      "validation":[{"name":x["name"],"ok":x["ok"]} for x in after],
      "model_calls":1,
    }
    receipt_path().write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
    write_output("changed","true")
    write_output("failed","false")
    write_output("changed_files",",".join(sorted(finals)))
    print(json.dumps(receipt))

if __name__=="__main__":
    phase="entry"
    try:
        main()
    except BaseException as exc:
        if isinstance(exc,KeyboardInterrupt):
            raise
        reason=type(exc).__name__+":"+str(exc)[:220]
        receipt={"actor":"continuous_autonomous_commerce_refiner",
                 "status":"refinement_failed_or_rejected",
                 "reason":reason}
        try:
            receipt_path().write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
        except OSError:
            pass
        write_output("changed","false")
        write_output("failed","true")
        print(json.dumps(receipt))
