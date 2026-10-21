#!/usr/bin/env python3
"""Evidence-driven continuous refiner for the autonomous commerce system.

The refiner is deliberately separate from the revenue operator. It studies privacy-safe
operational receipts and repository state, chooses the highest-impact system bottleneck,
implements a bounded multi-file improvement, validates it, and emits an auditable receipt.
It never invents revenue, customers, provider access, or commercial outcomes.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

ROOT=Path.cwd()
MODEL=os.getenv("LOCAL_MODEL","qwen3:4b-instruct-2507-q4_K_M")
SAFE_ROOTS=("tools","tests","products","storefront","ventures")
FORBIDDEN_PARTS=(".git",".github","node_modules","__pycache__",".venv","venv")
MAX_FILES=3
MAX_FILE_BYTES=35000
MAX_TOTAL_BYTES=70000
CORE_FILES=(
 "tools/autonomous_commerce_brain.py","tools/connected_commerce_tools.py",
 "tools/commerce_operator.py","tools/commercial_outcomes.py","tools/commerce_portfolio.py",
 "tools/render_direct_api.py","tools/continuous_commerce_refiner.py",
)

def run(*cmd,timeout=240):
    return subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout)

def infer(schema,system,observation,num_predict=1800):
    payload=json.dumps({"model":MODEL,"messages":[{"role":"system","content":system},
      {"role":"user","content":json.dumps(observation,ensure_ascii=False)}],
      "stream":False,"format":schema,
      "options":{"temperature":0.05,"num_ctx":24576,"num_predict":num_predict}}).encode()
    req=urllib.request.Request("http://127.0.0.1:11434/api/chat",data=payload,
                               headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=600) as response:
        raw=json.load(response).get("message",{}).get("content","")
    obj=json.loads(raw)
    if not isinstance(obj,dict):
        raise ValueError("model_output_not_object")
    return obj

def safe_path(raw):
    p=Path(str(raw))
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] not in SAFE_ROOTS:
        return False
    if any(part in FORBIDDEN_PARTS for part in p.parts):
        return False
    if p.suffix.lower() not in {".py",".js",".json",".md",".html",".css",".txt",".csv"}:
        return False
    return True

def inventory():
    rows=[]
    for root_name in SAFE_ROOTS:
        root=ROOT/root_name
        if not root.exists(): continue
        for p in root.rglob("*"):
            if not p.is_file() or any(x in p.parts for x in FORBIDDEN_PARTS): continue
            rel=str(p.relative_to(ROOT))
            if safe_path(rel):
                try: size=p.stat().st_size
                except OSError: continue
                rows.append({"path":rel,"bytes":size})
    return sorted(rows,key=lambda x:x["path"])[:400]

def sanitized_receipts():
    out=[]
    for folder in ("ops/enterprise-runs","ops/scheduled-commerce-attestations","ops/refiner-runs"):
        root=ROOT/folder
        if not root.is_dir(): continue
        for p in sorted(root.glob("*.json"),reverse=True)[:10]:
            try: row=json.loads(p.read_text(encoding="utf-8"))
            except (OSError,ValueError,TypeError): continue
            out.append({
              "source":folder,
              "status":row.get("status") or row.get("result"),
              "scorecard":row.get("economic_scorecard"),
              "blockers":(row.get("failures_or_blockers") or [])[:6],
              "changed_files":(row.get("changed_files") or [])[:6],
              "qualified_external_contacts":row.get("qualified_external_contacts"),
              "refinement_goal":row.get("refinement_goal"),
            })
    delivery=ROOT/"ops/commerce-deliveries"
    out.append({"source":"ops/commerce-deliveries",
                "privacy_safe_receipt_count":len(list(delivery.glob("*.json"))) if delivery.is_dir() else 0})
    return out[:28]

def validation():
    checks=[]
    commands=[
      ("compile",("python3","-m","compileall","-q","tools","tests")),
      ("tests",("python3","-m","unittest","discover","-s","tests","-v")),
      ("capability_smoke",("python3","tools/connected_commerce_tools.py")),
    ]
    for name,cmd in commands:
        p=run(*cmd,timeout=360)
        checks.append({"name":name,"ok":p.returncode==0,
          "tail":(p.stdout+p.stderr)[-1800:]})
    return checks

def changed_file_checks(paths):
    for path in paths:
        p=ROOT/path
        if path.endswith(".py"):
            q=run("python3","-m","py_compile",path,timeout=90)
            if q.returncode: raise RuntimeError("python_syntax_failed:"+path)
        elif path.endswith(".js"):
            q=run("node","--check",path,timeout=90)
            if q.returncode: raise RuntimeError("javascript_syntax_failed:"+path)
        elif path.endswith(".json"):
            json.loads(p.read_text(encoding="utf-8"))

def guard_content(path,content):
    if not isinstance(content,str) or not content.strip():
        raise ValueError("empty_content")
    if len(content.encode())>MAX_FILE_BYTES:
        raise ValueError("file_too_large")
    if re.search(r"(?:sk_live_|rk_live_|ghp_|github_pat_|AKIA)[A-Za-z0-9_\-]{8,}",content):
        raise ValueError("credential_literal_detected")
    if re.search(r"(?i)(password|secret|api[_-]?key)\s*=\s*['\"][^'\"]{8,}['\"]",content):
        raise ValueError("embedded_secret_detected")
    if not safe_path(path):
        raise ValueError("unsafe_path")

def read_targets(paths):
    result={}
    for path in paths:
        p=ROOT/path
        result[path]=p.read_text(encoding="utf-8")[:MAX_FILE_BYTES] if p.is_file() else ""
    return result

def write_output(name,value):
    out=os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out,"a",encoding="utf-8") as f:
            f.write(f"{name}={value}\n")

def main():
    if run("git","status","--porcelain","--untracked-files=no").stdout.strip():
        raise SystemExit("tracked_worktree_not_clean")
    baseline=validation()
    history=run("git","log","-18","--format=%h %s",timeout=30).stdout[:3000]
    core={p:text[:1800] for p,text in read_targets([p for p in CORE_FILES if (ROOT/p).is_file()]).items()}
    objective={
      "mission":"Continuously improve the autonomous revenue engine until it can originate, distribute, sell, fulfill, and learn from legitimate commercial transactions with minimal owner intervention.",
      "nonnegotiable":["No predetermined business-model menu","No fake buyers or fake revenue","No prompt-only work presented as execution","Use only authorized accounts/integrations","Preserve privacy and security controls"],
      "success_evidence":["verified external buyer reach","verified buyer response","verified paid order","verified fulfillment","measured conversion/revenue feedback"],
      "refiner_role":"Observe -> diagnose highest-leverage bottleneck -> implement -> validate -> integrate -> observe again."
    }
    diagnose_schema={"type":"object","properties":{
      "bottleneck":{"type":"string"},"evidence":{"type":"string"},"refinement_goal":{"type":"string"},
      "target_paths":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":MAX_FILES},
      "expected_measurable_effect":{"type":"string"}},
      "required":["bottleneck","evidence","refinement_goal","target_paths","expected_measurable_effect"],
      "additionalProperties":False}
    diagnosis=infer(diagnose_schema,
      "Act as the system-level refining engineer. Select the highest-impact CURRENT execution gap from evidence, not a favorite product or fixed business model. Code/test/deployment gaps are valid only when they block real commercial execution. Choose 1-3 safe repository paths to change; keep combined existing target source under 40KB; new files are allowed under tools/tests/products/storefront/ventures. Never choose .github, ops receipts, credentials, or unrelated projects.",
      {"objective":objective,"operational_evidence":sanitized_receipts(),
       "baseline_validation":baseline,"recent_commits":history,
       "repository_inventory":inventory(),"core_sources":core},1600)
    targets=[]
    for p in diagnosis.get("target_paths",[]):
        if safe_path(p) and p not in targets: targets.append(p)
    if not targets or len(targets)>MAX_FILES:
        raise SystemExit("diagnosis_selected_unsafe_targets")
    sources=read_targets(targets)
    if sum(len(v.encode()) for v in sources.values())>40000:
        raise SystemExit("diagnosis_selected_too_much_existing_source")
    change_schema={"type":"object","properties":{
      "changes":{"type":"array","minItems":1,"maxItems":MAX_FILES,"items":{
        "type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"},
        "reason":{"type":"string"}},"required":["path","content","reason"],"additionalProperties":False}},
      "verification":{"type":"string"}},
      "required":["changes","verification"],"additionalProperties":False}
    implementation=infer(change_schema,
      "Implement the diagnosed refinement, not another plan. Return COMPLETE replacement contents for each changed file. Keep scope bounded to selected targets. Preserve compatible public interfaces unless the evidence requires a deliberate migration. Do not weaken tests or security gates. Do not fabricate provider access, sales, customers, metrics, or success. Do not add spending, account creation, bulk unsolicited messaging, credential extraction, restriction bypasses, or destructive operations. Prefer reusable general capabilities over a hardcoded business niche.",
      {"objective":objective,"diagnosis":diagnosis,"selected_sources":sources,
       "baseline_validation":baseline},7000)
    changes=implementation.get("changes",[])
    if not 1<=len(changes)<=MAX_FILES:
        raise SystemExit("invalid_change_count")
    seen=set(); total=0; originals={}
    for ch in changes:
        path=ch.get("path")
        if path not in targets or path in seen: raise SystemExit("change_outside_diagnosed_targets")
        guard_content(path,ch.get("content"))
        seen.add(path); total+=len(ch["content"].encode())
        originals[path]=(ROOT/path).read_text(encoding="utf-8") if (ROOT/path).is_file() else None
    if total>MAX_TOTAL_BYTES: raise SystemExit("candidate_too_large")
    for ch in changes:
        p=ROOT/ch["path"]; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(ch["content"],encoding="utf-8")
    try:
        changed_file_checks(list(seen))
        after=validation()
        if not all(x["ok"] for x in after):
            raise RuntimeError("full_validation_failed")
        diff=run("git","diff","--",*sorted(seen),timeout=60).stdout
        if not diff.strip(): raise RuntimeError("no_material_change")
        critic_schema={"type":"object","properties":{"decision":{"type":"string","enum":["approve","reject"]},
          "reason":{"type":"string"},"risk":{"type":"string"}},"required":["decision","reason","risk"],
          "additionalProperties":False}
        critic=infer(critic_schema,
          "You are the final adversarial reviewer for an autonomous commerce-system refinement. Reject if the patch is prompt-only, unrelated to the diagnosed bottleneck, hardcodes a business model unnecessarily, weakens safety/tests, invents evidence, exposes secrets/PII, adds unauthorized spending or mass outreach, or cannot plausibly improve execution. Approve only a concrete, test-passing system improvement.",
          {"objective":objective,"diagnosis":diagnosis,"diff":diff[:24000],
           "validation":after},1200)
        if critic.get("decision")!="approve":
            raise RuntimeError("critic_rejected:"+str(critic.get("reason",""))[:160])
    except Exception:
        for path,old in originals.items():
            p=ROOT/path
            if old is None:
                if p.exists(): p.unlink()
            else: p.write_text(old,encoding="utf-8")
        raise
    receipt_dir=ROOT/"ops/refiner-runs"; receipt_dir.mkdir(parents=True,exist_ok=True)
    runid=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ID","local"))
    receipt={"actor":"continuous_autonomous_commerce_refiner","status":"validated_refinement",
      "bottleneck":str(diagnosis.get("bottleneck",""))[:500],
      "refinement_goal":str(diagnosis.get("refinement_goal",""))[:500],
      "expected_measurable_effect":str(diagnosis.get("expected_measurable_effect",""))[:500],
      "changed_files":sorted(seen),"validation":[{"name":x["name"],"ok":x["ok"]} for x in after],
      "critic":{"decision":"approve","risk":str(critic.get("risk",""))[:300]}}
    (receipt_dir/(runid+".json")).write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
    write_output("changed","true")
    write_output("changed_files",",".join(sorted(seen)))
    print(json.dumps(receipt))

if __name__=="__main__":
    try: main()
    except Exception as exc:
        write_output("changed","false")
        print(json.dumps({"actor":"continuous_autonomous_commerce_refiner","status":"no_integrated_change",
                          "reason":type(exc).__name__+":"+str(exc)[:220]}))
        raise
