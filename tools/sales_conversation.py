#!/usr/bin/env python3
"""Actual buyer-conversation continuation using the existing AgentMail account.

Only follow up on a real response to the independent agent's own previously
verified sent proposal. No scraped/uninvited contacts, no arbitrary recipients,
no automated contracts, invented quotes, payments, refunds or legal assurances.
One reply per independently verified incoming message, remotely deduplicated.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
import hashlib
import os
import re

from tools.commerce_operator import INBOX, agent_get, agent_send

REPLIES={
    "clarify_scope": (
        "Thanks for responding. Could you share a concrete description of the "
        "deliverable, required input/output formats, timeline, and acceptance "
        "criteria? I can then determine whether our existing tools can deliver it."
    ),
    "clarify_budget": (
        "Thanks for the details. What budget or fixed project amount do you have "
        "in mind, and what is the acceptance process? We will confirm scope and "
        "payment arrangements before beginning any paid work."
    ),
    "clarify_delivery": (
        "Thanks for the inquiry. Our automated workflow can create and test "
        "bounded software/data outputs and original content using existing "
        "owned assets. Please specify the deliverable and acceptance tests. "
        "I won't represent an untested capability as already delivered."
    ),
    "decline": (
        "Thank you for the reply. I don't have verified capacity to accept "
        "that particular engagement. I won't make delivery commitments I "
        "cannot fulfill. Wishing you success with the project."
    ),
}
SENSITIVE=re.compile(
    r"\b(refund|chargeback|legal claim|lawsuit|attorney|"
    r"credit card|card number|bank account|ssn|social security|password|"
    r"contract signature|sign this agreement|wire transfer|upfront deposit)\b",re.I
)
HEX=re.compile(r"^[0-9a-f]{18}$")
EMAIL=re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def known_invitations(history):
    tokens=set()
    for receipt in history:
        for a in receipt.get("actions",[]):
            if (a.get("result")=="sent_invited_proposal"
                and isinstance(a.get("source"),str)):
                tokens.add(hashlib.sha256(a["source"].encode()).hexdigest()[:18])
    return tokens

def pending_replies(history,now=None):
    """Return local-model-only buyer messages; NEVER emit their content in receipts."""
    if not os.environ.get("AGENTMAIL_API_KEY"):
        return []
    ids=known_invitations(history)
    if not ids:return []
    msgs=agent_get("messages",{"limit":90}).get("messages") or []
    cutoff=(now or datetime.now(timezone.utc))-timedelta(days=21)
    candidates=[]
    for msg in msgs:
        labels=set(msg.get("labels") or [])
        if "received" not in labels or labels.intersection({"spam","trash","blocked"}):
            continue
        armor=msg.get("agent_armor") or {}
        if armor.get("status") in {"pending","flagged","review","blocked"}:continue
        authentication=msg.get("authentication_results") or {}
        if (authentication.get("spf")=="fail" and authentication.get("dkim")=="fail"):
            continue
        sender=parseaddr(str(msg.get("from") or ""))[1].strip().lower()
        if not EMAIL.fullmatch(sender) or sender==INBOX.lower():continue
        if msg.get("attachments"):continue
        subject=str(msg.get("subject") or "")
        token=re.search(r"Regarding your public contract invitation \[([a-f0-9]{18})\]",
                        subject,re.I)
        if not token or token.group(1) not in ids:continue
        if not str(msg.get("message_id") or ""):continue
        try:
            stamp=datetime.fromisoformat(str(msg.get("timestamp") or "").replace("Z","+00:00"))
            if stamp.tzinfo is None or stamp<cutoff:continue
        except (TypeError,ValueError):
            continue
        text=str(msg.get("text") or msg.get("body") or "")[:1500]
        ident=hashlib.sha256(str(msg["message_id"]).encode()).hexdigest()[:18]
        # If the request needs a sensitive human decision, omit it from autonomous replies.
        if SENSITIVE.search(subject+"\n"+text):continue
        candidates.append({"id":ident,"thread_token":token.group(1),
                           "subject":subject[:160],"message":text[:900],
                           "from":sender})
    return candidates[:3]

def send_followup(item,kind,history,enabled=False):
    if kind not in REPLIES:
        raise ValueError("unknown_followup_kind")
    if not item or not HEX.fullmatch(str(item.get("id",""))):
        raise ValueError("invalid_buyer_reply")
    if item["thread_token"] not in known_invitations(history):
        raise ValueError("reply_without_verified_original_outreach")
    if not EMAIL.fullmatch(item["from"]):
        raise ValueError("invalid_recipient")
    if SENSITIVE.search(item.get("subject","")+"\n"+item.get("message","")):
        return {"result":"sensitive_inquiry_requires_human","id":item["id"]}
    previous=any(a.get("tool")=="reply_verified_buyer" and a.get("target")==item["id"]
                 and a.get("result") in {"sent","delivery_uncertain","remote_already_sent"}
                 for r in history for a in r.get("actions",[]))
    if previous:
        return {"result":"already_replied","id":item["id"]}
    if not enabled:
        return {"result":"eligible_dry_run","id":item["id"]}
    subject=("Re: Regarding your public contract invitation ["
             +item["thread_token"]+"] Follow-up ["+item["id"]+"]")
    # Provider-side sent history confirms retries don't duplicate.
    remote=agent_get("messages",{"limit":100,"labels":["sent"],"subject":[subject]})
    if any(m.get("subject")==subject and "sent" in (m.get("labels") or [])
           for m in remote.get("messages") or []):
        return {"result":"remote_already_sent","id":item["id"]}
    try:
        sent=agent_send(item["from"],subject,REPLIES[kind],[],
                        order_id="verified-buyer-followup-"+item["id"])
    except (RuntimeError,ValueError):
        return {"result":"delivery_uncertain","id":item["id"]}
    return {"result":"sent" if sent else "delivery_uncertain",
            "id":item["id"],"response_kind":kind}

def followup_opportunity(history,choose,model_prompt,enabled=False):
    """Model selects from legitimate inquiry IDs/kinds only; no freeform outbound."""
    pending=pending_replies(history)
    if not pending:return None
    choices=["skip"]
    for m in pending:
        for kind in REPLIES:
            choices.append(m["id"]+"|"+kind)
    schema={"type":"object","properties":{"choice":{"type":"string","enum":choices}},
            "required":["choice"],"additionalProperties":False}
    observations=[{"id":m["id"],"message":m["message"],"subject":m["subject"]}
                  for m in pending]
    choice=choose(schema,model_prompt+"\nYour priority is a real buyer's reply. "
                  "Choose a safe response to one previously verified inbound "
                  "message. Do not accept contracts or invent deliverables.",
                  {"messages":observations,"choices":choices})
    val=choice["choice"]
    if val=="skip":return {"tool":"reply_verified_buyer","result":"model_deferred"}
    if val not in choices:raise ValueError("model_choice_outside_verified_replies")
    ident,kind=val.split("|")
    item=next(x for x in pending if x["id"]==ident)
    outcome=send_followup(item,kind,history,enabled=enabled)
    return {"tool":"reply_verified_buyer","target":ident,**outcome}
