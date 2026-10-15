# Autonomous Revenue Engine — durable operating handoff

Updated 2026-10-15 by **live chat** (NOT an autonomous scheduled run). This file is the current operational state; older failures and iterations remain in Git history. Keep this concise and update only when facts change.

## GitHub permission status — important
- User explicitly selected **Allow all actions** for the GitHub plugin. The plugin's app-specific permissions were independently checked and reported **Allow all actions**.
- A fresh direct GitHub `create_file` call from live chat **SUCCEEDED**; commit: https://github.com/royaltproductions-bot/Agent-Operations/commit/86d527edbb23226e2dec8f0e8d0ebd71a5de0db1 and receipt `ops/runs/2026-10-15-permission-repair-check.json`.
- Earlier scheduled GitHub write attempts (acceptance and persistence probe) **FAILED** under previous conditions. Independent *scheduled* write permission must be rechecked with a distinct one-time task; DO NOT infer scheduled success merely from live chat success.
- Never work around a genuine safety restriction. If a scheduled write fails after the authorized setting, record a clear error and continue permitted external work where possible.

## Architecture and sequence
1. **GitHub Actions market radar** hourly at ~:12 UTC: `.github/workflows/ten-lane-opportunity-radar.yml` runs ten independent GitHub issue searches and duplicate/PR checks, updating `ops/discovery/current.json` and `ops/discovery/triage.json`. These are discovery hints, **not** sales, paying customers, or ChatGPT hourly runs.
2. **ChatGPT Revenue Scout** hourly at ~:25: read this file and latest radar, evaluate whether real buyers/jobs are available through permitted channels. Create ONLY genuinely qualified scored leads as individual `ops/agent-queue/<id>.json` per `ops/agent-queue/README.md`, or report zero if none. Verify current publication, funding/terms, competition, accessible contact/application, cost and ability to perform. Never spam or invent a lead. Record a dated actual scout run in `ops/scout-runs/` when GitHub writes are permitted.
3. **ChatGPT Revenue Executor** hourly: prioritize new PAID orders (live Stripe + AgentMail) and replies, then claim highest qualified unclaimed work with latest GitHub blob SHA and a 90-minute lease; perform actual permitted external action, verify by external receipt; mark completed or blocked with specific cause. Create unique `ops/runs/` start receipt at RUN START, update at end, and update handoff on material change. If Scout has no lead, research and attempt a concrete legal direct economic action itself, not more product generation or private drafts.
4. **Daily Audit** 22:00 America/New_York: reporting only. Check verified independent run files, GitHub Actions results, qualified leads, external actions, replies, paid accepted work, new live Stripe captures, and actual failures. Distinguish live-chat intervention and GH Actions from scheduled ChatGPT operations.

The **sequence is discover → qualify → externally execute → verify → record → self-correct**. Do not call a run complete merely because it has code, drafts or internal commits.

## Verified state / baseline
- Latest fully tested radar: **10/10** concurrent searches, **4** bounty hints, **4/4** with competing fixes; **0 independently verified available funded jobs**. https://github.com/royaltproductions-bot/Agent-Operations/actions/runs/38782039320 . Do not rework those four competed issues.
- Latest previously checked `ops/agent-queue/` contained **0** qualified JSON leads.
- VendorSettle Desk $49 live payment link: https://buy.stripe.com/eVq9AS63KcrdfUe3AP7Re00 . Public offer: https://github.com/royaltproductions-bot/Agent-Operations/blob/main/products/vendorsettle-desk/README.md . No new venture payment verified at previous last check; check LIVE Stripe each run. Private fulfillment after genuine payment and usable anonymized CSV (up to 500 rows), normally within three business days.
- Operations AgentMail `ari-operations@agentmail.to` previously had **0** customer threads. Gmail `ari.gpt.c@gmail.com` is connected and works directly. A single job application was sent by **live chat** at 2026-10-15T18:04:38Z; check Gmail before any follow-up and never claim it was sent by the scheduled executor. Proof remains privately in Gmail Sent.
- TikTok Buffer educational draft/video path functions; business promotion disclosures are not supported in that route, so do NOT post commercial advertisements disguised as education. Meticulate signup is restricted. Yaliyah Metricool belongs to a different brand and must NOT be touched.

## Operational controls
- All run files identify attribution honestly: `scheduled_scout`, `scheduled_hourly`, `one_time_test`, `github_actions`, `live_chat`.
- Start log only at actual execution; terminal status, UTC time, external source/receipt, actual new captured revenue (not projections), actual blocker and next independent action.
- Check remote destination before any retry to avoid duplicate messages/posts; transient failures at most twice. Do not circumvent genuine connector safety blocks, spam, spend funds, sign contracts, share customer information publicly or manufacture sales claims.
- Prioritize live customers > accepted/contracted work > genuinely qualified buyer opportunity > legal public distribution > infrastructure. If blocked, change lawful method rather than restating obstacle.
- Report **exact observed counts** only, never promises of an arbitrary speed or revenue multiplier.

## Known exact evidence
- Previously FAILED scheduled GitHub-write test: `ops/runs/` had no acceptance/probe file from earlier scheduled tests.
- NOW PASSED live GitHub-write test: https://github.com/royaltproductions-bot/Agent-Operations/commit/86d527edbb23226e2dec8f0e8d0ebd71a5de0db1 .
- Next validation: independent scheduled writer creates a DIFFERENT file and read-back verifies it. Only then mark autonomous GitHub persistence verified.
