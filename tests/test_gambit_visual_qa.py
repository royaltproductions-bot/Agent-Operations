"""Browser QA is evidence only, not an aesthetic-quality certification."""
import importlib.util
import shutil
import unittest

from tools.gambit_visual_qa import VIEWPORTS, render_browser_evidence


class VisualQATests(unittest.TestCase):
    def test_mobile_and_desktop_are_mandatory(self):
        self.assertEqual((390, 844), VIEWPORTS['mobile'])
        self.assertEqual((1440, 900), VIEWPORTS['desktop'])

    @unittest.skipUnless(importlib.util.find_spec('playwright') and
                         (shutil.which('google-chrome') or shutil.which('google-chrome-stable')),
                         'Browser QA integration uses Chrome on the hosted runner')
    def test_browser_actually_renders_desktop_and_mobile(self):
        html = '''<!doctype html><html><head><title>Visual fixture</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>body{font-family:sans-serif;background:#12345a;color:white;padding:4rem}
        main{max-width:55rem;margin:auto}h1{font-size:54px}
        @media(max-width:600px){body{padding:1rem}h1{font-size:34px}}</style></head>
        <body><main><h1>Professional rendering fixture</h1>
        <p>A page large enough to exercise the real Chrome renderer at two widths.</p>
        </main></body></html>'''
        evidence = render_browser_evidence(html)
        self.assertEqual({'desktop', 'mobile'}, set(evidence))
        self.assertTrue(all(x['screenshot'].startswith(b'\x89PNG')
                            for x in evidence.values()))


if __name__ == '__main__':
    unittest.main()
