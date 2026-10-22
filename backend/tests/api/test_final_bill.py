"""The final bill: the family uploads it, the hospital confirms it, the case is paid and closes."""

import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.case import CaseDocument, ReviewItem
from app.models.enums import (
    CaseStatus,
    CloseReason,
    ConfirmationStatus,
    DocumentKind,
    DocumentStatus,
    SettlementStatus,
    TrustLevel,
)
from app.models.money import Settlement
from app.models.verification import Confirmation
from app.services import case_service, confirmation_service, document_service, mail_service
from tests import pdf_fixtures as fx
from tests.browser import Browser
from tests.donation_helpers import RATE, donate
from tests.factories import ConfirmationFactory, FxRateFactory
from tests.hospital_helpers import REGISTER_EMAIL, Ward, last_code_sent_to, make_ward

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


@pytest.fixture(autouse=True)
def _storage(s3: None) -> None:
    """Uploads go to the in-memory bucket."""


def live_ward(*, target_kes: int = 100_000, threshold_kes: int | None = None, **fields) -> Ward:  # type: ignore[no-untyped-def]
    """A hospital with a live case on a KES estimate that the hospital already confirmed."""
    FxRateFactory(rate=RATE)
    ward = make_ward(
        threshold_kes=threshold_kes,
        status=CaseStatus.LIVE,
        trust_level=TrustLevel.HOSPITAL_CONFIRMED,
        target_kes_minor=target_kes * 100,
        is_estimate=True,
        buffer_bps=500,
        live_at=datetime.now(UTC) - timedelta(days=2),
        **fields,
    )
    ward.hospital.paypal_payout_email = "payouts@hospital.test"
    ConfirmationFactory(
        case=ward.case,
        confirmed_by=ward.clerk,
        amount_kes_minor=target_kes * 100,
        is_final_bill=False,
        finalized_at=datetime.now(UTC) - timedelta(days=2),
    )
    return ward


def upload_final(browser: Browser, case, pages: int = 3):  # type: ignore[no-untyped-def]
    return browser.post(
        f"/api/cases/{case.id}/final-bill",
        data={"file": (io.BytesIO(fx.clean_pdf(pages=pages)), "final bill.pdf")},
    )


def decide(browser: Browser, ward: Ward, **overrides):  # type: ignore[no-untyped-def]
    sent = browser.post(f"/api/hospital/cases/{ward.case.id}/otp", {"purpose": "decision"})
    assert sent.status_code == 201, sent.get_json()
    body = {
        "request_id": sent.get_json()["request_id"],
        "otp": last_code_sent_to(REGISTER_EMAIL),
        "decision": "confirmed",
        "amount_kes": "100000.00",
        "invoice_number": "FINAL-2026-0001",
        "is_final_bill": True,
        **overrides,
    }
    return browser.post(f"/api/hospital/cases/{ward.case.id}/decision", body)


# --------------------------------------------------------------------------- the family uploads


def test_the_family_adds_a_final_bill_to_a_live_case(browser_for: BrowserFor, db_session) -> None:
    ward = live_ward()

    response = upload_final(browser_for(ward.case.created_by), ward.case)

    assert response.status_code == 201
    body = response.get_json()
    assert body["kind"] == "final_bill"
    assert body["status"] == "triaged"  # the inline queue ran the checks
    assert set(body) >= {"id", "kind", "status", "mime_type", "size_bytes"}
    assert "final bill.pdf" not in str(body)
    document = db_session.get(CaseDocument, body["id"])
    assert document.storage_key is not None
    assert confirmation_service.awaiting_final_bill(ward.case) is document


@pytest.mark.parametrize("status", [CaseStatus.FUNDED])
def test_a_funded_case_can_take_a_final_bill_too(
    browser_for: BrowserFor, db_session, status: CaseStatus
) -> None:
    ward = live_ward()
    ward.case.status = status
    db_session.flush()

    assert upload_final(browser_for(ward.case.created_by), ward.case).status_code == 201


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.DRAFT,
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.CANCELLED,
        CaseStatus.CLOSED,
    ],
)
def test_a_case_that_is_not_live_cannot_take_one(
    browser_for: BrowserFor, db_session, status: CaseStatus
) -> None:
    ward = live_ward()
    ward.case.status = status
    ward.case.close_reason = CloseReason.FULLY_SETTLED if status == CaseStatus.CLOSED else None
    db_session.flush()

    response = upload_final(browser_for(ward.case.created_by), ward.case)

    assert response.status_code == 409
    assert _error(response)["code"] == "case_not_live"


def test_a_case_whose_bill_is_already_final_needs_no_other(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    ward.case.is_estimate = False
    db_session.flush()

    response = upload_final(browser_for(ward.case.created_by), ward.case)

    assert response.status_code == 409
    assert _error(response)["code"] == "final_bill_not_needed"


def test_only_one_final_bill_is_checked_at_a_time(browser_for: BrowserFor, db_session) -> None:
    ward = live_ward()
    owner = browser_for(ward.case.created_by)
    assert upload_final(owner, ward.case, pages=2).status_code == 201

    again = upload_final(owner, ward.case, pages=3)

    assert again.status_code == 409
    assert _error(again)["code"] == "final_bill_pending"


def test_after_the_hospital_answers_another_final_bill_is_welcome(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    owner = browser_for(ward.case.created_by)
    upload_final(owner, ward.case, pages=2)
    assert decide(browser_for(ward.clerk.user), ward, decision="queried").status_code == 200

    assert upload_final(owner, ward.case, pages=3).status_code == 201


def test_a_final_bill_whose_checking_failed_can_be_replaced(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    owner = browser_for(ward.case.created_by)
    first = upload_final(owner, ward.case, pages=2).get_json()
    db_session.get(CaseDocument, first["id"]).status = DocumentStatus.FAILED
    db_session.flush()

    assert upload_final(owner, ward.case, pages=3).status_code == 201


def test_the_same_file_twice_is_refused(browser_for: BrowserFor, db_session) -> None:
    ward = live_ward()
    owner = browser_for(ward.case.created_by)
    upload_final(owner, ward.case, pages=2)
    db_session.get(
        CaseDocument,
        db_session.execute(
            select(CaseDocument.id).where(CaseDocument.kind == DocumentKind.FINAL_BILL)
        ).scalar_one(),
    ).status = DocumentStatus.FAILED
    db_session.flush()

    response = upload_final(owner, ward.case, pages=2)

    assert response.status_code == 409
    assert _error(response)["code"] == "document_already_uploaded"


def test_a_file_that_is_not_a_bill_is_refused(browser_for: BrowserFor, db_session) -> None:
    ward = live_ward()

    response = browser_for(ward.case.created_by).post(
        f"/api/cases/{ward.case.id}/final-bill",
        data={"file": (io.BytesIO(fx.gif()), "final.gif")},
    )

    assert response.status_code == 415


def test_only_the_owning_family_can_add_one(browser_for: BrowserFor, db_session) -> None:
    from tests.factories import UserFactory

    ward = live_ward()

    assert upload_final(browser_for(UserFactory()), ward.case).status_code == 404
    assert upload_final(browser_for(ward.clerk.user), ward.case).status_code == 403


def test_an_anonymous_visitor_cannot_add_one(browser: Browser, db_session) -> None:
    ward = live_ward()

    assert upload_final(browser, ward.case).status_code == 401


# --------------------------------------------------------------------------- the hospital decides


def test_the_hospital_queue_lists_a_final_bill_waiting_on_a_live_case(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    clerk = browser_for(ward.clerk.user)
    assert clerk.get("/api/hospital/queue").get_json()["pending"] == []

    upload_final(browser_for(ward.case.created_by), ward.case)

    [item] = clerk.get("/api/hospital/queue").get_json()["pending"]
    assert (item["id"], item["kind"]) == (str(ward.case.id), "final_bill")


def test_a_live_case_without_a_final_bill_has_nothing_to_decide(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    clerk = browser_for(ward.clerk.user)

    response = clerk.post(f"/api/hospital/cases/{ward.case.id}/otp", {"purpose": "decision"})

    assert response.status_code == 409
    assert _error(response)["code"] == "case_not_awaiting_confirmation"


def test_confirming_the_final_bill_makes_the_amount_final_and_keeps_the_case_live(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    upload_final(browser_for(ward.case.created_by), ward.case)

    response = decide(browser_for(ward.clerk.user), ward, amount_kes="90000.00")

    assert response.status_code == 200
    assert response.get_json()["case_status"] == "live"
    assert response.get_json()["confirmation"]["is_final_bill"] is True
    db_session.refresh(ward.case)
    assert ward.case.target_kes_minor == 9_000_000
    assert ward.case.is_estimate is False
    assert ward.case.usd_goal_minor == 73_256  # KES 90,000 plus 5 percent, at 129, rounded up
    assert confirmation_service.awaiting_final_bill(ward.case) is None


def test_the_form_cannot_unmark_a_final_bill(browser_for: BrowserFor, db_session) -> None:
    ward = live_ward()
    upload_final(browser_for(ward.case.created_by), ward.case)

    decide(browser_for(ward.clerk.user), ward, is_final_bill=False)

    db_session.refresh(ward.case)
    assert ward.case.is_estimate is False


def test_a_larger_final_bill_reopens_a_funded_case_and_it_takes_donations_again(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    ward = live_ward(target_kes=1_000)  # estimate KES 1,000, goal KES 1,050
    donate(db_session, ward.case, "10.00")  # nets KES 1,214: funded
    assert ward.case.status == CaseStatus.FUNDED
    upload_final(browser_for(ward.case.created_by), ward.case)

    response = decide(browser_for(ward.clerk.user), ward, amount_kes="5000.00")

    assert response.status_code == 200
    db_session.refresh(ward.case)
    assert ward.case.status == CaseStatus.LIVE
    assert case_service.is_accepting_donations(ward.case)
    assert ward.case.target_kes_minor == 500_000


def test_a_smaller_final_bill_can_fund_a_live_case(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    ward = live_ward(target_kes=10_000)  # estimate KES 10,000
    donate(db_session, ward.case, "50.00")  # nets KES 6,224: live
    assert ward.case.status == CaseStatus.LIVE
    upload_final(browser_for(ward.case.created_by), ward.case)

    decide(browser_for(ward.clerk.user), ward, amount_kes="5500.00")  # goal KES 5,775

    db_session.refresh(ward.case)
    assert ward.case.status == CaseStatus.FUNDED


def test_confirming_the_final_bill_queues_a_settlement_check(
    browser_for: BrowserFor, db_session, manual_settlement
) -> None:
    ward = live_ward()
    upload_final(browser_for(ward.case.created_by), ward.case)
    manual_settlement.clear()

    decide(browser_for(ward.clerk.user), ward)

    assert manual_settlement == [("app.jobs.tasks.run_settlement_check", str(ward.case.id))]


def test_querying_the_final_bill_keeps_the_case_live_and_asks_an_admin_and_the_family(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    upload_final(browser_for(ward.case.created_by), ward.case)
    mail_service.outbox.clear()

    response = decide(
        browser_for(ward.clerk.user), ward, decision="queried", note="Total looks wrong"
    )

    assert response.status_code == 200
    assert response.get_json()["case_status"] == "live"
    db_session.refresh(ward.case)
    assert ward.case.status == CaseStatus.LIVE
    assert ward.case.is_estimate is True
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.payload["origin"] == "final_bill_query"
    assert "Total looks wrong" in item.reason
    assert [m.template for m in mail_service.outbox if m.to == ward.case.created_by.email] == [
        "case_queried"
    ]


def test_rejecting_the_final_bill_does_not_cancel_a_live_case(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    upload_final(browser_for(ward.case.created_by), ward.case)

    decide(browser_for(ward.clerk.user), ward, decision="rejected")

    db_session.refresh(ward.case)
    assert ward.case.status == CaseStatus.LIVE
    assert db_session.execute(select(ReviewItem)).scalar_one().payload["origin"] == (
        "final_bill_rejected"
    )


def test_a_final_bill_above_the_threshold_waits_for_a_second_supervisor(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward(threshold_kes=50_000)
    upload_final(browser_for(ward.case.created_by), ward.case)
    clerk = browser_for(ward.clerk.user)

    decided = decide(clerk, ward, amount_kes="120000.00")

    assert decided.get_json()["confirmation"]["status"] == "awaiting_second_approval"
    db_session.refresh(ward.case)
    assert ward.case.is_estimate is True  # not final until the second approval
    queue = clerk.get("/api/hospital/queue").get_json()
    assert queue["pending"] == []
    [waiting] = queue["awaiting_second_approval"]

    supervisor = browser_for(ward.supervisor.user)
    sent = supervisor.post(
        f"/api/hospital/cases/{ward.case.id}/otp", {"purpose": "second_approval"}
    )
    assert sent.status_code == 201
    approved = supervisor.post(
        f"/api/hospital/confirmations/{waiting['id']}/approve",
        {"request_id": sent.get_json()["request_id"], "otp": last_code_sent_to(REGISTER_EMAIL)},
    )

    assert approved.status_code == 200
    db_session.refresh(ward.case)
    assert ward.case.is_estimate is False
    assert ward.case.target_kes_minor == 12_000_000


def test_the_same_person_cannot_give_the_second_approval_of_a_final_bill(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward(threshold_kes=50_000)
    upload_final(browser_for(ward.case.created_by), ward.case)
    decide(browser_for(ward.supervisor.user), ward, amount_kes="120000.00")

    sent = browser_for(ward.supervisor.user).post(
        f"/api/hospital/cases/{ward.case.id}/otp", {"purpose": "second_approval"}
    )

    assert sent.status_code == 403
    assert _error(sent)["code"] == "same_person_cannot_approve"


# --------------------------------------------------------------------------- paying up and closing


def run_settlements(session, case) -> None:  # type: ignore[no-untyped-def]
    """Drain what a worker would do: send every pending tranche, poll every open one."""
    from app.services import settlement_service

    for _ in range(4):
        for s in session.execute(select(Settlement).where(Settlement.case_id == case.id)).scalars():
            if s.status == SettlementStatus.PENDING:
                settlement_service.execute_settlement(session, s.id)
            if s.status == SettlementStatus.PROCESSING:
                settlement_service.poll_settlement(session, s.id)
        settlement_service.run_settlement_check(session, case.id)


def test_estimate_to_final_bill_to_closed_case_end_to_end(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    ward = live_ward(target_kes=100_000)
    case = ward.case
    for _ in range(5):  # KES 125,000 net: more than the goal, so the case becomes funded
        donate(db_session, case, "200.00")
        if case.status == CaseStatus.FUNDED:
            break
    run_settlements(db_session, case)
    assert case.settled_kes_minor == 8_000_000  # the 80 % estimate cap, no more
    assert case.status == CaseStatus.FUNDED
    upload_final(browser_for(case.created_by), case)
    mail_service.outbox.clear()

    decide(browser_for(ward.clerk.user), ward, amount_kes="100000.00")
    run_settlements(db_session, case)

    db_session.refresh(case)
    assert case.settled_kes_minor == 10_000_000
    assert case.status == CaseStatus.CLOSED
    assert case.close_reason == CloseReason.FULLY_SETTLED
    assert [m.to for m in mail_service.outbox if m.template == "case_closed"] == [
        case.created_by.email
    ]


def test_a_final_bill_below_what_is_already_paid_closes_the_case_and_asks_an_admin(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    ward = live_ward(target_kes=100_000)
    case = ward.case
    donate(db_session, case, "1000.00")  # one large gift: far over the goal
    run_settlements(db_session, case)
    assert case.settled_kes_minor == 8_000_000
    upload_final(browser_for(case.created_by), case)
    mail_service.outbox.clear()

    decide(browser_for(ward.clerk.user), ward, amount_kes="70000.00")  # below the KES 80,000 paid

    db_session.refresh(case)
    assert case.status == CaseStatus.CLOSED
    assert case.close_reason == CloseReason.FINAL_BILL_LOWER
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.payload["origin"] == "final_bill_below_settled"
    assert (item.payload["settled_kes_minor"], item.payload["target_kes_minor"]) == (
        8_000_000,
        7_000_000,
    )
    closed = [m for m in mail_service.outbox if m.template == "case_closed"]
    assert len(closed) == 1
    assert "lower than the first estimate" in closed[0].text


def test_a_final_bill_equal_to_the_estimate_pays_the_rest_without_waiting_for_more_gifts(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    ward = live_ward(target_kes=100_000)
    case = ward.case
    donate(db_session, case, "1000.00")
    run_settlements(db_session, case)
    upload_final(browser_for(case.created_by), case)

    decide(browser_for(ward.clerk.user), ward, amount_kes="100000.00")
    run_settlements(db_session, case)

    db_session.refresh(case)
    tranches = (
        db_session.execute(
            select(Settlement).where(Settlement.case_id == case.id).order_by(Settlement.tranche_no)
        )
        .scalars()
        .all()
    )
    assert [t.amount_kes_minor for t in tranches] == [8_000_000, 2_000_000]
    assert case.status == CaseStatus.CLOSED


# --------------------------------------------------------------------------- housekeeping


def test_the_triage_sweep_picks_up_a_final_bill_that_was_never_queued(
    browser_for: BrowserFor, db_session, monkeypatch
) -> None:
    ward = live_ward()
    monkeypatch.setattr(document_service, "enqueue_triage", lambda docs: 0)
    created = upload_final(browser_for(ward.case.created_by), ward.case).get_json()
    document = db_session.get(CaseDocument, created["id"])
    assert document.status == DocumentStatus.UPLOADED
    document.created_at = datetime.now(UTC) - timedelta(minutes=10)
    db_session.flush()

    waiting = document_service.documents_waiting_for_triage(db_session)

    assert [d.id for d in waiting] == [document.id]


def test_a_final_bill_file_is_kept_until_the_hospital_has_decided_on_it(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    for confirmation in ward.case.confirmations:
        confirmation.finalized_at = datetime.now(UTC) - timedelta(days=2)
    created = upload_final(browser_for(ward.case.created_by), ward.case).get_json()
    document = db_session.get(CaseDocument, created["id"])
    first_bill = next(d for d in ward.case.documents if d.kind != DocumentKind.FINAL_BILL)
    first_bill.storage_key = document.storage_key + ".first"  # a stand-in file for the first bill
    first_bill.created_at = datetime.now(UTC) - timedelta(days=5)
    db_session.flush()

    document_service.sweep_raw_documents(db_session)

    assert document.storage_key is not None  # the final bill waits for its own confirmation
    assert document.raw_deleted_at is None


def test_a_final_bill_file_goes_a_day_after_its_own_confirmation(
    browser_for: BrowserFor, db_session
) -> None:
    ward = live_ward()
    created = upload_final(browser_for(ward.case.created_by), ward.case).get_json()
    decide(browser_for(ward.clerk.user), ward, amount_kes="100000.00")
    document = db_session.get(CaseDocument, created["id"])
    final = db_session.execute(
        select(Confirmation).where(Confirmation.document_id == document.id)
    ).scalar_one()
    assert final.status == ConfirmationStatus.CONFIRMED
    final.finalized_at = datetime.now(UTC) - timedelta(hours=25)
    document.created_at = datetime.now(UTC) - timedelta(days=3)  # uploaded before it was decided
    db_session.flush()

    document_service.sweep_raw_documents(db_session)

    assert document.storage_key is None
    assert document.raw_deleted_at is not None
