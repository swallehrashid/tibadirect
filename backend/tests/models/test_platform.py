from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.enums import (
    ActorType,
    AgentActionStatus,
    AgentActionType,
    AppealInputKind,
    RiskBand,
)
from app.models.platform import AppealCheck
from tests.factories import (
    AgentActionFactory,
    AppealCheckFactory,
    AuditLogFactory,
    CaseFactory,
    UserFactory,
)
from tests.models.helpers import assert_db_rejects

# ---- Appeal checks ----


def test_appeal_check_defaults_and_optional_match(db_session) -> None:
    check = AppealCheckFactory()
    db_session.refresh(check)
    assert check.matched_case_id is None
    assert check.matched_case is None
    assert check.input_kind is AppealInputKind.TEXT
    assert check.created_at is not None


def test_the_appeal_text_has_nowhere_to_be_stored() -> None:
    columns = set(AppealCheck.__table__.c.keys())
    assert columns == {
        "id",
        "created_at",
        "input_hmac",
        "input_kind",
        "risk_score",
        "risk_band",
        "reasons_en",
        "reasons_sw",
        "matched_case_id",
        "used_llm",
    }


def test_appeal_check_links_to_a_matched_case_both_ways(db_session) -> None:
    case = CaseFactory()
    check = AppealCheckFactory(matched_case=case, risk_score=5, risk_band=RiskBand.LOW)
    db_session.flush()
    assert check in case.appeal_checks
    assert check.matched_case is case


@pytest.mark.parametrize("score", [-1, 101])
def test_appeal_risk_score_range(db_session, score: int) -> None:
    error = assert_db_rejects(db_session, lambda: AppealCheckFactory(risk_score=score))
    assert "ck_appeal_checks_risk_score_range" in str(error)


def test_input_hash_is_fixed_width(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: AppealCheckFactory(input_hmac="a" * 65))
    assert isinstance(error, DataError)


def test_reasons_are_required(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: AppealCheckFactory(reasons_en=None))
    assert isinstance(error, IntegrityError)


def test_appeal_input_hash_is_indexed(db_session) -> None:
    names = set(
        db_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'appeal_checks'")
        ).scalars()
    )
    assert "ix_appeal_checks_input_hmac" in names


def test_appeal_repr_does_not_show_reasons(db_session) -> None:
    check = AppealCheckFactory(reasons_en=["secret reason text"])
    assert "secret" not in repr(check)


# ---- Audit log ----


def test_audit_defaults(db_session) -> None:
    entry = AuditLogFactory()
    db_session.refresh(entry)
    assert entry.details == {}
    assert entry.actor_user_id is None
    assert entry.request_id is None
    assert entry.ip_hmac is None


def test_audit_enum_is_lowercase_native(db_session) -> None:
    AuditLogFactory(actor_type=ActorType.PAYPAL)
    assert db_session.execute(text("SELECT actor_type::text FROM audit_log")).scalar_one() == (
        "paypal"
    )


def test_audit_can_record_a_user_actor_and_details(db_session) -> None:
    admin = UserFactory()
    entry = AuditLogFactory(
        actor_type=ActorType.USER,
        actor_user_id=admin.id,
        action="case.frozen",
        details={"reason_code": "fraud_suspected"},
        request_id="6f1c2d3e-1234-5678-9abc-def012345678",
    )
    db_session.flush()
    db_session.expire(entry)
    assert entry.actor_user_id == admin.id
    assert entry.details == {"reason_code": "fraud_suspected"}


def test_audit_action_and_target_are_indexed(db_session) -> None:
    rows = db_session.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'audit_log'")
    ).all()
    defs = dict(rows)
    assert "ix_audit_log_action" in defs
    assert "(target_type, target_id)" in defs["ix_audit_log_target"]


def test_audit_request_id_is_limited_to_a_uuid_length(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: AuditLogFactory(request_id="x" * 37))
    assert isinstance(error, DataError)


def test_audit_actor_user_cannot_be_deleted_while_referenced(db_session) -> None:
    admin = UserFactory()
    AuditLogFactory(actor_user_id=admin.id)
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: db_session.delete(admin))
    assert isinstance(error, IntegrityError)


def test_audit_target_may_be_absent(db_session) -> None:
    assert AuditLogFactory(target_id=None).target_id is None


def test_audit_repr_hides_details(db_session) -> None:
    entry = AuditLogFactory(details={"note": "private"})
    assert "private" not in repr(entry)
    assert "case.submitted" in repr(entry)


# ---- Agent actions ----


def test_agent_action_defaults(db_session) -> None:
    action = AgentActionFactory()
    db_session.refresh(action)
    assert action.status is AgentActionStatus.PROPOSED
    assert action.decided_by_id is None
    assert action.decided_at is None
    assert action.executed_at is None
    assert action.result is None
    assert action.error is None


def test_agent_action_links_to_its_case_both_ways(db_session) -> None:
    action = AgentActionFactory()
    db_session.flush()
    assert action in action.case.agent_actions


def test_an_action_may_have_no_case(db_session) -> None:
    assert AgentActionFactory(case=None).case_id is None


def test_agent_action_enums_are_lowercase_native(db_session) -> None:
    AgentActionFactory(action_type=AgentActionType.FREEZE)
    row = db_session.execute(text("SELECT action_type::text, status::text FROM agent_actions"))
    assert tuple(row.one()) == ("freeze", "proposed")


def test_rationale_and_payload_are_required(db_session) -> None:
    assert isinstance(
        assert_db_rejects(db_session, lambda: AgentActionFactory(payload=None)), IntegrityError
    )


@pytest.mark.parametrize(
    "status",
    [
        AgentActionStatus.APPROVED,
        AgentActionStatus.REJECTED,
        AgentActionStatus.EXECUTED,
        AgentActionStatus.FAILED,
    ],
)
def test_nothing_past_proposed_exists_without_a_human_decision(db_session, status) -> None:
    error = assert_db_rejects(db_session, lambda: AgentActionFactory(status=status))
    assert "ck_agent_actions_decided_by_a_human" in str(error)


def test_a_decision_needs_both_the_admin_and_the_time(db_session) -> None:
    admin = UserFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: AgentActionFactory(status=AgentActionStatus.APPROVED, decided_by_id=admin.id),
    )
    assert "ck_agent_actions_decided_by_a_human" in str(error)


def test_an_approved_and_executed_action_is_accepted(db_session) -> None:
    admin = UserFactory()
    db_session.flush()
    action = AgentActionFactory(
        status=AgentActionStatus.EXECUTED,
        decided_by_id=admin.id,
        decided_at=datetime.now(UTC),
        executed_at=datetime.now(UTC),
        result={"refund_id": "REF-1"},
    )
    db_session.flush()
    db_session.expire(action)
    assert action.result == {"refund_id": "REF-1"}


def test_a_proposed_action_needs_no_decision(db_session) -> None:
    assert AgentActionFactory(status=AgentActionStatus.PROPOSED).decided_by_id is None


def test_agent_action_repr_hides_payload_and_rationale(db_session) -> None:
    action = AgentActionFactory(rationale="private reasoning", payload={"x": "private"})
    assert "private" not in repr(action)
