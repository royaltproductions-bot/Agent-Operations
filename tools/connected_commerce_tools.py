#!/usr/bin/env python3
"""Provider layer for the autonomous commerce engine.

This module exposes real connected capabilities. It does not decide strategy.
Secrets stay in environment variables and provider/customer details are never
written to public receipts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

INBOX = "ari-operations@agentmail.to"
CATALOG_ROOTS = ("products", "ventures", "storefront")

TOOL_DEFINITIONS = {
    "stripe_offers": "Inspect current Stripe payment-link inventory.",
    "buffer_distribution": "Inspect the connected Buffer distribution channel.",
    "repository_assets": "Inspect products, ventures, storefronts, and deliverable assets in the repository.",
    "render_status": "Inspect the live Render deployment state.",
    "render_deployment": "Deploy the latest committed storefront revision when Render reports it is needed.",
}

def _request(url, bearer="", method="GET", body=None, basic=False, extra_headers=None):
    headers={"Accept":"application/json","User-Agent":"autonomous-commerce/2.0"}
    if bearer:
        headers["Authorization"]=(
            "Basic "+base64.b64encode((bearer+":").encode()).decode()
            if basic else "Bearer "+bearer
        )
    if extra_headers:
        headers.update(extra_headers)
    data=None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=25) as response:
            return json.loads(response.read(2_000_000).decode())
    except urllib.error.HTTPError as exc:
        host=urllib.parse.urlsplit(url).hostname or "provider"
        raise RuntimeError(f"provider_http_{exc.code}_{host}") from None
    except (urllib.error.URLError,TimeoutError):
        host=urllib.parse.urlsplit(url).hostname or "provider"
        raise RuntimeError(f"provider_unavailable_{host}") from None

def stripe_get(path,params=None):
    key=os.environ.get("STRIPE_SECRET_KEY","")
    if not key.startswith(("sk_live_","rk_live_","sk_test_","rk_test_")):
        raise RuntimeError("stripe_key_unavailable")
    query=urllib.parse.urlencode(params or {},doseq=True)
    return _request("https://api.stripe.com/v1/"+path+("?"+query if query else ""),
                    key,basic=True)

def agent_get(path,params=None):
    token=os.environ.get("AGENTMAIL_API_KEY","")
    if not token:
        raise RuntimeError("agentmail_key_unavailable")
    query=urllib.parse.urlencode(params or {},doseq=True)
    return _request(
        "https://api.agentmail.to/v0/inboxes/"+urllib.parse.quote(INBOX,safe="")+
        "/"+path+("?"+query if query else ""),token)

def agent_send(to,subject,body,files=None,order_id=""):
    token=os.environ.get("AGENTMAIL_API_KEY","")
    if not token:
        raise RuntimeError("agentmail_key_unavailable")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",str(to or "")):
        raise ValueError("invalid_recipient")
    if os.environ.get("COMMERCE_SEND_ENABLED")!="YES":
        raise RuntimeError("commerce_send_disabled")
    attachments=[]
    for name,content in files or []:
        attachments.append({
            "filename":name,
            "content_type":"text/plain" if name.endswith(".txt") else "text/csv",
            "content":base64.b64encode(content).decode(),
        })
    headers=None
    if order_id:
        headers={"Idempotency-Key":"commerce-"+hashlib.sha256(order_id.encode()).hexdigest()}
    response=_request(
        "https://api.agentmail.to/v0/inboxes/"+urllib.parse.quote(INBOX,safe="")+
        "/messages/send",token,method="POST",
        body={"to":[to],"subject":subject,"text":body,"attachments":attachments},
        extra_headers=headers)
    if not response.get("message_id"):
        raise RuntimeError("agentmail_send_unconfirmed")
    return response["message_id"]

def verified_paid_totals(max_pages=3):
    """Return aggregate verified Stripe Checkout results across all offers."""
    count=0
    gross_usd=0
    cursor=None
    for _ in range(max_pages):
        params={"limit":100}
        if cursor:
            params["starting_after"]=cursor
        page=stripe_get("checkout/sessions",params)
        data=page.get("data") or []
        for session in data:
            if not isinstance(session,dict):
                continue
            if session.get("status")!="complete" or session.get("payment_status")!="paid":
                continue
            if session.get("mode")!="payment":
                continue
            intent=session.get("payment_intent")
            if not isinstance(intent,str) or not intent.startswith("pi_"):
                continue
            try:
                pi=stripe_get("payment_intents/"+urllib.parse.quote(intent,safe=""))
                charge_id=pi.get("latest_charge")
                if (pi.get("status")!="succeeded" or not isinstance(charge_id,str)
                        or not charge_id.startswith("ch_")):
                    continue
                charge=stripe_get("charges/"+urllib.parse.quote(charge_id,safe=""))
                if (not charge.get("paid") or charge.get("refunded")
                        or int(charge.get("amount_refunded") or 0)>0
                        or charge.get("status")!="succeeded"):
                    continue
            except RuntimeError:
                continue
            count+=1
            if session.get("currency")=="usd":
                gross_usd+=int(session.get("amount_total") or 0)
        if not page.get("has_more") or not data:
            break
        cursor=data[-1].get("id")
        if not cursor:
            break
    return {"verified_paid_sessions":count,"confirmed_gross_usd":round(gross_usd/100,2)}

def capability_status():
    return {
        "github_actions":True,
        "stripe":bool(os.environ.get("STRIPE_SECRET_KEY")),
        "agentmail":bool(os.environ.get("AGENTMAIL_API_KEY")),
        "buffer":bool(os.environ.get("BUFFER_API_KEY")),
        "render":bool(os.environ.get("RENDER_API_KEY")),
        "tools":TOOL_DEFINITIONS,
    }

def inspect_stripe():
    if not os.environ.get("STRIPE_SECRET_KEY"):
        return {"status":"not_configured"}
    r=stripe_get("payment_links",{"limit":30})
    offers=[]
    for link in r.get("data") or []:
        if not isinstance(link,dict) or not str(link.get("id","")).startswith("plink_"):
            continue
        url=str(link.get("url") or "")
        if not url.startswith("https://"):
            continue
        offers.append({"link_id":link["id"],"url":url,"active":link.get("active") is True})
    return {"status":"live_api_verified","offers":offers[:30],"offer_count":len(offers)}

def inspect_buffer():
    if not os.environ.get("BUFFER_API_KEY"):
        return {"status":"not_configured"}
    from tools.buffer_publisher import find_channel, inspect_channel_posts
    channel=find_channel()
    inventory=inspect_channel_posts(channel)
    counters={}
    if isinstance(inventory,dict):
        counters={k:v for k,v in inventory.items()
                  if isinstance(v,(int,bool)) and not isinstance(v,str)}
    return {"status":"provider_inventory_queried","summary_counters":counters}

def inspect_assets():
    assets={}
    for folder in CATALOG_ROOTS:
        root=Path(folder)
        rows=[]
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if not p.is_file() or any(x in p.parts for x in (".git","node_modules","__pycache__")):
                    continue
                if p.stat().st_size>1_000_000:
                    continue
                rows.append(str(p))
                if len(rows)>=50:
                    break
        assets[folder]=rows
    return {"status":"repository_checked","assets":assets}

def execute_tool(name,argument=""):
    if name=="stripe_offers":
        return inspect_stripe()
    if name=="buffer_distribution":
        return inspect_buffer()
    if name=="repository_assets":
        return inspect_assets()
    if name=="render_status":
        from tools.render_direct_api import render_status
        return render_status()
    if name=="render_deployment":
        from tools.render_direct_api import deploy_site_if_needed
        return deploy_site_if_needed()
    raise ValueError("unknown_commerce_tool")

if __name__=="__main__":
    print(json.dumps({"capabilities":capability_status(),"assets":inspect_assets()}))
