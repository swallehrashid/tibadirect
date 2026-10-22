"""The job that finishes a freeze, and the hourly job that now covers frozen cases."""

import uuid

import pytest
from sqlalchemy import select

from app.jobs import tasks
from app.models.enums import CaseStatus, CloseReason, UserRole
from app.models.money import Refund
from app.services import case_ending_service, refund_service
from tests.donation_helpers import donate, settleable_case
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _manual(manual_settlement: list, manual_refunds: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze follow-ups and refunds are run by hand."""
    monkeypatch.setattr(case_ending_service, "enqueue_finish", lambda case_id: None)
    monkeypatch.setattr(refund_service, "_finish_freeze_if_frozen", lambda case: None)


def test_the_finish_job_reports_where_the_freeze_stands(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00")
    case_ending_service.freeze_case(
        db_session, case, UserFactory(role=UserRole.ADMIN), "Suspected fraud"
    )
    db_session.commit()

    assert tasks.finish_frozen_case(str(case.id)) == "waiting"
    for refund in db_session.execute(select(Refund)).scalars():
        refund_service.execute_refund(db_session, refund.id)

    assert tasks.finish_frozen_case(case.id) == "cancelled"
    assert case.status == CaseStatus.CANCELLED
    assert case.close_reason == CloseReason.FRAUD
    assert tasks.finish_frozen_case(case.id) == "not_frozen"


def test_the_finish_job_for_an_unknown_case_is_an_error_the_queue_will_show(db_session) -> None:
    with pytest.raises(Exception, match="No row was found"):
        tasks.finish_frozen_case(uuid.uuid4())


def test_the_hourly_job_also_queues_frozen_cases(db_session, paypal, monkeypatch) -> None:
    queued: list = []
    monkeypatch.setattr(case_ending_service, "enqueue_finish", queued.append)
    case = settleable_case()
    case_ending_service.freeze_case(
        db_session, case, UserFactory(role=UserRole.ADMIN), "Suspected fraud"
    )
    db_session.commit()

    assert tasks.expire_follow_up_holds() == 1
    assert queued == [case.id]
