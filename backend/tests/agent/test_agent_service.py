"""What the agent may propose and what an administrator's decision does."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.errors import Conflict, NotFound, ValidationFailed
from app.extensions import db
from app.models.enums import (
    ActorType,
    AgentActionStatus,
    AgentActionType,
    CaseStatus,
    DonationStatus,
    RefundStatus,
    UserRole,
)
from app.models.money import Refund
from app.models.platform import AgentAction, AuditLog
from app.services import agent_service as agent
from tests.donation_helpers import donate, live_case, settleable_case
from tests.factories import CaseFactory, UserFactory

REASON = "The donor wrote to say they paid twice by mistake."


@pytest.fixture
def admin(db_session):  # type: ignore[no-untyped-def]
    user = UserFactory(role=UserRole.ADMIN, display_name="Admin Amina")
    db.session.commit()
    return user


@pytest.fixture
def held(db_session, paypal):  # type: ignore[no-untyped-def]
    """A live case holding one donation of $50: nothing is paid out, so all of it can go back."""
    case = live_case()
    donation = donate(db_session, case, "50.00")
    db.session.commit()
    return case, donation


def audit(session, action_name: str):  # type: ignore[no-untyped-def]
    return session.scalars(select(AuditLog).where(AuditLog.action == action_name)).all()


# --------------------------------------------------------------------------- proposing


def test_a_refund_proposal_is_written_down_and_nothing_else(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    case, donation = held

    action = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=1500, reason=REASON
    )

    assert action.status == AgentActionStatus.PROPOSED
    assert action.action_type == AgentActionType.REFUND
    assert action.case_id == case.id
    assert action.payload == {"donation_id": str(donation.id), "amount_usd_minor": 1500}
    assert action.rationale == REASON
    assert action.decided_by_id is None
    assert db_session.scalars(select(Refund)).all() == []  # no refund exists
    row = audit(db_session, "agent_action.proposed")[0]
    assert row.actor_type == ActorType.AGENT
    assert row.actor_user_id == admin.id


def test_asking_for_the_same_thing_twice_leaves_one_proposal(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    _, donation = held
    one = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=1500, reason=REASON
    )
    two = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=1500, reason=REASON + " Again."
    )
    other = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=1000, reason=REASON
    )

    assert one.id == two.id
    assert other.id != one.id
    assert len(db_session.scalars(select(AgentAction)).all()) == 2


@pytest.mark.parametrize(
    ("amount", "reason", "message"),
    [
        (0, REASON, "more than nothing"),
        (-5, REASON, "more than nothing"),
        (10_000_000, REASON, "still held"),
        (500, "too short", "at least 10"),
    ],
)
def test_a_refund_that_cannot_be_made_is_not_proposed(  # type: ignore[no-untyped-def]
    db_session, admin, held, amount, reason, message
) -> None:
    _, donation = held

    with pytest.raises(agent.ProposalRejected) as caught:
        agent.propose_refund(
            db_session, admin, donation_id=donation.id, amount_usd_minor=amount, reason=reason
        )

    assert message in caught.value.message
    assert db_session.scalars(select(AgentAction)).all() == []


def test_only_a_donation_that_holds_money_can_be_refunded(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    _, donation = held
    donation.status = DonationStatus.REFUNDED
    with pytest.raises(agent.ProposalRejected, match="refunded donation"):
        agent.propose_refund(
            db_session, admin, donation_id=donation.id, amount_usd_minor=100, reason=REASON
        )
    with pytest.raises(agent.ProposalRejected, match="no such donation"):
        agent.propose_refund(
            db_session, admin, donation_id=uuid.uuid4(), amount_usd_minor=100, reason=REASON
        )


def test_a_payout_is_proposed_only_for_a_case_that_can_be_paid(db_session, admin, paypal) -> None:  # type: ignore[no-untyped-def]
    settleable = settleable_case()
    draft = CaseFactory(status=CaseStatus.DRAFT)
    db.session.commit()

    ok = agent.propose_payout(db_session, admin, case_id=settleable.id, reason=REASON)
    assert ok.action_type == AgentActionType.PAYOUT
    assert agent.propose_payout(db_session, admin, case_id=settleable.id, reason=REASON).id == ok.id
    with pytest.raises(agent.ProposalRejected, match="draft case"):
        agent.propose_payout(db_session, admin, case_id=draft.id, reason=REASON)
    with pytest.raises(agent.ProposalRejected, match="no such case"):
        agent.propose_payout(db_session, admin, case_id=uuid.uuid4(), reason=REASON)


def test_a_freeze_is_proposed_only_for_a_case_that_can_be_frozen(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    case, _ = held
    draft = CaseFactory(status=CaseStatus.DRAFT)
    db.session.commit()

    ok = agent.propose_freeze(db_session, admin, case_id=case.id, reason=REASON)
    assert ok.action_type == AgentActionType.FREEZE
    with pytest.raises(agent.ProposalRejected, match="draft case"):
        agent.propose_freeze(db_session, admin, case_id=draft.id, reason=REASON)


# --------------------------------------------------------------------------- approving


def test_approving_a_refund_makes_it_through_the_normal_service(
    db_session, admin, held, paypal
) -> None:  # type: ignore[no-untyped-def]
    _, donation = held
    action = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=1500, reason=REASON
    )
    db.session.commit()

    done = agent.approve(db_session, action.id, admin)

    assert done.status == AgentActionStatus.EXECUTED
    assert done.decided_by_id == admin.id
    assert done.decided_at is not None
    assert done.executed_at is not None
    assert done.result["amount_usd_minor"] == 1500
    refund = db_session.get(Refund, uuid.UUID(done.result["refund_id"]))
    assert refund.status == RefundStatus.SUCCEEDED  # the inline queue ran it
    assert refund.requested_by == ActorType.AGENT
    db_session.refresh(donation)
    assert donation.refunded_usd_minor == 1500
    names = {row.action for row in db_session.scalars(select(AuditLog))}
    assert {
        "agent_action.proposed",
        "agent_action.approved",
        "agent_action.executed",
        "refund.created",
    } <= names
    created = audit(db_session, "refund.created")[0]
    assert created.actor_type == ActorType.AGENT


def test_approving_a_payout_runs_the_settlement_check(
    db_session, admin, paypal, manual_settlement
) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case(target_kes_minor=10_000_000)
    donate(db_session, case, "200.00")  # the check job is held back, so no tranche exists yet
    action = agent.propose_payout(db_session, admin, case_id=case.id, reason=REASON)
    db.session.commit()

    done = agent.approve(db_session, action.id, admin)

    assert done.status == AgentActionStatus.EXECUTED
    assert done.result["tranche_no"] == 1
    assert done.result["amount_usd_minor"] > 0
    assert ("app.jobs.tasks.execute_settlement", done.result["settlement_id"]) in manual_settlement


def test_a_payout_with_nothing_due_is_recorded_as_failed_not_executed(  # type: ignore[no-untyped-def]
    db_session, admin, paypal, manual_settlement
) -> None:
    case = settleable_case(target_kes_minor=10_000_000)
    action = agent.propose_payout(db_session, admin, case_id=case.id, reason=REASON)
    db.session.commit()

    done = agent.approve(db_session, action.id, admin)

    assert done.status == AgentActionStatus.FAILED
    assert "No tranche is due" in done.error
    assert done.decided_by_id == admin.id  # a person did decide
    assert done.executed_at is None


def test_approving_a_freeze_freezes_the_case_and_starts_the_refunds(
    db_session, admin, held, paypal
) -> None:  # type: ignore[no-untyped-def]
    case, donation = held
    action = agent.propose_freeze(db_session, admin, case_id=case.id, reason=REASON)
    db.session.commit()

    done = agent.approve(db_session, action.id, admin)

    assert done.status == AgentActionStatus.EXECUTED
    assert done.result == {"refunds": 1}
    db_session.refresh(case)
    db_session.refresh(donation)
    # the freeze refunds everyone and, once they are paid back, ends as a cancelled case
    assert case.status in (CaseStatus.FROZEN, CaseStatus.CANCELLED)
    assert donation.refunded_usd_minor == donation.net_usd_minor


def test_a_proposal_that_became_impossible_is_recorded_as_failed(
    db_session, admin, held, paypal
) -> None:  # type: ignore[no-untyped-def]
    _, donation = held
    action = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=4000, reason=REASON
    )
    db.session.commit()
    # meanwhile the money went back some other way
    donation.refunded_usd_minor = donation.net_usd_minor
    db.session.commit()

    done = agent.approve(db_session, action.id, admin)

    assert done.status == AgentActionStatus.FAILED
    assert done.error.startswith("refund_exceeds_balance")
    assert db_session.scalars(select(Refund)).all() == []
    assert audit(db_session, "agent_action.failed")


def test_an_action_is_decided_once(db_session, admin, held, paypal) -> None:  # type: ignore[no-untyped-def]
    _, donation = held
    action = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=500, reason=REASON
    )
    db.session.commit()
    agent.approve(db_session, action.id, admin)

    with pytest.raises(Conflict) as again:
        agent.approve(db_session, action.id, admin)
    with pytest.raises(Conflict):
        agent.reject(db_session, action.id, admin, "Changed my mind about this one.")

    assert again.value.code == "action_already_decided"
    assert len(db_session.scalars(select(Refund)).all()) == 1


def test_an_unknown_action_is_not_found(db_session, admin) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NotFound):
        agent.approve(db_session, uuid.uuid4(), admin)


def test_a_bug_while_carrying_out_an_action_does_not_leave_it_approved(  # type: ignore[no-untyped-def]
    db_session, admin, held, paypal, monkeypatch
) -> None:
    case, _ = held
    action = agent.propose_freeze(db_session, admin, case_id=case.id, reason=REASON)
    db.session.commit()

    def broken(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setitem(agent._EXECUTORS, AgentActionType.FREEZE, broken)  # noqa: SLF001

    with pytest.raises(RuntimeError):
        agent.approve(db_session, action.id, admin)

    db_session.refresh(action)
    assert action.status == AgentActionStatus.FAILED
    assert action.error.startswith("internal_error")


# --------------------------------------------------------------------------- rejecting


def test_rejecting_needs_a_reason_and_changes_nothing_else(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    _, donation = held
    action = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=500, reason=REASON
    )
    db.session.commit()

    with pytest.raises(ValidationFailed) as short:
        agent.reject(db_session, action.id, admin, "no")
    assert short.value.code == "note_required"
    assert action.status == AgentActionStatus.PROPOSED

    done = agent.reject(db_session, action.id, admin, "The donor has since said it was fine.")

    assert done.status == AgentActionStatus.REJECTED
    assert done.decided_by_id == admin.id
    assert done.result == {"note": "The donor has since said it was fine."}
    assert db_session.scalars(select(Refund)).all() == []
    assert audit(db_session, "agent_action.rejected")[0].actor_user_id == admin.id


def test_the_database_will_not_store_a_decision_without_a_person(db_session, held) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    case, _ = held
    row = AgentAction(
        action_type=AgentActionType.FREEZE,
        case_id=case.id,
        payload={},
        rationale=REASON,
        status=AgentActionStatus.EXECUTED,
    )
    db_session.add(row)

    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --------------------------------------------------------------------------- listing


def test_the_list_puts_waiting_proposals_first_and_filters_by_status(
    db_session, admin, held
) -> None:  # type: ignore[no-untyped-def]
    case, donation = held
    old = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=300, reason=REASON
    )
    agent.reject(db_session, old.id, admin, "Not needed after all, thank you.")
    waiting = agent.propose_refund(
        db_session, admin, donation_id=donation.id, amount_usd_minor=400, reason=REASON
    )
    db.session.commit()

    everything = db_session.scalars(agent.actions()).all()
    only_rejected = db_session.scalars(agent.actions(AgentActionStatus.REJECTED)).all()

    assert [a.id for a in everything] == [waiting.id, old.id]
    assert [a.id for a in only_rejected] == [old.id]
    assert Decimal(waiting.payload["amount_usd_minor"]) == 400
