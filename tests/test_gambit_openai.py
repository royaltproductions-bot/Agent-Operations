"""Offline tests for Gambit's optional OpenAI inference provider."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from tools import gambit_openai
from tools import gambit_watch
from tools import gambit_worker as gambit


class GambitOpenAITests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        slot = patch.object(gambit_openai, "BUDGET_PATH",
                            Path(folder.name) / "openai_budget.json")
        slot.start()
        self.addCleanup(slot.stop)

    def test_all_gambit_workflow_stages_use_sol_by_default(self):
        with patch.dict(os.environ, {
            "GAMBIT_OPENAI_DECISION_MODEL": "gpt-6.1-sol",
            "GAMBIT_OPENAI_TRIAGE_MODEL": "gpt-6.1-sol",
            "GAMBIT_OPENAI_WRITING_MODEL": "gpt-6.1-sol",
            "GAMBIT_OPENAI_MODEL": "gpt-6.1-sol",
        }):
            for schema in (gambit.DECIDE_SCHEMA, gambit.WORK_SCHEMA,
                           gambit.REVIEW_SCHEMA, gambit.BID_NOTE_SCHEMA,
                           gambit.RESEARCH_PLAN_SCHEMA, gambit.REPLY_SCHEMA,
                           gambit.SITE_SPEC_SCHEMA):
                with self.subTest(properties=set(schema["properties"])):
                    self.assertEqual(gambit_openai.choose_model(schema, fast=True),
                                     "gpt-6.1-sol")
                    self.assertEqual(gambit_openai.choose_model(schema, fast=False),
                                     "gpt-6.1-sol")

    def test_budget_reserves_before_calls_and_blocks_monthly_overage(self):
        with patch.dict(os.environ, {"GAMBIT_OPENAI_MONTHLY_LIMIT_USD": "0.002"}):
            first = gambit_openai.reserve_monthly_budget(0.0012)
            self.assertEqual(first["call_count"], 1)
            self.assertEqual(first["reserved_micro_usd"], 1200)
            with self.assertRaisesRegex(RuntimeError, "openai_monthly_budget_exhausted"):
                gambit_openai.reserve_monthly_budget(0.0012)
            persisted = json.loads(gambit_openai.BUDGET_PATH.read_text())
            self.assertEqual(persisted["call_count"], 1)
            self.assertEqual(persisted["reserved_micro_usd"], 1200)
            self.assertNotIn("OPENAI_API_KEY", str(persisted))

    def test_budget_rejects_limit_above_twenty_and_resets_each_utc_month(self):
        with patch.dict(os.environ, {"GAMBIT_OPENAI_MONTHLY_LIMIT_USD": "21"}):
            with self.assertRaisesRegex(ValueError, "openai_monthly_budget_invalid"):
                gambit_openai.reserve_monthly_budget(0.01)
        gambit_openai.BUDGET_PATH.write_text(
            json.dumps({"month":"2020-02", "reserved_micro_usd":19000000,
                        "call_count":500}))
        with patch.dict(os.environ, {"GAMBIT_OPENAI_MONTHLY_LIMIT_USD": "20.00"}):
            updated = gambit_openai.reserve_monthly_budget(0.005)
        self.assertEqual(updated["call_count"], 1)
        self.assertEqual(updated["reserved_micro_usd"], 5000)

    def test_free_market_checks_cannot_reserve_openai_budget(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "",
                                     "GAMBIT_INFERENCE_PROVIDER": "auto"}):
            self.assertEqual(gambit_openai.provider(), "ollama")
            self.assertFalse(gambit_openai.BUDGET_PATH.exists())

    def test_default_remains_free_without_api_credential(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "",
                                     "GAMBIT_INFERENCE_PROVIDER": "auto"}):
            self.assertEqual(gambit_openai.provider(), "ollama")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-test-token",
                                     "GAMBIT_INFERENCE_PROVIDER": "auto"}):
            self.assertEqual(gambit_openai.provider(), "openai")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "",
                                     "GAMBIT_INFERENCE_PROVIDER": "openai"}):
            with self.assertRaisesRegex(RuntimeError, "openai_key_not_configured"):
                gambit_openai.provider()

    def test_older_gpt_models_are_selectable_with_verified_price_bounds(self):
        for model, expected in {
            "gpt-5.4-mini": (0.75, 4.50),
            "gpt-5.4": (2.50, 15.00),
            "gpt-5.5": (5.00, 30.00),
            "gpt-5.6-terra": (2.00, 12.00),
            "gpt-5.6-sol": (5.00, 30.00),
        }.items():
            with self.subTest(model=model):
                self.assertEqual(gambit_openai._MODEL_PRICES[model], expected)
                cost = gambit_openai.estimate_max_cost_usd(
                    "Research and produce a proposal", {"buyer": "demo"},
                    gambit.REVIEW_SCHEMA, 500, model)
                self.assertGreater(cost, 0)

    def test_legacy_model_configuration_does_not_override_prepaid_key_gate(self):
        env = {"GAMBIT_INFERENCE_PROVIDER": "auto",
               "GAMBIT_OPENAI_DECISION_MODEL": "gpt-5.4",
               "GAMBIT_OPENAI_WRITING_MODEL": "gpt-5.4-mini",
               "OPENAI_API_KEY": ""}
        with patch.dict(os.environ, env):
            self.assertEqual(gambit_openai.provider(), "ollama")
            self.assertEqual(gambit_openai.choose_model(gambit.DECIDE_SCHEMA),
                             "gpt-5.4")
            self.assertEqual(gambit_openai.choose_model(gambit.BID_NOTE_SCHEMA),
                             "gpt-5.4-mini")

    def test_only_explicit_supported_providers_are_allowed(self):
        with patch.dict(os.environ, {"GAMBIT_INFERENCE_PROVIDER": "invalid"},
                        clear=False):
            with self.assertRaisesRegex(ValueError, "unsupported_inference_provider"):
                gambit_openai.provider()

    def test_strict_schema_handles_optional_fields_and_nested_array_objects(self):
        schema = gambit_openai.strict_schema(gambit.SITE_SPEC_SCHEMA)
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("pages", schema["required"])
        self.assertEqual(schema["properties"]["pages"]["type"], ["array", "null"])
        self.assertNotIn("maxItems", schema["properties"]["pages"])
        nested = schema["properties"]["pages"]["items"]
        self.assertFalse(nested["additionalProperties"])
        self.assertEqual(nested["required"], ["slug", "nav_label", "headline", "intro", "sections"])

    def test_openai_json_schema_call_and_no_local_model_install(self):
        response = io.BytesIO(json.dumps({
            "choices": [{"finish_reason": "stop", "message": {
                "content": '{"decision":"pass","reason":"Unsupported","note":"","question":"","payout_cents":0,"sample_source_url":null}'
            }}]
        }).encode())
        env = {"GAMBIT_INFERENCE_PROVIDER": "openai",
               "OPENAI_API_KEY": "local-test-token",
               "GAMBIT_OPENAI_DECISION_MODEL": "gpt-6.1-sol",
               "GAMBIT_OPENAI_MAX_ESTIMATED_CALL_USD": "0.08"}
        with patch.dict(os.environ, env), \
             patch.object(gambit_openai.urllib.request, "urlopen", return_value=response) as http, \
             patch.object(gambit_watch, "command") as command:
            gambit_watch.prepare_models()
            answer = gambit.infer(gambit.DECIDE_SCHEMA, "Assess buyer work",
                                  {"title": "A limited summary"}, fast=True)
        command.assert_not_called()
        self.assertEqual(answer["decision"], "pass")
        req = http.call_args.args[0]
        self.assertEqual(req.full_url, gambit_openai.ENDPOINT)
        self.assertIn("Bearer local-test-token", req.get_header("Authorization"))
        payload = json.loads(req.data)
        self.assertEqual(payload["model"], "gpt-6.1-sol")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["response_format"]["json_schema"]["schema"]["required"],
                         list(gambit.DECIDE_SCHEMA["properties"]))
        self.assertEqual(payload["reasoning_effort"], "medium")

    def test_empty_key_cannot_make_paid_call(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
             patch.object(gambit_openai.urllib.request, "urlopen") as http:
            with self.assertRaisesRegex(RuntimeError, "openai_key_not_configured"):
                gambit_openai.infer(gambit.REVIEW_SCHEMA, "Return JSON", {})
        http.assert_not_called()

    def test_cost_ceiling_prevents_oversized_request_before_network(self):
        env = {"OPENAI_API_KEY": "local-test-token",
               "GAMBIT_OPENAI_MAX_ESTIMATED_CALL_USD": "0.001"}
        with patch.dict(os.environ, env), \
             patch.object(gambit_openai.urllib.request, "urlopen") as http:
            with self.assertRaisesRegex(RuntimeError, "openai_call_cost_ceiling_exceeded"):
                gambit_openai.infer(gambit.DECIDE_SCHEMA, "Assess", {"text": "x" * 8000},
                                    num_predict=1000)
        http.assert_not_called()

    def test_refusal_and_truncated_outputs_fail_closed(self):
        variants = [
            {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"refusal": "No"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]},
        ]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "local-test-token"}):
            for bad in variants:
                with self.subTest(bad=bad), \
                     patch.object(gambit_openai.urllib.request, "urlopen",
                                  return_value=io.BytesIO(json.dumps(bad).encode())):
                    with self.assertRaises(RuntimeError):
                        gambit_openai.infer(gambit.REVIEW_SCHEMA, "Return JSON", {})

    def test_transport_error_never_logs_secret_or_buyer_context(self):
        error = urllib.error.HTTPError(gambit_openai.ENDPOINT, 429,
                                      "Rate limit", {}, io.BytesIO(b"hidden secret"))
        env = {"OPENAI_API_KEY": "local-test-token", "GAMBIT_INFERENCE_PROVIDER": "openai"}
        with patch.dict(os.environ, env), \
             patch.object(gambit_openai.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "^openai_http_429$") as caught:
                gambit.infer(gambit.REVIEW_SCHEMA, "Buyer confidential", {"secret": "private"})
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIn("local-test-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
