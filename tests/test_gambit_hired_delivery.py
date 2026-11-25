"""Offline hired-work delivery: real binary generation, mocked transport only."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools import gambit_worker as w
from tools import gambit_artifacts as artifacts


class HiredDeliveryTests(unittest.TestCase):
    def test_real_pdf_mp4_xlsx_hire_to_submission(self):
        cases = [
            ("pdf", "Create PDF report", "Deliver a PDF report", "# Invoice review\nVerify totals and payment dates."),
            ("xlsx", "Produce invoice template XLSX", "Deliver an XLSX spreadsheet", "invoice,amount,status\nDEMO-1,250,unpaid"),
            ("mp4", "Create captioned explainer video", "Deliver MP4 video", "# Invoice review\nVerify the total and confirm payment status."),
            ("pptx", "Create PPTX deck", "Deliver a PPTX presentation", "# Invoice review\n- Verify total\n- Confirm payment status"),
        ]
        for fmt, title, verify, content in cases:
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as tmp:
                details = {"bounty":{"_id":"fixture","status":"claimed","payout_cents":1000,
                                    "title":title,"verification":verify},
                           "claim":{"status":"active","claim_id":"claim-fixture"}}
                result = {"status":"ready","deliverable_format":fmt,"deliverable_title":title,
                          "deliverable_text":content}
                state={"bounties":{},"recent_outcomes":[]}
                calls=[]
                binary=[]
                class Response:
                    status=200
                    def __enter__(self): return self
                    def __exit__(self,*args): return False
                class Opener:
                    def open(self,request,timeout):
                        binary.append(request.data)
                        self_outer.assertEqual(request.get_method(),"PUT")
                        return Response()
                self_outer=self
                def api(path,method="GET",payload=None):
                    if method=="GET": return details
                    calls.append((path,payload))
                    if path.endswith("/attachments"):
                        return {"attachment_id":"att-fixture","upload_url":"https://example.com/upload",
                                "headers":{"content-type":artifacts.OUTPUT_FILES[fmt][1]}}
                    if path.endswith("/complete"): return {"status":"complete"}
                    if path.endswith("/submissions"): return {"submission_id":"submission-fixture"}
                    raise AssertionError(path)
                with patch.object(w,"api",side_effect=api), patch.object(w,"messages_for",return_value=[]), \
                     patch.object(w,"produce",return_value=result), patch.object(w,"review",return_value={"approved":True}), \
                     patch.object(w,"save_state"), patch.object(w,"CURRENT_EVENTS",[]), \
                     patch("tools.gambit_research.public_url",return_value=True), \
                     patch.object(w.urllib.request,"build_opener",return_value=Opener()):
                    w.handle_hired({"_id":"fixture"},state)
                    w.handle_hired({"_id":"fixture"},state)
                self.assertEqual(len(binary),1)
                self.assertEqual(len(calls),3)
                self.assertTrue(calls[1][0].endswith("/attachments/att-fixture/complete"))
                self.assertTrue(calls[2][0].endswith("/submissions"))
                self.assertEqual(calls[2][1]["deliverables"][0]["data"]["attachment_id"],"att-fixture")
                self.assertEqual(state["bounties"]["fixture"]["submission_id"],"submission-fixture")
                data=binary[0]
                path=Path(tmp)/("fixture."+fmt)
                path.write_bytes(data)
                if fmt=="pdf":
                    from pypdf import PdfReader
                    self.assertIn("Invoice review",PdfReader(io.BytesIO(data)).pages[0].extract_text())
                elif fmt=="xlsx":
                    from openpyxl import load_workbook
                    self.assertEqual(load_workbook(io.BytesIO(data)).active["A2"].value,"DEMO-1")
                elif fmt=="pptx":
                    from pptx import Presentation
                    self.assertGreater(len(Presentation(io.BytesIO(data)).slides),0)
                else:
                    probe=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams",
                        "-show_format","-of","json",str(path)],text=True,timeout=30))
                    self.assertGreater(float(probe["format"]["duration"]),0)
                    self.assertEqual({s["codec_type"] for s in probe["streams"]},{"audio","video"})
                    from PIL import Image
                    frame=Path(tmp)/"frame.png"
                    subprocess.run(["ffmpeg","-v","error","-y","-i",str(path),"-frames:v","1",str(frame)],check=True,timeout=30)
                    image=Image.open(frame).convert("L")
                    # Visible dark text in the caption region, not a blank slideshow.
                    crop=image.crop((60,140,1200,650))
                    self.assertGreater(sum(v<100 for v in crop.getdata()),100)
                out=os.getenv("GAMBIT_TEST_ARTIFACT_DIR")
                if out:
                    destination=Path(out); destination.mkdir(parents=True,exist_ok=True)
                    (destination/path.name).write_bytes(data)
