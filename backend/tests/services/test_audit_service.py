"""The audit trail: actors, targets, request IDs and what may go into the details."""

import uuid

import pytest
from sqlalchemy import select

from app.models.enums import ActorType, CaseStatus, UserRole
from app.models.platform import AuditLog
from app.security import crypto
from app.services import audit_service
from app.services.audit_service import AuditError, Target
from tests.factories import CaseFactory, HospitalStaffFactory, UserFactory


def _rows(db_session) -> list[AuditLog]:  # type: ignore[no-untyped-def]
    return list(db_session.execute(select(AuditLog)).scalars())


def test_a_user_action_records_actor_and_target(app, db_session) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory()
    case = CaseFactory(created_by=user)
    entry = audit_service.record("case.submitted", case, actor=user, details={"documents": 2})
    assert entry.actor_type == ActorType.USER
    assert entry.actor_user_id == user.id
    assert (entry.target_type, entry.target_id) == ("case", case.id)
    assert entry.details == {"documents": 2}
    assert entry.created_at is not None
    assert _rows(db_session) == [entry]


@pytest.mark.parametrize(
    ("factory", "expected"),
    [(UserFactory, "user"), (CaseFactory, "case"), (HospitalStaffFactory, "hospital_staff")],
)
def test_the_target_type_is_the_snake_case_model_name(app, db_session, factory, expected) -> None:  # type: ignore[no-untyped-def]
    obj = factory()
    entry = audit_service.record("thing.happened", obj, actor=ActorType.SYSTEM)
    assert entry.target_type == expected
    assert entry.target_id == obj.id


def test_an_explicit_target_may_have_no_id(app, db_session) -> None:  # type: ignore[no-untyped-def]
    entry = audit_service.record(
        "ledger.verified", Target("ledger", None), actor=ActorType.SYSTEM, details={"valid": True}
    )
    assert (entry.target_type, entry.target_id) == ("ledger", None)


def test_system_and_paypal_actors_have_no_user(app, db_session) -> None:  # type: ignore[no-untyped-def]
    for actor in (ActorType.SYSTEM, ActorType.PAYPAL):
        entry = audit_service.record(
            "webhook.processed", Target("webhook_event", None), actor=actor
        )
        assert entry.actor_type == actor
        assert entry.actor_user_id is None


def test_an_agent_action_names_the_human_who_approved_it(app, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = UserFactory(role=UserRole.ADMIN)
    entry = audit_service.record(
        "refund.executed", CaseFactory(), actor=ActorType.AGENT, approver=admin
    )
    assert entry.actor_type == ActorType.AGENT
    assert entry.actor_user_id == admin.id


def test_a_user_cannot_also_have_an_approver(app, db_session) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AuditError):
        audit_service.record("a.b", CaseFactory(), actor=UserFactory(), approver=UserFactory())


@pytest.mark.parametrize(
    "action",
    ["", "submitted", "Case.Submitted", "case.", ".submitted", "case submitted", "a.b" * 30],
)
def test_actions_must_look_like_domain_dot_event(app, db_session, action: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AuditError):
        audit_service.record(action, CaseFactory(), actor=ActorType.SYSTEM)


def test_it_joins_the_callers_transaction_and_never_commits(app, db_session) -> None:  # type: ignore[no-untyped-def]
    savepoint = db_session.begin_nested()
    audit_service.record("case.submitted", CaseFactory(), actor=ActorType.SYSTEM)
    assert len(_rows(db_session)) == 1
    savepoint.rollback()
    assert _rows(db_session) == []


# --- Request IDs and addresses -----------------------------------------------------------------


def test_the_request_id_comes_from_the_current_request(app, db_session) -> None:  # type: ignore[no-untyped-def]
    with app.test_request_context("/", headers={"X-Request-ID": "req-12345678"}):
        from flask import g

        g.request_id = "req-12345678"
        entry = audit_service.record("case.submitted", CaseFactory(), actor=ActorType.SYSTEM)
    assert entry.request_id == "req-12345678"


def test_an_explicit_request_id_wins_and_works_outside_a_request(app, db_session) -> None:  # type: ignore[no-untyped-def]
    entry = audit_service.record(
        "case.triaged", CaseFactory(), actor=ActorType.SYSTEM, request_id="from-the-enqueuer"
    )
    assert entry.request_id == "from-the-enqueuer"
    assert audit_service.record("a.b", CaseFactory(), actor=ActorType.SYSTEM).request_id is None


def test_the_address_is_kept_only_as_a_keyed_hash(app, db_session) -> None:  # type: ignore[no-untyped-def]
    with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "203.0.113.9"}):
        entry = audit_service.record("a.b", CaseFactory(), actor=ActorType.SYSTEM)
    assert entry.ip_hmac == crypto.hash_ip("203.0.113.9")
    assert "203.0.113.9" not in entry.ip_hmac


def test_a_request_through_the_api_links_the_entry_to_the_response_header(browser) -> None:  # type: ignore[no-untyped-def]
    response = browser.sign_in_by_email("mama@example.test")
    request_id = response.headers["X-Request-ID"]
    ids = {row.request_id for row in browser_rows(browser) if row.action == "auth.login"}
    assert ids == {request_id}


def browser_rows(browser) -> list[AuditLog]:  # type: ignore[no-untyped-def]
    from app.extensions import db

    return list(db.session.execute(select(AuditLog)).scalars())


# --- Details -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["email", "Phone", "name", "display_name", "patient_reference", "diagnosis", "text", "otp"],
)
def test_forbidden_keys_raise_so_the_bug_is_found(app, db_session, key: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AuditError):
        audit_service.record("a.b", CaseFactory(), actor=ActorType.SYSTEM, details={key: "x"})


def test_forbidden_keys_are_caught_when_nested(app, db_session) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AuditError):
        audit_service.record(
            "a.b", CaseFactory(), actor=ActorType.SYSTEM, details={"changes": [{"email": "x"}]}
        )


def test_personal_data_inside_strings_is_replaced(app, db_session) -> None:  # type: ignore[no-untyped-def]
    entry = audit_service.record(
        "a.b",
        CaseFactory(),
        actor=ActorType.SYSTEM,
        details={"reason": "sent to jane.doe@example.test or 0712 345 678, ref 12345678"},
    )
    reason = entry.details["reason"]
    assert "jane" not in reason
    assert "0712" not in reason
    assert "12345678" not in reason
    assert "[email]" in reason
    assert "[phone]" in reason
    assert "[digits]" in reason


def test_ids_enums_and_numbers_are_stored_as_json(app, db_session) -> None:  # type: ignore[no-untyped-def]
    ident = uuid.uuid4()
    entry = audit_service.record(
        "case.transitioned",
        CaseFactory(),
        actor=ActorType.SYSTEM,
        details={
            "document_id": ident,
            "from": CaseStatus.DRAFT,
            "to": CaseStatus.PENDING_TRIAGE,
            "amount_minor": 2500,
            "ratio": 0.5,
            "flags": ("a", "b"),
            "none": None,
        },
    )
    db_session.expire(entry)
    assert entry.details == {
        "document_id": str(ident),
        "from": "draft",
        "to": "pending_triage",
        "amount_minor": 2500,
        "ratio": 0.5,
        "flags": ["a", "b"],
        "none": None,
    }


@pytest.mark.parametrize(
    "bad", [object(), {1: "x"}, b"bytes", {"a": {"b": {"c": {"d": {"e": 1}}}}}]
)
def test_details_that_cannot_be_stored_are_refused(app, db_session, bad) -> None:  # type: ignore[no-untyped-def]
    details = bad if isinstance(bad, dict) else {"value": bad}
    with pytest.raises(AuditError):
        audit_service.record("a.b", CaseFactory(), actor=ActorType.SYSTEM, details=details)


# --- Wired into sign-in ------------------------------------------------------------------------


def test_sign_in_and_logout_are_audited_without_the_address(browser) -> None:  # type: ignore[no-untyped-def]
    browser.sign_in_by_email("mama@example.test")
    browser.patch("/api/auth/me", {"display_name": "Mama"})
    browser.post("/api/auth/logout")
    rows = browser_rows(browser)
    assert sorted(row.action for row in rows) == [
        "auth.login",
        "auth.logout",
        "user.created",
        "user.updated",
    ]
    by_action = {row.action: row for row in rows}
    assert by_action["user.updated"].details == {"fields": ["display_name"]}
    assert by_action["user.created"].details == {"role": "family"}
    assert all(row.actor_type == ActorType.USER for row in rows)
    assert all(row.actor_user_id == row.target_id for row in rows)
    assert "mama@example.test" not in repr([row.details for row in rows])


def test_a_failed_sign_in_writes_nothing(browser) -> None:  # type: ignore[no-untyped-def]
    browser.post("/api/auth/verify", {"email": "mama@example.test", "code": "123456"})
    assert browser_rows(browser) == []
