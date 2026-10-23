"""Donation checkout: orders, capture, the money maths, the ledger and the cached totals."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.errors import NotFound, ValidationFailed
from app.models.case import Case
from app.models.enums import (
    ActorType,
    CaseStatus,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    LedgerEntryType,
)
from app.models.ledger import LedgerEntry
from app.models.money import Donation
from app.models.platform import AuditLog
from app.payments import parsing
from app.payments.errors import PayPalError
from app.payments.paypal_mock import MockPayPal
from app.services import case_service, donation_service, ledger_service, mail_service
from tests.donation_helpers import RATE, calls, donate, live_case, order_for


def ledger_for(session, case: Case) -> list[LedgerEntry]:  # type: ignore[no-untyped-def]
    return list(session.execute(ledger_service.case_entries(case.id)).scalars())


def audit_actions(session, target_id) -> list[str]:  # type: ignore[no-untyped-def]
    rows = session.execute(
        select(AuditLog.action).where(AuditLog.target_id == target_id).order_by(AuditLog.created_at)
    )
    return list(rows.scalars())


# --------------------------------------------------------------------------- creating an order


def test_an_order_is_made_with_the_donation_and_case_in_paypals_fields(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    case = live_case()
    sent = calls(paypal, "create_order", monkeypatch)

    donation = order_for(db_session, case, "25.00")

    assert donation.status == DonationStatus.CREATED
    assert donation.amount_usd_minor == 2500
    assert donation.paypal_order_id.startswith("MOCKORD")
    ((_, kwargs),) = sent
    assert kwargs["amount_usd_minor"] == 2500
    assert kwargs["custom_id"] == str(donation.id)
    assert kwargs["reference_id"] == str(case.id)
    assert kwargs["description"] == f"TibaDirect donation for case {case.public_slug}"
    assert kwargs["request_id"] == f"order-{donation.id}"
    assert case.alias not in kwargs["description"]
    assert "donation.created" in audit_actions(db_session, donation.id)


def test_the_donors_choices_are_stored(db_session, paypal: MockPayPal) -> None:
    case = live_case()

    donation = order_for(
        db_session,
        case,
        "10",
        excess=ExcessChoice.FOLLOW_UP_CARE,
        name="  Amina   O.  ",
        anonymous=False,
    )

    assert donation.excess_choice == ExcessChoice.FOLLOW_UP_CARE
    assert donation.donor_display_name == "Amina O."
    assert donation.is_anonymous is False


def test_an_anonymous_donor_leaves_no_name(db_session, paypal: MockPayPal) -> None:
    donation = order_for(db_session, live_case(), name="Amina", anonymous=True)

    assert donation.donor_display_name is None
    assert donation.is_anonymous is True


@pytest.mark.parametrize("amount", ["0.99", "0", "-5", "10000.01", "1000000"])
def test_an_amount_outside_one_to_ten_thousand_dollars_is_refused(
    db_session, paypal: MockPayPal, amount: str
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        order_for(db_session, live_case(), amount)

    assert caught.value.code in {"amount_out_of_range", "amount_invalid"}
    assert "amount_usd" in caught.value.details["json"]


@pytest.mark.parametrize("amount", ["1.00", "10000", "10000.00", "25.5", "99.99"])
def test_amounts_at_and_inside_the_limits_are_accepted(
    db_session, paypal: MockPayPal, amount: str
) -> None:
    donation = order_for(db_session, live_case(), amount)

    assert donation.amount_usd_minor == int(Decimal(amount) * 100)


@pytest.mark.parametrize("amount", ["25.005", "1.999", "10.001"])
def test_more_than_two_decimal_places_is_refused(
    db_session, paypal: MockPayPal, amount: str
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        order_for(db_session, live_case(), amount)

    assert caught.value.code == "amount_invalid"


@pytest.mark.parametrize("name", ["jane@example.test", "call 0712 345 678", "x" * 41, "a​b\x07"])
def test_a_donor_name_with_contact_details_or_junk_is_refused(
    db_session, paypal: MockPayPal, name: str
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        order_for(db_session, live_case(), name=name, anonymous=False)

    assert "donor_display_name" in caught.value.details["json"]


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.DRAFT,
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.CANCELLED,
    ],
)
def test_a_case_that_is_not_public_does_not_exist_to_donors(
    db_session, paypal: MockPayPal, monkeypatch, status: CaseStatus
) -> None:
    case = live_case()
    case.status = status
    db_session.flush()
    sent = calls(paypal, "create_order", monkeypatch)

    with pytest.raises(NotFound) as caught:
        order_for(db_session, case)

    assert caught.value.code == "case_not_found"
    assert sent == []


def test_a_case_whose_family_withdrew_the_public_page_does_not_exist_to_donors(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    case.consents[0].withdrawn_at = datetime.now(UTC)
    db_session.flush()

    with pytest.raises(NotFound):
        order_for(db_session, case)


def test_an_unknown_slug_is_not_found(db_session, paypal: MockPayPal) -> None:
    with pytest.raises(NotFound):
        donation_service.create_order(
            db_session,
            case_slug="nosuchcase01",
            amount_usd=Decimal("5"),
            excess_choice=ExcessChoice.REFUND,
            donor_display_name=None,
            is_anonymous=True,
        )


@pytest.mark.parametrize("status", [CaseStatus.FUNDED, CaseStatus.CLOSED, CaseStatus.FROZEN])
def test_a_public_case_that_is_not_live_refuses_new_orders_without_calling_paypal(
    db_session, paypal: MockPayPal, monkeypatch, status: CaseStatus
) -> None:
    case = live_case()
    case.status = status
    case.close_reason = "fully_settled" if status == CaseStatus.CLOSED else None
    case.freeze_reason = "checking" if status == CaseStatus.FROZEN else None
    db_session.flush()
    sent = calls(paypal, "create_order", monkeypatch)

    # A frozen case is hidden; funded and closed ones are still visible but closed to money.
    expected = NotFound if status == CaseStatus.FROZEN else donation_service.CaseNotAccepting
    with pytest.raises(expected):
        order_for(db_session, case)

    assert sent == []
    assert db_session.execute(select(func.count()).select_from(Donation)).scalar_one() == 0


def test_a_case_pledged_up_to_its_goal_refuses_new_orders(db_session, paypal: MockPayPal) -> None:
    case = live_case(target_kes_minor=1_000_000, buffer_bps=0)
    case.pledged_kes_minor = 1_000_000
    db_session.flush()

    with pytest.raises(donation_service.CaseNotAccepting) as caught:
        order_for(db_session, case)

    assert caught.value.code == "case_not_accepting"
    assert caught.value.status_code == 409


def test_nothing_is_stored_when_paypal_cannot_make_the_order(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    paypal.fail_next("create_order", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError):
        order_for(db_session, case)

    assert db_session.execute(select(func.count()).select_from(Donation)).scalar_one() == 0


def test_an_order_without_an_id_is_a_bad_response(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    monkeypatch.setattr(paypal, "create_order", lambda **_: {"status": "CREATED"})

    with pytest.raises(PayPalError) as caught:
        order_for(db_session, live_case())

    assert caught.value.code == "paypal_bad_response"


# --------------------------------------------------------------------------- capture: the maths


def test_a_capture_records_gross_fee_net_and_the_kes_value_at_the_rate_of_the_day(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()

    donation = donate(db_session, case, "25.00")

    assert donation.status == DonationStatus.CAPTURED
    assert donation.amount_usd_minor == 2500
    assert donation.paypal_fee_usd_minor == 103  # 2.9 % of $25.00 is 72.5 cents, plus 30 cents
    assert donation.net_usd_minor == 2397
    assert donation.fx_rate.rate == RATE
    assert donation.kes_equivalent_minor == 2397 * 129  # what the case receives, in KES cents
    assert donation.paypal_capture_id.startswith("MOCKCAP")
    assert donation.captured_at is not None
    assert donation.allocated_usd_minor == donation.refunded_usd_minor == 0


@pytest.mark.parametrize(
    ("amount", "fee", "net"),
    [("1.00", 33, 67), ("10.00", 59, 941), ("100.00", 320, 9680), ("9999.99", 29030, 970969)],
)
def test_the_fee_maths_holds_across_amounts(
    db_session, paypal: MockPayPal, amount: str, fee: int, net: int
) -> None:
    donation = donate(db_session, live_case(target_kes_minor=900_000_000), amount)

    assert (donation.paypal_fee_usd_minor, donation.net_usd_minor) == (fee, net)
    assert donation.amount_usd_minor == donation.paypal_fee_usd_minor + donation.net_usd_minor


def test_the_payers_email_is_kept_encrypted_and_findable_only_by_its_keyed_hash(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.approve(donation.paypal_order_id, payer_email="kind.donor@example.test")

    donation_service.capture_order(db_session, donation.paypal_order_id)

    raw = db_session.execute(
        text("SELECT donor_email_enc, donor_email_hmac FROM donations WHERE id = :id"),
        {"id": donation.id},
    ).one()
    assert b"kind.donor" not in bytes(raw.donor_email_enc)
    assert len(raw.donor_email_hmac) == 64
    assert "kind.donor" not in raw.donor_email_hmac
    assert donation.donor_email_enc == b"kind.donor@example.test"


# --------------------------------------------------------------------------- capture: the books


def test_a_capture_writes_the_donation_and_the_fee_to_the_ledger(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()

    donation = donate(db_session, case)

    entries = ledger_for(db_session, case)
    assert [(e.entry_type, e.account, e.amount_minor) for e in entries] == [
        (LedgerEntryType.DONATION_CAPTURED, LedgerAccount.CASE_FUNDS, 2500),
        (LedgerEntryType.PAYPAL_FEE, LedgerAccount.CASE_FUNDS, -103),
        (LedgerEntryType.PAYPAL_FEE, LedgerAccount.PAYPAL_FEES, 103),
    ]
    assert {(e.ref_type, e.ref_id, e.currency, e.case_id) for e in entries} == {
        ("donation", donation.id, "USD", case.id)
    }
    # what is left in case_funds is exactly what the case received
    in_case = sum(e.amount_minor for e in entries if e.account == LedgerAccount.CASE_FUNDS)
    assert in_case == donation.net_usd_minor
    assert ledger_service.verify_chain(db_session, case.id).valid


def test_the_cached_case_totals_follow_the_capture(db_session, paypal: MockPayPal) -> None:
    case = live_case()

    donate(db_session, case, "25.00")
    donate(db_session, case, "10.00")

    assert case.captured_net_usd_minor == 2397 + 941
    assert case.pledged_kes_minor == (2397 + 941) * 129
    assert case.donor_count == 2
    assert case.settled_kes_minor == 0


def test_the_cached_totals_equal_a_recount_from_the_rows(db_session, paypal: MockPayPal) -> None:
    case = live_case()
    for amount in ("25.00", "13.37", "100.00"):
        donate(db_session, case, amount)
    cached = (case.captured_net_usd_minor, case.pledged_kes_minor, case.donor_count)

    db_session.expire(case, ["donations"])
    case_service.recompute_totals(case)

    assert (case.captured_net_usd_minor, case.pledged_kes_minor, case.donor_count) == cached


def test_a_capture_is_audited_without_personal_data(db_session, paypal: MockPayPal) -> None:
    donation = donate(db_session, live_case())

    entry = db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == donation.id, AuditLog.action == "donation.captured"
        )
    ).scalar_one()
    assert entry.actor_type == ActorType.SYSTEM
    assert entry.details == {
        "case_id": str(donation.case_id),
        "amount_usd_minor": 2500,
        "fee_usd_minor": 103,
        "amount_matched_order": True,
    }


def test_a_donor_who_captures_the_goal_moves_the_case_to_funded(
    db_session, paypal: MockPayPal
) -> None:
    # Goal: KES 1,000.00 plus 5 % = KES 1,050.00; one $10 donation is worth KES 1,214.
    case = live_case(target_kes_minor=100_000, buffer_bps=500)

    donate(db_session, case, "10.00")

    assert case.status == CaseStatus.FUNDED
    assert case.funded_at is not None
    assert not case_service.is_accepting_donations(case)
    timeline = [step["status"] for step in case_service.status_timeline(db_session, case)]
    assert timeline[-1] == "funded"


def test_a_donation_short_of_the_goal_leaves_the_case_live(db_session, paypal: MockPayPal) -> None:
    case = live_case(target_kes_minor=10_000_000)

    donate(db_session, case, "25.00")

    assert case.status == CaseStatus.LIVE
    assert case.funded_at is None


# --------------------------------------------------------------------------- capture: idempotency


def test_capturing_twice_returns_the_same_donation_and_charges_once(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    sent = calls(paypal, "capture_order", monkeypatch)

    first = donation_service.capture_order(db_session, donation.paypal_order_id)
    second = donation_service.capture_order(db_session, donation.paypal_order_id)

    assert first.fresh
    assert not second.fresh
    assert first.donation.id == second.donation.id
    assert len(sent) == 1  # PayPal was asked once
    assert len(ledger_for(db_session, case)) == 3
    assert (case.captured_net_usd_minor, case.donor_count) == (2397, 1)
    assert (
        db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "donation.captured", AuditLog.target_id == donation.id)
        ).scalar_one()
        == 1
    )


def test_the_capture_call_carries_the_donations_own_idempotency_key(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    donation = order_for(db_session, live_case())
    sent = calls(paypal, "capture_order", monkeypatch)

    donation_service.capture_order(db_session, donation.paypal_order_id)

    assert sent[0][1]["request_id"] == f"capture-{donation.id}"


def test_a_capture_that_paypal_took_but_we_never_recorded_is_recovered_not_repeated(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    # The earlier attempt reached PayPal under another key, then our process died.
    paypal.capture_order(donation.paypal_order_id, request_id="lost-attempt")

    result = donation_service.capture_order(db_session, donation.paypal_order_id)

    assert result.fresh
    assert result.donation.status == DonationStatus.CAPTURED
    assert result.donation.net_usd_minor == 2397
    assert len(ledger_for(db_session, case)) == 3


def test_record_capture_does_nothing_the_second_time(db_session, paypal: MockPayPal) -> None:
    case = live_case()
    donation = donate(db_session, case)
    details = parsing.CaptureDetails(
        capture_id=donation.paypal_capture_id,
        status="COMPLETED",
        gross_usd_minor=2500,
        fee_usd_minor=103,
        net_usd_minor=2397,
    )

    assert (
        donation_service.record_capture(db_session, donation, case, details, actor=ActorType.PAYPAL)
        is False
    )

    assert len(ledger_for(db_session, case)) == 3
    assert case.donor_count == 1


def test_an_unknown_order_is_not_found(db_session, paypal: MockPayPal) -> None:
    with pytest.raises(NotFound) as caught:
        donation_service.capture_order(db_session, "NO-SUCH-ORDER")

    assert caught.value.code == "donation_not_found"


# --------------------------------------------------------------------------- capture: closed case


@pytest.mark.parametrize(
    "stop",
    ["funded", "closed", "frozen", "cancelled", "pledged_to_goal"],
)
def test_if_the_case_stopped_taking_money_nothing_is_captured_and_the_donor_is_not_charged(
    db_session, paypal: MockPayPal, monkeypatch, stop: str
) -> None:
    case = live_case(target_kes_minor=1_000_000, buffer_bps=0)
    donation = order_for(db_session, case)
    if stop == "pledged_to_goal":
        case.pledged_kes_minor = 1_000_000
    else:
        case.status = CaseStatus(stop)
        case.close_reason = "fully_settled" if stop == "closed" else None
        case.freeze_reason = "checking" if stop == "frozen" else None
    db_session.flush()
    sent = calls(paypal, "capture_order", monkeypatch)

    with pytest.raises(donation_service.CaseFullyFunded) as caught:
        donation_service.capture_order(db_session, donation.paypal_order_id)

    assert caught.value.code == "case_fully_funded"
    assert caught.value.status_code == 409
    assert "not been charged" in caught.value.message
    assert sent == []  # PayPal was never asked to take the money
    db_session.refresh(donation)
    assert donation.status == DonationStatus.NOT_CAPTURED_CASE_CLOSED
    assert donation.paypal_capture_id is None
    assert ledger_for(db_session, case) == []
    assert case.donor_count == 0
    assert "donation.not_captured" in audit_actions(db_session, donation.id)


def test_asking_again_after_a_refused_capture_is_refused_again_without_paypal(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    case.status = CaseStatus.CLOSED
    case.close_reason = "fully_settled"
    db_session.flush()
    sent = calls(paypal, "capture_order", monkeypatch)

    for _ in range(2):
        with pytest.raises(donation_service.CaseFullyFunded):
            donation_service.capture_order(db_session, donation.paypal_order_id)

    assert sent == []


def test_a_donor_who_ordered_before_the_case_filled_up_is_refused_at_capture(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case(target_kes_minor=100_000, buffer_bps=0)  # goal KES 1,000 = about $7.75
    first = order_for(db_session, case, "10.00")
    second = order_for(db_session, case, "10.00")

    donation_service.capture_order(db_session, first.paypal_order_id)
    assert case.status == CaseStatus.FUNDED
    with pytest.raises(donation_service.CaseFullyFunded):
        donation_service.capture_order(db_session, second.paypal_order_id)

    assert case.donor_count == 1


def test_money_paypal_already_took_is_recorded_even_after_the_case_closed(
    db_session, paypal: MockPayPal
) -> None:
    """The webhook path: the donor paid, then the page filled up. The money is real, so it is
    recorded; the excess policy decides later whether to refund it."""
    case = live_case()
    donation = order_for(db_session, case)
    case.status = CaseStatus.CLOSED
    case.close_reason = "fully_settled"
    db_session.flush()
    details = parsing.CaptureDetails("CAPLATE", "COMPLETED", 2500, 103, 2397, payer_email=None)

    assert donation_service.record_capture(
        db_session, donation, case, details, actor=ActorType.PAYPAL
    )

    assert donation.status == DonationStatus.CAPTURED
    assert case.status == CaseStatus.CLOSED
    assert case.captured_net_usd_minor == 2397
    assert len(ledger_for(db_session, case)) == 3


# ------------------------------------------------------------------- capture: PayPal says no


def test_a_declined_payment_method_leaves_the_order_open_for_another_try(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.fail_next("capture_order", issue="INSTRUMENT_DECLINED")

    with pytest.raises(PayPalError) as caught:
        donation_service.capture_order(db_session, donation.paypal_order_id)
    assert caught.value.code == "payment_declined"
    assert donation.status == DonationStatus.CREATED
    assert ledger_for(db_session, case) == []

    retry = donation_service.capture_order(db_session, donation.paypal_order_id)
    assert retry.donation.status == DonationStatus.CAPTURED


def test_an_order_the_donor_never_approved_cannot_be_captured(
    db_session, app, paypal: MockPayPal
) -> None:
    paypal.auto_approve = False
    donation = order_for(db_session, live_case())

    with pytest.raises(PayPalError) as caught:
        donation_service.capture_order(db_session, donation.paypal_order_id)

    assert caught.value.code == "order_not_approved"
    assert donation.status == DonationStatus.CREATED


def test_a_paypal_outage_leaves_the_donation_to_be_retried(db_session, paypal: MockPayPal) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.fail_next("capture_order", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError) as caught:
        donation_service.capture_order(db_session, donation.paypal_order_id)
    assert caught.value.retryable

    assert donation_service.capture_order(db_session, donation.paypal_order_id).fresh


def test_a_pending_payment_is_not_counted_until_it_clears(db_session, paypal: MockPayPal) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.next_capture_status("PENDING")

    with pytest.raises(donation_service.PaymentPending) as caught:
        donation_service.capture_order(db_session, donation.paypal_order_id)

    assert caught.value.code == "payment_pending"
    assert donation.status == DonationStatus.CREATED
    assert donation.paypal_capture_id is not None  # so the completed webhook can find it
    assert ledger_for(db_session, case) == []
    assert case.donor_count == 0


def test_a_declined_capture_marks_the_donation_denied_and_stays_denied(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.next_capture_status("DECLINED")

    with pytest.raises(donation_service.PaymentDeclined):
        donation_service.capture_order(db_session, donation.paypal_order_id)
    sent = calls(paypal, "capture_order", monkeypatch)
    with pytest.raises(donation_service.PaymentDeclined):
        donation_service.capture_order(db_session, donation.paypal_order_id)

    assert donation.status == DonationStatus.DENIED
    assert sent == []
    assert ledger_for(db_session, case) == []


def test_an_answer_without_a_capture_is_a_bad_response_and_changes_nothing(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    monkeypatch.setattr(paypal, "capture_order", lambda *a, **k: {"id": "x", "status": "COMPLETED"})

    with pytest.raises(PayPalError) as caught:
        donation_service.capture_order(db_session, donation.paypal_order_id)

    assert caught.value.code == "paypal_bad_response"
    assert donation.status == DonationStatus.CREATED
    assert ledger_for(db_session, case) == []


# --------------------------------------------------------------------------- receipts


def test_the_donor_is_emailed_a_receipt_with_the_ledger_link(
    db_session, app, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case)
    paypal.approve(donation.paypal_order_id, payer_email="receipts@example.test")
    mail_service.outbox.clear()

    donation_service.capture_order(db_session, donation.paypal_order_id)

    (message,) = mail_service.outbox
    assert message.to == "receipts@example.test"
    assert message.template == "donation_receipt"
    for amount in ("USD 25.00", "USD 1.03", "USD 23.97"):
        assert amount in message.text
    assert f"/c/{case.public_slug}#ledger" in message.text
    assert f"/receipt/{donation.receipt_token}" in message.text
    assert case.alias in message.text


def test_a_failed_receipt_email_never_undoes_the_donation(
    db_session, paypal: MockPayPal, monkeypatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise mail_service.MailError("down")

    monkeypatch.setattr(mail_service, "send", refuse)

    donation = donate(db_session, live_case())

    assert donation.status == DonationStatus.CAPTURED


def test_a_receipt_view_holds_the_donors_facts_and_no_private_data(
    db_session, paypal: MockPayPal
) -> None:
    case = live_case()
    donation = order_for(db_session, case, "25.00", name="Amina", anonymous=False)
    paypal.approve(donation.paypal_order_id, payer_email="private@example.test")
    donation_service.capture_order(db_session, donation.paypal_order_id)

    view = donation_service.receipt_view(db_session, donation)

    first_entry = ledger_for(db_session, case)[0]
    assert view["amount_usd_minor"] == 2500
    assert view["paypal_fee_usd_minor"] == 103
    assert view["net_usd_minor"] == 2397
    assert view["kes_estimate_minor"] == 2397 * 129
    assert view["fx_rate"] == "129.00000000"
    assert view["excess_choice"] == "refund"
    assert view["donor_display_name"] == "Amina"
    assert view["case_slug"] == case.public_slug
    assert view["ledger_hash_short"] == first_entry.hash[:12]
    assert view["ledger_reference"] == ledger_service.short_reference(donation.paypal_capture_id)
    flat = repr(view)
    assert "private@example.test" not in flat
    assert str(donation.id) not in flat
    assert donation.paypal_order_id not in flat
    assert first_entry.hash not in flat


def test_an_anonymous_receipt_names_nobody(db_session, paypal: MockPayPal) -> None:
    donation = order_for(db_session, live_case(), name="Amina", anonymous=True)
    donation_service.capture_order(db_session, donation.paypal_order_id)

    assert donation_service.receipt_view(db_session, donation)["donor_display_name"] is None


def test_only_a_donation_whose_money_was_taken_has_a_receipt(
    db_session, paypal: MockPayPal
) -> None:
    donation = order_for(db_session, live_case())

    with pytest.raises(NotFound) as caught:
        donation_service.get_receipt_or_404(db_session, donation.receipt_token)
    assert caught.value.code == "receipt_not_found"
    with pytest.raises(NotFound):
        donation_service.get_receipt_or_404(db_session, "not-a-token")

    donation_service.capture_order(db_session, donation.paypal_order_id)
    assert donation_service.get_receipt_or_404(db_session, donation.receipt_token).id == donation.id


def test_the_case_page_counts_the_donation_for_the_public(db_session, paypal: MockPayPal) -> None:
    case = live_case()
    donate(db_session, case, "25.00", name="Amina", anonymous=False)

    rows = ledger_service.public_rows(db_session, ledger_for(db_session, case))
    assert [row["donor_name"] for row in rows] == ["Amina", None, None]
    assert rows[0]["amount"] == "25.00"
