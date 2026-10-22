"""Deliveries and captures arriving at the same moment, on real connections."""

import json

from flask import Flask
from sqlalchemy import func, select

from app.extensions import db
from app.models.case import Case
from app.models.enums import DonationStatus
from app.models.integration import WebhookEvent
from app.models.money import Donation
from app.payments.paypal_client import get_client
from app.services import donation_service, ledger_service
from tests.donation_helpers import live_case, order_for
from tests.services.test_donation_concurrency import run_together
from tests.webhook_helpers import deliver, make_event, paypal_took_the_money


def _setup(app: Flask):  # type: ignore[no-untyped-def]
    with app.app_context():
        case = live_case()
        donation = order_for(db.session, case)
        db.session.commit()
        return case.id, donation.id, donation.paypal_order_id


def test_the_same_event_arriving_four_times_at_once_is_stored_and_applied_once(
    migrated_scratch_app: Flask,
) -> None:
    app = migrated_scratch_app
    case_id, donation_id, order_id = _setup(app)
    with app.app_context():
        donation = db.session.get(Donation, donation_id)
        event = make_event(
            "PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(get_client(), donation)
        )
    body = json.dumps(event).encode()

    def post() -> str:
        return deliver(app.test_client(), raw=body).get_json()["status"]

    outcomes = run_together(app, [post] * 4)

    assert sorted(outcomes) == ["duplicate", "duplicate", "duplicate", "received"]
    with app.app_context():
        assert db.session.execute(select(func.count()).select_from(WebhookEvent)).scalar_one() == 1
        assert db.session.get(Donation, donation_id).status == DonationStatus.CAPTURED
        assert len(db.session.execute(ledger_service.case_entries(case_id)).scalars().all()) == 3
        assert ledger_service.verify_chain(db.session, case_id).valid


def test_the_browsers_capture_and_paypals_event_arriving_together_record_the_money_once(
    migrated_scratch_app: Flask,
) -> None:
    app = migrated_scratch_app
    case_id, donation_id, order_id = _setup(app)
    with app.app_context():
        # PayPal has taken the money; the browser's capture call and PayPal's event now race.
        resource = paypal_took_the_money(get_client(), db.session.get(Donation, donation_id))
    event = make_event("PAYMENT.CAPTURE.COMPLETED", resource)

    def browser() -> object:
        return donation_service.capture_order(db.session, order_id).fresh

    def paypal_event() -> object:
        return deliver(app.test_client(), event).status_code

    outcomes = run_together(app, [browser, paypal_event])

    assert 200 in outcomes
    assert not any(isinstance(o, BaseException) and not hasattr(o, "code") for o in outcomes), (
        outcomes
    )
    with app.app_context():
        donation = db.session.get(Donation, donation_id)
        assert donation.status == DonationStatus.CAPTURED
        entries = db.session.execute(ledger_service.case_entries(case_id)).scalars().all()
        assert len(entries) == 3  # one donation, one fee pair: never doubled
        case = db.session.get(Case, case_id)
        assert (case.captured_net_usd_minor, case.donor_count) == (2397, 1)
        assert ledger_service.verify_chain(db.session, case_id).valid
