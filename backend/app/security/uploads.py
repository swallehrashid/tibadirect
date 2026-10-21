"""Upload validation: what may be stored, decided from the bytes and never from the name.

A bill is a PDF, a PNG or a JPEG of at most 10 MB. The type comes from the file's magic bytes and
must agree with the extension and the declared content type. PDFs may not be encrypted, may not
run anything (JavaScript, launch or open actions, embedded files) and have at most 10 pages;
images may not exceed 25 megapixels, which stops decompression bombs. The original file name is
never kept: storage keys are built from IDs.
"""

import hashlib
import io
import os
import warnings
from dataclasses import dataclass
from typing import Any

import filetype
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject

from app.errors import AppError
from app.models.case import MAX_UPLOAD_BYTES

MAX_PDF_PAGES = 10
MAX_IMAGE_PIXELS = 25_000_000
MAX_PDF_OBJECTS = 200_000

# Pillow warns at this size and refuses at twice this size; we refuse above it ourselves.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

ALLOWED_TYPES: dict[str, tuple[str, frozenset[str]]] = {
    "application/pdf": ("pdf", frozenset({".pdf"})),
    "image/png": ("png", frozenset({".png"})),
    "image/jpeg": ("jpg", frozenset({".jpg", ".jpeg"})),
}

# PDF names that mean "run something" or "hide something inside".
ACTIVE_PDF_KEYS = frozenset(
    {
        "/JavaScript",
        "/JS",
        "/OpenAction",
        "/AA",
        "/Launch",
        "/EmbeddedFile",
        "/EmbeddedFiles",
        "/RichMedia",
        "/XFA",
        "/SubmitForm",
        "/ImportData",
    }
)
ACTIVE_ACTION_TYPES = frozenset({"/JavaScript", "/Launch", "/SubmitForm", "/ImportData"})
_MAX_WALK_DEPTH = 40


class UploadRejected(AppError):
    """The file may not be stored. ``code`` says why, in words a family can act on."""

    status_code = 422
    code = "upload_rejected"
    message = "That file cannot be uploaded."


def _reject(code: str, message: str, status_code: int = 422) -> UploadRejected:
    return UploadRejected(message, code=code, status_code=status_code)


@dataclass(frozen=True)
class ValidatedUpload:
    """A file that passed every check. ``extension`` has no dot."""

    mime_type: str
    extension: str
    size_bytes: int
    sha256: str
    page_count: int | None
    width: int | None
    height: int | None


def validate_upload(
    data: bytes, *, filename: str | None, declared_type: str | None = None
) -> ValidatedUpload:
    """Check ``data`` and return what was found, or raise ``UploadRejected``."""
    size = len(data)
    if size == 0:
        raise _reject("file_empty", "The file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise _reject("file_too_large", "The file is larger than 10 MB. Try a smaller scan.", 413)

    guessed = filetype.guess(data)
    mime = guessed.mime if guessed is not None else None
    if mime not in ALLOWED_TYPES:
        raise _reject("file_type_not_allowed", "Upload a PDF, PNG or JPEG file.", 415)

    extension, allowed_extensions = ALLOWED_TYPES[mime]
    given = os.path.splitext(filename or "")[1].lower()
    if given not in allowed_extensions:
        raise _reject("file_type_mismatch", "The file name does not match what the file contains.")
    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared in ALLOWED_TYPES and declared != mime:
        raise _reject("file_type_mismatch", "The file type does not match what the file contains.")

    pages = width = height = None
    if mime == "application/pdf":
        pages = _check_pdf(data)
    else:
        width, height = _check_image(data, expected="PNG" if mime == "image/png" else "JPEG")

    return ValidatedUpload(
        mime_type=mime,
        extension=extension,
        size_bytes=size,
        sha256=hashlib.sha256(data).hexdigest(),
        page_count=pages,
        width=width,
        height=height,
    )


def _check_image(data: bytes, *, expected: str) -> tuple[int, int]:
    try:
        # Pillow warns about big pictures; the size check below is the rule, not the warning.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected:
                    raise _reject(
                        "file_type_mismatch", "The file type does not match what the file contains."
                    )
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise _reject(
                        "image_too_large",
                        "The picture is too large. Take it again at a lower resolution.",
                    )
                image.verify()  # structure only; the pixels are never decoded here
    except UploadRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise _reject("image_unreadable", "The picture could not be read.") from None
    return width, height


def _check_pdf(data: bytes) -> int:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise _reject("pdf_encrypted", "Password-protected PDFs are not accepted.")
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            raise _reject("pdf_too_many_pages", f"The PDF has more than {MAX_PDF_PAGES} pages.")
        if _has_active_content(reader):
            raise _reject(
                "pdf_active_content",
                "The PDF contains scripts or attachments. Print it to a new PDF and upload that.",
            )
    except UploadRejected:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, RecursionError, OSError):
        raise _reject("pdf_unreadable", "The PDF could not be read.") from None
    return pages


def _object_numbers(reader: PdfReader) -> set[int]:
    numbers: set[int] = set()
    for table in reader.xref.values():
        numbers.update(table)
    numbers.update(getattr(reader, "xref_objStm", {}))
    return numbers


def _has_active_content(reader: PdfReader) -> bool:
    numbers = _object_numbers(reader)
    if len(numbers) > MAX_PDF_OBJECTS:
        raise _reject("pdf_unreadable", "The PDF could not be read.")
    roots: list[Any] = [reader.trailer]
    roots.extend(reader.get_object(number) for number in sorted(numbers))
    return any(_walk(root, 0) for root in roots)


def _walk(node: Any, depth: int) -> bool:
    """True if a dictionary below ``node`` names an active feature. Links are not followed:
    every indirect object is visited on its own."""
    if depth > _MAX_WALK_DEPTH:
        return True  # nesting this deep is not a bill
    if isinstance(node, DictionaryObject):
        if ACTIVE_PDF_KEYS.intersection(node.keys()):
            return True
        if str(node.get("/S", "")) in ACTIVE_ACTION_TYPES:
            return True
        return any(_walk(value, depth + 1) for value in node.values())
    if isinstance(node, ArrayObject):
        return any(_walk(item, depth + 1) for item in node)
    return False
