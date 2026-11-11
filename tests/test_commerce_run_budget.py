"""The commerce runner must yield before its hard GitHub Actions timeout."""
import time
import unittest
from unittest.mock import patch
from tools import autonomous_commerce_brain as commerce
from tools import open_web_discovery as discovery

class CommerceBudgetTests(unittest.TestCase):
    def test_model_is_not_called_after_execution_budget(self):
        with patch.object(commerce, "RUN_DEADLINE", time.monotonic()+5):
            with patch.object(commerce, "_model_json_once") as model:
                with self.assertRaisesRegex(TimeoutError, "commerce_execution_budget_deferred"):
                    commerce.model_json({"type":"object"}, "system", {})
                model.assert_not_called()

    def test_web_discovery_stops_without_expensive_search(self):
        with patch.object(discovery, "duckduckgo_html") as provider:
            rows, errors=discovery.open_web_candidates(
                queries=["paid website developer needed"],
                include_discussions=False, include_paid_issues=False,
                deadline_monotonic=time.monotonic()-1,
            )
            self.assertEqual(rows, [])
            provider.assert_not_called()
            self.assertTrue(any(e.get("reason")=="discovery_deadline_reached" for e in errors))

    def test_default_budget_is_backward_compatible(self):
        with patch.object(commerce, "RUN_DEADLINE", None):
            self.assertGreaterEqual(commerce.remaining_run_seconds(), 3600)
            self.assertEqual(commerce._model_timeout_seconds(), 180)

if __name__=="__main__":
    unittest.main()
