#!/usr/bin/env python3
"""Model-selected Render deployments through the EXISTING connected ChatGPT watchdog.

GitHub Actions has NO Render API credentials. The independent model may record
an immutable deployment request when the published site changed; the existing
hourly watchdog calls the user's connected Render integration, verifies the
deployment, and writes a provider-grounded acknowledgement. Never call a queue
receipt "deployed". Never place secret credentials in git.
"""
from datetime import datetime, timezone
import json
import re
import subprocess
from pathlib import Path

SERVICE_ID = "srv-dbc3j8uq1p3s73c63sv0"
WORKSPACE_ID = "tea-dbc3ifrbc2fs73b5oudg"
SITE_PATH = "storefront"
REQUESTS = Path("ops/render-deploy-requests")
ACKS = Path("ops/render-deploy-acks")
BASELINE = "8e5a64ebe26dd9d27e54b4da615cd24f6ab47b67"  # Render live deploy 2026-10-21
SHA = re.compile(r"^[0-9a-f]{40}$")

def git(args):
    r = subprocess.run(["git"] + list(args), check=False,
                       text=True, capture_output=True, timeout=18)
    if r.returncode:
        raise RuntimeError("git_source_state_unavailable")
    return r.stdout.strip()

def site_revision():
    """Git commit that last changed the published site, not unrelated repo commits."""
    rev = git(["log", "-1", "--format=%H", "--", SITE_PATH])
    if not SHA.fullmatch(rev):
        raise RuntimeError("site_revision_not_verified")
    return rev

def latest_acknowledged_revision():
    """Only confirmed live Render deployments are recognized as published."""
    rev = BASELINE
    for path in sorted(ACKS.glob("*.json")):
        try:
            ack=json.loads(path.read_text(encoding="utf-8"))
        except (ValueError,OSError):
            continue
        if (ack.get("service_id")==SERVICE_ID
            and ack.get("status")=="live"
            and ack.get("verified_by")=="render_connector"
            and SHA.fullmatch(str(ack.get("site_revision","")))):
            rev=ack["site_revision"]
    return rev

def render_status():
    current=site_revision()
    deployed=latest_acknowledged_revision()
    requested=REQUESTS/(current+".json")
    return {
        "service_id": SERVICE_ID,
        "site_revision": current,
        "last_verified_live_site_revision": deployed,
        "needs_deployment": current!=deployed,
        "already_requested": requested.exists(),
        "connection": "existing_chatgpt_render_connector_not_available_inside_github",
        "status": "pending_deployment" if current!=deployed else "already_live_as_last_verified"
    }

def request_render_deployment():
    """Make an auditable request; does not publish or make paid API calls."""
    state=render_status()
    if not state["needs_deployment"]:
        return {"status":"no_new_site_changes","site_revision":state["site_revision"]}
    if state["already_requested"]:
        return {"status":"already_queued_not_deployed","site_revision":state["site_revision"]}
    if not Path(SITE_PATH, "index.html").is_file():
        raise RuntimeError("site_has_no_index")
    REQUESTS.mkdir(parents=True,exist_ok=True)
    path=REQUESTS/(state["site_revision"]+".json")
    request={
        "schema_version":1,
        "status":"awaiting_render_connector_execution",
        "requested_at_utc":datetime.now(timezone.utc).isoformat(),
        "requested_by":"independent_github_ollama_model",
        "service_id":SERVICE_ID,
        "workspace_id":WORKSPACE_ID,
        "site_revision":state["site_revision"],
        "target_branch":"main",
        "site_root":SITE_PATH,
        "live_url":"https://vendorsettle-checkout.onrender.com",
        "important":"This is a request, not a Render deployment or live proof.",
    }
    with path.open("x",encoding="utf-8") as file:
        json.dump(request,file,indent=2)
        file.write("\n")
    return {"status":"queued_for_existing_render_connector_not_deployed",
            "site_revision":state["site_revision"],
            "request":str(path)}

if __name__=="__main__":
    print(json.dumps(render_status()))
