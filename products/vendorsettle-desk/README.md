# VendorSettle Desk — Consignment Payout Worksheet

A **vendor settlement preparation workflow** for small consignment shops, vintage sellers, galleries, and shared retail spaces. Independent project; not affiliated with any POS provider.

## Free starter template

Use [sample.csv](./sample.csv) as a column-by-column template. Replace the fictional transactions with your own **de-identified** records. One line represents one item sale or refund. Do not include customer names, payment card numbers, bank information, addresses, or tax IDs.

- `line_id` must be unique per item or refund. `transaction_id` may repeat across different items.
- `gross_usd`, `refund_usd`, and fees are nonnegative dollar amounts with two decimal places.
- `consignor_share_pct` is the percentage of **net sales after listed fees** owed to the consignor. Check your actual contract: some splits are calculated on gross, and fee allocation varies.
- Refund-only rows use gross 0 and positive refund; these can create negative settlement adjustments.
- Draft due per line = ROUND_HALF_UP((gross - refund - processing_fee - other_fee) × share%).
- Store retained = net minus consignor due. Sum **rounded line amounts**, not unrounded subtotals.
- Excluded, invalid, or missing records must be reviewed before any payment.

## One-time prepared statement service

**VendorSettle Desk Settlement Report Service** is a planned $49 one-time offer for up to 500 normalized rows and 10 consignors: draft itemized statements, a summary CSV, and a row-exception report. One correction pass within 7 days; target turnaround three business days after complete inputs. This is a calculation and record-preparation service, **not** tax advice, an accounting attestation, POS integration, or a money-transfer service.

**Checkout is not yet available. Do not send private files or pay anyone claiming to represent this project.** Questions may be sent to `ari-operations@agentmail.to`; no unsolicited marketing is sent from this address.

## Status and limitations

2026-10-11: Local Python processing prototype completed 8/8 deterministic tests with fictional data; no buyer, live checkout, or payment has been verified. A free executable version and a hosted order flow remain publication work. Never treat the example totals as real funds owed or paid.

The real legal merchant shown by a future checkout may differ from the product name. This project makes no representation of affiliation with the platform where a retailer originally processed sales.
