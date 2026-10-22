"""Hospital invoicing: issuing, recording tranches, replacing, reading back, and the sweep."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models.case import ReviewItem
from app.models.enums import ConfirmationStatus, SettlementStatus
from app.models.money import Settlement
from app.models.platform import AuditLog
from app.payments.errors import PayPalError
from app.payments.paypal_mock import MockPayPal
from app.services import invoicing_service as invoicing
from tests.donation_helpers import calls
from tests.factories import (
    CaseFactory,
    ConfirmationFactory,
    FxRateFactory,
    SettlementFactory,
)


def made(factory, **fields):  # type: ignore[no-untyped-def]
    """Build a row and commit it, as the service sees it in a job (the session is fresh)."""
    row = factory(**fields)
    db.session.commit()
    return row


@pytest.fixture(autouse=True)
def rate(db_session):  # type: ignore[no-untyped-def]
    return made(FxRateFactory, rate=Decimal("129.00000000"))


@pytest.fixture
def credentials(app, monkeypatch):  # type: ignore[no-untyped-def]
    """Give a case's hospital PayPal credentials, so invoicing is switched on for it."""

    def give(case) -> None:  # type: ignore[no-untyped-def]
        code = case.hospital.register_entry.register_code
        monkeypatch.setitem(
            app.config,
            "HOSPITAL_PAYPAL_CREDENTIALS",
            {code: {"client_id": "id", "client_secret": "secret"}},
        )

    return give


@pytest.fixture
def bill(credentials):  # type: ignore[no-untyped-def]
    """A confirmed bill of KES 85,000 at a hospital that has credentials."""
    case = made(CaseFactory)
    credentials(case)
    return made(
        ConfirmationFactory, case=case, amount_kes_minor=8_500_000, invoice_number="INV-2026-0412"
    )


def paid_tranche(confirmation, number: int, kes_minor: int, **kw):  # type: ignore[no-untyped-def]
    return made(
        SettlementFactory,
        case=confirmation.case,
        confirmation=confirmation,
        tranche_no=number,
        amount_kes_minor=kes_minor,
        amount_usd_minor=kes_minor // 129,
        status=SettlementStatus.SUCCEEDED,
        completed_at=datetime(2026, 10, 24, 9, 0, tzinfo=UTC),
        **kw,
    )


def reviews(session, case):  # type: ignore[no-untyped-def]
    return session.scalars(select(ReviewItem).where(ReviewItem.case_id == case.id)).all()


# --------------------------------------------------------------------------- issuing


def test_a_confirmed_bill_becomes_an_invoice_that_is_sent(
    db_session, paypal: MockPayPal, bill
) -> None:  # type: ignore[no-untyped-def]
    assert invoicing.issue(db_session, bill.id) == "issued"

    db_session.refresh(bill)
    assert bill.paypal_invoice_id.startswith("INV2-")
    assert bill.invoice_status == "SENT"
    assert bill.invoice_usd_minor == 65_891  # 85,000 / 129 = 658.91
    invoice = paypal.get_invoice(bill.paypal_invoice_id)
    assert invoice["status"] == "SENT"
    assert invoice["items"][0]["name"] == "Medical bill INV-2026-0412"
    assert invoice["items"][0]["unit_amount"] == {"currency_code": "USD", "value": "658.91"}
    assert invoice["detail"]["note"] == "KES 85,000.00 bill confirmed by billing desk"
    assert invoice["detail"]["invoice_number"] == invoicing.invoice_number_for(bill)
    recipient = invoice["primary_recipients"][0]["billing_info"]["email_address"]
    assert recipient == "collector@tibadirect.test"


def test_nothing_about_the_patient_or_the_case_is_in_the_invoice(
    db_session, paypal: MockPayPal, bill
) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    db_session.refresh(bill)

    text = repr(paypal.get_invoice(bill.paypal_invoice_id))

    assert bill.case.alias not in text
    assert str(bill.case_id) not in text
    assert "diagnosis" not in text.lower()


def test_issuing_twice_makes_one_invoice(db_session, paypal: MockPayPal, bill, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    created = calls(paypal, "create_invoice", monkeypatch)
    sends = calls(paypal, "send_invoice", monkeypatch)

    invoicing.issue(db_session, bill.id)
    invoicing.issue(db_session, bill.id)

    assert len(created) == 1
    assert len(sends) == 1


def test_issuing_is_audited(db_session, bill) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id, request_id="req-1")

    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "invoice.issued")).one()
    assert row.details["case_id"] == str(bill.case_id)
    assert row.details["invoice_usd_minor"] == 65_891
    assert row.request_id == "req-1"


def test_a_hospital_without_credentials_gets_no_invoice(db_session, paypal: MockPayPal) -> None:
    confirmation = made(
        ConfirmationFactory,
    )

    assert invoicing.issue(db_session, confirmation.id) == "not_configured"

    db_session.refresh(confirmation)
    assert confirmation.paypal_invoice_id is None
    assert confirmation.invoice_status is None


def test_without_the_collector_email_invoicing_is_off(db_session, bill, app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(app.config, "PAYPAL_COLLECTOR_EMAIL", "")

    assert invoicing.issue(db_session, bill.id) == "not_configured"


def test_only_a_confirmed_bill_is_invoiced(db_session, credentials) -> None:  # type: ignore[no-untyped-def]
    case = made(CaseFactory)
    credentials(case)
    queried = made(ConfirmationFactory, case=case, status=ConfirmationStatus.QUERIED)

    assert invoicing.issue(db_session, queried.id) == "not_confirmed"
    assert invoicing.issue(db_session, uuid.uuid4()) == "missing"


def test_if_sending_fails_the_invoice_is_kept_and_the_retry_sends_it(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, bill, monkeypatch
) -> None:
    created = calls(paypal, "create_invoice", monkeypatch)
    paypal.fail_next("send_invoice", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError):
        invoicing.issue(db_session, bill.id)
    db_session.refresh(bill)
    assert bill.paypal_invoice_id is not None
    assert bill.invoice_status == "DRAFT"

    assert invoicing.issue(db_session, bill.id) == "issued"
    db_session.refresh(bill)
    assert bill.invoice_status == "SENT"
    assert len(created) == 1  # the second run sent the first run's invoice


def test_an_outage_creating_the_invoice_is_retried_not_recorded_as_failure(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, bill
) -> None:
    paypal.fail_next("create_invoice", http_status=500, name="INTERNAL_SERVER_ERROR")

    with pytest.raises(PayPalError):
        invoicing.issue(db_session, bill.id)

    db_session.refresh(bill)
    assert bill.invoice_status is None
    assert reviews(db_session, bill.case) == []


def test_a_refusal_marks_the_invoice_failed_and_asks_a_person_to_look(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, bill
) -> None:
    paypal.fail_next("create_invoice", http_status=422, issue="DUPLICATE_INVOICE_NUMBER")

    assert invoicing.issue(db_session, bill.id) == "failed"

    db_session.refresh(bill)
    assert bill.invoice_status == invoicing.FAILED
    item = reviews(db_session, bill.case)[0]
    assert item.payload["origin"] == "invoice_failed"
    assert item.payload["paypal_issue"] == "DUPLICATE_INVOICE_NUMBER"
    assert invoicing.issue(db_session, bill.id) == "already_done"  # not retried by itself
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "invoice.failed")).one()


def test_a_replaced_invoice_nobody_paid_on_is_cancelled(
    db_session, paypal: MockPayPal, credentials
) -> None:  # type: ignore[no-untyped-def]
    case = made(CaseFactory)
    credentials(case)
    first = made(ConfirmationFactory, case=case, amount_kes_minor=5_000_000)
    invoicing.issue(db_session, first.id)
    final = made(ConfirmationFactory, case=case, amount_kes_minor=7_000_000, is_final_bill=True)

    assert invoicing.issue(db_session, final.id) == "issued"

    db_session.refresh(first)
    db_session.refresh(final)
    assert first.invoice_status == "CANCELLED"
    assert paypal.get_invoice(first.paypal_invoice_id)["status"] == "CANCELLED"
    assert final.invoice_status == "SENT"


def test_two_confirmations_finalised_at_the_same_moment_never_cancel_each_other(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, credentials
) -> None:
    case = made(CaseFactory)
    credentials(case)
    moment = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
    one = made(ConfirmationFactory, case=case, finalized_at=moment)
    two = made(ConfirmationFactory, case=case, finalized_at=moment)

    invoicing.issue(db_session, one.id)
    invoicing.issue(db_session, two.id)

    db_session.refresh(one)
    db_session.refresh(two)
    assert (one.invoice_status, two.invoice_status) == ("SENT", "SENT")


def test_a_replaced_invoice_with_a_payment_on_it_is_left_to_the_hospital(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, credentials
) -> None:
    case = made(CaseFactory)
    credentials(case)
    first = made(ConfirmationFactory, case=case, amount_kes_minor=5_000_000)
    invoicing.issue(db_session, first.id)
    paid_tranche(first, 1, 2_000_000)
    invoicing.record_payment(db_session, db_session.scalars(select(Settlement.id)).one())
    final = made(ConfirmationFactory, case=case, amount_kes_minor=7_000_000, is_final_bill=True)

    invoicing.issue(db_session, final.id)

    db_session.refresh(first)
    assert first.invoice_status == "PARTIALLY_PAID"
    assert paypal.get_invoice(first.paypal_invoice_id)["status"] == "PARTIALLY_PAID"


# --------------------------------------------------------------------------- tranche payments


def test_the_parts_of_an_invoice_add_up_to_it_whatever_the_rate_did(db_session, bill) -> None:  # type: ignore[no-untyped-def]
    bill.invoice_usd_minor = 65_891
    parts = [paid_tranche(bill, 1, 2_125_000), paid_tranche(bill, 2, 2_125_000)]
    parts.append(paid_tranche(bill, 3, 4_250_000))

    shares = [invoicing.payment_share(bill, s) for s in parts]

    assert shares == [16_472, 16_473, 32_946]
    assert sum(shares) == 65_891


def test_a_part_does_not_depend_on_which_tranche_is_recorded_first(db_session, bill) -> None:  # type: ignore[no-untyped-def]
    bill.invoice_usd_minor = 10_001
    first, second, third = (paid_tranche(bill, n, 2_833_333) for n in (1, 2, 3))
    third.amount_kes_minor = 8_500_000 - 2 * 2_833_333

    a = [invoicing.payment_share(bill, s) for s in (third, first, second)]

    assert sum(a) == 10_001


def test_a_failed_tranche_does_not_count_in_the_shares(db_session, bill) -> None:  # type: ignore[no-untyped-def]
    bill.invoice_usd_minor = 60_000
    failed = paid_tranche(bill, 1, 4_250_000)
    failed.status = SettlementStatus.FAILED
    second = paid_tranche(bill, 2, 4_250_000)

    assert invoicing.payment_share(bill, second) == 30_000


def test_a_paid_tranche_is_recorded_on_the_invoice(db_session, paypal: MockPayPal, bill) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    tranche = paid_tranche(bill, 1, 2_125_000)

    assert invoicing.record_payment(db_session, tranche.id) == "recorded"

    db_session.refresh(tranche)
    db_session.refresh(bill)
    assert tranche.invoice_payment_id.startswith("MOCKINVPAY")
    assert bill.invoice_status == "PARTIALLY_PAID"
    payment = paypal.get_invoice(bill.paypal_invoice_id)["payments"]["transactions"][0]
    assert payment["amount"]["value"] == "164.72"
    assert payment["method"] == "BANK_TRANSFER"
    assert payment["payment_date"] == "2026-10-24"
    assert "Tranche 1" in payment["note"]
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "invoice.payment_recorded")
    ).one()


def test_the_last_tranche_pays_the_invoice_off(db_session, paypal: MockPayPal, bill) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    first = paid_tranche(bill, 1, 4_250_000)
    last = paid_tranche(bill, 2, 4_250_000)

    invoicing.record_payment(db_session, first.id)
    invoicing.record_payment(db_session, last.id)

    db_session.refresh(bill)
    assert bill.invoice_status == "PAID"
    assert paypal.get_invoice(bill.paypal_invoice_id)["due_amount"]["value"] == "0.00"


def test_a_tranche_is_never_recorded_twice(
    db_session, paypal: MockPayPal, bill, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    tranche = paid_tranche(bill, 1, 2_125_000)
    recorded = calls(paypal, "record_invoice_payment", monkeypatch)

    assert invoicing.record_payment(db_session, tranche.id) == "recorded"
    assert invoicing.record_payment(db_session, tranche.id) == "already_recorded"

    assert len(recorded) == 1


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("unpaid", "not_paid"),
        ("no_invoice", "no_invoice"),
        ("draft", "invoice_not_sent"),
    ],
)
def test_a_tranche_waits_until_there_is_a_sent_invoice_to_record_it_on(  # type: ignore[no-untyped-def]
    db_session, bill, setup, expected
) -> None:
    tranche = paid_tranche(bill, 1, 2_125_000)
    if setup == "unpaid":
        tranche.status = SettlementStatus.PROCESSING
    elif setup == "draft":
        bill.paypal_invoice_id, bill.invoice_status = "INV2-X", "DRAFT"

    assert invoicing.record_payment(db_session, tranche.id) == expected

    db_session.refresh(tranche)
    assert tranche.invoice_payment_id is None  # still to be done, the sweep will come back


def test_a_tranche_on_a_cancelled_invoice_is_marked_skipped_not_retried(db_session, bill) -> None:  # type: ignore[no-untyped-def]
    bill.paypal_invoice_id, bill.invoice_status = "INV2-X", "CANCELLED"
    tranche = paid_tranche(bill, 1, 2_125_000)

    assert invoicing.record_payment(db_session, tranche.id) == "invoice_closed"

    db_session.refresh(tranche)
    assert tranche.invoice_payment_id == invoicing.SKIPPED


def test_when_paypal_refuses_a_payment_a_person_is_asked_and_it_is_not_retried(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, bill
) -> None:
    invoicing.issue(db_session, bill.id)
    tranche = paid_tranche(bill, 1, 2_125_000)
    paypal.fail_next("record_invoice_payment", http_status=422, issue="INVALID_INVOICE_STATUS")

    assert invoicing.record_payment(db_session, tranche.id) == "failed"

    db_session.refresh(tranche)
    assert tranche.invoice_payment_id == invoicing.FAILED
    assert reviews(db_session, bill.case)[0].payload["origin"] == "invoice_payment_failed"
    assert invoicing.record_payment(db_session, tranche.id) == "already_recorded"


def test_an_outage_recording_a_payment_is_retried(db_session, paypal: MockPayPal, bill) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    tranche = paid_tranche(bill, 1, 2_125_000)
    paypal.fail_next("record_invoice_payment", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError):
        invoicing.record_payment(db_session, tranche.id)

    db_session.refresh(tranche)
    assert tranche.invoice_payment_id is None
    assert invoicing.record_payment(db_session, tranche.id) == "recorded"


def test_if_the_status_cannot_be_read_back_the_payment_is_still_kept(
    db_session, paypal: MockPayPal, bill
) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    tranche = paid_tranche(bill, 1, 2_125_000)
    paypal.fail_next("get_invoice", http_status=422, issue="SOMETHING")

    assert invoicing.record_payment(db_session, tranche.id) == "recorded"

    db_session.refresh(tranche)
    db_session.refresh(bill)
    assert tranche.invoice_payment_id.startswith("MOCKINVPAY")
    assert bill.invoice_status == "SENT"  # the next read fixes it
    assert invoicing.refresh_status(db_session, bill.id) == "PARTIALLY_PAID"


# --------------------------------------------------------------------------- status


def test_refreshing_reads_the_status_from_paypal(db_session, paypal: MockPayPal, bill) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    db_session.refresh(bill)
    paypal.cancel_invoice(bill.paypal_invoice_id, note="by the hospital", request_id="x")

    assert invoicing.refresh_status(db_session, bill.id) == "CANCELLED"
    assert invoicing.refresh_status(db_session, uuid.uuid4()) is None


def test_a_reported_status_is_kept_and_audited(db_session, bill) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    db_session.refresh(bill)

    assert invoicing.apply_reported_status(db_session, bill.paypal_invoice_id, "PAID") is True
    db_session.flush()

    assert bill.invoice_status == "PAID"
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "invoice.status_changed")
    ).one()
    assert invoicing.apply_reported_status(db_session, "INV2-NOBODY", "PAID") is False


# --------------------------------------------------------------------------- queueing and the sweep


def test_jobs_are_queued_only_for_a_hospital_with_credentials(
    db_session, bill, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    queued: list[tuple] = []
    monkeypatch.setattr(invoicing, "_enqueue", lambda func, arg: queued.append((func, arg)))
    tranche = paid_tranche(bill, 1, 2_125_000)
    other = made(
        ConfirmationFactory,
    )

    invoicing.enqueue_issue(bill)
    invoicing.enqueue_payment(tranche)
    invoicing.enqueue_issue(other)

    assert queued == [
        ("app.jobs.tasks.issue_invoice", str(bill.id)),
        ("app.jobs.tasks.record_invoice_payment", str(tranche.id)),
    ]


def test_the_sweep_does_what_was_missed(db_session, paypal: MockPayPal, bill) -> None:  # type: ignore[no-untyped-def]
    tranche = paid_tranche(bill, 1, 2_125_000)

    first = invoicing.sweep(db_session)

    db_session.refresh(bill)
    db_session.refresh(tranche)
    assert first >= 2  # the invoice, then the tranche on it
    assert bill.invoice_status == "PARTIALLY_PAID"
    assert tranche.invoice_payment_id is not None
    assert len(paypal.get_invoice(bill.paypal_invoice_id)["payments"]["transactions"]) == 1


def test_the_sweep_finishes_an_invoice_that_was_only_drafted(
    db_session, paypal: MockPayPal, bill
) -> None:  # type: ignore[no-untyped-def]
    paypal.fail_next("send_invoice", http_status=503, name="SERVICE_UNAVAILABLE")
    with pytest.raises(PayPalError):
        invoicing.issue(db_session, bill.id)

    invoicing.sweep(db_session)

    db_session.refresh(bill)
    assert bill.invoice_status == "SENT"


def test_the_sweep_notices_a_status_changed_in_paypal(db_session, paypal: MockPayPal, bill) -> None:  # type: ignore[no-untyped-def]
    invoicing.issue(db_session, bill.id)
    db_session.refresh(bill)
    paypal.cancel_invoice(bill.paypal_invoice_id, note="x", request_id="x")

    invoicing.sweep(db_session)

    db_session.refresh(bill)
    assert bill.invoice_status == "CANCELLED"


def test_the_sweep_leaves_other_hospitals_and_failed_invoices_alone(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, bill, monkeypatch
) -> None:
    other = made(
        ConfirmationFactory,
    )
    broken = made(ConfirmationFactory, case=bill.case)
    broken.invoice_status = invoicing.FAILED
    created = calls(paypal, "create_invoice", monkeypatch)

    invoicing.sweep(db_session)

    db_session.refresh(other)
    assert other.paypal_invoice_id is None
    assert len(created) == 1  # only the first bill's, not the failed one's


def test_one_bad_invoice_does_not_stop_the_sweep(db_session, paypal: MockPayPal, bill) -> None:  # type: ignore[no-untyped-def]
    second = made(ConfirmationFactory, case=bill.case, amount_kes_minor=1_000_000)
    paypal.fail_next("create_invoice", http_status=503, name="SERVICE_UNAVAILABLE")

    done = invoicing.sweep(db_session)

    db_session.refresh(bill)
    db_session.refresh(second)
    assert done == 2  # one bill hit the outage; the other was invoiced and its status read
    assert [bill.invoice_status, second.invoice_status].count("SENT") == 1
    assert [bill.invoice_status, second.invoice_status].count(None) == 1


def test_with_no_credentials_anywhere_the_sweep_does_nothing(db_session) -> None:
    made(
        ConfirmationFactory,
    )

    assert invoicing.sweep(db_session) == 0
