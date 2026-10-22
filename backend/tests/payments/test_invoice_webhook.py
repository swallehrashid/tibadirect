"""INVOICING.INVOICE.PAID: PayPal tells us a hospital's invoice was paid."""

from sqlalchemy import select

from app.models.enums import WebhookStatus
from app.models.integration import WebhookEvent
from app.models.platform import AuditLog
from app.services import invoicing_service as invoicing
from tests.factories import CaseFactory, ConfirmationFactory
from tests.webhook_helpers import deliver, make_event


def invoiced(app, monkeypatch, db_session):  # type: ignore[no-untyped-def]
    case = CaseFactory()
    code = case.hospital.register_entry.register_code
    monkeypatch.setitem(
        app.config, "HOSPITAL_PAYPAL_CREDENTIALS", {code: {"client_id": "i", "client_secret": "s"}}
    )
    confirmation = ConfirmationFactory(case=case)
    db_session.commit()
    invoicing.issue(db_session, confirmation.id)
    db_session.refresh(confirmation)
    return confirmation


def only_event(session) -> WebhookEvent:  # type: ignore[no-untyped-def]
    return session.scalars(select(WebhookEvent)).one()


def test_a_paid_event_sets_the_invoice_status(client, app, db_session, paypal, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    confirmation = invoiced(app, monkeypatch, db_session)
    event = make_event(
        "INVOICING.INVOICE.PAID",
        {"invoice": {"id": confirmation.paypal_invoice_id, "status": "PAID"}},
    )

    response = deliver(client, event)

    assert response.status_code == 200
    db_session.refresh(confirmation)
    assert confirmation.invoice_status == "PAID"
    assert only_event(db_session).status == WebhookStatus.PROCESSED
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "invoice.status_changed")
    ).one()


def test_the_resource_may_be_the_invoice_itself(
    client, app, db_session, paypal, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    confirmation = invoiced(app, monkeypatch, db_session)

    deliver(
        client,
        make_event(
            "INVOICING.INVOICE.PAID", {"id": confirmation.paypal_invoice_id, "status": "PAID"}
        ),
    )

    db_session.refresh(confirmation)
    assert confirmation.invoice_status == "PAID"


def test_an_event_for_an_invoice_we_did_not_issue_is_acknowledged_and_ignored(  # type: ignore[no-untyped-def]
    client, db_session, paypal
) -> None:
    response = deliver(
        client,
        make_event("INVOICING.INVOICE.PAID", {"invoice": {"id": "INV2-NOBODY", "status": "PAID"}}),
    )

    assert response.status_code == 200
    event = only_event(db_session)
    assert event.status == WebhookStatus.IGNORED
    assert "No confirmation" in event.error


def test_an_event_with_no_invoice_in_it_is_ignored(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    deliver(client, make_event("INVOICING.INVOICE.PAID", {"something": "else"}))

    assert only_event(db_session).status == WebhookStatus.IGNORED


def test_a_forged_paid_event_changes_nothing(client, app, db_session, paypal, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    confirmation = invoiced(app, monkeypatch, db_session)

    response = deliver(
        client,
        make_event(
            "INVOICING.INVOICE.PAID",
            {"invoice": {"id": confirmation.paypal_invoice_id, "status": "PAID"}},
        ),
        sign=False,
    )

    assert response.status_code == 400
    db_session.refresh(confirmation)
    assert confirmation.invoice_status == "SENT"
