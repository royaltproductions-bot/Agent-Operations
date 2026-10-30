"""Optional OpenAI inference for Gambit. No billable calls without explicit opt-in.

Requires OPENAI_API_KEY and GAMBIT_INFERENCE_PROVIDER=auto or openai.
No prompts, buyer data, keys, or responses are logged.
"""
from __future__ import annotations

import copy
import json
import math
import os
import urllib.error
import urllib.request

ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-6.1-sol"
# OpenAI standard short-context rates (USD per million tokens, October 2026).
# If selecting another model, set GAMBIT_OPENAI_INPUT_USD_PER_MTOK and
# GAMBIT_OPENAI_OUTPUT_USD_PER_MTOK for conservative budgeting.
_MODEL_PRICES = {
    "gpt-6.1-terra": (1.0, 5.0),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-sol": (2.0, 10.0),
    # Legacy OpenAI models still offered through the API.
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-mini-2026-03-17": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.4-2026-03-05": (2.50, 15.00),
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.5-2026-04-23": (5.00, 30.00),
    "gpt-5.6-terra": (2.00, 12.00),
    # Preflight uses non-promotional prices as a safety buffer because
    # GPT-5.6 Sol's $4/$20 promotional rates may change after Nov 21, 2026.
    "gpt-5.6-sol": (5.00, 30.00),
}


def available():
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def provider():
    setting = os.getenv("GAMBIT_INFERENCE_PROVIDER", "ollama").strip().lower()
    if setting not in {"ollama", "openai", "auto"}:
        raise ValueError("unsupported_inference_provider")
    if setting == "auto":
        # Connecting a key is the explicit opt-in; absent means existing free
        # local inference continues with zero external API spending.
        return "openai" if available() else "ollama"
    if setting == "openai" and not available():
        raise RuntimeError("openai_key_not_configured")
    return setting



def choose_model(schema, fast=False):
    """Spend inference budget in proportion to commercial decision importance.

    Never let a tiny screening model write binding proposals or judge whether
    a bounty's full implementation is feasible.
    """
    properties = set((schema.get("properties") or {}).keys())
    if {"decision", "payout_cents"}.issubset(properties) or "approved" in properties:
        return os.getenv("GAMBIT_OPENAI_DECISION_MODEL", "gpt-6.1-sol").strip()
    if properties == {"note"} or "reply_text" in properties:
        return os.getenv("GAMBIT_OPENAI_WRITING_MODEL", "gpt-6.1-terra").strip()
    if "requires_web" in properties or fast:
        return os.getenv("GAMBIT_OPENAI_TRIAGE_MODEL", "gpt-6-luna").strip()
    return os.getenv("GAMBIT_OPENAI_MODEL", DEFAULT_MODEL).strip()


def strict_schema(schema):
    """Normalize existing schemas to OpenAI's strict Structured Outputs subset."""
    def convert(node):
        if not isinstance(node, dict):
            return node
        result = {k: copy.deepcopy(v) for k, v in node.items()
                  if k not in {"minItems", "maxItems", "minLength", "maxLength"}}
        if node.get("type") == "object":
            properties = node.get("properties") or {}
            original_required = set(node.get("required") or [])
            result["properties"] = {}
            for name, value in properties.items():
                part = convert(value)
                if name not in original_required:
                    # All strict properties must be required; preserve optionality
                    # through a nullable union instead of inventing a default.
                    part["type"] = [part["type"], "null"] if isinstance(
                        part.get("type"), str) else part.get("type")
                result["properties"][name] = part
            result["required"] = list(properties)
            result["additionalProperties"] = False
        elif node.get("type") == "array":
            result["items"] = convert(node.get("items") or {})
        return result
    if schema.get("type") != "object":
        raise ValueError("openai_schema_must_be_object")
    return convert(schema)


def estimate_max_cost_usd(system, context, schema, max_output_tokens, model):
    """Conservative preflight estimate, NOT actual usage or an account spend cap."""
    input_price, output_price = _MODEL_PRICES.get(model, (None, None))
    if input_price is None:
        # A custom model requires its own explicitly supplied rates.
        input_price = float(os.environ["GAMBIT_OPENAI_INPUT_USD_PER_MTOK"])
        output_price = float(os.environ["GAMBIT_OPENAI_OUTPUT_USD_PER_MTOK"])
    encoded = json.dumps({"system": system, "context": context, "schema": schema},
                         ensure_ascii=False)
    # Use ~2 characters/token (intentionally cautious) plus modest framing.
    input_estimate = math.ceil(len(encoded) / 2) + 1500
    return (input_estimate * input_price +
            max_output_tokens * output_price) / 1_000_000


def infer(schema, system, context, *, num_predict=900, num_ctx=5500, fast=False):
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("openai_key_not_configured")
    model = choose_model(schema, fast=fast)
    if model not in _MODEL_PRICES:
        raise ValueError("openai_model_not_verified_for_gambit")
    normalized = strict_schema(schema)
    # Reasoning tokens count towards the maximum; grant some headroom for
    # nontrivial reasoning without modifying the public output contract.
    max_completion = max(800, num_predict + min(1400, num_predict))
    bound = estimate_max_cost_usd(system, context, normalized, max_completion, model)
    limit = float(os.getenv("GAMBIT_OPENAI_MAX_ESTIMATED_CALL_USD", "0.08"))
    if not (0 < limit <= 1.00):
        raise ValueError("openai_cost_limit_invalid")
    if bound > limit:
        raise RuntimeError("openai_call_cost_ceiling_exceeded")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system + " Respond as JSON matching the schema."},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "gambit_output", "strict": True, "schema": normalized}},
        "max_completion_tokens": max_completion,
        "reasoning_effort": os.getenv(
            "GAMBIT_OPENAI_REASONING_EFFORT",
            "medium" if model == "gpt-6.1-sol" else "low"),
    }
    request = urllib.request.Request(
        ENDPOINT, method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90 if fast else 150) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do NOT log the body; provider error bodies may contain user data.
        raise RuntimeError("openai_http_" + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("openai_network_unavailable") from None
    choices = data.get("choices") or []
    if not choices or choices[0].get("finish_reason") != "stop":
        raise RuntimeError("openai_incomplete_output")
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise RuntimeError("openai_refusal")
    try:
        value = json.loads(message.get("content") or "")
    except (ValueError, TypeError):
        raise RuntimeError("openai_invalid_json") from None
    if not isinstance(value, dict):
        raise RuntimeError("openai_non_object_output")
    # No unverified fallback model, no silently generated bids on API failures.
    return value
