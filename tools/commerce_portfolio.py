#!/usr/bin/env python3
"""Evidence-driven opportunity portfolio and cross-run stagnation safeguards.

The AI's PURPOSE is confirmed, collected money and authorized delivery, not model
tokens, output files, web searches or a convenient favorite product.
This is a commercial decision control layer, not another opportunity scanner.
"""
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
import re

LANES = {
    "commissioned_services": {
        "description": "Sell paid, bounded Python/JavaScript/CSV/reporting work to a real buyer already requesting a contractor",
        "proof": "Original explicit paid invitation; actual authorized contact; accepted brief; independently deliverable scope",
        "paths": ["hackernews", "reddit_forhire", "github"],
    },
    "existing_product_sales": {
        "description": "Validate and distribute an ALREADY deliverable owned product or service with an operational checkout",
        "proof": "Buyer demand and legitimate externally reachable distribution; Stripe settled payment, no imaginary traffic",
        "paths": ["hackernews", "reddit_forhire", "github"],
    },
    "digital_asset_licensing": {
        "description": "License genuine owned software, templates, educational assets or IP where buyer expresses demand",
        "proof": "Verified ownership and deliverability, buyer authorization and actual license/checkout; do not resell third-party assets",
        "paths": ["hackernews", "reddit_forhire", "github"],
    },
    "creator_commissions": {
        "description": "Locate actual paid briefs for owned original short-form video, editing, scripts, or UGC-style productions",
        "proof": "Public verified paid brief and achievable output; no synthetic human testimonials; lawful disclosure",
        "paths": ["reddit_forhire", "hackernews", "github"],
    },
    "paid_software_tasks": {
        "description": "Evaluate real open technical tasks with externally verified posted compensation, no unfunded bounty guesses",
        "proof": "Original live issue, actual funded/contract authorization, feasible deliverable and confirmed funds",
        "paths": ["github", "reddit_forhire", "hackernews"],
    },
    "distribution_partnerships": {
        "description": "Find businesses openly requesting revenue-share/referral/distribution partners using only existing permitted accounts",
        "proof": "Verifiable current terms, authorized contact, no new accounts/deposits/spend or invented commissions",
        "paths": ["reddit_forhire", "hackernews", "github"],
    },
    "conversion_and_fulfillment": {
        "description": "Work actual inbound enquiries, maintain checkout and fulfill legitimately paid transactions; troubleshoot leakage",
        "proof": "Confirmed buyer message, independently confirmed Stripe order, delivery receipt or real conversion measurement",
        "paths": ["hackernews", "reddit_forhire", "github"],
    },
}
CHANNELS = ("hackernews", "reddit_forhire", "github")
POSITIVE = {"sent_invited_proposal","paid_order_fulfilled","confirmed_buyer_response",
            "verified_paid_order","verified_sale","accepted_project",
            "publish_requested_not_live_verified"}
NO_ECONOMIC_OUTCOME = {
    "no_matching_public_demand", "model_rejected_candidates",
    "model_candidate_evaluation_invalid","duplicate_or_unusable_search",
    "model_strategy_invalid","invitation_or_capability_not_verified",
    "model_selected_unlisted_candidate", "original_source_invalid",
    "search_source_unavailable", "source_access_blocked",
}
STOPWORDS = set("""a an and are as at be but by can do for from have how i in into is it
its of on or our out paid please real seeking service small software the their them
there this to tool using via we with work would you your custom actual buyer specific
publicly seeking business owner low cost automate automation python javascript code
project projects company companies offering""".split())
SYNONYMS = {
    "bakery":"food_inventory", "bakeries":"food_inventory",
    "bread":"food_inventory", "coffee":"food_inventory", "cafe":"food_inventory",
    "pastry":"food_inventory", "ingredient":"food_inventory",
    "spoilage":"food_inventory", "stock":"inventory", "inventory":"inventory",
    "shop":"small_business", "store":"small_business", "shops":"small_business",
    "tracking":"tracking", "tracker":"tracking", "track":"tracking",
    "spreadsheet":"spreadsheet", "sheets":"spreadsheet",
    "csv":"spreadsheet", "csvs":"spreadsheet",
    "tiktok":"video", "shorts":"video", "reels":"video",
    "editing":"video", "video":"video",
    "payouts":"settlement", "settlement":"settlement", "vendors":"settlement"
}
HYPOTHESIS_STOPWORDS = STOPWORDS | {
    "portland","or","ga","ny","usa","city","local","daily","simple","online",
    "subscription","one","time","free","micro","system","alerts","alert",
    "create","build","develop","tool","full","existing","new","direct",
    "market","use","users","want","wants","needs","need","getting","get",
    "community","remote","low","fee","dollar",
}

def normalized_tokens(value):
    raw=re.findall(r"[a-z0-9]+",str(value or "").lower())
    tokens=[]
    for x in raw[:140]:
        if x in HYPOTHESIS_STOPWORDS or len(x)<3:continue
        val=SYNONYMS.get(x,x)
        if val not in tokens:tokens.append(val)
    return set(tokens)

def overlap(a,b):
    a,b=normalized_tokens(a),normalized_tokens(b)
    if len(a)<2 or len(b)<2:return 0.0
    # Overlap coefficient catches reworded versions of the same narrow idea.
    return len(a & b)/min(len(a),len(b))

def prior_experiments(history):
    rows=[]
    for receipt in history:
        for x in receipt.get("hypotheses",[]):
            if isinstance(x,dict):
                rows.append({
                    "lane":x.get("lane") or "commissioned_services",
                    "channel":x.get("channel"),
                    "buyer":x.get("buyer",""),
                    "problem":x.get("problem",""),
                    "offer":x.get("business_model",""),
                    "query":x.get("query",""),
                    "reached_buyer":bool(receipt.get("qualified_external_contacts")),
                })
    return rows

def lane_scores(history, freshness_window=36):
    """Avoid tunnel vision while preserving a genuinely productive lane."""
    recent=prior_experiments(history)[-freshness_window:]
    scores={}
    for name in LANES:
        n=sum(x["lane"]==name for x in recent)
        wins=sum(x["lane"]==name and x["reached_buyer"] for x in recent)
        # Bounded exploitation: even a successful lane must not monopolize runs.
        scores[name]=n*4-min(wins,2)*3
    return scores

def choose_lane(history, attempted=(), preferred=None):
    scores=lane_scores(history)
    for name in attempted:
        if name in scores:scores[name]+=10
    lowest=min(scores.values())
    options=[name for name in LANES if scores[name]==lowest]
    if preferred in options:return preferred
    # Stable ordering rotates naturally as receipts accumulate.
    return options[0]

def choose_channel(history,lane,attempted=()):
    order=LANES[lane]["paths"]
    counts=Counter(x.get("channel") for x in prior_experiments(history)
                   if x.get("lane")==lane)
    for channel in attempted:
        counts[channel]+=5
    return min(order,key=lambda c:(counts[c],order.index(c)))

def reject_repetition(proposal,history,current=()):
    """Reject both exact and reworded dead-end experiments across runs."""
    query=proposal.get("search_query","")
    buyer=proposal.get("buyer","")
    problem=proposal.get("customer_problem","")
    combined=" ".join((buyer,problem,proposal.get("offer","")))
    if len(query.strip())<4:
        return "empty_or_non_searchable_hypothesis"
    # Demographic inventions are not factual lead evidence, and must not drive products.
    if re.search(r"\b(?:Portland|Seattle|Atlanta|Chicago|New York)\b",buyer,re.I) and \
       re.search(r"\b(?:seeking|wants|needs|looking for)\b",buyer,re.I):
        return "invented_localized_buyer_disallowed"
    for old in list(prior_experiments(history))[-45:]+list(current):
        old_query=old.get("query","")
        old_combined=" ".join((old.get("buyer",""),old.get("problem",""),old.get("offer","")))
        if query.strip().lower()==old_query.strip().lower():
            return "repeat_exact_search"
        if proposal.get("search_channel")==old.get("channel") and overlap(query,old_query)>=0.7:
            return "repeat_rephrased_search"
        if (overlap(combined,old_combined)>=0.6
            and not old.get("reached_buyer") and old.get("lane")==proposal.get("lane")):
            return "recycled_unvalidated_market"
    return None

def diagnostics(history,current=()):
    recent=(prior_experiments(history)[-20:]+list(current))[-20:]
    failures=sum(not x.get("reached_buyer") for x in recent)
    channels=len(set(x.get("channel") for x in recent if x.get("channel")))
    lanes=len(set(x.get("lane") for x in recent if x.get("lane")))
    return {
        "recent_experiments":len(recent),
        "recent_unvalidated_experiments":failures,
        "unique_lanes":lanes,
        "unique_demand_channels":channels,
        "research_loop_alert":len(recent)>=4 and failures==len(recent),
        "instruction":("STOP building speculative products; force different "
           "buyer route and an authorized real contact" if len(recent)>=4
           and failures==len(recent) else "Prioritize buyer evidence and paid execution"),
    }

def scorecard(report):
    """Separate market activities from verified money and actual external contact."""
    contacts=sum(x.get("result")=="sent_invited_proposal"
                 for x in report.get("actions",[]))
    queries=len(report.get("hypotheses",[]))
    return {
        "research_attempts":queries,
        "externally_sent_verified_invitations":contacts,
        "buyer_replies_verified":0,
        "paid_orders_verified":None,
        "revenue_collected_usd_verified":None,
        "paid_deliverables_sent_verified":0,
        "distribution_exposure_verified":None,
        "economic_outcome_this_run":contacts>0,
        "note":"An invitation sent is external outreach, not acceptance, conversion, a customer, or money."
    }
