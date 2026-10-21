import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.case import MAX_UPLOAD_BYTES, CaseDocument
from app.models.enums import DocumentKind, DocumentStatus, RiskBand, TriageRoute
from tests.factories import (
    CaseDocumentFactory,
    CaseFactory,
    TriageResultFactory,
)
from tests.models.helpers import assert_db_rejects


def test_document_defaults(db_session) -> None:
    document = CaseDocumentFactory()
    db_session.refresh(document)
    assert document.status is DocumentStatus.UPLOADED
    assert document.storage_key is None
    assert document.redacted_text is None
    assert document.raw_deleted_at is None
    assert document.phash is None
    assert document.fingerprint is None


def test_document_links_to_its_case_both_ways(db_session) -> None:
    document = CaseDocumentFactory()
    db_session.flush()
    assert document in document.case.documents


@pytest.mark.parametrize("size", [0, -5, MAX_UPLOAD_BYTES + 1])
def test_size_must_be_between_1_byte_and_10_mib(db_session, size: int) -> None:
    error = assert_db_rejects(db_session, lambda: CaseDocumentFactory(size_bytes=size))
    assert "ck_case_documents_size_range" in str(error)


@pytest.mark.parametrize("size", [1, MAX_UPLOAD_BYTES])
def test_size_limits_are_accepted(db_session, size: int) -> None:
    assert CaseDocumentFactory(size_bytes=size).size_bytes == size


def test_kind_is_a_lowercase_native_enum(db_session) -> None:
    CaseDocumentFactory(kind=DocumentKind.FINAL_BILL)
    assert db_session.execute(text("SELECT kind::text FROM case_documents")).scalar_one() == (
        "final_bill"
    )


def test_hashes_have_fixed_widths(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: CaseDocumentFactory(sha256="a" * 65))
    assert isinstance(error, DataError)


def test_lookup_columns_are_indexed(db_session) -> None:
    names = set(
        db_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'case_documents'")
        ).scalars()
    )
    assert {
        "ix_case_documents_case_id",
        "ix_case_documents_sha256",
        "ix_case_documents_phash",
        "ix_case_documents_fingerprint",
    } <= names


def test_deleting_a_case_removes_its_documents_and_consents(db_session) -> None:
    document = CaseDocumentFactory()
    db_session.flush()
    case_id = document.case.id
    db_session.delete(document.case)
    db_session.flush()
    remaining = db_session.execute(
        text("SELECT count(*) FROM case_documents WHERE case_id = :id"), {"id": case_id}
    ).scalar_one()
    assert remaining == 0


def test_a_document_must_belong_to_a_case(db_session) -> None:
    document = CaseDocumentFactory()
    db_session.flush()
    stray = CaseDocument(
        uploaded_by_id=document.uploaded_by_id,
        kind=DocumentKind.INVOICE,
        mime_type="application/pdf",
        size_bytes=10,
        sha256="b" * 64,
    )
    error = assert_db_rejects(db_session, lambda: db_session.add(stray))
    assert isinstance(error, IntegrityError)


def test_photo_reference_is_nulled_when_the_document_goes(db_session) -> None:
    case = CaseFactory()
    photo = CaseDocumentFactory(case=case, kind=DocumentKind.PHOTO, mime_type="image/jpeg")
    db_session.flush()
    case.photo_document_id = photo.id
    db_session.flush()
    db_session.execute(text("DELETE FROM case_documents WHERE id = :id"), {"id": photo.id})
    db_session.expire(case)
    assert case.photo_document_id is None


def test_triage_result_round_trips_json(db_session) -> None:
    result = TriageResultFactory(
        extracted={"total_kes": "85000.00", "line_items": [{"label": "Bed", "amount_kes": "5000"}]},
        signals=[{"code": "totals_mismatch", "weight": 25, "detail": "Sum differs"}],
        risk_score=25,
        risk_band=RiskBand.LOW,
    )
    db_session.flush()
    db_session.expire(result)
    assert result.extracted["line_items"][0]["label"] == "Bed"
    assert result.signals[0]["code"] == "totals_mismatch"
    assert result.route is TriageRoute.HOSPITAL_CONFIRMATION


def test_triage_result_links_to_case_and_document(db_session) -> None:
    result = TriageResultFactory()
    db_session.flush()
    assert result in result.document.triage_results
    assert result in result.case.triage_results
    assert result.case is result.document.case


@pytest.mark.parametrize("score", [-1, 101])
def test_risk_score_must_be_between_0_and_100(db_session, score: int) -> None:
    error = assert_db_rejects(db_session, lambda: TriageResultFactory(risk_score=score))
    assert "ck_triage_results_risk_score_range" in str(error)


@pytest.mark.parametrize("score", [0, 100])
def test_risk_score_limits_are_accepted(db_session, score: int) -> None:
    assert TriageResultFactory(risk_score=score).risk_score == score


def test_several_results_can_exist_per_document(db_session) -> None:
    document = CaseDocumentFactory()
    TriageResultFactory(document=document, case=document.case, risk_score=40)
    TriageResultFactory(document=document, case=document.case, risk_score=10)
    db_session.flush()
    assert len(document.triage_results) == 2


def test_triage_json_columns_are_required(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: TriageResultFactory(extracted=None))
    assert isinstance(error, IntegrityError)


def test_a_case_with_triage_results_cannot_be_deleted(db_session) -> None:
    result = TriageResultFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(
            text("DELETE FROM cases WHERE id = :id"), {"id": result.case_id}
        ),
    )
    assert isinstance(error, IntegrityError)
