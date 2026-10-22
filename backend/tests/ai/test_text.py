"""Reading the text of a bill: text layer, OCR fallback, fonts, and the page-one image."""

import re
from decimal import Decimal

import pytest
from PIL import Image
from pytesseract import TesseractNotFoundError

from app.ai import text
from tests.ai.helpers import (
    DATA_DIR,
    document,
    double_drawn_pdf,
    ground_truth,
    jpeg_bytes,
    labels,
    needs_dataset,
    needs_tesseract,
    overlay_pdf,
    overstruck_pdf,
    png_bytes,
    scan_pdf,
    text_image,
    text_pdf,
)

LINES = [
    "Kilele Mission Hospital",
    "IN-PATIENT INVOICE",
    "Invoice No.: KMH/26/059153",
    "Maternity consultation 3,300.00",
    "Total (KES) 66,700.00",
]


@pytest.fixture
def no_ocr(monkeypatch: pytest.MonkeyPatch) -> list:
    """Fail the test if Tesseract is called, and record that it was."""
    calls: list = []

    def refuse(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("OCR was used on a page that has a text layer")

    monkeypatch.setattr(text, "image_to_data", refuse)
    return calls


# --------------------------------------------------------------------------- text layer


def test_a_pdf_with_a_text_layer_is_read_directly(no_ocr: list) -> None:
    result = text.extract(text_pdf(LINES), "application/pdf")

    assert result.method == "text_layer"
    assert result.page_count == 1
    assert result.ocr_pages == []
    assert result.confidence is None
    assert "Invoice No.: KMH/26/059153" in result.text
    assert "Total (KES) 66,700.00" in result.text
    assert result.readable
    assert no_ocr == []


def test_words_carry_their_font_and_size(no_ocr: list) -> None:
    result = text.extract(text_pdf(LINES, bold_last_line=True), "application/pdf")

    by_text = {w.text: w for w in result.words}
    assert by_text["Kilele"].fontname == "Helvetica"
    assert by_text["66,700.00"].fontname == "Helvetica-Bold"
    assert by_text["Kilele"].size == 12.0
    assert by_text["Kilele"].page == 1
    assert by_text["Kilele"].top < by_text["66,700.00"].top  # reading order, top to bottom


def test_page_one_is_returned_as_an_image_for_hashing(no_ocr: list) -> None:
    result = text.extract(text_pdf(LINES), "application/pdf")

    assert isinstance(result.first_page, Image.Image)
    width, height = result.first_page.size
    assert abs(width - 827) <= 1  # A4 at the 100 dpi preview resolution
    assert abs(height - 1169) <= 1


def test_pages_are_joined_and_kept_apart(no_ocr: list) -> None:
    second = ["Second page of the bill", "Balance due (KES) 12,000.00", "x" * 40]

    result = text.extract(text_pdf(LINES, second), "application/pdf")

    assert result.page_count == 2
    assert len(result.page_texts) == 2
    assert "Second page of the bill" in result.page_texts[1]
    assert "Second page" not in result.page_texts[0]
    assert result.text == "\n\n".join(result.page_texts)


def test_the_text_layer_threshold_is_fifty_characters_a_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr_calls: list = []
    monkeypatch.setattr(
        text, "_ocr", lambda image: ocr_calls.append(image) or ("from the scan", 80.0)
    )
    fifty = "x" * 50
    forty_nine = "y" * 49

    enough = text.extract(text_pdf([fifty]), "application/pdf")
    too_little = text.extract(text_pdf([forty_nine]), "application/pdf")

    assert enough.method == "text_layer"
    assert ocr_calls
    assert too_little.method == "ocr"
    assert too_little.text == "from the scan"
    assert len(ocr_calls) == 1


# --------------------------------------------------------------------------- hidden text


def test_a_total_covered_by_a_white_box_and_retyped_is_read_as_the_reader_sees_it(
    no_ocr: list,
) -> None:
    result = text.extract(overlay_pdf(), "application/pdf")

    assert "Total 74,000" in result.text
    assert "46,250" not in result.text
    assert result.hidden_words == ["Total 46,250"]


def test_words_and_fonts_leave_out_what_is_hidden(no_ocr: list) -> None:
    result = text.extract(overlay_pdf(), "application/pdf")

    assert "46,250" not in {w.text for w in result.words}
    assert "74,000" in {w.text for w in result.words}


def test_a_box_drawn_before_the_text_hides_nothing(no_ocr: list) -> None:
    result = text.extract(overlay_pdf(box_before_text=True), "application/pdf")

    assert result.hidden_words == []
    assert "Total 74,000" in result.text


def test_a_shaded_band_is_not_a_cover(no_ocr: list) -> None:
    result = text.extract(overlay_pdf(box_color=(0.8, 0.9, 1.0), retype_at=690), "application/pdf")

    assert result.hidden_words == []
    assert "Total 46,250" in result.text


def test_text_drawn_twice_in_one_place_is_not_hiding_anything(no_ocr: list) -> None:
    result = text.extract(double_drawn_pdf(), "application/pdf")

    assert result.hidden_words == []
    assert "fake bold heading line" in result.text


def test_a_value_typed_over_another_without_a_box_hides_the_earlier_one(no_ocr: list) -> None:
    result = text.extract(overstruck_pdf(), "application/pdf")

    assert result.hidden_words
    assert "74,000" in result.text


@pytest.mark.parametrize(
    ("color", "white"),
    [
        (1, True),
        (0.96, True),
        (0.5, False),
        ((1.0, 1.0, 1.0), True),
        ((1.0, 0.95, 1.0), True),
        ((1.0, 0.5, 1.0), False),
        ((0, 0, 0, 0), True),
        ((0, 0, 0, 1), False),
        ((0.2, 0, 0, 0), False),
        (None, False),
        ((), False),
        ("white", False),
    ],
)
def test_only_white_counts_as_a_cover(color: object, white: bool) -> None:
    assert text._is_white(color) is white  # noqa: SLF001


# --------------------------------------------------------------------------- scans


def test_a_page_without_a_text_layer_is_read_by_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Image.Image] = []

    def fake(image: Image.Image) -> tuple[str, float]:
        seen.append(image)
        return "Invoice KMH/26/059153\nTotal 66,700.00", 91.5

    monkeypatch.setattr(text, "_ocr", fake)

    result = text.extract(scan_pdf(LINES), "application/pdf")

    assert result.method == "ocr"
    assert result.ocr_pages == [1]
    assert result.confidence == 91.5
    assert result.words == []  # an OCR'd page has no fonts to report
    assert "Total 66,700.00" in result.text
    assert seen[0].size[0] > 1000  # rendered at 200 dpi, not the preview resolution


def test_a_mixed_pdf_reads_each_page_the_way_it_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text, "_ocr", lambda image: ("scanned page text", 70.0))
    from tests.ai.helpers import canvas, io

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(595, 842))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(50, 780, "A digital first page with plenty of text in its text layer, okay")
    pdf.showPage()
    pdf.showPage()  # an empty second page: nothing to read
    pdf.save()

    result = text.extract(buffer.getvalue(), "application/pdf")

    assert result.method == "mixed"
    assert result.ocr_pages == [2]
    assert "digital first page" in result.page_texts[0]
    assert result.page_texts[1] == "scanned page text"


def test_at_most_five_scanned_pages_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(text, "_ocr", lambda image: calls.append(1) or ("page text", 80.0))
    pages = [["page"] for _ in range(8)]

    result = text.extract(scan_pdf(*pages), "application/pdf")

    assert len(calls) == text.MAX_OCR_PAGES
    assert result.page_count == 8
    assert result.ocr_pages == [1, 2, 3, 4, 5]


@needs_tesseract
def test_ocr_really_reads_a_scanned_pdf() -> None:
    result = text.extract(scan_pdf(LINES), "application/pdf")

    assert result.method == "ocr"
    assert result.ocr_available
    assert "KMH/26/059153" in result.text
    assert "66,700.00" in result.text
    assert result.confidence is not None
    assert result.confidence > 70


@needs_tesseract
@pytest.mark.parametrize(("encode", "mime"), [(png_bytes, "image/png"), (jpeg_bytes, "image/jpeg")])
def test_ocr_really_reads_png_and_jpeg(encode, mime: str) -> None:
    result = text.extract(encode(text_image(LINES)), mime)

    assert result.method == "ocr"
    assert result.ocr_pages == [1]
    assert result.page_count == 1
    assert "KMH/26/059153" in result.text
    assert isinstance(result.first_page, Image.Image)


@needs_tesseract
def test_a_photo_taken_sideways_is_turned_upright_and_read() -> None:
    sideways = text_image(LINES).rotate(90, expand=True)

    result = text.extract(png_bytes(sideways), "image/png")

    assert "KMH/26/059153" in result.text


# --------------------------------------------------------------------------- OCR logic


def test_a_poor_read_is_retried_with_the_page_turned_upright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = [("garbled", 20.0), ("clean text", 88.0)]
    monkeypatch.setattr(text, "_read", lambda image: reads.pop(0))
    monkeypatch.setattr(text, "_turned_upright", lambda image: image.rotate(90))

    assert text._ocr(Image.new("RGB", (50, 50))) == ("clean text", 88.0)  # noqa: SLF001


def test_a_good_first_read_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text, "_read", lambda image: ("fine", 90.0))
    monkeypatch.setattr(
        text, "_turned_upright", lambda image: pytest.fail("a good read needs no second try")
    )

    assert text._ocr(Image.new("RGB", (50, 50))) == ("fine", 90.0)  # noqa: SLF001


def test_the_turned_read_is_kept_only_if_it_is_better(monkeypatch: pytest.MonkeyPatch) -> None:
    reads = [("meh", 40.0), ("worse", 10.0)]
    monkeypatch.setattr(text, "_read", lambda image: reads.pop(0))
    monkeypatch.setattr(text, "_turned_upright", lambda image: image)

    assert text._ocr(Image.new("RGB", (50, 50))) == ("meh", 40.0)  # noqa: SLF001


def test_no_orientation_found_means_no_second_read(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(text, "_read", lambda image: calls.append(1) or ("meh", 30.0))
    monkeypatch.setattr(text, "_turned_upright", lambda image: None)

    text._ocr(Image.new("RGB", (50, 50)))  # noqa: SLF001

    assert len(calls) == 1


def test_a_failed_orientation_check_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    from pytesseract import TesseractError

    def fail(*args: object, **kwargs: object) -> None:
        raise TesseractError(1, "osd data missing")

    monkeypatch.setattr(text, "image_to_osd", fail)

    assert text._turned_upright(Image.new("L", (50, 50))) is None  # noqa: SLF001


def test_an_upright_page_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text, "image_to_osd", lambda *a, **k: {"rotate": 0})

    assert text._turned_upright(Image.new("L", (50, 50))) is None  # noqa: SLF001


def test_ocr_that_blows_up_gives_an_empty_read_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(text, "image_to_data", explode)

    result = text.extract(png_bytes(text_image(LINES)), "image/png")

    assert result.text == ""
    assert result.method == "none"
    assert result.ocr_available  # the engine is there; this read failed


def test_without_tesseract_the_result_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise TesseractNotFoundError()

    monkeypatch.setattr(text, "image_to_data", missing)

    photo = text.extract(png_bytes(text_image(LINES)), "image/png")
    scan = text.extract(scan_pdf(LINES), "application/pdf")

    for result in (photo, scan):
        assert result.ocr_available is False
        assert result.text == ""
        assert result.method == "none"
        assert result.first_page is not None  # the page image is still there for hashing


def test_a_pdf_with_a_text_layer_needs_no_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise TesseractNotFoundError()

    monkeypatch.setattr(text, "image_to_data", missing)

    result = text.extract(text_pdf(LINES), "application/pdf")

    assert result.method == "text_layer"
    assert result.ocr_available


def test_word_confidences_and_lines_are_put_back_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        text,
        "image_to_data",
        lambda *a, **k: {
            "text": ["Total", "", "(KES)", "66,700.00", "Deposit", "13,000.00"],
            "block_num": [1, 1, 1, 1, 1, 1],
            "par_num": [1, 1, 1, 1, 1, 1],
            "line_num": [1, 1, 1, 1, 2, 2],
            "conf": [90, -1, 80, 70, 60, 50],
        },
    )

    assert text._read(Image.new("L", (10, 10))) == (
        "Total (KES) 66,700.00\nDeposit 13,000.00",
        70.0,
    )  # noqa: SLF001


# --------------------------------------------------------------------------- bad input


def test_a_damaged_pdf_is_reported_not_raised() -> None:
    result = text.extract(b"%PDF-1.4 not really a pdf", "application/pdf")

    assert result.error == "unreadable_pdf"
    assert result.method == "none"
    assert result.text == ""


def test_a_damaged_image_is_reported_not_raised() -> None:
    result = text.extract(b"\x89PNG\r\n\x1a\n broken", "image/png")

    assert result.error == "unreadable_image"
    assert result.method == "none"


def test_an_unsupported_type_is_reported() -> None:
    assert text.extract(b"GIF89a", "image/gif").error == "unsupported_type"


def test_a_huge_page_is_rendered_at_a_lower_resolution() -> None:
    assert text._dpi_for((595, 842), 200) == 200  # noqa: SLF001
    assert text._dpi_for((5000, 7000), 200) == 50  # noqa: SLF001  # never below a readable 50
    assert text._dpi_for(None, 200) == 200  # noqa: SLF001
    assert text._dpi_for((3000, 4000), 200) < 200  # noqa: SLF001


def test_the_ocr_and_preview_sizes_stay_within_the_limit() -> None:
    longest_a4_inches = 842 / 72
    assert longest_a4_inches * text.OCR_DPI < text.MAX_RASTER_SIDE


# --------------------------------------------------------------------------- the synthetic dataset


OVERLAID = {"overlay_total", "overlay_line", "date_altered"}


def _printed_total(result: text.ExtractedText) -> Decimal | None:
    """The amount on the visible 'Total' line, however it is formatted."""
    for line in result.text.splitlines():
        if line.lower().startswith(("total", "estimated total")):
            match = re.search(r"([\d,]+(?:\.\d+)?)\s*$", line)
            if match:
                return Decimal(match.group(1).replace(",", ""))
    return None


@needs_dataset
@pytest.mark.parametrize(
    "row", [r for r in labels() if r["filename"].endswith(".pdf")], ids=lambda r: r["doc_id"]
)
def test_every_synthetic_pdf_gives_up_the_total_a_reader_sees_and_its_overlays(
    row: dict, no_ocr: list
) -> None:
    raw, mime = document(row["doc_id"])
    truth = ground_truth()[row["doc_id"]]

    result = text.extract(raw, mime)

    assert result.method == "text_layer"
    assert result.words, "fonts are needed for the tamper checks"
    assert result.first_page is not None
    assert row["invoice_number_shown"] in result.text
    assert truth["fields"]["facility"].split()[0] in result.text
    assert _printed_total(result) == Decimal(row["total_shown"])
    if row["tamper_type"] in OVERLAID:
        assert result.hidden_words, "an overlay should leave the old value behind"
    else:
        assert result.hidden_words == []


@needs_dataset
@needs_tesseract
@pytest.mark.parametrize("doc_id", ["INV-005", "INV-001", "INV-010"])
def test_ocr_reads_most_of_a_synthetic_photo(doc_id: str) -> None:
    raw, mime = document(doc_id)
    truth = ground_truth()[doc_id]["fields"]

    result = text.extract(raw, mime)

    assert result.method == "ocr"
    assert result.confidence is not None
    assert result.confidence > 60
    first_word_of_facility = truth["facility"].split()[0]
    assert first_word_of_facility.lower() in result.text.lower()
    assert DATA_DIR.exists()
