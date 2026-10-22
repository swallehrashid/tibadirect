"""PayPal webhooks: verified, stored once, processed safely, and reconciled with the books."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from redis.exceptions import RedisError
from sqlalchemy import func, select

from app.models.case import ReviewItem
from app.models.enums import (
    CaseStatus,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    RefundStatus,
    ReviewSource,
    WebhookStatus,
)
from app.models.integration import WebhookEvent
from app.models.money import Refund
from app.payments import webhooks
from app.payments.errors import PayPalError
from app.payments.paypal_mock import MockPayPal
from app.services import donation_service, ledger_service, mail_service
from tests.donation_helpers import donate, live_case, order_for
from tests.factories import RefundFactory
from tests.webhook_helpers import (
    deliver,
    make_event,
    paypal_took_the_money,
    refund_resource,
)


def events(session) -> list[WebhookEvent]:  # type: ignore[no-untyped-def]
    return list(session.execute(select(WebhookEvent).order_by(WebhookEvent.created_at)).scalars())


def ledger(session, case):  # type: ignore[no-untyped-def]
    return list(session.execute(ledger_service.case_entries(case.id)).scalars())


def amounts(session, case):  # type: ignore[no-untyped-def]
    return [(e.entry_type, e.account, e.amount_minor) for e in ledger(session, case)]


def count(session, model) -> int:  # type: ignore[no-untyped-def]
    return session.execute(select(func.count()).select_from(model)).scalar_one()


# --------------------------------------------------------------------------- the front door


def test_a_signed_completed_event_finishes_a_donation_the_browser_never_confirmed(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.approve(donation.paypal_order_id, payer_email="closed.tab@example.test")
    resource = paypal_took_the_money(paypal, donation)
    mail_service.outbox.clear()

    response = deliver(client, make_event("PAYMENT.CAPTURE.COMPLETED", resource))

    assert response.status_code == 200
    assert response.get_json() == {"status": "received"}
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CAPTURED
    assert (donation.amount_usd_minor, donation.paypal_fee_usd_minor, donation.net_usd_minor) == (
        2500,
        103,
        2397,
    )
    assert donation.paypal_capture_id == resource["id"]
    assert amounts(db_session, case) == [
        (LedgerEntryType.DONATION_CAPTURED, LedgerAccount.CASE_FUNDS, 2500),
        (LedgerEntryType.PAYPAL_FEE, LedgerAccount.CASE_FUNDS, -103),
        (LedgerEntryType.PAYPAL_FEE, LedgerAccount.PAYPAL_FEES, 103),
    ]
    assert (case.captured_net_usd_minor, case.donor_count) == (2397, 1)
    (event,) = events(db_session)
    assert (event.status, event.signature_verified) == (WebhookStatus.PROCESSED, True)
    assert event.processed_at is not None
    # the payer's address came from the order, so the receipt still goes out
    assert [m.to for m in mail_service.outbox] == ["closed.tab@example.test"]


def test_the_same_event_delivered_again_changes_nothing(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    event = make_event("PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation))

    first = deliver(client, event)
    second = deliver(client, event)
    third = deliver(client, event)

    assert [r.get_json()["status"] for r in (first, second, third)] == [
        "received",
        "duplicate",
        "duplicate",
    ]
    assert count(db_session, WebhookEvent) == 1
    assert len(ledger(db_session, case)) == 3
    assert (case.captured_net_usd_minor, case.donor_count) == (2397, 1)


def test_a_new_event_for_money_already_recorded_is_processed_but_changes_nothing(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    resource = {
        "id": donation.paypal_capture_id,
        "status": "COMPLETED",
        "amount": {"currency_code": "USD", "value": "25.00"},
        "custom_id": str(donation.id),
    }

    response = deliver(client, make_event("PAYMENT.CAPTURE.COMPLETED", resource))

    assert response.status_code == 200
    assert events(db_session)[0].status == WebhookStatus.PROCESSED
    assert len(ledger(db_session, case)) == 3
    assert case.donor_count == 1


def test_a_forged_event_is_refused_and_kept_in_the_log(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    event = make_event("PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation))

    response = deliver(client, event, headers={"PAYPAL-TRANSMISSION-SIG": "MOCKSIG-forged"})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "webhook_signature_invalid"
    (row,) = events(db_session)
    assert (row.signature_verified, row.status) == (False, WebhookStatus.IGNORED)
    assert row.paypal_event_id != event["id"]  # a forger cannot use up a real event's ID
    assert row.payload["id"] == event["id"]
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CREATED
    assert ledger(db_session, case) == []


def test_a_forged_copy_cannot_stop_the_genuine_event_being_processed(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    event = make_event("PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation))

    deliver(client, event, sign=False)  # the forger gets there first
    genuine = deliver(client, event)

    assert genuine.get_json() == {"status": "received"}
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CAPTURED


def test_a_delivery_signed_for_other_content_is_refused(
    client, db_session, paypal: MockPayPal
) -> None:
    from app.payments.paypal_mock import signed_webhook_headers
    from tests.webhook_helpers import WEBHOOK_ID

    case = live_case()
    donation = order_for(db_session, case)
    real = make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-X", "custom_id": str(donation.id)})
    tampered = make_event(
        "PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation), event_id=real["id"]
    )
    headers = signed_webhook_headers(WEBHOOK_ID, json.dumps(real).encode())

    response = deliver(client, tampered, sign=False, headers=headers)

    assert response.status_code == 400
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CREATED


@pytest.mark.parametrize(
    "header",
    [
        "PAYPAL-AUTH-ALGO",
        "PAYPAL-CERT-URL",
        "PAYPAL-TRANSMISSION-ID",
        "PAYPAL-TRANSMISSION-SIG",
        "PAYPAL-TRANSMISSION-TIME",
    ],
)
def test_a_delivery_missing_a_signature_header_is_refused(
    client, db_session, paypal: MockPayPal, header: str
) -> None:
    event = make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-1"})
    body = json.dumps(event).encode()
    from app.payments.paypal_mock import signed_webhook_headers
    from tests.webhook_helpers import WEBHOOK_ID

    headers = signed_webhook_headers(WEBHOOK_ID, body)
    del headers[header]

    response = client.post(
        "/api/webhooks/paypal", data=body, content_type="application/json", headers=headers
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'"text"', b"{}", b'{"id": 5, "event_type": "X"}', b'{"id": "A"}'],
)
def test_a_body_that_is_not_an_event_is_refused_and_not_stored(
    client, db_session, paypal: MockPayPal, body: bytes
) -> None:
    response = deliver(client, raw=body)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "webhook_invalid"
    assert count(db_session, WebhookEvent) == 0


def test_a_huge_body_is_refused_before_anything_is_read(
    client, db_session, paypal: MockPayPal
) -> None:
    body = b'{"id": "A", "event_type": "X", "pad": "' + b"x" * (webhooks.MAX_BODY_BYTES + 1) + b'"}'

    response = deliver(client, raw=body)

    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "webhook_too_large"
    assert count(db_session, WebhookEvent) == 0


def test_if_paypal_cannot_be_asked_to_verify_the_event_is_refused_for_resending(
    client, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    def down(**_: object) -> bool:
        raise PayPalError(
            status_code=502, code="paypal_unavailable", message="down", retryable=True
        )

    monkeypatch.setattr(paypal, "verify_webhook_signature", down)

    response = deliver(client, make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-1"}))

    assert response.status_code == 502
    assert count(db_session, WebhookEvent) == 0  # nothing stored, so PayPal's resend is new


def test_the_route_needs_no_csrf_token_and_no_session(
    client, db_session, paypal: MockPayPal, csrf_on: None
) -> None:
    response = deliver(client, make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-1"}))

    assert response.status_code == 200


def test_the_stored_copy_of_an_event_holds_no_payer_details(
    client, db_session, paypal: MockPayPal
) -> None:
    event = make_event(
        "CHECKOUT.ORDER.APPROVED",
        {
            "id": "ORD-1",
            "status": "APPROVED",
            "payer": {"email_address": "p@example.test", "name": {"given_name": "Pat"}},
            "purchase_units": [{"shipping": {"address": {"address_line_1": "1 Road"}}}],
        },
    )

    deliver(client, event)

    (row,) = events(db_session)
    text = json.dumps(row.payload)
    for private in ("p@example.test", "Pat", "Road"):
        assert private not in text
    assert row.payload["resource"]["id"] == "ORD-1"
    assert (row.event_type, row.resource_type, row.resource_id) == (
        "CHECKOUT.ORDER.APPROVED",
        "order",
        "ORD-1",
    )


def test_a_failure_to_queue_does_not_lose_the_event(
    client, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    def no_redis(*_: object, **__: object) -> None:
        raise RedisError("down")

    monkeypatch.setattr(webhooks.jobs, "enqueue", no_redis)
    case = live_case()
    donation = order_for(db_session, case)
    event = make_event("PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation))

    response = deliver(client, event)

    assert response.status_code == 200
    (row,) = events(db_session)
    assert row.status == WebhookStatus.RECEIVED
    monkeypatch.undo()
    row.received_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.flush()
    assert webhooks.enqueue_stale_events(db_session) == 1
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CAPTURED


def test_the_sweep_leaves_recent_unverified_and_finished_events_alone(
    client, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    monkeypatch.setattr(webhooks.jobs, "enqueue", lambda *a, **k: None)
    deliver(client, make_event("PAYMENT.CAPTURE.DENIED", {"id": "A"}))  # recent
    deliver(client, make_event("PAYMENT.CAPTURE.DENIED", {"id": "B"}), sign=False)  # forged
    (recent, forged) = events(db_session)
    forged.received_at = datetime.now(UTC) - timedelta(hours=1)
    done = WebhookEvent(
        paypal_event_id="WH-DONE",
        event_type="X",
        payload={},
        signature_verified=True,
        status=WebhookStatus.PROCESSED,
        received_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(done)
    db_session.flush()

    assert webhooks.enqueue_stale_events(db_session) == 0
    assert recent.status == WebhookStatus.RECEIVED


# --------------------------------------------------------------------------- capture events


def test_a_completed_event_for_a_payment_we_do_not_know_is_acknowledged_and_ignored(
    client, db_session, paypal: MockPayPal
) -> None:
    resource = {
        "id": "CAP-STRANGER",
        "status": "COMPLETED",
        "amount": {"currency_code": "USD", "value": "5.00"},
    }

    response = deliver(client, make_event("PAYMENT.CAPTURE.COMPLETED", resource))

    assert response.status_code == 200
    (row,) = events(db_session)
    assert row.status == WebhookStatus.IGNORED
    assert "No donation matches" in row.error


def test_a_payment_whose_custom_id_names_another_donation_is_ignored(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    resource = paypal_took_the_money(paypal, donation)
    resource["custom_id"] = "00000000-0000-0000-0000-000000000001"

    deliver(client, make_event("PAYMENT.CAPTURE.COMPLETED", resource))

    assert events(db_session)[0].status == WebhookStatus.IGNORED
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CREATED


def test_a_donation_is_found_by_its_custom_id_when_nothing_else_matches(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    resource = paypal_took_the_money(paypal, donation)
    del resource["supplementary_data"]  # no order link: only the custom ID names the donation

    deliver(client, make_event("PAYMENT.CAPTURE.COMPLETED", resource))

    db_session.refresh(donation)
    assert donation.status == DonationStatus.CAPTURED


def test_a_capture_that_is_still_pending_is_ignored_until_the_completed_event(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.next_capture_status("PENDING")
    resource = paypal_took_the_money(paypal, donation)

    deliver(client, make_event("PAYMENT.CAPTURE.COMPLETED", resource))

    assert events(db_session)[0].status == WebhookStatus.IGNORED
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CREATED


def test_money_paypal_took_is_recorded_even_if_the_case_has_since_closed(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    resource = paypal_took_the_money(paypal, donation)
    case.status = CaseStatus.CLOSED
    case.close_reason = "fully_settled"
    db_session.flush()

    deliver(client, make_event("PAYMENT.CAPTURE.COMPLETED", resource))

    db_session.refresh(donation)
    assert donation.status == DonationStatus.CAPTURED
    assert case.status == CaseStatus.CLOSED
    assert case.captured_net_usd_minor == 2397


def test_a_late_completed_event_can_fill_the_goal_and_fund_the_case(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case(target_kes_minor=100_000, buffer_bps=500)
    donation = order_for(db_session, case, "10.00")

    deliver(
        client, make_event("PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation))
    )

    assert case.status == CaseStatus.FUNDED


def test_a_denied_event_marks_a_waiting_donation_denied(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)

    deliver(
        client, make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-1", "custom_id": str(donation.id)})
    )

    db_session.refresh(donation)
    assert donation.status == DonationStatus.DENIED
    assert events(db_session)[0].status == WebhookStatus.PROCESSED


def test_a_denied_event_cannot_undo_money_already_taken(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)

    deliver(
        client,
        make_event("PAYMENT.CAPTURE.DENIED", {"id": donation.paypal_capture_id}),
    )

    db_session.refresh(donation)
    assert donation.status == DonationStatus.CAPTURED
    assert events(db_session)[0].status == WebhookStatus.IGNORED


# --------------------------------------------------------------------------- refund events


def test_a_refunded_event_finishes_the_refund_we_asked_for(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    refund = RefundFactory(
        donation=donation,
        amount_usd_minor=1000,
        reason=RefundReason.EXCESS_POLICY,
        status=RefundStatus.PENDING,
    )
    db_session.commit()

    deliver(
        client,
        make_event("PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "REFUND-1", 1000)),
    )

    db_session.refresh(refund)
    db_session.refresh(donation)
    assert (refund.status, refund.paypal_refund_id) == (RefundStatus.SUCCEEDED, "REFUND-1")
    assert (donation.refunded_usd_minor, donation.status) == (
        1000,
        DonationStatus.PARTIALLY_REFUNDED,
    )
    assert amounts(db_session, case)[-2:] == [
        (LedgerEntryType.REFUND_ISSUED, LedgerAccount.CASE_FUNDS, -1000),
        (LedgerEntryType.REFUND_ISSUED, LedgerAccount.DONOR_REFUNDS, 1000),
    ]
    assert case.refunded_usd_minor == 1000
    assert case.pledged_kes_minor == (2397 - 1000) * 129
    assert ledger_service.verify_chain(db_session, case.id).valid


def test_the_same_refund_reported_in_a_second_event_is_applied_once(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    RefundFactory(donation=donation, amount_usd_minor=1000, status=RefundStatus.PENDING)
    db_session.commit()
    resource = refund_resource(donation, "REFUND-1", 1000)

    deliver(client, make_event("PAYMENT.CAPTURE.REFUNDED", resource))
    deliver(client, make_event("PAYMENT.CAPTURE.REFUNDED", resource))  # a different event ID

    db_session.refresh(donation)
    assert donation.refunded_usd_minor == 1000
    assert count(db_session, Refund) == 1
    assert len(ledger(db_session, case)) == 5


def test_refunding_everything_the_case_holds_marks_the_donation_refunded(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    RefundFactory(donation=donation, amount_usd_minor=2397, status=RefundStatus.PENDING)
    db_session.commit()

    deliver(client, make_event("PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "R-1", 2397)))

    db_session.refresh(donation)
    assert donation.status == DonationStatus.REFUNDED
    assert case.pledged_kes_minor == 0


def test_a_refund_made_in_paypal_itself_is_recorded(client, db_session, paypal: MockPayPal) -> None:
    case = live_case()
    donation = donate(db_session, case)

    deliver(
        client,
        make_event("PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "REFUND-EXT", 1500)),
    )

    refund = db_session.execute(select(Refund)).scalar_one()
    assert (refund.paypal_refund_id, refund.amount_usd_minor) == ("REFUND-EXT", 1500)
    assert (refund.reason, refund.requested_by.value, refund.status) == (
        RefundReason.ADMIN,
        "paypal",
        RefundStatus.SUCCEEDED,
    )
    db_session.refresh(donation)
    assert donation.refunded_usd_minor == 1500
    assert count(db_session, ReviewItem) == 0


def test_a_full_refund_in_paypal_applies_what_the_case_held_and_asks_an_admin_to_look(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)  # gross 25.00, the case holds 23.97

    deliver(
        client, make_event("PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "R-FULL", 2500))
    )

    db_session.refresh(donation)
    assert (donation.refunded_usd_minor, donation.status) == (2397, DonationStatus.REFUNDED)
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.source == ReviewSource.ADMIN
    assert item.payload["paypal_refund_usd_minor"] == 2500
    assert item.payload["applied_usd_minor"] == 2397


def test_a_refund_in_paypal_of_money_already_paid_out_is_only_a_review_item(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    donation.allocated_usd_minor = donation.net_usd_minor  # all of it went to the hospital
    db_session.commit()

    deliver(client, make_event("PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "R-X", 500)))

    db_session.refresh(donation)
    assert donation.refunded_usd_minor == 0
    assert count(db_session, Refund) == 0
    assert db_session.execute(select(ReviewItem)).scalar_one().payload["applied_usd_minor"] == 0


def test_a_refund_while_another_is_still_being_recorded_waits_and_is_retried(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    RefundFactory(donation=donation, amount_usd_minor=1000, status=RefundStatus.PENDING)
    db_session.commit()
    event = WebhookEvent(
        paypal_event_id="WH-REF",
        event_type="PAYMENT.CAPTURE.REFUNDED",
        payload=make_event("PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "R-OTHER", 400)),
        signature_verified=True,
    )
    db_session.add(event)
    db_session.commit()

    with pytest.raises(webhooks.WebhookDeferred):
        webhooks.process_event(db_session, event.id)

    db_session.refresh(event)
    assert event.status == WebhookStatus.FAILED
    assert event.error == "Waiting for related data"
    assert count(db_session, Refund) == 1  # no external refund was invented


def test_a_refund_that_is_not_completed_is_ignored(client, db_session, paypal: MockPayPal) -> None:
    donation = donate(db_session, live_case())

    deliver(
        client,
        make_event(
            "PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "R-P", 500, status="PENDING")
        ),
    )

    assert events(db_session)[0].status == WebhookStatus.IGNORED
    assert count(db_session, Refund) == 0


@pytest.mark.parametrize(
    "resource",
    [{}, {"id": "R-1", "status": "COMPLETED"}, {"id": 5}],
)
def test_a_refund_event_that_does_not_name_its_payment_is_ignored(
    client, db_session, paypal: MockPayPal, resource: dict
) -> None:
    deliver(client, make_event("PAYMENT.CAPTURE.REFUNDED", resource))

    assert events(db_session)[0].status == WebhookStatus.IGNORED


# --------------------------------------------------------------------------- reversal events


def test_a_reversed_payment_leaves_the_case_and_tells_an_admin(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)

    deliver(client, make_event("PAYMENT.CAPTURE.REVERSED", {"id": donation.paypal_capture_id}))

    db_session.refresh(donation)
    assert donation.status == DonationStatus.REVERSED
    assert amounts(db_session, case)[-1] == (
        LedgerEntryType.REVERSAL,
        LedgerAccount.CASE_FUNDS,
        -2397,
    )
    assert (case.captured_net_usd_minor, case.pledged_kes_minor, case.donor_count) == (0, 0, 0)
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.case_id == case.id
    assert item.payload["removed_from_case_usd_minor"] == 2397
    assert ledger_service.verify_chain(db_session, case.id).valid


def test_a_reversal_only_takes_back_money_still_in_the_case(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    donation.allocated_usd_minor = 1000  # already sent to the hospital
    donation.refunded_usd_minor = 400
    db_session.commit()

    deliver(client, make_event("PAYMENT.CAPTURE.REVERSED", {"id": donation.paypal_capture_id}))

    assert amounts(db_session, case)[-1][2] == -(2397 - 1000 - 400)
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.payload["already_paid_out_usd_minor"] == 1000


def test_a_reversal_reported_twice_is_applied_once(client, db_session, paypal: MockPayPal) -> None:
    case = live_case()
    donation = donate(db_session, case)
    resource = {"id": donation.paypal_capture_id}

    deliver(client, make_event("PAYMENT.CAPTURE.REVERSED", resource))
    deliver(client, make_event("PAYMENT.CAPTURE.REVERSED", resource))

    assert len(ledger(db_session, case)) == 4
    assert count(db_session, ReviewItem) == 1


def test_a_reversal_for_a_payment_never_recorded_is_ignored(
    client, db_session, paypal: MockPayPal
) -> None:
    donation = order_for(db_session, live_case())
    donation.paypal_capture_id = "CAP-NEVER"
    db_session.commit()

    deliver(client, make_event("PAYMENT.CAPTURE.REVERSED", {"id": "CAP-NEVER"}))

    assert events(db_session)[0].status == WebhookStatus.IGNORED
    assert count(db_session, ReviewItem) == 0


# --------------------------------------------------------------------------- other events


@pytest.mark.parametrize(
    "event_type",
    [
        "CHECKOUT.ORDER.APPROVED",
        "PAYMENT.PAYOUTSBATCH.SUCCESS",
        "PAYMENT.PAYOUTSBATCH.DENIED",
        "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
        "PAYMENT.PAYOUTS-ITEM.FAILED",
        "PAYMENT.PAYOUTS-ITEM.BLOCKED",
        "PAYMENT.PAYOUTS-ITEM.RETURNED",
        "INVOICING.INVOICE.PAID",
        "BILLING.SOMETHING.NEW",
    ],
)
def test_events_with_nothing_to_act_on_are_acknowledged_and_marked_ignored(
    client, db_session, paypal: MockPayPal, event_type: str
) -> None:
    response = deliver(client, make_event(event_type, {"id": "X-1"}))

    assert response.status_code == 200
    (row,) = events(db_session)
    assert row.status == WebhookStatus.IGNORED
    assert row.error


# --------------------------------------------------------------------------- failures and retries


def test_a_handler_that_fails_leaves_the_event_failed_and_a_retry_completes_it(
    client, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    boom = {"armed": True}
    original = webhooks.HANDLERS["PAYMENT.CAPTURE.COMPLETED"]

    def flaky(session, resource, event):  # type: ignore[no-untyped-def]
        if boom["armed"]:
            raise RuntimeError("database hiccup for jane@example.test")
        original(session, resource, event)

    monkeypatch.setitem(webhooks.HANDLERS, "PAYMENT.CAPTURE.COMPLETED", flaky)
    deliver(
        client, make_event("PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation))
    )
    (row,) = events(db_session)
    assert row.status == WebhookStatus.FAILED
    assert "jane@example.test" not in row.error  # the reason is scrubbed
    assert "[email]" in row.error
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CREATED

    boom["armed"] = False
    assert webhooks.process_event(db_session, row.id) == WebhookStatus.PROCESSED
    db_session.refresh(donation)
    assert donation.status == DonationStatus.CAPTURED


def test_a_finished_event_is_never_handled_again(
    client, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    deliver(client, make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-1"}))
    (row,) = events(db_session)
    called = []
    monkeypatch.setitem(webhooks.HANDLERS, row.event_type, lambda *a: called.append(a))

    assert webhooks.process_event(db_session, row.id) == WebhookStatus.IGNORED

    assert called == []


def test_processing_an_unknown_or_unverified_event_does_nothing(
    client, db_session, paypal: MockPayPal
) -> None:
    import uuid

    deliver(client, make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-1"}), sign=False)
    (forged,) = events(db_session)

    assert webhooks.process_event(db_session, uuid.uuid4()) is None
    assert webhooks.process_event(db_session, forged.id) is None
    assert forged.status == WebhookStatus.IGNORED


def test_the_mock_webhook_id_is_used_when_none_is_configured_in_mock_mode(
    app, client, db_session, paypal: MockPayPal
) -> None:
    from app.payments.paypal_mock import signed_webhook_headers

    app.config["PAYPAL_WEBHOOK_ID"] = ""
    event = make_event("PAYMENT.CAPTURE.DENIED", {"id": "CAP-1"})
    body = json.dumps(event).encode()
    headers = signed_webhook_headers(webhooks.MOCK_WEBHOOK_ID, body)

    response = client.post(
        "/api/webhooks/paypal", data=body, content_type="application/json", headers=headers
    )

    assert response.status_code == 200
    app.config["PAYPAL_WEBHOOK_ID"] = "WH-TEST-WEBHOOK-ID"


def test_in_sandbox_mode_without_a_webhook_id_nothing_can_be_verified(
    app, client, db_session, monkeypatch
) -> None:
    monkeypatch.setitem(app.config, "PAYPAL_WEBHOOK_ID", "")
    monkeypatch.setitem(app.config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setitem(app.config, "PAYPAL_CLIENT_ID", "id")
    monkeypatch.setitem(app.config, "PAYPAL_CLIENT_SECRET", "secret")

    with app.app_context():
        assert webhooks._webhook_id() == ""  # noqa: SLF001


def test_the_donation_service_is_what_records_a_webhook_capture(
    client, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    """One code path for both the browser's capture and PayPal's event."""
    seen = []
    original = donation_service.record_capture

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs["actor"])
        return original(*args, **kwargs)

    monkeypatch.setattr(donation_service, "record_capture", spy)
    donation = order_for(db_session, live_case())

    deliver(
        client, make_event("PAYMENT.CAPTURE.COMPLETED", paypal_took_the_money(paypal, donation))
    )

    assert [actor.value for actor in seen] == ["paypal"]


# --------------------------------------------------------------------------- payout events


def sent_tranche(db_session, paypal, outcome: str = "SUCCESS"):  # type: ignore[no-untyped-def]
    """A tranche sent to PayPal and still marked processing: PayPal has yet to report back."""
    from app.models.enums import SettlementStatus
    from app.services import settlement_service
    from tests.donation_helpers import settleable_case

    case = settleable_case()
    donate(db_session, case, "200.00")
    paypal.next_payout_outcome("PENDING")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    assert settlement.status == SettlementStatus.PROCESSING
    batch = paypal.store.get("payout", settlement.external_batch_id)
    batch["outcome"] = outcome  # PayPal has now made up its mind
    paypal.store.put("payout", settlement.external_batch_id, batch)
    return case, settlement


def item_resource(settlement) -> dict:  # type: ignore[no-untyped-def]
    return {
        "payout_batch_id": settlement.external_batch_id,
        "payout_item_id": "ITEM-X",
        "transaction_status": "SUCCESS",
    }


@pytest.mark.usefixtures("manual_settlement")
def test_an_item_succeeded_event_finishes_the_tranche_it_names(
    client, db_session, paypal: MockPayPal
) -> None:
    from app.models.enums import SettlementStatus

    case, settlement = sent_tranche(db_session, paypal)
    mail_service.outbox.clear()

    response = deliver(
        client, make_event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", item_resource(settlement))
    )

    assert response.status_code == 200
    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUCCEEDED
    assert case.settled_kes_minor == 2_501_310
    assert events(db_session)[0].status == WebhookStatus.PROCESSED
    assert {m.template for m in mail_service.outbox} == {"tranche_settled"}


@pytest.mark.usefixtures("manual_settlement")
def test_the_event_only_names_the_tranche_paypal_is_asked_for_the_truth(
    client, db_session, paypal: MockPayPal
) -> None:
    """A forged-looking 'succeeded' cannot pay a tranche PayPal says has failed."""
    from app.models.enums import SettlementStatus

    case, settlement = sent_tranche(db_session, paypal, outcome="FAILED")

    deliver(client, make_event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", item_resource(settlement)))

    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.FAILED
    assert case.settled_kes_minor == 0


@pytest.mark.usefixtures("manual_settlement")
def test_a_batch_event_is_matched_by_batch_id_or_by_our_key(
    client, db_session, paypal: MockPayPal
) -> None:
    from app.models.enums import SettlementStatus

    case, settlement = sent_tranche(db_session, paypal)
    resource = {
        "batch_header": {
            "payout_batch_id": "UNKNOWN",
            "sender_batch_header": {"sender_batch_id": settlement.idempotency_key},
        }
    }

    deliver(client, make_event("PAYMENT.PAYOUTSBATCH.SUCCESS", resource))

    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUCCEEDED


@pytest.mark.usefixtures("manual_settlement")
def test_a_payout_event_for_a_payout_we_never_made_is_ignored(
    client, db_session, paypal: MockPayPal
) -> None:
    deliver(
        client,
        make_event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"payout_batch_id": "NOT-OURS"}),
    )
    deliver(client, make_event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {}))

    assert [e.status for e in events(db_session)] == [WebhookStatus.IGNORED] * 2
    assert "No settlement matches" in events(db_session)[0].error


@pytest.mark.usefixtures("manual_settlement")
def test_a_payout_returned_after_it_was_paid_is_a_review_item_not_a_reversal(
    client, db_session, paypal: MockPayPal
) -> None:
    from app.models.enums import SettlementStatus
    from app.services import settlement_service

    case, settlement = sent_tranche(db_session, paypal)
    settlement_service.poll_settlement(db_session, settlement.id)
    assert settlement.status == SettlementStatus.SUCCEEDED
    batch = paypal.store.get("payout", settlement.external_batch_id)
    batch["outcome"] = "RETURNED"
    paypal.store.put("payout", settlement.external_batch_id, batch)
    mail_service.outbox.clear()

    deliver(client, make_event("PAYMENT.PAYOUTS-ITEM.RETURNED", item_resource(settlement)))

    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUCCEEDED
    assert case.settled_kes_minor == 2_501_310
    assert (
        db_session.execute(select(ReviewItem)).scalar_one().payload["provider_status"] == "failed"
    )
    assert mail_service.outbox == []


@pytest.mark.usefixtures("manual_settlement")
def test_a_payout_event_delivered_twice_pays_and_tells_once(
    client, db_session, paypal: MockPayPal
) -> None:
    case, settlement = sent_tranche(db_session, paypal)
    event = make_event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", item_resource(settlement))
    mail_service.outbox.clear()

    deliver(client, event)
    deliver(client, event)
    deliver(client, make_event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", item_resource(settlement)))

    assert (
        len([m for m in mail_service.outbox if m.template == "tranche_settled"]) == 2
    )  # family, hospital
    assert case.settled_kes_minor == 2_501_310


def test_a_reversal_takes_back_only_money_still_in_the_case_not_what_went_to_the_pool(
    client, db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = donate(db_session, case)
    donation.pooled_usd_minor = donation.net_usd_minor  # already moved to the solidarity pool
    db_session.commit()

    deliver(client, make_event("PAYMENT.CAPTURE.REVERSED", {"id": donation.paypal_capture_id}))

    db_session.refresh(donation)
    assert donation.status == DonationStatus.REVERSED
    assert [e for e in ledger(db_session, case) if e.entry_type == LedgerEntryType.REVERSAL] == []
    assert (
        db_session.execute(select(ReviewItem)).scalar_one().payload["removed_from_case_usd_minor"]
        == 0
    )
