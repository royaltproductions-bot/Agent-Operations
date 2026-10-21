#!/usr/bin/env python3
"""Small cross-run memory for the autonomous commerce engine.

Business models stay open-ended. Only technical search connectors are finite.
"""
from __future__ import annotations

import re

CHANNELS=("hackernews","reddit_forhire","github")

STOPWORDS=set("""a an and are as at be but by can do for from have how i in into is it
its of on or our out paid please real seeking service small software the their them
there this to tool using via we with work would you your custom actual buyer specific
publicly business owner project company offering""".split())

def _tokens(value):
    return {
        word for word in re.findall(r"[a-z0-9]+",str(value or "").lower())
        if len(word)>=3 and word not in STOPWORDS
    }

def _overlap(a,b):
    left,right=_tokens(a),_tokens(b)
    if len(left)<2 or len(right)<2:
        return 0.0
    return len(left & right)/min(len(left),len(right))

def prior_experiments(history):
    rows=[]
    for receipt in reversed(history):
        for item in receipt.get("hypotheses",[]):
            if not isinstance(item,dict):
                continue
            rows.append({
                "channel":item.get("channel"),
                "buyer":item.get("buyer",""),
                "problem":item.get("problem",""),
                "offer":item.get("business_model",""),
                "query":item.get("query",""),
                "reached_buyer":bool(receipt.get("qualified_external_contacts")),
            })
    return rows

def reject_repetition(proposal,history,current=()):
    """Flag only repeated dead-end searches; never reject a business category."""
    query=str(proposal.get("search_query") or "").strip()
    if len(query)<4:
        return "empty_or_non_searchable_hypothesis"

    buyer=str(proposal.get("buyer") or "")
    problem=str(proposal.get("customer_problem") or "")
    offer=str(proposal.get("offer") or "")
    combined=" ".join((buyer,problem,offer))

    for old in list(prior_experiments(history))[-30:]+list(current):
        old_query=str(old.get("query") or "")
        old_combined=" ".join((
            str(old.get("buyer") or ""),
            str(old.get("problem") or ""),
            str(old.get("offer") or ""),
        ))
        if query.lower()==old_query.strip().lower():
            return "repeat_exact_search"
        if (proposal.get("search_channel")==old.get("channel")
                and _overlap(query,old_query)>=0.65):
            return "repeat_rephrased_search"
        if _overlap(combined,old_combined)>=0.60 and not old.get("reached_buyer"):
            return "recycled_unvalidated_market"
    return None

def diagnostics(history,current=()):
    recent=(prior_experiments(history)[-20:]+list(current))[-20:]
    misses=sum(not x.get("reached_buyer") for x in recent)
    return {
        "recent_experiments":len(recent),
        "recent_without_buyer_reach":misses,
        "unique_demand_channels":len({
            x.get("channel") for x in recent if x.get("channel")
        }),
        "persistent_no_buyer_reach":len(recent)>=4 and misses==len(recent),
    }

def scorecard(report):
    contacts=sum(
        action.get("result")=="sent_invited_proposal"
        for action in report.get("actions",[])
    )
    return {
        "research_attempts":len(report.get("hypotheses",[])),
        "externally_sent_verified_invitations":contacts,
        "buyer_replies_verified":0,
        "paid_orders_verified":None,
        "revenue_collected_usd_verified":None,
        "economic_outcome_this_run":contacts>0,
    }
