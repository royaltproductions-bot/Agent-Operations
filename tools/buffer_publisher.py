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
try:
    from .public_voice_guard import assert_autonomous_voice
except ImportError:
    # Support direct CLI entrypoint as well as unittest package imports.
    from public_voice_guard import assert_autonomous_voice

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
        for candidate in (gql(query).get("channels") or []):
            candidate["_organization_id"] = org["id"]
            candidates.append(candidate)
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

def validated_video(url):
    """Only approved, durable HTTPS MP4 assets within the public campaign media folder."""
    uri = urlparse(url if isinstance(url, str) else "")
    if (uri.scheme != "https" or uri.hostname != "raw.githubusercontent.com"
            or uri.username or uri.password or uri.query or uri.fragment
            or not uri.path.startswith("/royaltproductions-bot/Agent-Operations/")
            or not uri.path.startswith("/royaltproductions-bot/Agent-Operations/main/campaigns/oddly-useful-stuff/media/")
            or not uri.path.lower().endswith(".mp4")):
        raise ValueError("video_url must point to a public MP4 in Oddly Useful Stuff's GitHub media folder")
    return url

def asset_for(post, mode):
    image_url = post.get("image_url")
    video_url = post.get("video_url")
    if image_url and video_url:
        raise ValueError("Choose either image_url or video_url, not both")
    if video_url:
        video = validated_video(video_url)
        return " assets: [{video: {url: " + quoted(video) + "}}]"
    if image_url:
        if mode == "queue":
            raise ValueError("Automatic TikTok queue requires video_url; image-only posts may require notification publishing. Use draft mode instead.")
        return " assets: [{image: {url: " + quoted(validated_media(image_url)) + "}}]"
    if mode == "queue":
        raise ValueError("Live TikTok queue requires a real public video_url")
    return ""

def recent_channel_posts(channel):
    """Fetch last 30 Buffer posts for remote-state verification before mutation."""
    query = (
        'query { posts(first: 30 input: { organizationId: '
        + quoted(channel["_organization_id"])
        + ' filter: { channelIds: [' + quoted(channel["id"])
        + '] } }) { edges { node { id text status schedulingType dueAt sentAt externalLink } } } }'
    )
    edges = ((gql(query).get("posts") or {}).get("edges") or [])
    return [edge.get("node") or {} for edge in edges]

def assert_no_duplicate(channel, text, mode):
    """Fail closed when retries risk duplicate live/queued posts or duplicate drafts."""
    if mode not in ("draft", "queue"):
        return
    for post in recent_channel_posts(channel):
        status = str(post.get("status") or "").lower()
        if str(post.get("text") or "").strip() != text:
            continue
        if (mode == "draft" and status in ("draft", "scheduled", "sent")) or (
                mode == "queue" and status in ("scheduled", "sent")):
            raise RuntimeError(
                "Potential duplicate " + status + " Buffer post " + str(post.get("id")) +
                "; verify remote state instead of creating another.")

def inspect_channel_posts(channel):
    """Read-only inventory: Buffer status is not itself independent proof of a live TikTok post."""
    posts = recent_channel_posts(channel)
    counts = {}
    for post in posts:
        status = str(post.get("status") or "unknown").lower()
        counts[status] = counts.get(status, 0) + 1
    return {
        "result": "buffer_inventory_not_publication_proof",
        "channel": channel["name"],
        "sample_size": len(posts),
        "status_counts": counts,
        "posts": [{
            "id": p.get("id"), "status": p.get("status"),
            "scheduling_type": p.get("schedulingType"),
            "due_at": p.get("dueAt"), "sent_at": p.get("sentAt"),
            "external_url_unverified": p.get("externalLink")
        } for p in posts],
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "draft", "queue", "inventory"], default="check")
    parser.add_argument("--input", help="JSON file with text and image_url; required for draft/queue")
    args = parser.parse_args()
    channel = find_channel()
    print(json.dumps({"connection": "verified", "channel": channel.get("name"), "service": "tiktok", "id": channel["id"]}))
    if args.mode == "check":
        return
    if args.mode == "inventory":
        print(json.dumps(inspect_channel_posts(channel)))
        return
    if not args.input:
        raise ValueError("--input is required for draft/queue")
    with open(args.input, encoding="utf-8") as file:
        post = json.load(file)
    text = str(post.get("text") or "").strip()
    if not text or len(text) > 2000:
        raise ValueError("Post text is missing or exceeds our safety length")
    assert_autonomous_voice(text)
    assets = asset_for(post, args.mode)
    assert_no_duplicate(channel, text, args.mode)
    if args.mode == "queue":
        if post.get("commercial_promotion") is True:
            raise RuntimeError("Commercial TikTok promotions require native content disclosure; this Buffer API route does not expose that setting.")
        if os.environ.get("BUFFER_QUEUE_ENABLED") != "YES":
            raise RuntimeError("Queuing disabled. Set BUFFER_QUEUE_ENABLED=YES only after a successful draft test.")
        if post.get("approved_for_queue") is not True:
            raise RuntimeError("Content needs approved_for_queue=true to enter live publishing queue.")
    graph = (
        "mutation { createPost(input: {"
        " text: " + quoted(text) +
        " channelId: " + quoted(channel["id"]) +
        " schedulingType: automatic mode: " + ("shareNow" if args.mode == "queue" and post.get("publish_now") is True else "addToQueue") +
        " saveToDraft: " + ("true" if args.mode == "draft" else "false") +
        (" metadata: {tiktok: {isAiGenerated: true}}" if post.get("is_ai_generated") is True else "") +
        assets +
        " }) { ... on PostActionSuccess { post { id text dueAt } }"
        " ... on MutationError { message } } }"
    )
    result = (gql(graph).get("createPost") or {})
    if not result.get("post", {}).get("id"):
        raise RuntimeError("Buffer rejected post: " + str(result.get("message") or result))
    info = result["post"]
    result_label = ("draft_created" if args.mode == "draft" else
                    "publish_requested_not_live_verified" if post.get("publish_now") is True else "queued_not_live_verified")
    print(json.dumps({"result": result_label,
                      "post_id": info["id"], "due_at": info.get("dueAt"),
                      "channel": channel["id"]}))

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError) as err:
        print("ERROR: " + str(err), file=sys.stderr)
        sys.exit(1)
