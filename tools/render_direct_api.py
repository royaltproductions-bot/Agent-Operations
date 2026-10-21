#!/usr/bin/env python3
"""Direct Render REST client for the existing independently running commerce AI.

The credential is read ONLY from GitHub Actions RENDER_API_KEY secret at runtime.
This does not depend on ChatGPT, a manual deploy hook or an hourly intermediary.
Never print the key, headers, full HTTP errors, or unrelated Render resources.

Only operate on the existing Render static service and its Git-backed main branch.
No creation/deletion, billing, service settings or environment-variable mutation.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

API_ROOT = "https://api.render.com/v1"
SERVICE_ID = "srv-dbc3j8uq1p3s73c63sv0"
WORKSPACE_ID = "tea-dbc3ifrbc2fs73b5oudg"
SOURCE_REPO = "https://github.com/royaltproductions-bot/Agent-Operations"
PUBLISH_DIR = "storefront"
LIVE_URL = "https://vendorsettle-checkout.onrender.com"
SHA = re.compile(r"^[0-9a-f]{40}$")
PENDING = {"created", "queued", "build_in_progress", "update_in_progress",
           "pre_deploy_in_progress"}

def git(*args):
    p = subprocess.run(["git", *args], capture_output=True, text=True,
                       timeout=12, check=False)
    if p.returncode:
        raise RuntimeError("git_state_unavailable")
    return p.stdout.strip()

def commit_has_site_revision(commit, site_revision):
    """Use exact Git ancestry rather than a matching timestamp or partial SHA."""
    if not SHA.fullmatch(str(commit)) or not SHA.fullmatch(str(site_revision)):
        return False
    p = subprocess.run(["git", "merge-base", "--is-ancestor",
                        site_revision, commit], capture_output=True,
                       text=True, timeout=12, check=False)
    return p.returncode == 0

def source_state():
    head = git("rev-parse", "HEAD")
    changed = git("log", "-1", "--format=%H", "--", PUBLISH_DIR)
    if not SHA.fullmatch(head) or not SHA.fullmatch(changed):
        raise RuntimeError("github_checkout_revision_unverified")
    if not commit_has_site_revision(head, changed):
        raise RuntimeError("github_site_revision_not_on_checkout")
    return {"head_commit":head, "site_revision":changed}

def api(method, path, data=None, opener=None):
    """Call only service-specific endpoints, no user-directed arbitrary URL."""
    if path not in ("/services/"+SERVICE_ID,
                    "/services/"+SERVICE_ID+"/deploys") and not re.fullmatch(
            "/services/"+SERVICE_ID+r"/deploys/dep-[a-z0-9]+", path):
        raise ValueError("render_endpoint_not_permitted")
    key=os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("render_api_key_not_configured")
    if method not in ("GET", "POST"):
        raise ValueError("render_method_not_permitted")
    if method=="POST" and path!="/services/"+SERVICE_ID+"/deploys":
        raise ValueError("render_write_not_permitted")
    headers={"Authorization":"Bearer "+key,"Accept":"application/json"}
    payload=None
    if data is not None:
        if method!="POST":
            raise ValueError("render_unexpected_body")
        payload=json.dumps(data).encode("utf-8")
        headers["Content-Type"]="application/json"
    request=urllib.request.Request(API_ROOT+path,method=method,
                                   data=payload,headers=headers)
    try:
        with (opener or urllib.request.urlopen)(request,timeout=25) as response:
            return json.loads(response.read(250000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not echo response bodies, which might expose account details.
        raise RuntimeError("render_http_"+str(exc.code)) from None
    except (urllib.error.URLError,TimeoutError):
        raise RuntimeError("render_transport_unavailable") from None

def service():
    row=api("GET","/services/"+SERVICE_ID)
    details=row.get("serviceDetails") or {}
    if (row.get("id")!=SERVICE_ID
        or row.get("ownerId")!=WORKSPACE_ID
        or row.get("type")!="static_site"
        or row.get("branch")!="main"
        or str(row.get("repo","")).removesuffix(".git").rstrip("/")!=SOURCE_REPO
        or details.get("publishPath") not in ("./storefront","storefront")):
        raise RuntimeError("render_service_scope_verification_failed")
    return {"id":SERVICE_ID,"workspace":WORKSPACE_ID,
            "branch":"main","repo":SOURCE_REPO,"url":LIVE_URL,
            "verified":True}

def deployments():
    rows=api("GET","/services/"+SERVICE_ID+"/deploys")
    if not isinstance(rows,list):
        raise RuntimeError("render_deploy_list_malformed")
    return [r.get("deploy",r) for r in rows[:20]
            if isinstance(r,dict) and isinstance(r.get("deploy",r),dict)]

def render_status():
    """Authoritative provider status plus site commit ancestry, never invented live."""
    svc=service()
    state=source_state()
    rows=deployments()
    current=next((r for r in rows if r.get("status") in PENDING),None)
    live=next((r for r in rows if r.get("status")=="live"),None)
    live_commit=(live.get("commit") or {}).get("id","") if live else ""
    is_current=bool(live and commit_has_site_revision(live_commit,state["site_revision"]))
    return {
        "status":"live_for_site_revision" if is_current else
                 ("deploy_in_progress" if current else "site_revision_undeployed"),
        "connection":"direct_render_api",
        "service_id":svc["id"],
        "site_revision":state["site_revision"],
        "head_commit":state["head_commit"],
        "provider_live_commit":live_commit if SHA.fullmatch(live_commit) else None,
        "latest_live_deploy_id":live.get("id") if live else None,
        "pending_deploy_id":current.get("id") if current else None,
        "needs_deployment":not is_current and not current,
        "live_url":LIVE_URL
    }

def deploy_site_if_needed():
    """Deploy the EXACT checkout SHA, only if site content is not already live.

    A newly submitted Render build is merely accepted: not proof of publication.
    Provider state checks prevent duplicate deploys while build is pending.
    """
    state=render_status()
    if state["status"]=="live_for_site_revision":
        return {"status":"already_live_verified","service_id":SERVICE_ID,
                "site_revision":state["site_revision"],
                "deploy_id":state["latest_live_deploy_id"],
                "provider_commit":state["provider_live_commit"]}
    if state["status"]=="deploy_in_progress":
        return {"status":"existing_build_in_progress","service_id":SERVICE_ID,
                "deploy_id":state["pending_deploy_id"],
                "site_revision":state["site_revision"]}
    # Fail closed: an unrelated active site is NOT silently replaced with a
    # newer checkout artifact, and no custom commit provided by the language model.
    revision=state["head_commit"]
    if not commit_has_site_revision(revision,state["site_revision"]):
        raise RuntimeError("target_not_site_ancestor")
    launched=api("POST","/services/"+SERVICE_ID+"/deploys",{
        "clearCache":"do_not_clear",
        "commitId":revision,
        "deployMode":"build_and_deploy"
    })
    if not isinstance(launched,dict) or not str(launched.get("id","")).startswith("dep-"):
        raise RuntimeError("render_deploy_not_accepted")
    return {"status":"deploy_accepted_not_live_verified","service_id":SERVICE_ID,
            "deploy_id":launched["id"],"commit_id":revision,
            "provider_status":launched.get("status")}

def main():
    if len(sys.argv)>2 or (len(sys.argv)==2 and sys.argv[1] not in ("--check","--deploy")):
        raise SystemExit("usage: python3 -m tools.render_direct_api [--check|--deploy]")
    try:
        value=deploy_site_if_needed() if len(sys.argv)==2 and sys.argv[1]=="--deploy" else render_status()
    except (RuntimeError,ValueError) as exc:
        # Controlled exception reason, never credentials or provider response.
        print(json.dumps({"render_tool":"failed","reason":str(exc)}))
        raise SystemExit(1)
    print(json.dumps({"render_tool":value}))

if __name__=="__main__":
    main()
