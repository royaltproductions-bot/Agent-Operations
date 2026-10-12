# DocTriage Ledger — PDF inventory and human-review queue

A local-first inventory for teams managing hundreds or thousands of PDF documents. Generates one CSV row per PDF with SHA-256, page count, observed tag-tree presence, document language/title metadata, extractable-text coverage, direct image XObject count, annotations, and a **triage priority**. The program never uploads documents.

**This is NOT an accessibility checker, WCAG audit, PDF/UA certification, OCR engine, or legal determination.** A tagged PDF may still be inaccessible. A page with extractable text can still be a scanned page with an OCR layer. Image counts omit nested Form XObjects. Human evaluation remains necessary.

## Install and run

```bash
python -m pip install 'pypdf>=5,<7'
python doctriage.py /path/to/pdfs --output inventory.csv
```

Python 3.10+, offline, recursive `.pdf` scan. Encrypted and malformed files get `needs_access` rather than disappearing. CSV is UTF-8 BOM; spreadsheet-formula-looking strings are escaped.

## Priority labels

* `high`: no marked tag tree observed or no extractable text on any page.
* `medium`: some pages lack extractable text or document language/title metadata absent.
* `baseline`: no listed triage flags, but **human review still required**.
* `needs_access`: encrypted without access, unreadable, or parse error.

Priority is a **review queue**, not a finding of noncompliance. Legal applicability and document exemptions require separate analysis.

## Possible commercial offering — NOT yet accepting orders

Fixed-scope service proposal: inventory up to 100 customer-authorized PDFs, return CSV and sorted human-review queue within two business days after receiving the documents. Excludes document repair, OCR, compliance certification, legal advice, and any unsupported promise of secure document handling. **No checkout or paid delivery is active until transfer, terms, and fulfillment are operational.**

## Independent market check, 2026-10-11

* Charlotte County, Florida's 2026 accessibility RFP addendum mentioned approximately 5,350 PDFs and explicitly raised risk-based document assessment with documented triage. The solicitation **closed Oct 6, 2026**: evidence of a problem, **not a live lead**. https://www.governmentcontracts.us/government-contracts/opportunity-details/48929780881918855.htm
* A Texas procurement listing describes an accessibility auditing subcontract due Oct 23, 2026, but is part of a broader Infor implementation. Eligibility and a direct subcontract route **not verified**. https://www.cleat.ai/government/contracts/digital-accessibility-ada-wcag-auditor-zd4x
* DOJ's April 2026 interim rule extended ADA Title II web/app deadlines to Apr 26, 2027 for larger public entities and Apr 26, 2028 for smaller entities/special districts. https://www.ada.gov/title-ii-web-rule/
* PAC warns that machine checks do not replace human accessibility evaluation. https://pac.pdf-accessibility.org/en/check

Government procurement demand does not establish demand for this specific script or service. Free competing tools exist. **No paying buyer, order, distribution partner, or revenue verified.**

## License

Copyright (c) 2026 DocTriage Ledger contributors. All rights reserved. Private evaluation permitted; commercial redistribution, rebranding, or resale requires written permission. Dependencies retain their own licenses.
