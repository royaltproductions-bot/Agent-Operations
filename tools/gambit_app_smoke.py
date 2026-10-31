"""Independent real backend + desktop/mobile QA smoke for Gambit."""
from tools.gambit_app_builder import verify_project
from tools.gambit_visual_qa import render_browser_evidence

FILES = {
"server.py": """import json, os
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
class App(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=='/api/status':
            body=json.dumps({'ok':True}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
        elif self.path=='/':
            body=Path('index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/html')
        else:
            self.send_error(404)
            return
        self.end_headers()
        self.wfile.write(body)
if __name__=='__main__':
    ThreadingHTTPServer(('127.0.0.1',int(os.getenv('PORT','8765'))),App).serve_forever()
""",
"index.html": """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opportunity Atlas</title><link rel="stylesheet" href="assets/app.css"></head>
<body><main><p>Business research workspace</p><h1>Opportunity Atlas</h1>
<p>Research markets, compare opportunities, and structure the next steps.</p>
<section><label for="idea">Describe a business opportunity</label>
<textarea id="idea">Local garden education startup</textarea>
<button id="analyze">Analyze idea</button>
<output id="report">Enter your opportunity to begin.</output></section></main>
<script src="assets/app.js"></script></body></html>""",
"assets/app.css": """body{margin:0;color:#f2f8ff;background:linear-gradient(135deg,#09132c,
#254d84,#621d80);font:19px Arial,sans-serif;min-height:100vh}
main{max-width:850px;padding:8vw 30px;margin:auto}
h1{font-size:clamp(40px,8vw,84px);margin:14px 0}
section{background:#ffffff25;padding:30px;border:1px solid #fff8;
border-radius:22px;margin-top:50px}label{display:block}textarea{display:block;
width:95%;height:100px;font:inherit;margin:15px 0}button{padding:16px 24px;
background:#88f0e2;color:#08172a;border:0;border-radius:12px}
output{display:block;margin-top:24px;padding:15px;background:#0710288a}
@media(max-width:480px){main{padding:25px 16px}section{padding:17px}}""",
"assets/app.js": """document.getElementById('analyze').addEventListener('click',()=>{
const idea=document.getElementById('idea').value.trim();
document.getElementById('report').textContent=idea?
'Start by identifying customers, costs, competition, and risks for: '+idea:
'Describe your opportunity first.';});""",
"README.md": "# Opportunity Atlas\n\nRequires Python 3.12+. Run python server.py locally. No API key required for the offline example.\n",
"tests/test_app.py": """import unittest
from pathlib import Path
class Smoke(unittest.TestCase):
 def test_app_files(self):
  self.assertIn('Opportunity Atlas',Path('index.html').read_text())
  self.assertIn('addEventListener',Path('assets/app.js').read_text())
 def test_import(self):
  import server
  self.assertTrue(hasattr(server,'App'))
"""
}
if __name__ == "__main__":
    evidence=verify_project(FILES,render_browser_evidence)
    assert evidence["desktop"] and evidence["mobile"]
    print("APP_SOURCE_SMOKE_PASSED",evidence["http"])
