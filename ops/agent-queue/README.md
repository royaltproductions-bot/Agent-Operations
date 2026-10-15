# Qualified opportunity queue (agent handoff)

This folder contains ONE JSON file per verified commercial opportunity. It is **not** a sales result or activity log.

## Multi-agent pipeline
- **Scout automation**: find concrete, public, recent buyer need or a permitted reachable distribution opportunity. Confirm primary evidence URL, offer/channel fit and permissions. Score against `tools/agent_queue.py`. Create at most **three** qualified files per day; do not create weak ideas just to fill slots. Use stable id `YYYY-MM-DD-short-descriptor`; redact all personal information.
- **Hourly executor**: check this folder AFTER checking live Stripe orders and AgentMail. Rank via `python3 tools/agent_queue.py --mode rank`; select the highest value **qualified** item. Fetch current file SHA then mark `claimed` via the authorized GitHub update using that SHA (optimistic concurrency) and set `claimed_by: "executor"`, `claim_until_utc` (now + 90 minutes). Check remote state before an external action or retry.
- **Executor**: finish external action within permissions, then mark `completed` and include a real outcome URL, or `blocked` with concrete reason. Update the central handoff. If an expired lease appears, inspect remote state before reclaiming, never duplicate outreach/publishing.
- **Daily audit**: report qualified opportunities, claimed/expired, completed with evidence, blocked, sales and independent run counts. Do not count opportunities themselves as distribution/revenue.

## Data format
Each JSON has: `schema_version:1`, stable `id` matching file name, `kind` (buyer_request, channel_opportunity, paid_job, inbound_order), `status` (qualified, claimed, completed, blocked, discarded), `attribution` (scout, executor, live_chat), `buyer_signal` (why a buyer/channel is real), `proposed_action` (one actionable next step), `channel`, public `evidence_url`, UTC `created_at_utc` and `updated_at_utc`, five integer `score_factors` (0–5) `buyer_intent,reachability,audience_match,offer_fit,urgency`, weighted `priority_score` (5×intent + 4×reachability + 4×audience + 4×offer + 3×urgency), `safety_review:"pass"`, `contains_private_data:false`.

Score >=70 required to qualify; max 10 unfinished items. Claim requires `claimed_by` and `claim_until_utc`. Completed work requires `outcome_url` and `outcome_description`. Blocked items require `blocked_reason`. No private data, credentials, buyer email addresses, personal profiles, payment tokens, or pseudonymous customer records in this public repo.

An unverified web post is evidence of *potential interest*, not buyer contact. Agent must not represent its score as guaranteed sales.

## Safety / efficiency
The Scout is *not* authorized to spend, scrape private customer data, send unsolicited bulk messages, enter a contract, bypass connector refusals, or make social posts on someone else's behalf. Publisher channel permission and platform commercial disclosure requirements remain mandatory. The executor alone owns final commercial outreach/publication under existing authorizations and must avoid duplicates.

If no qualifying evidence: report zero qualified and save no speculative queue files. **Empty queue is better than fictional demand.**
