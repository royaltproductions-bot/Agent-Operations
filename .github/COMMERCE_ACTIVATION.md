# Activate independent commerce operator — one-time external authorization

**Status:** The GitHub-side code and synthetic tests are deployed. The live order checker is deliberately FAIL-CLOSED while the external credentials below are absent. The existing ChatGPT Stripe and AgentMail OAuth connectors do NOT authorize GitHub Actions to read their credentials. Never paste API keys into issues, README, chat, source code, or Actions logs.

GitHub secret configuration: https://github.com/royaltproductions-bot/Agent-Operations/settings/secrets/actions

Only two repository Actions secrets are needed for the existing already-created $49 VendorSettle service:

1. `STRIPE_SECRET_KEY` — a **restricted live** Stripe API key (`rk_live_`) scoped to **read Payment Links, Checkout Sessions, Payment Intents and Charges** for the existing merchant account; a `sk_live_` key also works but has broader access and is not recommended. Create this in the **Stripe Dashboard**, not by copying the ChatGPT connected app token. No Stripe mutation/write permission is needed.
2. `AGENTMAIL_API_KEY` — a dedicated AgentMail API key scoped to **list/read messages, retrieve attachments and send messages** only from the already-authorized inbox `ari-operations@agentmail.to`, created in the AgentMail Console. Do not substitute a chat OAuth session. Preserve provider-enforced sending limits.

Once those two secret values exist, `.github/workflows/independent-commerce-operator.yml` runs hourly in **GitHub Actions itself**, without ChatGPT Scheduled Tasks:
- Finds the **existing** $49 Stripe Payment Link and lists Checkout Sessions.
- Verifies live paid USD $49 payment, successful PaymentIntent and an unrefunded successful charge; checks existing delivery receipts.
- Finds exactly one matching CSV attachment from the payer email received after payment (extra matching messages are held for review).
- Checks that data meets the advertised 500-row limit and excludes obvious email/card information, then uses the existing deterministic report engine.
- Checks the outgoing message subject in AgentMail before sending, sends the private report files and notes privately, and writes a public **PII-free** hashed receipt only after a provider send acknowledgement.
- Will not create a charge, issue a transfer, send speculative outreach, spend an advertising budget, or invent a buyer.

**Limitations that must NOT be hidden:** Stripe and AgentMail APIs in a real run have not yet been tested from GitHub; the authenticated end-to-end customer transaction is unverified. This is the fulfillment lane for the existing service, not a demand-generation channel. An hourly public-opportunity scanner and documentary publisher run independently but cannot manufacture buyers. An external LLM was initially coded against GitHub Models, which GitHub **retired July 30, 2026**; this path was removed and evidence-based qualification is now labeled correctly (NOT AI inference). A suitable explicitly authorized model-provider key would be needed to add a third-party LLM, and ongoing usage must not be charged without explicit permission.

**Acceptance evidence:** inspect an hourly GitHub Actions run with all steps successful **AND** credentials recognized; an authenticated read of the Stripe link, authenticated inbox read, and ideally a genuine funded paid-order-to-report-email external delivery, with no duplicate. A green result from code unit tests or a video Buffer publish request alone is NOT a production end-to-end acceptance.

Do not enable verbose network debug logging or expose buyer CSVs or emails in the public GitHub repository. Failure logs should show only error categories. To pause delivery, remove `AGENTMAIL_API_KEY`, or remove the hourly `schedule` trigger.
