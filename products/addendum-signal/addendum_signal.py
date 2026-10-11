#!/usr/bin/env python3
"""Addendum Signal: compare tender/RFP text revisions locally.

Usage: python3 addendum_signal.py original.txt revised.txt --csv changes.csv
No network calls, API keys, or third-party dependencies.
"""
import argparse
import csv
import difflib
import re
from pathlib import Path

PATTERNS = {
    "deadline": r"deadline|due date|submit by|submission date|no later than|closing date",
    "eligibility": r"eligib|certif|licens|registration|qualified|experience",
    "pricing": r"pric|cost|fee|budget|rate|bid bond",
    "insurance": r"insur|liability|coverage|bonding",
    "attachments": r"attachment|appendix|annex|exhibit|form|schedule",
    "mandatory": r"shall|must|required|mandatory",
}

def clauses(text):
    return [re.sub(r"\s+", " ", line).strip()
            for line in text.splitlines() if len(line.strip()) > 5]

def labels(text):
    return [k for k, pattern in PATTERNS.items() if re.search(pattern, text, re.I)]

def compare(before, after):
    a, b = clauses(before), clauses(after)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    output = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        left, right = a[i1:i2], b[j1:j2]
        for n in range(max(len(left), len(right))):
            old = left[n] if n < len(left) else ""
            new = right[n] if n < len(right) else ""
            if old == new:
                continue
            kind = "modified" if old and new else ("removed" if old else "added")
            cats = labels(old + " " + new)
            priority = "urgent" if "deadline" in cats else ("high" if cats else "review")
            output.append((kind, priority, "; ".join(cats), old, new))
    return output

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("revised", type=Path)
    parser.add_argument("--csv", type=Path, default=Path("addendum-review.csv"))
    args = parser.parse_args()
    rows = compare(args.original.read_text(encoding="utf-8"),
                   args.revised.read_text(encoding="utf-8"))
    with args.csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Change", "Priority", "Categories", "Original", "Revised"])
        for row in rows:
            writer.writerow([("'" + value if re.match(r"^\s*[=+@-]", value) else value)
                             for value in row])
    print(f"Compared files: {len(rows)} review items. Export: {args.csv}")
    for kind, priority, categories, old, new in rows:
        print(f"[{priority.upper()}] {kind}: {categories} | {old[:60]} -> {new[:60]}")

if __name__ == "__main__":
    main()
