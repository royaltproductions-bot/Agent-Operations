"""Use a licensed responsive design foundation for no-cost source-only sites.

Gambit supplies buyer-grounded content. The open-source Landwind Tailwind CSS
foundation (MIT license) owns the CSS reset/layout utilities; this adapter composes the page,
bundles its dependencies, and never invents testimonials, analytics or contacts.
No provider credentials or paid API calls are required.
"""
from __future__ import annotations

from html import escape
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

RESOURCE_DIR = Path(__file__).resolve().parent.parent / "assets" / "website_builder"
PALETTES = {
    "indigo": ("#6b5bf6", "#a998ff", "#11122a"),
    "ocean": ("#19a9c6", "#7ee5e7", "#08262e"),
    "forest": ("#48ad86", "#b2e5c5", "#10231c"),
    "amber": ("#e7a749", "#f6d999", "#241c15"),
    "rose": ("#d576a4", "#f0b9d1", "#291c26"),
}


def static_site_eligible(details: dict) -> bool:
    """Only source-only frontend work; server features need different resources."""
    bounty = details.get("bounty") or {}
    title = str(bounty.get("title") or "").lower().strip()
    joined = " ".join(str(bounty.get(name) or "") for name in
                      ("title", "description", "verification")).lower()
    # HTML/CSS can be INPUT or background context for a report. A research,
    # audit, comparison or explanation must not become a generated website.
    if re.search(
            r"^(?:audit|review|analy[sz]e|compare|research|explain|summari[sz]e|"
            r"investigate|evaluate|report on|write (?:an? )?(?:report|review|guide|"
            r"analysis|summary))\b", title):
        return False
    # "one-page summary" and "static analysis" are NOT website requests.
    # Require an explicit web deliverable, not an incidental adjective.
    if not re.search(
            r"\b(?:static\s+(?:web)?site|static\s+web ?page|"
            r"landing[- ]page|one[- ]page\s+(?:web)?site|"
            r"one[- ]page\s+web ?page|single[- ]page\s+(?:web)?site|"
            r"single[- ]page\s+web ?page|html|css|front[- ]end|"
            r"portfolio page|webpage mockup)\b", joined):
        return False
    if re.search(r"\b(?:backend|back.end|database|login|authentication|server.side|"
                 r"payments?|stripe|shopify|wordpress|user accounts?|api integration|"
                 r"production deployment)\b", joined):
        return False
    return True


def resources_available() -> bool:
    return (RESOURCE_DIR / "output.css").is_file() and (RESOURCE_DIR / "LICENSE").is_file()


def _line(value: object, max_len: int = 240) -> str:
    if not isinstance(value, str):
        raise ValueError("website_content_not_text")
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not 3 <= len(cleaned) <= max_len or re.search(
            r"\b(?:lorem ipsum|your company|company name|placeholder|insert here|"
            r"example\.com|coming soon|sample text)\b", cleaned, re.I):
        raise ValueError("website_content_incomplete")
    return cleaned


def _items(value: object, minimum: int, maximum: int) -> list[tuple[str, str]]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("website_invalid_section_count")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("website_invalid_section_item")
        result.append((_line(item.get("title"), 90), _line(item.get("description"), 340)))
    return result


def validate_spec(spec: dict, public_brief: str) -> dict:
    if not isinstance(spec, dict) or not resources_available():
        raise ValueError("website_design_resource_missing")
    data = {key: _line(spec.get(key), 130 if key in {
                "brand", "eyebrow", "services_heading", "about_heading",
                "approach_heading", "cta_heading"} else 360)
            for key in ("brand", "eyebrow", "headline", "intro", "about",
                        "services_heading", "about_heading", "approach_heading", "cta_heading")}
    data["services"] = _items(spec.get("services"), 3, 6)
    data["steps"] = _items(spec.get("steps"), 2, 4)
    data["theme"] = spec.get("theme", "indigo")
    if data["theme"] not in PALETTES:
        raise ValueError("website_unknown_theme")
    raw_contact = str(spec.get("contact_url") or "").strip()
    if raw_contact:
        split = urlsplit(raw_contact)
        if (split.scheme not in {"https", "mailto", "tel"} or
                (split.scheme == "https" and not split.hostname) or
                raw_contact not in public_brief):
            raise ValueError("website_unverified_contact_url")
    data["contact_url"] = raw_contact or "#services"
    data["contact_label"] = (_line(spec.get("contact_label"), 60) if raw_contact
                             else "Explore services")
    data["contact_enabled"] = bool(raw_contact)
    return data


_OVERRIDES = """
:root{color-scheme:dark;--accent:ACCENT;--accent-soft:ACCENTSOFT;--surface:SURFACE}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:5rem}
.container{width:min(calc(100% - 2.5rem),1180px);max-width:none;margin-inline:auto}.visually-hidden-focusable{position:absolute;left:-9999px;top:auto;width:1px;height:1px;overflow:hidden}.visually-hidden-focusable:focus{position:absolute;left:1rem;top:1rem;z-index:99;width:auto;height:auto;overflow:visible;background:#fff;color:#111;padding:.7rem 1rem;border-radius:8px}
body{background:#0d111a;color:#f4f5fa;font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;line-height:1.6}
a{color:inherit;text-decoration:none}a:hover{color:var(--accent-soft)}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid var(--accent-soft);outline-offset:4px}
.site-nav{position:sticky;top:0;z-index:20;background:rgba(13,17,26,.88);border-bottom:1px solid rgba(255,255,255,.09);backdrop-filter:blur(20px)}
.nav-inner{min-height:76px;display:flex;align-items:center;justify-content:space-between;gap:1.3rem}
.wordmark{display:flex;align-items:center;gap:.75rem;font-size:1.15rem;font-weight:800;letter-spacing:-.035em;min-width:0}.wordmark span:last-child{overflow-wrap:anywhere}
.brand-symbol{display:inline-grid;place-items:center;background:var(--accent);color:#0b111a;font-weight:900;width:37px;height:37px;border-radius:12px;flex-shrink:0}
.nav-links{display:flex;align-items:center;gap:2rem;font-size:.9rem;color:#d5d8e4}.nav-links a:hover{color:#fff}
.nav-cta,.primary-link{display:inline-flex;align-items:center;justify-content:center;padding:.9rem 1.35rem;border-radius:999px;background:var(--accent);color:#090d13;font-weight:800;border:1px solid transparent;transition:transform .2s,background .2s}.nav-cta:hover,.primary-link:hover{background:var(--accent-soft);color:#090d13;transform:translateY(-2px)}
.mobile-nav{display:none}.mobile-nav summary{cursor:pointer;list-style:none;padding:.5rem;border:1px solid #4b5261;border-radius:10px}.mobile-nav summary::-webkit-details-marker{display:none}
.hero{position:relative;isolation:isolate;overflow:hidden;padding:clamp(5rem,9vw,9rem) 0 clamp(5rem,9vw,8rem);background:radial-gradient(ellipse at 75% 25%,color-mix(in srgb,var(--accent) 19%,transparent),transparent 50%),radial-gradient(ellipse at 8% 90%,rgba(104,119,178,.15),transparent 50%),#0d111a}
.hero::after{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px);background-size:58px 58px;z-index:-2;mask-image:linear-gradient(90deg,transparent,#000)}
.hero-copy{max-width:790px}.eyebrow,.section-kicker{color:var(--accent-soft);font-weight:800;letter-spacing:.17em;text-transform:uppercase;font-size:.74rem}
.hero h1{font-size:clamp(3.2rem,7.5vw,6.75rem);font-weight:800;letter-spacing:-.069em;line-height:1.03;max-width:900px;margin:1.4rem 0 1.7rem;overflow-wrap:anywhere}
.hero-description{font-size:clamp(1.05rem,1.8vw,1.3rem);line-height:1.8;color:#adb6c7;max-width:660px;margin:0 0 2rem}
.hero-actions{display:flex;align-items:center;flex-wrap:wrap;gap:1.5rem}.text-link{font-weight:700;color:#dbe1ef;border-bottom:1px solid #727b8c;padding-bottom:.35rem}
.hero-art{position:absolute;right:-13vw;bottom:-35%;width:51vw;max-width:760px;aspect-ratio:1;border:1px solid color-mix(in srgb,var(--accent) 35%,transparent);border-radius:50%;z-index:-1;box-shadow:0 0 0 7vw rgba(255,255,255,.012),0 0 0 13vw rgba(255,255,255,.009)}
.hero-art::before{content:"";position:absolute;inset:18%;border:2px solid color-mix(in srgb,var(--accent) 65%,transparent);border-radius:22%;transform:rotate(32deg);box-shadow:0 0 65px color-mix(in srgb,var(--accent) 18%,transparent)}
.section-pad{padding:clamp(4rem,8vw,7rem) 0}.section-title{font-size:clamp(2rem,4.2vw,3.6rem);letter-spacing:-.052em;font-weight:800;line-height:1.16;margin:.8rem 0 1.4rem}
.muted{color:#aab4c4}.section-lead{font-size:1.12rem;max-width:720px;line-height:1.8}
.services{background:#121923}.services-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;margin-top:3.2rem}
.service-card{padding:clamp(1.3rem,2.4vw,2.25rem);background:linear-gradient(145deg,#1c2634,#151e2a);border:1px solid #334053;border-radius:20px;min-width:0;transition:transform .2s,border-color .2s}
.service-card:hover{transform:translateY(-5px);border-color:var(--accent)}.card-num{color:var(--accent-soft);font-family:ui-monospace,monospace;font-size:.85rem;letter-spacing:.15em}.service-card h3{font-size:1.35rem;letter-spacing:-.035em;margin:1.6rem 0 .75rem}.service-card p{color:#b4becd;margin:0}
.about-wrap{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr);gap:clamp(2rem,7vw,7rem);align-items:start}.about-mark{aspect-ratio:1.1;max-height:450px;border-radius:25px;overflow:hidden;background:radial-gradient(circle at 65% 40%,color-mix(in srgb,var(--accent) 55%,transparent),transparent 38%),repeating-linear-gradient(135deg,#1b2a3a 0,#1b2a3a 2px,#121b26 2px,#121b26 24px);border:1px solid #314052;display:grid;place-items:center}
.about-mark span{font-size:clamp(4rem,9vw,9rem);font-weight:900;letter-spacing:-.13em;color:var(--accent-soft);text-shadow:0 10px 55px #0008}
.step-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2rem;margin-top:3rem}.step{border-top:1px solid #3c4757;padding-top:1.5rem}.step h3{font-size:1.25rem;margin:.5rem 0}.step p{color:#adb7c6}
.cta-section{text-align:center;background:linear-gradient(130deg,var(--surface),#151924);border-top:1px solid #313947;padding:clamp(4rem,8vw,7rem) 0}.cta-section .section-lead{margin:0 auto 2rem}
footer{background:#090e15;border-top:1px solid #232a37;padding:2rem 0;color:#8e9aac;font-size:.85rem}.footer-inner{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}
@media(max-width:991px){.nav-links{display:none}.mobile-nav{display:block;position:relative}.mobile-nav[open] .menu-content{display:flex}.menu-content{position:absolute;right:0;top:calc(100% + .7rem);width:min(80vw,240px);display:none;flex-direction:column;gap:.9rem;background:#1a2330;border:1px solid #415066;border-radius:13px;padding:1.1rem;box-shadow:0 15px 45px #0007}.services-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.hero-art{opacity:.3;right:-30vw}}
@media(max-width:650px){.hero{padding:5rem 0}.hero h1{font-size:clamp(2.7rem,11vw,4rem)}.services-grid,.about-wrap,.step-list{grid-template-columns:1fr}.hero-art{opacity:.18}.nav-inner{min-height:68px}.hero-actions{gap:1.2rem}.section-pad{padding:4.5rem 0}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{transition:none!important;animation:none!important}}
"""


def create_project(spec: dict, public_brief: str) -> tuple[bytes, str, dict[str, bytes]]:
    """Return a deployable multi-file static ZIP, index HTML, and local assets."""
    data = validate_spec(spec, public_brief)
    ink, light, surface = PALETTES[data["theme"]]
    css = (_OVERRIDES.replace("ACCENTSOFT", light).replace("ACCENT", ink)
           .replace("SURFACE", surface))
    def e(value: str) -> str:
        return escape(value, quote=True)
    service_blocks = "".join(
        f'<article class="service-card"><span class="card-num">{i:02d} / {len(data["services"]):02d}</span>'
        f'<h3>{e(title)}</h3><p>{e(description)}</p></article>'
        for i, (title, description) in enumerate(data["services"], 1))
    step_blocks = "".join(
        f'<article class="step"><span class="card-num">STEP {i:02d}</span>'
        f'<h3>{e(title)}</h3><p>{e(description)}</p></article>'
        for i, (title, description) in enumerate(data["steps"], 1))
    initials = "".join(part[0] for part in data["brand"].split()[:2]).upper()
    cta_target = e(data["contact_url"])
    cta_rel = ' rel="noopener noreferrer"' if cta_target.startswith("https:") else ""
    nav_contact = (f'<a class="nav-cta" href="{cta_target}"{cta_rel}>{e(data["contact_label"])}</a>'
                   if data["contact_enabled"] else '')
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(data['brand'])} — {e(data['eyebrow'])}</title><meta name="description" content="{e(data['intro'])}">
<meta name="theme-color" content="#0d111a"><link rel="stylesheet" href="assets/framework.css"><link rel="stylesheet" href="assets/custom.css"></head>
<body><a class="visually-hidden-focusable" href="#main">Skip to content</a>
<header class="site-nav"><nav class="container nav-inner" aria-label="Primary navigation">
<a class="wordmark" href="#top"><span class="brand-symbol" aria-hidden="true">{e(initials)}</span><span>{e(data['brand'])}</span></a>
<div class="nav-links"><a href="#services">Services</a><a href="#about">About</a><a href="#approach">Approach</a>{nav_contact}</div>
<details class="mobile-nav"><summary aria-label="Toggle menu">Menu ☰</summary><div class="menu-content"><a href="#services">Services</a><a href="#about">About</a><a href="#approach">Approach</a>{nav_contact}</div></details>
</nav></header><main id="main"><section class="hero" id="top"><div class="hero-art" aria-hidden="true"></div><div class="container">
<div class="hero-copy"><div class="eyebrow">{e(data['eyebrow'])}</div><h1>{e(data['headline'])}</h1>
<p class="hero-description">{e(data['intro'])}</p><div class="hero-actions"><a class="primary-link" href="{cta_target}"{cta_rel}>{e(data['contact_label'])} <span aria-hidden="true">&nbsp; ↗</span></a>
<a class="text-link" href="#about">Learn more</a></div></div></div></section>
<section class="services section-pad" id="services"><div class="container"><p class="section-kicker">What we offer</p>
<h2 class="section-title">{e(data['services_heading'])}</h2><p class="section-lead muted">{e(data['intro'])}</p>
<div class="services-grid">{service_blocks}</div></div></section>
<section class="section-pad" id="about"><div class="container about-wrap"><div class="about-mark" aria-hidden="true"><span>{e(initials)}</span></div>
<div><p class="section-kicker">Get to know us</p><h2 class="section-title">{e(data['about_heading'])}</h2>
<p class="section-lead muted">{e(data['about'])}</p></div></div></section>
<section class="services section-pad" id="approach"><div class="container"><p class="section-kicker">Our approach</p>
<h2 class="section-title">{e(data['approach_heading'])}</h2><div class="step-list">{step_blocks}</div></div></section>
<section class="cta-section"><div class="container"><p class="section-kicker">Explore what's possible</p>
<h2 class="section-title">{e(data['cta_heading'])}</h2><p class="section-lead muted">{e(data['intro'])}</p>
<a class="primary-link" href="{cta_target}"{cta_rel}>{e(data['contact_label'])} <span aria-hidden="true">&nbsp; ↗</span></a></div></section>
</main><footer><div class="container footer-inner"><span>{e(data['brand'])}</span><span>Made with care for every screen.</span></div></footer></body></html>'''
    assets = {"assets/framework.css": (RESOURCE_DIR / "output.css").read_bytes(),
              "assets/custom.css": css.encode("utf-8"),
              "assets/LICENSE": (RESOURCE_DIR / "LICENSE").read_bytes()}
    readme = (f"{data['brand']} - static website source\n\nOpen index.html in a browser or "
              "deploy these files to any static web host. No hosting or domain is included.\n"
              "Responsive CSS foundation: Landwind Tailwind CSS, MIT licensed.\n"
              "No external fonts, scripts, trackers or paid dependencies.\n")
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html.encode("utf-8"))
        for path, value in assets.items():
            archive.writestr(path, value)
        archive.writestr("README.txt", readme)
    return output.getvalue(), html, assets
