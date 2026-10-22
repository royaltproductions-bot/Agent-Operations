#!/usr/bin/env python3
"""Small evidence-driven refiner for the autonomous commerce engine.

One job: use real commerce receipts to improve one core execution file, then let
production commerce determine whether the change helped.
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT=Path.cwd()
MODEL=os.getenv("REFINER_MODEL","qwen2.5-coder:7b-instruct")
CORE={
    "engine":"tools/autonomous_commerce_brain.py",
    "providers":"tools/connected_commerce_tools.py",
}
MAX_SOURCE=28000

def receipts(folder,limit=8):
    root=ROOT/folder
    if not root.is_dir():
        return []
    rows=[]
    for path in sorted(root.glob("*.json"),key=lambda p:p.name,reverse=True)[:limit]:
        try:
            data=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            continue
        if isinstance(data,dict):
            rows.append((path.name,data))
    return rows

def choose_signal():
    commerce=receipts("ops/enterprise-runs",8)
    if not commerce:
        return None

    latest_name,latest=commerce[0]

    # Broken transaction machinery is repair work immediately.
    structural=[
        str(item.get("reason"))
        for item in (latest.get("structural_failures") or [])
        if isinstance(item,dict) and item.get("reason")
    ]
    fulfillment_failures=[
        item for item in (latest.get("fulfillment") or [])
        if isinstance(item,dict) and item.get("result")=="fulfillment_failed"
    ]
    if structural or fulfillment_failures:
        reason=(structural[0] if structural else "fulfillment_failed")
        target=("providers" if any(word in reason for word in
                ("stripe","payment","provider","checkout","mail","fulfillment"))
                else "engine")
        return {"kind":"structural","reason":reason,"occurrences":1,
                "target":CORE[target],"basis_receipt":latest_name}

    # Count commercial evidence by independent run.
    reasons=Counter()
    for _,row in commerce:
        for reason in {
            str(item.get("reason"))
            for item in (row.get("commercial_outcomes") or [])
            if isinstance(item,dict) and item.get("reason")
        }:
            reasons[reason]+=1

    persistent=[(reason,count) for reason,count in reasons.items() if count>=3]
    if persistent:
        reason,count=max(persistent,key=lambda x:(x[1],x[0]))
        signal={"kind":"persistent_commercial_pattern",
                "reason":reason,"occurrences":count,
                "target":CORE["engine"],"basis_receipt":latest_name}
    else:
        recent=commerce[:4]
        if len(recent)<3:
            return None
        offers=[
            int((row.get("economic_result") or {}).get("new_offers_sent") or 0)
            for _,row in recent
        ]
        revenue=[
            int((row.get("economic_result") or {}).get(
                "verified_autonomous_revenue_cents_seen") or 0)
            for _,row in recent
        ]
        if not any(offers):
            signal={"kind":"persistent_commercial_pattern",
                    "reason":"no_transaction_progress",
                    "occurrences":len(recent),
                    "target":CORE["engine"],"basis_receipt":latest_name}
        elif not any(revenue):
            signal={"kind":"persistent_commercial_pattern",
                    "reason":"offers_not_converting",
                    "occurrences":len(recent),
                    "target":CORE["engine"],"basis_receipt":latest_name}
        else:
            return None

    # Give a commercial change three fresh autonomous runs to prove itself.
    names=[name for name,_ in commerce]
    for _,row in receipts("ops/refiner-runs",6):
        if row.get("status")!="deployed" or row.get("signal_reason")!=signal["reason"]:
            continue
        previous=row.get("basis_receipt")
        if previous in names and names.index(previous)<3:
            return None
        break
    return signal

def source(path):
    text=(ROOT/path).read_text(encoding="utf-8")
    if len(text)<=MAX_SOURCE:
        return text
    # Keep beginning and end; both files place configuration/imports first and
    # runtime/main logic near the end.
    half=MAX_SOURCE//2
    return text[:half]+"\n# ... middle omitted ...\n"+text[-half:]

def schema():
    return {
        "type":"object",
        "properties":{
            "change":{
                "type":"object",
                "properties":{
                    "path":{"type":"string"},
                    "reason":{"type":"string"},
                    "replacements":{
                        "type":"array","minItems":1,"maxItems":4,
                        "items":{
                            "type":"object",
                            "properties":{
                                "old":{"type":"string"},
                                "new":{"type":"string"},
                            },
                            "required":["old","new"],
                            "additionalProperties":False,
                        },
                    },
                },
                "required":["path","reason","replacements"],
                "additionalProperties":False,
            }
        },
        "required":["change"],
        "additionalProperties":False,
    }

def infer(signal,code,recovery=""):
    spec=schema()
    instruction=(
        "You are the coding brain for an autonomous commercial engine. "
        "Improve the diagnosed problem with ONE bounded executable patch to the supplied target. "
        "Do not create a business-model menu, validation framework, blocker framework, test framework, "
        "status framework, or new control layer. Prefer fewer branches and more executable fallback paths. "
        "Commercial failure is evidence, never permission for inaction. Preserve truthful provider evidence, "
        "credential privacy, anti-spam constraints, and no fabricated buyers/payments. "
        "Each replacement.old must be copied EXACTLY from target_source and occur once."
    )
    if recovery:
        instruction+=(
            " Previous patch was unusable: "+recovery[:180]+
            ". Recover with exactly one target-file change and exact replacement anchors."
        )
    payload=json.dumps({
        "model":MODEL,
        "messages":[
            {"role":"system","content":instruction},
            {"role":"user","content":json.dumps({
                "signal":signal,
                "target_source":code,
            },ensure_ascii=False)},
        ],
        "stream":False,
        "format":spec,
        "keep_alive":"20m",
        "options":{"temperature":0.05,"num_ctx":12288,"num_predict":1800},
    }).encode()
    req=urllib.request.Request("http://127.0.0.1:11434/api/chat",data=payload,
                               headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=600) as response:
            raw=json.load(response).get("message",{}).get("content","")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("ollama_http_"+str(exc.code)) from None
    except TimeoutError:
        raise RuntimeError("ollama_timeout") from None
    obj=json.loads(raw)
    if not isinstance(obj,dict):
        raise ValueError("model_output_not_object")
    return obj

def apply(obj,signal):
    change=obj.get("change")
    if not isinstance(change,dict) or change.get("path")!=signal["target"]:
        raise ValueError("wrong_target")
    replacements=change.get("replacements")
    if not isinstance(replacements,list) or not 1<=len(replacements)<=4:
        raise ValueError("invalid_replacements")

    path=ROOT/signal["target"]
    original=path.read_text(encoding="utf-8")
    final=original
    for rep in replacements:
        if not isinstance(rep,dict):
            raise ValueError("invalid_replacement")
        old,new=rep.get("old"),rep.get("new")
        if not isinstance(old,str) or not old or not isinstance(new,str):
            raise ValueError("invalid_replacement")
        if final.count(old)!=1:
            raise ValueError("replacement_anchor_not_unique")
        final=final.replace(old,new,1)

    if final==original:
        raise ValueError("no_change")
    if re.search(r"(?:sk_live_|rk_live_|ghp_|github_pat_|AKIA)[A-Za-z0-9_\-]{8,}",final):
        raise ValueError("credential_literal")
    if re.search(r"(?i)(password|secret|api[_-]?key)\s*=\s*['\"][^'\"]{8,}['\"]",final):
        raise ValueError("embedded_secret")

    # Never let model-generated Python corrupt the working commerce kernel.
    try:
        compile(final,str(path),"exec")
    except SyntaxError as exc:
        raise ValueError(f"syntax_error_line_{exc.lineno}:{exc.msg}") from None

    path.write_text(final,encoding="utf-8")
    return str(path.relative_to(ROOT))

def output(name,value):
    target=os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target,"a",encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")

def write_receipt(data):
    root=ROOT/"ops/refiner-runs"
    root.mkdir(parents=True,exist_ok=True)
    rid=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ID","local"))
    attempt=re.sub(r"[^A-Za-z0-9_-]","",os.getenv("GITHUB_RUN_ATTEMPT","1"))
    path=root/f"{rid}-{attempt}.json"
    path.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")

def main():
    signal=choose_signal()
    if not signal:
        write_receipt({"actor":"commerce_refiner","status":"no_change",
                       "model":MODEL,"reason":"no_new_persistent_problem"})
        output("changed","false")
        return

    code=source(signal["target"])
    calls=1
    try:
        patch=infer(signal,code)
        changed=apply(patch,signal)
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        calls=2
        patch=infer(signal,code,recovery=str(exc))
        changed=apply(patch,signal)

    write_receipt({
        "actor":"commerce_refiner",
        "status":"deployed",
        "model":MODEL,
        "basis_receipt":signal["basis_receipt"],
        "signal_kind":signal["kind"],
        "signal_reason":signal["reason"],
        "signal_occurrences":signal["occurrences"],
        "changed_file":changed,
        "model_calls":calls,
        "commercial_proof":"next production commerce outcomes",
    })
    output("changed","true")

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        write_receipt({
            "actor":"commerce_refiner",
            "status":"attempt_unusable",
            "model":MODEL,
            "reason":type(exc).__name__+":"+str(exc)[:180],
        })
        output("changed","false")
        # A bad refinement attempt is evidence, not a reason to stop commerce.
