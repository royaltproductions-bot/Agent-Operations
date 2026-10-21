"""Render deploy requests go through existing connected Render account, not a fake API."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from tools import render_deploy_bridge as b
from tools import connected_commerce_tools as ct

class RenderBridgeTests(unittest.TestCase):
    def test_actual_render_connection_is_not_implicitly_available_in_github(self):
        status=ct.capability_status()
        self.assertIn("watchdog", status["render"])
        self.assertIn("render_deployment", status["tools"])

    def test_site_is_unchanged_no_request(self):
        with tempfile.TemporaryDirectory() as temp:
            rev=b.BASELINE
            with patch.object(b,"site_revision",return_value=rev), \
                 patch.object(b,"REQUESTS",Path(temp)/"requests"), \
                 patch.object(b,"ACKS",Path(temp)/"acks"):
                result=b.request_render_deployment()
            self.assertEqual(result["status"],"no_new_site_changes")
            self.assertFalse((Path(temp)/"requests").exists())

    def test_model_can_queue_modified_site_exactly_once_without_publishing(self):
        with tempfile.TemporaryDirectory() as temp:
            site=Path(temp)/"storefront"
            site.mkdir()
            (site/"index.html").write_text("a real changed owned site")
            rev="f"*40
            with patch.object(b,"site_revision",return_value=rev), \
                 patch.object(b,"SITE_PATH",str(site)), \
                 patch.object(b,"REQUESTS",Path(temp)/"requests"), \
                 patch.object(b,"ACKS",Path(temp)/"acks"):
                first=ct.execute_tool("render_deployment")
                again=ct.execute_tool("render_deployment")
                queued=json.loads((Path(temp)/"requests"/(rev+".json")).read_text())
            self.assertEqual(first["status"],"queued_for_existing_render_connector_not_deployed")
            self.assertEqual(again["status"],"already_queued_not_deployed")
            self.assertEqual(queued["status"],"awaiting_render_connector_execution")
            self.assertEqual(queued["service_id"],b.SERVICE_ID)
            self.assertEqual(queued["site_revision"],rev)
            self.assertNotIn("render_api_key",json.dumps(queued))

    def test_only_ack_from_actual_render_connector_counts_as_deployed(self):
        with tempfile.TemporaryDirectory() as temp:
            ack=Path(temp)/"acks"
            ack.mkdir()
            p=ack/"ack.json"
            rev="e"*40
            p.write_text(json.dumps({"service_id":b.SERVICE_ID,
                "site_revision":rev,"status":"live",
                "verified_by":"untrusted_runner"}))
            with patch.object(b,"ACKS",ack):
                self.assertEqual(b.latest_acknowledged_revision(),b.BASELINE)
            p.write_text(json.dumps({"service_id":b.SERVICE_ID,
                "site_revision":rev,"status":"live",
                "verified_by":"render_connector"}))
            with patch.object(b,"ACKS",ack):
                self.assertEqual(b.latest_acknowledged_revision(),rev)

if __name__=="__main__":
    unittest.main()
