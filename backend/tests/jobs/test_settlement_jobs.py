"""The settlement jobs a worker runs."""

import uuid

import pytest

from app.jobs import tasks
from app.models.enums import SettlementStatus
from app.services import settlement_service
from tests.donation_helpers import donate, settleable_case


@pytest.fixture(autouse=True)
def _manual(manual_settlement: list) -> None:
    """Each job is called by hand in these tests."""


def test_the_check_job_takes_an_id_and_reports_what_it_did(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "10.00")
    assert tasks.run_settlement_check(str(case.id)) == "below_threshold"

    donate(db_session, case, "200.00")
    assert tasks.run_settlement_check(case.id) == "created"
    assert tasks.run_settlement_check(case.id) == "nothing_available"


def test_the_execute_job_sends_the_tranche(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement

    assert tasks.execute_settlement(str(settlement.id)) == "processing"
    assert tasks.execute_settlement(settlement.id) == "processing"  # a repeat sends nothing


def test_the_execute_job_for_an_unknown_tranche_is_none(db_session) -> None:
    assert tasks.execute_settlement(uuid.uuid4()) is None


def test_the_poll_job_follows_tranches_that_are_due(db_session, paypal) -> None:
    from datetime import UTC, datetime, timedelta

    case = settleable_case()
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    settlement.updated_at = datetime.now(UTC) - timedelta(minutes=3)
    db_session.commit()

    assert tasks.poll_payout_status() == 1

    db_session.refresh(settlement)
    assert settlement.status == SettlementStatus.SUCCEEDED


def test_the_sweep_job_reports_how_many_jobs_it_queued(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "10.00")

    assert tasks.sweep_settlements() == 1
