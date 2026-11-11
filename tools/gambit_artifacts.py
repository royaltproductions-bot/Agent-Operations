"""Real, local production tools for commissioned text-based deliverables.

The model writes source text; these adapters create binary files and verify
them before they are ever represented to a buyer as completed documents.
No network, shell commands, external credentials, or paid services are used.
"""
from __future__ import annotations

from html import escape
import io
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import wave


OUTPUT_FILES = {
    "csv": ("gambit-research.csv", "text/csv"),
    "xlsx": ("gambit-research.xlsx",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "docx": ("gambit-report.docx",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": ("gambit-report.pdf", "application/pdf"),
    "pptx": ("gambit-presentation.pptx",
             "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "mp4": ("gambit-explainer.mp4", "video/mp4"),
    "site_zip": ("gambit-static-site.zip", "application/zip"),
    "app_zip": ("gambit-tested-application.zip", "application/zip"),
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



def render_pptx(text):
    from pptx import Presentation
    from pptx.util import Pt
    lines = _paragraph_lines(text)
    prs = Presentation()
    first = lines[0][1][:120]
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = first
    if len(slide.placeholders) > 1:
        slide.placeholders[1].text = "Gambit deliverable"
    body_lines = [line for _, line in lines[1:]] or [first]
    for start in range(0, len(body_lines), 6):
        chunk = body_lines[start:start + 6]
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = chunk[0][:100]
        frame = slide.placeholders[1].text_frame
        frame.clear()
        for idx, line in enumerate(chunk[1:] or chunk):
            p = frame.paragraphs[0] if idx == 0 else frame.add_paragraph()
            p.text = line[:700]
            p.font.size = Pt(20)
    out = io.BytesIO()
    prs.save(out)
    data = out.getvalue()
    if not 1000 < len(data) <= MAX_DOCUMENT_BYTES:
        raise ValueError("pptx_output_invalid")
    verify = Presentation(io.BytesIO(data))
    if not verify.slides:
        raise ValueError("pptx_output_unreadable")
    return data



def render_mp4(text):
    """Create a bounded captioned slideshow with local synthesized narration."""
    from PIL import Image, ImageDraw, ImageFont
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe") or not shutil.which("espeak-ng"):
        raise ValueError("mp4_runtime_unavailable")
    clean = " ".join(line for _, line in _paragraph_lines(text))
    clean = re.sub(r"\s+", " ", clean).strip()[:1600]
    if len(clean) < 20:
        raise ValueError("mp4_source_invalid")
    words = clean.split()
    chunks, current = [], []
    for word in words:
        current.append(word)
        if len(" ".join(current)) >= 360 and len(chunks) < 3:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    chunks = chunks[:4]
    with tempfile.TemporaryDirectory(prefix="gambit-video-") as folder:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, 42) if os.path.exists(font_path) else ImageFont.load_default()
        slide_paths = []
        for idx, chunk in enumerate(chunks):
            image = Image.new("RGB", (1280, 720), "white")
            draw = ImageDraw.Draw(image)
            title = f"Explainer {idx + 1}/{len(chunks)}"
            draw.text((70, 55), title, fill="black", font=font)
            wrapped = textwrap.wrap(chunk, width=48)
            y = 150
            for line in wrapped[:10]:
                draw.text((70, y), line, fill="black", font=font)
                y += 55
            slide_path = f"{folder}/slide-{idx:03d}.png"
            image.save(slide_path, format="PNG")
            slide_paths.append(slide_path)
        wav_path = f"{folder}/narration.wav"
        subprocess.run(["espeak-ng", "-s", "160", "-w", wav_path, clean],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60)
        with wave.open(wav_path, "rb") as audio:
            audio_seconds = audio.getnframes() / float(audio.getframerate())
        per_slide = max(2.0, (audio_seconds + 0.75) / len(slide_paths))
        concat_path = f"{folder}/slides.txt"
        with open(concat_path, "w", encoding="utf-8") as handle:
            for slide_path in slide_paths:
                handle.write("file '" + slide_path.replace("'", "'\\''") + "'\n")
                handle.write(f"duration {per_slide:.3f}\n")
            handle.write("file '" + slide_paths[-1].replace("'", "'\\''") + "'\n")
        output_path = f"{folder}/explainer.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-i", wav_path, "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", "-shortest", output_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
           timeout=120)
        data = open(output_path, "rb").read()
        if not 10_000 < len(data) <= 25_000_000 or b"ftyp" not in data[:32]:
            raise ValueError("mp4_output_invalid")
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", output_path
        ], check=True, capture_output=True, text=True, timeout=20)
        if float(probe.stdout.strip() or 0) <= 0:
            raise ValueError("mp4_output_unreadable")
        return data


def make_document(format_name, text):
    """Use an actual generation adapter, never a model's file claim."""
    if format_name == "pdf":
        return render_pdf(text)
    if format_name == "docx":
        return render_docx(text)
    if format_name == "pptx":
        return render_pptx(text)
    if format_name == "mp4":
        return render_mp4(text)
    raise ValueError("unsupported_document_format")
