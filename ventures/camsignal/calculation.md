# CAMSignal calculation specification

Given tenant square feet T, allocation denominator D, total reported CAM pool P, lease-excluded costs E, landlord billed allocation B, and estimated CAM already paid A:

- Tenant share = T / D.
- Eligible pool = P - E.
- Simplified annual baseline = round((P - E) * T / D, 2).
- Review variance = B - baseline.
- Baseline true-up = baseline - A.
- Billed true-up = B - A.

Input checks: T > 0, D > 0, T <= D, P >= 0, 0 <= E <= P, B >= 0, A >= 0. Currency uses two decimal places.

A positive variance is not proof of overbilling. Lease-specific exclusions, caps, base years, gross-ups, denominators, and other clauses may change the calculation. No legal conclusion or refund promise.

Regression example: T=2400, D=40000, P=240000, E=18000, B=15840, A=14400. Eligible pool=222000; baseline=13320; variance=2520; baseline true-up=-1080; billed true-up=1440.
