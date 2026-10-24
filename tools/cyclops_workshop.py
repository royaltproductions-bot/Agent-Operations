#!/usr/bin/env python3
"""Run model-authored production code against DISCOVERED tools, without repo secrets.

The Docker container receives only a scratch work folder; network is disabled,
the image is read-only, privileges dropped, and no GitHub/Stripe/AgentMail
credentials are exposed. A product is NOT restricted to document/spreadsheet
formats; whatever local tools can really manufacture becomes eligible.
"""
from __future__ import annotations
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

IMAGE=os.getenv("CYCLOPS_WORKSHOP_IMAGE","cyclops-workshop:local")
MAX_ARTIFACT_BYTES=950000
MAX_TOTAL_BYTES=1400000
CAPABILITY_PROBE="""
import importlib.metadata, importlib.util, json, shutil, sys
# Discover the environment that actually exists rather than presenting
# the model with a human-selected list of product or file formats.
installed=sorted({str(d.metadata.get('Name') or '').strip()
                  for d in importlib.metadata.distributions()
                  if str(d.metadata.get('Name') or '').strip()})
mods=('PIL','reportlab','openpyxl','docx','pptx','matplotlib','cairosvg','svgwrite','pypdf','fpdf')
bins=('python3','node','ffmpeg','convert','magick','dot','rsvg-convert')
print(json.dumps({'python':sys.version.split()[0],
                  'installed_packages':installed,
                  'importable_modules':[m for m in mods if importlib.util.find_spec(m)],
                  'runnable_commands':[b for b in bins if shutil.which(b)],
                  'workspace':'/work',
                  'network':'disabled',
                  'artifacts':'use whichever installed tools can manufacture the chosen product'}))
"""

def _container(cmd,folder=None):
    args=["docker","run","--rm","--network","none","--read-only",
          "--cap-drop","ALL","--security-opt","no-new-privileges",
          "--pids-limit","96","--cpus","2","--memory","1500m",
          "--user","65534:65534",
          "--tmpfs","/tmp:rw,nosuid,nodev,size=64m"]
    if folder is not None:
        args+=["--mount","type=bind,src="+str(folder)+",dst=/work",
               "--workdir","/work"]
    args += [IMAGE]+cmd
    # The GitHub job's 45-minute bound is the production execution deadline.
    return subprocess.run(args,capture_output=True,text=True,check=False)


def discover_tools():
    probe=_container(["python3","-c",CAPABILITY_PROBE])
    if probe.returncode:
        raise RuntimeError("workshop_capabilities_unavailable:"+
                           (probe.stderr or probe.stdout)[-300:])
    try:
        data=json.loads(probe.stdout.strip())
    except ValueError:
        raise RuntimeError("workshop_capabilities_invalid_response") from None
    data["execution_tool"]="isolated_python_orchestrator"
    data["rules"]="Python may invoke only tools installed in this container; never claim a file exists until actually generated and inspected."
    return data


def _safe_path(path):
    """Accept paths relative to /work or explicitly rooted inside /work.

    The model sees /work as its sandbox output directory, while the host
    verifier uses paths relative to the mounted scratch directory.
    Never allow other absolute locations or parent traversal.
    """
    name=str(path or "").strip().replace("\\","/")
    if name.startswith("/work/"):
        name=name[len("/work/"):]
    p=PurePosixPath(name)
    if (not name or p.is_absolute() or any(t in ("", ".", "..") for t in name.split("/"))
            or len(name)>180 or len(p.parts)>5):
        raise ValueError("unsafe_build_path:"+str(path or "")[:90])
    return p


def manufacture(script,outputs):
    """Execute a real build, then return authenticated file content, never claims."""
    if not isinstance(script,str) or not script.strip() or len(script)>150000:
        raise ValueError("empty_or_oversized_build_script")
    if not isinstance(outputs,list) or not outputs or len(outputs)>30:
        raise ValueError("missing_production_outputs")
    paths=[_safe_path(x) for x in outputs]
    if len({str(x).casefold() for x in paths})!=len(paths):
        raise ValueError("duplicate_output_names")
    with tempfile.TemporaryDirectory(prefix="cyclops-build-") as temp:
        root=Path(temp)
        root.chmod(0o777)
        src=root/"build.py"
        src.write_text(script,encoding="utf-8")
        src.chmod(0o644)
        result=_container(["python3","/work/build.py"],root)
        if result.returncode:
            raise RuntimeError("production_tool_execution_failed:"+
                               (result.stderr or result.stdout)[-850:])
        artifacts=[]
        total=0
        for name in paths:
            p=root.joinpath(*name.parts)
            if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root.resolve()):
                raise RuntimeError("output_file_not_created:"+str(name))
            content=p.read_bytes()
            if not content or len(content)>MAX_ARTIFACT_BYTES:
                raise RuntimeError("output_empty_or_exceeds_vault_capacity:"+str(name))
            total+=len(content)
            if total>MAX_TOTAL_BYTES:
                raise RuntimeError("output_package_exceeds_vault_capacity")
            mime=mimetypes.guess_type(str(name))[0] or "application/octet-stream"
            artifacts.append({
                "filename":str(name),"content_b64":base64.b64encode(content).decode("ascii"),
                "size_bytes":len(content),"sha256":hashlib.sha256(content).hexdigest(),
                "content_type":mime,
            })
        return artifacts
