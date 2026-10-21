"""Bills a family uploads, and submitting a case for checking.

Uploads are validated from their bytes (``security.uploads``), stored privately under an
ID-based key, and described by a row that holds the hash, size and type but never the original
file name. Submitting a case needs both required consents and at least one bill, moves it to
``pending_triage`` and queues a triage job for each bill.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict, NotFound
from app.jobs import queue as jobs
from app.models.case import Case, CaseDocument
from app.models.enums import (
    CaseStatus,
    ConsentScope,
    DocumentKind,
    DocumentStatus,
)
from app.models.identity import User
from app.security import uploads
from app.services import audit_service, case_service, storage_service

logger = logging.getLogger(__name__)

# Kinds a family may upload while the case is a draft. The final bill has its own route, and the
# photo arrives with the photo feature.
DRAFT_UPLOAD_KINDS = (DocumentKind.INVOICE, DocumentKind.ESTIMATE)
BILL_KINDS = frozenset({DocumentKind.INVOICE, DocumentKind.ESTIMATE, DocumentKind.FINAL_BILL})
MAX_BILL_DOCUMENTS = 5
REQUIRED_CONSENTS = (ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE)
TRIAGE_JOB = "app.jobs.tasks.triage_document"


def _bills(case: Case) -> list[CaseDocument]:
    return [document for document in case.documents if document.kind in BILL_KINDS]


def add_document(
    session: Session,
    case: Case,
    user: User,
    *,
    data: bytes,
    filename: str | None,
    declared_type: str | None,
    kind: DocumentKind,
) -> CaseDocument:
    """Validate and store one upload on a draft case. The caller commits."""
    if case.status != CaseStatus.DRAFT:
        raise Conflict(
            "Bills can only be added while the case is a draft.", code="case_not_editable"
        )
    if kind not in DRAFT_UPLOAD_KINDS:
        raise Conflict(
            "Upload an invoice or an estimate here.",
            code="document_kind_not_allowed",
            details={"allowed": [k.value for k in DRAFT_UPLOAD_KINDS]},
        )
    checked = uploads.validate_upload(data, filename=filename, declared_type=declared_type)
    if len(_bills(case)) >= MAX_BILL_DOCUMENTS:
        raise Conflict(
            f"A case can have at most {MAX_BILL_DOCUMENTS} bills.", code="too_many_documents"
        )
    if any(document.sha256 == checked.sha256 for document in case.documents):
        raise Conflict("That file is already on this case.", code="document_already_uploaded")

    document_id = uuid.uuid4()
    key = storage_service.document_key(case.id, document_id, checked.extension)
    storage_service.put_object(key, data, checked.mime_type)
    try:
        document = CaseDocument(
            id=document_id,
            case=case,
            uploaded_by_id=user.id,
            kind=kind,
            status=DocumentStatus.UPLOADED,
            storage_key=key,
            mime_type=checked.mime_type,
            size_bytes=checked.size_bytes,
            page_count=checked.page_count,
            sha256=checked.sha256,
        )
        session.add(document)
        session.flush()
        audit_service.record(
            "document.uploaded",
            case,
            actor=user,
            details={
                "document_id": document.id,
                "kind": kind,
                "mime_type": checked.mime_type,
                "size_bytes": checked.size_bytes,
            },
        )
    except Exception:
        _forget_object(key)
        raise
    return document


def _forget_object(key: str) -> None:
    """Remove an object whose database row could not be written (best effort)."""
    try:
        storage_service.delete_object(key)
    except storage_service.StorageError:
        logger.warning("could not remove an orphaned upload")


def remove_document(session: Session, case: Case, user: User, document_id: uuid.UUID) -> None:
    """Delete a bill from a draft case, file and row. The caller commits."""
    if case.status != CaseStatus.DRAFT:
        raise Conflict(
            "Bills can only be removed while the case is a draft.", code="case_not_editable"
        )
    document = next((d for d in case.documents if d.id == document_id), None)
    if document is None:
        raise NotFound("Document not found.", code="document_not_found")
    if document.storage_key:
        storage_service.delete_object(document.storage_key)
    audit_service.record(
        "document.removed",
        case,
        actor=user,
        details={"document_id": document.id, "kind": document.kind},
    )
    case.documents.remove(document)
    session.delete(document)
    session.flush()


def list_documents(case: Case) -> list[CaseDocument]:
    """The case's documents, oldest first."""
    return sorted(case.documents, key=lambda document: document.created_at)


def raw_available(document: CaseDocument) -> bool:
    """True while the original file is still held (it is deleted after confirmation)."""
    return document.storage_key is not None and document.raw_deleted_at is None


def preview_url(document: CaseDocument) -> str | None:
    """A five-minute link to the original while it still exists, otherwise ``None``."""
    if not raw_available(document) or document.storage_key is None:
        return None
    return storage_service.signed_url(document.storage_key)


def submit_case(session: Session, case: Case, user: User) -> list[CaseDocument]:
    """Move a complete draft to ``pending_triage`` and return the bills to triage.

    The caller commits, then calls ``enqueue_triage`` with the result: a worker must never see a
    case that is not saved yet.
    """
    if case.status != CaseStatus.DRAFT:
        raise case_service.InvalidTransition(
            f"A case that is {case.status.value} cannot be submitted.",
            details={"from": case.status.value, "to": CaseStatus.PENDING_TRIAGE.value},
        )
    missing = [scope.value for scope in REQUIRED_CONSENTS if case.active_consent(scope) is None]
    if missing:
        raise Conflict(
            "Both required consents are needed before the case can be submitted.",
            code="consent_required",
            details={"missing": missing},
        )
    bills = _bills(case)
    if not bills:
        raise Conflict("Upload the hospital bill before submitting.", code="bill_required")
    case_service.transition(case, CaseStatus.PENDING_TRIAGE, user)
    audit_service.record("case.submitted", case, actor=user, details={"documents": len(bills)})
    session.flush()
    return bills


def enqueue_triage(documents: list[CaseDocument]) -> int:
    """Queue a triage job for each document. Returns how many were queued.

    A queue that is down must not undo a submission: the scheduler's sweep finds documents still
    waiting and queues them again.
    """
    queued = 0
    for document in documents:
        try:
            jobs.enqueue(jobs.QUEUE_TRIAGE, TRIAGE_JOB, document.id)
            queued += 1
        except Exception:
            logger.exception("could not queue triage; the sweep will retry")
    return queued


def documents_waiting_for_triage(
    session: Session, older_than_seconds: int = 300
) -> list[CaseDocument]:
    """Bills of cases in ``pending_triage`` that no worker has started on, for the sweep."""
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    statement = (
        select(CaseDocument)
        .join(Case, CaseDocument.case_id == Case.id)
        .where(
            Case.status == CaseStatus.PENDING_TRIAGE,
            Case.submitted_at < cutoff,
            CaseDocument.kind.in_(BILL_KINDS),
            CaseDocument.status == DocumentStatus.UPLOADED,
        )
        .order_by(CaseDocument.created_at)
    )
    return list(session.execute(statement).scalars())
