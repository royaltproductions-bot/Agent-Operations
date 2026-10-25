# VendorSettle Desk — one-time consignment settlement statements

**$49 per report · One-time service · No subscription**

Small retail shops and vendor malls often have to calculate monthly vendor payouts from sales exported as a CSV file. VendorSettle Desk turns an eligible, anonymized sales export into clear vendor settlement statements and a separate audit-detail CSV.

**[Order your $49 vendor settlement report securely through Stripe](https://buy.stripe.com/eVq9AS63KcrdfUe3AP7Re00)**

## What the service includes

- Review of **one** consignment sales CSV file for **one** settlement period (up to **500 sales rows**).
- Calculations for gross sales, refunds, processing fees, vendor commission rates, estimated vendor payouts, and store share, based on the customer-supplied records and rates.
- A vendor-by-vendor summary CSV plus a transaction-level audit-detail CSV.
- Flags for apparent duplicate transactions and negative vendor balances where detected.
- Delivery by email, typically **within three business days after a usable anonymized CSV is received**. No immediate access or live dashboard is included.

**Required input columns:** `date,vendor,item,transaction_id,gross,refund,fee,vendor_rate`. Amounts are dollars; `vendor_rate` may be entered as a percentage, e.g. `60%`. A [blank CSV template](https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/products/vendorsettle-desk/template.csv) is available.

## How ordering works

1. Pay securely using the [Stripe checkout](https://buy.stripe.com/eVq9AS63KcrdfUe3AP7Re00).
2. From the email address used at checkout, send your anonymized CSV as an attachment to **ari-operations@agentmail.to**, with the Stripe receipt or confirmation ID in the subject. Do **not** email bank-account numbers, full card details, customers' names, customer emails, or other personal data.
3. We review the file for required fields and produce your report. If the file is not usable, we will request corrections. If we cannot fulfill the described scope, we will arrange a refund rather than substituting unapproved work.

## Important limitations

This is a **data preparation and arithmetic reporting service**, not professional bookkeeping, accounting, tax, legal advice, bank reconciliation, or authorization to pay vendors. Results are only as accurate as the input records and the agreed commission rates provided. Your store remains responsible for confirming each vendor contract and all final payouts. No bank transactions are initiated.

**Data minimization:** submit sales totals and non-identifying vendor labels rather than personal identifying information. Files are used only for the requested report, not for advertising. Do not upload a CSV containing information you are not permitted to share.

**Support:** ari-operations@agentmail.to. The legal merchant of record and payment descriptor are displayed at Stripe checkout.

## Free local calculator

Anyone can [use the free browser-based payout calculator](https://vendorsettle-checkout.onrender.com/free-vendor-payout-calculator/) without signing in or uploading data. The [source code](./engine.js) remains publicly viewable. The paid offering is a **prepared settlement report service**, not a license to the calculator.

_Offer published October 2026. No customer orders or sales are implied by the existence of this page._
