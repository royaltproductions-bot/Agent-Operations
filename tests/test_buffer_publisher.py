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
