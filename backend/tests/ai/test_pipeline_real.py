"""The triage pipeline on real bills from the synthetic set: storage, checks, score and routing."""

import hashlib
import re
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import lexicon
from app.ai import llm, pipeline, redact
from app.models.case import CaseDocument, ReviewItem, TriageResult
from app.models.enums import (
    CaseStatus,
    ConsentScope,
    DocumentKind,
    DocumentStatus,
    ReviewSource,
    RiskBand,
    TriageRoute,
)
from app.models.hospital import normalize_facility_name
from app.services import storage_service
from app.synthetic_data import load_register
from tests.ai.helpers import document, ground_truth, labels, needs_dataset
from tests.factories import (
    CaseDocumentFactory,
    CaseFactory,
    ConsentFactory,
    FacilityRegisterEntryFactory,
    HospitalFactory,
)

pytestmark = pytest.mark.usefixtures("s3")


@pytest.fixture
def register(db_session):  # type: ignore[no-untyped-def]
    """The 25 fictional facilities, each onboarded as a hospital so a case can choose it."""
    hospitals = {}
    for row in load_register():
        entry = FacilityRegisterEntryFactory(
            register_code=row.register_code,
            name=row.name,
            normalized_name=normalize_facility_name(row.name),
            official_email=row.official_email,
            official_phone=row.official_phone,
            county=row.county,
        )
        hospitals[row.register_code] = HospitalFactory(register_entry=entry)
    return hospitals


def submit(
    register,
    doc_id: str,
    *,
    status: CaseStatus = CaseStatus.PENDING_TRIAGE,
    kind: DocumentKind = DocumentKind.INVOICE,
):  # type: ignore[no-untyped-def]
    """A case at the right hospital with the bill stored, ready for the triage job."""
    row = next(r for r in labels() if r["doc_id"] == doc_id)
    raw, mime = document(doc_id)
    case = CaseFactory(
        hospital=register.get(row["register_code"]) or HospitalFactory(), status=status
    )
    key = storage_service.document_key(
        case.id, uuid.uuid4(), "pdf" if mime == "application/pdf" else "jpg"
    )
    storage_service.put_object(key, raw, mime)
    stored = CaseDocumentFactory(
        case=case,
        kind=kind,
        storage_key=key,
        mime_type=mime,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    from app.extensions import db

    db.session.commit()
    return case, stored


def triage(register, doc_id: str, **kw):  # type: ignore[no-untyped-def]
    case, stored = submit(register, doc_id, **kw)
    result = pipeline.process_document(stored.id)
    return case, stored, result


# --------------------------------------------------------------------------- a genuine bill


@needs_dataset
def test_a_genuine_bill_is_read_checked_and_sent_to_the_hospital(register, db_session) -> None:  # type: ignore[no-untyped-def]
    row = next(r for r in labels() if r["label"] == "genuine" and r["filename"].endswith(".pdf"))
    truth = ground_truth()[row["doc_id"]]["fields"]

    case, stored, result = triage(register, row["doc_id"])

    assert result.risk_band == RiskBand.LOW
    assert result.route == TriageRoute.HOSPITAL_CONFIRMATION
    assert result.signals == []
    assert case.status == CaseStatus.PENDING_CONFIRMATION
    assert stored.status == DocumentStatus.TRIAGED
    assert result.reasons_en == [
        "The automatic checks found nothing unusual. The hospital will still confirm the bill."
    ]
    extracted = result.extracted
    assert extracted["facility_name"] == truth["facility"]
    assert extracted["invoice_number"] == row["invoice_number_shown"]
    assert Decimal(extracted["total_kes"]) == Decimal(row["total_shown"])
    assert "patient_reference" not in extracted
    assert result.model_versions["text"] == "text_layer"
    assert result.model_versions["llm"] == "none"
    assert result.used_llm is False
    assert result.duration_ms >= 0


@needs_dataset
def test_only_clean_text_is_stored_and_the_fingerprint_and_page_hash_are_set(
    register, db_session
) -> None:  # type: ignore[no-untyped-def]
    row = next(r for r in labels() if r["label"] == "genuine" and r["filename"].endswith(".pdf"))
    truth = ground_truth()[row["doc_id"]]

    _, stored, _ = triage(register, row["doc_id"])

    kept = re.sub(r"\s+", " ", stored.redacted_text).lower()
    for secret in truth["must_redact"]:
        assert re.sub(r"\s+", " ", secret).lower() not in kept
    assert redact.residual(stored.redacted_text) == []
    assert lexicon.find_medical_terms(stored.redacted_text) == []
    assert len(stored.phash) == 16
    assert len(stored.fingerprint) == 64
    assert stored.page_count == 1


@needs_dataset
@pytest.mark.parametrize(
    "row",
    [r for r in labels() if r["label"] == "genuine" and r["filename"].endswith(".pdf")],
    ids=lambda r: r["doc_id"],
)
def test_no_genuine_digital_bill_is_flagged(register, db_session, row: dict) -> None:  # type: ignore[no-untyped-def]
    _, _, result = triage(register, row["doc_id"])

    assert result.signals == []
    assert result.route == TriageRoute.HOSPITAL_CONFIRMATION


# --------------------------------------------------------------------------- tampered and copied


@needs_dataset
def test_an_edited_total_sends_the_bill_to_a_person_with_the_reasons(register, db_session) -> None:  # type: ignore[no-untyped-def]
    row = next(
        r
        for r in labels()
        if r["tamper_type"] == "overlay_total" and r["filename"].endswith(".pdf")
    )

    case, stored, result = triage(register, row["doc_id"])

    codes = [s["code"] for s in result.signals]
    assert {"totals_mismatch", "hidden_text_under_overlay", "pdf_editor_software"} <= set(codes)
    assert result.risk_band == RiskBand.HIGH
    assert result.route == TriageRoute.MANUAL_REVIEW
    assert case.status == CaseStatus.IN_REVIEW
    assert len(result.reasons_en) == 3
    assert result.reasons_en[0] == "Part of the bill has been covered over and typed again."
    item = db_session.execute(select(ReviewItem).where(ReviewItem.case_id == case.id)).scalar_one()
    assert item.source == ReviewSource.AI_FLAG
    assert item.payload["triage_result_id"] == str(result.id)
    assert set(codes) == set(item.payload["signals"])
    assert item.payload["risk_score"] == result.risk_score


@needs_dataset
def test_the_same_bill_on_another_case_is_a_duplicate_and_goes_to_review(
    register, db_session
) -> None:  # type: ignore[no-untyped-def]
    original = next(
        r
        for r in labels()
        if r["label"] == "duplicate"
        and r["dup_variant"] == "exact_copy"
        and r["filename"].endswith(".pdf")
    )
    triage(register, original["dup_of"])

    case, _, result = triage(register, original["doc_id"])

    codes = [s["code"] for s in result.signals]
    assert "duplicate_exact" in codes
    assert result.route == TriageRoute.MANUAL_REVIEW
    assert case.status == CaseStatus.IN_REVIEW


@needs_dataset
def test_a_hospital_missing_from_the_register_is_noted_but_not_rejected(
    register, db_session
) -> None:  # type: ignore[no-untyped-def]
    row = next(r for r in labels() if r["tamper_type"] == "unknown_facility")
    if not row["filename"].endswith(".pdf"):
        pytest.skip("this synthetic bill is a photograph and needs OCR")
    _, _, result = triage(register, row["doc_id"])

    assert "facility_not_in_register" in [s["code"] for s in result.signals]
    assert result.route in (TriageRoute.HOSPITAL_CONFIRMATION, TriageRoute.MANUAL_REVIEW)


# ----------------------------------------------------------- when the file cannot be read


def test_a_missing_file_goes_to_the_hospital_with_a_note_and_never_fails_the_job(
    register, db_session
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.PENDING_TRIAGE)
    stored = CaseDocumentFactory(case=case, storage_key=None)
    from app.extensions import db

    db.session.commit()

    result = pipeline.process_document(stored.id)

    assert result.route == TriageRoute.HOSPITAL_CONFIRMATION
    assert [s["code"] for s in result.signals] == ["low_extraction_confidence"]
    assert case.status == CaseStatus.PENDING_CONFIRMATION


def test_a_deleted_original_is_treated_like_a_missing_file(register, db_session) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    case = CaseFactory(status=CaseStatus.PENDING_TRIAGE)
    stored = CaseDocumentFactory(
        case=case, storage_key="cases/x/y.pdf", raw_deleted_at=datetime.now(UTC)
    )
    from app.extensions import db

    db.session.commit()

    assert [s["code"] for s in pipeline.process_document(stored.id).signals] == [
        "low_extraction_confidence"
    ]


def test_a_storage_outage_fails_the_document_and_raises_so_the_queue_retries(
    register, db_session
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.PENDING_TRIAGE)
    stored = CaseDocumentFactory(case=case, storage_key="cases/does/not-exist.pdf")
    from app.extensions import db

    db.session.commit()

    with pytest.raises(storage_service.StorageError):
        pipeline.process_document(stored.id)

    db.session.expire_all()
    assert db.session.get(CaseDocument, stored.id).status == DocumentStatus.FAILED
    assert db.session.execute(select(TriageResult)).first() is None


# --------------------------------------------------------------------------- the language model


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        block = SimpleNamespace(
            type="tool_use", name=llm.TOOL_NAME, input={"confidence": 0.9, "total_kes": "1"}
        )
        return SimpleNamespace(content=[block])


@pytest.fixture
def model(app, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setitem(app.config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setitem(app.config, "LLM_API_KEY", "test-key-not-real")
    monkeypatch.setitem(app.config, "LLM_MODEL", "test-model")
    client = RecordingClient()
    monkeypatch.setattr(llm, "client_factory", lambda: client)
    return client


@needs_dataset
def test_the_model_is_not_used_without_the_familys_consent(register, db_session, model) -> None:  # type: ignore[no-untyped-def]
    row = next(r for r in labels() if r["label"] == "genuine" and r["filename"].endswith(".pdf"))

    _, _, result = triage(register, row["doc_id"])

    assert model.calls == []
    assert result.used_llm is False


@needs_dataset
def test_with_consent_only_clean_text_goes_to_the_model_and_its_total_cannot_win(
    register, db_session, model
) -> None:  # type: ignore[no-untyped-def]
    row = next(r for r in labels() if r["label"] == "genuine" and r["filename"].endswith(".pdf"))
    truth = ground_truth()[row["doc_id"]]
    case, stored = submit(register, row["doc_id"])
    ConsentFactory(case=case, scope=ConsentScope.AI_PROCESSING_ABROAD)
    from app.extensions import db

    db.session.commit()

    result = pipeline.process_document(stored.id)

    assert result.used_llm is True
    assert result.model_versions["llm"] == "test-model"
    sent = model.calls[0]["messages"][0]["content"]
    for secret in truth["must_redact"]:
        assert secret not in sent
    assert Decimal(result.extracted["total_kes"]) == Decimal(
        row["total_shown"]
    )  # not the model's 1
    assert "extraction_disagreement" in [s["code"] for s in result.signals]


# --------------------------------------------------------------------------- final bills


@needs_dataset
def test_a_final_bill_on_a_live_case_gets_a_review_item_without_moving_the_case(
    register, db_session
) -> None:  # type: ignore[no-untyped-def]
    row = next(
        r
        for r in labels()
        if r["tamper_type"] == "overlay_total" and r["filename"].endswith(".pdf")
    )

    case, stored, result = triage(
        register, row["doc_id"], status=CaseStatus.LIVE, kind=DocumentKind.FINAL_BILL
    )

    assert result.route == TriageRoute.MANUAL_REVIEW
    assert case.status == CaseStatus.LIVE
    assert stored.status == DocumentStatus.TRIAGED
    assert (
        db_session.execute(select(ReviewItem).where(ReviewItem.case_id == case.id))
        .scalar_one()
        .source
        == ReviewSource.AI_FLAG
    )
