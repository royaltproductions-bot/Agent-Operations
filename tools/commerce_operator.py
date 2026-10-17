#!/usr/bin/env python3
"""GitHub-native commerce operator: paid intake -> private CSV fulfillment -> receipt.

No ChatGPT dependency. API credentials are ONLY environment secrets, never in
logs or the public repo. Never sends an email or records a sale in dry-run mode.
"""
import base64
import csv
from datetime import datetime, timezone
from email.utils import parseaddr
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path("ops/commerce-deliveries")
INBOX = "ari-operations@agentmail.to"
CHECKOUT_URL = "https://buy.stripe.com/eVq9AS63KcrdfUe3AP7Re00"
CHECKOUT_LINK_ID = "plink_1UQ81zBto3o776uyd6CwIP8s"  # Identified via live Stripe read; public, non-secret
MAX_BYTES = 250_000
DRY_RUN = os.environ.get("COMMERCE_SEND_ENABLED", "") != "YES"

def error(msg):
    raise RuntimeError(msg)

def request(url, bearer="", method="GET", body=None, basic=False, extra_headers=None):
    headers = {"Accept": "application/json", "User-Agent": "oddly-commerce-operator/1.0"}
    if bearer:
        headers["Authorization"] = ("Basic " + base64.b64encode((bearer+":").encode()).decode()
                                    if basic else "Bearer " + bearer)
    if extra_headers:
        headers.update(extra_headers)
    data = None if body is None else json.dumps(body).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read(2_000_000)
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        # No returned body: it may contain PII or API credentials.
        error("Provider HTTP " + str(e.code) + " on " + urllib.parse.urlsplit(url).hostname)
    except urllib.error.URLError:
        error("Provider connection failed at " + urllib.parse.urlsplit(url).hostname)

def stripe_get(path, params=None):
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith(("sk_live_", "rk_live_")):
        error("Stripe live API key unavailable: no paid-order execution possible")
    query = urllib.parse.urlencode(params or {}, doseq=True)
    return request("https://api.stripe.com/v1/" + path + ("?"+query if query else ""), key, basic=True)

def agent_get(path, params=None):
    token = os.environ.get("AGENTMAIL_API_KEY", "")
    if not token:
        error("AgentMail API key unavailable: no private customer intake possible")
    query = urllib.parse.urlencode(params or {}, doseq=True)
    return request("https://api.agentmail.to/v0/inboxes/" + urllib.parse.quote(INBOX, safe="")
                   + "/" + path + ("?"+query if query else ""), token)

def agent_send(to, subject, body, files, order_id=""):
    token = os.environ.get("AGENTMAIL_API_KEY", "")
    if not token or not to or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
        error("Outbound credentials or recipient invalid")
    if DRY_RUN:
        error("Dry run cannot send a message")
    response = request("https://api.agentmail.to/v0/inboxes/"
                       + urllib.parse.quote(INBOX, safe="") + "/messages/send",
                       token, method="POST", body={
          "to": [to], "subject": subject, "text": body,
          "attachments": [{"filename": name,
                           "content_type": "text/plain" if name.endswith(".txt") else "text/csv",
                           "content": base64.b64encode(content).decode()}
                          for name, content in files]},
          extra_headers={"Idempotency-Key": "vendorsettle-"
                         + hashlib.sha256(order_id.encode()).hexdigest()} if order_id else None)
    if not response.get("message_id"):
        error("AgentMail did not return a sent message receipt")
    return response["message_id"]

def find_payment_link():
    # Avoid scanning many unrelated checkout products each hour.
    # Verify ID+URL from Stripe rather than trusting a hardcoded ID blindly.
    item = stripe_get("payment_links/" + CHECKOUT_LINK_ID)
    if item.get("id") != CHECKOUT_LINK_ID or item.get("url") != CHECKOUT_URL or item.get("active") is not True:
        return None
    return CHECKOUT_LINK_ID

def verified_paid_sessions(link):
    cursor = None
    for _ in range(3):
        args = {"limit": 100, "payment_link": link}
        if cursor:
            args["starting_after"] = cursor
        page = stripe_get("checkout/sessions", args)
        for s in page.get("data", []):
            if (s.get("payment_link") == link and s.get("status") == "complete"
                    and s.get("payment_status") == "paid"
                    and s.get("mode") == "payment"
                    and s.get("currency") == "usd"
                    and s.get("amount_total") == 4900
                    and s.get("id", "").startswith("cs_live_")):
                yield s
        if not page.get("has_more") or not page.get("data"):
            break
        cursor = page["data"][-1]["id"]

def verified_unreversed_payment(session):
    """Confirm payment, amount, and refund state against live Stripe objects."""
    intent_id = session.get("payment_intent") or ""
    if not isinstance(intent_id, str) or not intent_id.startswith("pi_"):
        return False
    pi = stripe_get("payment_intents/" + urllib.parse.quote(intent_id, safe=""))
    if (pi.get("status") != "succeeded" or pi.get("currency") != "usd"
            or pi.get("amount_received") != 4900 or pi.get("amount") != 4900):
        return False
    charge_id = pi.get("latest_charge")
    if not isinstance(charge_id, str) or not charge_id.startswith("ch_"):
        return False
    charge = stripe_get("charges/" + urllib.parse.quote(charge_id, safe=""))
    if (charge.get("payment_intent") != intent_id or not charge.get("paid")
            or charge.get("refunded") or int(charge.get("amount_refunded") or 0) > 0
            or charge.get("status") != "succeeded"):
        return False
    return True


def safe_key(session_id):
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

def receipt_path(session_id):
    return ROOT / (safe_key(session_id) + ".json")

def read_inbound():
    out = []
    cursor = None
    for _ in range(2):
        params = {"limit": 100}
        if cursor:
            params["page_token"] = cursor
        page = agent_get("messages", params)
        out.extend(page.get("messages") or [])
        cursor = page.get("next_page_token")
        if not cursor:
            break
    # Incoming files are untrusted: never process outgoing, blocked, pending
    # security-review, or explicitly failed-authentication messages.
    def acceptable(m):
        labels = set(m.get("labels") or [])
        if "received" not in labels or "spam" in labels or "trash" in labels or "blocked" in labels:
            return False
        if parseaddr(m.get("from", ""))[1].lower() == INBOX.lower():
            return False
        armor = m.get("agent_armor") or {}
        if armor.get("status") in ("pending", "blocked", "review", "flagged"):
            return False
        auth = m.get("authentication_results") or {}
        if auth and str(auth.get("spf", "")).lower() == "fail" and str(auth.get("dkim", "")).lower() == "fail":
            return False
        return bool(m.get("message_id") and m.get("attachments"))
    return [m for m in out if acceptable(m)]

def find_customer_file(session, messages):
    email = (session.get("customer_details") or {}).get("email") or session.get("customer_email") or ""
    email = email.strip().lower()
    if not email:
        return None
    matches = []
    for m in messages:
        sender = parseaddr(m.get("from", ""))[1].strip().lower()
        if sender != email:
            continue
        # Only email received after checkout may fulfill this session.
        try:
            received = datetime.fromisoformat(str(m.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if received < int(session.get("created") or 0) - 60:
            continue
        csvs = [a for a in m.get("attachments") or []
                if str(a.get("filename") or "").lower().endswith(".csv")
                and 0 < int(a.get("size") or 0) <= MAX_BYTES]
        if len(csvs) == 1:
            matches.append((m, csvs[0]))
    if len(matches) != 1:
        return None  # ambiguity requires private manual resolution, never guess
    return matches[0]

def download_csv(message, attachment):
    meta = agent_get("messages/" + urllib.parse.quote(message["message_id"], safe="")
                     + "/attachments/" + urllib.parse.quote(attachment["attachment_id"], safe=""))
    url = meta.get("download_url") or ""
    parsed = urllib.parse.urlsplit(url)
    # URL supplied by authenticated AgentMail attachment API; no arbitrary URL in customer text.
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        error("Attachment URL invalid")
    # Reject redirects to untrusted origins and insecure/private address ranges.
    # AgentMail's signed CDN supplies the URL; never accept links from email text.
    import ipaddress
    host = parsed.hostname.lower()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        error("Attachment host address is not public")
    if (parsed.hostname.endswith(".local") or parsed.hostname in ("localhost", "127.0.0.1")
            or parsed.hostname.startswith("192.168.")):
        error("Attachment host invalid")
    req = urllib.request.Request(url, headers={"User-Agent": "oddly-commerce-operator/1.0"})
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        error("CSV too large")
    return raw

def validate_input(raw):
    if not raw or b"\x00" in raw:
        error("Invalid customer CSV")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        error("CSV must be UTF-8")
    rows = list(csv.reader(io.StringIO(text)))
    if not 2 <= len(rows) <= 501:
        error("Report requires 1-500 sales rows")
    headers = [s.lower().strip() for s in rows[0]]
    for required in ("date", "vendor", "item", "transaction_id", "gross", "refund", "fee", "vendor_rate"):
        if required not in headers:
            error("CSV is missing expected columns")
    for row in rows[1:]:
        if len(row) != len(headers):
            error("Inconsistent CSV row width")
        for cell in row:
            if re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", cell, re.I):
                error("Potential personal email in CSV: private review required")
            digits = re.sub(r"\D", "", cell)
            if len(digits) in (15, 16) and re.fullmatch(r"[\d \-]+", cell.strip()):
                error("Possible payment card or personal identifier in CSV")
    return text

def settlement(text):
    # Execute the existing tested deterministic JS engine entirely in memory.
    node = r"""
const fs=require('fs'),vm=require('vm');
const sandbox={}; vm.runInNewContext(fs.readFileSync('products/vendorsettle-desk/engine.js','utf8'),sandbox,{timeout:2000});
const input=fs.readFileSync(0,'utf8');
const report=sandbox.VendorSettle.settle(input,{});
process.stdout.write(JSON.stringify({summary:report.payoutCSV,detail:report.detailCSV,
  count:report.totals.lines,warnings:report.warnings}));
"""
    p = subprocess.run(["node", "-e", node], input=text, capture_output=True,
                       text=True, timeout=12, check=False)
    if p.returncode:
        error("Settlement validation failed; manual private review required")
    data = json.loads(p.stdout)
    if data["count"] < 1 or data["count"] > 500:
        error("Unexpected settlement record count")
    files = {"vendor-summary.csv": data["summary"].encode("utf-8"),
             "transaction-audit.csv": data["detail"].encode("utf-8")}
    if data.get("warnings"):
        # Include duplicate/negative balance findings promised in the offer.
        files["review-notes.txt"] = (
            "Review all figures against source records before payouts.\n"
            + "\n".join(str(w) for w in data["warnings"]) + "\n"
        ).encode("utf-8")
    return files, data["count"]

def previously_sent(subject, messages=None):
    # Query the provider's searchable Sent history, not just the last 100
    # messages, before retrying an externally visible delivery.
    remote = agent_get("messages", {"limit": 100, "subject": [subject], "labels": ["sent"]})
    return any(m.get("subject", "").strip() == subject and
               "sent" in m.get("labels", []) and
               parseaddr(m.get("from", ""))[1].lower() == INBOX.lower()
               for m in remote.get("messages") or [])

def run():
    ROOT.mkdir(parents=True, exist_ok=True)
    report = {"actor": "native_github_actions_not_chatgpt",
              "mode": "send_enabled" if not DRY_RUN else "read_only",
              "stripe_ready": bool(os.environ.get("STRIPE_SECRET_KEY")),
              "agentmail_ready": bool(os.environ.get("AGENTMAIL_API_KEY")),
              "paid_sessions_found": None, "new_deliveries": 0,
              "status": "not_verified", "buyer_outreach": 0}
    if not (report["stripe_ready"] and report["agentmail_ready"]):
        report["status"] = "credentials_missing"
        print(json.dumps(report))
        if os.environ.get("COMMERCE_REQUIRE_LIVE_READY") == "YES":
            error("Required Stripe and AgentMail GitHub secrets are not configured")
        return
    link = find_payment_link()
    if not link:
        report["status"] = "existing_checkout_link_not_found"
        print(json.dumps(report))
        if os.environ.get("COMMERCE_REQUIRE_LIVE_READY") == "YES":
            error("Live $49 Stripe payment link is unavailable on this account")
        return
    orders = list(verified_paid_sessions(link))
    report["paid_sessions_found"] = len(orders)
    messages = read_inbound()
    for session in orders:
        sid = session["id"]
        target = receipt_path(sid)
        if target.exists():
            continue
        if not verified_unreversed_payment(session):
            continue
        # Same buyer may have multiple paid orders. Require an explicit
        # Checkout Session / PaymentIntent reference when orders are ambiguous.
        buyer = ((session.get("customer_details") or {}).get("email") or session.get("customer_email") or "").lower()
        same_buyer_orders = [order for order in orders if
            (((order.get("customer_details") or {}).get("email") or order.get("customer_email") or "").lower() == buyer)]
        matched = find_customer_file(session, messages)
        if not matched:
            # A legitimate paid buyer might not have supplied the required CSV.
            # Send no more than one private, idempotent instructions email.
            email = (session.get("customer_details") or {}).get("email") or session.get("customer_email")
            notice_subject = "VendorSettle input required — order " + sid
            if email and not DRY_RUN and not previously_sent(notice_subject):
                sent_notice = agent_send(
                    email, notice_subject,
                    "Thank you for your VendorSettle Desk order. To prepare your report, "
                    "please reply with one anonymized CSV (up to 500 sales rows) containing "
                    "date,vendor,item,transaction_id,gross,refund,fee,vendor_rate. "
                    "Please exclude customers' personal data, bank details and card numbers. "
                    "Your report is typically delivered within three business days after "
                    "the usable file arrives. Questions? Reply privately to this email.",
                    [], order_id=sid + "-intake-request")
                if sent_notice:
                    report["buyer_outreach"] += 1
            continue
        if len(same_buyer_orders) > 1:
            incoming_subject = str(matched[0].get("subject") or "")
            if not (sid in incoming_subject or
                    (session.get("payment_intent") and session["payment_intent"] in incoming_subject)):
                continue
        message, attachment = matched
        subject = "VendorSettle report — order " + sid
        if previously_sent(subject):
            # Manual reconciliation instead of duplicating a possibly delivered email.
            report["status"] = "sent_without_receipt_needs_reconciliation"
            continue
        raw = download_csv(message, attachment)
        text = validate_input(raw)
        files, count = settlement(text)
        if DRY_RUN:
            report["status"] = "validated_order_ready_not_sent"
            continue
        recipient = (session.get("customer_details") or {}).get("email") or session.get("customer_email")
        # Re-read state immediately before sending to minimize duplicate
        # delivery after prior retries or delayed provider indexing.
        if previously_sent(subject):
            report["status"] = "sent_without_receipt_needs_reconciliation"
            continue
        sent = agent_send(recipient, subject,
          "Attached are your vendor summary and transaction audit CSVs, plus review notes if needed. Please review all calculations and flags against original records before paying vendors.", list(files.items()), order_id=sid + "-delivery")
        # Public record contains NO PII, CSV data, email addresses or attachments.
        record = {"schema_version": 1, "actor": "independent_github_actions",
                  "order_id_sha256": safe_key(sid), "paid_verified": True,
                  "delivery_confirmed_by_agentmail": bool(sent),
                  "sales_rows_processed": count,
                  "sent_at_utc": datetime.now(timezone.utc).isoformat(),
                  "attachment_count": len(files)}
        target.write_text(json.dumps(record, indent=2) + "\n")
        report["new_deliveries"] += 1
    if report["status"] == "not_verified":
        report["status"] = "completed"
    print(json.dumps(report))

if __name__ == "__main__":
    try:
        run()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print("COMMERCE OPERATOR ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
