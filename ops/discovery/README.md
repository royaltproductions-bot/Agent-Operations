# Ten-way opportunity radar — commercial pre-screen, not qualified leads

Created October 15, 2026 to increase **concurrent market-search capacity** without multiplying scheduled ChatGPT agents or spamming channels. The hourly GitHub Actions workflow `.github/workflows/ten-lane-opportunity-radar.yml` invokes `tools/parallel_opportunity_radar.py`. One low-cost Actions runner performs exactly **10 independent public GitHub issue searches concurrently** using its normal `GITHUB_TOKEN`; no third-party subscription, no new credentials, no outbound contact, no spending.

## Output
`current.json` is an auto-refreshed **unverified opportunity hint index**. Its dollar figures are extracted from public issues and explicitly NOT independently verified payer funds. `score_for_review_only` is discovery rank, **not** the Agent Queue qualification score and **never** proof that a customer or payer exists. Results are de-duplicated by source URL; empty results are acceptable. Failed lanes are reported rather than silently misclassified as success.

## Scout verification checklist — MUST PASS before promoting into `ops/agent-queue/`
1. Open the PRIMARY issue/official solicitation, not an aggregator. Verify it is still open and has a real owner/provider, explicit reward terms and independent payer evidence if possible.
2. Check existing/open PRs, comments, assignees, whether bounty is already claimed or under competition; high competition should usually be discarded.
3. Check eligibility, payout currency/rail, payment triggers, restrictions, deadlines, and whether claimant must prepay/escrow/fund anything. Reject required spending and speculative future rewards.
4. Establish that the authorized connected account can actually do the work and submit through an accepted channel. **The available GitHub connection currently does not advertise a fork-creation action, so do not assume arbitrary-repository PR submission is possible.**
5. Check whether the estimated work/review time is defensible relative to the payout and likely success. A "$150" mention alone means nothing.
6. Only once verified, create a unique queue JSON according to `ops/agent-queue/README.md`; do not copy the radar's score or mark a raw radar item as qualified.
7. If none pass, record **zero verified payout opportunities** and pivot to actual reachable buyer requests/distribution through authorized channels.

## Outcome goal
At least ten independent sources screened simultaneously versus one serial scan. **10× concurrent search lanes is NOT 10× revenue, 10× verified leads, or 10× scheduled assistant invocations.** Measure run success, false-positive rate, queue promotions, completed external actions, and Stripe-collected dollars separately.

## Known low-value routes
- Meticulate new user signup is restricted; do not depend on it.
- Gmail is already connected and API working; do not ask user to reauthorize because the in-app re-link screen errors.
- TikTok Buffer route cannot set native business-promotion disclosure; do not label ads as neutral educational posts.
- `uvsmtid/protoprimer#143` attracted four competing PRs Oct 15; not priority despite a real $75 offer.
