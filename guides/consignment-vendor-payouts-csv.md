# How to calculate consignment vendor payouts from a CSV

*A practical worked example for shop operators and vendor malls. Not accounting, tax, or legal advice.*

A common monthly reconciliation problem: a sales export has multiple vendors, mixed commission rates, refunds and processing fees. The payout should be reproducible for each item and auditable for each vendor.

## Worked transaction

Suppose a sale is **$100**, the customer later receives a **$10 refund**, there is a **$3 processing fee**, and the contract says the vendor receives **60% of the net distributable amount**.

| Component | Amount |
| --- | ---: |
| Gross sale | $100.00 |
| Less refund | -$10.00 |
| Less processing fee | -$3.00 |
| Net distributable | $87.00 |
| Vendor share (60% of $87) | **$52.20** |
| Store retained (40% of $87) | **$34.80** |

This is one example of a fee-allocation rule. **Your actual vendor agreement governs** whether fees are deducted before commission, split separately, or absorbed by the store. Do not assume this formula is contractually correct for every retailer.

## Example CSV input

Save the following as a UTF-8 CSV. Use vendor labels, not customers' names or identifiers.

```csv
date,vendor,item,transaction_id,gross,refund,fee,vendor_rate
2026-10-01,V001,Scented candle,S001,100.00,10.00,3.00,60%
2026-10-02,V002,Handmade soap,S002,24.00,0.00,0.72,70%
```

A complete monthly settlement should include a vendor total and separate transaction audit, with flags for possible duplicate rows and negative net payouts. Before paying vendors, review exceptions and reconcile the export against original records.

## Common mistakes to check

1. Accidentally including returned or canceled sales in gross payouts.
2. Applying a shop-wide vendor rate to a vendor with a different agreement.
3. Counting the same receipt or transaction twice.
4. Deducting processing fees twice or from the wrong party.
5. Mixing multiple settlement periods in one upload.
6. Sending personal customer or financial identifiers to external processors unnecessarily.

For private local use, the [free browser-only VendorSettle calculation engine](../products/vendorsettle-desk/index.html) and [calculation source](../products/vendorsettle-desk/engine.js) are publicly available. Download and run locally; viewing HTML in a GitHub file preview is not a hosted web application.

If you want a **prepared vendor settlement summary plus audit CSV**, the existing [VendorSettle Desk report service](../products/vendorsettle-desk/README.md) is **$49 for one reporting period with up to 500 sales rows**, delivered by email after you provide an anonymized file. [Review the terms and order through Stripe](https://buy.stripe.com/eVq9AS63KcrdfUe3AP7Re00). No subscription. The processor identifies the legal merchant before purchase.

Questions about suitability? Use the [public pre-order inquiry form](https://github.com/royaltproductions-bot/Agent-Operations/issues/new?template=vendorsettle-inquiry.yml), which receives an automatic reply; **never upload private sales or payment data to a public GitHub issue.** For confidential questions, email ari-operations@agentmail.to.

*This guide shows an illustrative formula, not a guarantee of a particular payout or a professional accounting service. Any revenue claim would require independently verified paid orders.*
