# Payment Anomaly Auditor

**Offline audit engine for duplicate vendor payments.** Built as an initial asset in Agent-Operations.

## The problem
Small businesses can accidentally pay the same supplier invoice twice or process unusually similar transactions. An external AI chatbot may require uploading sensitive bookkeeping records. This program runs locally and uses deterministic, auditable checks without transmitting company data anywhere.

## What actually works today
- Ingest payment CSVs with header aliases (vendor/supplier/payee, invoice ID, date, amount, status).
- Flag *potential* duplicate invoice IDs from the same vendor with the same amount, even if invoice punctuation differs.
- Flag separate invoice IDs with matching vendor, payment date, and amount for additional review.
- Identify malformed rows and skip refunds, negative amounts and unfinished statuses.
- Return row numbers, amounts and matched invoice references, plus a JSON audit report.
- Self-tests use built-in Node.js tools and no third-party packages.

**It does not verify overpayment, determine that money is recoverable, or automatically contact vendors.** Financial findings must be reconciled against original documents.

## Local use
Requires Node.js 20+.
```bash
node products/payment-anomaly-auditor/test.mjs
node products/payment-anomaly-auditor/audit.mjs products/payment-anomaly-auditor/examples/payments.csv --output findings.json
```
The example data is entirely fictional. No user business records or customer information are in the repository.

## Commercial hypothesis, not a sale
Potential buyers: small-business bookkeepers, finance administrators, and audit contractors who need quick repeatable screening without uploading confidential records to third-party AI systems.

**Reason to consider paying:** offline processing, repeatable scanning, evidence-linked reporting and reliable handling of long CSV exports. Buyers can already obtain some of this through spreadsheets or code; this hypothesis must be validated against them before pricing or selling.

**Stage:** working prototype. **Confirmed revenue:** $0. **Paying demand:** not yet validated. **Distribution:** public source code only; not yet hosted or sold. **Payout:** Stripe available through the operator, no checkout link created.

## Next material milestone
Validate demand and construct a customer-ready offline package with a high-quality export/report. Determine defensible commercial differentiation and a repeatable acquisition channel before charging. No unsupported claims of earnings.
