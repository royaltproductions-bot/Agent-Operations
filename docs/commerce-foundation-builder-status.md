# Autonomous commerce system

The active revenue system has two workflows and three responsibilities.

## Commerce engine

`.github/workflows/independent-intelligence.yml` runs `tools/autonomous_commerce_brain.py` at :23 each hour and on manual dispatch.

Its job is to discover real demand, choose an open-ended commercial model, use connected provider tools, reach authorized buyers when possible, observe real Stripe/AgentMail outcomes, and write one commerce receipt.

## Refiner

`.github/workflows/independent-commerce-foundation-builder.yml` runs `tools/continuous_commerce_refiner.py` at :07 each hour and on manual dispatch.

It reads recent commerce receipts. A structural execution failure may trigger immediate repair; a commercial pattern must recur across multiple runs before it can trigger a refinement. The refiner patches one core file and sends the changed system back to commerce. Production outcomes—not duplicate test gates—determine whether the change helped.

## Provider layer

`tools/connected_commerce_tools.py` owns connected Stripe, AgentMail, Buffer, repository-asset, and Render access. It exposes capabilities but does not choose business strategy.

The system has no predetermined business-model lanes. Technical connectors are finite; commercial ideas are not.

Historical receipts may remain in `ops/`, but old health monitors, fixed VendorSettle automation, ten-lane radar, agent queues, compatibility workers, and their workflows are no longer part of the active revenue architecture.
