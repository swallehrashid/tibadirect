"""Paying the hospital in tranches: when one is due, how it is paid, and when it fails."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.case import ReviewItem
from app.models.enums import (
    CaseStatus,
    ConfirmationStatus,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    ReviewSource,
    SettlementMethod,
    SettlementStatus,
    TrustLevel,
)
from app.models.money import Settlement, SettlementAllocation
from app.models.platform import AuditLog
from app.payments.errors import PayPalError
from app.services import case_service, ledger_service, mail_service, settlement_service
from tests.donation_helpers import calls, donate, settleable_case
from tests.factories import ConfirmationFactory

# Rate 129 KES per dollar, fee 2.9 % + 30 cents. A $200 gift nets $193.90 = KES 25,013.10.
BIG = "200.00"
BIG_NET_USD = 19_390
BIG_NET_KES = 2_501_310


@pytest.fixture(autouse=True)
def queued(manual_settlement: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Every test here drives the steps by hand; ``queued`` lists the jobs it would have run."""
    return manual_settlement


@pytest.fixture
def low_thresholds(app):  # type: ignore[no-untyped-def]
    """Tranches of any size: a tiny minimum and a tiny share of the bill."""
    saved = {k: app.config[k] for k in ("TRANCHE_MIN_KES", "TRANCHE_FRACTION_BPS")}
    app.config.update(TRANCHE_MIN_KES=1, TRANCHE_FRACTION_BPS=1)
    yield
    app.config.update(saved)


def settlements(session, case):  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(Settlement).where(Settlement.case_id == case.id).order_by(Settlement.tranche_no)
        ).scalars()
    )


def check(session, case):  # type: ignore[no-untyped-def]
    return settlement_service.run_settlement_check(session, case.id)


def pay(session, case):  # type: ignore[no-untyped-def]
    """A tranche made, sent and followed until the provider says it is paid."""
    result = check(session, case)
    assert result.settlement is not None, result.reason
    settlement_service.execute_settlement(session, result.settlement.id)
    settlement_service.poll_settlement(session, result.settlement.id)
    return result.settlement


# --------------------------------------------------------------------------- when nothing is due


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.DRAFT,
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.FROZEN,
        CaseStatus.CLOSED,
        CaseStatus.CANCELLED,
    ],
)
def test_only_live_and_funded_cases_settle(db_session, paypal, status: CaseStatus) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    case.status = status
    case.close_reason = "fully_settled" if status == CaseStatus.CLOSED else None
    case.freeze_reason = "checking" if status == CaseStatus.FROZEN else None
    db_session.commit()

    result = check(db_session, case)

    assert (result.settlement, result.reason) == (None, "case_not_settleable")


def test_a_case_the_hospital_has_not_confirmed_never_settles(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    case.trust_level = TrustLevel.PENDING_CONFIRMATION
    db_session.commit()

    assert check(db_session, case).reason == "case_not_settleable"


@pytest.mark.parametrize("status", [ConfirmationStatus.QUERIED, ConfirmationStatus.REJECTED])
def test_a_case_without_a_confirmed_bill_does_not_settle(db_session, paypal, status) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    for confirmation in case.confirmations:
        confirmation.status = status
    db_session.commit()

    assert check(db_session, case).reason == "no_confirmation"


def test_a_hospital_that_is_switched_off_is_not_paid(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    case.hospital.is_active = False
    db_session.commit()

    assert check(db_session, case).reason == "hospital_inactive"


def test_a_hospital_without_a_payout_email_cannot_be_paid_by_paypal(db_session, paypal) -> None:
    case = settleable_case(payout_email=None)
    donate(db_session, case, BIG)

    assert check(db_session, case).reason == "hospital_not_ready"


def test_the_unbuilt_partner_adapter_is_reported_not_attempted(db_session, paypal) -> None:
    case = settleable_case(method=SettlementMethod.JENGA)
    donate(db_session, case, BIG)

    assert check(db_session, case).reason == "hospital_not_ready"


def test_a_case_with_no_money_has_nothing_to_settle(db_session, paypal) -> None:
    assert check(db_session, settleable_case()).reason == "nothing_available"


def test_a_donation_below_the_tranche_threshold_waits(db_session, paypal) -> None:
    case = settleable_case()  # threshold: a quarter of KES 100,000 = KES 25,000
    donate(db_session, case, "100.00")  # KES 12,500: not enough yet

    result = check(db_session, case)

    assert (result.settlement, result.reason) == (None, "below_threshold")
    assert settlements(db_session, case) == []


# --------------------------------------------------------------------------- the first tranche


def test_money_over_the_threshold_creates_a_pending_tranche_and_queues_it(
    db_session, paypal, queued
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, BIG)
    assert queued == [("app.jobs.tasks.run_settlement_check", str(case.id))]  # the capture asks
    queued.clear()

    result = check(db_session, case)

    settlement = result.settlement
    assert result.reason == "created"
    assert settlement.status == SettlementStatus.PENDING
    assert (settlement.tranche_no, settlement.amount_usd_minor) == (1, BIG_NET_USD)
    assert settlement.amount_kes_minor == BIG_NET_KES
    assert settlement.adapter == SettlementMethod.PAYPAL_PAYOUTS
    assert settlement.fx_rate.rate == donation.fx_rate.rate
    assert settlement.confirmation_id == case.confirmations[0].id
    assert settlement.idempotency_key == f"tiba-settle-{settlement.id.hex}"
    assert queued == [("app.jobs.tasks.execute_settlement", str(settlement.id))]
    assert donation.allocated_usd_minor == BIG_NET_USD
    allocation = db_session.execute(select(SettlementAllocation)).scalar_one()
    assert (allocation.settlement_id, allocation.donation_id, allocation.amount_usd_minor) == (
        settlement.id,
        donation.id,
        BIG_NET_USD,
    )
    assert "settlement.created" in [
        a
        for (a,) in db_session.execute(
            select(AuditLog.action).where(AuditLog.target_id == settlement.id)
        )
    ]


def test_creating_a_tranche_moves_no_money_on_the_ledger_yet(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    before = len(db_session.execute(ledger_service.case_entries(case.id)).scalars().all())

    check(db_session, case)

    assert len(db_session.execute(ledger_service.case_entries(case.id)).scalars().all()) == before
    assert case.settled_kes_minor == 0


def test_asking_twice_does_not_pay_the_same_money_twice(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)

    first = check(db_session, case)
    second = check(db_session, case)

    assert first.reason == "created"
    assert second.reason == "nothing_available"
    assert len(settlements(db_session, case)) == 1


def test_the_deposit_the_hospital_asked_for_is_paid_first_even_though_it_is_small(
    db_session, paypal
) -> None:
    case = settleable_case(deposit_kes_minor=500_000)  # KES 5,000
    donate(db_session, case, "50.00")  # nets KES 6,224.25: over the deposit, under the usual 25 %

    result = check(db_session, case)

    assert result.reason == "created"
    assert result.settlement.amount_usd_minor == 4_825  # everything available, not only the deposit


def test_without_a_deposit_the_same_money_waits(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "50.00")

    assert check(db_session, case).reason == "below_threshold"


def test_the_deposit_waits_until_it_is_covered(db_session, paypal) -> None:
    case = settleable_case(deposit_kes_minor=500_000)
    donate(db_session, case, "20.00")  # KES 2,000: under the deposit

    assert check(db_session, case).reason == "below_threshold"


def test_after_the_deposit_tranche_the_usual_threshold_applies_again(db_session, paypal) -> None:
    case = settleable_case(deposit_kes_minor=500_000)
    donate(db_session, case, "50.00")
    pay(db_session, case)

    donate(db_session, case, "50.00")  # another KES 6,224: over the old deposit, under 25 %

    assert check(db_session, case).reason == "below_threshold"


def test_a_failed_deposit_tranche_does_not_count_as_the_first(db_session, paypal) -> None:
    case = settleable_case(deposit_kes_minor=500_000)
    donate(db_session, case, "50.00")
    paypal.next_payout_outcome("FAILED")
    pay(db_session, case)
    assert settlements(db_session, case)[0].status == SettlementStatus.FAILED

    assert check(db_session, case).reason == "created"  # the deposit rule still applies


# ----------------------------------------------------------------- how much, and from whom


def test_the_tranche_draws_on_the_oldest_donations_first(
    db_session, paypal, low_thresholds
) -> None:
    case = settleable_case(target_kes_minor=200_000, is_estimate=True)  # cap 80 % = KES 1,600
    older = donate(db_session, case, "10.00")  # nets $9.41
    newer = donate(db_session, case, "20.00")  # nets $19.12
    older.captured_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()

    result = check(db_session, case)

    assert result.settlement.amount_kes_minor == 160_000
    assert result.settlement.amount_usd_minor == 1_241  # KES 1,600 rounded up to whole cents
    db_session.refresh(older)
    db_session.refresh(newer)
    assert (older.allocated_usd_minor, newer.allocated_usd_minor) == (941, 300)
    allocated = {
        a.donation_id: a.amount_usd_minor
        for a in db_session.execute(select(SettlementAllocation)).scalars()
    }
    assert allocated == {older.id: 941, newer.id: 300}


def test_the_next_tranche_only_uses_what_is_left(db_session, paypal, low_thresholds) -> None:
    case = settleable_case(target_kes_minor=10_000_000, is_estimate=True)
    donation = donate(db_session, case, "1000.00")  # nets $970.70
    first = check(db_session, case).settlement
    donation.captured_at = datetime.now(UTC)
    case.is_estimate = False  # the final bill is confirmed
    db_session.commit()

    second = check(db_session, case).settlement

    assert first.amount_kes_minor == 8_000_000  # the 80 % estimate cap
    # only what the bill still needs, with the cents rounded up so the last tranche is exact
    assert second.amount_usd_minor == 15_504
    assert second.amount_kes_minor == 2_000_000  # the rest of the bill, to the cent
    db_session.refresh(donation)
    assert donation.allocated_usd_minor <= donation.net_usd_minor


def test_a_donation_part_refunded_only_offers_what_remains(
    db_session, paypal, low_thresholds
) -> None:
    case = settleable_case(target_kes_minor=10_000_000)
    donation = donate(db_session, case, "100.00")  # nets $96.80 = 9,680 cents
    donation.refunded_usd_minor = 4_000
    db_session.commit()

    assert check(db_session, case).settlement.amount_usd_minor == 5_680


def test_refunded_and_reversed_donations_hold_nothing(db_session, paypal, low_thresholds) -> None:
    case = settleable_case()
    refunded = donate(db_session, case, "100.00")
    reversed_ = donate(db_session, case, "100.00")
    refunded.status = DonationStatus.REFUNDED
    refunded.refunded_usd_minor = refunded.net_usd_minor
    reversed_.status = DonationStatus.REVERSED
    db_session.commit()

    assert check(db_session, case).reason == "nothing_available"


# --------------------------------------------------------------------------- the estimate cap


def test_while_the_bill_is_an_estimate_no_more_than_80_percent_is_paid(db_session, paypal) -> None:
    case = settleable_case(is_estimate=True)  # KES 100,000 estimate: cap KES 80,000
    donate(db_session, case, "1000.00")  # nets KES 125,210: far more than the cap

    settlement = pay(db_session, case)

    assert settlement.amount_kes_minor == 8_000_000
    assert case.settled_kes_minor == 8_000_000
    assert check(db_session, case).reason == "cap_reached"


def test_tranches_still_in_flight_count_against_the_cap(db_session, paypal) -> None:
    case = settleable_case(is_estimate=True)
    donate(db_session, case, "1000.00")
    first = check(db_session, case)  # created but not yet sent

    second = check(db_session, case)

    assert first.reason == "created"
    assert second.reason == "cap_reached"
    assert sum(s.amount_kes_minor for s in settlements(db_session, case)) == 8_000_000


def test_a_final_bill_lifts_the_cap_and_the_rest_is_paid(db_session, paypal) -> None:
    case = settleable_case(is_estimate=True)
    donate(db_session, case, "1000.00")
    pay(db_session, case)
    case.is_estimate = False  # the hospital confirmed a final bill of the same amount
    db_session.commit()

    last = pay(db_session, case)

    assert last.amount_kes_minor == 2_000_000
    assert case.settled_kes_minor == 10_000_000
    assert [s.tranche_no for s in settlements(db_session, case)] == [1, 2]


def test_a_failed_tranche_does_not_use_up_the_cap(db_session, paypal) -> None:
    case = settleable_case(is_estimate=True)
    donate(db_session, case, "1000.00")
    paypal.next_payout_outcome("FAILED")
    pay(db_session, case)

    assert check(db_session, case).reason == "created"


# --------------------------------------------------------------------------- sending


def test_sending_starts_the_paypal_payout_with_the_settlements_own_key(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    sent = calls(paypal, "create_payout", monkeypatch)

    status = settlement_service.execute_settlement(db_session, settlement.id)

    assert status == SettlementStatus.PROCESSING
    ((_, kwargs),) = sent
    assert kwargs["sender_batch_id"] == settlement.idempotency_key
    assert kwargs["request_id"] == settlement.idempotency_key
    (item,) = kwargs["items"]
    assert item.receiver_email == "payouts@hospital.test"
    assert item.amount_usd_minor == BIG_NET_USD
    assert item.sender_item_id == settlement.id.hex[:30]
    assert len(item.sender_item_id) == 30
    assert item.note == f"TibaDirect tranche 1 for invoice {case.confirmations[0].invoice_number}"
    assert settlement.external_batch_id.startswith("MOCKBATCH")
    assert settlement.completed_at is None


def test_the_note_to_paypal_holds_no_clinical_or_personal_text(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    sent = calls(paypal, "create_payout", monkeypatch)

    settlement_service.execute_settlement(db_session, settlement.id)

    text = repr(sent[0][1])
    assert case.alias not in text
    assert case.created_by.email not in text


def test_a_tranche_that_has_been_sent_is_not_sent_again(db_session, paypal, monkeypatch) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    sent = calls(paypal, "create_payout", monkeypatch)

    again = settlement_service.execute_settlement(db_session, settlement.id)

    assert again == SettlementStatus.PROCESSING
    assert sent == []


def test_an_unknown_settlement_is_none(db_session, paypal) -> None:
    import uuid

    assert settlement_service.execute_settlement(db_session, uuid.uuid4()) is None
    assert settlement_service.poll_settlement(db_session, uuid.uuid4()) is None


def test_a_paypal_outage_leaves_the_tranche_unsent_and_a_retry_uses_the_same_key(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    paypal.fail_next("create_payout", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError) as caught:
        settlement_service.execute_settlement(db_session, settlement.id)
    assert caught.value.retryable
    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.PENDING
    assert settlement.external_batch_id is None
    db_session.refresh(donation)
    assert donation.allocated_usd_minor == BIG_NET_USD  # the money stays set aside

    sent = calls(paypal, "create_payout", monkeypatch)
    settlement_service.execute_settlement(db_session, settlement.id)
    assert sent[0][1]["request_id"] == settlement.idempotency_key
    assert settlement.status == SettlementStatus.PROCESSING


def test_paypal_pays_once_however_many_times_the_send_is_repeated(db_session, paypal) -> None:
    """The mock refuses a second batch under the same ID, as PayPal does."""
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    settlement.status = SettlementStatus.PENDING  # as if the answer had been lost
    db_session.commit()

    settlement_service.execute_settlement(db_session, settlement.id)

    assert settlement.status == SettlementStatus.PROCESSING
    assert len(paypal.store._data) >= 1  # type: ignore[attr-defined]


def test_a_misconfigured_account_stops_the_send_but_keeps_the_tranche(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    paypal.fail_next("create_payout", http_status=401, name="AUTHENTICATION_FAILURE")

    with pytest.raises(PayPalError) as caught:
        settlement_service.execute_settlement(db_session, settlement.id)

    assert caught.value.code == "paypal_auth_failed"
    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.PENDING


def test_a_case_frozen_before_sending_cancels_the_tranche_and_frees_the_money(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    case.status = CaseStatus.FROZEN
    case.freeze_reason = "checking something"
    db_session.commit()
    sent = calls(paypal, "create_payout", monkeypatch)

    status = settlement_service.execute_settlement(db_session, settlement.id)

    assert status == SettlementStatus.FAILED
    assert sent == []
    db_session.refresh(settlement)
    db_session.refresh(donation)
    assert settlement.failure_reason.startswith("Cancelled")
    assert donation.allocated_usd_minor == 0
    assert (
        db_session.execute(select(func.count()).select_from(SettlementAllocation)).scalar_one() == 0
    )
    assert db_session.execute(select(ReviewItem)).first() is None  # expected, so no review


# ----------------------------------------------------------------- following it to the end


def test_a_paid_tranche_reaches_the_ledger_the_totals_and_both_inboxes(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    mail_service.outbox.clear()

    status = settlement_service.poll_settlement(db_session, settlement.id)

    assert status == SettlementStatus.SUCCEEDED
    assert settlement.completed_at is not None
    assert settlement.external_item_id.startswith("MOCKITEM")
    entries = db_session.execute(ledger_service.case_entries(case.id)).scalars().all()
    paid = [e for e in entries if e.entry_type == LedgerEntryType.SETTLEMENT_SENT]
    assert [(e.account, e.amount_minor, e.currency) for e in paid] == [
        (LedgerAccount.CASE_FUNDS, -BIG_NET_USD, "USD"),
        (LedgerAccount.HOSPITAL_SETTLED, BIG_NET_KES, "KES"),
    ]
    assert {e.ref_id for e in paid} == {settlement.id}
    held = sum(e.amount_minor for e in entries if e.account == LedgerAccount.CASE_FUNDS)
    assert held == donation.net_usd_minor - BIG_NET_USD == 0
    assert (case.settled_usd_minor, case.settled_kes_minor) == (BIG_NET_USD, BIG_NET_KES)
    assert ledger_service.verify_chain(db_session, case.id).valid
    recipients = sorted(m.to for m in mail_service.outbox)
    assert recipients == sorted([case.created_by.email, case.hospital.official_email])
    assert {m.template for m in mail_service.outbox} == {"tranche_settled"}


def test_the_emails_name_the_tranche_label_the_simulation_and_hold_no_clinical_text(
    db_session, paypal
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    pay(db_session, case)

    family = next(m for m in mail_service.outbox if m.to == case.created_by.email)
    hospital = next(m for m in mail_service.outbox if m.to == case.hospital.official_email)
    assert "Payment 1: KES 25,013.10" in family.text
    assert "Settlement simulated via PayPal Payouts" in family.text
    assert "reached the hospital" in family.text
    assert "has been sent to your hospital" in hospital.text
    assert case.confirmations[0].invoice_number in hospital.text
    assert f"/c/{case.public_slug}" in family.text


def test_the_cached_totals_equal_a_recount_after_a_payment(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    pay(db_session, case)
    cached = (
        case.captured_net_usd_minor,
        case.pledged_kes_minor,
        case.settled_usd_minor,
        case.settled_kes_minor,
        case.donor_count,
    )

    db_session.expire(case, ["donations", "settlements"])
    case_service.recompute_totals(case)

    assert cached == (
        case.captured_net_usd_minor,
        case.pledged_kes_minor,
        case.settled_usd_minor,
        case.settled_kes_minor,
        case.donor_count,
    )


def test_polling_a_tranche_that_is_not_ready_changes_nothing_but_the_check_time(
    db_session, paypal
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    paypal.next_payout_outcome("PENDING")
    settlement = check(db_session, case).settlement
    settlement_service.execute_settlement(db_session, settlement.id)

    assert (
        settlement_service.poll_settlement(db_session, settlement.id) == SettlementStatus.PROCESSING
    )

    assert case.settled_kes_minor == 0
    assert (
        db_session.execute(
            select(func.count())
            .select_from(Settlement)
            .where(Settlement.status == SettlementStatus.PROCESSING)
        ).scalar_one()
        == 1
    )


def test_polling_a_tranche_not_yet_sent_or_already_done_does_nothing(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement

    assert settlement_service.poll_settlement(db_session, settlement.id) == SettlementStatus.PENDING
    settlement_service.execute_settlement(db_session, settlement.id)
    settlement_service.poll_settlement(db_session, settlement.id)
    mail_service.outbox.clear()

    assert (
        settlement_service.poll_settlement(db_session, settlement.id) == SettlementStatus.SUCCEEDED
    )
    assert mail_service.outbox == []  # nobody is told twice


def test_the_simulated_partner_pays_the_hospital_without_paypal(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case(method=SettlementMethod.MOCK_PARTNER, payout_email=None)
    donate(db_session, case, BIG)
    sent = calls(paypal, "create_payout", monkeypatch)

    settlement = pay(db_session, case)

    assert sent == []
    assert settlement.status == SettlementStatus.SUCCEEDED
    assert settlement.external_batch_id.startswith("MOCKPARTNER-")
    assert settlement.adapter == SettlementMethod.MOCK_PARTNER


def test_the_simulated_partner_takes_its_configured_time(db_session, app, paypal) -> None:
    app.config["MOCK_PARTNER_DELAY_SECONDS"] = 3600
    try:
        case = settleable_case(method=SettlementMethod.MOCK_PARTNER, payout_email=None)
        donate(db_session, case, BIG)
        settlement = check(db_session, case).settlement
        settlement_service.execute_settlement(db_session, settlement.id)

        assert (
            settlement_service.poll_settlement(db_session, settlement.id)
            == SettlementStatus.PROCESSING
        )
    finally:
        app.config["MOCK_PARTNER_DELAY_SECONDS"] = 0


# --------------------------------------------------------------------------- when it goes wrong


@pytest.mark.parametrize("outcome", ["FAILED", "BLOCKED", "RETURNED"])
def test_a_payout_that_does_not_arrive_frees_the_money_and_asks_an_admin_to_look(
    db_session, paypal, outcome: str
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, BIG)
    paypal.next_payout_outcome(outcome)

    settlement = pay(db_session, case)

    assert settlement.status == SettlementStatus.FAILED
    assert settlement.failure_reason
    assert settlement.completed_at is not None
    db_session.refresh(donation)
    assert donation.allocated_usd_minor == 0
    assert (
        db_session.execute(select(func.count()).select_from(SettlementAllocation)).scalar_one() == 0
    )
    assert case.settled_kes_minor == 0
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert (item.case_id, item.source) == (case.id, ReviewSource.ADMIN)
    assert item.payload["settlement_id"] == str(settlement.id)
    assert not [
        e
        for e in db_session.execute(ledger_service.case_entries(case.id)).scalars()
        if e.entry_type == LedgerEntryType.SETTLEMENT_SENT
    ]
    assert mail_service.outbox == [] or all(
        m.template != "tranche_settled" for m in mail_service.outbox
    )


def test_after_a_failure_the_next_check_makes_a_new_tranche_with_a_new_key(
    db_session, paypal
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    paypal.next_payout_outcome("FAILED")
    failed = pay(db_session, case)

    retry = pay(db_session, case)

    assert retry.id != failed.id
    assert (retry.tranche_no, failed.tranche_no) == (2, 1)
    assert retry.idempotency_key != failed.idempotency_key
    assert retry.status == SettlementStatus.SUCCEEDED
    assert case.settled_kes_minor == BIG_NET_KES


def test_paypal_refusing_for_lack_of_funds_fails_the_tranche_for_review(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    paypal.fail_next("create_payout", http_status=400, name="INSUFFICIENT_FUNDS")

    status = settlement_service.execute_settlement(db_session, settlement.id)

    assert status == SettlementStatus.FAILED
    assert settlement.failure_reason == "paypal_insufficient_funds"
    db_session.refresh(donation)
    assert donation.allocated_usd_minor == 0
    assert (
        db_session.execute(select(ReviewItem)).scalar_one().payload["reason"]
        == "paypal_insufficient_funds"
    )


def test_the_failure_reason_is_scrubbed_and_cut_short(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement

    settlement_service._fail(  # noqa: SLF001
        db_session, case, settlement, "jane@example.test " + "x" * 400, None
    )

    assert "jane@example.test" not in settlement.failure_reason
    assert len(settlement.failure_reason) <= 300


def test_a_provider_report_that_contradicts_a_closed_tranche_opens_a_review_only(
    db_session, paypal
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = pay(db_session, case)
    assert settlement.status == SettlementStatus.SUCCEEDED
    batch = paypal.store.get("payout", settlement.external_batch_id)
    batch["outcome"] = "RETURNED"  # the hospital's account sent the money back
    paypal.store.put("payout", settlement.external_batch_id, batch)
    mail_service.outbox.clear()

    status = settlement_service.poll_settlement(db_session, settlement.id, include_finished=True)

    assert status == SettlementStatus.SUCCEEDED
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.payload["our_status"] == "succeeded"
    assert item.payload["provider_status"] == "failed"
    assert case.settled_kes_minor == BIG_NET_KES  # unchanged: a person decides
    assert mail_service.outbox == []


def test_a_tranche_the_provider_calls_paid_after_we_failed_it_opens_a_review_only(
    db_session, paypal
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    paypal.next_payout_outcome("FAILED")
    settlement = pay(db_session, case)
    batch = paypal.store.get("payout", settlement.external_batch_id)
    batch["outcome"] = "SUCCESS"
    paypal.store.put("payout", settlement.external_batch_id, batch)

    status = settlement_service.poll_settlement(db_session, settlement.id, include_finished=True)

    assert status == SettlementStatus.FAILED
    assert db_session.execute(select(func.count()).select_from(ReviewItem)).scalar_one() == 2


# --------------------------------------------------------------------------- polling and sweeping


def test_open_tranches_are_polled_at_a_growing_interval() -> None:
    assert settlement_service.poll_interval(timedelta(minutes=3)) == timedelta(minutes=2)
    assert settlement_service.poll_interval(timedelta(minutes=30)) == timedelta(minutes=10)
    assert settlement_service.poll_interval(timedelta(hours=5)) == timedelta(minutes=30)


def test_polling_the_open_tranches_asks_only_those_that_are_due(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    settlement.updated_at = datetime.now(UTC)  # just asked
    db_session.commit()
    assert settlement_service.poll_open_settlements(db_session) == 0

    settlement.updated_at = datetime.now(UTC) - timedelta(minutes=3)
    db_session.commit()

    assert settlement_service.poll_open_settlements(db_session) == 1
    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUCCEEDED


def test_one_tranche_that_cannot_be_asked_does_not_stop_the_others(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    settlement = check(db_session, case).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    settlement.updated_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()
    paypal.fail_next("get_payout", http_status=503, name="SERVICE_UNAVAILABLE")

    assert settlement_service.poll_open_settlements(db_session) == 0
    assert settlement_service.poll_open_settlements(db_session) == 1


def test_a_tranche_still_open_after_three_days_is_flagged_once(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    paypal.next_payout_outcome("PENDING")
    settlement = check(db_session, case).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    settlement.requested_at = datetime.now(UTC) - timedelta(days=4)
    settlement.updated_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()

    settlement_service.poll_open_settlements(db_session)
    settlement.updated_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()
    settlement_service.poll_open_settlements(db_session)

    assert db_session.execute(select(func.count()).select_from(ReviewItem)).scalar_one() == 1


def test_the_sweep_requeues_unsent_tranches_and_checks_cases_holding_money(
    db_session, paypal, queued
) -> None:
    waiting = settleable_case()
    donate(db_session, waiting, BIG)
    stuck = check(db_session, waiting).settlement
    stuck.created_at = datetime.now(UTC) - timedelta(minutes=10)
    holding = settleable_case()
    donate(db_session, holding, "10.00")
    empty = settleable_case()
    db_session.commit()
    queued.clear()

    count = settlement_service.sweep(db_session)

    jobs = sorted(queued)
    assert ("app.jobs.tasks.execute_settlement", str(stuck.id)) in jobs
    assert ("app.jobs.tasks.run_settlement_check", str(holding.id)) in jobs
    assert ("app.jobs.tasks.run_settlement_check", str(empty.id)) not in jobs
    assert count == len(jobs)


def test_a_just_created_tranche_is_left_for_its_own_job(db_session, paypal, queued) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    check(db_session, case)
    queued.clear()

    settlement_service.sweep(db_session)

    assert ("app.jobs.tasks.execute_settlement") not in [func for func, _ in queued]


def test_a_queue_outage_is_logged_not_raised(db_session, paypal, monkeypatch) -> None:
    from redis.exceptions import RedisError

    def down(*_a, **_k):  # type: ignore[no-untyped-def]
        raise RedisError("down")

    monkeypatch.undo()  # the autouse stub off, then the real queue made to fail
    monkeypatch.setattr(settlement_service.jobs, "enqueue", down)

    settlement_service.enqueue_check(settleable_case().id)  # does not raise


def test_a_confirmed_bill_from_another_case_does_not_unlock_this_one(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, BIG)
    for confirmation in list(case.confirmations):
        confirmation.status = ConfirmationStatus.QUERIED
    ConfirmationFactory(status=ConfirmationStatus.CONFIRMED)  # some other case's bill
    db_session.commit()

    assert check(db_session, case).reason == "no_confirmation"
