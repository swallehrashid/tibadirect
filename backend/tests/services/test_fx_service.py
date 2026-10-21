"""The latest USD to KES rate and the goal it sets."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models.money import FxRate
from app.money import usd_goal_minor
from app.services import fx_service
from tests.factories import CaseFactory, FxRateFactory


def test_the_newest_stored_rate_wins(db_session) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    FxRateFactory(rate=Decimal("120"), fetched_at=now - timedelta(hours=2))
    newest = FxRateFactory(rate=Decimal("131.5"), fetched_at=now)
    FxRateFactory(rate=Decimal("125"), fetched_at=now - timedelta(hours=1))
    assert fx_service.latest(db_session).id == newest.id


def test_with_no_stored_rate_the_configured_static_rate_is_stored_once(app, db_session) -> None:  # type: ignore[no-untyped-def]
    db_session.execute(FxRate.__table__.delete())
    first = fx_service.latest(db_session)
    again = fx_service.latest(db_session)
    assert first.id == again.id
    assert (first.base, first.quote, first.source) == ("USD", "KES", "static")
    assert first.rate == Decimal(str(app.config["FX_STATIC_USD_KES"]))
    assert db_session.execute(select(func.count()).select_from(FxRate)).scalar_one() == 1


def test_the_usd_goal_covers_the_target_plus_the_buffer_rounded_up(db_session) -> None:  # type: ignore[no-untyped-def]
    FxRateFactory(rate=Decimal("129.00000000"))
    case = CaseFactory(target_kes_minor=8_500_000, buffer_bps=500)
    assert fx_service.usd_goal_for(db_session, case) == 69_187
    assert fx_service.usd_goal_for(db_session, case) == usd_goal_minor(
        8_500_000, 500, Decimal("129")
    )


def test_a_case_without_a_target_has_no_goal(db_session) -> None:  # type: ignore[no-untyped-def]
    assert fx_service.usd_goal_for(db_session, CaseFactory(target_kes_minor=None)) is None


def test_a_bigger_buffer_means_a_bigger_goal(db_session) -> None:  # type: ignore[no-untyped-def]
    FxRateFactory(rate=Decimal("100"))
    small = CaseFactory(target_kes_minor=1_000_000, buffer_bps=0)
    large = CaseFactory(target_kes_minor=1_000_000, buffer_bps=1000)
    assert fx_service.usd_goal_for(db_session, small) == 10_000
    assert fx_service.usd_goal_for(db_session, large) == 11_000
