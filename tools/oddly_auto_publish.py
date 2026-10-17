#!/usr/bin/env python3
"""Prepare approved organic documentary Buffer posts from newly rendered SVG video.

Called ONLY by the renderer after creating MP4s. The renderer publishes these
post files itself AFTER committing the MP4. GitHub's GITHUB_TOKEN commits do
not trigger dependent push workflows, so do not rely on a second Action.
"""
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from public_voice_guard import assert_autonomous_voice

ROOT = Path("campaigns/oddly-useful-stuff")
RENDERED = Path("/tmp/oddly-new-videos.txt")
READY = Path("/tmp/oddly-ready-posts.txt")
READY.write_text("", encoding="utf-8")
if not RENDERED.exists():
    raise RuntimeError("Renderer did not provide new-video manifest")

ready = []
for raw in RENDERED.read_text(encoding="utf-8").splitlines():
    if not raw.strip():
        continue
    video = Path(raw)
    stem = video.stem
    if (not re.fullmatch(r"ai-commerce-[a-z0-9-]+", stem)
            or video.parent != ROOT / "media"
            or not video.is_file()
            or video.stat().st_size < 1000):
        continue
    svg = ROOT / "creative-source" / (stem + ".svg")
    if not svg.is_file():
        raise RuntimeError(f"Missing SVG source for {video}")
    root = ET.parse(svg).getroot()
    if root.attrib.get("data-auto-publish", "").strip().lower() != "true":
        continue
    caption = root.attrib.get("data-social-caption", "").strip()
    if not caption or len(caption) > 1900:
        raise ValueError(f"Opt-in episode needs a real caption: {svg}")
    # Guard public caption AND visible video text. No private operator or
    # overseer references; keep the AI account a singular autonomous narrator.
    visible_text = " ".join(str(part) for part in root.itertext() if part and part.strip())
    assert_autonomous_voice(caption, visible_text)
    # This automatic route is reserved strictly for organic AI experiment
    # documentation; the Buffer API cannot supply native paid-ad disclosures.
    lower = caption.lower()
    if any(s in lower for s in ("buy now", "purchase here", "checkout", "affiliate link", "discount code")):
        raise ValueError(f"Promotional CTA prohibited on organic auto route: {svg}")
    target = ROOT / "posts" / (stem + "-auto.json")
    if target.exists():
        # Never resubmit if a post file has already been prepared.
        continue
    payload = {
        "text": caption,
        "video_url": "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/"
                     + video.as_posix(),
        "is_ai_generated": True,
        "commercial_promotion": False,
        "approved_for_queue": True,
        "publish_now": True,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    ready.append(target.as_posix())
READY.write_text("\n".join(ready) + ("\n" if ready else ""), encoding="utf-8")
print(json.dumps({"newly_rendered": len(RENDERED.read_text().splitlines()),
                  "opt_in_posts_prepared": len(ready), "post_files": ready}))
