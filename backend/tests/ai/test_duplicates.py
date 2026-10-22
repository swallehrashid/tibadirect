"""Finding the same bill again: exact copies, look-alikes, fingerprints and the same patient."""

import io
from decimal import Decimal

import pytest
from PIL import Image, ImageOps

from app.ai import duplicates, extract, redact, text
from app.models.enums import CaseStatus
from tests.ai.helpers import DATA_DIR, document, labels, needs_dataset
from tests.factories import CaseDocumentFactory, CaseFactory, HospitalFactory

BILL_A = """Kilele Mission Hospital
Invoice No.: KMH/26/059153
1 Specialist consultation 2 4,000.00 8,000.00
2 General ward bed 8 8,400.00 67,200.00
3 Oxygen therapy 8 3,600.00 28,800.00
Total (KES) 104,000.00"""
BILL_B = """Kilele Mission Hospital
Invoice No.: KMH/26/061777
1 Specialist consultation 1 3,300.00 3,300.00
2 Private ward bed 3 5,100.00 15,300.00
Total (KES) 18,600.00"""


def stored(case, **fields):  # type: ignore[no-untyped-def]
    return CaseDocumentFactory(case=case, **fields)


def codes(signals) -> list[str]:  # type: ignore[no-untyped-def]
    return [s.code for s in signals]


def run(session, case, document, **kw):  # type: ignore[no-untyped-def]
    values = {
        "sha256": document.sha256,
        "phash": document.phash,
        "fingerprint": document.fingerprint,
        "redacted_text": document.redacted_text,
    }
    return duplicates.check(session, case, document, **{**values, **kw})


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("raw", "amounts"),
    [
        ("Total 66,700.00 and 3,300.00", {"66700", "3300"}),
        ("qty 2 at 4,000 each = 8000", {"4000", "8000"}),  # one digit and two digits are skipped
        ("Date 26/05/2026 no 059153", {"059153"}),
        ("Fee 12.50 and 125.50", {"12.50", "125.50"}),
        ("Date 2026-09-29 and 27 Jun 2026, total 5,000", {"5000"}),
        ("", set()),
    ],
)
def test_amounts_are_the_numbers_of_three_digits_or_more_written_one_way(
    raw: str, amounts: set[str]
) -> None:
    assert duplicates.amounts_in(raw) == frozenset(amounts)


def test_similarity_is_the_share_of_amounts_in_common() -> None:
    assert duplicates.similarity(frozenset({"1000", "2000"}), frozenset({"1000", "2000"})) == 1.0
    assert duplicates.similarity(
        frozenset({"1000", "2000"}), frozenset({"1000", "3000"})
    ) == pytest.approx(1 / 3)
    assert duplicates.similarity(frozenset(), frozenset()) == 0.0
    assert duplicates.similarity(frozenset({"1000"}), frozenset()) == 0.0


def test_the_page_hash_is_sixteen_hex_digits_and_stable() -> None:
    image = Image.new("RGB", (200, 300), "white")

    first = duplicates.image_hash(image)

    assert len(first) == 16
    assert int(first, 16) >= 0
    assert duplicates.image_hash(image.copy()) == first


def test_hamming_counts_differing_bits() -> None:
    assert duplicates.hamming("0000000000000000", "0000000000000000") == 0
    assert duplicates.hamming("0000000000000000", "ffffffffffffffff") == 64
    assert duplicates.hamming("0000000000000001", "0000000000000003") == 1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("KMH/26/059153", "KMH 26 059153"),
        ("kmh-26-059153", "KMH/26/059153"),
        ("KMH/26/O59153", "KMH/26/059153"),
        ("KMH/26/0S9153", "KMH/26/0S9153"),
    ],
)
def test_invoice_numbers_that_differ_only_in_punctuation_case_or_ocr_lookalikes_are_equal(
    first: str, second: str
) -> None:
    assert duplicates.normalize_invoice_number(first) == duplicates.normalize_invoice_number(second)


def test_the_fingerprint_ignores_layout_and_needs_all_three_parts(app) -> None:  # type: ignore[no-untyped-def]
    with app.app_context():
        one = duplicates.invoice_fingerprint(
            "Kilele Mission Hospital", "KMH/26/059153", Decimal("104000.00")
        )
        two = duplicates.invoice_fingerprint(
            "Kilele Mission Hospital", "KMH 26 059153", Decimal("104000")
        )
        other_total = duplicates.invoice_fingerprint(
            "Kilele Mission Hospital", "KMH/26/059153", Decimal("104001")
        )
        other_place = duplicates.invoice_fingerprint(
            "Mto Mwema Hospital", "KMH/26/059153", Decimal("104000")
        )

        assert one == two
        assert len({one, other_total, other_place}) == 3
        assert len(one or "") == 64
        assert duplicates.invoice_fingerprint(None, "A1", Decimal(5)) is None
        assert duplicates.invoice_fingerprint("X", "", Decimal(5)) is None
        assert duplicates.invoice_fingerprint("X", "///", Decimal(5)) is None
        assert duplicates.invoice_fingerprint("X", "A1", None) is None


# --------------------------------------------------------------------------- the checks


def test_a_new_bill_with_nothing_in_common_raises_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    other = CaseFactory()
    stored(other, sha256="a" * 64, phash="0" * 16, redacted_text=BILL_B, fingerprint="b" * 64)
    mine = CaseFactory()
    document = stored(
        mine, sha256="c" * 64, phash="ffffffff00000000", redacted_text=BILL_A, fingerprint="d" * 64
    )

    assert run(db_session, mine, document) == []


def test_the_same_file_on_another_case_is_an_exact_duplicate(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), sha256="a" * 64)
    mine = CaseFactory()

    assert codes(run(db_session, mine, stored(mine, sha256="a" * 64))) == ["duplicate_exact"]


def test_the_same_file_on_the_same_case_is_not(db_session) -> None:  # type: ignore[no-untyped-def]
    mine = CaseFactory()
    stored(mine, sha256="a" * 64)

    assert run(db_session, mine, stored(mine, sha256="a" * 64)) == []


def test_the_same_amounts_in_another_layout_are_a_near_duplicate(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), redacted_text=BILL_A, phash="0" * 16)
    mine = CaseFactory()
    reformatted = (
        "KILELE MISSION\nBill\nConsultation 2 x 4000 = 8000\nWard 8 x 8400 = 67200\n"
        "Oxygen 8 x 3600 = 28800\nGrand total 104000"
    )

    signals = run(
        db_session, mine, stored(mine, redacted_text=reformatted, phash="ffffffffffffffff")
    )

    assert codes(signals) == ["duplicate_near"]
    assert signals[0].detail["similarity"] >= 0.6


def test_the_same_amounts_under_another_invoice_number_are_another_bill(db_session) -> None:  # type: ignore[no-untyped-def]
    """The same delivery package billed to two families: identical lines, different invoices."""
    stored(CaseFactory(), redacted_text=BILL_A, phash="0123456789abcdef")
    mine = CaseFactory()
    other_invoice = BILL_A.replace("KMH/26/059153", "KMH/26/061200")

    signals = run(
        db_session,
        mine,
        stored(mine, redacted_text=other_invoice, phash="0123456789abcdef"),
        invoice_number="KMH/26/061200",
    )

    assert signals == []


def test_one_slipped_character_in_the_invoice_number_is_still_the_same_bill(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), redacted_text=BILL_A, phash="0123456789abcdef")
    mine = CaseFactory()
    photo = BILL_A.replace("KMH/26/059153", "KMH/26/05915S")

    signals = run(
        db_session,
        mine,
        stored(mine, redacted_text=photo, phash="0123456789abcdef"),
        invoice_number="KMH/26/05915S",
    )

    assert codes(signals) == ["duplicate_near"]


def test_a_page_that_looks_alike_but_shows_other_amounts_is_not_a_duplicate(db_session) -> None:  # type: ignore[no-untyped-def]
    """Two bills of one hospital look alike on the page; only the amounts tell them apart."""
    stored(CaseFactory(), redacted_text=BILL_A, phash="0123456789abcdef")
    mine = CaseFactory()

    signals = run(db_session, mine, stored(mine, redacted_text=BILL_B, phash="0123456789abcdef"))

    assert signals == []


def test_a_similar_page_with_some_matching_amounts_is_a_near_duplicate(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), redacted_text=BILL_A, phash="0123456789abcdef")
    mine = CaseFactory()
    half = (
        "Total 104,000.00\n1 Bed 8 8,400.00 67,200.00\n"
        "something 1 999.00 999.00\nother 1 777.00 777.00"
    )

    signals = run(db_session, mine, stored(mine, redacted_text=half, phash="0123456789abcdee"))

    assert codes(signals) == ["duplicate_near"]
    assert signals[0].detail["distance"] == 1


def test_matching_amounts_below_the_bar_with_a_different_page_raise_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), redacted_text=BILL_A, phash="0000000000000000")
    mine = CaseFactory()
    one_shared = "Total 104,000.00\na 1 111.00 111.00\nb 1 222.00 222.00\nc 1 333.00 333.00"

    assert (
        run(db_session, mine, stored(mine, redacted_text=one_shared, phash="ffffffffffffffff"))
        == []
    )


def test_no_readable_text_means_only_the_file_and_the_hash_can_speak(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), redacted_text=BILL_A, phash="0" * 16)
    mine = CaseFactory()

    assert run(db_session, mine, stored(mine, redacted_text=None, phash="0" * 16)) == []


def test_the_same_fingerprint_on_another_case_is_flagged(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), fingerprint="f" * 64)
    mine = CaseFactory()

    assert codes(run(db_session, mine, stored(mine, fingerprint="f" * 64))) == [
        "duplicate_fingerprint"
    ]


def test_a_missing_fingerprint_matches_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(CaseFactory(), fingerprint=None)
    mine = CaseFactory()

    assert run(db_session, mine, stored(mine, fingerprint=None)) == []


def test_every_signal_that_applies_is_raised_together(db_session) -> None:  # type: ignore[no-untyped-def]
    stored(
        CaseFactory(), sha256="a" * 64, phash="0" * 16, redacted_text=BILL_A, fingerprint="f" * 64
    )
    mine = CaseFactory()

    signals = run(
        db_session,
        mine,
        stored(mine, sha256="a" * 64, phash="0" * 16, redacted_text=BILL_A, fingerprint="f" * 64),
    )

    assert codes(signals) == ["duplicate_exact", "duplicate_near", "duplicate_fingerprint"]
    assert all(s.forces_manual_review for s in signals)


# --------------------------------------------------------------------------- the same patient


def test_the_same_patient_at_the_same_hospital_in_an_open_case_is_flagged(db_session) -> None:  # type: ignore[no-untyped-def]
    hospital = HospitalFactory()
    first = CaseFactory(hospital=hospital, status=CaseStatus.LIVE)
    first.set_patient_ref("IP/26/61332")
    mine = CaseFactory(hospital=hospital, status=CaseStatus.PENDING_TRIAGE)
    mine.set_patient_ref("ip/26/61332")
    db_session.flush()

    assert codes(run(db_session, mine, stored(mine))) == ["patient_active_elsewhere"]


@pytest.mark.parametrize("status", [CaseStatus.CLOSED, CaseStatus.CANCELLED])
def test_an_ended_case_does_not_count(db_session, status: CaseStatus) -> None:  # type: ignore[no-untyped-def]
    hospital = HospitalFactory()
    old = CaseFactory(
        hospital=hospital,
        status=status,
        close_reason="fully_settled" if status == CaseStatus.CLOSED else None,
    )
    old.set_patient_ref("IP/26/61332")
    mine = CaseFactory(hospital=hospital)
    mine.set_patient_ref("IP/26/61332")
    db_session.flush()

    assert run(db_session, mine, stored(mine)) == []


def test_the_same_reference_at_another_hospital_is_a_different_patient(db_session) -> None:  # type: ignore[no-untyped-def]
    first = CaseFactory(status=CaseStatus.LIVE)
    first.set_patient_ref("IP/26/61332")
    mine = CaseFactory()
    mine.set_patient_ref("IP/26/61332")
    db_session.flush()

    assert run(db_session, mine, stored(mine)) == []


def test_a_case_is_not_a_duplicate_of_itself(db_session) -> None:  # type: ignore[no-untyped-def]
    mine = CaseFactory()
    mine.set_patient_ref("IP/26/61332")
    db_session.flush()

    assert run(db_session, mine, stored(mine)) == []


# --------------------------------------------------------------------------- the synthetic bills

ALL_ROWS = (
    sorted(labels(), key=lambda r: (r["submitted_at"], r["doc_id"])) if DATA_DIR.exists() else []
)


def _page_image(raw: bytes, mime: str) -> Image.Image | None:
    if mime == "application/pdf":
        return text._render_page(raw, 1, 100, None)  # noqa: SLF001
    return ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")


@needs_dataset
def test_in_submission_order_every_digital_duplicate_is_caught_and_no_other_bill_is(
    db_session, app
) -> None:  # type: ignore[no-untyped-def]
    """The dataset's instructions: process in submitted_at order, index each after checking."""
    import hashlib

    missed, wrongly_flagged = [], []
    by_id = {r["doc_id"]: r for r in ALL_ROWS}
    for row in ALL_ROWS:
        raw, mime = document(row["doc_id"])
        is_pdf = mime == "application/pdf"
        read = text.extract(raw, mime) if is_pdf else None
        original = read.text if read else ""
        cleaned = redact.redact(original).text if original else None
        parsed = extract.extract(original).bill if original else None
        fingerprint = (
            duplicates.invoice_fingerprint(
                parsed.facility_name, parsed.invoice_number, parsed.total_kes
            )
            if parsed
            else None
        )
        page = _page_image(raw, mime)
        mine = CaseFactory()
        document_row = CaseDocumentFactory(
            case=mine,
            sha256=hashlib.sha256(raw).hexdigest(),
            phash=duplicates.image_hash(page) if page else None,
            redacted_text=cleaned,
            fingerprint=fingerprint,
        )
        db_session.flush()
        raised = codes(
            duplicates.check(
                db_session,
                mine,
                document_row,
                sha256=document_row.sha256,
                phash=document_row.phash,
                fingerprint=fingerprint,
                redacted_text=cleaned,
                invoice_number=parsed.invoice_number if parsed else None,
            )
        )
        original_is_pdf = row["dup_of"] and by_id[row["dup_of"]]["filename"].endswith(".pdf")
        # A copy of a photograph can only be recognised by what the photograph says, which
        # needs OCR; the evaluation (P3-F16) measures those with Tesseract.
        if row["label"] == "duplicate" and is_pdf and original_is_pdf and not raised:
            missed.append(row["doc_id"])
        if (
            row["label"] == "duplicate"
            and not is_pdf
            and "duplicate_exact" not in raised
            and row["dup_variant"] == "exact_copy"
        ):
            missed.append(row["doc_id"])
        if row["label"] != "duplicate" and raised:
            wrongly_flagged.append((row["doc_id"], row["label"], row["tamper_type"], raised))
    assert missed == []
    assert wrongly_flagged == []
