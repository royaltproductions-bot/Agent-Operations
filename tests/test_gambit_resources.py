"""Resource integration contracts; a green configuration must execute real tools."""
import io
import os
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import gambit_resources as resources


class ResourceRouterTests(unittest.TestCase):
    def test_paid_provider_is_rejected_even_when_available(self):
        with self.assertRaisesRegex(ValueError, "paid_gambit_resource_forbidden"):
            resources.register(resources.Provider(
                "paid-api", "site_source", lambda: True, lambda: None,
                zero_cost=False))

    def test_missing_resource_fails_closed(self):
        with self.assertRaisesRegex(resources.ResourceUnavailable, "resource_unavailable"):
            resources.execute("unsupported_job")

    def test_free_provider_can_override_bundled_site_resource(self):
        resources._load()
        output = io.BytesIO()
        with ZipFile(output, "w") as archive:
            archive.writestr("index.html", "<html>Ready</html>")
        provider = resources.Provider(
            "verified-external-designer", "site_source", lambda: True,
            lambda spec, brief: (output.getvalue(), "<html>Ready</html>", {}),
            priority=100)
        resources.register(provider)
        try:
            result = resources.execute("site_source", {}, "a buyer brief")
            self.assertEqual(result[1], "<html>Ready</html>")
            self.assertEqual(resources.manifest()[1]["billing"], "no_charge")
        finally:
            resources._PROVIDERS["site_source"].remove(provider)

    def test_fake_site_zip_is_rejected_even_if_provider_claims_success(self):
        resources._load()
        provider = resources.Provider(
            "fake-designer", "site_source", lambda: True,
            lambda spec, brief: (b"unverified" * 100, "<html>Fake</html>", {}),
            priority=100)
        resources.register(provider)
        try:
            with self.assertRaisesRegex(ValueError, "site_provider_invalid_archive"):
                resources.execute("site_source", {}, "a buyer brief")
        finally:
            resources._PROVIDERS["site_source"].remove(provider)

    def test_provider_output_must_match_shared_contract(self):
        resources._load()
        provider = resources.Provider("broken-search", "public_search", lambda: True,
                                      lambda queries: "I found ten leads", priority=100)
        resources.register(provider)
        try:
            with self.assertRaisesRegex(ValueError, "public_search_invalid_output"):
                resources.execute("public_search", ["actual leads"])
        finally:
            resources._PROVIDERS["public_search"].remove(provider)

    def test_unhealthy_external_provider_does_not_qualify_a_bid(self):
        resources._load()
        provider = resources.Provider(
            "offline-service", "premium_research", lambda: 1 / 0,
            lambda: {"results": []})
        resources.register(provider)
        try:
            self.assertFalse(resources.available("premium_research"))
            with self.assertRaises(resources.ResourceUnavailable):
                resources.execute("premium_research")
            self.assertFalse(next(x for x in resources.manifest()
                                  if x["capability"] == "premium_research")["available"])
        finally:
            resources._PROVIDERS.pop("premium_research")

    def test_invalid_plugin_cannot_leave_partially_active_catalog(self):
        original_loaded = resources._LOADED
        original = {k: list(v) for k, v in resources._PROVIDERS.items()}
        try:
            resources._LOADED = False
            resources._PROVIDERS.clear()
            with patch.dict(os.environ, {"GAMBIT_RESOURCE_PLUGINS":
                                      "untrusted.http://buyer.example"}):
                with self.assertRaisesRegex(ValueError, "untrusted_resource_module"):
                    resources.available("site_source")
            self.assertFalse(resources._LOADED)
            self.assertEqual(resources._PROVIDERS, {})
        finally:
            resources._PROVIDERS.clear()
            resources._PROVIDERS.update(original)
            resources._LOADED = original_loaded


if __name__ == "__main__":
    unittest.main()
