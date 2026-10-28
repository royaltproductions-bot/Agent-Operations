"""Verify the external design-service contract without consuming credits."""
from io import BytesIO
import os
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import gambit_web_design as web


class DesignResourceTests(unittest.TestCase):
    def test_no_credentials_means_no_outbound_call(self):
        with patch.dict(os.environ, {"V0_API_KEY": ""}), \
             patch.object(web, "_request") as request:
            with self.assertRaisesRegex(RuntimeError, "v0_not_connected"):
                web.generate_from_resource({"title": "Landing page", "description": "Business"})
            request.assert_not_called()

    def test_provider_project_files_are_preserved_as_a_real_archive(self):
        items = [{"name": "package.json", "content": '{"scripts":{"build":"next build"}}'},
                 {"name": "app/page.tsx", "content": "export default function Page() { return <h1>Example</h1> }"}]
        with patch.dict(os.environ, {"V0_API_KEY": "test-key"}), \
             patch.object(web, "_request", side_effect=[
                 {"id": "chat_123", "latestVersion": {"id": "v123", "status": "completed"}},
                 {"status": "completed", "files": items,
                  "demoUrl": "https://example.vercel.app/"}]) as request:
            result = web.generate_from_resource({"title": "Responsive shop website",
                                                 "description": "Build a store landing page."})
        self.assertEqual(result.file_count, 2)
        self.assertEqual(result.preview_url, "https://example.vercel.app/")
        with ZipFile(BytesIO(result.archive)) as archive:
            self.assertEqual(set(archive.namelist()), {"package.json", "app/page.tsx"})
            self.assertIn(b"Example", archive.read("app/page.tsx"))
        self.assertEqual(request.call_args_list[0].args[:2], ("/chats", "POST"))
        self.assertIn("responsive", request.call_args_list[0].args[2]["message"].lower())

    def test_rejects_path_traversal_duplicate_and_missing_project(self):
        for files in [
            [{"name": "../passwords.txt", "content": "oops"}],
            [{"name": "package.json", "content": "a"},
             {"name": "package.json", "content": "b"}],
            [{"name": ".env", "content": "secret"}],
            [{"name": "docs/readme.md", "content": "hello"}],
        ]:
            with self.subTest(files=files), self.assertRaises(ValueError):
                web.package_project(files)

    def test_rejects_missing_version_or_failed_generation(self):
        with patch.dict(os.environ, {"V0_API_KEY": "test-key"}), \
             patch.object(web, "_request", return_value={
                 "id": "chat_123", "latestVersion": {"status": "failed"}}):
            with self.assertRaisesRegex(RuntimeError, "v0_generation_failed"):
                web.generate_from_resource({"title": "Website", "description": "A business page"})

    def test_request_path_boundary_and_no_secret_logging(self):
        with patch.dict(os.environ, {"V0_API_KEY": "supersecret"}):
            with self.assertRaisesRegex(ValueError, "v0_path_not_allowed"):
                web._request("/../../../unauthorized")


if __name__ == "__main__":
    unittest.main()
