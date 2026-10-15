# Revenue Scout independent runs

One immutable JSON receipt per *actual* independently scheduled Scout run. Attribution MUST be `scheduled_scout`; include `recorded_at_utc`, `status` (completed/blocked/failed), verified source URLs examined, count of qualified lead files actually created, and next concrete action. Optional start receipt is separate `state:running` and does not count as completed. Never claim GitHub Actions or live-chat work as a Scout run. This directory exists so the agent can list/read it without 404 errors. Do not store buyer PII.
