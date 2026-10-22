"""Upload validation: the type comes from the bytes, and hostile files are refused."""

import hashlib

import pytest

from app.models.case import MAX_UPLOAD_BYTES
from app.security import uploads
from app.security.uploads import UploadRejected, validate_upload
from tests import pdf_fixtures as fx


def _reject(data: bytes, filename: str | None, declared: str | None = None) -> UploadRejected:
    with pytest.raises(UploadRejected) as raised:
        validate_upload(data, filename=filename, declared_type=declared)
    return raised.value


# --- Accepted ----------------------------------------------------------------------------------


def test_a_plain_pdf_is_accepted_and_described() -> None:
    data = fx.clean_pdf(pages=3)
    result = validate_upload(data, filename="Bill.PDF", declared_type="application/pdf")
    assert result.mime_type == "application/pdf"
    assert result.extension == "pdf"
    assert result.page_count == 3
    assert result.size_bytes == len(data)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.width is None


@pytest.mark.parametrize("filename", ["scan.png", "SCAN.PNG"])
def test_a_png_is_accepted_with_its_size(filename: str) -> None:
    result = validate_upload(fx.png(40, 30), filename=filename, declared_type="image/png")
    assert (result.mime_type, result.extension) == ("image/png", "png")
    assert (result.width, result.height, result.page_count) == (40, 30, None)


@pytest.mark.parametrize("filename", ["photo.jpg", "photo.jpeg", "PHOTO.JPG"])
def test_a_jpeg_is_accepted_under_either_extension(filename: str) -> None:
    result = validate_upload(fx.jpeg(), filename=filename)
    assert (result.mime_type, result.extension) == ("image/jpeg", "jpg")


def test_exactly_ten_pages_and_exactly_the_size_limit_are_fine() -> None:
    assert validate_upload(fx.clean_pdf(pages=10), filename="a.pdf").page_count == 10
    assert uploads.MAX_UPLOAD_BYTES == MAX_UPLOAD_BYTES == 10_485_760


def test_a_missing_declared_type_or_octet_stream_is_not_held_against_the_file() -> None:
    assert validate_upload(fx.png(), filename="a.png", declared_type=None)
    assert validate_upload(fx.png(), filename="a.png", declared_type="application/octet-stream")
    assert validate_upload(fx.png(), filename="a.png", declared_type="image/png; charset=binary")


# --- Size and type -----------------------------------------------------------------------------


def test_an_empty_file_is_refused() -> None:
    error = _reject(b"", "a.pdf")
    assert (error.code, error.status_code) == ("file_empty", 422)


def test_a_file_over_ten_megabytes_is_refused_before_anything_else_looks_at_it() -> None:
    error = _reject(b"%PDF-" + b"0" * MAX_UPLOAD_BYTES, "a.pdf")
    assert (error.code, error.status_code) == ("file_too_large", 413)


def test_a_file_of_exactly_the_limit_passes_the_size_check() -> None:
    error = _reject(b"x" * MAX_UPLOAD_BYTES, "a.pdf")
    assert error.code == "file_type_not_allowed"  # it got past the size check


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"MZ\x90\x00" + b"\x00" * 200, "invoice.pdf"),  # a Windows program
        (b"#!/bin/sh\nrm -rf /\n", "invoice.pdf"),
        (b"<html><script>alert(1)</script></html>", "invoice.png"),
        (b"just some text", "invoice.pdf"),
        (fx.gif(), "invoice.png"),
        (b"PK\x03\x04" + b"\x00" * 100, "invoice.pdf"),  # a zip
    ],
)
def test_the_type_is_decided_by_the_magic_bytes_not_the_name(data: bytes, name: str) -> None:
    error = _reject(data, name)
    assert (error.code, error.status_code) == ("file_type_not_allowed", 415)


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (fx.png(), "bill.pdf"),
        (fx.clean_pdf(), "bill.png"),
        (fx.jpeg(), "bill.png"),
        (fx.png(), "bill.jpg"),
        (fx.png(), "bill"),
        (fx.png(), ""),
        (fx.png(), None),
        (fx.png(), "bill.pdf.png.exe"),
        (fx.png(), "bill.png.exe"),
    ],
)
def test_the_extension_must_agree_with_the_contents(data: bytes, name: str | None) -> None:
    assert _reject(data, name).code == "file_type_mismatch"


def test_the_declared_content_type_must_agree_when_it_names_an_allowed_type() -> None:
    assert _reject(fx.png(), "a.png", declared="application/pdf").code == "file_type_mismatch"
    assert _reject(fx.clean_pdf(), "a.pdf", declared="image/jpeg").code == "file_type_mismatch"


def test_a_pdf_with_junk_before_the_header_is_not_a_pdf_here() -> None:
    assert _reject(b"\n\n" + fx.clean_pdf(), "a.pdf").code == "file_type_not_allowed"


# --- PDFs --------------------------------------------------------------------------------------


def test_eleven_pages_are_too_many() -> None:
    assert _reject(fx.clean_pdf(pages=11), "a.pdf").code == "pdf_too_many_pages"


def test_an_encrypted_pdf_is_refused() -> None:
    assert _reject(fx.encrypted_pdf(), "a.pdf").code == "pdf_encrypted"


def test_a_pdf_encrypted_with_an_empty_password_is_still_refused() -> None:
    assert _reject(fx.encrypted_pdf_with_empty_password(), "a.pdf").code == "pdf_encrypted"


@pytest.mark.parametrize(
    "build",
    [
        fx.pdf_with_javascript,
        fx.pdf_with_launch_action,
        fx.pdf_with_page_action,
        fx.pdf_with_attachment,
        fx.pdf_with_action_inside_an_array,
        fx.pdf_with_obfuscated_javascript_name,
    ],
)
def test_a_pdf_that_can_run_or_hide_something_is_refused(build) -> None:  # type: ignore[no-untyped-def]
    error = _reject(build(), "a.pdf")
    assert (error.code, error.status_code) == ("pdf_active_content", 422)


@pytest.mark.parametrize(
    "key",
    [
        "/OpenAction",
        "/AA",
        "/JavaScript",
        "/JS",
        "/Launch",
        "/EmbeddedFile",
        "/EmbeddedFiles",
        "/RichMedia",
        "/XFA",
    ],
)
def test_each_listed_pdf_feature_is_refused_wherever_it_appears(key: str) -> None:
    from pypdf.generic import NumberObject

    assert _reject(fx.pdf_with_catalog_entry(key, NumberObject(1)), "a.pdf").code == (
        "pdf_active_content"
    )


def test_an_ordinary_catalog_entry_is_fine() -> None:
    from pypdf.generic import TextStringObject

    data = fx.pdf_with_catalog_entry("/Lang", TextStringObject("en"))
    assert validate_upload(data, filename="a.pdf").page_count == 1


def test_a_truncated_pdf_is_unreadable_not_a_crash() -> None:
    error = _reject(fx.clean_pdf()[:60], "a.pdf")
    assert error.code in {"pdf_unreadable", "pdf_active_content"}
    assert error.status_code == 422


def test_garbage_after_a_pdf_header_is_unreadable() -> None:
    assert _reject(b"%PDF-1.7\n" + b"\x00\xff" * 300, "a.pdf").code == "pdf_unreadable"


# --- Images ------------------------------------------------------------------------------------


def test_a_picture_over_twenty_five_megapixels_is_refused_without_decoding_it() -> None:
    bomb = fx.huge_png(6000, 5000)  # 30 megapixels in a few kilobytes
    assert len(bomb) < 100_000
    assert _reject(bomb, "a.png").code == "image_too_large"


def test_a_picture_at_the_limit_is_accepted() -> None:
    result = validate_upload(fx.huge_png(5000, 5000), filename="a.png")
    assert (result.width, result.height) == (5000, 5000)


def test_a_truncated_picture_is_unreadable() -> None:
    assert _reject(fx.png(400, 400)[:90], "a.png").code == "image_unreadable"


def test_a_jpeg_that_is_really_a_png_with_a_jpg_name_is_a_mismatch() -> None:
    assert _reject(fx.png(), "a.jpg").code == "file_type_mismatch"


def test_pillows_own_bomb_limit_matches_ours() -> None:
    from PIL import Image

    assert Image.MAX_IMAGE_PIXELS == uploads.MAX_IMAGE_PIXELS == 25_000_000
