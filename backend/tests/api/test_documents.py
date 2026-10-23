"""Bill uploads, submitting a case and reading the result of the checks."""

import io
from collections.abc import Callable

import pytest
import requests
from sqlalchemy import select

from app.jobs import queue as jobs
from app.jobs.worker import make_worker
from app.models.case import CaseDocument
from app.models.enums import CaseStatus, ConsentScope, DocumentKind, UserRole
from app.models.platform import AuditLog
from app.services import storage_service
from tests import pdf_fixtures as fx
from tests.browser import Browser
from tests.factories import CaseDocumentFactory, CaseFactory, ConsentFactory, UserFactory

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


def _upload(
    browser: Browser, case, data: bytes, name: str = "bill.pdf", kind: str = "invoice", **kw
):  # type: ignore[no-untyped-def]
    return browser.post(
        f"/api/cases/{case.id}/documents",
        data={"kind": kind, "file": (io.BytesIO(data), name)},
        **kw,
    )


def _consent_both(case) -> None:  # type: ignore[no-untyped-def]
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)


@pytest.fixture
def owner_browser(browser_for: BrowserFor, s3: None):  # type: ignore[no-untyped-def]
    case = CaseFactory()
    return case, browser_for(case.created_by)


# --- Uploading ---------------------------------------------------------------------------------


def test_a_pdf_is_validated_stored_and_described(owner_browser, db_session, app) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    data = fx.clean_pdf(pages=2)
    response = _upload(browser, case, data, "Patient John Doe Invoice.pdf")
    assert response.status_code == 201
    body = response.get_json()
    assert body["kind"] == "invoice"
    assert body["status"] == "uploaded"
    assert body["mime_type"] == "application/pdf"
    assert body["size_bytes"] == len(data)
    assert body["page_count"] == 2
    assert body["raw_available"] is True
    assert "John" not in response.get_data(as_text=True)

    document = db_session.get(CaseDocument, body["id"])
    assert document.storage_key == f"cases/{case.id}/{document.id}.pdf"
    assert len(document.sha256) == 64
    with app.app_context():
        assert storage_service.get_object(document.storage_key) == data


def test_no_part_of_the_original_file_name_is_stored_anywhere(owner_browser, db_session) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _upload(browser, case, fx.png(), "Mama Wanjiru discharge.PNG")
    rows = db_session.execute(select(CaseDocument)).scalars().all()
    audit = db_session.execute(select(AuditLog)).scalars().all()
    dump = repr([(vars(r)) for r in rows]) + repr([a.details for a in audit])
    assert "Wanjiru" not in dump
    assert "discharge" not in dump


def test_png_and_jpeg_are_accepted_too(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    png = _upload(browser, case, fx.png(), "a.png", kind="estimate")
    jpg = _upload(browser, case, fx.jpeg(), "a.jpeg")
    assert (png.status_code, png.get_json()["kind"], png.get_json()["page_count"]) == (
        201,
        "estimate",
        None,
    )
    assert jpg.status_code == 201


def test_the_upload_is_audited_without_identifying_content(owner_browser, db_session) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    response = _upload(browser, case, fx.clean_pdf())
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "document.uploaded")
    ).scalar_one()
    assert entry.details == {
        "document_id": response.get_json()["id"],
        "kind": "invoice",
        "mime_type": "application/pdf",
        "size_bytes": len(fx.clean_pdf()),
    }
    assert entry.request_id == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    ("data", "name", "status", "code"),
    [
        (fx.pdf_with_javascript(), "a.pdf", 422, "pdf_active_content"),
        (fx.encrypted_pdf(), "a.pdf", 422, "pdf_encrypted"),
        (fx.clean_pdf(pages=11), "a.pdf", 422, "pdf_too_many_pages"),
        (b"MZ" + b"\x00" * 100, "invoice.pdf", 415, "file_type_not_allowed"),
        (fx.png(), "invoice.pdf", 422, "file_type_mismatch"),
        (fx.huge_png(6000, 5000), "a.png", 422, "image_too_large"),
    ],
)
def test_hostile_or_unsuitable_files_are_refused_and_nothing_is_kept(
    owner_browser, db_session, app, data: bytes, name: str, status: int, code: str
) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    response = _upload(browser, case, data, name)
    assert (response.status_code, _error(response)["code"]) == (status, code)
    assert db_session.execute(select(CaseDocument)).first() is None
    with app.app_context():
        assert list(storage_service._keys_under("cases/")) == []


def test_a_file_over_ten_megabytes_is_413(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    response = _upload(browser, case, b"%PDF-" + b"0" * 10_600_000)
    assert response.status_code == 413
    assert _error(response)["code"] == "file_too_large"


def test_a_request_far_over_the_limit_is_refused_before_it_is_read(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    response = _upload(browser, case, b"%PDF-" + b"0" * 13_000_000)
    assert response.status_code == 413
    assert _error(response)["code"] == "payload_too_large"


def test_a_missing_file_or_kind_is_422(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    no_file = browser.post(f"/api/cases/{case.id}/documents", data={"kind": "invoice"})
    no_kind = browser.post(
        f"/api/cases/{case.id}/documents", data={"file": (io.BytesIO(fx.png()), "a.png")}
    )
    bad_kind = _upload(browser, case, fx.png(), "a.png", kind="selfie")
    assert [r.status_code for r in (no_file, no_kind, bad_kind)] == [422, 422, 422]
    assert "file" in _error(no_file)["details"]["files"]
    assert "kind" in _error(no_kind)["details"]["form"]


@pytest.mark.parametrize("kind", ["final_bill", "photo"])
def test_kinds_with_their_own_flow_are_refused_here(owner_browser, kind: str) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    response = _upload(browser, case, fx.png(), "a.png", kind=kind)
    assert (response.status_code, _error(response)["code"]) == (409, "document_kind_not_allowed")
    assert _error(response)["details"]["allowed"] == ["invoice", "estimate"]


def test_the_same_file_twice_is_refused(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    data = fx.png()
    assert _upload(browser, case, data, "a.png").status_code == 201
    again = _upload(browser, case, data, "copy.png")
    assert (again.status_code, _error(again)["code"]) == (409, "document_already_uploaded")


def test_a_case_holds_at_most_five_bills(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    for width in range(10, 15):
        assert _upload(browser, case, fx.png(width, 10), f"{width}.png").status_code == 201
    sixth = _upload(browser, case, fx.png(99, 10), "six.png")
    assert (sixth.status_code, _error(sixth)["code"]) == (409, "too_many_documents")


def test_uploads_are_only_for_drafts(owner_browser, db_session) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    case.status = CaseStatus.PENDING_TRIAGE
    db_session.flush()
    response = _upload(browser, case, fx.png(), "a.png")
    assert (response.status_code, _error(response)["code"]) == (409, "case_not_editable")


def test_the_twenty_first_upload_in_an_hour_is_limited(browser_for: BrowserFor, s3) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    browser = browser_for(case.created_by)
    statuses = [
        _upload(browser, case, fx.png(10, 10), "a.png", kind="estimate").status_code
        for _ in range(21)
    ]
    assert statuses.count(429) == 1
    assert statuses[-1] == 429


# --- Listing, preview and removal --------------------------------------------------------------


def test_the_list_shows_metadata_and_a_working_preview_link(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    data = fx.clean_pdf()
    _upload(browser, case, data)
    [item] = browser.get(f"/api/cases/{case.id}/documents").get_json()["items"]
    assert set(item) == {
        "id", "kind", "status", "mime_type", "size_bytes", "page_count", "created_at",
        "raw_available", "preview_url",
    }  # fmt: skip
    assert "X-Amz-Expires=300" in item["preview_url"]
    assert requests.get(item["preview_url"], timeout=5).content == data


def test_once_the_original_is_deleted_there_is_no_link(owner_browser, db_session) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    case, browser = owner_browser
    _upload(browser, case, fx.clean_pdf())
    db_session.execute(select(CaseDocument)).scalar_one().raw_deleted_at = datetime.now(UTC)
    db_session.flush()
    [item] = browser.get(f"/api/cases/{case.id}/documents").get_json()["items"]
    assert item["raw_available"] is False
    assert item["preview_url"] is None


def test_removing_a_bill_deletes_the_file_and_the_row(owner_browser, db_session, app) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    document_id = _upload(browser, case, fx.clean_pdf()).get_json()["id"]
    key = db_session.get(CaseDocument, document_id).storage_key
    assert browser.delete(f"/api/cases/{case.id}/documents/{document_id}").status_code == 204
    assert browser.get(f"/api/cases/{case.id}/documents").get_json()["items"] == []
    with app.app_context():
        assert storage_service.object_exists(key) is False


def test_removal_is_for_drafts_and_this_cases_documents_only(owner_browser, db_session) -> None:  # type: ignore[no-untyped-def]
    import uuid

    case, browser = owner_browser
    other_case_doc = CaseDocumentFactory()
    assert browser.delete(f"/api/cases/{case.id}/documents/{other_case_doc.id}").status_code == 404
    assert browser.delete(f"/api/cases/{case.id}/documents/{uuid.uuid4()}").status_code == 404
    document_id = _upload(browser, case, fx.clean_pdf()).get_json()["id"]
    case.status = CaseStatus.PENDING_TRIAGE
    db_session.flush()
    response = browser.delete(f"/api/cases/{case.id}/documents/{document_id}")
    assert (response.status_code, _error(response)["code"]) == (409, "case_not_editable")


# --- Submitting --------------------------------------------------------------------------------


def test_submitting_needs_both_consents_and_names_what_is_missing(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _upload(browser, case, fx.clean_pdf())
    ConsentFactory(case=case, scope=ConsentScope.RECEIPT_SHARING)
    response = browser.post(f"/api/cases/{case.id}/submit")
    assert (response.status_code, _error(response)["code"]) == (409, "consent_required")
    assert _error(response)["details"]["missing"] == ["hospital_verification", "public_page"]


def test_a_withdrawn_consent_does_not_count(owner_browser) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    case, browser = owner_browser
    _upload(browser, case, fx.clean_pdf())
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE, withdrawn_at=datetime.now(UTC))
    response = browser.post(f"/api/cases/{case.id}/submit")
    assert _error(response)["details"]["missing"] == ["public_page"]


def test_submitting_needs_a_bill(owner_browser) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    response = browser.post(f"/api/cases/{case.id}/submit")
    assert (response.status_code, _error(response)["code"]) == (409, "bill_required")


def test_a_photo_alone_is_not_a_bill(owner_browser, db_session) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    CaseDocumentFactory(case=case, kind=DocumentKind.PHOTO)
    assert _error(browser.post(f"/api/cases/{case.id}/submit"))["code"] == "bill_required"


def test_submitting_queues_triage_and_the_worker_moves_the_case_on(
    owner_browser, async_jobs: None, app, db_session
) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    _upload(browser, case, fx.clean_pdf())
    response = browser.post(f"/api/cases/{case.id}/submit")
    assert response.status_code == 202
    assert response.get_json()["status"] == "pending_triage"
    assert response.get_json()["trust_level"] == "unverified"
    with app.app_context():
        assert jobs.get_queue(jobs.QUEUE_TRIAGE).count == 1
    assert browser.get(f"/api/cases/{case.id}/triage").get_json()["ready"] is False

    # The worker runs in its own context; the test must not hold another session open meanwhile.
    with app.app_context():
        make_worker(app).work(burst=True)

    db_session.expire_all()
    body = browser.get(f"/api/cases/{case.id}").get_json()
    assert body["status"] == "pending_confirmation"
    assert body["trust_level"] == "pending_confirmation"
    assert [step["status"] for step in body["timeline"]] == [
        "draft",
        "pending_triage",
        "pending_confirmation",
    ]
    triage = browser.get(f"/api/cases/{case.id}/triage").get_json()
    assert triage["ready"] is True
    # a blank page cannot be read and names no hospital: worth a note, never a rejection
    assert (triage["risk_band"], triage["route"]) == ("medium", "hospital_confirmation")
    assert triage["reasons_en"] == [
        "We could not find this hospital in the facility register.",
        "We could not read all of the bill clearly.",
    ]
    assert "risk_score" not in triage
    assert "signals" not in triage


def test_inline_mode_runs_the_whole_flow_before_the_response(owner_browser, redis_clean) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    _upload(browser, case, fx.clean_pdf())
    response = browser.post(f"/api/cases/{case.id}/submit")
    assert response.status_code == 202
    assert response.get_json()["status"] == "pending_confirmation"


def test_the_submission_is_audited(owner_browser, redis_clean, db_session) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    _upload(browser, case, fx.clean_pdf())
    response = browser.post(f"/api/cases/{case.id}/submit")
    actions = {
        a.action: a
        for a in db_session.execute(select(AuditLog)).scalars()
        if a.action.startswith("case.")
    }
    assert actions["case.submitted"].details == {"documents": 1}
    assert actions["case.submitted"].request_id == response.headers["X-Request-ID"]


def test_a_second_submission_is_a_conflict(owner_browser, redis_clean) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    _upload(browser, case, fx.clean_pdf())
    browser.post(f"/api/cases/{case.id}/submit")
    again = browser.post(f"/api/cases/{case.id}/submit")
    assert (again.status_code, _error(again)["code"]) == (409, "invalid_transition")


def test_a_queue_that_is_down_does_not_undo_the_submission(
    owner_browser, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    _upload(browser, case, fx.clean_pdf())

    def down(*args: object, **kwargs: object) -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(jobs, "enqueue", down)
    response = browser.post(f"/api/cases/{case.id}/submit")
    assert response.status_code == 202
    assert response.get_json()["status"] == "pending_triage"


def test_withdrawing_hospital_verification_before_triage_runs_cancels_and_skips_it(
    owner_browser, async_jobs: None, app, db_session
) -> None:  # type: ignore[no-untyped-def]
    case, browser = owner_browser
    _consent_both(case)
    _upload(browser, case, fx.clean_pdf())
    browser.post(f"/api/cases/{case.id}/submit")
    browser.delete(f"/api/cases/{case.id}/consents/hospital_verification")
    with app.app_context():
        make_worker(app).work(burst=True)
    db_session.expire_all()
    assert browser.get(f"/api/cases/{case.id}").get_json()["status"] == "cancelled"
    assert browser.get(f"/api/cases/{case.id}/triage").get_json()["ready"] is False


# --- Access ------------------------------------------------------------------------------------


def test_anonymous_callers_get_401(browser: Browser) -> None:
    case = CaseFactory()
    for response in (
        browser.get(f"/api/cases/{case.id}/documents"),
        browser.post(f"/api/cases/{case.id}/submit"),
        browser.get(f"/api/cases/{case.id}/triage"),
    ):
        assert response.status_code == 401


def test_someone_elses_case_is_a_404_on_every_route(browser_for: BrowserFor, s3) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    document = CaseDocumentFactory(case=case)
    stranger = browser_for(UserFactory())
    responses = [
        stranger.get(f"/api/cases/{case.id}/documents"),
        _upload(stranger, case, fx.png(), "a.png"),
        stranger.delete(f"/api/cases/{case.id}/documents/{document.id}"),
        stranger.post(f"/api/cases/{case.id}/submit"),
        stranger.get(f"/api/cases/{case.id}/triage"),
    ]
    assert [r.status_code for r in responses] == [404] * 5
    assert {_error(r)["code"] for r in responses} == {"case_not_found"}


def test_other_roles_are_refused(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    admin = browser_for(UserFactory(role=UserRole.ADMIN))
    assert admin.get(f"/api/cases/{case.id}/documents").status_code == 403
    assert admin.post(f"/api/cases/{case.id}/submit").status_code == 403


def test_the_routes_are_documented(client) -> None:  # type: ignore[no-untyped-def]
    spec = client.get("/api/openapi.json").get_json()
    upload = spec["paths"]["/api/cases/{case_id}/documents"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    assert {"413", "415"} <= set(upload["responses"])
    for path in ("submit", "triage"):
        assert f"/api/cases/{{case_id}}/{path}" in spec["paths"]
