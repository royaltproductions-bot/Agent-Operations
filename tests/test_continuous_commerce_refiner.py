import unittest
from tools import continuous_commerce_refiner as r

class ContinuousCommerceRefinerTests(unittest.TestCase):
    def test_safe_paths_allow_engine_files_and_new_modules(self):
        self.assertTrue(r.safe_path("tools/new_capability.py"))
        self.assertTrue(r.safe_path("tests/test_new_capability.py"))
        self.assertTrue(r.safe_path("storefront/index.html"))

    def test_safe_paths_block_workflows_and_traversal(self):
        self.assertFalse(r.safe_path(".github/workflows/change.yml"))
        self.assertFalse(r.safe_path("../outside.py"))
        self.assertFalse(r.safe_path("/tmp/escape.py"))
        self.assertFalse(r.safe_path("ops/fake-revenue.json"))

    def test_guard_rejects_embedded_live_secret_shape(self):
        prefix="sk"+"_live_"
        with self.assertRaises(ValueError):
            r.guard_content("tools/x.py", 'TOKEN="'+prefix+'abcdefghijklmnopqrstuvwxyz"')

    def test_guard_accepts_normal_general_code(self):
        r.guard_content("tools/x.py", "def useful():\n    return 1\n")


    def test_single_run_commercial_misses_do_not_trigger_code_refinement(self):
        state={
            "structural_failure_counts":{},
            "commercial_outcome_counts":{"no_matching_public_demand":4},
            "commercial_run_counts":{"no_matching_public_demand":1},
            "commercial_streaks":{"no_matching_public_demand":1},
        }
        self.assertIsNone(r.diagnose(state))

    def test_persistent_cross_run_commercial_failure_can_trigger_refinement(self):
        state={
            "structural_failure_counts":{},
            "commercial_outcome_counts":{"no_matching_public_demand":9},
            "commercial_run_counts":{"no_matching_public_demand":3},
            "commercial_streaks":{"no_matching_public_demand":3},
        }
        diagnosis=r.diagnose(state)
        self.assertEqual(diagnosis["trigger_kind"],"persistent_commercial_pattern")
        self.assertEqual(diagnosis["target"],"tools/autonomous_commerce_brain.py")

    def test_commercial_failure_without_consecutive_persistence_does_not_refine(self):
        state={
            "structural_failure_counts":{},
            "commercial_outcome_counts":{"model_rejected_candidates":8},
            "commercial_run_counts":{"model_rejected_candidates":4},
            "commercial_streaks":{"model_rejected_candidates":1},
        }
        self.assertIsNone(r.diagnose(state))


    def test_recent_commercial_refinement_gets_cooldown_before_same_pattern_rewrites_again(self):
        state={
            "structural_failure_counts":{},
            "commercial_outcome_counts":{"no_matching_public_demand":12},
            "commercial_run_counts":{"no_matching_public_demand":4},
            "commercial_streaks":{"no_matching_public_demand":4},
            "recently_refined_commercial_patterns":["no_matching_public_demand"],
        }
        self.assertIsNone(r.diagnose(state))

    def test_structural_capability_gap_can_trigger_refinement(self):
        state={
            "structural_failure_counts":{
                "distribution_connector_not_yet_available":2,
            },
            "commercial_outcome_counts":{},
            "commercial_run_counts":{},
            "commercial_streaks":{},
        }
        diagnosis=r.diagnose(state)
        self.assertEqual(diagnosis["trigger_kind"],"structural_execution_failure")
        self.assertEqual(diagnosis["target"],"tools/connected_commerce_tools.py")

if __name__=="__main__":
    unittest.main()
