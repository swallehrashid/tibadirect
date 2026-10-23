"""The settlement adapters: how each one reads a tranche and the provider's answer."""

import pytest

from app.models.enums import SettlementMethod
from app.payments.adapters import (
    JengaAdapter,
    MockPartnerAdapter,
    NotConfigured,
    PayPalPayoutsAdapter,
    adapter_for,
)
from app.payments.adapters.paypal_payouts import interpret, sender_item_id
from app.services import settlement_service
from tests.donation_helpers import donate, settleable_case


def batch(batch_status: str = "SUCCESS", items: list | None = None) -> dict:
    return {
        "batch_header": {"payout_batch_id": "BATCH1", "batch_status": batch_status},
        "items": items if items is not None else [],
    }


def item(state: str, **extra: object) -> dict:
    return {"payout_item_id": "ITEM1", "transaction_status": state, **extra}


@pytest.mark.parametrize(
    ("method", "adapter"),
    [
        (SettlementMethod.PAYPAL_PAYOUTS, PayPalPayoutsAdapter),
        (SettlementMethod.MOCK_PARTNER, MockPartnerAdapter),
        (SettlementMethod.JENGA, JengaAdapter),
    ],
)
def test_each_settlement_method_has_its_adapter(method, adapter) -> None:
    assert isinstance(adapter_for(method), adapter)
    assert adapter_for(method).method == method


@pytest.mark.parametrize(
    ("response", "status", "item_id", "failure"),
    [
        (batch("PENDING"), "processing", None, None),
        (batch("PROCESSING"), "processing", None, None),
        (batch("SUCCESS", [item("SUCCESS")]), "succeeded", "ITEM1", None),
        (batch("SUCCESS", [item("PENDING")]), "processing", "ITEM1", None),
        (batch("SUCCESS", [item("ONHOLD")]), "processing", "ITEM1", None),
        (batch("SUCCESS", [item("UNCLAIMED")]), "processing", "ITEM1", None),
        (batch("SUCCESS", [item("FAILED")]), "failed", "ITEM1", "failed"),
        (batch("SUCCESS", [item("BLOCKED")]), "failed", "ITEM1", "blocked"),
        (batch("SUCCESS", [item("RETURNED")]), "failed", "ITEM1", "returned"),
        (batch("SUCCESS", [item("REFUNDED")]), "failed", "ITEM1", "refunded"),
        (batch("SUCCESS", [item("REVERSED")]), "failed", "ITEM1", "reversed"),
        (
            batch("SUCCESS", [item("FAILED", errors={"name": "RECEIVER_UNREGISTERED"})]),
            "failed",
            "ITEM1",
            "receiver_unregistered",
        ),
        (batch("DENIED"), "failed", None, "batch denied"),
        (batch("CANCELED"), "failed", None, "batch canceled"),
    ],
)
def test_paypals_batch_is_read_as_one_answer(response, status, item_id, failure) -> None:
    result = interpret(response)

    assert (result.status, result.item_id, result.failure) == (status, item_id, failure)
    assert result.batch_id == "BATCH1"


def test_an_empty_answer_means_still_processing() -> None:
    assert interpret({}).status == "processing"


@pytest.fixture(autouse=True)
def _manual(manual_settlement: list) -> None:
    """These tests build tranches by hand."""


def test_a_tranche_without_a_batch_id_cannot_be_polled_yet(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement

    assert PayPalPayoutsAdapter().poll(settlement).status == "processing"


def test_the_sender_item_id_fits_the_30_characters_paypal_allows(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement

    assert len(sender_item_id(settlement)) == 30
    assert settlement.id.hex.startswith(sender_item_id(settlement))


def test_paypal_needs_a_payout_email_and_the_partner_needs_nothing(db_session) -> None:
    case = settleable_case(payout_email=None)

    assert "payout email" in PayPalPayoutsAdapter().check_ready(case.hospital)
    assert MockPartnerAdapter().check_ready(case.hospital) is None
    assert "not configured" in JengaAdapter().check_ready(case.hospital)


def test_the_mock_partners_reference_is_the_same_every_time(db_session, paypal) -> None:
    case = settleable_case(method=SettlementMethod.MOCK_PARTNER, payout_email=None)
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement
    adapter = MockPartnerAdapter()

    first = adapter.send(settlement, case.hospital)
    again = adapter.send(settlement, case.hospital)

    assert first == again
    assert first.batch_id.startswith("MOCKPARTNER-")
    assert len(first.batch_id) <= 40


def test_the_mock_partner_ignores_a_reference_that_is_not_its_own(db_session, paypal) -> None:
    case = settleable_case(method=SettlementMethod.MOCK_PARTNER, payout_email=None)
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement.external_batch_id = "SOMETHINGELSE"

    assert MockPartnerAdapter().poll(settlement).status == "processing"


def test_the_jenga_stub_refuses_to_send_or_poll(db_session, paypal) -> None:
    case = settleable_case(method=SettlementMethod.JENGA)
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id)
    assert settlement.settlement is None  # the engine does not even create one

    stub = JengaAdapter()
    with pytest.raises(NotConfigured):
        stub.send(None, case.hospital)  # type: ignore[arg-type]
    with pytest.raises(NotConfigured):
        stub.poll(None)  # type: ignore[arg-type]
