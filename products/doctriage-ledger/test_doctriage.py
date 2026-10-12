"""Offline regression tests. Dependencies: pypdf and reportlab."""
import csv
import tempfile
import unittest
from pathlib import Path
from reportlab.pdfgen import canvas
from pypdf import PdfWriter
from doctriage import inspect, safe_cell, main

class TriageTests(unittest.TestCase):
    def test_formula_safety(self):
        self.assertEqual(safe_cell('=HYPERLINK("evil")'), "'=HYPERLINK(\"evil\")")
        self.assertEqual(safe_cell('report.pdf'), 'report.pdf')

    def test_mixed_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_pdf = root / 'text.pdf'
            c = canvas.Canvas(str(text_pdf))
            c.drawString(70, 700, 'Hello accessible world')
            c.save()
            blank_pdf = root / 'blank.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with blank_pdf.open('wb') as f:
                writer.write(f)
            text = inspect(text_pdf, root)
            blank = inspect(blank_pdf, root)
            self.assertEqual(text['text_pages'], 1)
            self.assertEqual(blank['no_text_pages'], 1)
            self.assertEqual(blank['review_priority'], 'high')
            self.assertEqual(text['tag_tree_present'], 'no')
            output = root / 'results.csv'
            main([str(root), '--output', str(output)])
            with output.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(len(row['sha256']) == 64 for row in rows))

    def test_broken_pdf_does_not_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bad.pdf').write_bytes(b'not a PDF')
            row = inspect(root / 'bad.pdf', root)
            self.assertEqual(row['review_priority'], 'needs_access')
            self.assertEqual(row['review_reasons'], 'parse_error')

if __name__ == '__main__':
    unittest.main()
