#!/usr/bin/env python3
"""Guarded Buffer -> TikTok publisher for Oddly Useful Stuff.

Requires only Python 3 standard library, BUFFER_API_KEY secret, and an
explicit content JSON for creating drafts or queuing posts.
Uses the Buffer GraphQL API documented at https://developers.buffer.com.
No publishing action occurs in --check mode.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

API = "https://api.buffer.com"
EXPECTED_HANDLE = "oddly.useful.stuff"

def gql(query):
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BUFFER_API_KEY is missing. Add it as a GitHub Actions secret; never commit or paste it.")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise RuntimeError(
            "BUFFER_API_KEY contains invalid characters. "
            "Go to Buffer Settings > API and use Copy key to copy the full "
            "raw token; replace the existing GitHub repository secret value. "
            "Do not copy masked dots, a label, or a URL."
        )
    if key.startswith(("Bearer ", "bearer ", "{", "http")):
        raise RuntimeError("BUFFER_API_KEY must be the raw Buffer API token, not a prefix, JSON, or URL.")
    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        API, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Buffer returned HTTP " + str(exc.code)) from None
    if result.get("errors"):
        raise RuntimeError("Buffer GraphQL error: " + "; ".join(str(x.get("message", "unknown")) for x in result["errors"]))
    return result.get("data") or {}

def quoted(s):
    return json.dumps(str(s), ensure_ascii=False)

def find_channel():
    account = gql("query { account { organizations { id name } } }")
    orgs = (account.get("account") or {}).get("organizations") or []
    candidates = []
    for org in orgs:
        query = ('query { channels(input: { organizationId: '
                 + quoted(org["id"]) + ' }) { id name displayName externalLink service isDisconnected isLocked isQueuePaused } }')
        candidates.extend(gql(query).get("channels") or [])
    # Refuse to publish to ANY non-TikTok account or an approximate handle match.
    matches = []
    for channel in candidates:
        if str(channel.get("service", "")).lower() != "tiktok":
            continue
        values = [str(channel.get(k) or "").strip().lower().lstrip("@")
                  for k in ("name", "displayName")]
        link = str(channel.get('externalLink') or '').strip()
        if link:
            parsed = urlparse(link if '://' in link else 'https://' + link)
            if (parsed.hostname or '').lower().endswith('tiktok.com'):
                values.append(parsed.path.strip('/').lstrip('@').lower())
        if EXPECTED_HANDLE in values:
            matches.append(channel)
    if len(matches) != 1:
        safe = [{"name": c.get("name"), "service": c.get("service")} for c in candidates]
        raise RuntimeError("Expected exactly one TikTok @" + EXPECTED_HANDLE
                           + ", found " + str(len(matches)) + ". Connected channels: " + json.dumps(safe))
    channel = matches[0]
    if channel.get("isDisconnected") or channel.get("isLocked"):
        raise RuntimeError("Expected TikTok channel exists but is disconnected or locked in Buffer")
    if channel.get("isQueuePaused"):
        raise RuntimeError("Expected TikTok channel queue is paused in Buffer; no queued posts will publish")
    return channel

def validated_media(url):
    if not isinstance(url, str):
        raise ValueError("image_url must be a public HTTPS URL")
    uri = urlparse(url)
    if uri.scheme != "https" or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError("image_url must be a clean HTTPS URL (no credentials, query or fragment)")
    if uri.hostname == "raw.githubusercontent.com":
        allowed = uri.path.startswith("/royaltproductions-bot/Agent-Operations/")
    elif uri.hostname == "royaltproductions-bot.github.io":
        allowed = uri.path.startswith("/Agent-Operations/")
    else:
        allowed = False
    if not allowed or not uri.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise ValueError("Use an image hosted publicly under Agent-Operations GitHub or GitHub Pages")
    return url

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "draft", "queue"], default="check")
    parser.add_argument("--input", help="JSON file with text and image_url; required for draft/queue")
    args = parser.parse_args()
    channel = find_channel()
    print(json.dumps({"connection": "verified", "channel": channel.get("name"), "service": "tiktok", "id": channel["id"]}))
    if args.mode == "check":
        return
    if not args.input:
        raise ValueError("--input is required for draft/queue")
    with open(args.input, encoding="utf-8") as file:
        post = json.load(file)
    text = str(post.get("text") or "").strip()
    if not text or len(text) > 2000:
        raise ValueError("Post text is missing or exceeds our safety length")
    image = validated_media(post.get("image_url"))
    if args.mode == "queue":
        if os.environ.get("BUFFER_QUEUE_ENABLED") != "YES":
            raise RuntimeError("Queuing disabled. Set BUFFER_QUEUE_ENABLED=YES only after a successful draft test.")
        if post.get("approved_for_queue") is not True:
            raise RuntimeError("Content needs approved_for_queue=true to enter live publishing queue.")
    graph = (
        "mutation { createPost(input: {"
        " text: " + quoted(text) +
        " channelId: " + quoted(channel["id"]) +
        " schedulingType: automatic mode: addToQueue" +
        " saveToDraft: " + ("true" if args.mode == "draft" else "false") +
        " assets: [{image: {url: " + quoted(image) + "}}]"
        " }) { ... on PostActionSuccess { post { id text dueAt } }"
        " ... on MutationError { message } } }"
    )
    result = (gql(graph).get("createPost") or {})
    if not result.get("post", {}).get("id"):
        raise RuntimeError("Buffer rejected post: " + str(result.get("message") or result))
    info = result["post"]
    print(json.dumps({"result": "draft_created" if args.mode == "draft" else "queued",
                      "post_id": info["id"], "due_at": info.get("dueAt"),
                      "channel": channel["id"]}))

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError) as err:
        print("ERROR: " + str(err), file=sys.stderr)
        sys.exit(1)
