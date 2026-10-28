"""Real, local production tools for commissioned text-based deliverables.

The model writes source text; these adapters create binary files and verify
them before they are ever represented to a buyer as completed documents.
No network, shell commands, external credentials, or paid services are used.
"""
from __future__ import annotations

from html import escape
import io
import re


OUTPUT_FILES = {
    "csv": ("gambit-research.csv", "text/csv"),
    "xlsx": ("gambit-research.xlsx",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "docx": ("gambit-report.docx",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": ("gambit-report.pdf", "application/pdf"),
}
SUPPORTED_FORMATS = frozenset({"text", *OUTPUT_FILES})
MAX_DOCUMENT_SOURCE = 80_000
MAX_DOCUMENT_BYTES = 2_000_000


def requested_output_format(details):
    """Recognize explicit final formats, not formats mentioned as input.

    Multiple requested file types are intentionally not treated as one file.
    """
    bounty = details.get("bounty") or {}
    title = str(bounty.get("title") or "")
    verification = bounty.get("verification") or ""
    if not isinstance(verification, str):
        import json
        verification = json.dumps(verification, ensure_ascii=False)
    final_text = title + " " + verification
    matches = set()
    for name in ("pdf", "docx", "xlsx", "csv", "pptx", "mp4", "mov", "png", "jpg", "jpeg"):
        pattern = rf"\b{re.escape(name)}\b"
        for found in re.finditer(pattern, final_text, re.I):
            prefix = final_text[max(0, found.start() - 70):found.start()]
            suffix = final_text[found.end():found.end() + 35]
            if (re.search(r"\b(?:submit|deliver|upload|export|provide|produce|create|generate|"
                          r"compile|write|final|finished|download|attach|output|as|in)\b",
                          prefix, re.I)
                    or re.search(r"^\s*(?:report|document|file|spreadsheet|deck|video|"
                                 r"presentation|table|workbook)\b", suffix, re.I)):
                matches.add("jpg" if name == "jpeg" else name)
    return matches


def _paragraph_lines(text):
    """Convert text/Markdown headings and lists into simple document structure."""
    if not text.strip() or len(text) > MAX_DOCUMENT_SOURCE:
        raise ValueError("document_source_invalid")
    result = []
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        if match := re.match(r"^#{1,3}\s+(.+)", value):
            result.append(("heading", match.group(1)))
        elif match := re.match(r"^(?:[-*]|\d+\.)\s+(.+)", value):
            result.append(("bullet", match.group(1)))
        else:
            result.append(("body", value))
    if not result:
        raise ValueError("document_source_invalid")
    return result


def render_docx(text):
    from docx import Document
    from docx.shared import Inches
    doc = Document()
    for kind, line in _paragraph_lines(text):
        if kind == "heading":
            doc.add_heading(line, level=2)
        elif kind == "bullet":
            doc.add_paragraph(line, style="List Bullet")
        else:
            doc.add_paragraph(line)
    doc.sections[0].top_margin = Inches(0.8)
    doc.sections[0].bottom_margin = Inches(0.8)
    out = io.BytesIO()
    doc.save(out)
    data = out.getvalue()
    if not 500 < len(data) <= MAX_DOCUMENT_BYTES:
        raise ValueError("docx_output_invalid")
    verify = Document(io.BytesIO(data))
    if not any(p.text.strip() for p in verify.paragraphs):
        raise ValueError("docx_output_unreadable")
    return data


def render_pdf(text):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from pypdf import PdfReader
    data_out = io.BytesIO()
    document = SimpleDocTemplate(data_out, pagesize=letter, topMargin=48,
                                 bottomMargin=48, leftMargin=52, rightMargin=52)
    styles = getSampleStyleSheet()
    heading = ParagraphStyle("GambitHeading", parent=styles["Heading2"],
                             textColor=colors.HexColor("#142A4E"), spaceBefore=12)
    body = ParagraphStyle("GambitBody", parent=styles["BodyText"],
                          fontName="Helvetica", fontSize=10.2, leading=14.5,
                          alignment=TA_LEFT, spaceAfter=7)
    bullet = ParagraphStyle("GambitBullet", parent=body, leftIndent=17,
                            firstLineIndent=-10)
    story = []
    for kind, line in _paragraph_lines(text):
        safe = escape(line)
        # Preserve long URLs without passing Markdown or model-supplied markup
        # through the ReportLab paragraph parser.
        if kind == "heading":
            story.append(Paragraph(safe, heading))
        elif kind == "bullet":
            story.append(Paragraph("&#8226; " + safe, bullet))
        else:
            story.append(Paragraph(safe, body))
    story.append(Spacer(1, 8))
    document.build(story)
    data = data_out.getvalue()
    if not 500 < len(data) <= MAX_DOCUMENT_BYTES:
        raise ValueError("pdf_output_invalid")
    verify = PdfReader(io.BytesIO(data))
    if not 1 <= len(verify.pages) <= 40 or not any(
            (p.extract_text() or "").strip() for p in verify.pages):
        raise ValueError("pdf_output_unreadable")
    return data


def make_document(format_name, text):
    """Use an actual generation adapter, never a model's file claim."""
    if format_name == "pdf":
        return render_pdf(text)
    if format_name == "docx":
        return render_docx(text)
    raise ValueError("unsupported_document_format")
