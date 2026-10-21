"""Render direct-key integration tests. No provider calls and no credential writes."""
import json
import os
import unittest
from unittest.mock import patch
from tools import render_direct_api as rd
from tools import connected_commerce_tools as ct

class Response:
    def __init__(self, data):
        self.data=data
    def __enter__(self):return self
    def __exit__(self, *args):return False
    def read(self, n=250000):return json.dumps(self.data).encode()

SERVICE = {"id":rd.SERVICE_ID,"ownerId":rd.WORKSPACE_ID,
           "type":"static_site","branch":"main","repo":rd.SOURCE_REPO,
           "serviceDetails":{"publishPath":"./storefront"}}
REV="a"*40
OLD="b"*40
HEAD="c"*40

class RenderDirectTests(unittest.TestCase):
    def test_render_secret_only_in_github_env_not_model_prompt(self):
        with patch.dict(os.environ,{"RENDER_API_KEY":"test-secret"}):
            capabilities=ct.capability_status()
        self.assertTrue(capabilities["render"])
        self.assertNotIn("test-secret",repr(capabilities))
        self.assertIn("render_status",ct.TOOL_DEFINITIONS)
        self.assertIn("render_deployment",ct.TOOL_DEFINITIONS)

    def test_get_service_uses_existing_key_and_fixed_endpoint(self):
        captured=[]
        def fake(req, timeout):
            captured.append(req)
            return Response(SERVICE)
        with patch.dict(os.environ,{"RENDER_API_KEY":"private-key"}), \
             patch.object(rd.urllib.request,"urlopen",fake):
            result=rd.service()
        self.assertEqual(result["id"],rd.SERVICE_ID)
        self.assertEqual(captured[0].get_header("Authorization"),"Bearer private-key")
        self.assertEqual(captured[0].full_url,rd.API_ROOT+"/services/"+rd.SERVICE_ID)
        self.assertNotIn("private-key",repr(result))

    def test_provider_scope_mismatch_blocks_actions(self):
        with patch.object(rd,"api",return_value={**SERVICE,"ownerId":"other-workspace"}):
            with self.assertRaisesRegex(RuntimeError,"render_service_scope_verification_failed"):
                rd.service()

    def test_no_key_fails_without_leaking_credential(self):
        with patch.dict(os.environ,{"RENDER_API_KEY":""}):
            with self.assertRaisesRegex(RuntimeError,"render_api_key_not_configured"):
                rd.api("GET","/services/"+rd.SERVICE_ID)

    def test_arbitrary_services_writes_cannot_be_called(self):
        with self.assertRaisesRegex(ValueError,"render_endpoint_not_permitted"):
            rd.api("GET","/services/srv-other-account")
        with self.assertRaisesRegex(ValueError,"render_method_not_permitted"):
            rd.api("DELETE","/services/"+rd.SERVICE_ID)
        with self.assertRaisesRegex(ValueError,"render_write_not_permitted"):
            rd.api("POST","/services/"+rd.SERVICE_ID,{})

    def test_list_deploys_unwraps_render_api_shape(self):
        rows=[{"deploy":{"id":"dep-test","status":"live","commit":{"id":OLD}},
               "cursor":"opaque"}]
        with patch.object(rd,"api",return_value=rows):
            self.assertEqual(rd.deployments()[0]["id"],"dep-test")

    def test_live_provider_commit_requires_ancestry(self):
        with patch.object(rd,"service",return_value={"id":rd.SERVICE_ID}), \
             patch.object(rd,"source_state",return_value={"head_commit":HEAD,"site_revision":REV}), \
             patch.object(rd,"deployments",return_value=[
                 {"id":"dep-live","status":"live","commit":{"id":HEAD}}]), \
             patch.object(rd,"commit_has_site_revision",
                          side_effect=lambda commit,site: commit==HEAD and site==REV):
            status=rd.render_status()
            self.assertEqual(status["status"],"live_for_site_revision")
            self.assertFalse(status["needs_deployment"])

    def test_deploy_blocked_when_latest_site_already_live(self):
        with patch.object(rd,"render_status",return_value={
                "status":"live_for_site_revision","site_revision":REV,
                "latest_live_deploy_id":"dep-verified","provider_live_commit":HEAD}), \
             patch.object(rd,"api") as calls:
            result=rd.deploy_site_if_needed()
            self.assertEqual(result["status"],"already_live_verified")
            calls.assert_not_called()

    def test_pending_build_prevents_duplicate_deploy(self):
        with patch.object(rd,"render_status",return_value={
                "status":"deploy_in_progress","site_revision":REV,"pending_deploy_id":"dep-queued"}), \
             patch.object(rd,"api") as calls:
            result=rd.deploy_site_if_needed()
            self.assertEqual(result["status"],"existing_build_in_progress")
            calls.assert_not_called()

    def test_verified_commit_deployment_executes_actual_post(self):
        state={"status":"site_revision_undeployed","site_revision":REV,
               "head_commit":HEAD}
        calls=[]
        def invoke(method,path,body=None):
            calls.append((method,path,body))
            return {"id":"dep-accepted","status":"created","commit":{"id":HEAD}}
        with patch.object(rd,"render_status",return_value=state), \
             patch.object(rd,"commit_has_site_revision",return_value=True), \
             patch.object(rd,"api",side_effect=invoke):
            result=ct.execute_tool("render_deployment")
        self.assertEqual(result["status"],"deploy_accepted_not_live_verified")
        self.assertEqual(calls[0][0],"POST")
        self.assertEqual(calls[0][1],"/services/"+rd.SERVICE_ID+"/deploys")
        self.assertEqual(calls[0][2]["commitId"],HEAD)
        self.assertEqual(calls[0][2]["clearCache"],"do_not_clear")

if __name__=="__main__":
    unittest.main()
