#!/usr/bin/env python3
"""Continuous evidence-driven refiner for the autonomous commerce engine.

The refiner is a control loop, not a business-model menu:
observe real operational receipts -> diagnose the current SYSTEM bottleneck ->
ask the local model for one bounded implementation -> integrate -> run commerce.
Commercial performance is the validation signal; duplicate test/compile gates do
not suppress experiments or refinements.
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
MODEL=os.getenv("REFINER_MODEL") or os.getenv("LOCAL_MODEL","qwen2.5-coder:7b-instruct")
SAFE_ROOTS=("tools","tests","products","storefront","ventures")
FORBIDDEN_PARTS=(".git",".github","node_modules","__pycache__",".venv","venv")
CONTROL_PLANE={"tools/continuous_commerce_refiner.py"}
MAX_CHANGES=2
MAX_NEW_FILE_BYTES=32000
MAX_CONTEXT_CHARS=19000
LEGACY_STRUCTURAL_REASONS={"distribution_connector_not_yet_available","engine_runtime_failure"}
COMMERCIAL_REFINEMENT_MIN_RUNS=3
COMMERCIAL_REFINEMENT_MIN_STREAK=3
COMMERCIAL_REFINEMENT_COOLDOWN_CYCLES=3

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
    structural=[]
    commercial=[]
    commercial_run_counts=Counter()
    commercial_streaks=Counter()
    streak_open={}
    contacts=replies=paid=fulfilled=0
    latest_scorecard={}
    latest_provider={}
    research_loop=False

    # enterprise is newest-first. Track commercial reasons by DISTINCT run so
    # three misses inside one run do not masquerade as a persistent pattern.
    for index,row in enumerate(enterprise):
        latest_scorecard=latest_scorecard or (row.get("economic_scorecard") or {})
        latest_provider=latest_provider or (row.get("verified_provider_outcomes") or {})
        contacts+=int(row.get("qualified_external_contacts") or 0)
        diagnostic=row.get("portfolio_diagnostics") or {}
        research_loop=research_loop or diagnostic.get("research_loop_alert") is True

        explicit=row.get("structural_failures")
        if isinstance(explicit,list):
            for item in explicit[:12]:
                if isinstance(item,dict) and item.get("reason"):
                    structural.append(str(item["reason"])[:100])
        else:
            # Old receipts mixed market outcomes with failures. Only known
            # structural capability gaps survive legacy migration.
            for item in (row.get("failures_or_blockers") or [])[:12]:
                if isinstance(item,dict) and item.get("reason") in LEGACY_STRUCTURAL_REASONS:
                    structural.append(str(item["reason"])[:100])

        reasons=set()
        for item in (row.get("commercial_outcomes") or [])[:20]:
            if isinstance(item,dict) and item.get("reason"):
                reason=str(item["reason"])[:100]
                commercial.append(reason)
                reasons.add(reason)
        for reason in reasons:
            commercial_run_counts[reason]+=1

        # Newest-first consecutive-run streak. A missing reason closes its streak.
        if index==0:
            for reason in reasons:
                streak_open[reason]=True
                commercial_streaks[reason]=1
        else:
            for reason in list(streak_open):
                if streak_open[reason]:
                    if reason in reasons:
                        commercial_streaks[reason]+=1
                    else:
                        streak_open[reason]=False

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
                      if x.get("status")=="refiner_execution_failed"][:6]
    recently_refined_patterns=[
        str(x.get("commercial_pattern_reason"))
        for x in refiner[:COMMERCIAL_REFINEMENT_COOLDOWN_CYCLES]
        if x.get("status")=="experimental_refinement_deployed"
        and x.get("trigger_kind")=="persistent_commercial_pattern"
        and x.get("commercial_pattern_reason")
    ]
    return {
      "enterprise_runs_seen":len(enterprise),
      "structural_failure_counts":dict(Counter(structural).most_common(10)),
      "recent_structural_failures":structural[:14],
      "commercial_outcome_counts":dict(Counter(commercial).most_common(10)),
      "commercial_run_counts":dict(commercial_run_counts.most_common(12)),
      "commercial_streaks":dict(commercial_streaks.most_common(12)),
      "recent_commercial_outcomes":commercial[:14],
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
      "recently_refined_commercial_patterns":recently_refined_patterns,
    }

def diagnose(state):
    """Diagnose immediate structural defects or persistent commercial patterns.

    Structural execution defects may trigger repair immediately. Commercial misses
    NEVER block the commerce engine; they become refiner evidence only after the same
    failure pattern persists across multiple independent runs.
    """
    structural=state["structural_failure_counts"]
    runtime_failures=structural.get("engine_runtime_failure",0)+structural.get("engine_process_failure",0)
    if runtime_failures:
        return {
          "trigger_kind":"structural_execution_failure",
          "bottleneck":"The commerce engine encountered a runtime failure and could not complete its experiment.",
          "evidence":f"engine_runtime_failure={runtime_failures}",
          "goal":"Repair the commerce runtime so the interrupted experiment path can execute and produce provider-grounded evidence on the next cycle.",
          "target":"tools/autonomous_commerce_brain.py",
          "measurable_effect":"The commerce engine completes its experiment cycle instead of terminating structurally."
        }

    missing_distribution=structural.get("distribution_connector_not_yet_available",0)
    if missing_distribution:
        return {
          "trigger_kind":"structural_execution_failure",
          "bottleneck":"A required execution capability is not implemented in the connected commerce layer.",
          "evidence":f"distribution_connector_not_yet_available={missing_distribution}",
          "goal":"Implement a reusable authorized execution capability for model-selected distribution routes without hardcoding a business model.",
          "target":"tools/connected_commerce_tools.py",
          "measurable_effect":"A previously selected legitimate distribution route becomes executable and provider-verifiable."
        }

    run_counts=state.get("commercial_run_counts") or {}
    streaks=state.get("commercial_streaks") or {}
    persistent=[
        (reason,int(run_counts.get(reason,0)),int(streaks.get(reason,0)))
        for reason in run_counts
        if int(run_counts.get(reason,0))>=COMMERCIAL_REFINEMENT_MIN_RUNS
        and int(streaks.get(reason,0))>=COMMERCIAL_REFINEMENT_MIN_STREAK
    ]
    if not persistent:
        return None

    # Do not rewrite the same business practice every hour. A deployed
    # commercial refinement gets several cycles to produce new evidence first.
    cooling=set(state.get("recently_refined_commercial_patterns") or [])
    eligible=[x for x in persistent if x[0] not in cooling]
    if not eligible:
        return None

    # Pick the most persistent recurring business-practice failure. This is a
    # refinement signal, never an execution veto.
    reason,runs,streak=max(eligible,key=lambda x:(x[2],x[1],x[0]))
    evidence=f"{reason} appeared in {runs} recent runs; current consecutive streak={streak}"
    if reason in {"no_matching_public_demand","model_rejected_candidates",
                  "repetition_warning","repeat_query_source_pivot",
                  "unusable_search_term","model_strategy_invalid",
                  "model_candidate_evaluation_invalid",
                  "model_selected_unlisted_candidate"}:
        return {
          "trigger_kind":"persistent_commercial_pattern",
          "commercial_pattern_reason":reason,
          "bottleneck":"A commercial decision pattern is repeatedly failing to advance toward qualified demand.",
          "evidence":evidence,
          "goal":"Improve market selection, search/pivot reasoning, and candidate qualification while preserving open-ended experimentation. Do not forbid the failed idea class; improve how evidence is gathered and acted on.",
          "target":"tools/autonomous_commerce_brain.py",
          "measurable_effect":"The repeated failure pattern declines across later independent runs while commercial experimentation continues."
        }

    # Unknown recurring commercial signals are evidence, but not enough to let
    # deterministic diagnosis guess which subsystem to rewrite.
    return None

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

def infer_implementation(diagnosis,state,source,recovery_note=""):
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
    if recovery_note:
        prompt += "\nThe previous implementation payload was unusable: "+recovery_note[:220]+
                  "\nRECOVER NOW: return EXACTLY ONE change, and it MUST patch target_path using exact replacements from the supplied source. Do not return an empty changes array."
    system=prompt+"\nReturn ONLY JSON matching: "+json.dumps(schema,separators=(",",":"))
    observation={"diagnosis":diagnosis,
                 "operational_evidence":{
                    "structural_failure_counts":state["structural_failure_counts"],
                    "recent_structural_failures":state["recent_structural_failures"],
                    "commercial_run_counts":state["commercial_run_counts"],
                    "commercial_streaks":state["commercial_streaks"],
                    "recent_commercial_outcomes":state["recent_commercial_outcomes"],
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

def substantive_change(path,old,new):
    return old != new

def apply_changes(implementation,diagnosis):
    changes=implementation.get("changes")
    if isinstance(changes,list) and len(changes)>MAX_CHANGES:
        target=diagnosis["target"]
        target_changes=[x for x in changes if isinstance(x,dict) and x.get("path")==target]
        other_changes=[x for x in changes if x not in target_changes]
        changes=(target_changes+other_changes)[:MAX_CHANGES]
        implementation=dict(implementation)
        implementation["changes"]=changes
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
    phase="observe_and_diagnose"
    state=evidence_state()
    diagnosis=diagnose(state)
    if diagnosis is None:
        receipt={
          "actor":"continuous_autonomous_commerce_refiner",
          "status":"no_refinement_required_this_cycle",
          "model":MODEL,
          "commercial_outcomes_observed":state["commercial_outcome_counts"],
          "commercial_run_counts":state["commercial_run_counts"],
          "commercial_streaks":state["commercial_streaks"],
          "structural_failures_observed":state["structural_failure_counts"],
          "note":"No immediate structural defect or sufficiently persistent cross-run commercial pattern warranted a code change.",
        }
        receipt_path().write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
        write_output("changed","false")
        write_output("failed","false")
        print(json.dumps(receipt))
        return
    target=diagnosis["target"]
    if not safe_path(target) or not (ROOT/target).is_file():
        raise RuntimeError("diagnosed_target_unavailable")
    source=compact_source(target,diagnosis["goal"]+" "+diagnosis["evidence"])
    if not source:
        raise RuntimeError("target_context_empty")
    phase="implementation_inference"
    implementation=infer_implementation(diagnosis,state,source)
    model_calls=1
    phase="apply_candidate"
    try:
        originals,finals=apply_changes(implementation,diagnosis)
    except ValueError as exc:
        # A malformed model patch is a recoverable generation error, not a reason
        # to stop the autonomous system.
        phase="implementation_recovery"
        implementation=infer_implementation(diagnosis,state,source,recovery_note=str(exc))
        model_calls=2
        phase="apply_recovered_candidate"
        originals,finals=apply_changes(implementation,diagnosis)
    phase="experimental_integration"
    diff=run("git","diff","--",*sorted(finals),timeout=90).stdout
    new_files=[p for p,o in originals.items() if o is None]
    if not diff.strip() and not new_files:
        raise RuntimeError("no_material_diff")
    phase="receipt"
    receipt={
      "actor":"continuous_autonomous_commerce_refiner",
      "status":"experimental_refinement_deployed",
      "trigger_kind":diagnosis.get("trigger_kind"),
      "commercial_pattern_reason":diagnosis.get("commercial_pattern_reason"),
      "bottleneck":diagnosis["bottleneck"],
      "evidence":diagnosis["evidence"],
      "refinement_goal":diagnosis["goal"],
      "expected_measurable_effect":diagnosis["measurable_effect"],
      "changed_files":sorted(finals),
      "commercial_validation":"production commerce outcomes and verified revenue",
      "model":MODEL,
      "model_calls":model_calls,
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
                 "status":"refiner_attempt_unusable","model":MODEL,
                 "reason":reason,
                 "note":"Refinement attempt did not produce a deployable patch; commerce must continue."}
        try:
            receipt_path().write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
        except OSError:
            pass
        write_output("changed","false")
        write_output("failed","true")
        write_output("changed","false")
        print(json.dumps(receipt))
