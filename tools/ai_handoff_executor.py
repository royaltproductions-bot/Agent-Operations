#!/usr/bin/env python3
"""Convert AI-authored GitHub handoff jobs into original documentary SVG sources.

Job files are instructions, not credentials or payments. This deterministic
executor never infers an unverified claim or overrides provider refusals.
"""
from __future__ import annotations
import hashlib
import html
import json
from pathlib import Path
import re
import textwrap
from urllib.parse import urlsplit

try:
    from .public_voice_guard import assert_autonomous_voice
except ImportError:
    from public_voice_guard import assert_autonomous_voice

QUEUE = Path("ops/ai-handoffs")
SOURCE = Path("campaigns/oddly-useful-stuff/creative-source")
OUTCOMES = Path("ops/ai-handoff-results")
READY = Path("/tmp/ai-handoff-ready.txt")
ID = re.compile(r"^[a-z][a-z0-9-]{9,79}$")
CONFIDENTIAL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")

def validate(job, filename=None):
    if not isinstance(job, dict) or job.get("schema_version") != 1:
        raise ValueError("Handoff schema_version must be 1")
    ident = job.get("id")
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        raise ValueError("Use lowercase unique handoff id of 10-80 characters")
    if filename and Path(filename).stem != ident:
        raise ValueError("Job filename must match id")
    if job.get("action") != "publish_organic_documentary":
        raise ValueError("Only the documentary rendering action is enabled")
    if job.get("approved_for_automatic_publication") is not True:
        raise ValueError("Unapproved publishing is prohibited")
    if job.get("authored_by") not in ("scheduled_chatgpt", "live_chat"):
        raise ValueError("Handoff must identify creator, never claim GH is AI")
    evidence = str(job.get("evidence_url") or "")
    url = urlsplit(evidence)
    if (url.scheme != "https" or url.hostname != "github.com"
        or not url.path.startswith("/royaltproductions-bot/Agent-Operations/")
        or url.username or url.password):
        raise ValueError("Provide concrete first-party GitHub evidence URL")
    fields = {
       "heading": (5, 36),
       "subtitle": (10, 70),
       "line_one": (8, 44),
       "line_two": (8, 44),
       "punchline": (8, 78),
       "caption": (55, 750),
    }
    for field,(lower,upper) in fields.items():
        value = job.get(field)
        if not isinstance(value,str) or not lower <= len(value.strip()) <= upper:
            raise ValueError(field + " needs a concise original text")
        if any(ord(char)<32 and char not in "\n" for char in value):
            raise ValueError("Control characters not permitted")
        if CONFIDENTIAL.search(value):
            raise ValueError("Email addresses cannot appear in public video")
    # Reject the previously spammed scan-count template, even if an agent
    # accidentally reintroduces it under a different filename.
    stale_card = re.compile(r"\\b(?:BOUNTY SAFARI|PUBLIC HINTS|COMPETING FIXES)\\b", re.I)
    if stale_card.search(" ".join(job[k] for k in fields)):
        raise ValueError("Repeated bounty-scan cards are retired; tell a fresh factual story")
    if job.get("visual_style") not in (None, "avatar", "casefile", "console"):
        raise ValueError("visual_style must be avatar, casefile, or console")
    assert_autonomous_voice(*(job[k] for k in fields))
    return job

def make_svg(job):
    ident=job["id"]
    # Changing ID changes scene accents and abstract decoration, not facts.
    d=hashlib.sha256(ident.encode()).digest()
    palettes=[
       ("#090f28","#313566","#8aebd3","#fce79a"),
       ("#19122c","#4d245b","#85dffd","#f8c2dc"),
       ("#0c1f2d","#2d4563","#f5da84","#83f4bd"),
       ("#1c1530","#47304e","#c3ecff","#ffd8ad"),
    ]
    bg1,bg2,accent,gold=palettes[d[0]%len(palettes)]
    esc=lambda s:html.escape(str(s),quote=True)
    # Split solely on whitespace: code text isn't treated as SVG markup.
    punchlines=textwrap.wrap(job["punchline"],width=28,break_long_words=False)[:3]
    punch_svg="\n".join(
       f'<text x="540" y="{1593+i*60}" text-anchor="middle" font-family="DejaVu Sans,Arial" '
       f'font-size="43" font-weight="bold" fill="{gold}">{esc(line)}</text>'
       for i,line in enumerate(punchlines))
    # Rotate among genuinely different centerpiece treatments. Styling is
    # deterministic for repeatability, while each episode's words remain factual.
    layout_names = ("avatar", "casefile", "console")
    requested_layout = job.get("visual_style")
    if requested_layout not in (None, *layout_names):
        raise ValueError("visual_style must be avatar, casefile, or console")
    layout = requested_layout or layout_names[d[1] % len(layout_names)]
    if layout == "avatar":
        artwork = f"""
<path d="M320 708 Q543 559 768 708" fill="none" stroke="{accent}" stroke-width="25" stroke-linecap="round"/>
<rect x="235" y="694" width="610" height="438" rx="193" fill="{accent}" stroke="#fff" stroke-width="6"/>
<rect x="306" y="761" width="468" height="242" rx="90" fill="#13213a"/>
<ellipse cx="428" cy="881" rx="58" ry="67" fill="{gold}"/>
<ellipse cx="654" cy="881" rx="58" ry="67" fill="{gold}"/>
<path d="M496 1025 Q540 1052 584 1025" fill="none" stroke="{accent}" stroke-width="11" stroke-linecap="round"/>
<path d="M541 677 V627" stroke="{gold}" stroke-width="16"/>
<circle cx="541" cy="603" r="32" fill="{gold}"/>
<text x="540" y="1187" text-anchor="middle" font-family="DejaVu Sans,Arial" font-size="36" fill="{gold}">THE AI IS STILL TRYING</text>
"""
    elif layout == "casefile":
        artwork = f"""
<g transform="rotate(-5 540 845)">
<rect x="225" y="598" width="660" height="573" rx="28" fill="#eceada" opacity=".25"/>
<rect x="192" y="574" width="660" height="573" rx="24" fill="#fff4d9"/>
<rect x="235" y="613" width="365" height="23" fill="{bg1}" opacity=".20"/>
<rect x="235" y="658" width="530" height="17" fill="{bg1}" opacity=".20"/>
<rect x="235" y="699" width="449" height="17" fill="{bg1}" opacity=".20"/>
<circle cx="543" cy="880" r="137" stroke="{bg2}" stroke-width="20" fill="none"/>
<path d="M638 980 L743 1103" stroke="{bg2}" stroke-width="26" stroke-linecap="round"/>
<text x="540" y="918" text-anchor="middle" font-family="DejaVu Sans,Arial" font-size="142" font-weight="bold" fill="{bg2}">?</text>
</g>
<rect x="293" y="1123" width="506" height="62" rx="18" fill="{accent}"/>
<text x="546" y="1168" text-anchor="middle" font-family="DejaVu Sans,Arial" font-size="35" font-weight="bold" fill="{bg1}">CASE FILE: OPEN</text>
"""
    else:
        artwork = f"""
<rect x="153" y="580" width="775" height="560" rx="27" fill="#050d1c" stroke="{accent}" stroke-width="8"/>
<rect x="154" y="581" width="772" height="68" rx="25" fill="{bg2}"/>
<circle cx="215" cy="618" r="15" fill="{gold}"/>
<circle cx="256" cy="618" r="15" fill="{accent}"/>
<circle cx="297" cy="618" r="15" fill="#fff" opacity=".4"/>
<text x="204" y="763" font-family="DejaVu Sans Mono,monospace" font-size="40" fill="{accent}">&gt; NEW ATTEMPT</text>
<text x="204" y="845" font-family="DejaVu Sans Mono,monospace" font-size="36" fill="#f4f0e5">&gt; CHECK REALITY</text>
<text x="204" y="930" font-family="DejaVu Sans Mono,monospace" font-size="36" fill="{gold}">&gt; STILL LEARNING</text>
<rect x="204" y="977" width="37" height="51" fill="{accent}"/>
<path d="M294 1093 L766 1093" stroke="{accent}" stroke-width="7" stroke-linecap="round"/>
<path d="M728 1054 L776 1093 L728 1132" stroke="{accent}" stroke-width="9" fill="none" stroke-linejoin="round"/>
"""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920"
 data-auto-publish="true" data-social-caption="{esc(job["caption"])}">
<defs><linearGradient id="scene" x1="0" x2="1" y1="0" y2="1">
<stop offset="0" stop-color="{bg1}"/><stop offset="1" stop-color="{bg2}"/></linearGradient>
<filter id="glow"><feGaussianBlur stdDeviation="16"/></filter></defs>
<rect width="1080" height="1920" fill="url(#scene)"/>
<circle cx="{690+d[1]}" cy="{285+d[2]}" r="275" fill="{accent}" opacity=".13"/>
<circle cx="{130+d[3]}" cy="1420" r="290" fill="{gold}" opacity=".12"/>
<rect x="62" y="80" width="525" height="70" rx="35" fill="#122536" stroke="{accent}" stroke-width="3"/>
<text x="325" y="126" text-anchor="middle" font-family="DejaVu Sans,Arial" font-size="36"
 font-weight="bold" fill="{accent}">ODDLY USEFUL STUFF</text>
<text x="63" y="312" font-family="DejaVu Sans,Arial" font-size="70" font-weight="bold" fill="#fff">{esc(job["heading"])}</text>
<text x="65" y="391" font-family="DejaVu Sans,Arial" font-size="32" fill="{gold}">{esc(job["subtitle"])}</text>
<rect x="65" y="468" width="950" height="1010" rx="48" fill="#152037" stroke="{accent}" stroke-width="3" opacity=".96"/>
{artwork}
<rect x="127" y="1200" width="825" height="214" rx="30" fill="#0a1327" stroke="{accent}" stroke-width="2"/>
<text x="540" y="1282" text-anchor="middle" font-family="DejaVu Sans,Arial" font-size="47"
 font-weight="bold" fill="#fff">{esc(job["line_one"])}</text>
<text x="540" y="1368" text-anchor="middle" font-family="DejaVu Sans,Arial" font-size="43"
 fill="{gold}">{esc(job["line_two"])}</text>
{punch_svg}
<text x="540" y="1879" text-anchor="middle" font-family="DejaVu Sans,Arial" font-size="26" fill="#c5c9d7">Independent experiment • Evidence-backed</text>
</svg>'''

def process(root=QUEUE, source=SOURCE, outcomes=OUTCOMES):
    READY.write_text("no\n",encoding="utf-8")
    produced=[]
    rejected=[]
    for filename in sorted(root.glob("*.json")):
        try:
            job=validate(json.loads(filename.read_text(encoding="utf-8")),filename)
            stem="ai-commerce-"+job["id"]
            svg=source/(stem+".svg")
            receipt=outcomes/(job["id"]+".json")
            if svg.exists() or receipt.exists():
                continue
            rendered=make_svg(job)
            source.mkdir(parents=True,exist_ok=True)
            outcomes.mkdir(parents=True,exist_ok=True)
            svg.write_text(rendered,encoding="utf-8")
            receipt.write_text(json.dumps({
                "schema_version":1,"id":job["id"],"handoff_received":True,
                "source":str(filename),"creative_source":str(svg),
                "evidence_url":job["evidence_url"],
                "renderer_dispatched":False,
                "buffer_submitted":False,
                "live_tiktok_verified":False,
                "note":"This is only a preparation receipt; check downstream workflows."
            },indent=2)+"\n",encoding="utf-8")
            produced.append(str(svg))
        except (ValueError,OSError,KeyError) as exc:
            rejected.append({"path":str(filename),"reason":str(exc)[:160]})
    if produced:
        READY.write_text("yes\n",encoding="utf-8")
    print(json.dumps({"new_approved_sources":produced,"rejected":rejected},ensure_ascii=False))
    if rejected:
        raise ValueError("Invalid handoff files: "+str(len(rejected)))
    return produced

if __name__=="__main__":
    process()
