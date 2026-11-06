# Dashboard measurements

Verified interactively on 2026-11-06 around 02:42 UTC after owner sign-in:
- Gambit Unread: explicit “No unread updates” (0).
- Gambit Not hired: explicit “No lost bids” (0).
- Operator Payouts: explicit “No earnings yet” (0).
- Operator balance after Refresh balance: $0.00. This is not settled revenue.
- Transfer history / disbursement timestamps: not displayed; unknown.
- Profile settings leave Rating and Bounties completed hidden. No public rating
  or completed-job count was inferred from that configuration.

The collector runs once inside the existing Gambit job, before the resident
poll loop. No new schedule or workflow dispatch is introduced. Fixture HTML
contains the observed semantic elements, with styling/navigation removed; no
buyer messages are retained. Only verified empty-state layouts and the numeric
balance label are supported. Nonempty queues/history require verified layouts
before implementing their parsers; they yield null plus `layout_unverified`.

## Runner authentication still required

The interactive cloud-browser sign-in does not authenticate GitHub Actions.
Set repository Actions secret `GAMBIT_BROWSER_STORAGE_STATE` to an explicitly
provisioned Playwright storage-state JSON for this TryBounty operator account.
Do not paste the credential into chat, code, issues, logs, or public state.
The collector accepts only trybounty.ai cookie/origin domains. It never exports
or logs the credential. Expired sessions yield null with an error; marketplace
polling continues. No session was copied out of the interactive browser.

Until that secret is provisioned, this is a deployed collector with an
explicit authentication blocker, NOT live scheduled dashboard acquisition.
The current manual observations above are point-in-time evidence only.
