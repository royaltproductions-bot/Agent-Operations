# Continuous autonomous commerce refiner status

The active refiner is `tools/continuous_commerce_refiner.py`, executed by `.github/workflows/independent-commerce-foundation-builder.yml` every hour and on manual dispatch. The former narrow development worker is only a compatibility entrypoint.

Purpose: continuously inspect privacy-safe commercial outcomes and repository state, identify the highest-impact execution bottleneck, implement a bounded system improvement, run the full Python regression suite plus capability smoke checks, obtain a second adversarial model review, record a refinement receipt, and integrate only a validated change.

Scope is open-ended commercial infrastructure rather than a predetermined business model. Existing regression tests cannot be modified by the refiner. Existing source files are changed by exact anchored patches instead of whole-file regeneration; new modules may be created only in bounded repository roots. Workflow files, credentials, ops evidence, and unrelated projects are outside model write scope.

The separate Independent On-Runner Intelligence workflow remains the production entrepreneur/executor. It supplies the refiner with real outcome evidence including demand-search failures, buyer reach, live Stripe aggregate outcomes, AgentMail reply aggregates, fulfillment evidence, and portfolio stagnation diagnostics. The refiner improves the engine; it is not itself evidence of customers, sales, or revenue.

Completion standard for any refinement is code change + regression validation + capability smoke + adversarial approval + persisted receipt + successful main-branch integration. Commercial success remains separately evidenced by provider/outcome receipts.
