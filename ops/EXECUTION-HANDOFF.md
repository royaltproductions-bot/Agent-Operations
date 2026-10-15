# Revenue engine — fast recovery state
_Last corrective update: 2026-10-15, LIVE CHAT, **not** an autonomous scheduled run. Historical evidence is in git history, ops/activity-ledger.json and ops/runs._

## Immediate priorities (do these; no further infrastructure research)
1. Query authorized LIVE Stripe for new paid VendorSettle Desk payments (existing checkout https://buy.stripe.com/eVq9AS63KcrdfUe3AP7Re00). Query AgentMail ari-operations@agentmail.to for genuine incoming customers. **Paid orders are the first priority.** Typical fulfillment: up to 500 anonymized CSV rows, vendor summary + audit CSV, ~3 business days after usable file.
2. Read `ops/agent-queue/` and top entries in `ops/discovery/current.json`; confirm primary evidence, actual payer and terms, eligibility, competing PRs, permitted submission route and time-to-money. Claim task safely by current SHA and act externally; do not count raw discovery as a qualified lead.
3. If no executable lead, use a genuinely connected, permission-compatible channel to reach people with a stated immediate need, not another speculative product or private social draft. Execute **up to 3 truly permitted** distinct actions per hourly run when possible; otherwise document real blocker and pivot, not fake activity.
4. End with a truthful timestamped `ops/runs/` record and update this handoff. Include `attribution=scheduled_hourly`, exact public receipt/remote result or definitive failure, next actual task. A GitHub Actions commit **is not** a ChatGPT automation execution.

## Ten-way discovery accelerator
- `.github/workflows/ten-lane-opportunity-radar.yml`: hourly ~:12 UTC, ONE free-style GitHub runner does **10 concurrent public GitHub issue searches**, deduplicates/filter obvious bogus payouts and writes `ops/discovery/current.json`. No outreach, claims or paid APIs.
- Unit tests and market scan **passed with 10/10 lanes** on Oct 15: https://github.com/royaltproductions-bot/Agent-Operations/actions/runs/38778280558 .
- At last read **4 raw cash-bounty hints**, not funded or exclusive verified orders: `paraloom-labs/paraloom-core` #929 and #930 (up to $150, payment/claim method unconfirmed), `uvsmtid/protoprimer` #143 ($75 but FOUR competing PRs #144-147), and `SUSU-LABS/susu-web` #51 ($50, 10 comments).
- `ops/discovery/README.md` defines Scout verification; `ops/agent-queue/README.md` defines scored actual handoff, score ≥70, ≤10 pending.
- Revenue Scout Agent operates hourly ~:25, reviews/verifies up to five raw hints and queues ONLY genuinely reachable paid work. Main executor follows next hourly cycle; daily auditor reports real funnel conversion. Ten search lanes are **10× discovery concurrency, NOT 10× revenue**.
- GitHub tool availability has no advertised fork-creation action: external repo PR bounties can be **unactionable** without an authorized submission route. Reject speculative/unfunded/competitive work.

## Current real customer/payment/channel state (verified Oct 15)
- LIVE Stripe link ACTIVE, merchant account connected; direct list of Checkout Sessions had **0**, most recently listed unrelated Stripe intents were historic 2024 Sirius Sage purchases. **No new VendorSettle revenue verified**. Never count historic charges.
- AgentMail operations inbox accessible but **0 threads** at last check.
- Gmail `ari.gpt.c@gmail.com` ALREADY works through connected Gmail API; failed in-app reconnect UI does not block operations. Do not make user reauthorize unnecessarily.
- Meticulate new signup restricted, NOT connected, NOT a dependency.
- TikTok @oddly.useful.stuff first **noncommercial** educational video: Buffer-confirmed `sent`, external destination https://tiktok.com/@oddly.useful.stuff/video/7696936897301531917 ; TikTok page not independently reopened. Evidence https://github.com/royaltproductions-bot/Agent-Operations/actions/runs/38771872096 . No paid conversions established. Buffer lacks native TikTok business-promotion disclosure; **do not post ads disguised as education**. Read-only TikTok monitoring was REDUCED to daily at 09:17 UTC to eliminate 23 low-value polls/day.
- Historically `ops/runs/` had only TWO persisted task logs although many commits. Persistence is still unverified until genuine independent scheduled runs maintain it. Do not claim otherwise.

## Operating safeguards & stop-loss
No spending, self-funded tasks, spam, deceptive claims, contract acceptance, unsafe data exposure, or bypassing app/platform restrictions. No Yaliyah Metricool access for this engine. Transient retry at most twice, first check remote state for duplicate. Definitive permissions/compliance block: record and switch to a DIFFERENT lawful route; after 2 unproductive cycles of one tactic pivot. Agent proposal, GitHub scan, private draft, and sent-without-public-confirmation are not buyer engagement or captured cash. Notify user when new money or concrete material outcome verified, otherwise daily reporting audit continues at 22:00 ET.
