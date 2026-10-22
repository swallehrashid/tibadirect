"""Documents built on the fly for the AI tests, and the synthetic dataset as a fixture."""

import csv
import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "synthetic"
HAS_TESSERACT = shutil.which("tesseract") is not None
needs_tesseract = pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract is not installed")
needs_dataset = pytest.mark.skipif(not DATA_DIR.exists(), reason="synthetic dataset not present")


def text_pdf(*pages: list[str], bold_last_line: bool = False) -> bytes:
    """A PDF with a real text layer: each argument is one page, a list of lines."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    for lines in pages:
        y = 780
        for index, line in enumerate(lines):
            bold = bold_last_line and index == len(lines) - 1
            pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 12)
            pdf.drawString(50, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def text_image(lines: list[str], size: int = 34) -> Image.Image:
    """White page with black text, big enough for OCR to read reliably."""
    font = ImageFont.load_default(size=size)
    image = Image.new("RGB", (1400, 120 + len(lines) * (size + 24)), "white")
    draw = ImageDraw.Draw(image)
    y = 40
    for line in lines:
        draw.text((50, y), line, fill="black", font=font)
        y += size + 24
    return image


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def scan_pdf(*pages: list[str]) -> bytes:
    """A PDF whose pages are pictures of text and have no text layer, like a scanner makes."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    for lines in pages:
        image = text_image(lines)
        height = 595 * image.height / image.width  # keep the proportions, or OCR sees squashed text
        pdf.drawImage(ImageReader(image), 0, 842 - height - 20, width=595, height=height)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def labels() -> list[dict[str, str]]:
    with (DATA_DIR / "labels.csv").open() as handle:
        return list(csv.DictReader(handle))


def ground_truth() -> dict[str, dict]:
    with (DATA_DIR / "ground_truth.jsonl").open() as handle:
        return {row["doc_id"]: row for row in map(json.loads, handle)}


def document(doc_id: str) -> tuple[bytes, str]:
    """A synthetic document's bytes and MIME type."""
    row = next(r for r in labels() if r["doc_id"] == doc_id)
    path = DATA_DIR / "invoices" / row["filename"]
    mime = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}[path.suffix[1:]]
    return path.read_bytes(), mime


def overlay_pdf(
    *, box_before_text: bool = False, box_color: tuple = (1, 1, 1), retype_at: int = 720
) -> bytes:
    """A bill whose total was changed the way an editor does it: the old value is covered by a box
    and the new one typed on top. With ``box_before_text`` the box is drawn first instead, so
    nothing is hidden; a non-white ``box_color`` is a shaded band, not a cover (and the new value
    is then typed lower down, so it does not overstrike the old one)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, 780, "Kilele Mission Hospital  Invoice No.: KMH/26/059153")
    pdf.drawString(50, 760, "Maternity consultation 3,300.00 and other charges of the stay")

    def box() -> None:
        pdf.setFillColorRGB(*box_color)
        pdf.rect(380, 715, 120, 20, stroke=0, fill=1)
        pdf.setFillColorRGB(0, 0, 0)

    if box_before_text:
        box()
        pdf.drawString(400, 720, "Total 74,000")
    else:
        pdf.drawString(400, 720, "Total 46,250")
        box()
        pdf.drawString(400, retype_at, "Total 74,000")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def double_drawn_pdf() -> bytes:
    """Text drawn twice at the same spot, the old trick for fake bold. Nothing is hidden."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    pdf.setFont("Helvetica", 12)
    for _ in range(2):
        pdf.drawString(50, 780, "Kilele Mission Hospital invoice with a fake bold heading line")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def overstruck_pdf() -> bytes:
    """A different value typed over the old one with no box, so both show through each other."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, 780, "Kilele Mission Hospital invoice number KMH/26/059153 follows here")
    pdf.drawString(400, 720, "Total 46,250")
    pdf.drawString(400, 720, "Total 74,000")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
