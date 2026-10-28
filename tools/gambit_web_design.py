"""Thin adapter to an existing website-design service (v0 Platform API v1).

Gambit supplies a public brief; v0 owns design and code generation. This module
only manages the API contract, packages its returned files, and reports a
preview. It never deploys, spends beyond a provider's available credits, or
claims a website is ready without a completed generation. An account API key is
required; the ChatGPT connector cannot authenticate an unattended worker.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import os
from pathlib import PurePosixPath
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from zipfile import ZIP_DEFLATED, ZipFile

API_ROOT = "https://api.v0.dev/v1"
MAX_FILES = 120
MAX_FILE_BYTES = 500_000
MAX_PROJECT_BYTES = 6_000_000


@dataclass(frozen=True)
class WebsiteResult:
    archive: bytes
    preview_url: str | None
    chat_id: str
    file_count: int


def connected() -> bool:
    """An available credential enables requests, not a claim of proven quality."""
    return bool(os.environ.get("V0_API_KEY"))


def _request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    if not re.fullmatch(r"/chats(?:/[A-Za-z0-9_-]+(?:/versions/[A-Za-z0-9_-]+)?)?", path):
        raise ValueError("v0_path_not_allowed")
    secret = os.environ.get("V0_API_KEY")
    if not secret:
        raise RuntimeError("v0_not_connected")
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        API_ROOT + path + ("?includeDefaultFiles=true" if "/versions/" in path else ""),
        data=raw, method=method,
        headers={"Authorization": "Bearer " + secret,
                 "Accept": "application/json", "Content-Type": "application/json",
                 "User-Agent": "AriOperations-Gambit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=110) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not echo provider bodies, user prompts, request URLs or credentials.
        raise RuntimeError(f"v0_http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("v0_unreachable") from None


def _valid_project_path(name: str) -> bool:
    if not name or len(name) > 200 or "\\" in name or "\x00" in name:
        return False
    parts = PurePosixPath(name).parts
    return not name.startswith("/") and all(part not in {"", ".", ".."} for part in parts) and not (
        any(part in {".git", "node_modules"} for part in parts)
        or parts[-1] in {".env", ".env.local", ".env.production"})


def package_project(files: list[dict]) -> tuple[bytes, int]:
    """Retain the specialist's real multi-file project, without running its code."""
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("v0_invalid_file_count")
    output, seen, total = io.BytesIO(), set(), 0
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for item in files:
            name, content = item.get("name"), item.get("content")
            if not isinstance(name, str) or not _valid_project_path(name) or name in seen:
                raise ValueError("v0_unsafe_project_path")
            if not isinstance(content, str):
                raise ValueError("v0_nontext_source_file")
            encoded = content.encode("utf-8")
            total += len(encoded)
            if len(encoded) > MAX_FILE_BYTES or total > MAX_PROJECT_BYTES:
                raise ValueError("v0_project_too_large")
            archive.writestr(name, encoded)
            seen.add(name)
    if not any(name in seen for name in ("package.json", "index.html")):
        raise ValueError("v0_missing_project_entrypoint")
    return output.getvalue(), len(seen)


def _site_prompt(brief: dict) -> str:
    """Never export private messages, attachments, customer emails, or secrets."""
    title = str(brief.get("title") or "")[:200]
    description = str(brief.get("description") or "")[:6000]
    verification = str(brief.get("verification") or "")[:2000]
    if not title or not description:
        raise ValueError("website_brief_incomplete")
    return ("Build the complete, professionally designed, responsive website "
            "specified below. Deliver ALL real source files and a working preview. "
            "Use polished contemporary typography, hierarchy, spacing, visuals, "
            "mobile navigation, accessibility and attractive mobile layouts. "
            "Write concrete buyer-specific copy. Never invent testimonials, "
            "customers, metrics, credentials or reviews. Do not add forms with "
            "nonfunctional submit buttons, dummy links, paid dependencies, "
            "tracking code, or bogus integrations. Only implement features the "
            "brief supports. If required business content/assets are missing, "
            "make the missing items explicit and do not falsely claim completion. "
            "\nPUBLIC JOB TITLE: " + title + "\nPUBLIC BRIEF: " + description +
            "\nACCEPTANCE CRITERIA: " + verification)


def generate_from_resource(brief: dict, *, max_wait_seconds: int = 200) -> WebsiteResult:
    """Invoke the existing design platform; never substitute local HTML invention."""
    if not connected():
        raise RuntimeError("v0_not_connected")
    payload = {"message": _site_prompt(brief), "chatPrivacy": "private",
               "responseMode": "sync"}
    response = _request("/chats", "POST", payload)
    chat_id = str(response.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,100}", chat_id):
        raise RuntimeError("v0_missing_chat_id")
    deadline = time.monotonic() + max_wait_seconds
    while True:
        version = response.get("latestVersion") or {}
        if version.get("status") == "failed":
            raise RuntimeError("v0_generation_failed")
        if version.get("status") == "completed":
            version_id = str(version.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,100}", version_id):
                raise RuntimeError("v0_missing_version_id")
            full_version = _request(f"/chats/{chat_id}/versions/{version_id}")
            if full_version.get("status") != "completed":
                raise RuntimeError("v0_version_not_ready")
            archive, count = package_project(full_version.get("files") or [])
            preview = full_version.get("demoUrl") or version.get("demoUrl")
            if not (isinstance(preview, str) and
                    urllib.parse.urlsplit(preview).scheme == "https"):
                preview = None
            return WebsiteResult(archive, preview, chat_id, count)
        if time.monotonic() >= deadline:
            raise RuntimeError("v0_generation_timeout")
        time.sleep(min(4, max(0, deadline - time.monotonic())))
        response = _request(f"/chats/{chat_id}")
