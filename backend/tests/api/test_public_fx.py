"""GET /api/public/fx."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.money import FxRate
from tests.factories import FxRateFactory


def test_it_returns_the_latest_rate_as_a_decimal_string(client, db_session) -> None:  # type: ignore[no-untyped-def]
    FxRateFactory(rate=Decimal("128"), fetched_at=datetime.now(UTC) - timedelta(hours=2))
    FxRateFactory(rate=Decimal("129.5"), source="open_er_api", fetched_at=datetime.now(UTC))

    response = client.get("/api/public/fx")

    assert response.status_code == 200
    body = response.get_json()
    assert body["rate"] == "129.50000000"
    assert (body["base"], body["quote"], body["source"]) == ("USD", "KES", "open_er_api")
    assert body["is_live"] is True
    assert body["is_stale"] is False
    assert body["fetched_at"]


def test_a_live_rate_that_was_not_refreshed_is_reported_stale(client, db_session) -> None:  # type: ignore[no-untyped-def]
    FxRateFactory(source="open_er_api", fetched_at=datetime.now(UTC) - timedelta(hours=8))

    assert client.get("/api/public/fx").get_json()["is_stale"] is True


def test_with_nothing_stored_it_answers_with_the_static_rate_and_writes_nothing(  # type: ignore[no-untyped-def]
    client, db_session
) -> None:
    db_session.execute(FxRate.__table__.delete())

    body = client.get("/api/public/fx").get_json()

    assert (body["rate"], body["source"], body["is_live"]) == ("129.00000000", "static", False)
    assert db_session.execute(FxRate.__table__.select()).first() is None


def test_it_needs_no_sign_in(client, db_session) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/public/fx").status_code == 200
