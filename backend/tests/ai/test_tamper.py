"""Tamper signals: file structure, metadata, arithmetic, hidden text, fonts and photographs."""

import io
from decimal import Decimal

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.ai import extract, tamper, text
from app.ai.extract import ExtractedBill, LineItem
from app.ai.text import ExtractedText, Word
from tests.ai.helpers import (
    DATA_DIR,
    document,
    jpeg_bytes,
    labels,
    needs_dataset,
    overlay_pdf,
    text_image,
    text_pdf,
)

LINES = ["Kilele Mission Hospital", "Invoice No.: KMH/26/059153", "Total (KES) 100.00"]


def pdf_with(metadata: dict[str, str]) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(text_pdf(LINES))))
    writer.add_metadata(metadata)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def codes(signals) -> list[str]:  # type: ignore[no-untyped-def]
    return [s.code for s in signals]


def item(amount: str, qty: int | None = None, unit: str | None = None) -> LineItem:
    return LineItem(
        label="x",
        amount_kes=Decimal(amount),
        quantity=qty,
        unit_kes=Decimal(unit) if unit else None,
    )


def bill(total: str | None, *items: LineItem) -> ExtractedBill:
    return ExtractedBill(total_kes=Decimal(total) if total else None, line_items=list(items))


# --------------------------------------------------------------------------- PDF file checks


def test_a_plain_pdf_raises_nothing(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert tamper.pdf_signals(text_pdf(LINES)) == []


@pytest.mark.parametrize(
    "maker",
    [
        {"/Producer": "Adobe Acrobat Pro 2024"},
        {"/Creator": "Microsoft Word"},
        {"/Producer": "QuickPDF Editor 9.1", "/Creator": "MediBill HMS 4.2"},
        {"/Creator": "Online Invoice Maker 3.0"},
        {"/Producer": "photoshop"},
    ],
)
def test_a_pdf_made_or_changed_by_an_editor_is_flagged(app, maker: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert codes(tamper.pdf_signals(pdf_with(maker))) == ["pdf_editor_software"]


@pytest.mark.parametrize(
    "maker",
    [
        "MediBill HMS 4.2",
        "CarePoint Billing 2.8",
        "ReportLab PDF Library",
        "Microsoft Print to PDF",
    ],
)
def test_hospital_billing_systems_and_plain_printers_are_not_editors(app, maker: str) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert tamper.pdf_signals(pdf_with({"/Producer": maker, "/Creator": maker})) == []


def test_a_pdf_changed_more_than_a_day_after_it_was_made_is_flagged(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        signals = tamper.pdf_signals(
            pdf_with(
                {"/CreationDate": "D:20260801090000+03'00'", "/ModDate": "D:20260805120000+03'00'"}
            )
        )

    assert codes(signals) == ["pdf_modified_after_creation"]
    assert signals[0].detail == {"hours": 99}


@pytest.mark.parametrize(
    ("created", "modified"),
    [
        ("D:20260801090000", "D:20260802080000"),  # 23 hours
        ("D:20260801090000", "D:20260801090000"),
        ("D:20260805090000", "D:20260801090000"),  # modified before it was made
        ("garbage", "D:20260801090000"),
        ("D:20261399250000", "D:20260801090000"),  # not a real date
    ],
)
def test_a_short_or_unreadable_gap_is_not_flagged(app, created: str, modified: str) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert tamper.pdf_signals(pdf_with({"/CreationDate": created, "/ModDate": modified})) == []


def test_a_pdf_saved_again_on_top_of_the_original_is_flagged(app) -> None:  # type: ignore[no-untyped-def]
    update = (
        b"\n9 0 obj\n<< /Edited true >>\nendobj\nxref\n0 1\ntrailer\n<< >>\nstartxref\n0\n%%EOF\n"
    )
    edited = text_pdf(LINES) + update
    assert edited.count(b"%%EOF") == 2

    with app.app_context():
        signals = tamper.pdf_signals(edited)

    assert "pdf_incremental_updates" in codes(signals)
    assert next(s for s in signals if s.code == "pdf_incremental_updates").detail == {
        "revisions": 2
    }


def test_a_linearised_pdf_has_two_endings_legitimately(app) -> None:  # type: ignore[no-untyped-def]
    raw = (
        b"%PDF-1.4\n%\xe2\xe3\n1 0 obj\n<< /Linearized 1 >>\nendobj\n%%EOF\n"
        + b"x" * 50
        + b"\n%%EOF\n"
    )

    with app.app_context():
        assert tamper.pdf_signals(raw) == []


def test_unreadable_metadata_is_ignored(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert tamper.pdf_signals(b"%PDF-1.4 not really") == []


# --------------------------------------------------------------------------- arithmetic


def test_lines_that_add_up_to_the_total_raise_nothing() -> None:
    assert tamper.arithmetic_signals(bill("100", item("60"), item("40"))) == []


def test_a_total_within_one_percent_of_the_lines_is_rounding() -> None:
    assert tamper.arithmetic_signals(bill("10000", item("5000"), item("4950"))) == []


def test_a_total_more_than_one_percent_off_the_lines_is_flagged() -> None:
    signals = tamper.arithmetic_signals(bill("10000", item("5000"), item("4800")))

    assert codes(signals) == ["totals_mismatch"]
    assert signals[0].detail == {"lines": "9800", "total": "10000"}


def test_without_lines_or_a_total_there_is_nothing_to_add_up() -> None:
    assert tamper.arithmetic_signals(bill("100")) == []
    assert tamper.arithmetic_signals(bill(None, item("100"))) == []


def test_quantity_times_price_must_be_the_amount() -> None:
    assert tamper.arithmetic_signals(bill(None, item("8000", 2, "4000"))) == []
    assert tamper.arithmetic_signals(bill(None, item("8000.50", 2, "4000.00"))) == []  # a shilling

    flagged = tamper.arithmetic_signals(bill(None, item("9000", 2, "4000"), item("100", 1, "100")))

    assert codes(flagged) == ["line_math_mismatch"]
    assert flagged[0].detail == {"lines": 1}


def test_a_line_without_a_quantity_or_price_is_not_tested() -> None:
    assert tamper.arithmetic_signals(bill(None, item("9000"), item("9000", 2, None))) == []


# --------------------------------------------------------------------------- hidden text and fonts


def word(value: str, font: str, top: float, x0: float = 0.0, page: int = 1) -> Word:
    return Word(text=value, fontname=font, size=10.0, page=page, x0=x0, top=top)


def test_hidden_text_is_a_signal_of_its_own(app) -> None:  # type: ignore[no-untyped-def]
    raw = overlay_pdf()
    read = text.extract(raw, "application/pdf")

    with app.app_context():
        signals = tamper.check(raw, "application/pdf", read, ExtractedBill())

    assert "hidden_text_under_overlay" in codes(signals)
    assert next(s for s in signals if s.code == "hidden_text_under_overlay").detail == {
        "segments": 1
    }


def test_a_total_in_a_typeface_used_nowhere_else_is_flagged() -> None:
    words = [
        word("Specialist", "Helvetica", 10),
        word("8,000.00", "Helvetica", 10, 100),
        word("Total", "Helvetica-Bold", 30),
        word("104,000.00", "Courier-Bold", 30, 100),
    ]

    assert tamper.font_inconsistent(words)


def test_a_bold_total_in_a_typeface_the_bill_also_uses_is_fine() -> None:
    words = [
        word("INVOICE", "Helvetica-Bold", 1),
        word("Specialist", "Helvetica", 10),
        word("8,000.00", "Helvetica", 10, 100),
        word("Total", "Helvetica-Bold", 30),
        word("104,000.00", "Helvetica-Bold", 30, 100),
    ]

    assert not tamper.font_inconsistent(words)


def test_two_line_amounts_in_a_different_typeface_are_flagged_but_one_is_not() -> None:
    base = [word(f"{n},000.00", "Helvetica", 10 * n, 100) for n in range(1, 5)]
    one = [*base, word("9,999.00", "Courier", 70, 100)]
    two = [*one, word("8,888.00", "Courier", 80, 100)]

    assert not tamper.font_inconsistent(one)
    assert tamper.font_inconsistent(two)


@pytest.mark.parametrize("words", [[], [word("Hello", "Helvetica", 1)]])
def test_without_amounts_there_is_nothing_to_compare(words: list[Word]) -> None:
    assert not tamper.font_inconsistent(words)


# --------------------------------------------------------------------------- photographs


def photo() -> Image.Image:
    return text_image(
        ["Kilele Mission Hospital", "Invoice No.: KMH/26/059153", "Total (KES) 66,700.00"] * 4, 30
    )


def test_a_photograph_compressed_once_scores_low() -> None:
    saved = Image.open(io.BytesIO(jpeg_bytes(photo())))

    assert tamper.ela_score(saved) < tamper.ELA_HIGH


def test_a_textured_patch_pasted_into_a_page_scores_high() -> None:
    import numpy as np

    rng = np.random.default_rng(1)
    patch = Image.fromarray((rng.random((60, 300, 3)) * 255).astype("uint8"))
    faint = Image.blend(Image.new("RGB", (300, 60), "white"), patch, 100 / 255)
    tampered = photo()
    tampered.paste(faint, (40, 60))
    out = io.BytesIO()
    tampered.save(out, "JPEG", quality=85)

    assert tamper.ela_score(Image.open(io.BytesIO(out.getvalue()))) > tamper.ELA_HIGH


def test_an_almost_blank_page_cannot_be_judged() -> None:
    assert tamper.ela_score(Image.new("RGB", (400, 400), "white")) == 0.0


def test_a_photograph_edited_in_an_editor_says_so_in_its_exif(app) -> None:  # type: ignore[no-untyped-def]
    image = photo()
    exif = Image.Exif()
    exif[tamper.SOFTWARE_TAG] = "Adobe Photoshop 25.0"
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)

    with app.app_context():
        assert "exif_editor_software" in codes(tamper.image_signals(out.getvalue()))


def test_a_camera_is_not_an_editor(app) -> None:  # type: ignore[no-untyped-def]
    exif = Image.Exif()
    exif[tamper.SOFTWARE_TAG] = "Android Camera 14"
    out = io.BytesIO()
    photo().save(out, "JPEG", exif=exif)

    with app.app_context():
        assert tamper.image_signals(out.getvalue()) == []


def test_a_broken_image_raises_nothing(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        assert tamper.image_signals(b"not an image") == []


def test_check_picks_the_file_checks_for_the_file_type(app) -> None:  # type: ignore[no-untyped-def]
    pdf = pdf_with({"/Producer": "Adobe Acrobat Pro"})
    empty = ExtractedText()

    with app.app_context():
        assert "pdf_editor_software" in codes(
            tamper.check(pdf, "application/pdf", empty, ExtractedBill())
        )
        assert "pdf_editor_software" not in codes(
            tamper.check(jpeg_bytes(photo()), "image/jpeg", empty, ExtractedBill())
        )


# --------------------------------------------------------------------------- the synthetic bills

PDF_ROWS = [r for r in labels() if r["filename"].endswith(".pdf")] if DATA_DIR.exists() else []
RASTER_ROWS = (
    [r for r in labels() if not r["filename"].endswith(".pdf")] if DATA_DIR.exists() else []
)
# the dataset's name for a signal -> the codes this module raises for it
MAPPING = {
    "total_mismatch": {"totals_mismatch"},
    "line_math_mismatch": {"line_math_mismatch"},
    "hidden_text_under_overlay": {"hidden_text_under_overlay"},
    "font_inconsistent": {"font_inconsistency"},
    "metadata_edited": {"pdf_editor_software", "pdf_modified_after_creation"},
}


def _signals(row: dict, app) -> set[str]:  # type: ignore[no-untyped-def]
    raw, mime = document(row["doc_id"])
    read = text.extract(raw, mime)
    parsed = extract.extract(read.text).bill
    with app.app_context():
        return set(codes(tamper.check(raw, mime, read, parsed)))


@needs_dataset
@pytest.mark.parametrize("row", PDF_ROWS, ids=lambda r: r["doc_id"])
def test_every_digital_bill_raises_what_it_should_and_a_genuine_one_raises_nothing(
    row: dict, app
) -> None:  # type: ignore[no-untyped-def]
    raised = _signals(row, app)

    if row["label"] == "genuine":
        assert raised == set()
    for expected in row["expected_signals"].split("|"):
        if expected in MAPPING:
            assert raised & MAPPING[expected], f"{expected} was not found in {row['doc_id']}"
    if row["label"] == "duplicate":
        assert raised <= {"pdf_editor_software"}  # copies made with an invoice generator


@needs_dataset
def test_the_dataset_has_no_second_ending_in_any_pdf_though_its_labels_expect_one() -> None:
    """Documented limit: 'incremental_update' is expected for some bills but is not in the files."""
    assert all(document(r["doc_id"])[0].count(b"%%EOF") == 1 for r in PDF_ROWS)


@needs_dataset
@pytest.mark.parametrize("row", RASTER_ROWS, ids=lambda r: r["doc_id"])
def test_no_photograph_that_was_not_spliced_is_called_edited(row: dict, app) -> None:  # type: ignore[no-untyped-def]
    raw, mime = document(row["doc_id"])
    with app.app_context():
        raised = set(codes(tamper.image_signals(raw)))

    if row["tamper_type"] != "image_splice":
        assert raised == set()
