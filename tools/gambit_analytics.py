"""Deterministic arithmetic for buyer-supplied CSV files.

Language models draft the explanation; Python checks the underlying numbers.
Only bounded, in-memory CSV data is inspected. No buyer code is executed.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import io


def _number(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    # Financial spreadsheets frequently use thousands separators and dollars.
    if raw.startswith("$"):
        raw = raw[1:]
    raw = raw.replace(",", "")
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _display(value):
    return format(value, "f")


def summarize_csv_sources(sources):
    """Return exact counts and numeric column statistics for well-formed CSVs.

    Unknown/mixed data is omitted rather than silently treated as zero. These
    numbers are tool-calculated evidence, never claims of independently
    verified financial figures.
    """
    reports = []
    for source in (sources or [])[:4]:
        filename = str(source.get("filename") or "")
        if not filename.lower().endswith(".csv"):
            continue
        data = str(source.get("text") or "")
        try:
            reader = csv.DictReader(io.StringIO(data, newline=""))
            headers = reader.fieldnames or []
            if not headers or len(headers) > 30 or len(set(headers)) != len(headers):
                continue
            rows = []
            for row in reader:
                if len(rows) >= 500 or None in row:
                    rows = []
                    break
                if any(value is None for value in row.values()):
                    rows = []
                    break
                rows.append(row)
        except (csv.Error, UnicodeError):
            continue
        if not rows:
            continue
        label_field = next((h for h in headers if h and all(
            _number(row.get(h)) is None for row in rows)), None)
        numbers = []
        for field in headers[:8]:
            parsed = [_number(row.get(field)) for row in rows]
            valid = [(index, value) for index, value in enumerate(parsed)
                     if value is not None]
            # Every populated value must be numeric; missing values are
            # counted and disclosed rather than implicitly zero-filled.
            if not valid or any(row.get(field, "").strip() and value is None
                                for row, value in zip(rows, parsed)):
                continue
            total = sum((value for _, value in valid), Decimal(0))
            high = max(value for _, value in valid)
            low = min(value for _, value in valid)
            highest = [index for index, value in valid if value == high]
            lowest = [index for index, value in valid if value == low]
            entry = {"column": field, "numeric_rows": len(valid),
                     "missing_rows": len(rows) - len(valid),
                     "sum": _display(total),
                     "mean": _display(total / Decimal(len(valid))),
                     "minimum": _display(low), "maximum": _display(high)}
            if label_field:
                entry["highest_labels"] = [rows[i][label_field] for i in highest[:6]]
                entry["lowest_labels"] = [rows[i][label_field] for i in lowest[:6]]
                entry["label_column"] = label_field
            numbers.append(entry)
        if numbers:
            reports.append({"filename": filename[:100], "data_rows": len(rows),
                            "columns": numbers})
    return reports
