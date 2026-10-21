#!/usr/bin/env python3
"""Existing account tool bridge for on-runner commercial intelligence.

No new registrations, tokens, or API services. The already configured GitHub
Actions secrets are used by narrow verified APIs, never passed to the local
LLM prompt and never echoed to stdout or public GitHub receipts.
"""
import json
import os
from pathlib import Path
import re
import subprocess

CATALOG_ROOTS = ("products", "ventures", "storefront")
ALLOWED_WORKFLOWS = {"oddly-useful-render.yml", "independent-commerce-operator.yml"}

TOOL_DEFINITIONS = {
    "stripe_offers": "Inspect LIVE existing Stripe payment-link inventory using repository secret; read-only, not an order or a sale.",
    "buffer_distribution": "Inspect connected Buffer TikTok channel, queue and delivery status; provider status alone is not public reach.",
    "repository_assets": "Inspect actual digital products, deployable code and venture artifacts in this repository.",
    "dispatch_existing_workflow": "Dispatch an existing approved GitHub operational workflow (requires an applicable verified need)."
}

def capability_status():
    """No credential values, no secret existence information beyond configured bools."""
    return {
        "github_actions": True,
        "python_nodejs_runner": True,
        "github_workflow_dispatch": bool(os.environ.get("GH_TOKEN")),
        "stripe": bool(os.environ.get("STRIPE_SECRET_KEY")),
        "agentmail": bool(os.environ.get("AGENTMAIL_API_KEY")),
        "buffer": bool(os.environ.get("BUFFER_API_KEY")),
        "render": "not_directly_connected; existing_chatgpt_watchdog_can_deploy",
        "gmail": "not_directly_connected; AgentMail_is_approved_inbox_equivalent",
        "tools": TOOL_DEFINITIONS
    }

def inspect_stripe():
    from tools.commerce_operator import stripe_get
    if not os.environ.get("STRIPE_SECRET_KEY"):
        return {"status": "not_configured"}
    # Payment link metadata only. NO customers, charges, email, funds or payment IDs.
    r = stripe_get("payment_links", {"limit": 30})
    offers=[]
    for link in r.get("data", []):
        if not isinstance(link, dict) or not link.get("id","").startswith("plink_"):
            continue
        url=str(link.get("url") or "")
        if not url.startswith("https://"):
            continue
        offers.append({"link_id": link["id"], "url": url,
                       "active": link.get("active") is True})
    return {"status": "live_api_verified", "offers": offers[:30],
            "offer_count": len(offers), "new_revenue_verified": None}

def inspect_buffer():
    if not os.environ.get("BUFFER_API_KEY"):
        return {"status": "not_configured"}
    from tools.buffer_publisher import find_channel, inspect_channel_posts
    channel=find_channel()
    inventory=inspect_channel_posts(channel)
    # Provider output can include post text (or previews) and should not be
    # copied wholesale into a public model receipt. Keep only safe counters.
    counters={}
    if isinstance(inventory,dict):
        for k,v in inventory.items():
            if isinstance(v,(int,bool)) and not isinstance(v,str):
                counters[k]=v
    return {"status":"provider_inventory_queried",
            "channel":"oddly.useful.stuff","summary_counters":counters,
            "social_publication_verified":False}

def inspect_assets():
    assets={}
    for folder in CATALOG_ROOTS:
        root=Path(folder)
        if not root.is_dir():
            assets[folder]=[]
            continue
        results=[]
        for p in sorted(root.rglob("*")):
            if not p.is_file():continue
            if p.suffix.lower() not in {".py",".js",".json",".md",".html",".csv",".txt",".svg",".mp4"}:continue
            if any(v in p.parts for v in (".git","node_modules","__pycache__")):continue
            if p.stat().st_size>1_000_000:continue
            results.append(str(p))
            if len(results)>=42:break
        assets[folder]=results
    return {"status":"repository_checked","assets":assets,
            "note":"Presence of a file alone does not establish buyer demand or legal sale readiness."}

def dispatch_workflow(name):
    if name not in ALLOWED_WORKFLOWS:
        raise ValueError("Workflow not in explicitly permitted registry")
    if not os.environ.get("GH_TOKEN") or not os.environ.get("GITHUB_REPOSITORY"):
        return {"status":"github_dispatch_not_configured"}
    result=subprocess.run(["gh","workflow","run",name,"--repo",
                           os.environ["GITHUB_REPOSITORY"],"--ref","main"],
                          text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          timeout=30,check=False)
    if result.returncode:
        return {"status":"github_dispatch_rejected","workflow":name}
    return {"status":"workflow_dispatch_accepted_not_execution_proof","workflow":name}

def execute_tool(name,argument=""):
    if name=="stripe_offers":
        return inspect_stripe()
    if name=="buffer_distribution":
        return inspect_buffer()
    if name=="repository_assets":
        return inspect_assets()
    if name=="dispatch_existing_workflow":
        return dispatch_workflow(argument)
    raise ValueError("Unknown commerce tool")

if __name__=="__main__":
    print(json.dumps({"capabilities":capability_status(),"assets":inspect_assets()}))
