"""Contract and artifact tests for the no-cost static website builder."""
import io
import unittest
from zipfile import ZipFile

from tools.gambit_sites import pack_static_site, static_site_eligible


VALID_SITE = """<!doctype html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Example landing page</title><style>
body { font-family: sans-serif; padding: 2rem; background: #fff; }
main { max-width: 50rem; margin: auto; }
@media(max-width:700px) { main { padding: 1rem; } }
</style></head><body><main><h1>Original example</h1>
<p>A real static page.</p><button type="button" id="tap">Click</button>
<script>document.getElementById('tap').addEventListener('click', () => { alert('Hello'); });</script>
</main></body></html>"""


class StaticSiteTests(unittest.TestCase):
    def test_site_is_real_zip_with_html_and_readme(self):
        data = pack_static_site(VALID_SITE)
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual({'index.html', 'README.txt'}, set(archive.namelist()))
            html = archive.read('index.html').decode()
            self.assertIn('Original example', html)
            self.assertIn('No hosting or production deployment', archive.read('README.txt').decode())

    def test_prevent_external_script_or_network_dependent_artifacts(self):
        with self.assertRaises(ValueError):
            pack_static_site(VALID_SITE.replace('<script>',
                                                '<script src="https://bad.example/script.js">'))
        with self.assertRaises(ValueError):
            pack_static_site(VALID_SITE.replace("alert('Hello')", "fetch('https://x.test')"))
        with self.assertRaises(ValueError):
            pack_static_site(VALID_SITE.replace('<h1>', '<img src="missing.png"><h1>'))

    def test_external_cta_hyperlinks_are_not_blocked_as_assets(self):
        site = VALID_SITE.replace('<h1>', '<a href="https://example.org/contact">Contact</a><h1>')
        self.assertTrue(pack_static_site(site).startswith(b'PK'))

    def test_reject_markup_and_syntax_errors(self):
        with self.assertRaises(ValueError):
            pack_static_site(VALID_SITE.replace('</main>', '</section>'))
        with self.assertRaises(ValueError):
            pack_static_site(VALID_SITE.replace("alert('Hello')", "alert("))

    def test_only_static_source_jobs_eligible(self):
        eligible = {'bounty': {'title': 'Build single-page static HTML landing page',
                               'verification': 'Deliver source ZIP only.'}}
        self.assertTrue(static_site_eligible(eligible))
        not_static = {'bounty': {'title': 'Build an interactive SaaS app',
                                 'verification': 'Requires user accounts and database.'}}
        self.assertFalse(static_site_eligible(not_static))
        not_hosted = {'bounty': {'title': 'Deploy a landing page',
                                 'verification': 'Provide a live hosted URL.'}}
        self.assertTrue(static_site_eligible(not_hosted))  # separate deployment gate rejects it


if __name__ == '__main__':
    unittest.main()
