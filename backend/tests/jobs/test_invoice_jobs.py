"""The invoicing jobs a worker runs."""

import uuid
from datetime import UTC, datetime

import pytest

from app.extensions import db
from app.jobs import tasks
from app.models.enums import SettlementStatus
from tests.factories import CaseFactory, ConfirmationFactory, FxRateFactory, SettlementFactory


@pytest.fixture
def invoiced_hospital(app, monkeypatch, db_session):  # type: ignore[no-untyped-def]
    FxRateFactory()
    case = CaseFactory()
    code = case.hospital.register_entry.register_code
    monkeypatch.setitem(
        app.config, "HOSPITAL_PAYPAL_CREDENTIALS", {code: {"client_id": "i", "client_secret": "s"}}
    )
    confirmation = ConfirmationFactory(case=case)
    db.session.commit()
    return confirmation


def test_the_issue_job_takes_an_id_and_reports_what_it_did(invoiced_hospital, paypal) -> None:  # type: ignore[no-untyped-def]
    assert tasks.issue_invoice(str(invoiced_hospital.id)) == "issued"
    assert tasks.issue_invoice(invoiced_hospital.id) == "issued"  # a repeat makes no second invoice
    assert tasks.issue_invoice(uuid.uuid4()) == "missing"


def test_the_payment_job_records_a_tranche(invoiced_hospital, paypal) -> None:  # type: ignore[no-untyped-def]
    tasks.issue_invoice(invoiced_hospital.id)
    tranche = SettlementFactory(
        case=invoiced_hospital.case,
        confirmation=invoiced_hospital,
        status=SettlementStatus.SUCCEEDED,
        amount_kes_minor=2_000_000,
        completed_at=datetime(2026, 10, 24, tzinfo=UTC),
    )
    db.session.commit()

    assert tasks.record_invoice_payment(str(tranche.id)) == "recorded"
    assert tasks.record_invoice_payment(tranche.id) == "already_recorded"
    assert tasks.record_invoice_payment(uuid.uuid4()) == "missing"


def test_the_sweep_job_reports_how_much_it_did(invoiced_hospital, paypal) -> None:  # type: ignore[no-untyped-def]
    assert tasks.sweep_invoices() == 2  # the invoice is issued, then its status read
    assert tasks.sweep_invoices() == 1  # an open invoice's status is read again
