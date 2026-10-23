"""The admin console API: stats, cases, the review queue, freezing and the audit log."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.case import ReviewItem
from app.models.enums import (
    CaseStatus,
    CloseReason,
    ConsentScope,
    ReviewResolution,
    ReviewSource,
    ReviewStatus,
    UserRole,
)
from app.models.platform import AuditLog
from app.seed import SLUGS, seed_all
from app.services import mail_service
from tests.browser import Browser
from tests.factories import (
    CaseFactory,
    ConsentFactory,
    DonationFactory,
    HospitalStaffFactory,
    ReviewItemFactory,
    TriageResultFactory,
    UserFactory,
)

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


@pytest.fixture(autouse=True)
def _freeze_stays_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A freeze ends in a cancellation once the donors are repaid, which the queue does at once in
    tests; these tests are about the freeze itself, so the follow-up is left unqueued."""
    from app.services import case_ending_service

    monkeypatch.setattr(case_ending_service, "enqueue_finish", lambda case_id: None)


@pytest.fixture
def admin(browser_for: BrowserFor, db_session) -> Browser:  # type: ignore[no-untyped-def]
    return browser_for(UserFactory(role=UserRole.ADMIN, email="test.admin@example.test"))


def _resolved_item(reason: str = "done") -> ReviewItem:
    """A review item a person has already resolved (the database insists on every field)."""
    return ReviewItemFactory(
        reason=reason,
        status=ReviewStatus.RESOLVED,
        resolution=ReviewResolution.NO_ACTION,
        resolution_note="Checked and fine.",
        resolved_by_id=UserFactory().id,
        resolved_at=datetime.now(UTC),
    )


ADMIN_ROUTES = [
    ("get", "/api/admin/stats"),
    ("get", "/api/admin/review-items"),
    ("get", "/api/admin/cases"),
    ("get", "/api/admin/audit-log"),
    ("get", f"/api/admin/cases/{uuid.uuid4()}"),
    ("post", f"/api/admin/cases/{uuid.uuid4()}/freeze"),
    ("post", f"/api/admin/cases/{uuid.uuid4()}/settlements/run"),
    ("post", f"/api/admin/review-items/{uuid.uuid4()}/resolve"),
]


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_every_admin_route_refuses_anonymous_family_and_staff(
    browser: Browser, browser_for: BrowserFor, method: str, path: str
) -> None:
    assert getattr(browser, method)(path).status_code == 401
    assert getattr(browser_for(UserFactory()), method)(path).status_code == 403
    staff = HospitalStaffFactory().user
    assert getattr(browser_for(staff), method)(path).status_code == 403


# --- Stats -------------------------------------------------------------------------------------


def test_stats_count_cases_by_status_and_total_the_money(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    seed_all(db_session)
    db_session.flush()
    body = admin.get("/api/admin/stats").get_json()
    assert body["cases_total"] == 8
    assert set(body["cases_by_status"]) == {s.value for s in CaseStatus}
    assert sum(body["cases_by_status"].values()) == 8
    assert body["cases_by_status"]["live"] == 1
    assert body["cases_by_status"]["draft"] == 1
    assert body["money"]["captured_net_usd_minor"] > 0
    assert body["money"]["donations"] > 0
    assert body["users"]["admin"] >= 1
    assert body["ledger_entries"] > 0
    assert set(body["waiting"]) == {"open_review_items", "awaiting_second_approval"}


def test_stats_on_an_empty_system_are_zeros(admin: Browser) -> None:
    body = admin.get("/api/admin/stats").get_json()
    assert body["cases_total"] == 0
    assert set(body["cases_by_status"].values()) == {0}
    assert body["money"] == dict.fromkeys(body["money"], 0)
    assert body["waiting"] == {"open_review_items": 0, "awaiting_second_approval": 0}


def test_stats_show_queue_sizes_and_survive_a_queue_outage(
    admin: Browser, monkeypatch: pytest.MonkeyPatch, redis_clean
) -> None:  # type: ignore[no-untyped-def]
    body = admin.get("/api/admin/stats").get_json()
    assert [q["name"] for q in body["queues"]] == ["triage", "payments", "default"]

    from app.services import job_service

    def down() -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(job_service, "overview", down)
    again = admin.get("/api/admin/stats")
    assert again.status_code == 200
    assert again.get_json()["queues"] is None


def test_stats_count_open_review_items_only(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    ReviewItemFactory()
    ReviewItemFactory()
    _resolved_item()
    assert admin.get("/api/admin/stats").get_json()["waiting"]["open_review_items"] == 2


# --- Cases -------------------------------------------------------------------------------------


def test_the_case_list_is_newest_first_and_paginated(admin: Browser) -> None:
    for index in range(3):
        CaseFactory(alias=f"Case number {index}")
    first = admin.get("/api/admin/cases?page_size=2").get_json()
    second = admin.get("/api/admin/cases?page_size=2&page=2").get_json()
    assert (first["total"], len(first["items"]), len(second["items"])) == (3, 2, 1)
    item = first["items"][0]
    assert set(item) == {
        "id", "public_slug", "alias", "status", "trust_level", "hospital_name", "owner_email",
        "target_kes_minor", "pledged_kes_minor", "settled_kes_minor", "created_at", "updated_at",
    }  # fmt: skip


def test_the_case_list_filters_by_status_and_text(admin: Browser) -> None:
    live = CaseFactory(status=CaseStatus.LIVE, alias="Findable Alias")
    CaseFactory(status=CaseStatus.DRAFT, alias="Other Case")
    by_status = admin.get("/api/admin/cases?status=live").get_json()
    assert [c["id"] for c in by_status["items"]] == [str(live.id)]
    for query in (
        "findable",
        live.public_slug.upper(),
        live.hospital.display_name[:8],
        live.created_by.email,
    ):
        found = admin.get(f"/api/admin/cases?q={query}").get_json()
        assert live.id.hex in {c["id"].replace("-", "") for c in found["items"]}, query
    assert admin.get("/api/admin/cases?q=%25").get_json()["total"] == 0


def test_a_bad_status_filter_is_a_validation_error(admin: Browser) -> None:
    assert admin.get("/api/admin/cases?status=nonsense").status_code == 422
    assert admin.get("/api/admin/cases?q=" + "x" * 81).status_code == 422


def test_the_full_case_view_has_everything_an_admin_needs(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    seed_all(db_session)
    db_session.flush()
    from app.models.case import Case

    case = db_session.execute(select(Case).where(Case.public_slug == SLUGS["funded"])).scalar_one()
    body = admin.get(f"/api/admin/cases/{case.id}").get_json()
    assert body["public_slug"] == SLUGS["funded"]
    assert body["owner"]["email"] == case.created_by.email
    assert body["patient_reference"] == case.patient_reference
    for key in (
        "documents",
        "triage",
        "consents",
        "confirmations",
        "donations",
        "settlements",
        "review_items",
        "audit_trail",
    ):
        assert key in body
    assert len(body["donations"]) >= 1
    assert body["confirmations"][0]["status"] == "confirmed"
    assert {c["scope"] for c in body["consents"]} >= {"public_page", "hospital_verification"}
    assert body["money"]["pledged_kes_minor"] == case.pledged_kes_minor
    assert body["audit_trail"]
    assert "ip_hmac" not in str(body)


def test_the_case_view_shows_the_checks_signals_and_the_donors_choice_of_name(
    admin: Browser, db_session
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    TriageResultFactory(
        case=case,
        document__case=case,
        signals=[{"name": "duplicate_near", "weight": 45}],
        risk_score=45,
    )
    DonationFactory(
        case=case,
        status="captured",
        donor_display_name="Hidden Donor",
        is_anonymous=True,
        net_usd_minor=2400,
    )
    DonationFactory(
        case=case,
        status="captured",
        donor_display_name="Named Donor",
        is_anonymous=False,
        net_usd_minor=2400,
    )
    body = admin.get(f"/api/admin/cases/{case.id}").get_json()
    [triage] = body["triage"]
    assert triage["signals"] == [{"name": "duplicate_near", "weight": 45}]
    assert triage["risk_score"] == 45
    names = {d["donor_display_name"] for d in body["donations"]}
    assert names == {None, "Named Donor"}
    assert "Hidden Donor" not in str(body)


def test_opening_a_case_is_written_to_the_audit_log(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    admin.get(f"/api/admin/cases/{case.id}")
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "admin.case_viewed")
    ).scalar_one()
    assert (entry.target_id, entry.actor_type.value) == (case.id, "user")


def test_an_unknown_case_is_404(admin: Browser) -> None:
    response = admin.get(f"/api/admin/cases/{uuid.uuid4()}")
    assert (response.status_code, _error(response)["code"]) == (404, "case_not_found")


# --- Freezing ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
    ],
)
def test_an_admin_can_freeze_a_case_in_play(admin: Browser, db_session, status: CaseStatus) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=status, trust_level="hospital_confirmed")
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    response = admin.post(
        f"/api/admin/cases/{case.id}/freeze", {"reason": "Bill could not be confirmed"}
    )
    assert response.status_code == 200
    body = response.get_json()
    assert (body["status"], body["trust_level"]) == ("frozen", "unverified")
    assert body["freeze_reason"] == "Bill could not be confirmed"
    db_session.refresh(case)
    assert case.frozen_at is not None
    assert not case.is_publicly_visible


def test_freezing_hides_the_page_and_stops_donations(admin: Browser, client, db_session) -> None:  # type: ignore[no-untyped-def]
    from app.services import case_service

    case = CaseFactory(status=CaseStatus.LIVE, target_kes_minor=8_500_000)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    assert client.get(f"/api/public/cases/{case.public_slug}").status_code == 200
    assert case_service.is_accepting_donations(case)
    admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": "Suspected forged invoice"})
    assert client.get(f"/api/public/cases/{case.public_slug}").status_code == 404
    assert not case_service.is_accepting_donations(case)


def test_freezing_is_audited_with_the_admin_as_actor(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": "Suspected forged invoice"})
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "case.transitioned")
    ).scalar_one()
    assert entry.details == {"from": "live", "to": "frozen"}
    assert entry.actor_user_id is not None
    assert "forged" not in str(entry.details)


@pytest.mark.parametrize("status", [CaseStatus.DRAFT, CaseStatus.CLOSED, CaseStatus.CANCELLED])
def test_a_case_that_is_not_in_play_cannot_be_frozen(admin: Browser, status: CaseStatus) -> None:
    case = CaseFactory(
        status=status,
        close_reason=CloseReason.FULLY_SETTLED if status == CaseStatus.CLOSED else None,
    )
    response = admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": "Because I said so"})
    assert (response.status_code, _error(response)["code"]) == (409, "invalid_transition")


def test_freezing_twice_is_a_conflict(admin: Browser) -> None:
    case = CaseFactory(status=CaseStatus.LIVE)
    admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": "Suspected forged invoice"})
    again = admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": "Suspected forged invoice"})
    assert (again.status_code, _error(again)["code"]) == (409, "case_already_frozen")


@pytest.mark.parametrize("reason", ["", "short", "x" * 301, None])
def test_a_freeze_needs_a_real_reason(admin: Browser, reason: str | None) -> None:
    case = CaseFactory(status=CaseStatus.LIVE)
    response = admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": reason})
    assert response.status_code == 422
    assert case.status == CaseStatus.LIVE


# --- Review queue ------------------------------------------------------------------------------


def test_the_queue_lists_open_items_first_with_the_longest_wait_on_top(
    admin: Browser, db_session
) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    newer = ReviewItemFactory(reason="newer")
    older = ReviewItemFactory(reason="older")
    newer.created_at, older.created_at = now - timedelta(hours=1), now - timedelta(hours=5)
    done = _resolved_item("done")
    done.created_at = now - timedelta(days=9)
    db_session.flush()
    items = admin.get("/api/admin/review-items").get_json()["items"]
    assert [i["reason"] for i in items] == ["older", "newer", "done"]
    only_open = admin.get("/api/admin/review-items?status=open").get_json()
    assert [i["reason"] for i in only_open["items"]] == ["older", "newer"]
    resolved = admin.get("/api/admin/review-items?status=resolved").get_json()
    assert [i["reason"] for i in resolved["items"]] == ["done"]
    assert set(items[0]) == {
        "id", "case_id", "case_alias", "case_status", "source", "status", "reason", "payload",
        "resolution", "resolution_note", "resolved_at", "created_at",
    }  # fmt: skip


def _in_review_item() -> ReviewItem:
    case = CaseFactory(status=CaseStatus.IN_REVIEW)
    return ReviewItemFactory(case=case, source=ReviewSource.AI_FLAG)


def test_approving_for_confirmation_moves_the_case_to_the_hospital(
    admin: Browser, db_session
) -> None:  # type: ignore[no-untyped-def]
    item = _in_review_item()
    response = admin.post(
        f"/api/admin/review-items/{item.id}/resolve",
        {
            "resolution": "approved_for_confirmation",
            "note": "Duplicate was the same family's earlier draft.",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert (body["status"], body["resolution"]) == ("resolved", "approved_for_confirmation")
    assert body["resolution_note"] == "Duplicate was the same family's earlier draft."
    assert body["case_status"] == "pending_confirmation"
    db_session.refresh(item.case)
    assert item.case.status == CaseStatus.PENDING_CONFIRMATION
    assert item.resolved_by_id is not None
    assert item.resolved_at is not None


def test_cancelling_ends_the_case(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    item = _in_review_item()
    body = admin.post(
        f"/api/admin/review-items/{item.id}/resolve",
        {"resolution": "case_cancelled", "note": "The invoice is a copy of another family's."},
    ).get_json()
    assert body["case_status"] == "cancelled"


@pytest.mark.parametrize("resolution", ["kept_unverified", "no_action"])
def test_the_other_resolutions_leave_the_case_alone(
    admin: Browser, db_session, resolution: str
) -> None:  # type: ignore[no-untyped-def]
    item = _in_review_item()
    body = admin.post(
        f"/api/admin/review-items/{item.id}/resolve",
        {"resolution": resolution, "note": "Looked at it and left it as is."},
    ).get_json()
    assert body["status"] == "resolved"
    assert body["case_status"] == "in_review"


def test_approving_a_case_that_is_not_in_review_is_refused_and_nothing_is_saved(
    admin: Browser, db_session
) -> None:  # type: ignore[no-untyped-def]
    item = ReviewItemFactory(
        case=CaseFactory(status=CaseStatus.LIVE), source=ReviewSource.FAMILY_REQUEST
    )
    response = admin.post(
        f"/api/admin/review-items/{item.id}/resolve",
        {"resolution": "approved_for_confirmation", "note": "Approve it please."},
    )
    assert (response.status_code, _error(response)["code"]) == (409, "case_not_in_review")
    db_session.refresh(item)
    assert item.status == ReviewStatus.OPEN


@pytest.mark.parametrize("note", ["", "   ", "too short", "x" * 501, None])
def test_a_resolution_needs_a_written_reason(admin: Browser, db_session, note: str | None) -> None:  # type: ignore[no-untyped-def]
    item = _in_review_item()
    response = admin.post(
        f"/api/admin/review-items/{item.id}/resolve", {"resolution": "no_action", "note": note}
    )
    assert response.status_code == 422
    db_session.refresh(item)
    assert item.status == ReviewStatus.OPEN


def test_an_item_can_only_be_resolved_once(admin: Browser) -> None:
    item = _in_review_item()
    body = {"resolution": "no_action", "note": "Nothing to do here, checked."}
    assert admin.post(f"/api/admin/review-items/{item.id}/resolve", body).status_code == 200
    again = admin.post(f"/api/admin/review-items/{item.id}/resolve", body)
    assert (again.status_code, _error(again)["code"]) == (409, "review_item_resolved")


def test_an_unknown_resolution_or_item_is_refused(admin: Browser) -> None:
    item = _in_review_item()
    assert (
        admin.post(
            f"/api/admin/review-items/{item.id}/resolve", {"resolution": "shrug", "note": "x" * 12}
        ).status_code
        == 422
    )
    missing = admin.post(
        f"/api/admin/review-items/{uuid.uuid4()}/resolve",
        {"resolution": "no_action", "note": "x" * 12},
    )
    assert (missing.status_code, _error(missing)["code"]) == (404, "review_item_not_found")


def test_resolving_is_audited_without_the_note(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    item = _in_review_item()
    admin.post(
        f"/api/admin/review-items/{item.id}/resolve",
        {"resolution": "kept_unverified", "note": "Private reasoning about the family."},
    )
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "review.resolved")
    ).scalar_one()
    assert entry.details == {
        "review_item_id": str(item.id),
        "resolution": "kept_unverified",
        "source": "ai_flag",
    }
    assert "Private reasoning" not in str(entry.details)


# --- Audit log ---------------------------------------------------------------------------------


def test_the_audit_log_is_newest_first_and_filterable(admin: Browser, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": "Suspected forged invoice"})
    admin.get(f"/api/admin/cases/{case.id}")
    everything = admin.get("/api/admin/audit-log").get_json()
    stamps = [e["created_at"] for e in everything["items"]]
    assert stamps == sorted(stamps, reverse=True)
    mine = admin.get(f"/api/admin/audit-log?target_type=case&target_id={case.id}").get_json()
    assert {e["action"] for e in mine["items"]} == {
        "case.transitioned",
        "case.freeze_signals",
        "admin.case_viewed",
    }
    assert all(e["target_id"] == str(case.id) for e in mine["items"])
    assert set(mine["items"][0]) == {
        "id", "created_at", "actor_type", "actor_user_id", "action", "target_type", "target_id",
        "details", "request_id",
    }  # fmt: skip
    assert admin.get("/api/admin/audit-log?target_type=ledger").get_json()["total"] == 0
    assert admin.get("/api/admin/audit-log?target_id=nope").status_code == 422


def test_audit_entries_carry_the_request_id_of_the_call_that_made_them(admin: Browser) -> None:
    case = CaseFactory()
    response = admin.get(f"/api/admin/cases/{case.id}")
    entries = admin.get(f"/api/admin/audit-log?target_id={case.id}").get_json()["items"]
    assert [e["request_id"] for e in entries] == [response.headers["X-Request-ID"]]


# --- The family's request for a review ---------------------------------------------------------


def test_a_family_can_ask_for_a_review_once(browser_for: BrowserFor, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.PENDING_CONFIRMATION)
    family = browser_for(case.created_by)
    response = family.post(
        f"/api/cases/{case.id}/review-request", {"message": "Please look at the total again."}
    )
    assert response.status_code == 201
    assert set(response.get_json()) == {"id", "status", "created_at"}
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert (item.source, item.status, item.payload) == (
        ReviewSource.FAMILY_REQUEST,
        ReviewStatus.OPEN,
        {"message": "Please look at the total again."},
    )
    again = family.post(
        f"/api/cases/{case.id}/review-request", {"message": "Please look again at it."}
    )
    assert (again.status_code, _error(again)["code"]) == (409, "review_already_requested")


def test_after_the_first_is_resolved_the_family_may_ask_again(
    browser_for: BrowserFor, db_session, admin: Browser
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    family = browser_for(case.created_by)
    item_id = family.post(
        f"/api/cases/{case.id}/review-request", {"message": "Something looks wrong here."}
    ).get_json()["id"]
    admin.post(
        f"/api/admin/review-items/{item_id}/resolve",
        {"resolution": "no_action", "note": "Explained to the family."},
    )
    assert (
        family.post(
            f"/api/cases/{case.id}/review-request", {"message": "I have another question."}
        ).status_code
        == 201
    )


@pytest.mark.parametrize("message", ["", "short", "x" * 501])
def test_the_request_needs_a_message_of_a_sensible_length(
    browser_for: BrowserFor, message: str
) -> None:
    case = CaseFactory(status=CaseStatus.LIVE)
    response = browser_for(case.created_by).post(
        f"/api/cases/{case.id}/review-request", {"message": message}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("status", [CaseStatus.DRAFT, CaseStatus.CANCELLED])
def test_a_review_needs_a_submitted_case_that_has_not_ended(
    browser_for: BrowserFor, status: CaseStatus
) -> None:
    case = CaseFactory(status=status)
    response = browser_for(case.created_by).post(
        f"/api/cases/{case.id}/review-request", {"message": "Please check my case."}
    )
    assert (response.status_code, _error(response)["code"]) == (409, "case_not_reviewable")


def test_someone_elses_case_cannot_be_reviewed_on_request(browser_for: BrowserFor) -> None:
    case = CaseFactory(status=CaseStatus.LIVE)
    response = browser_for(UserFactory()).post(
        f"/api/cases/{case.id}/review-request", {"message": "Please check this case."}
    )
    assert (response.status_code, _error(response)["code"]) == (404, "case_not_found")


def test_a_request_is_audited_and_appears_in_the_admin_queue(
    browser_for: BrowserFor, admin: Browser, db_session
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE, alias="Baby Zawadi")
    browser_for(case.created_by).post(
        f"/api/cases/{case.id}/review-request", {"message": "Please check this case."}
    )
    [item] = admin.get("/api/admin/review-items?status=open").get_json()["items"]
    assert (item["source"], item["case_alias"], item["payload"]) == (
        "family_request",
        "Baby Zawadi",
        {"message": "Please check this case."},
    )
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "review.requested")
    ).scalar_one()
    assert "Please check" not in str(entry.details)


# --- Public totals -----------------------------------------------------------------------------


def test_the_landing_page_totals_count_public_cases_only(client, db_session) -> None:  # type: ignore[no-untyped-def]
    public = CaseFactory(
        status=CaseStatus.LIVE,
        pledged_kes_minor=2_000_000,
        settled_kes_minor=500_000,
        donor_count=4,
    )
    ConsentFactory(case=public, scope=ConsentScope.PUBLIC_PAGE)
    hidden = CaseFactory(
        status=CaseStatus.LIVE, pledged_kes_minor=9_000_000, donor_count=9
    )  # no consent
    closed = CaseFactory(
        status=CaseStatus.CLOSED,
        close_reason=CloseReason.FULLY_SETTLED,
        pledged_kes_minor=1_000_000,
        settled_kes_minor=1_000_000,
        donor_count=2,
    )
    ConsentFactory(case=closed, scope=ConsentScope.PUBLIC_PAGE)
    CaseFactory(status=CaseStatus.DRAFT, pledged_kes_minor=7_000_000)
    body = client.get("/api/public/stats").get_json()
    assert body["public_cases"] == 2
    assert body["open_cases"] == 1
    assert body["pledged_kes_minor"] == 3_000_000
    assert body["settled_kes_minor"] == 1_500_000
    assert body["donations"] == 6
    assert body["hospitals"] >= 1
    assert hidden.pledged_kes_minor == 9_000_000


def test_the_landing_page_totals_on_an_empty_system(client, db_session) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/public/stats").get_json()
    assert {k: v for k, v in body.items() if k != "hospitals"} == {
        "public_cases": 0,
        "open_cases": 0,
        "pledged_kes_minor": 0,
        "settled_kes_minor": 0,
        "donations": 0,
    }


def test_the_routes_are_documented(client) -> None:  # type: ignore[no-untyped-def]
    paths = client.get("/api/openapi.json").get_json()["paths"]
    for path in (
        "/api/admin/stats",
        "/api/admin/review-items",
        "/api/admin/review-items/{item_id}/resolve",
        "/api/admin/cases",
        "/api/admin/cases/{case_id}",
        "/api/admin/cases/{case_id}/freeze",
        "/api/admin/audit-log",
        "/api/cases/{case_id}/review-request",
        "/api/public/stats",
    ):
        assert path in paths
    assert mail_service.outbox == []
