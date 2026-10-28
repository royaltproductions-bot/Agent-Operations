"""One-time real-model benchmark; no live Bounty, credentials, or paid API calls.

Executes the same Qwen work-generation path as production against fully known
offline source evidence. Exits nonzero if the model fabricates or omits rows.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from zipfile import ZipFile

from tools.gambit_sites import pack_static_site
from tools.gambit_visual_qa import render_browser_evidence

from tools import gambit_worker as worker


def research_case():
    source_data = [
        ("https://example.org/acme", "Acme Materials", "Augusta"),
        ("https://example.com/bravo", "Bravo Supply", "Atlanta"),
        ("https://example.net/cobalt", "Cobalt Services", "Savannah"),
    ]
    evidence = {"status": "available", "pages_read": 3, "results": [
        {"url": url, "title": name, "search_query": "Georgia suppliers",
         "snippet": "", "evidence_level": "page_read",
         "page_text": f"{name} is a supplier located in {city}, Georgia."}
        for url, name, city in source_data]}
    bounty = {"bounty": {
        "title": "Compile three sourced Georgia supplier records",
        "description": "Using only the supplied evidence, prepare a complete CSV with "
                       "exactly three rows and columns company,city,source_url. "
                       "Each row must reflect the source page and include its exact URL. "
                       "No invented company names or websites.",
        "verification": "Three distinct source-backed rows and a valid CSV header."}}
    result = worker.produce(bounty, [], research=evidence)
    if result.get("status") != "ready" or result.get("deliverable_format") != "csv":
        raise AssertionError("research_model_did_not_produce_csv")
    content = worker.prepare_csv(result.get("deliverable_text", "")).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(content)))
    expected = {(name.lower(), city.lower(), url)
                for url, name, city in source_data}
    if len(rows) != 3 or set(rows[0]) != {"company", "city", "source_url"}:
        raise AssertionError("research_wrong_schema_or_row_count")
    observed = {(row["company"].strip().lower(), row["city"].strip().lower(),
                 row["source_url"].strip()) for row in rows}
    if observed != expected:
        raise AssertionError("research_hallucinated_or_omitted_source_record")
    return {"case": "sourced_three_record_csv", "result": "passed", "rows": len(rows)}


def analysis_case():
    bounty = {"bounty": {
        "title": "Compute quarterly sales summary",
        "description": "Use only the attached source table. Report the total of the three "
                       "months, the monthly average, and the highest month. "
                       "Do not introduce any external figures.",
        "verification": "Arithmetic agrees with the supplied table and names the correct peak month."}}
    sources = [{"filename": "sales.csv", "text":
                "month,sales\nJanuary,147\nFebruary,92\nMarch,181\n"}]
    result = worker.produce(bounty, [], sources=sources,
                            research={"status": "not_needed", "results": []})
    if result.get("status") != "ready" or result.get("deliverable_format") not in {
            "text", "csv", "xlsx"}:
        raise AssertionError("analysis_model_not_ready: " + repr({
            "status": result.get("status"), "format": result.get("deliverable_format"),
            "reason": str(result.get("reason") or "")[:150],
            "preview": str(result.get("deliverable_text") or "")[:500]}))
    content = str(result.get("deliverable_text") or "")
    if result.get("deliverable_format") in {"csv", "xlsx"}:
        worker.prepare_csv(content)
    for value in ("420", "140", "March", "181"):
        if not re.search(rf"\b{re.escape(value)}\b", content, re.I):
            raise AssertionError("analysis_missing_expected_result: " + repr(content[:900]))
    return {"case": "three_month_analysis", "result": "passed"}


def static_site_case():
    """Use the production LLM and real bundler, without touching a paid Bounty."""
    bounty = {"bounty": {
        "title": "Build a static HTML landing page for Summit Soap",
        "description": "Create one self-contained, mobile-responsive HTML landing page "
                       "for a fictional handmade soap brand called Summit Soap. "
                       "Include a headline, product benefits section and a visible "
                       "Contact Us call to action. Use embedded CSS and no external "
                       "fonts, images, scripts, or network requests.",
        "verification": "Deliver a ZIP containing the standalone index.html source, "
                        "with responsive CSS and no server or live hosting."}}
    result = worker.produce(bounty, [], research={"status": "not_needed", "results": []})
    if result.get("status") != "ready" or result.get("deliverable_format") != "site_zip":
        raise AssertionError("static_site_model_wrong_output_format: " + repr({
            "status": result.get("status"), "format": result.get("deliverable_format"),
            "reason": str(result.get("reason") or "")[:180]}))
    archive = pack_static_site(str(result.get("deliverable_text") or ""))
    import io
    with ZipFile(io.BytesIO(archive)) as bundle:
        html = bundle.read("index.html").decode("utf-8")
    if ("summit soap" not in html.lower() or "contact us" not in html.lower() or
            "<style" not in html.lower()):
        raise AssertionError("static_site_missing_required_content")
    preview_dir = os.environ.get("GAMBIT_BENCHMARK_PREVIEW_DIR")
    viewports = render_browser_evidence(html, preview_dir=preview_dir)
    return {"case": "self_contained_static_site", "result": "passed",
            "zip_bytes": len(archive),
            "rendered_viewports": list(viewports),
            "desktop_heading_px": viewports["desktop"]["heading_px"],
            "mobile_heading_px": viewports["mobile"]["heading_px"]}


if __name__ == "__main__":
    outcomes = []
    for run_case in (research_case, analysis_case, static_site_case):
        try:
            outcomes.append(run_case())
        except Exception as exc:
            outcomes.append({"case": run_case.__name__, "result": "failed",
                             "reason": type(exc).__name__ + ":" + str(exc)[:1000]})
    print(json.dumps({"gambit_real_model_benchmark": outcomes}))
    if any(item["result"] != "passed" for item in outcomes):
        raise SystemExit(1)
