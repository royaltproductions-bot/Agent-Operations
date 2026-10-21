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


    def test_commercial_outcomes_do_not_trigger_code_refinement(self):
        state={
            "structural_failure_counts":{},
            "commercial_outcome_counts":{
                "no_matching_public_demand":4,
                "model_rejected_candidates":3,
            },
        }
        self.assertIsNone(r.diagnose(state))

    def test_structural_capability_gap_can_trigger_refinement(self):
        state={
            "structural_failure_counts":{
                "distribution_connector_not_yet_available":2,
            },
            "commercial_outcome_counts":{},
        }
        diagnosis=r.diagnose(state)
        self.assertEqual(diagnosis["target"],"tools/connected_commerce_tools.py")

if __name__=="__main__":
    unittest.main()
