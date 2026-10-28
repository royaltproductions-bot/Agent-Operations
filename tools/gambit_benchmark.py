"""One-time real-model benchmark; no live Bounty, credentials, or paid API calls.

Executes the same Qwen work-generation path as production against fully known
offline source evidence. Exits nonzero if the model fabricates or omits rows.
"""
from __future__ import annotations

import csv
import io
import json
import re

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


if __name__ == "__main__":
    outcomes = []
    for run_case in (research_case, analysis_case):
        try:
            outcomes.append(run_case())
        except Exception as exc:
            outcomes.append({"case": run_case.__name__, "result": "failed",
                             "reason": type(exc).__name__ + ":" + str(exc)[:1000]})
    print(json.dumps({"gambit_real_model_benchmark": outcomes}))
    if any(item["result"] != "passed" for item in outcomes):
        raise SystemExit(1)
