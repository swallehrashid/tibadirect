"""/api/admin/agent: asking, the list of proposals, and the administrator's decision."""

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models.enums import AgentActionStatus, CaseStatus, UserRole
from app.models.money import Refund
from app.models.platform import AgentAction
from tests.agent.helpers import configure, say, use
from tests.browser import Browser
from tests.donation_helpers import donate, live_case
from tests.factories import UserFactory

BrowserFor = Callable[..., Browser]
ASK = "/api/admin/agent/ask"
ACTIONS = "/api/admin/agent/actions"


@pytest.fixture
def admin_user(db_session):  # type: ignore[no-untyped-def]
    user = UserFactory(role=UserRole.ADMIN, display_name="Admin Amina")
    db.session.commit()
    return user


@pytest.fixture
def admin(browser_for: BrowserFor, admin_user) -> Browser:  # type: ignore[no-untyped-def]
    return browser_for(admin_user)


@pytest.fixture
def held(db_session, paypal):  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case, "50.00")
    db.session.commit()
    return case, donation


def refund_proposal(donation, amount: str = "10.00") -> tuple:  # type: ignore[no-untyped-def]
    return (
        "propose_refund",
        {
            "donation_id": str(donation.id),
            "amount_usd": amount,
            "reason": "The donor says they gave twice.",
        },
    )


# --------------------------------------------------------------------------- who and when


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", ASK),
        ("get", ACTIONS),
        ("post", f"{ACTIONS}/{uuid.uuid4()}/approve"),
        ("post", f"{ACTIONS}/{uuid.uuid4()}/reject"),
    ],
)
def test_only_an_administrator_may_use_the_agent(  # type: ignore[no-untyped-def]
    app, browser_for, db_session, monkeypatch, method, path
) -> None:
    configure(app, monkeypatch)
    family = browser_for(UserFactory(role=UserRole.FAMILY))
    staff = browser_for(UserFactory(role=UserRole.HOSPITAL_STAFF))

    for caller in (family, staff):
        assert getattr(caller, method)(path).status_code == 403
    anonymous = app.test_client()
    assert getattr(anonymous, method)(path).status_code in (401, 403)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", ASK),
        ("get", ACTIONS),
        ("post", f"{ACTIONS}/{uuid.uuid4()}/approve"),
        ("post", f"{ACTIONS}/{uuid.uuid4()}/reject"),
    ],
)
def test_with_the_flag_off_the_agent_does_not_exist(admin: Browser, method, path) -> None:  # type: ignore[no-untyped-def]
    sent = {"question": "hello there"}
    response = admin.get(path) if method == "get" else admin.post(path, sent)

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "feature_disabled"


def test_with_no_language_model_asking_is_a_503(app, admin: Browser, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    configure(app, monkeypatch)
    monkeypatch.setitem(app.config, "LLM_API_KEY", "")

    response = admin.post(ASK, {"question": "How is it going?"})

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "agent_unavailable"


def test_a_model_that_cannot_be_reached_is_a_502(app, admin: Browser, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    configure(app, monkeypatch, ConnectionError("down"))

    response = admin.post(ASK, {"question": "How is it going?"})

    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "agent_failed"


# --------------------------------------------------------------------------- asking


def test_a_question_gets_an_answer_the_tool_trace_and_any_proposals(  # type: ignore[no-untyped-def]
    app, admin: Browser, held, monkeypatch
) -> None:
    case, donation = held
    configure(
        app,
        monkeypatch,
        use("get_case_summary", {"case": case.public_slug}),
        use(*refund_proposal(donation)),
        say("It has raised $48.25. I proposed a $10.00 refund; it is waiting for approval."),
    )

    response = admin.post(
        ASK, {"question": "Check this case and fix the double payment.", "case_id": str(case.id)}
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["answer"].startswith("It has raised")
    assert body["steps"] == 3
    assert body["stopped_early"] is False
    assert [(c["name"], c["ok"]) for c in body["tool_calls"]] == [
        ("get_case_summary", True),
        ("propose_refund", True),
    ]
    [action] = body["proposed_actions"]
    assert action["status"] == "proposed"
    assert action["action_type"] == "refund"
    assert action["payload"]["amount_usd_minor"] == 1000
    assert action["case_alias"] == case.alias
    assert action["decided_by"] is None


@pytest.mark.parametrize("payload", [{}, {"question": "hi"}, {"question": "x" * 1001}])
def test_a_question_must_be_a_real_one(app, admin: Browser, monkeypatch, payload) -> None:  # type: ignore[no-untyped-def]
    configure(app, monkeypatch)

    assert admin.post(ASK, payload).status_code == 422


def test_a_question_about_a_case_that_does_not_exist_is_a_404(
    app, admin: Browser, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    configure(app, monkeypatch)

    response = admin.post(ASK, {"question": "How is it going?", "case_id": str(uuid.uuid4())})

    assert response.status_code == 404


def test_thirty_questions_an_hour_is_the_limit(app, admin: Browser, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    configure(app, monkeypatch)

    for _ in range(30):
        assert admin.post(ASK, {"question": "How is it going?"}).status_code == 200

    assert admin.post(ASK, {"question": "How is it going?"}).status_code == 429


# --------------------------------------------------------------------------- deciding


def propose(app, admin, held, monkeypatch, amount: str = "10.00") -> str:  # type: ignore[no-untyped-def]
    _, donation = held
    configure(app, monkeypatch, use(*refund_proposal(donation, amount)), say("proposed"))
    return admin.post(ASK, {"question": "Please refund the double payment."}).get_json()[
        "proposed_actions"
    ][0]["id"]


def test_the_list_shows_waiting_proposals_and_filters(
    app, admin: Browser, held, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    first = propose(app, admin, held, monkeypatch, "10.00")
    second = propose(app, admin, held, monkeypatch, "5.00")
    admin.post(f"{ACTIONS}/{first}/reject", {"note": "Not needed, the donor withdrew it."})

    everything = admin.get(ACTIONS).get_json()
    waiting = admin.get(f"{ACTIONS}?status=proposed").get_json()
    rejected = admin.get(f"{ACTIONS}?status=rejected").get_json()

    assert [a["id"] for a in everything["items"]] == [second, first]
    assert everything["total"] == 2
    assert [a["id"] for a in waiting["items"]] == [second]
    assert [a["id"] for a in rejected["items"]] == [first]
    assert rejected["items"][0]["decided_by"] == "Admin Amina"
    assert admin.get(f"{ACTIONS}?status=nonsense").status_code == 422


def test_approving_carries_the_refund_out(
    app, admin: Browser, held, db_session, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _, donation = held
    action_id = propose(app, admin, held, monkeypatch)

    response = admin.post(f"{ACTIONS}/{action_id}/approve")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "executed"
    assert body["decided_by"] == "Admin Amina"
    assert body["result"]["amount_usd_minor"] == 1000
    assert body["error"] is None
    db_session.refresh(donation)
    assert donation.refunded_usd_minor == 1000
    assert db_session.scalars(select(Refund)).one().amount_usd_minor == 1000


def test_approving_something_no_longer_possible_answers_with_the_failed_action(  # type: ignore[no-untyped-def]
    app, admin: Browser, held, db_session, monkeypatch
) -> None:
    _, donation = held
    action_id = propose(app, admin, held, monkeypatch, "40.00")
    donation.refunded_usd_minor = donation.net_usd_minor
    db.session.commit()

    response = admin.post(f"{ACTIONS}/{action_id}/approve")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "failed"
    assert body["error"].startswith("refund_exceeds_balance")
    assert db_session.scalars(select(Refund)).all() == []


def test_a_decided_action_cannot_be_decided_again(app, admin: Browser, held, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    action_id = propose(app, admin, held, monkeypatch)
    assert admin.post(f"{ACTIONS}/{action_id}/approve").status_code == 200

    again = admin.post(f"{ACTIONS}/{action_id}/approve")
    reject = admin.post(f"{ACTIONS}/{action_id}/reject", {"note": "Too late to say no now."})

    assert again.status_code == 409
    assert again.get_json()["error"]["code"] == "action_already_decided"
    assert reject.status_code == 409


def test_rejecting_needs_a_reason_and_leaves_the_money_alone(  # type: ignore[no-untyped-def]
    app, admin: Browser, held, db_session, monkeypatch
) -> None:
    _, donation = held
    action_id = propose(app, admin, held, monkeypatch)

    assert admin.post(f"{ACTIONS}/{action_id}/reject", {}).status_code == 422
    assert admin.post(f"{ACTIONS}/{action_id}/reject", {"note": "no"}).status_code == 422
    response = admin.post(
        f"{ACTIONS}/{action_id}/reject", {"note": "The donor withdrew the request."}
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "rejected"
    db_session.refresh(donation)
    assert donation.refunded_usd_minor == 0
    assert db_session.get(AgentAction, uuid.UUID(action_id)).status == AgentActionStatus.REJECTED


def test_an_unknown_action_is_a_404(app, admin: Browser, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    configure(app, monkeypatch)
    missing = uuid.uuid4()

    assert admin.post(f"{ACTIONS}/{missing}/approve").status_code == 404
    assert (
        admin.post(f"{ACTIONS}/{missing}/reject", {"note": "Not there at all."}).status_code == 404
    )


def test_approving_a_freeze_through_the_api_freezes_the_case(
    app, admin: Browser, held, db_session, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    case, _ = held
    configure(
        app,
        monkeypatch,
        use("propose_freeze", {"case_id": str(case.id), "reason": "Signals do not add up."}),
        say("proposed"),
    )
    action_id = admin.post(ASK, {"question": "Freeze it please."}).get_json()["proposed_actions"][
        0
    ]["id"]

    response = admin.post(f"{ACTIONS}/{action_id}/approve")

    assert response.get_json()["status"] == "executed"
    db_session.refresh(case)
    assert case.status in (CaseStatus.FROZEN, CaseStatus.CANCELLED)
