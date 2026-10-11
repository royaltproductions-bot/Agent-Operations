# ScoutSheet — Sourcing Decision Workbook (v1)

**Purpose:** A reusable spreadsheet method for independent secondhand resellers to decide what to buy, what to skip, and what to research next. This is an original educational worksheet, not a live-price feed or guaranteed-profit system.

## Import columns

Create a spreadsheet with the following headers, in this exact order:

`Date | Item | Model / MPN | Marketplace | Seller asking | Shipping in | Sales tax | Repair / prep | Expected resale | Selling fees % | Selling fees fixed | Shipping out | Packaging | Required profit | All-in cost | Expected net | Expected profit | Maximum purchase | Decision | Evidence URL | Notes`

Use one row per prospective purchase. Fill A–N, T and U manually. Use O–S for calculated fields.

## Formulas (Excel or Google Sheets)

Enter the following formulas in row 2 and copy down:

- **O2 — All-in cost:** `=SUM(E2:H2)`
- **P2 — Expected net proceeds:** `=I2*(1-J2)-K2-L2-M2`
- **Q2 — Expected profit:** `=P2-O2`
- **R2 — Maximum purchase price for required profit:** `=P2-F2-G2-H2-N2`
- **S2 — Decision:** `=IF(OR(I2="",E2=""),"RESEARCH",IF(R2<0,"PASS",IF(E2<=R2,"REVIEW","PASS")))`

Set **J** as a percentage, not a whole number. Example: 13% is entered as 13%, not 13. Put zero in unknown cost cells only when the cost is truly zero; otherwise leave the decision at RESEARCH until costs are known. A REVIEW decision is not a buy recommendation: confirm demand, item condition, seller legitimacy, return risk, platform rules and cash availability.

## Worked example (hypothetical, not a current listing)

An item is offered for $80, shipping in is $10, tax $5, prep $5, estimated resale $160, percentage fee 13%, fixed fee $0.30, shipping out $12, packaging $3 and required profit $25.

- All-in cost: $100
- Expected net proceeds: $123.90
- Expected profit: $23.90
- Maximum acceptable purchase price to meet $25 target: $78.90
- Decision: PASS at $80, assuming estimates hold.

## Research workflow

1. Import your existing buying list and identify model numbers, item conditions and maximum budgets.
2. Search live listings on the marketplaces you actually use; use model numbers and condition qualifiers to narrow irrelevant results.
3. Record evidence URLs and dates. **Asking prices are not proof of completed sale values.**
4. Estimate fees using the applicable platform's current official fee schedule. Include payment processing, shipping, taxes, refunds and expected returns where applicable.
5. Apply the maximum-purchase formula before contacting a seller. Reject deals with missing essential inputs rather than manufacturing profit.
6. Track actual purchase and sale outcomes in a separate realized-results sheet. Compare projected profit against realized profit each month.

## Evidence of the problem (not evidence of willingness to pay)

- On 27 August 2026, a reseller described managing 150–200 product targets in Google Sheets and manually re-running marketplace searches when the list changed: https://www.reddit.com/r/InventoryManagement/comments/1vzt83f/anyone_using_a_spreadsheet_as_the_source_of_truth/
- On 6 February 2026, resellers discussed the time cost of scanning multiple sources and checking resale prices while evaluating potential purchases: https://www.reddit.com/r/Flipping/comments/1qxjov7/how_are_you_sourcing_inventory_fast_enough_to/
- On 2 August 2026, a reseller reported dozens of open tabs and saved searches while still missing items: https://www.reddit.com/r/Flipping/comments/1vdei6e/removed/

These are independent public discussions, not customer orders. Commercial demand and conversion remain unverified.

## Scope and rights

This workbook and its formulas were created for ScoutSheet. Reuse of the formulas is permitted; no marketplace data, private customer lists, third-party photos or scraped listings are included. The worksheet does not provide financial, tax or legal advice.

**Version:** 2026-10-11. **Operational status:** usable worksheet; no paying customers verified. **Next validation gate:** demonstrate that resellers will pay for a genuinely differentiated completed setup or recurring, permissioned data workflow, not just a generic template.
