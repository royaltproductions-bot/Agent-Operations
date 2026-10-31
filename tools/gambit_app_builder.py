"""Resumable original web-application source production for Gambit.

The model chooses the actual app architecture and writes one file per cycle.
Encrypted checkpoints retain buyer-specific code without publishing it in Git.
Executable generated code is only tested in a restricted Docker container:
never directly in the credential-bearing marketplace worker.
"""
from __future__ import annotations

import base64
from cryptography.fernet import Fernet
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

REQUIRED = ("server.py", "index.html", "README.md", "tests/test_app.py")
MAX_FILES = 14
MAX_FILE_BYTES = 32_000
MAX_ARCHIVE_BYTES = 350_000
ALLOWED_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".md", ".txt"}
PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,90}")
PLAN_SCHEMA = {"type":"object","properties":{
    "status":{"type":"string","enum":["ready","need_information"]},
    "question":{"type":"string"},
    "app_name":{"type":"string"},
    "files":{"type":"array","minItems":4,"maxItems":14,"items":{
        "type":"object","properties":{"path":{"type":"string"},"purpose":{"type":"string"}},
        "required":["path","purpose"],"additionalProperties":False}}
    },"required":["status","question","app_name","files"],"additionalProperties":False}
FILE_SCHEMA = {"type":"object","properties":{
    "path":{"type":"string"},"content":{"type":"string"}},
    "required":["path","content"],"additionalProperties":False}


def _cipher():
    key=os.environ.get("BOUNTY_AGENT_API_KEY")
    if not key:
        raise RuntimeError("app_checkpoint_key_missing")
    digest=hashlib.sha256(("gambit-app-work-v1:"+key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(value):
    return _cipher().encrypt(json.dumps(value,ensure_ascii=False).encode()).decode()


def unseal(value):
    return json.loads(_cipher().decrypt(value.encode()).decode())


def safe_path(value):
    if not isinstance(value,str) or not PATH.fullmatch(value):
        raise ValueError("app_unsafe_path")
    p=PurePosixPath(value)
    if p.is_absolute() or any(part in (".","..") for part in p.parts):
        raise ValueError("app_unsafe_path")
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("app_unsupported_file")
    if p.parts[0].startswith("."):
        raise ValueError("app_hidden_file")
    return value


def validate_plan(plan):
    if plan.get("status")!="ready":
        return []
    paths=[safe_path(item.get("path")) for item in plan.get("files") or []]
    if not (set(REQUIRED) <= set(paths)) or len(set(paths)) != len(paths):
        raise ValueError("app_missing_required_files")
    if not 4 <= len(paths) <= MAX_FILES:
        raise ValueError("app_invalid_file_count")
    for item in plan["files"]:
        if not str(item.get("purpose") or "").strip():
            raise ValueError("app_file_purpose_missing")
    return paths


def requested(details):
    """Identify commissioned working applications, not research about websites."""
    bounty=details.get("bounty") or {}
    text=" ".join(str(bounty.get(key) or "") for key in
                  ("title","description","verification")).lower()
    return bool(re.search(
        r"\b(?:build|create|develop|implement|deliver|produce)\b.{0,140}"
        r"\b(?:web\s*app|web\s*application|ai\s*chat\s*prototype|"
        r"web\s*prototype|working\s*application)\b",text))


def available():
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker","info","--format","{{.ServerVersion}}"],
                              timeout=6, capture_output=True).returncode == 0
    except (OSError,subprocess.TimeoutExpired):
        return False


def make_zip(files):
    names=list(files)
    if not set(REQUIRED).issubset(names) or len(names)>MAX_FILES:
        raise ValueError("app_incomplete_project")
    for name,code in files.items():
        safe_path(name)
        if not isinstance(code,str) or not code.strip() or len(code.encode())>MAX_FILE_BYTES:
            raise ValueError("app_invalid_source")
        if name.endswith(".py"):
            compile(code,name,"exec")  # Syntax check; does NOT execute generated code.
    with io.BytesIO() as output:
        with ZipFile(output,"w",ZIP_DEFLATED) as z:
            for name in sorted(files):
                z.writestr(name,files[name].encode())
        bundle=output.getvalue()
    if len(bundle)>MAX_ARCHIVE_BYTES:
        raise ValueError("app_archive_too_large")
    with ZipFile(io.BytesIO(bundle)) as z:
        if z.namelist()!=sorted(files):
            raise ValueError("app_archive_invalid")
    return bundle


def verify_project(files, render):
    """Test generated Python with no network, secrets, privileges or writable source.

    Docker's network is NONE. Buyer-required external API integrations remain
    separately unverified and must be disclosed, not claimed as tested.
    """
    make_zip(files)
    if not available():
        raise RuntimeError("app_isolated_runner_unavailable")
    with tempfile.TemporaryDirectory(prefix="gambit-app-") as folder:
        for name,content in files.items():
            target=Path(folder)/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(content,encoding="utf-8")
        # Run real supplied unit tests AND an independent HTTP smoke test.
        # Generated code can neither read the worker environment nor use network.
        smoke=(
          "import os,subprocess,time,urllib.request\n"
          "env={**os.environ,'PORT':'8765','PYTHONDONTWRITEBYTECODE':'1'}\n"
          "p=subprocess.Popen(['python','-B','server.py'],env=env,"
          "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
          "try:\n"
          "  body=None\n"
          "  for _ in range(25):\n"
          "    try:\n"
          "      with urllib.request.urlopen('http://127.0.0.1:8765/',timeout=1) as r:\n"
          "        body=r.read(100000)\n"
          "        if r.status==200: break\n"
          "    except Exception: time.sleep(.2)\n"
          "  if not body or b'<html' not in body.lower():\n"
          "    raise RuntimeError('app_http_smoke_failed')\n"
          "finally:\n"
          "  p.terminate()\n"
          "  try: p.wait(timeout=2)\n"
          "  except Exception: p.kill()\n"
        )
        runner=[
            "docker","run","--rm","--network","none","--read-only",
            "--cap-drop","ALL","--security-opt","no-new-privileges",
            "--pids-limit","48","--memory","256m","--cpus","1",
            "--user","65534:65534",
            "--tmpfs","/tmp:rw,nosuid,size=32m",
            "--volume",folder+":/workspace:ro","--workdir","/workspace",
            "--env","PYTHONDONTWRITEBYTECODE=1",
            "python:3.12-slim","python","-B","-c",
            "import subprocess,sys\n"
            "a=subprocess.run(['python','-B','-m','unittest','discover','-s','tests','-q'],"
            "timeout=20)\n"
            "if a.returncode: sys.exit(2)\n"+smoke
        ]
        try:
            result=subprocess.run(runner,timeout=75,capture_output=True,text=True,env={
                "PATH":os.environ.get("PATH","/usr/bin:/bin"),
                "HOME":os.environ.get("HOME","/tmp"),
                "DOCKER_HOST":os.environ.get("DOCKER_HOST","unix:///var/run/docker.sock")
            })
        except subprocess.TimeoutExpired as exc:
            raise ValueError("app_sandbox_timeout") from exc
        if result.returncode:
            raise ValueError("app_sandbox_tests_failed")
        # Use the established Playwright desktop AND mobile QA resource.
        assets={name:code.encode() for name,code in files.items()
                if name!="index.html" and name.startswith("assets/")}
        evidence=render(files["index.html"],asset_files=assets)
        if not {"desktop","mobile"}.issubset(evidence):
            raise ValueError("app_missing_device_qa")
        return {"sandbox":"isolated_tests_passed","http":"status_200_html",
                "desktop":True,"mobile":True,"files":sorted(files),
                "external_api_connections":"not_tested_in_offline_sandbox"}


def _plan_prompt(details):
    bounty=details.get("bounty") or {}
    return {field:str(bounty.get(field) or "")[:9000]
            for field in ("title","description","verification")}


def progress(details,state,bounty_id,claim_id,infer,render):
    """Advance at most ONE model-generated section per invocation.

    Caller must save/checkpoint state after EACH return. The job is not
    delivered until source code, tests and UI rendering all pass.
    """
    encrypted=state.setdefault("app_projects",{}).get(bounty_id)
    obj=unseal(encrypted) if encrypted else {}
    if obj.get("claim_id")!=claim_id:
        obj={"claim_id":claim_id,"plan":None,"files":{},"repairs":0,"feedback":""}
    brief=_plan_prompt(details)
    if obj["plan"] is None:
        plan=infer(PLAN_SCHEMA,
            "Design an ORIGINAL complete working Python-standard-library web app "
            "from the buyer brief, not a static brochure. Build linked pages and "
            "working browser interactions. Use only Python standard-library server, "
            "HTML, CSS and vanilla JS: no pip dependencies, paid services or "
            "unverified credentials. Required filenames: server.py, index.html, "
            "README.md, tests/test_app.py. Add only necessary assets/app.js, "
            "assets/app.css and other source files. tests/test_app.py must run "
            "offline using unittest. README must explain local operation and "
            "environment variables for external API keys if requested. Never "
            "claim offline tests verify live third-party API calls. Provide a "
            "specific per-file purpose and no placeholders. Return strict JSON.",
            brief,num_predict=1400,num_ctx=7000)
        if plan["status"]!="ready":
            return {"status":"need_information","question":plan.get("question",""),
                    "reason":"app_requirements_missing"}
        validate_plan(plan)
        obj["plan"]=plan
        state["app_projects"][bounty_id]=seal(obj)
        return {"status":"building","stage":"planned","completed":0,
                "total":len(plan["files"])}
    plan=obj["plan"]
    paths=validate_plan(plan)
    pending=next((item for item in plan["files"]
                  if item["path"] not in obj["files"]),None)
    if pending:
        result=infer(FILE_SCHEMA,
            "Write ONE complete, production-useful source file of the proposed "
            "web application. Include actual functioning logic; no TODOs, "
            "mock backend responses disguised as real data, paid packages, "
            "external CDN dependencies, or embedded API keys. Server must "
            "run with python server.py, use PORT environment variable, listen "
            "on 127.0.0.1, and serve index.html for GET /. Use stdlib only; "
            "provide local API routes relevant to buyer. Frontend must render "
            "without calling remote URLs on page load, to enable offline device "
            "QA. Tests use unittest and exercise actual endpoints. "
            "Avoid repeating other files; be consistent with their interfaces. "
            "Return JSON with EXACT requested path and complete content.",
            {"brief":brief,"plan":plan,"requested_file":pending,
             "existing_files":[{"path":name,"preview":content[:1400]}
                               for name,content in obj["files"].items()],
             "repair_feedback":obj.get("feedback","")},
            num_predict=3300,num_ctx=12000)
        path=safe_path(result.get("path"))
        if path!=pending["path"] or len(str(result.get("content") or "").encode())>MAX_FILE_BYTES:
            raise ValueError("app_wrong_or_oversized_file")
        obj["files"][path]=result["content"]
        obj["feedback"]=""
        state["app_projects"][bounty_id]=seal(obj)
        return {"status":"building","stage":"file_written",
                "completed":len(obj["files"]),"total":len(paths)}
    try:
        archive=make_zip(obj["files"])
        evidence=verify_project(obj["files"],render)
    except (ValueError,RuntimeError,SyntaxError) as exc:
        obj["repairs"]=int(obj.get("repairs") or 0)+1
        # Repair one file each cycle, retaining all other completed sections.
        name="server.py" if ("sandbox" in str(exc) or "http" in str(exc)
                             or "SyntaxError" in type(exc).__name__) else "tests/test_app.py"
        if "device" in str(exc) or "visual" in str(exc):
            name="index.html"
        obj["files"].pop(name,None)
        obj["feedback"]=str(exc)[:220]
        state["app_projects"][bounty_id]=seal(obj)
        return {"status":"building","stage":"repair", "failed_check":str(exc)[:90],
                "completed":len(obj["files"]),"total":len(paths)}
    return {"status":"ready","reason":"","question":"",
            "deliverable_format":"app_zip",
            "deliverable_title":str(plan["app_name"])[:90]+" — tested app source",
            "deliverable_text":"Application source, setup instructions, and offline smoke tests.",
            "_app_binary":archive,"_app_evidence":evidence,
            "_app_plan":plan}


def request_revision(state,bounty_id,feedback):
    encrypted=state.get("app_projects",{}).get(bounty_id)
    if not encrypted:
        return
    obj=unseal(encrypted)
    obj["files"].pop("server.py",None)
    obj["feedback"]=str(feedback)[:500]
    state["app_projects"][bounty_id]=seal(obj)
