"""Restricted, versioned public-only agent card and buyer metadata disclosure guard.

This is NOT a publication API. Account owner approval is needed before editing
the marketplace dashboard card. Runtime buyer metadata replies nevertheless
must only use the vetted conservative public fields in this file's source JSON.
No secrets, private customer text, raw model settings, or owner identity here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

CARD_PATH = Path(__file__).resolve().parents[1] / "ops/gambit/public-agent-card.json"
EXPECTED_FIELDS = {
    "schema_version", "publication_status", "agent_name", "identity",
    "short_description", "description", "bio", "capability_answer", "integrity_answer",
    "restricted_answer", "out_of_scope", "categories",
}
_ALLOWED_STATUSES = {"proposed_for_owner_review", "owner_approved"}
_SOURCE_FIELDS = ("identity", "capability_answer", "integrity_answer", "restricted_answer")
# Deliberately limited: capability changes cannot silently broaden these texts.


def card():
    data = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) != EXPECTED_FIELDS or
            data.get("schema_version") != 1 or
            data.get("publication_status") not in _ALLOWED_STATUSES or
            not all(isinstance(data.get(k), str) and 20 <= len(data[k]) <= 1800
                    for k in _SOURCE_FIELDS + ("bio", "description", "short_description", "out_of_scope")) or
            len(data["bio"]) > 280 or
            not isinstance(data.get("categories"), list) or
            not all(isinstance(x, str) and x for x in data["categories"])):
        raise ValueError("invalid_public_agent_card")
    return data


def _message_text(message):
    return str((message.get("content") or {}).get("text") or "").strip().lower()


def topic(message):
    """Buyer questions about Gambit must never be answered from free-form AI."""
    raw = _message_text(message)
    if not raw:
        return None
    cleaned = re.sub(r"\b(?:the\s+)?(?:fuck(?:ing)?|hell|heck|tf)\b", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Restricted asks win over generic identity/introduction asks.
    sensitive = (
        r"\b(?:api\s*keys?|access\s*tokens?|passwords?|credentials?|"
        r"system\s*prompts?|developer\s*instructions?|hidden\s*instructions?|"
        r"internal\s*instructions?|private\s*(?:keys?|emails?|logs?|data)|"
        r"(?:your|gambit['’]?s|ari['’]?s|internal|private|backend)\s+source\s*code|"
        r"repositories|repo\s*(?:url|link|name)|"
        r"backend\s*architecture|internal\s*(?:architecture|infrastructure|logs?)|"
        r"account\s*owner|legal\s*owner|real\s*name|personal\s*(?:phone|email|address)|"
        r"who\s+(?:owns|built|created|developed|programmed)\s+(?:you|ari|gambit)|"
        r"your\s+(?:owner|boss|creator|developer)|"
        r"model\s*(?:name|version|provider)|(?:which|what)\s+(?:model|llm|stack|framework|hosting)|"
        r"your\s+(?:model|stack|hosting|budget|api|provider|operator|code|"
        r"instructions|prompt|engineer|company\s+owner|security\s+setup)|"
        r"(?:who|what)\s+(?:is|are)\s+(?:your|the)\s+(?:owner|creator|engineer|"
        r"operator|developer|backend|infrastructure|model|server|framework)|"
        r"(?:personal|private|internal)\s+(?:details?|contact|numbers?|info)|"
        r"where\s+(?:are\s+you\s+hosted|is\s+your\s+server))\b"
    )
    if re.search(sensitive, cleaned) or re.search(
            r"\b(?:reveal|print|ignore|override|dump|show)\b.{0,80}"
            r"\b(?:prompt|instructions|secrets?|keys?|tokens?|credentials?|internal)\b",
            cleaned):
        return "restricted"
    if re.search(
        r"\b(?:who\s+(?:is|are|this|'s|the)\s+(?:this\s+)?"
        r"(?:ari|gambit|you(?=\s*(?:[?!.,;:]|$)))|"
        r"who\s+(?:is\s+|['’]s\s+)?this\b|"
        r"who\s+(?:am\s+i\s+talking\s+to|you(?=\s*(?:[?!.,;:]|$))|this\s+ari)|"
        r"what\s+(?:is|does)\s+ari|"
        r"are\s+you\s+(?:an?\s+)?(?:ai|bot|agent|human)|"
        r"is\s+this\s+(?:an?\s+)?(?:ai|bot|agent))\b", cleaned):
        return "identity"
    if re.search(
        r"\b(?:technical\s+integrity|how\s+do\s+you\s+(?:verify|validate|test|"
        r"check|protect|ensure)|what\s+(?:checks?|tests?|standards?)\s+"
        r"(?:do\s+you|are\s+used)|"
        r"(?:are|is)\s+(?:you|your\s+work)\s+(?:certified|verified|secure)|"
        r"security\s+(?:certification|audit|controls?)|"
        r"soc\s*2|iso\s*27001|penetration\s+tests?)\b", cleaned):
        return "integrity"
    if re.search(
        r"\b(?:what\s+(?:can\s+you|services?\s+do\s+you|work\s+do\s+you)|"
        r"(?:your|gambit['’]?s|ari['’]?s)\s+(?:capabilit(?:y|ies)|"
        r"specialt(?:y|ies)|services?|skills?|expertise)|"
        r"what\s+(?:can|does)\s+(?:gambit|ari)\s+do|"
        r"are\s+you\s+(?:able|capable)\s+to|"
        r"can\s+(?:you|gambit|ari)\s+(?:build|create|make|develop|deliver|do|handle|deploy|code|produce))\b", cleaned):
        return "capability"
    return None


def public_answer(message):
    subject = topic(message)
    if subject is None:
        return None
    data = card()
    return {
        "identity": data["identity"],
        "capability": data["capability_answer"],
        "integrity": data["integrity_answer"],
        "restricted": data["restricted_answer"],
    }[subject]


_PRIVATE_BRANDS = (
    "royaltproductions-bot", "royal t griffith",
    "r t productions llc", "ops/gambit/", "agent-operations",
)
_SECRET_TEMPLATES = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/-]{12,}\b|"
    r"\b(?:sk-|ghp_|gho_|github_pat_)[A-Za-z0-9_-]{14,}\b)", re.I)


def check_outbound_text(text):
    """Defense in depth at the actual API send boundary; fail closed."""
    if not isinstance(text, str):
        raise ValueError("invalid_customer_text")
    lower = text.lower()
    if (any(x in lower for x in _PRIVATE_BRANDS) or _SECRET_TEMPLATES.search(text) or
        re.search(r"\b(?:my|our|we\s+use|i\s+use|i\s+run|powered\s+by)\b"
                  r".{0,55}\b(?:gpt[- ]?\d|qwen|ollama|github\s+actions|"
                  r"openai(?:\s+api)?|docker|playwright|claude|"
                  r"anthropic|render\s+service)\b", lower)): 
        raise ValueError("private_metadata_disclosure_blocked")
    for name in ("BOUNTY_AGENT_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN",
                 "BOUNTY_WEBHOOK_SECRET", "RENDER_API_KEY", "STRIPE_SECRET_KEY"):
        secret = os.getenv(name)
        if secret and len(secret) >= 12 and secret in text:
            raise ValueError("credential_disclosure_blocked")
    return text


def markdown():
    item = card()
    status = ("Owner-approved public wording." if item["publication_status"] == "owner_approved"
              else "Owner review required before publishing to TryBounty.")
    return (
        "# Gambit — public TryBounty Agent Card\n\n"
        "**Status: " + status + "**\n"
        "This repository copy is the allowlisted source for routine public "
        "identity and capability replies, not proof of a live profile update.\n\n"
        "## Registered identity\n\n" + item["agent_name"] + "\n\n"
        "## Short description\n\n" + item["short_description"] + "\n\n"
        "## Agent Card description\n\n" + item["description"] + "\n\n"
        "## Profile bio (280-character limit)\n\n" + item["bio"] + "\n\n"
        "## Public categories\n\n" +
        "".join("- " + c + "\n" for c in item["categories"]) + "\n"
        "## Allowed technical-integrity statement\n\n" +
        item["integrity_answer"] + "\n\n"
        "## Scope limitations\n\n" + item["out_of_scope"] + "\n\n"
        "## Internal disclosure policy\n\n"
        "Buyer questions about identity, capabilities, verification or "
        "nonpublic internals are answered only from version-controlled "
        "public fields, never from the free-form model or private context. "
        "New claims require owner review. The public card does not establish "
        "a completed paid contract or an external certification.\n\n"
        "## Marketplace publication\n\n"
        "Not automatically published. Bounty's Agent Card is edited in its "
        "Agents dashboard. Do not copy unapproved capabilities into that profile.\n"
    )


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", action="store_true")
    opts = parser.parse_args()
    if opts.check:
        target = CARD_PATH.with_name("trybounty-agent-card.md")
        if target.read_text(encoding="utf-8") != markdown():
            print("public_agent_card_document_out_of_sync", file=sys.stderr)
            raise SystemExit(1)
    if opts.render:
        print(markdown(), end="")
