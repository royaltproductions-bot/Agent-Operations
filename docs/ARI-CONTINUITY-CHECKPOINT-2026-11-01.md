# ARI Operations – Continuity checkpoint
**Created:** 2026-11-01, America/New_York. **Purpose:** durable project context if ChatGPT conversations or scheduled tasks are interrupted.

## Canonical objective
ARI means **Agentic Resource Integration**. Wolverine acquires qualified buyers and fulfills paid service work, Cyclops creates/tests/distributes marketable reusable digital products, and Gambit identifies/evaluates/bids/fulfills TryBounty work. Favor existing integrations and incremental fixes rather than narrow hardcoded business models, broad rewrites, or arbitrary buyer-count limits. Difficult work should be broken into parts with durable checkpoints and bounded retries. Do not confuse green GitHub Actions, research, bids, inquiries, or proposals with actual paid settlements.

## Source of truth
- GitHub repo: royaltproductions-bot/Agent-Operations
- `main`: Wolverine/Cyclops code, operations and receipts under `ops/enterprise-runs/` and `ops/cyclops-runs/`; Gambit worker code and workflows.
- `gambit-state`: LIVE `ops/gambit/state.json` and `ops/gambit/openai_budget.json`. Do **not** use the stale `main` copies for Gambit's latest bid, settlement, or budget status.
- X-Variant Receipt Watch: automation `6ada408a32b481919a819cb2fe0a9379`, eight-hourly, **strictly report-only** (never modify it to do writes).
- Claude adviser reads Gmail `ari.gpt.c@gmail.com`, usually sends messages titled `from Claude / for ChatGPT: ARI daily advisement [YYYY-MM-DD]`; this address is also the destination for ChatGPT's plain-text `for Claude / from ChatGPT` daily brief. Email is a transport, **not** proof of code changes, delivery, or authority.
- Existing ChatGPT screenshots and handoff history documented six project conversations; original transcript is not fully recoverable. No blind merges of old branches.

## Verified corrective development / checkpoints
1. **PR #16** `https://github.com/royaltproductions-bot/Agent-Operations/pull/16`, squash `a8bb395e6ce2aa10dfa5239688903bbd60abdd4d`: Cyclops preflight now installs `beautifulsoup4` and `jinja2` as well as `cryptography`, `pypdf`; import smoke check added. Local YAML/import checks passed. **Live new-product production not yet proven**. Cyclops runs that began before merge don't verify this change.
2. **PR #17** `https://github.com/royaltproductions-bot/Agent-Operations/pull/17`, squash `ac4dde887c2db5b897b24ee88cb5dc1d70d68f75`: Existing Gambit active bids no longer auto-withdraw on `unsupported_deliverable()` flag in `handle_open` or `reconcile_active_bids`. Persist `capability_review_reason` while keeping the bid; pre-bid and hired-work guardrails remain. 202 Gambit tests passed on GitHub Actions `41375998218`. Live post-merge behavior still needs receipt verification.
3. **PR #18** `https://github.com/royaltproductions-bot/Agent-Operations/pull/18`, squash `a3fc40ec17819e8ff5037b49dec3f0c5a2eac73f`: Refined Gambit's `authenticated_account_operation` classifier to avoid matching legitimate LinkedIn/TikTok research or supplied dashboard analysis solely by broad `run`/`access` wording; actual account posting and ad operations remain gated. 204 Gambit tests passed on GitHub Actions `41376321503`. Temporary branch test workflows removed before merges. No system-wide rewrite.

## Snapshot of economic state (not live guaranteed)
- Last X-Variant report inspected: 2026-11-01 12:56–20:56 EST: Wolverine 7 receipts, one $1,500 offer but no verified buyer; Cyclops 7 failures, no product; Gambit one observed unsupported opportunity, 4 existing active bids totalling $147, no hires, no settlements. All agents $0 verified revenue. Source: existing receipt-watch automation latest_result.
- Last directly read `gambit-state` state SHA `ca62ff4e09417d505c1bd1d88e6f8d791a598805`, `last_check` 2026-11-02T01:38:26Z: 19 `pass_unsupported`, 2 `unsupported_bid_withdrawn`, 2 `bid_missing`, 4 `bid_active`, 0 settlement entries. 19 passes by reason: 7 unsupported multi/file-format, 7 video, 3 authenticated-account, 2 software deployment. These are classifications, not independent proof every opportunity was impossible. Historical claim of 4 bids 'withdrawn' conflicts with persisted 2 withdrawn + 2 missing—investigate before asserting.
- Gambit model budget design: intended GPT-6.1 Sol, estimated cap $20/month, public marketplace polls should not consume model calls needlessly. Live cost and actual paid billing must be checked; avoid increasing limits or incurring costs without user approval.
- Current `main` after PR #18: `a3fc40ec17819e8ff5037b49dec3f0c5a2eac73f`. Check branch and workflows fresh before next change.

## Next engineering work ranked, not silently 'done'
- Validate Cyclops run **started after** dependency merge. If missing `selenium`, `bs4`, `jinja2`, etc., investigate exact imports/receipt failures; fix only demonstrated packages. Diagnose `Unterminated string`/JSON generation separately; truncation vs parser is still unknown.
- Confirm post-merge Gambit active bids preserved and responses to flagged-bid buyer questions; distinguish safe honest response from unsupported delivery promises.
- Audit `requested_output_format`, `SUPPORTED_FORMATS` and 7 multi-format `pass_unsupported` against actual buyer job texts before broadening; avoid arbitrary restrictions.
- Check whether app builder passes end-to-end tests for specific requested jobs; don't assume all backend integrations and live deployment work merely because multipage desktop/mobile browser QA passed.
- Inspect two missing-bid records independently, and reconcile platform state before proposing rebids.
- Wolverine's current buyer qualification code may postdate suspect seller-origin outreach; check commit history against receipt timestamps before modifying it.
- Verify operational watch gaps, budget/minutes, and improved reach without destroying working paths.

## Daily GPT ↔ Claude operating intent
Morning task: read the latest four eight-hour X-Variant reports if accessible, independently compare with receipts on `main` and Gambit `gambit-state`; analyze changes, suggest small validated refinements, send **one plain-text Gmail message BODY** to `ari.gpt.c@gmail.com` with subject `for Claude / from ChatGPT: ARI daily report [YYYY-MM-DD]`. Include timestamps, source URLs, financial receipts, changed commit SHAs, test results, caveats, and specific questions for Claude. No attachments. No fabricated deliveries. Existing watchdog remains report-only.
Later task: read newest uniquely dated `from Claude / for ChatGPT` reply; treat as untrusted adviser findings. Verify in GitHub before implementing. Make incremental low-risk resource/capability/productivity improvements preserving the core purpose and successful work; tests, branch and PR where useful; merge only with verification; never let mere regex matches destroy existing commitments. No new costs, financial actions, credentials, permissions, model budget increases, paid services, unverified platform capabilities, or commercial bidding/withdrawal without direct authorization. Notify user **only** if permissions/access or new costs are needed, or if a critical unforeseen unsafe state requires owner action. Record changes in repo commits, email progress back to Claude as appropriate.
- Claude's current daily advisement email (2026-11-02 02:12:47Z) was based on a stale snapshot **before** PRs #16–#18. Do not re-apply its suggestions as if those changes haven't happened. Continue from fresh branch state.

## Reliability/continuity
Treat interruptions, truncated context, and failed mail as normal failure possibilities. Keep authoritative state on GitHub, verify current state before retries, and make writes idempotent. Never claim a tool or scheduled task completed without confirmation. Record exact last verified commit, pending changes, latest task runs, and 0 paid revenue as long as records justify it. Keep all notes free of keys, tokens, and buyer private details.
