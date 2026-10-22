"""Simultaneous captures on real connections: money is taken once and never past the goal."""

import threading
from collections.abc import Callable

from flask import Flask
from sqlalchemy import func, select

from app.errors import AppError
from app.extensions import db
from app.models.case import Case
from app.models.enums import CaseStatus, DonationStatus
from app.models.money import Donation
from app.payments.paypal_client import get_client
from app.services import donation_service, ledger_service
from tests.donation_helpers import live_case, order_for


def run_together(app: Flask, jobs: list[Callable[[], object]]) -> list[object]:
    """Run each job in its own thread and connection, released at the same moment."""
    outcomes: list[object] = [None] * len(jobs)
    start = threading.Barrier(len(jobs))

    def worker(index: int) -> None:
        with app.app_context():
            try:
                start.wait(timeout=10)
                outcomes[index] = jobs[index]()
            except AppError as exc:
                outcomes[index] = exc
            except BaseException as exc:  # noqa: BLE001 - reported by the test below
                outcomes[index] = exc
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(jobs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes


def counting_captures(app: Flask) -> list[str]:
    """Wrap the fake's capture so the test can see how many times PayPal was asked."""
    with app.app_context():
        gateway = get_client()
    seen: list[str] = []
    original = gateway.capture_order

    def spy(order_id: str, *, request_id: str):  # type: ignore[no-untyped-def]
        seen.append(order_id)
        return original(order_id, request_id=request_id)

    gateway.capture_order = spy  # type: ignore[method-assign]
    return seen


def test_two_requests_capturing_one_order_take_the_money_once(
    migrated_scratch_app: Flask,
) -> None:
    app = migrated_scratch_app
    with app.app_context():
        case = live_case()
        donation = order_for(db.session, case)
        db.session.commit()
        order_id, case_id = donation.paypal_order_id, case.id
    seen = counting_captures(app)

    def capture() -> object:
        return donation_service.capture_order(db.session, order_id).fresh

    outcomes = run_together(app, [capture, capture, capture])

    assert sorted(outcomes) == [False, False, True]  # one did the work, two saw it done
    assert seen == [order_id]
    with app.app_context():
        assert db.session.execute(select(func.count()).select_from(Donation)).scalar_one() == 1
        entries = db.session.execute(ledger_service.case_entries(case_id)).scalars().all()
        assert len(entries) == 3
        assert ledger_service.verify_chain(db.session, case_id).valid
        case = db.session.get(Case, case_id)
        assert (case.captured_net_usd_minor, case.donor_count) == (2397, 1)


def test_two_donors_racing_for_the_last_room_cannot_both_be_charged(
    migrated_scratch_app: Flask,
) -> None:
    app = migrated_scratch_app
    with app.app_context():
        # The goal is KES 1,000.00, about $7.75: one $10 donation fills it, a second cannot fit.
        case = live_case(target_kes_minor=100_000, buffer_bps=0)
        first = order_for(db.session, case, "10.00")
        second = order_for(db.session, case, "10.00")
        db.session.commit()
        orders = [first.paypal_order_id, second.paypal_order_id]
        case_id = case.id
    seen = counting_captures(app)

    outcomes = run_together(
        app, [lambda o=o: donation_service.capture_order(db.session, o).fresh for o in orders]
    )

    refused = [o for o in outcomes if isinstance(o, donation_service.CaseFullyFunded)]
    assert len(refused) == 1
    assert [o for o in outcomes if o is True] == [True]
    assert len(seen) == 1  # PayPal was asked to take money once
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.status == CaseStatus.FUNDED
        assert case.donor_count == 1
        statuses = sorted(
            d.status.value for d in db.session.execute(select(Donation)).scalars().all()
        )
        assert statuses == sorted(
            [DonationStatus.CAPTURED.value, DonationStatus.NOT_CAPTURED_CASE_CLOSED.value]
        )
        assert ledger_service.verify_chain(db.session).valid
