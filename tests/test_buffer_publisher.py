import os
import unittest
from unittest.mock import patch

from tools import buffer_publisher as pub


def channel(name="oddly.useful.stuff", service="tiktok", **kw):
    data = {"id": "0123456789abcdef01234567", "name": name, "displayName": "Oddly Useful Stuff",
            "service": service, "isDisconnected": False, "isLocked": False, "isQueuePaused": False}
    data.update(kw)
    return data


def fake_gql(channels):
    def fetch(query):
        if "account {" in query:
            return {"account": {"organizations": [{"id": "0123456789abcdef01234567", "name": "O.U.S."}]}}
        if "channels(input:" in query:
            return {"channels": channels}
        raise AssertionError("Unexpected API operation")
    return fetch


class PublisherSafetyTests(unittest.TestCase):
    def test_exact_tiktok_channel_is_selected(self):
        with patch.object(pub, "gql", fake_gql([channel()])):
            self.assertEqual(pub.find_channel()["id"], "0123456789abcdef01234567")

    def test_wrong_tiktok_account_fails_closed(self):
        with patch.object(pub, "gql", fake_gql([channel(name="yaliyahmayter")])):
            with self.assertRaisesRegex(RuntimeError, "Expected exactly one"):
                pub.find_channel()

    def test_wrong_social_service_fails_closed(self):
        with patch.object(pub, "gql", fake_gql([channel(service="instagram")])):
            with self.assertRaises(RuntimeError):
                pub.find_channel()

    def test_locked_or_disconnected_channel_fails(self):
        for kw in ({"isLocked": True}, {"isDisconnected": True}, {"isQueuePaused": True}):
            with self.subTest(kw=kw), patch.object(pub, "gql", fake_gql([channel(**kw)])):
                with self.assertRaises(RuntimeError):
                    pub.find_channel()

    def test_only_public_repo_media_accepted(self):
        good = "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/campaigns/oddly-useful-stuff/media/post.png"
        self.assertEqual(pub.validated_media(good), good)
        for url in ("https://example.org/other.png", "http://raw.githubusercontent.com/a/b.png",
                    "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/file.txt",
                    "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/post.png?token=abc"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                pub.validated_media(url)

    def test_tiktok_queue_requires_real_video(self):
        good = "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/campaigns/oddly-useful-stuff/media/promo.mp4"
        asset = pub.asset_for({"text": "Useful", "video_url": good}, "queue")
        self.assertIn("video:", asset)
        self.assertIn(good, asset)
        with self.assertRaisesRegex(ValueError, "Automatic TikTok queue requires video_url"):
            pub.asset_for({"text": "Useful", "image_url":
                           "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/campaigns/oddly-useful-stuff/media/promo.png"}, "queue")
        self.assertIn("image:", pub.asset_for({"image_url":
            "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/campaigns/oddly-useful-stuff/media/promo.png"}, "draft"))

    def test_rejects_unsafe_video_hosts_and_conflicting_assets(self):
        for url in ("https://evil.example/video.mp4",
                    "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/file.mp4",
                    "https://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/campaigns/oddly-useful-stuff/media/p.mp4?x=1",
                    "http://raw.githubusercontent.com/royaltproductions-bot/Agent-Operations/main/campaigns/oddly-useful-stuff/media/p.mp4"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                pub.validated_video(url)
        with self.assertRaisesRegex(ValueError, "either image_url or video_url"):
            pub.asset_for({"image_url": "image", "video_url": "video"}, "queue")

    def test_read_only_inventory_is_not_mistaken_for_publication(self):
        candidate = channel()
        candidate["_organization_id"] = "0123456789abcdef01234567"
        def inventory_gql(query):
            self.assertIn("posts(first: 30", query)
            return {"posts": {"edges": [
                {"node": {"id": "a", "status": "draft", "externalLink": None}},
                {"node": {"id": "b", "status": "sent", "externalLink": "https://www.tiktok.com/@oddly.useful.stuff/video/example"}}
            ]}}
        with patch.object(pub, "gql", inventory_gql):
            info = pub.inspect_channel_posts(candidate)
        self.assertEqual(info["status_counts"], {"draft": 1, "sent": 1})
        self.assertEqual(info["result"], "buffer_inventory_not_publication_proof")
        self.assertIn("unverified", list(info["posts"][1].keys())[-1])

    def test_masked_or_non_ascii_key_rejected_before_request(self):
        with patch.dict(os.environ, {"BUFFER_API_KEY": "●●●●●masked"}):
            with self.assertRaisesRegex(RuntimeError, "contains invalid characters"):
                pub.gql("query { account { id } }")

    def test_bearer_prefix_rejected(self):
        with patch.dict(os.environ, {"BUFFER_API_KEY": "Bearer token123"}):
            with self.assertRaises(RuntimeError):
                pub.gql("query { account { id } }")

    def test_api_key_required(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "BUFFER_API_KEY"):
            pub.gql("query { account { id } }")


if __name__ == "__main__":
    unittest.main()
