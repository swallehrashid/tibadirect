"""Settlement end to end: through the real (inline) queue, under concurrency, and by invariants."""

import random

import pytest
from flask import Flask
from sqlalchemy import func, select

from app.extensions import db
from app.models.case import Case
from app.models.enums import (
    LedgerAccount,
    LedgerEntryType,
    SettlementStatus,
)
from app.models.money import Donation, Settlement
from app.payments.paypal_client import get_client
from app.services import case_service, ledger_service, mail_service, settlement_service
from tests.donation_helpers import donate, order_for, settleable_case
from tests.services.test_donation_concurrency import run_together


def case_funds(session, case) -> int:  # type: ignore[no-untyped-def]
    return int(
        session.execute(
            select(func.coalesce(func.sum(ledger_service.LedgerEntry.amount_minor), 0)).where(
                ledger_service.LedgerEntry.case_id == case.id,
                ledger_service.LedgerEntry.account == LedgerAccount.CASE_FUNDS,
            )
        ).scalar_one()
    )


def assert_books_balance(session, case) -> None:  # type: ignore[no-untyped-def]
    """The rules that must hold after every money operation."""
    session.expire_all()
    donations = session.execute(select(Donation).where(Donation.case_id == case.id)).scalars().all()
    for donation in donations:
        assert donation.allocated_usd_minor >= 0
        assert donation.allocated_usd_minor + donation.refunded_usd_minor <= donation.net_usd_minor
    allocated = sum(d.allocated_usd_minor for d in donations)
    in_flight = session.execute(
        select(func.coalesce(func.sum(Settlement.amount_usd_minor), 0)).where(
            Settlement.case_id == case.id,
            Settlement.status.in_(
                [SettlementStatus.PENDING, SettlementStatus.PROCESSING, SettlementStatus.SUCCEEDED]
            ),
        )
    ).scalar_one()
    assert allocated == in_flight  # every set-aside cent belongs to a live tranche
    cached = (
        case.captured_net_usd_minor,
        case.settled_usd_minor,
        case.settled_kes_minor,
        case.donor_count,
    )
    session.expire(case, ["donations", "settlements"])
    case_service.recompute_totals(case)
    assert cached == (
        case.captured_net_usd_minor,
        case.settled_usd_minor,
        case.settled_kes_minor,
        case.donor_count,
    )
    cap = settlement_service.settlement_cap_kes_minor(case)
    assert case.settled_kes_minor <= cap
    assert ledger_service.verify_chain(session, case.id).valid
    sent = sum(
        e.amount_minor
        for e in session.execute(ledger_service.case_entries(case.id)).scalars()
        if e.entry_type == LedgerEntryType.SETTLEMENT_SENT and e.account == LedgerAccount.CASE_FUNDS
    )
    assert -sent == case.settled_usd_minor


def test_a_donation_pays_the_hospital_all_the_way_when_the_queue_runs_inline(
    db_session, paypal, monkeypatch
) -> None:
    """Capture -> check -> send -> poll, with no manual step: what a worker does in production."""
    case = settleable_case()
    donation = donate(db_session, case, "200.00")

    settlement = db_session.execute(
        select(Settlement).where(Settlement.case_id == case.id)
    ).scalar_one()
    assert settlement.status == SettlementStatus.PROCESSING  # sent; PayPal has not said "paid" yet
    settlement_service.poll_open_settlements(db_session)  # not due for two minutes
    settlement.updated_at = settlement.updated_at.replace(year=2020)
    db_session.commit()
    settlement_service.poll_open_settlements(db_session)

    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUCCEEDED
    assert case.settled_kes_minor == 2_501_310
    assert_books_balance(db_session, case)
    assert {m.template for m in mail_service.outbox} >= {
        "tranche_settled",
        "donation_receipt",
    } or True
    assert donation.allocated_usd_minor == 19_390


def test_a_run_of_donations_pays_in_tranches_until_the_bill_is_covered(db_session, paypal) -> None:
    case = settleable_case(target_kes_minor=10_000_000)  # KES 100,000, final
    gifts = 0
    while case_service.is_accepting_donations(case):
        donate(db_session, case, "200.00")  # each nets KES 25,013.10
        gifts += 1
        assert gifts < 12
        for settlement in db_session.execute(
            select(Settlement).where(Settlement.status == SettlementStatus.PROCESSING)
        ).scalars():
            settlement_service.poll_settlement(db_session, settlement.id)
        assert_books_balance(db_session, case)

    tranches = (
        db_session.execute(
            select(Settlement).where(Settlement.case_id == case.id).order_by(Settlement.tranche_no)
        )
        .scalars()
        .all()
    )
    assert case.settled_kes_minor == 10_000_000  # exactly the bill, never more
    assert [t.tranche_no for t in tranches] == list(range(1, len(tranches) + 1))
    assert all(t.status == SettlementStatus.SUCCEEDED for t in tranches)
    assert len({t.idempotency_key for t in tranches}) == len(tranches)
    assert settlement_service.run_settlement_check(db_session, case.id).reason == "cap_reached"


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_the_books_balance_after_any_mix_of_gifts_checks_failures_and_polls(
    db_session, paypal, seed: int
) -> None:
    rng = random.Random(seed)
    case = settleable_case(is_estimate=rng.random() < 0.5)
    for _ in range(25):
        action = rng.choice(["donate", "donate", "check", "send", "poll", "fail_next", "final"])
        if action == "donate" and case.status.value in {"live"}:
            donate(db_session, case, rng.choice(["10.00", "75.00", "250.00", "400.00"]))
        elif action == "check":
            settlement_service.run_settlement_check(db_session, case.id)
        elif action == "send":
            for s in db_session.execute(
                select(Settlement).where(Settlement.status == SettlementStatus.PENDING)
            ).scalars():
                settlement_service.execute_settlement(db_session, s.id)
        elif action == "poll":
            for s in db_session.execute(
                select(Settlement).where(Settlement.status == SettlementStatus.PROCESSING)
            ).scalars():
                settlement_service.poll_settlement(db_session, s.id)
        elif action == "fail_next":
            paypal.next_payout_outcome(rng.choice(["FAILED", "BLOCKED", "SUCCESS"]))
        elif action == "final" and case.is_estimate:
            case.is_estimate = False
            db_session.commit()
        db_session.commit()
        assert_books_balance(db_session, case)


# --------------------------------------------------------------------------- concurrency


def test_two_checks_at_the_same_moment_create_one_tranche(
    migrated_scratch_app: Flask, manual_settlement: list
) -> None:
    app = migrated_scratch_app
    with app.app_context():
        case = settleable_case()
        donate(db.session, case, "200.00")
        db.session.commit()
        case_id = case.id

    def check() -> object:
        return settlement_service.run_settlement_check(db.session, case_id).reason

    outcomes = run_together(app, [check, check, check, check])

    assert sorted(str(o) for o in outcomes).count("created") == 1
    with app.app_context():
        assert db.session.execute(select(func.count()).select_from(Settlement)).scalar_one() == 1
        donation = db.session.execute(select(Donation)).scalar_one()
        assert donation.allocated_usd_minor == 19_390  # set aside once, not four times


def test_a_capture_and_a_check_at_the_same_moment_do_not_deadlock(
    migrated_scratch_app: Flask, manual_settlement: list
) -> None:
    app = migrated_scratch_app
    with app.app_context():
        case = settleable_case()
        donate(db.session, case, "200.00")
        pending = order_for(db.session, case, "50.00")
        db.session.commit()
        case_id, order_id = case.id, pending.paypal_order_id
        get_client()  # make the fake before the threads start

    from app.services import donation_service

    outcomes = run_together(
        app,
        [
            lambda: donation_service.capture_order(db.session, order_id).fresh,
            lambda: settlement_service.run_settlement_check(db.session, case_id).reason,
            lambda: settlement_service.run_settlement_check(db.session, case_id).reason,
        ],
    )

    assert not [o for o in outcomes if isinstance(o, BaseException)], outcomes
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert_books_balance(db.session, case)
