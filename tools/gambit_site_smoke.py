"""Render one full-size source-site sample in actual Chrome on the CI runner."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tools.gambit_site_resources import create_project
from tools.gambit_visual_qa import render_browser_evidence


def main():
    preview_dir = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "gambit-website-preview"
    # This is fictional sample copy to test real rendering, never a buyer delivery.
    brief = ("Orchard House is an independent ceramics studio. It offers "
             "handmade tableware, custom pottery and introductory wheel workshops. "
             "Reach them at mailto:hello@orchardhouse.test. Deliver only a static ZIP.")
    spec = {
        "brand": "Orchard House", "eyebrow": "Independent ceramics studio",
        "headline": "Crafted for everyday rituals.",
        "intro": "Discover handmade tableware and pottery experiences shaped by thoughtful craft.",
        "about": "Orchard House creates durable handmade pieces and welcomes curious beginners to learn pottery.",
        "services_heading": "Ceramics for everyday moments.",
        "about_heading": "Rooted in thoughtful craftsmanship.",
        "approach_heading": "From curiosity to clay.",
        "cta_heading": "Find your next favorite piece.",
        "theme": "amber", "contact_url": "mailto:hello@orchardhouse.test",
        "contact_label": "Get in touch", "services": [
            {"title": "Handmade tableware", "description": "Functional ceramics for the moments around your table."},
            {"title": "Custom pottery", "description": "Small-batch work for personal projects and gifts."},
            {"title": "Wheel workshops", "description": "Introductory sessions for trying your hand at pottery."}],
        "steps": [
            {"title": "Explore your options", "description": "Find a piece or workshop that feels right for you."},
            {"title": "Connect with the studio", "description": "Ask about current pieces or available workshop times."}]}
    package, html, resources = create_project(spec, brief)
    evidence = render_browser_evidence(html, preview_dir=preview_dir, asset_files=resources)
    (preview_dir / "preview-source.zip").write_bytes(package)
    print(json.dumps({"desktop": {k: v for k, v in evidence["desktop"].items()
                                 if k != "screenshot"},
                      "mobile": {k: v for k, v in evidence["mobile"].items()
                                if k != "screenshot"}}))


if __name__ == "__main__":
    main()
