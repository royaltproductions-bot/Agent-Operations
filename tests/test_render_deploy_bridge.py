"""The former Render watchdog queue is retired: direct API compatibility tests."""
import unittest
from unittest.mock import patch
from tools import render_deploy_bridge as legacy

class NoChatGPTDependencyTests(unittest.TestCase):
    def test_compatibility_invokes_real_api_deployment_function(self):
        with patch("tools.render_direct_api.deploy_site_if_needed",
                   return_value={"status":"already_live_verified"}):
            # legacy imported by name; monkeypatch the reference at the wrapper.
            with patch.object(legacy,"deploy_site_if_needed",
                              return_value={"status":"already_live_verified"}) as direct:
                result=legacy.request_render_deployment()
        self.assertEqual(result["status"],"already_live_verified")
        direct.assert_called_once_with()

    def test_no_queue_is_necessary(self):
        self.assertEqual(legacy.SERVICE_ID,"srv-dbc3j8uq1p3s73c63sv0")
        self.assertFalse(hasattr(legacy,"REQUESTS"))
        self.assertFalse(hasattr(legacy,"ACKS"))

if __name__=="__main__":
    unittest.main()
