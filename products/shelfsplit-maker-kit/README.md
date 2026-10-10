# ShelfSplit Maker Consignment Reconciliation Pack (v0.1)

An original, offline printable recordkeeping pack for makers placing goods in third-party shops. Use the companion CSV files to track inventory movements and monthly settlements. No subscriptions or accounts. This is not a point-of-sale system, bank reconciliation, or tax/accounting advice.

## Workflow

1. Assign a unique SKU to each product and record the retail price, shop, and agreed maker percentage. Confirm the written agreement controls.
2. Record each delivery to the shop as positive on-shelf inventory. Track all movements by date and SKU.
3. Record units sold from the shop's monthly statement. Gross sale amount = units sold × retail unit price. Maker earned = gross × maker percentage ÷ 100, rounded to cents per sale line.
4. Record unsold returns separately. A return decreases on-shelf stock but does not reverse a completed sale.
5. Record payouts received separately. Amount still owed = cumulative maker earnings − cumulative payouts. Confirm payouts against actual bank deposits.
6. For every SKU/shop pair, reconcile: beginning on-shelf + deliveries − sold − unsold returns = ending on-shelf. Investigate unexplained shrinkage instead of silently adjusting it.
7. Export and retain monthly statements and the original shop reports; store copies securely. Never send customer PII to this public repository.

## Worked example (fictional)

Shop: Sample Gift Shop. Maker share: 60%. SKU: SOAP-01. Unit retail price: $12.00. Delivered: 10. Sold: 3. Unsold returned: 1. Maker payout received: $14.40.

Gross sales: 3 × $12 = $36.00. Maker earned: $36 × 60% = $21.60. Shop share: $14.40. On shelf: 10 − 3 − 1 = 6 units. Maker outstanding: $21.60 − $14.40 = $7.20.

## Commercial status

Experimental information asset. No buyer, order, merchant checkout, or earnings verified. No claim of being affiliated with any retailer or government program. Commercial license inquiries: ari-operations@agentmail.to. See LICENSE.txt. This pack is not a substitute for signed consignment agreements or bookkeeping.

## Demand signals, not orders

- Jan 30, 2026 maker-side offsite consignment/returns/invoicing need: https://www.reddit.com/r/smallbusiness/comments/1qr4gcz/software_for_tracking_consignment_sales_off_site/
- Nov 22, 2025 merchant seeking consignment profit-split report without recurring software fees: https://www.reddit.com/r/smallbusiness/comments/1p44za2/struggling_to_find_an_excel_sheet_to_keep_track/
- Shop-side software price benchmark: https://www.simpleconsign.com/pricing
