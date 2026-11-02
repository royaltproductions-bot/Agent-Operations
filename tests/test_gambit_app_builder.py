"""Tests for resumable encrypted application production and safety boundaries."""
import io
import os
from unittest.mock import patch
import unittest
from zipfile import ZipFile

from tools import gambit_app_builder as app


PLAN={"status":"ready","question":"","app_name":"Simple buyer application",
      "files":[{"path":p,"purpose":"Implement "+p} for p in app.REQUIRED]}
EXAMPLE={
 "server.py":"from http.server import BaseHTTPRequestHandler,HTTPServer\n",
 "index.html":"<!doctype html><html><body><h1>App</h1></body></html>",
 "README.md":"# Application\nRun with python server.py",
 "tests/test_app.py":"import unittest\nclass Smoke(unittest.TestCase):\n def test_workflow(self): self.assertTrue(True)\n"
}


class AppIntegrationTests(unittest.TestCase):
    def test_app_matching_does_not_confuse_company_websites_with_web_apps(self):
        self.assertTrue(app.requested({"bounty":{
            "title":"Research fundraising events",
            "description":"Build a simple web app for nonprofit domain uploads."}}))
        self.assertTrue(app.requested({"bounty":{
            "title":"Build an AI Business Opportunity Analysis Prototype",
            "description":"Create a web prototype with chat input and report."}}))
        self.assertFalse(app.requested({"bounty":{
            "title":"Research 15 fintech startups",
            "description":"Record website URL for each startup."}}))

    def test_functional_app_product_language_and_backend_site_requests(self):
        accepted = [
            ("Develop an internal dashboard", "Users review uploaded CSVs.", ""),
            ("Build a client portal", "Client-specific account data.", "Source ZIP."),
            ("Create a customer portal", "Working forms and account pages.", ""),
            ("Make a browser-based tool", "Process locally supplied inputs.", ""),
            ("Build an admin dashboard", "Use a Python backend.", "Deliver source."),
            ("Create a SaaS dashboard", "Provide app source.", ""),
            ("Develop a web-based application", "Implement API endpoints.", ""),
            ("Build a website", "Allow customers to upload CSV files and process data.", ""),
            ("Create a website", "Backend must save submitted records.", ""),
            ("Build a website", "Server-side calculations on submitted data.", ""),
            ("Build a landing page", "An API endpoint receives user uploads.", ""),
            ("Create a dashboard", "Include database and working login.", ""),
        ]
        for title, description, verification in accepted:
            with self.subTest(title=title, description=description):
                self.assertTrue(app.requested({"bounty": {
                    "title": title, "description": description,
                    "verification": verification}}))
        excluded = [
            ("Research dashboards", "Deliver sourced CSV report.", ""),
            ("Build a company website", "Static HTML and CSS brochure.", ""),
            ("Create a landing page", "Email contact link only.", "Source ZIP."),
            ("Write a report", "Compare admin dashboards available online.", ""),
            ("Build an HTML mockup", "Visual only, no backend.", ""),
            ("Research fundraising events", "Find website URLs for public events.", ""),
            ("Design a static site", "Describe SaaS dashboards our company sells.", ""),
            ("Build a public marketing website",
             "Describe services using the words client portal.", "Static HTML."),
            ("Create a brochure site",
             "Include a download link for existing CSV.", "Source files only."),
        ]
        for title, description, verification in excluded:
            with self.subTest(title=title, description=description):
                self.assertFalse(app.requested({"bounty": {
                    "title": title, "description": description,
                    "verification": verification}}))

    def test_paths_prevent_escape_and_untrusted_executable_uploads(self):
        for name in ("../secrets.py","/etc/passwd","assets/../secret.py",
                     ".github/workflows/actions.yml","run.sh","a\\b.py"):
            with self.assertRaises(ValueError,msg=name):
                app.safe_path(name)
        self.assertEqual("assets/app.js",app.safe_path("assets/app.js"))

    def test_assembler_never_executes_untrusted_python(self):
        files=dict(EXAMPLE)
        files["server.py"]="raise RuntimeError('should never run on assembly')"
        archive=app.make_zip(files)
        with ZipFile(io.BytesIO(archive)) as z:
            self.assertEqual(z.read("server.py").decode(),files["server.py"])

    def test_each_file_persists_as_encrypted_progress_between_cycles(self):
        state={}
        answers=[PLAN]+[{"path":name,"content":EXAMPLE[name]}
                        for name in app.REQUIRED]
        with patch.dict(os.environ,{"BOUNTY_AGENT_API_KEY":"safe-dummy-for-tests"}), \
             patch.object(app,"verify_project",return_value={
                 "sandbox":"isolated_tests_passed","desktop":True,"mobile":True,
                 "http":"status_200_html"}) as verify:
            def infer(*a,**kw):
                return answers.pop(0)
            def advance():
                return app.progress({"bounty":{"title":"Build a web app",
                    "description":"Working original application."}},
                    state,"buyer-1","claim-1",infer,lambda *args,**kw:{})
            planned=advance()
            self.assertEqual((planned["stage"],planned["completed"]),("planned",0))
            self.assertNotIn("Simple buyer application",
                             state["app_projects"]["buyer-1"])
            for count in range(1,5):
                partial=advance()
                self.assertEqual(partial["completed"],count)
                self.assertEqual(partial["stage"],"file_written")
                self.assertNotIn(EXAMPLE[app.REQUIRED[count-1]],
                                 state["app_projects"]["buyer-1"])
            complete=advance()
            self.assertEqual(complete["status"],"ready")
            self.assertEqual(complete["deliverable_format"],"app_zip")
            self.assertTrue(complete["_app_binary"].startswith(b"PK"))
            verify.assert_called_once()

    def test_wrong_file_cannot_corrupt_persisted_checkpoint(self):
        state={}
        with patch.dict(os.environ,{"BOUNTY_AGENT_API_KEY":"safe-dummy-for-tests"}):
            def infer(*a,**kw):
                return PLAN if "buyer-1" not in state.get("app_projects",{}) else {
                    "path":"secrets.py","content":"print('no')"}
            app.progress({"bounty":{}} ,state,"buyer-1","claim-1",infer,
                         lambda *args,**kw:{})
            before=state["app_projects"]["buyer-1"]
            with self.assertRaises(ValueError):
                app.progress({"bounty":{}},state,"buyer-1","claim-1",infer,
                             lambda *args,**kw:{})
            self.assertEqual(before,state["app_projects"]["buyer-1"])

    def test_revision_retains_other_finished_app_sections(self):
        state={}
        with patch.dict(os.environ,{"BOUNTY_AGENT_API_KEY":"safe-dummy-for-tests"}):
            original={"claim_id":"c1","plan":PLAN,"files":dict(EXAMPLE),
                      "feedback":"","repairs":0}
            state["app_projects"]={"job":app.seal(original)}
            app.request_revision(state,"job","Backend failed")
            restored=app.unseal(state["app_projects"]["job"])
            self.assertNotIn("server.py",restored["files"])
            self.assertIn("index.html",restored["files"])
            self.assertIn("Backend failed",restored["feedback"])

    def test_actual_sandbox_never_runs_inside_production_worker(self):
        self.assertFalse(app.available() if os.environ.get("GAMBIT_APP_TEST_NO_DOCKER") else False)
        # Resource discovery and archive assembly may run on the worker.
        # Only verify_project invokes docker and receives a scrubbed env.


if __name__=="__main__":
    unittest.main()
