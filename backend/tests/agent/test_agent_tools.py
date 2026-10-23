"""The treasurer agent's tools: what they show the model, and what they refuse."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.agent import tools
from app.extensions import db
from app.models.enums import AgentActionStatus, SettlementStatus, UserRole
from app.models.money import Donation
from app.models.platform import AgentAction
from tests.donation_helpers import donate, live_case, settleable_case
from tests.factories import DonationFactory, SettlementFactory, UserFactory


@pytest.fixture
def admin(db_session):  # type: ignore[no-untyped-def]
    user = UserFactory(role=UserRole.ADMIN)
    db.session.commit()
    return user


@pytest.fixture
def held(db_session, paypal):  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case, "50.00", name="Jane Wanjiku Donor", anonymous=False)
    db.session.commit()
    return case, donation


def run(session, admin, name: str, **arguments):  # type: ignore[no-untyped-def]
    return tools.call(session, admin, name, arguments)


# --------------------------------------------------------------------------- what exists


def test_the_agent_has_exactly_these_tools_and_none_that_carry_out_an_action() -> None:
    assert [t.name for t in tools.TOOLS] == [
        "get_case_summary",
        "list_donations",
        "get_ledger",
        "verify_ledger",
        "list_settlements",
        "paypal_transaction_search",
        "get_fx_rate",
        "propose_refund",
        "propose_payout",
        "propose_freeze",
    ]
    assert [t.name for t in tools.TOOLS if t.proposes] == [
        "propose_refund",
        "propose_payout",
        "propose_freeze",
    ]
    assert not [t for t in tools.BY_NAME if "approve" in t or "execute" in t]


def test_every_tool_is_described_to_the_model_with_a_closed_schema() -> None:
    for spec in tools.specs():
        assert spec["description"]
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])


# --------------------------------------------------------------------------- reading


def test_a_case_summary_gives_status_and_money_by_id_or_slug(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    case, _ = held

    by_id, ok = run(db_session, admin, "get_case_summary", case="" + str(case.id))
    by_slug, ok_slug = run(db_session, admin, "get_case_summary", case=case.public_slug)

    assert ok
    assert ok_slug
    assert by_id == by_slug
    assert by_id["status"] == "live"
    assert by_id["slug"] == case.public_slug
    assert by_id["target_kes"] == "KES 100,000.00"
    assert by_id["captured_net_usd"] == "USD 48.25"
    assert by_id["donor_count"] == 1
    assert by_id["hospital"] == case.hospital.display_name


def test_an_unknown_case_is_an_error_the_model_can_read(db_session, admin) -> None:  # type: ignore[no-untyped-def]
    result, ok = run(db_session, admin, "get_case_summary", case=str(uuid.uuid4()))
    assert (ok, result) == (False, {"error": "There is no such case."})
    result, ok = run(db_session, admin, "get_case_summary", case="no-such-slug")
    assert ok is False


def test_donations_show_the_money_and_nothing_about_the_donor(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    case, donation = held

    result, ok = run(db_session, admin, "list_donations", case_id=str(case.id))

    assert ok
    row = result["donations"][0]
    assert row["donation_id"] == str(donation.id)
    assert row["amount_usd_minor"] == 5000
    assert row["still_held_usd_minor"] == donation.net_usd_minor
    assert row["paypal_capture_id"] == donation.paypal_capture_id
    shown = json.dumps(result)
    for private in ("Jane", "Wanjiku", donation.receipt_token, donation.paypal_order_id):
        assert private not in shown
    assert "@" not in shown


def test_a_long_list_is_cut_and_says_so(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    case, _ = held
    for _ in range(tools.MAX_ROWS + 2):
        DonationFactory(case=case)
    db.session.commit()

    result, _ = run(db_session, admin, "list_donations", case_id=str(case.id))

    assert len(result["donations"]) == tools.MAX_ROWS
    assert result["truncated"] is True


def test_the_ledger_is_newest_first_and_the_chain_verifies(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    case, _ = held

    ledger, _ = run(db_session, admin, "get_ledger", case_id=str(case.id))
    verified, ok = run(db_session, admin, "verify_ledger", case_id=str(case.id))

    seqs = [entry["seq"] for entry in ledger["entries"]]
    assert seqs == sorted(seqs, reverse=True)
    assert {e["type"] for e in ledger["entries"]} >= {"donation_captured", "paypal_fee"}
    assert len(ledger["entries"][0]["hash"]) == tools.SHORT
    assert ok
    assert verified["valid"] is True
    assert verified["entries_checked"] >= 1


def test_settlements_list_the_tranches(db_session, admin, paypal) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case()
    SettlementFactory(
        case=case, confirmation=case.confirmations[0], status=SettlementStatus.SUCCEEDED
    )
    db.session.commit()

    result, ok = run(db_session, admin, "list_settlements", case_id=case.public_slug)

    assert ok
    assert result["settlements"][0]["tranche_no"] == 1
    assert result["settlements"][0]["status"] == "succeeded"


def test_the_fx_rate_comes_with_its_source(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    result, ok = run(db_session, admin, "get_fx_rate")

    assert ok
    assert result["usd_to_kes"] == "129.00000000"
    assert result["source"] == "test"


def test_paypals_own_report_is_summarised_without_the_payer(db_session, admin, held) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)

    result, ok = run(
        db_session,
        admin,
        "paypal_transaction_search",
        start=(now - timedelta(days=1)).isoformat(),
        end=(now + timedelta(days=1)).isoformat(),
    )

    assert ok
    assert result["transactions"][0]["event_code"] == "T0006"
    assert result["transactions"][0]["amount"] == "50.00"
    assert "lag" in result["note"]
    assert "payer" not in json.dumps(result).lower()


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("yesterday", "2026-10-23", "start must be a date"),
        ("2026-10-01", "soon", "end must be a date"),
        ("2026-10-05", "2026-10-01", "end must be after start"),
        ("2026-01-01", "2026-06-01", "at most 31 days"),
    ],
)
def test_a_bad_search_window_is_explained(db_session, admin, start, end, message) -> None:  # type: ignore[no-untyped-def]
    result, ok = run(db_session, admin, "paypal_transaction_search", start=start, end=end)

    assert ok is False
    assert message in result["error"]


# --------------------------------------------------------------------------- proposing


def test_proposing_a_refund_writes_a_proposal_and_says_nothing_was_done(
    db_session, admin, held
) -> None:  # type: ignore[no-untyped-def]
    _, donation = held

    result, ok = run(
        db_session,
        admin,
        "propose_refund",
        donation_id=str(donation.id),
        amount_usd="12.50",
        reason="The donor paid twice and asked for one back.",
    )

    assert ok
    assert result["status"] == "proposed"
    assert "Nothing has been done" in result["note"]
    action = db_session.get(AgentAction, uuid.UUID(result["action_id"]))
    assert action.status == AgentActionStatus.PROPOSED
    assert action.payload["amount_usd_minor"] == 1250
    assert db_session.get(Donation, donation.id).refunded_usd_minor == 0


@pytest.mark.parametrize("amount", ["lots", "1.234", "-5", "0", ""])
def test_an_amount_that_is_not_dollars_and_cents_is_refused(
    db_session, admin, held, amount
) -> None:  # type: ignore[no-untyped-def]
    _, donation = held

    result, ok = run(
        db_session,
        admin,
        "propose_refund",
        donation_id=str(donation.id),
        amount_usd=amount,
        reason="A reason that is long enough.",
    )

    assert ok is False
    assert "error" in result


def test_a_proposal_that_cannot_be_made_comes_back_as_a_reason_not_a_crash(
    db_session, admin, held
) -> None:  # type: ignore[no-untyped-def]
    case, _ = held

    result, ok = run(db_session, admin, "propose_freeze", case_id=str(case.id), reason="short")

    assert ok is False
    assert "at least 10" in result["error"]
    result, ok = run(
        db_session,
        admin,
        "propose_refund",
        donation_id="not-an-id",
        amount_usd="1.00",
        reason="x" * 20,
    )
    assert (ok, result) == (False, {"error": "donation_id must be an ID."})


def test_payout_and_freeze_proposals_name_the_case(db_session, admin, paypal) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case()
    db.session.commit()

    payout, ok = run(
        db_session, admin, "propose_payout", case_id=case.public_slug, reason="A tranche looks due."
    )
    freeze, ok_freeze = run(
        db_session, admin, "propose_freeze", case_id=str(case.id), reason="Signals look wrong."
    )

    assert ok
    assert ok_freeze
    assert payout["action_id"] != freeze["action_id"]


# --------------------------------------------------------------------------- the guard


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("approve_action", {}, "no tool called approve_action"),
        ("get_fx_rate", "not an object", "must be an object"),
        ("get_fx_rate", {"extra": 1}, "Unknown input: extra"),
        ("get_case_summary", {}, "Missing input: case"),
        ("list_donations", {"case_id": ""}, "Missing input: case_id"),
    ],
)
def test_a_call_that_does_not_fit_the_tool_is_turned_away(
    db_session, admin, name, arguments, message
) -> None:  # type: ignore[no-untyped-def]
    result, ok = tools.call(db_session, admin, name, arguments)

    assert ok is False
    assert message in result["error"]


def test_a_huge_result_is_replaced_by_a_request_for_less() -> None:
    assert json.loads(tools.render({"rows": ["x" * 100] * 500}))["error"].startswith("The result")
    assert json.loads(tools.render({"a": 1})) == {"a": 1}
