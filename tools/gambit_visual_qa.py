"""Browser rendering checks for experimental static-site output.

These checks catch blank pages, broken assets, JavaScript exceptions, horizontal
overflow and absent responsive hierarchy. They do NOT judge aesthetic quality,
so passing this module does not authorize professional website bids.
"""
from __future__ import annotations

import io
from pathlib import Path
import tempfile


VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}


def render_browser_evidence(html_text, preview_dir=None):
    """Render one generated page at two sizes and return in-memory evidence."""
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageStat

    if not html_text or len(html_text.encode("utf-8")) > 120_000:
        raise ValueError("visual_qa_source_invalid")
    evidence = {}
    with tempfile.TemporaryDirectory(prefix="gambit-visual-qa-") as workdir:
        page_path = Path(workdir) / "index.html"
        page_path.write_text(html_text, encoding="utf-8")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for label, (width, height) in VIEWPORTS.items():
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            device_scale_factor=1)
                    console_errors = []
                    page.on("pageerror", lambda error: console_errors.append("js_error"))
                    page.on("console", lambda message: (
                        console_errors.append("console_error") if message.type == "error" else None))
                    page.on("requestfailed", lambda request: console_errors.append("resource_failed"))
                    page.goto(page_path.as_uri(), wait_until="load", timeout=15_000)
                    page.wait_for_timeout(400)
                    metrics = page.evaluate("""() => {
                        const doc = document.documentElement;
                        const heading = document.querySelector('h1');
                        const body = document.body;
                        const h1box = heading?.getBoundingClientRect();
                        const css = heading ? getComputedStyle(heading) : null;
                        return {
                            overflow: doc.scrollWidth > window.innerWidth + 2,
                            body_text_length: (body?.innerText || '').trim().length,
                            heading_visible: !!heading && !!h1box && h1box.width > 0 &&
                                h1box.height > 0 && css.visibility !== 'hidden' &&
                                css.display !== 'none',
                            heading_px: css ? parseFloat(css.fontSize) : 0,
                            broken_images: [...document.images].filter(img =>
                                !img.complete || img.naturalWidth === 0).length,
                            page_title: document.title || ''
                        };
                    }""")
                    png = page.screenshot(full_page=True, animations="disabled", timeout=15_000)
                    if preview_dir:
                        destination = Path(preview_dir)
                        destination.mkdir(parents=True, exist_ok=True)
                        (destination / f"website-{label}.png").write_bytes(png)
                    image = Image.open(io.BytesIO(png)).convert("RGB")
                    variation = max(ImageStat.Stat(image.resize((256, 144))).stddev)
                    page.close()
                    if (console_errors or metrics["overflow"] or
                            metrics["broken_images"] or not metrics["heading_visible"] or
                            metrics["body_text_length"] < 35 or
                            metrics["heading_px"] < (24 if label == "mobile" else 30) or
                            variation < 8):
                        raise ValueError("visual_qa_failed_" + label)
                    evidence[label] = {"screenshot": png,
                                       "width": width, "height": height,
                                       "heading_px": metrics["heading_px"],
                                       "color_variation": round(variation, 2),
                                       "body_text_length": metrics["body_text_length"]}
            finally:
                browser.close()
    return evidence
