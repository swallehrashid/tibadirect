"""Small PDFs and images built in memory for the upload tests, including hostile ones."""

import io

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


def _bytes(writer: PdfWriter) -> bytes:
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def clean_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    return _bytes(writer)


def pdf_with_catalog_entry(name: str, value: object) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer._root_object[NameObject(name)] = value  # type: ignore[index]
    return _bytes(writer)


def pdf_with_javascript() -> bytes:
    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('hello')"),
        }
    )
    return pdf_with_catalog_entry("/OpenAction", action)


def pdf_with_launch_action() -> bytes:
    action = DictionaryObject(
        {NameObject("/S"): NameObject("/Launch"), NameObject("/F"): TextStringObject("cmd.exe")}
    )
    return pdf_with_catalog_entry("/OpenAction", action)


def pdf_with_page_action() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    page[NameObject("/AA")] = DictionaryObject(
        {
            NameObject("/O"): DictionaryObject(
                {
                    NameObject("/S"): NameObject("/JavaScript"),
                    NameObject("/JS"): TextStringObject("1"),
                }
            )
        }
    )
    return _bytes(writer)


def pdf_with_attachment() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_attachment("payload.exe", b"MZ not really")
    return _bytes(writer)


def pdf_with_action_inside_an_array() -> bytes:
    action = DictionaryObject({NameObject("/S"): NameObject("/Launch")})
    return pdf_with_catalog_entry("/Hidden", ArrayObject([NumberObject(1), action]))


def encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret-for-this-test")
    return _bytes(writer)


def encrypted_pdf_with_empty_password() -> bytes:
    """Opens without a password, but is still encrypted: its contents cannot be inspected."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("")
    return _bytes(writer)


def pdf_with_obfuscated_javascript_name() -> bytes:
    """A hand-written PDF whose action names use #-escapes (``/J#61vaScript``) to dodge a grep."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        b"<< /S /J#61vaScript /J#53 (app.alert(1)) >>",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    xref_at = len(body)
    body += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode()
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    body += trailer.encode()
    return body


def png(width: int = 40, height: int = 30) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


def jpeg(width: int = 40, height: int = 30) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="JPEG")
    return out.getvalue()


def huge_png(width: int, height: int) -> bytes:
    """A one-bit PNG with many pixels: a few kilobytes on disk, a decompression bomb in memory."""
    out = io.BytesIO()
    Image.new("1", (width, height)).save(out, format="PNG", optimize=True)
    return out.getvalue()


def gif() -> bytes:
    out = io.BytesIO()
    Image.new("P", (10, 10)).save(out, format="GIF")
    return out.getvalue()
