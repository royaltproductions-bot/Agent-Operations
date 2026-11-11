"""Every advertised document output must be an actual, readable file."""
import io
import unittest

from tools.gambit_artifacts import (OUTPUT_FILES, make_document,
                                    requested_output_format)


class ArtifactTests(unittest.TestCase):
    def test_pdf_file_is_real_and_its_text_is_readable(self):
        from pypdf import PdfReader
        result = make_document('pdf', '# Quarterly findings\n\nRevenue was 4,251.\n\n- Source A')
        self.assertTrue(result.startswith(b'%PDF-'))
        text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(result)).pages)
        self.assertIn('Quarterly findings', text)
        self.assertIn('4,251', text)

    def test_docx_file_is_real_and_its_text_is_readable(self):
        from docx import Document
        result = make_document('docx', '# Research note\n- Item one\nA real finding')
        self.assertTrue(result.startswith(b'PK'))
        paragraphs = '\n'.join(p.text for p in Document(io.BytesIO(result)).paragraphs)
        self.assertIn('Research note', paragraphs)
        self.assertIn('A real finding', paragraphs)

    def test_pptx_file_is_real_and_contains_text(self):
        from pptx import Presentation
        result = make_document('pptx', '# Market brief\n- Finding one\n- Finding two')
        self.assertTrue(result.startswith(b'PK'))
        deck = Presentation(io.BytesIO(result))
        text = '\n'.join(
            shape.text for slide in deck.slides for shape in slide.shapes
            if hasattr(shape, 'text'))
        self.assertIn('Market brief', text)
        self.assertIn('Finding one', text)

    def test_pdf_escapes_untrusted_markup(self):
        from pypdf import PdfReader
        content = '<script>malicious</script> & plain text'
        result = make_document('pdf', content)
        text = PdfReader(io.BytesIO(result)).pages[0].extract_text()
        self.assertIn(content, text)

    def test_empty_and_oversized_content_is_rejected(self):
        for value in ['', '  ', 'a' * 90_000]:
            with self.assertRaises(ValueError):
                make_document('pdf', value)

    def test_unknown_formats_are_never_claimed(self):
        self.assertEqual({'pdf', 'docx', 'pptx', 'xlsx', 'csv', 'site_zip', 'app_zip'}, set(OUTPUT_FILES))
        with self.assertRaises(ValueError):
            make_document('mp4', 'A video')

    def test_read_only_pdf_input_does_not_force_pdf_output(self):
        details = {'bounty': {'title': 'Summarize the attached report',
                              'verification': 'Read the PDF and deliver a text summary.'}}
        self.assertEqual(set(), requested_output_format(details))


if __name__ == '__main__':
    unittest.main()
