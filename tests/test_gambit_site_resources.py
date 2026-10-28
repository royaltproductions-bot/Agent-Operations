"""No-cost, buyer-grounded site resource and actual worker contract checks."""
import io
import os
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import gambit_site_resources as sites
from tools import gambit_worker as gambit


BRIEF = ("Orchard House is a small independent ceramics studio. "
         "We offer handmade tableware, small-batch custom pottery, "
         "and introductory wheel workshops. Customers value thoughtful "
         "craftsmanship, durable pieces and the chance to learn pottery. "
         "Get in touch at mailto:hello@orchardhouse.test. "
         "Deliver a static landing page as source files in a ZIP. No hosting needed.")

SPEC = {
    "status": "ready", "reason": "", "question": "", "brand": "Orchard House",
    "eyebrow": "Independent ceramics studio", "headline": "Crafted for everyday rituals.",
    "intro": "Discover handmade tableware and pottery experiences shaped by thoughtful craft.",
    "about": "Orchard House creates durable handmade pieces and welcomes curious beginners into the studio.",
    "services_heading": "Ceramics for everyday moments.",
    "about_heading": "Rooted in thoughtful craftsmanship.",
    "approach_heading": "From curiosity to clay.",
    "cta_heading": "Find your next favorite piece.",
    "theme": "amber", "contact_url": "mailto:hello@orchardhouse.test",
    "contact_label": "Get in touch", "services": [
        {"title": "Handmade tableware", "description": "Practical ceramic pieces created for daily use."},
        {"title": "Custom pottery", "description": "Small-batch ceramics made with your needs in mind."},
        {"title": "Wheel workshops", "description": "Introductory sessions for learning to work with clay."},
    ], "steps": [
        {"title": "Explore the work", "description": "Find the collection or class that fits your interests."},
        {"title": "Get in touch", "description": "Ask about current pieces, projects or workshop times."},
    ]}


class SiteResourceTests(unittest.TestCase):
    def test_packaged_real_assets_and_licensed_source(self):
        archive, html, assets = sites.create_project(SPEC, BRIEF)
        self.assertIn('href="assets/framework.css"', html)
        self.assertIn("mailto:hello@orchardhouse.test", html)
        self.assertNotIn("example.com", html)
        with ZipFile(io.BytesIO(archive)) as bundle:
            self.assertEqual(set(bundle.namelist()), {
                "index.html", "assets/framework.css", "assets/custom.css",
                "assets/LICENSE", "README.txt"})
            self.assertEqual(bundle.read("index.html"), html.encode())
            self.assertGreater(len(bundle.read("assets/framework.css")), 40_000)
            self.assertIn(b"MIT License", bundle.read("assets/LICENSE"))
        self.assertIn("assets/custom.css", assets)

    def test_unverified_contact_and_placeholder_rejected(self):
        self.assertRaises(ValueError, sites.create_project,
                          {**SPEC, "contact_url": "https://fake-payments.test"}, BRIEF)
        self.assertRaises(ValueError, sites.create_project,
                          {**SPEC, "headline": "Your company goes here"}, BRIEF)

    def test_website_offer_requires_live_renderer(self):
        details = {"bounty": {"title": "Static landing page", "description": BRIEF,
                              "verification": "ZIP source files, mobile layout"}}
        self.assertEqual("website_visual_quality_unverified",
                         gambit.unsupported_deliverable(details))
        with patch.object(gambit, "website_capable", return_value=True):
            self.assertIsNone(gambit.unsupported_deliverable(details))

    def test_worker_creates_deliverable_with_design_resource(self):
        details = {"bounty": {"title": "Static landing page", "description": BRIEF,
                              "verification": "ZIP source files, mobile layout"}}
        with (patch.object(gambit, "website_capable", return_value=True),
              patch.object(gambit, "infer", return_value=SPEC) as inference):
            result = gambit.produce(details, [])
        self.assertEqual("site_zip", result["deliverable_format"])
        self.assertEqual("ready", result["status"])
        self.assertGreater(len(result["_site_binary"]), 8_000)
        self.assertIn("licensed design", inference.call_args.args[1])

    def test_missing_brief_asks_question_without_fake_delivery(self):
        details = {"bounty": {"title": "Static landing page", "description": BRIEF}}
        answer = {**SPEC, "status": "need_information",
                  "question": "Which services should this page describe?"}
        with (patch.object(gambit, "website_capable", return_value=True),
              patch.object(gambit, "infer", return_value=answer)):
            result = gambit.produce(details, [])
        self.assertEqual("need_information", result["status"])
        self.assertNotIn("_site_binary", result)


if __name__ == "__main__":
    unittest.main()
