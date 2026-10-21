#!/usr/bin/env python3
"""Independent, bounded Qwen development worker. Runs in GitHub Actions, not ChatGPT."""
import json, os, pathlib, subprocess, urllib.request
ROOT=pathlib.Path.cwd()
MODEL=os.getenv("LOCAL_MODEL","qwen3:4b-instruct-2507-q4_K_M")
TARGETS=("tools/connected_commerce_tools.py","tools/autonomous_commerce_brain.py","tools/commerce_operator.py")
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
    history=run("git","log","-5","--format=%s").stdout[:800]
    files={p:(ROOT/p).read_text()[:12000] for p in TARGETS}
    task=("Implement ONE substantive general-purpose commerce execution primitive, not a prompt-only change. "
          "Prefer reusable original digital deliverable creation or dynamic offer catalog infrastructure. "
          "Keep existing public interfaces backward compatible. No hardcoded business niches. "
          "Do not alter credentials, workflows, security controls or unrelated files. "
          "Choose exactly one of the allowed existing Python files. Return its COMPLETE replacement source. "
          "No external network requests or purchases in generated code. Existing payment and customer data stay private.")
    result=infer([{"role":"system","content":task},
       {"role":"user","content":json.dumps({"recent_commits":history,"files":files})}])
    path=result["path"]
    if path not in TARGETS or not isinstance(result["content"],str) or len(result["content"])<500:
        raise SystemExit("Invalid model output")
    original=(ROOT/path).read_text()
    (ROOT/path).write_text(result["content"])
    checks=[("python3","-m","py_compile",path),
            ("python3","-m","unittest","discover","-s","tests","-p","test_commerce_operator.py")]
    for check in checks:
        p=run(*check,timeout=180)
        if p.returncode:
            (ROOT/path).write_text(original)
            print("Validation rejected candidate:",check)
            return
    diff=run("git","diff","--numstat","--",path).stdout.strip()
    if not diff or len(result["content"])>len(original)*2+18000:
        (ROOT/path).write_text(original)
        print("Candidate rejected: empty or oversized")
        return
    print(json.dumps({"changed_file":path,"diff_numstat":diff,
       "rationale":str(result.get("rationale",""))[:350],
       "status":"validated_candidate_for_pull_request"}))
if __name__=="__main__": main()
