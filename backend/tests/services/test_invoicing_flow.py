"""Invoicing alongside real settlement: tranches reach the invoice through the inline queue."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models.enums import CaseStatus, SettlementStatus
from app.models.money import Settlement
from app.payments.paypal_mock import MockPayPal
from app.services import invoicing_service as invoicing
from app.services import settlement_service
from tests.donation_helpers import donate, settleable_case


@pytest.fixture
def hospital_with_credentials(app, monkeypatch):  # type: ignore[no-untyped-def]
    def give(case) -> None:  # type: ignore[no-untyped-def]
        code = case.hospital.register_entry.register_code
        monkeypatch.setitem(
            app.config,
            "HOSPITAL_PAYPAL_CREDENTIALS",
            {code: {"client_id": "id", "client_secret": "secret"}},
        )

    return give


def pay_out_what_is_open(session) -> None:  # type: ignore[no-untyped-def]
    for settlement in session.execute(
        select(Settlement).where(Settlement.status == SettlementStatus.PROCESSING)
    ).scalars():
        settlement_service.poll_settlement(session, settlement.id)


def test_every_tranche_lands_on_the_invoice_and_the_invoice_ends_paid(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, hospital_with_credentials
) -> None:
    case = settleable_case(target_kes_minor=10_000_000)  # KES 100,000, a final bill
    hospital_with_credentials(case)
    confirmation = settlement_service.latest_confirmation(case)
    assert invoicing.issue(db_session, confirmation.id) == "issued"
    db_session.refresh(confirmation)

    gifts = 0
    while case.status != CaseStatus.CLOSED:
        donate(db_session, case, "200.00")
        pay_out_what_is_open(db_session)
        gifts += 1
        assert gifts < 12

    db_session.refresh(confirmation)
    invoice = paypal.get_invoice(confirmation.paypal_invoice_id)
    settlements = db_session.scalars(select(Settlement).where(Settlement.case_id == case.id)).all()
    payments = invoice["payments"]["transactions"]
    assert len(settlements) >= 2
    assert all(s.invoice_payment_id for s in settlements)
    assert len(payments) == len(settlements)
    assert invoice["status"] == "PAID"
    assert confirmation.invoice_status == "PAID"
    assert sum(Decimal(p["amount"]["value"]) for p in payments) == Decimal(
        invoice["amount"]["value"]
    )


def test_a_tranche_paid_before_the_invoice_exists_is_recorded_once_it_does(  # type: ignore[no-untyped-def]
    db_session, paypal: MockPayPal, hospital_with_credentials
) -> None:
    case = settleable_case(target_kes_minor=10_000_000)
    hospital_with_credentials(case)
    donate(db_session, case, "200.00")
    pay_out_what_is_open(db_session)
    tranche = db_session.scalars(select(Settlement).where(Settlement.case_id == case.id)).first()
    assert tranche.status == SettlementStatus.SUCCEEDED
    assert tranche.invoice_payment_id is None  # no invoice yet: nothing could be recorded

    invoicing.sweep(db_session)

    db_session.refresh(tranche)
    confirmation = settlement_service.latest_confirmation(case)
    assert tranche.invoice_payment_id
    assert confirmation.invoice_status == "PARTIALLY_PAID"


def test_without_credentials_settlement_is_unchanged(db_session, paypal: MockPayPal) -> None:
    case = settleable_case(target_kes_minor=10_000_000)
    donate(db_session, case, "200.00")
    pay_out_what_is_open(db_session)

    tranche = db_session.scalars(select(Settlement).where(Settlement.case_id == case.id)).first()
    assert tranche.status == SettlementStatus.SUCCEEDED
    assert tranche.invoice_payment_id is None
    assert (
        db.session.get(type(tranche.confirmation), tranche.confirmation_id).paypal_invoice_id
        is None
    )
