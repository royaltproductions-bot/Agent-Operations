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
import mimetypes
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

INBOX = "ari-operations@agentmail.to"
CATALOG_ROOTS = ("products", "ventures", "storefront")
CHECKOUT_BRAND = "Ari Operations"
CHECKOUT_MERCHANT_NOTICE = "Ari Operations · Payments processed by Sirius Sage."

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
        with urllib.request.urlopen(req,timeout=30) as response:
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
            "content_type":mimetypes.guess_type(name)[0] or "text/plain",
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


def stripe_post(path,fields,idempotency_key=""):
    """Stripe form-encoded mutation with deterministic idempotency."""
    key=os.environ.get("STRIPE_SECRET_KEY","")
    if not key.startswith(("sk_live_","rk_live_","sk_test_","rk_test_")):
        raise RuntimeError("stripe_key_unavailable")
    headers={
        "Accept":"application/json",
        "User-Agent":"autonomous-commerce/3.0",
        "Authorization":"Basic "+base64.b64encode((key+":").encode()).decode(),
        "Content-Type":"application/x-www-form-urlencoded",
    }
    if idempotency_key:
        headers["Idempotency-Key"]=idempotency_key[:255]
    data=urllib.parse.urlencode(fields,doseq=True).encode()
    req=urllib.request.Request("https://api.stripe.com/v1/"+path,data=data,
                               headers=headers,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=30) as response:
            return json.loads(response.read(2_000_000).decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError("stripe_write_http_"+str(exc.code)) from None
    except (urllib.error.URLError,TimeoutError):
        raise RuntimeError("stripe_write_unavailable") from None

def _offer_id(source_url):
    return hashlib.sha256(str(source_url).encode()).hexdigest()[:24]

def existing_autonomous_offer(offer_id):
    """Find an already-created payment link by stable autonomous offer id."""
    page=stripe_get("payment_links",{"limit":100})
    for link in page.get("data") or []:
        if not isinstance(link,dict):
            continue
        metadata=link.get("metadata") or {}
        if metadata.get("autonomous_offer_id")==offer_id:
            return {
                "offer_id":offer_id,
                "payment_link_id":link.get("id"),
                "checkout_url":link.get("url"),
                "active":link.get("active") is True,
                "customer_brand":metadata.get("customer_brand"),
            }
    return None

def stripe_usd_unit_amount(price_usd):
    """Convert a dollar-denominated decision into Stripe's USD minor units.

    This is a provider transport conversion, NOT an autonomous price limit.
    It rejects currency-format errors instead of silently rounding a price.
    """
    raw=str(price_usd).strip().lower().replace(",","")
    raw=raw.removeprefix("$").removesuffix("usd").strip()
    try:
        dollars=Decimal(raw)
    except InvalidOperation:
        raise ValueError("checkout_price_invalid_usd") from None
    if not dollars.is_finite() or dollars<0:
        raise ValueError("checkout_price_invalid_usd")
    cents=dollars*100
    if cents!=cents.to_integral_value():
        raise ValueError("checkout_price_subcent_precision")
    return int(cents)


def ensure_autonomous_checkout(source_url,title,description,scope,price_usd):
    """Create or recover one Stripe checkout for an autonomous digital offer."""
    offer_id=_offer_id(source_url)
    existing=existing_autonomous_offer(offer_id)
    if (existing and existing.get("active") and existing.get("checkout_url")
            and existing.get("customer_brand")==CHECKOUT_BRAND):
        return existing
    if existing:
        # Legacy X-variant links were deliberately retired after a failed
        # product-quality review. Do not reuse an old unbranded link, either.
        raise RuntimeError("previous_autonomous_offer_retired_for_source")
    amount=stripe_usd_unit_amount(price_usd)
    # Provider-boundary conversion only; no invented commercial price range.
    # Stripe validates actual checkout eligibility.
    safe_title=re.sub(r"\s+"," ",str(title)).strip()[:120]
    safe_desc=re.sub(r"\s+"," ",str(description)).strip()[:400]
    safe_scope=re.sub(r"\s+"," ",str(scope)).strip()[:450]
    safe_source=str(source_url)[:450]
    product=stripe_post("products",{
        "name":(CHECKOUT_BRAND+" | "+safe_title)[:120],
        "description":safe_desc,
        "metadata[autonomous_revenue]":"yes",
        "metadata[customer_brand]":CHECKOUT_BRAND,
        "metadata[autonomous_offer_id]":offer_id,
        "metadata[source_url]":safe_source,
        "metadata[scope]":safe_scope,
    },"auto-product-"+offer_id)
    product_id=product.get("id")
    if not isinstance(product_id,str) or not product_id.startswith("prod_"):
        raise RuntimeError("stripe_product_creation_unconfirmed")
    price=stripe_post("prices",{
        "unit_amount":amount,
        "currency":"usd",
        "product":product_id,
    },"auto-price-"+offer_id)
    price_id=price.get("id")
    if not isinstance(price_id,str) or not price_id.startswith("price_"):
        raise RuntimeError("stripe_price_creation_unconfirmed")
    link=stripe_post("payment_links",{
        "line_items[0][price]":price_id,
        "line_items[0][quantity]":"1",
        "metadata[autonomous_revenue]":"yes",
        "metadata[customer_brand]":CHECKOUT_BRAND,
        "metadata[autonomous_offer_id]":offer_id,
        "metadata[source_url]":safe_source,
        "metadata[scope]":safe_scope,
        "custom_text[submit][message]":CHECKOUT_MERCHANT_NOTICE,
        "payment_intent_data[description]":(CHECKOUT_BRAND+" | "+safe_title)[:200],
    },"auto-link-"+offer_id)
    link_id=link.get("id")
    url=link.get("url")
    if not isinstance(link_id,str) or not link_id.startswith("plink_") or not str(url).startswith("https://"):
        raise RuntimeError("stripe_payment_link_creation_unconfirmed")
    return {"offer_id":offer_id,"payment_link_id":link_id,
            "checkout_url":url,"active":link.get("active") is True}

def _verified_session(session):
    if (not isinstance(session,dict) or session.get("status")!="complete"
            or session.get("payment_status")!="paid" or session.get("mode")!="payment"):
        return False
    intent=session.get("payment_intent")
    if not isinstance(intent,str) or not intent.startswith("pi_"):
        return False
    pi=stripe_get("payment_intents/"+urllib.parse.quote(intent,safe=""))
    charge_id=pi.get("latest_charge")
    if pi.get("status")!="succeeded" or not isinstance(charge_id,str) or not charge_id.startswith("ch_"):
        return False
    charge=stripe_get("charges/"+urllib.parse.quote(charge_id,safe=""))
    return bool(charge.get("paid") and not charge.get("refunded")
                and int(charge.get("amount_refunded") or 0)==0
                and charge.get("status")=="succeeded")

def autonomous_paid_orders(limit=100):
    """Verified paid autonomous orders; ignore unrelated Stripe history cheaply."""
    links_page=stripe_get("payment_links",{"limit":100})
    autonomous_links={}
    for link in links_page.get("data") or []:
        if not isinstance(link,dict):
            continue
        metadata=link.get("metadata") or {}
        ident=link.get("id")
        if (metadata.get("autonomous_revenue")=="yes"
                and isinstance(ident,str) and ident.startswith("plink_")):
            autonomous_links[ident]=metadata
    if not autonomous_links:
        return []

    page=stripe_get("checkout/sessions",{"limit":min(int(limit),100)})
    orders=[]
    for session in page.get("data") or []:
        link_id=session.get("payment_link")
        metadata=autonomous_links.get(link_id)
        if not metadata:
            continue
        try:
            if not _verified_session(session):
                continue
        except RuntimeError:
            continue
        customer=(session.get("customer_details") or {}).get("email") or session.get("customer_email")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",str(customer or "")):
            continue
        orders.append({
            "session_id":session.get("id"),
            "offer_id":metadata.get("autonomous_offer_id"),
            "source_url":metadata.get("source_url"),
            "scope":metadata.get("scope"),
            "customer_email":customer,
            "amount_total":int(session.get("amount_total") or 0),
            "currency":session.get("currency"),
        })
    return orders

def recent_sent_subjects(limit=100):
    """One provider read for duplicate protection across an autonomous run."""
    result=agent_get("messages",{"limit":min(int(limit),100),"labels":["sent"]})
    return {
        str(m.get("subject") or "")
        for m in result.get("messages") or []
        if "sent" in (m.get("labels") or [])
    }

def sent_subject_exists(subject):
    """Provider-grounded duplicate protection for a single autonomous action."""
    return str(subject) in recent_sent_subjects()

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
