"""Offline tests for Gambit's optional OpenAI inference provider."""
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from tools import gambit_openai
from tools import gambit_watch
from tools import gambit_worker as gambit


class GambitOpenAITests(unittest.TestCase):
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
               "GAMBIT_OPENAI_TRIAGE_MODEL": "gpt-6.1-terra",
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
        self.assertEqual(payload["model"], "gpt-6.1-terra")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["response_format"]["json_schema"]["schema"]["required"],
                         list(gambit.DECIDE_SCHEMA["properties"]))
        self.assertEqual(payload["reasoning_effort"], "low")

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
