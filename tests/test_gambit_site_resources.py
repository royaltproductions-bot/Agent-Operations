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
    def test_html_css_audit_remains_report_not_website_commission(self):
        audit = {"bounty": {
            "title": "Audit HTML and CSS accessibility",
            "description": "Review a website source and prepare an audit report.",
            "verification": "Deliver a written accessibility analysis."}}
        self.assertFalse(sites.static_site_eligible(audit))
        self.assertIsNone(gambit.unsupported_deliverable(audit))
        # An authentic source-site commission still qualifies.
        website = {"bounty": {
            "title": "Design a static HTML landing page",
            "description": "Provide a responsive source ZIP.",
            "verification": "Working HTML and CSS files."}}
        self.assertTrue(sites.static_site_eligible(website))

    def test_one_page_report_is_not_misclassified_as_website(self):
        self.assertFalse(sites.static_site_eligible({
            "bounty": {"title": "Summarize a customer memo",
                       "description": "Write a one-page summary of the supplied memo.",
                       "verification": "Deliver an accurate prose report."}}))
        self.assertFalse(sites.static_site_eligible({
            "bounty": {"title": "Static analysis of CSV figures",
                       "description": "Calculate a one-page table of averages.",
                       "verification": "A CSV analysis report."}}))
        self.assertTrue(sites.static_site_eligible({
            "bounty": {"title": "Create a single-page website",
                       "description": "Deliver standalone HTML and CSS.",
                       "verification": "Source ZIP."}}))

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

    def test_complete_multipage_site_links_and_packages_all_pages(self):
        spec = {**SPEC, "pages": [
            {"slug": "about", "nav_label": "About", "headline": "Meet our ceramics studio",
             "intro": "Learn how the Orchard House studio approaches handmade work.",
             "sections": [{"title": "Our craft",
                           "description": "Small-batch handmade ceramic pieces for everyday use."}]},
            {"slug": "services", "nav_label": "Services", "headline": "Pottery and workshops",
             "intro": "Explore tableware, custom pottery and introductory wheel workshops.",
             "sections": [{"title": "Handmade tableware",
                           "description": "Durable tableware is made by hand in small batches."}]},
            {"slug": "contact", "nav_label": "Contact", "headline": "Speak to the studio",
             "intro": "Ask Orchard House about available pottery and studio workshops.",
             "sections": [{"title": "Start a conversation",
                           "description": "Get in touch about a project or an introductory workshop."}]}]}
        archive, html, assets = sites.create_project(spec, BRIEF)
        with ZipFile(io.BytesIO(archive)) as bundle:
            files = set(bundle.namelist())
            self.assertTrue({"index.html", "about.html", "services.html",
                             "contact.html", "assets/framework.css",
                             "assets/custom.css"}.issubset(files))
            for page in ("index.html", "about.html", "services.html", "contact.html"):
                source = bundle.read(page).decode("utf-8")
                self.assertIn('href="assets/framework.css"', source)
                self.assertIn('href="assets/custom.css"', source)
                self.assertIn('href="index.html"', source)
                for linked in ("about.html", "services.html", "contact.html"):
                    self.assertIn(f'href="{linked}"', source)
                self.assertIn("<h1>", source)
        self.assertEqual(html, ZipFile(io.BytesIO(archive)).read("index.html").decode())
        self.assertIn("services.html", assets)

    def test_multipage_invalid_slug_or_duplicate_page_fails(self):
        extra = {"nav_label": "About", "headline": "Meet our pottery makers",
                 "intro": "Learn more about the small pottery studio.",
                 "sections": [{"title": "Our approach",
                               "description": "Handmade ceramics for everyday moments."}]}
        for pages in ([{"slug": "../bad", **extra}],
                      [{"slug": "about", **extra}, {"slug": "about", **extra}],
                      [{"slug": "index", **extra}]):
            with self.assertRaises(ValueError):
                sites.create_project({**SPEC, "pages": pages}, BRIEF)

    def test_multipage_request_is_eligible_without_paid_tools(self):
        details = {"bounty": {"title": "Build a four-page website",
                              "description": "Create a multi-page static website with an About page.",
                              "verification": "Provide linked HTML and CSS source files."}}
        self.assertTrue(sites.multipage_requested(details))
        self.assertTrue(sites.static_site_eligible(details))
        with patch.object(gambit, "website_capable", return_value=True):
            self.assertIsNone(gambit.unsupported_deliverable(details))

    def test_worker_generates_multiple_linked_pages_without_website_vendor(self):
        details = {"bounty": {"title": "Build a multi-page website",
                              "description": BRIEF + " Include separate About and Services pages.",
                              "verification": "Linked, responsive HTML files in one ZIP."}}
        extra = {"slug": "about", "nav_label": "About",
                 "headline": "Orchard House pottery craft",
                 "intro": "Explore our pottery studio and its everyday handmade work.",
                 "sections": [{"title": "About the studio",
                               "description": "Independent ceramics made through thoughtful craftsmanship."}]}
        with (patch.object(gambit, "website_capable", return_value=True),
              patch.object(gambit, "infer",
                           return_value={**SPEC, "pages": [extra]}) as infer):
            output = gambit.produce(details, [])
        self.assertEqual(output["status"], "ready")
        self.assertEqual(output["deliverable_format"], "site_zip")
        with ZipFile(io.BytesIO(output["_site_binary"])) as archive:
            self.assertIn("about.html", archive.namelist())
        self.assertTrue(infer.call_args.kwargs["num_predict"] > 1800)

    def test_unverified_contact_and_placeholder_rejected(self):
        self.assertRaises(ValueError, sites.create_project,
                          {**SPEC, "contact_url": "https://fake-payments.test"}, BRIEF)
        self.assertRaises(ValueError, sites.create_project,
                          {**SPEC, "headline": "Your company goes here"}, BRIEF)

    @patch.object(gambit, "website_capable", return_value=False)
    def test_website_offer_requires_live_renderer(self, _capable):
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

    def test_previously_blocked_hired_website_is_revisited_when_supported(self):
        state = {"bounties": {"site-1": {
            "action": "needs_operator_review",
            "reason": "website_visual_quality_unverified"}}}
        claimed = {"_id": "site-1", "status": "claimed"}
        with (patch.object(gambit, "website_capable", return_value=True),
              patch.object(gambit, "pages", side_effect=[[], [claimed]]),
              patch.object(gambit, "relevant_events", return_value=([], None))):
            _, _, _, _, _, active = gambit.scan(state)
        self.assertEqual([claimed], active)


if __name__ == "__main__":
    unittest.main()
