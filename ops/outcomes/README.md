# Paid-opportunity external outcomes

Append-only, one JSON per attempted actual commercial action associated with a validated lead ID. Record the matching lead ID, UTC time, tool/channel, real external receipt (if authorized and public-safe), outcome status, and actual captured funds only when verified in LIVE Stripe. An attempt blocked by a safety rule has `status=blocked`, `sent_count=0`, `captured_usd=0` after direct verification; never reroute it around the restriction. This directory's existence permits clean listing by the Executor. No customer private data or secrets.
