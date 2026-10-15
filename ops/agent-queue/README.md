# Immutable qualified opportunity handoff — actual revenue engine

GitHub is the shared loading room for **Revenue Scout** (hourly at :25 ET) and **Executor** (hourly at :48 ET). One `ops/agent-queue/<stable-id>.json` per **independently evidenced** qualified opportunity. The existence of a lead is *not* a delivered action, a paying customer, or revenue.

## Real sequence
1. Scout reads the latest discovery and prior `ops/agent-queue/`, `ops/outcomes/`, plus live original solicitation. Check **real buyer need, explicit permitted contact/application, eligibility, advertised terms, lack of competing completed work, and ability to fulfill**. An unverified bounty price, job salary, web search hit, or imagined customer does not qualify.
2. Scout scores five evidence factors and creates no more than 3 new actually verified opportunities per day. It does not send messages. Publish no private customer data.
3. Executor checks new paid Stripe orders and inbound customer requests **first**. Then it ranks unprocessed qualified leads using `python3 tools/agent_queue.py --mode rank`; the script automatically excludes leads with immutable executor outcomes in `ops/outcomes/`. Check newest GitHub data, Sent folder and external target *again* before contact to prevent duplicate communication.
4. Executor performs one genuinely authorized buyer-facing action within the SAME run, verifies external receipt, then creates a **NEW immutable** `ops/outcomes/<lead-id>-<UTC>.json` with `lead_id`, `status` (sent/completed/attempted/blocked), UTC timestamp, exact evidence where public-safe, and payment amount only if confirmed by LIVE processor. Don't update the original lead file. For blocked sends, record `sent_count:0` and the specific safety restriction. Do not retry a blocked message through another provider.
5. Both scheduled Scout and Executor must record separately attributable start and terminal receipts in `ops/scout-runs/` and `ops/runs/`. Auditor and the independent `revenue-engine-health.yml` monitor cross-check these against external evidence; GitHub Actions are not ChatGPT agent runs.
6. If no qualified leads or legal execution route: report factual zero in run logs, do not invent work; pivot to a distinct permitted channel and let daily audit report the limitation.

## Queue JSON schema
Each file has:
- `schema_version:1` and stable lowercase dash-separated `id` equal to file basename.
- `kind`: buyer_request, channel_opportunity, paid_job, or inbound_order.
- `status:"qualified"` (remains immutable), `attribution:"scout"`.
- `buyer_signal` minimum 25 characters; `proposed_action` minimum 20 characters; `channel`.
- Primary public `evidence_url` using HTTPS; `created_at_utc` and `updated_at_utc` as ISO UTC ending in Z.
- `score_factors`: five integers 0–5 for buyer_intent, reachability, audience_match, offer_fit, urgency.
- `priority_score = 5×buyer_intent + 4×reachability + 4×audience_match + 4×offer_fit + 3×urgency`, minimum 70. This is a screen, **not** predicted earnings.
- `safety_review:"pass"`; `contains_private_data:false`.

The validated queue supports at most 10 unresolved opportunities. `tools/agent_queue.py` validates inputs and automatically discounts an item after a terminal outcome file. It also supports legacy `claimed` states but the CURRENT scheduled path **does not use mutable leases** because scheduled update_file was not reliable.

## Non-negotiables
No spending, unauthorized contracts, unsolicited bulk messages, unlicensed material, private client data in GitHub, manipulation of safety gates, or cross-brand posts through Yaliyah's Metricool connection. Only confirmed paid Stripe events count revenue. Review public promises and permitted commercial disclosures before distribution.

**If the eligible queue is empty, the measured number of ready opportunities is zero.**
