"""Offline checks for public research with source attribution and bounded retrieval."""
import unittest

from tools import gambit_research as research


class FakeSearch:
    def __init__(self):
        self.queries = []
        self.reads = []

    def text(self, query, **kwargs):
        self.queries.append(query)
        return [
            {"title": "Example company", "href": "https://example.com/about",
             "body": "Public company information"},
            {"title": "Local secret", "href": "http://127.0.0.1:11434/api/tags",
             "body": "Must never fetch"},
        ]

    def extract(self, url, fmt="text_plain"):
        self.reads.append(url)
        return {"url": url, "content": "Example publicly available profile page"}


class SearchTests(unittest.TestCase):
    def test_public_urls_exclude_local_and_non_web_destinations(self):
        for url in ("file:///etc/passwd", "http://localhost/", "http://127.0.0.1/",
                    "http://10.0.0.4/", "http://[::1]/", "http://example.com:22/",
                    "http://example.com:bad-port/", "https://user:pass@example.com/"):
            self.assertIsNone(research.public_url(url), url)
        self.assertEqual(research.public_url("https://example.com/a#b"),
                         "https://example.com/a")

    def test_real_search_result_and_page_are_labeled(self):
        fake = FakeSearch()
        result = research.search_public_web(["example company public profile"], ddgs=fake)
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["evidence_level"], "page_read")
        self.assertEqual(fake.reads, ["https://example.com/about"])

    def test_query_count_and_duplicate_urls_are_bounded(self):
        fake = FakeSearch()
        result = research.search_public_web([f"query {i}" for i in range(20)], ddgs=fake)
        self.assertLessEqual(len(fake.queries), research.MAX_QUERIES)
        self.assertEqual(len(result["results"]), 1)

    def test_snippets_are_not_falsely_called_verified_pages(self):
        fake = FakeSearch()
        def fail_extract(url, fmt="text_plain"):
            raise RuntimeError("source_unavailable")
        fake.extract = fail_extract
        result = research.search_public_web(["example"], ddgs=fake)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["results"][0]["evidence_level"], "search_snippet")

    def test_extracted_pages_cover_multiple_queries_not_just_first_query(self):
        class DiverseSearch(FakeSearch):
            def text(self, query, **kwargs):
                self.queries.append(query)
                return [{"href": f"https://{query}{i}.example.com/info",
                         "title": f"Company {i}", "body": "Public details"}
                        for i in range(12)]

        fake = DiverseSearch()
        result = research.search_public_web(["industry", "region"], ddgs=fake)
        self.assertEqual(result["pages_read"], research.MAX_FETCHED_PAGES)
        self.assertEqual(fake.reads[0], "https://industry0.example.com/info")
        self.assertEqual(fake.reads[1], "https://region0.example.com/info")
        self.assertEqual(result["results"][1]["search_query"], "region")

    def test_long_page_keeps_relevant_fact_beyond_navigation_boilerplate(self):
        page = ('Navigation privacy settings ' * 120 +
                ' Verified contact details for Acme solar suppliers in Augusta '
                'including a public office at Broad Street. ' +
                'Additional generic legal disclaimers ' * 80)
        excerpt = research.relevant_excerpt(
            page, 'Acme solar suppliers Augusta public office', limit=700)
        self.assertLessEqual(len(excerpt), 700)
        self.assertIn('Broad Street', excerpt)


if __name__ == "__main__":
    unittest.main()
