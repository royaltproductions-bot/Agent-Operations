"""Regression tests for bounded Cyclops execution."""
import time
import unittest
from unittest.mock import patch

from tools import cyclops_asset_engine as cyclops


class CyclopsRunBudgetTests(unittest.TestCase):
    def tearDown(self):
        cyclops.RUN_DEADLINE = None

    def test_model_timeout_never_runs_past_receipt_reserve(self):
        cyclops.RUN_DEADLINE = time.monotonic() + 100
        with patch.object(cyclops, '_model_timeout_seconds', return_value=180):
            timeout = cyclops.model_call_timeout_seconds()
        self.assertGreaterEqual(timeout, 5)
        self.assertLessEqual(timeout, 10)

    def test_normal_model_timeout_remains_configured_when_budget_is_plentiful(self):
        cyclops.RUN_DEADLINE = time.monotonic() + 900
        with patch.object(cyclops, '_model_timeout_seconds', return_value=180):
            self.assertEqual(cyclops.model_call_timeout_seconds(), 180)


if __name__ == '__main__':
    unittest.main()
