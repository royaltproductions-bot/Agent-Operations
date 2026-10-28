"""Build and check self-contained static websites for commercial delivery.

The builder accepts one complete HTML document with embedded CSS/JS. It does
not run buyer/model JavaScript, publish anything, or access client credentials.
The result is a ready-to-open ZIP containing a single index.html and README.
"""
from __future__ import annotations

from html.parser import HTMLParser
import io
import re
import shutil
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

MAX_HTML_BYTES = 120_000
MAX_BUNDLE_BYTES = 250_000
VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "track", "wbr"})
UNSAFE_ELEMENTS = frozenset({"iframe", "object", "embed", "form", "base", "frame", "frameset"})


class SiteParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.stack = []
        self.scripts = []
        self.current_script = None
        self.errors = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.tags.append(tag)
        if tag in UNSAFE_ELEMENTS:
            self.errors.append("unsupported_site_element")
        for key, value in attrs:
            name, val = (key or "").lower(), (value or "").strip()
            if (name.startswith("on") or name in {"srcdoc", "integrity"} or
                    val.lower().startswith(("javascript:", "data:"))):
                self.errors.append("unsafe_site_attribute")
            if ((name == "src" or (name == "href" and tag not in {"a"}))
                    and val and not val.startswith("#")):
                # The ZIP includes exactly one HTML asset: all its CSS and
                # JavaScript must be inline and images cannot be unbundled.
                self.errors.append("site_missing_or_external_asset")
            if name == "href" and tag == "a" and val.startswith(("//", "/")):
                self.errors.append("site_server_relative_link")
        if tag == "script":
            if any(k.lower() == "src" for k, _ in attrs):
                self.errors.append("external_script_disallowed")
            self.current_script = []
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script" and self.current_script is not None:
            self.scripts.append("".join(self.current_script))
            self.current_script = None
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("site_unbalanced_markup")
            return
        self.stack.pop()

    def handle_data(self, data):
        if self.current_script is not None:
            self.current_script.append(data)


def static_site_eligible(details):
    """Only explicit static/front-end builds; no auth, database or live hosting."""
    bounty = details.get("bounty") or {}
    joined = " ".join(str(bounty.get(name) or "") for name in
                      ("title", "description", "verification")).lower()
    if not re.search(r"\b(?:static|landing page|one.page|single.page|html|css|"
                     r"front.end|portfolio page|webpage mockup)\b", joined):
        return False
    if re.search(r"\b(?:backend|back.end|database|login|authentication|server.side|"
                 r"payments?|stripe|shopify|wordpress|user accounts?|api integration|"
                 r"production deployment)\b", joined):
        return False
    return True


def pack_static_site(html_text):
    """Check syntax and package site without executing untrusted code."""
    raw = html_text.strip().encode("utf-8")
    if not 250 <= len(raw) <= MAX_HTML_BYTES:
        raise ValueError("static_site_invalid_length")
    if not re.search(r"^\s*<!doctype\s+html\s*>", html_text, re.I):
        raise ValueError("static_site_doctype_missing")
    parsed = SiteParser()
    parsed.feed(html_text)
    parsed.close()
    if parsed.errors or parsed.stack or not {"html", "head", "title", "body"} <= set(parsed.tags):
        raise ValueError("static_site_markup_invalid")
    if not re.search(r"<meta\s+[^>]*name\s*=\s*['\"]viewport['\"]", html_text, re.I):
        raise ValueError("static_site_viewport_missing")
    if re.search(r"\b(?:fetch\s*\(|XMLHttpRequest\b|WebSocket\b|EventSource\b|eval\s*\(|"
                 r"new\s+Function\s*\(|document\.cookie\b|localStorage\b|sessionStorage\b)",
                 html_text, re.I):
        raise ValueError("static_site_unsupported_script_behavior")
    if re.search(r"@import\s+|url\s*\(\s*['\"]?https?://", html_text, re.I):
        raise ValueError("static_site_external_css")
    if parsed.scripts:
        if not shutil.which("node"):
            raise ValueError("static_site_js_validator_missing")
        for script in parsed.scripts:
            if not script.strip():
                continue
            check = subprocess.run(["node", "--check", "-"], input=script,
                                   text=True, capture_output=True, timeout=10)
            if check.returncode != 0:
                raise ValueError("static_site_javascript_invalid")
    out = io.BytesIO()
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("index.html", raw)
        archive.writestr("README.txt", ("Self-contained static website.\n"
                                         "Open index.html in a modern web browser.\n"
                                         "No hosting or production deployment is included.\n"))
    data = out.getvalue()
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("static_site_bundle_too_large")
    with ZipFile(io.BytesIO(data)) as verify:
        if set(verify.namelist()) != {"index.html", "README.txt"} or verify.read("index.html") != raw:
            raise ValueError("static_site_bundle_invalid")
    return data
