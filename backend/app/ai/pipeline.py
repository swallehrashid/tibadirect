"""Triage of one uploaded bill, step by step (specification 7.1).

1. Read the original file from private storage.
2. Read its text (text layer, or OCR).
3. Redact it, and store only the redacted text and the page hash.
4. Read the fields, with the language model as a second opinion only if the family agreed and a
   key is set.
5. Raise signals: reading problems, duplicates, the facility, tampering.
6. Score them, choose the route and write the reasons.
7. Save a ``TriageResult``, mark the document triaged, open a review item if a person must look,
   and move the case on once every bill on it has a result.

The engine triages and explains; it never approves or rejects. A bill whose original is gone, or
that cannot be read, still goes to the hospital with a note that it could not be read.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.ai import duplicates, extract, facility, llm, redact, risk, tamper, text
from app.ai.reasons import reasons_for
from app.ai.signals import Signal
from app.extensions import db
from app.models.case import Case, CaseDocument, TriageResult
from app.models.enums import (
    ActorType,
    CaseStatus,
    ConsentScope,
    DocumentKind,
    DocumentStatus,
    ReviewSource,
    RiskBand,
    TriageRoute,
)
from app.services import case_service, review_service, storage_service

logger = logging.getLogger(__name__)

BILL_KINDS = frozenset({DocumentKind.INVOICE, DocumentKind.ESTIMATE, DocumentKind.FINAL_BILL})
PIPELINE_VERSION = "v1"


@dataclass(frozen=True)
class TriageOutcome:
    """What the checks found, ready to be stored as a ``TriageResult``."""

    extracted: dict[str, Any] = field(default_factory=dict)
    signals: list[Any] = field(default_factory=list)
    risk_score: int = 0
    risk_band: RiskBand = RiskBand.LOW
    route: TriageRoute = TriageRoute.HOSPITAL_CONFIRMATION
    reasons_en: list[str] = field(default_factory=list)
    reasons_sw: list[str] = field(default_factory=list)
    used_llm: bool = False
    model_versions: dict[str, str] = field(default_factory=dict)


def _unreadable(reason: str) -> TriageOutcome:
    """A bill with no file to read: it goes to the hospital, with the reason in plain words."""
    signals = [Signal("low_extraction_confidence", {"reason": reason})]
    assessed = risk.assess(signals)
    en, sw = reasons_for(signals)
    return TriageOutcome(
        signals=[s.as_json() for s in signals],
        risk_score=assessed.score,
        risk_band=assessed.band,
        route=assessed.route,
        reasons_en=en,
        reasons_sw=sw,
        model_versions={"pipeline": PIPELINE_VERSION},
    )


def analyse(document: CaseDocument) -> TriageOutcome:
    """Run every check on one document and return the outcome. Updates the document's stored
    redacted text, page hash and fingerprint; the caller commits."""
    case = document.case
    if document.storage_key is None or document.raw_deleted_at is not None:
        return _unreadable("no_file")
    raw = storage_service.get_object(document.storage_key)
    read = text.extract(raw, document.mime_type)

    cleaned = redact.redact(read.text)
    use_model = (
        case.active_consent(ConsentScope.AI_PROCESSING_ABROAD) is not None and llm.available()
    )
    extraction = extract.extract(read.text, use_llm=use_model, ocr_confidence=read.confidence)
    bill = extraction.bill

    document.redacted_text = cleaned.text or None
    document.phash = duplicates.image_hash(read.first_page) if read.first_page else None
    if read.page_count:
        document.page_count = min(read.page_count, 32767)

    signals: list[Signal] = list(extraction.signals)
    if read.error or (not read.readable and not read.ocr_available):
        signals = [s for s in signals if s.code != "low_extraction_confidence"]
        signals.append(
            Signal("low_extraction_confidence", {"reason": read.error or "ocr_unavailable"})
        )

    matched = facility.check(db.session, bill, read.text, case.hospital)
    canonical = (
        matched.match.entry.name
        if matched.match.entry and matched.match.score >= 85
        else bill.facility_name
    )
    document.fingerprint = duplicates.invoice_fingerprint(
        canonical, bill.invoice_number, bill.total_kes
    )

    signals.extend(
        duplicates.check(
            db.session,
            case,
            document,
            sha256=document.sha256,
            phash=document.phash,
            fingerprint=document.fingerprint,
            redacted_text=document.redacted_text,
            invoice_number=bill.invoice_number,
        )
    )
    signals.extend(matched.signals)
    signals.extend(tamper.check(raw, document.mime_type, read, bill))

    assessed = risk.assess(signals)
    reasons_en, reasons_sw = reasons_for(signals)
    return TriageOutcome(
        extracted=bill.model_dump(mode="json", exclude={"patient_reference"}),
        signals=[s.as_json() for s in signals],
        risk_score=assessed.score,
        risk_band=assessed.band,
        route=assessed.route,
        reasons_en=reasons_en,
        reasons_sw=reasons_sw,
        used_llm=extraction.used_llm,
        model_versions={
            "pipeline": PIPELINE_VERSION,
            "text": read.method,
            "llm": str(llm_model()) if extraction.used_llm else "none",
        },
    )


def llm_model() -> str:
    from flask import current_app

    return current_app.config.get("LLM_MODEL", "")


def _latest_result(document: CaseDocument) -> TriageResult | None:
    return max(document.triage_results, key=lambda result: result.created_at, default=None)


def route_case_when_triaged(case: Case, *, request_id: str | None = None) -> bool:
    """Move the case on once every bill on it has a result. Returns True if it moved.

    One document that needs a person's eye sends the whole case to review; otherwise the case
    goes to the hospital for confirmation.
    """
    if case.status != CaseStatus.PENDING_TRIAGE:
        return False
    bills = [document for document in case.documents if document.kind in BILL_KINDS]
    if not bills or any(document.status != DocumentStatus.TRIAGED for document in bills):
        return False
    results = [_latest_result(document) for document in bills]
    needs_review = any(
        result is not None and result.route == TriageRoute.MANUAL_REVIEW for result in results
    )
    target = CaseStatus.IN_REVIEW if needs_review else CaseStatus.PENDING_CONFIRMATION
    case_service.transition(
        case, target, ActorType.SYSTEM, "triage_complete", request_id=request_id
    )
    return True


def _may_be_triaged(document: CaseDocument, case: Case) -> bool:
    """A first bill is checked while the case waits for triage; a final bill, while it is live."""
    if case.status == CaseStatus.PENDING_TRIAGE:
        return True
    return document.kind == DocumentKind.FINAL_BILL and case.status in (
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
    )


def process_document(
    document_id: uuid.UUID, *, request_id: str | None = None
) -> TriageResult | None:
    """Triage one document inside a job. Safe to run twice: a document is checked once.

    Does nothing if the case has moved on (cancelled, frozen) since the job was queued. A crash
    marks the document failed and re-raises so the queue's retry policy takes over.
    """
    document = db.session.get(CaseDocument, document_id)
    if document is None:
        logger.warning("triage skipped: document not found")
        return None
    case = document.case
    existing = _latest_result(document)
    if existing is not None:
        route_case_when_triaged(case, request_id=request_id)
        db.session.commit()
        return existing
    if not _may_be_triaged(document, case):
        logger.info("triage skipped: case is not waiting for triage")
        return None

    document.status = DocumentStatus.PROCESSING
    db.session.flush()
    started = time.perf_counter()
    try:
        outcome = analyse(document)
        result = TriageResult(
            case=case,
            document=document,
            extracted=outcome.extracted,
            signals=outcome.signals,
            risk_score=outcome.risk_score,
            risk_band=outcome.risk_band,
            route=outcome.route,
            reasons_en=outcome.reasons_en,
            reasons_sw=outcome.reasons_sw,
            used_llm=outcome.used_llm,
            model_versions=outcome.model_versions,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        db.session.add(result)
        document.status = DocumentStatus.TRIAGED
        db.session.flush()
        if outcome.route == TriageRoute.MANUAL_REVIEW:
            review_service.create_item(
                db.session,
                case,
                ReviewSource.AI_FLAG,
                "The automatic checks found something a person should look at",
                {
                    "triage_result_id": str(result.id),
                    "risk_score": outcome.risk_score,
                    "signals": [s["code"] for s in outcome.signals],
                },
            )
        route_case_when_triaged(case, request_id=request_id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        failed = db.session.get(CaseDocument, document_id)
        if failed is not None:
            failed.status = DocumentStatus.FAILED
            db.session.commit()
        raise
    return result
