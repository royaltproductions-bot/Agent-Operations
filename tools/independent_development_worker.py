#!/usr/bin/env python3
"""Independent, bounded Qwen development worker. Runs in GitHub Actions, not ChatGPT."""
import json, os, pathlib, subprocess, urllib.request
ROOT=pathlib.Path.cwd()
MODEL=os.getenv("LOCAL_MODEL","qwen3:4b-instruct-2507-q4_K_M")
TARGETS=("tools/connected_commerce_tools.py","tools/autonomous_commerce_brain.py","tools/commerce_operator.py","tools/dynamic_offer_catalog.py","tools/digital_artifact_factory.py","tools/commerce_distribution.py","tools/general_order_fulfillment.py")
def run(*cmd,timeout=90):
    return subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
def infer(messages):
    payload=json.dumps({"model":MODEL,"messages":messages,"stream":False,
      "format":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"},"rationale":{"type":"string"}},"required":["path","content","rationale"]},
      "options":{"temperature":0.1,"num_ctx":8192,"num_predict":3500}}).encode()
    req=urllib.request.Request("http://127.0.0.1:11434/api/chat",data=payload,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=420) as response:
        return json.loads(json.load(response)["message"]["content"])
def main():
    if run("git","status","--porcelain").stdout.strip():
        raise SystemExit("Working tree must be clean")
    history=run("git","log","-12","--format=%s").stdout[:1800]
    observations=[]
    for folder in ("ops/enterprise-runs","ops/scheduled-commerce-attestations"):
        root=ROOT/folder
        if root.is_dir():
            for receipt in sorted(root.glob("*.json"),reverse=True)[:8]:
                try:
                    row=json.loads(receipt.read_text())
                    observations.append({"source":folder,"status":row.get("status"),
                        "scorecard":row.get("economic_scorecard"),
                        "blockers":row.get("failures_or_blockers",[])[:5],
                        "hypotheses":len(row.get("hypotheses",[])),
                        "qualified_contacts":row.get("qualified_external_contacts",0)})
                except (OSError,ValueError,TypeError): pass
    objectives={"mission":"Continuously refine the complete autonomous revenue engine",
        "success":["real buyer reach","verified payment","fulfilled deliverable","repeatable positive economics"],
        "capabilities":["open-ended discovery","offer invention","asset creation","distribution","dynamic checkout","fulfillment","feedback learning"]}
    files={p:(ROOT/p).read_text()[:12000] if (ROOT/p).exists() else "" for p in TARGETS}
    task=("You are a CONTINUOUS COMMERCE REFINER, not a one-off code generator. "
          "Each run analyze observed operational results, identify the highest-impact execution gap, "
          "and implement one measurable improvement toward autonomous end-to-end commercial operation. "
          "Business models and strategies are OPEN ENDED; do not hardcode any niche or product. "
          "Prioritize working distribution, dynamic offers, verified payments and fulfillment over prompts or dashboards. "
          "You may create a new allowed module. Preserve working behavior, tests and security controls. "
          "Never invent customers, transactions or provider capabilities. "
          "Return exactly one allowed Python path and its complete source.")
    result=infer([{"role":"system","content":task},
       {"role":"user","content":json.dumps({"recent_commits":history,"observations":observations,"objectives":objectives,"files":files})}])
    path=result["path"]
    if path not in TARGETS or not isinstance(result["content"],str) or len(result["content"])<500:
        raise SystemExit("Invalid model output")
    original=(ROOT/path).read_text() if (ROOT/path).exists() else None
    (ROOT/path).parent.mkdir(parents=True,exist_ok=True)
    (ROOT/path).write_text(result["content"])
    checks=[("python3","-m","py_compile",path),
            ("python3","-m","unittest","discover","-s","tests","-p","test_commerce_operator.py")]
    for check in checks:
        p=run(*check,timeout=180)
        if p.returncode:
            (ROOT/path).write_text(original) if original is not None else (ROOT/path).unlink()
            print("Validation rejected candidate:",check)
            return
    diff=run("git","diff","--numstat","--",path).stdout.strip() if original is not None else "new module"
    if (not diff and original is not None) or len(result["content"])>len(original or "")*2+18000:
        (ROOT/path).write_text(original) if original is not None else (ROOT/path).unlink()
        print("Candidate rejected: empty or oversized")
        return
    print(json.dumps({"changed_file":path,"diff_numstat":diff,
       "rationale":str(result.get("rationale",""))[:350],
       "status":"validated_refinement_for_pull_request"}))
if __name__=="__main__": main()
