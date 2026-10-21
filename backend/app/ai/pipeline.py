"""Triage orchestration for one uploaded bill.

Phase 2 ships a stub that returns a neutral result, so the whole flow (upload, submit, triage,
hospital confirmation) works end to end. Phase 3 replaces ``analyse`` with the real checks and
keeps everything around it: the result is stored, the document is marked triaged, and once every
bill on the case is triaged the case moves on.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.extensions import db
from app.models.case import Case, CaseDocument, TriageResult
from app.models.enums import (
    ActorType,
    CaseStatus,
    DocumentKind,
    DocumentStatus,
    RiskBand,
    TriageRoute,
)
from app.services import case_service

logger = logging.getLogger(__name__)

BILL_KINDS = frozenset({DocumentKind.INVOICE, DocumentKind.ESTIMATE, DocumentKind.FINAL_BILL})
STUB_VERSION = "stub-v0"


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


def analyse(document: CaseDocument) -> TriageOutcome:
    """The stub: nothing was checked yet, so the bill goes to the hospital with a neutral note."""
    return TriageOutcome(
        reasons_en=["Automatic checks pending"],
        # Swahili wording is unreviewed; a native speaker checks it in P4-F10.
        reasons_sw=["Ukaguzi wa kiotomatiki unasubiri"],
        model_versions={"pipeline": STUB_VERSION},
    )


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
    if case.status != CaseStatus.PENDING_TRIAGE:
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
