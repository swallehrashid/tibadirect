"""The latest USD to KES rate and the goal it sets."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import requests
import responses
from sqlalchemy import func, select

from app.jobs import tasks
from app.models.case import Case
from app.models.enums import CaseStatus
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


# --------------------------------------------------------------------------- refreshing

PROVIDER_URL = fx_service.OPEN_ER_API_URL


@pytest.fixture
def live_provider(app):  # type: ignore[no-untyped-def]
    app.config["FX_PROVIDER"] = "open_er_api"
    yield
    app.config["FX_PROVIDER"] = "static"


def rates(db_session):  # type: ignore[no-untyped-def]
    return list(
        db_session.execute(select(FxRate).order_by(FxRate.fetched_at, FxRate.created_at))
        .scalars()
        .all()
    )


@responses.activate
def test_a_refresh_stores_the_providers_rate_to_eight_places(live_provider, db_session) -> None:  # type: ignore[no-untyped-def]
    db_session.execute(FxRate.__table__.delete())
    responses.add(
        responses.GET, PROVIDER_URL, body='{"result":"success","rates":{"KES":129.3456789012}}'
    )

    row = fx_service.refresh_rates(db_session)

    assert (row.source, row.rate) == ("open_er_api", Decimal("129.34567890"))
    assert fx_service.latest(db_session).id == row.id
    assert len(rates(db_session)) == 1


@responses.activate
def test_the_rate_is_read_without_going_through_a_float(live_provider, db_session) -> None:  # type: ignore[no-untyped-def]
    responses.add(responses.GET, PROVIDER_URL, body='{"rates":{"KES":129.10000000000001}}')

    assert fx_service.refresh_rates(db_session).rate == Decimal("129.10000000")


@pytest.mark.parametrize(
    "body",
    [
        '{"rates":{"KES":0}}',
        '{"rates":{"KES":-5}}',
        '{"rates":{"KES":12.9}}',  # a slipped decimal point
        '{"rates":{"KES":12900}}',
        '{"rates":{"KES":"abc"}}',
        '{"rates":{"UGX":3700}}',
        '{"result":"error","rates":{"KES":129}}',
        "not json",
        "[]",
    ],
)
@responses.activate
def test_an_unusable_answer_is_never_stored(live_provider, db_session, body: str) -> None:  # type: ignore[no-untyped-def]
    db_session.execute(FxRate.__table__.delete())
    responses.add(responses.GET, PROVIDER_URL, body=body)

    row = fx_service.refresh_rates(db_session)

    assert row.source == "static"
    assert [r.source for r in rates(db_session)] == ["static"]


@responses.activate
def test_a_provider_outage_keeps_a_live_rate_under_a_day_old(live_provider, db_session) -> None:  # type: ignore[no-untyped-def]
    db_session.execute(FxRate.__table__.delete())
    kept = FxRateFactory(
        rate=Decimal("130.2"),
        source="open_er_api",
        fetched_at=datetime.now(UTC) - timedelta(hours=5),
    )
    responses.add(responses.GET, PROVIDER_URL, status=503)

    row = fx_service.refresh_rates(db_session)

    assert row.id == kept.id
    assert len(rates(db_session)) == 1


@responses.activate
def test_a_provider_outage_replaces_a_day_old_live_rate_with_the_static_one(  # type: ignore[no-untyped-def]
    live_provider, db_session
) -> None:
    FxRateFactory(
        rate=Decimal("130.2"),
        source="open_er_api",
        fetched_at=datetime.now(UTC) - timedelta(hours=25),
    )
    responses.add(responses.GET, PROVIDER_URL, body=requests.Timeout())

    row = fx_service.refresh_rates(db_session)

    assert (row.source, row.rate) == ("static", Decimal("129.00"))
    assert fx_service.latest(db_session).id == row.id


def test_the_static_provider_stores_the_static_rate_every_time(app, db_session) -> None:  # type: ignore[no-untyped-def]
    db_session.execute(FxRate.__table__.delete())

    fx_service.refresh_rates(db_session)
    fx_service.refresh_rates(db_session)

    assert [r.source for r in rates(db_session)] == ["static", "static"]


@responses.activate
def test_a_new_rate_resets_the_usd_goal_of_open_cases_only(live_provider, db_session) -> None:  # type: ignore[no-untyped-def]
    live = CaseFactory(status=CaseStatus.LIVE, target_kes_minor=1_000_000, buffer_bps=500)
    funded = CaseFactory(status=CaseStatus.FUNDED, target_kes_minor=2_000_000, buffer_bps=0)
    closed = CaseFactory(
        status=CaseStatus.CLOSED, close_reason="fully_settled", target_kes_minor=500_000
    )
    draft = CaseFactory(target_kes_minor=None)
    for case in (live, funded, closed):
        case.usd_goal_minor = 1
    db_session.flush()
    versions = {case.id: case.version_id for case in (live, funded)}
    responses.add(responses.GET, PROVIDER_URL, body='{"rates":{"KES":100}}')

    fx_service.refresh_rates(db_session)
    db_session.expire_all()

    assert db_session.get(Case, live.id).usd_goal_minor == 10_500
    assert db_session.get(Case, funded.id).usd_goal_minor == 20_000
    assert db_session.get(Case, closed.id).usd_goal_minor == 1
    assert db_session.get(Case, draft.id).usd_goal_minor is None
    # the version column is untouched, so a donation in flight is not turned away
    assert {c: db_session.get(Case, c).version_id for c in versions} == versions


@responses.activate
def test_a_goal_that_does_not_change_is_not_written(live_provider, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE, target_kes_minor=1_000_000, buffer_bps=0)
    case.usd_goal_minor = 10_000
    db_session.flush()

    assert fx_service.update_open_case_goals(db_session, Decimal("100")) == 0


@responses.activate
def test_the_hourly_job_commits_and_names_the_source(live_provider, db_session) -> None:  # type: ignore[no-untyped-def]
    responses.add(responses.GET, PROVIDER_URL, body='{"rates":{"KES":131}}')

    assert tasks.refresh_fx_rates() == "open_er_api"
    assert fx_service.current_rate(db_session).rate == Decimal("131.00000000")


# --------------------------------------------------------------------------- the public view


def test_current_rate_reads_the_newest_row_without_writing(db_session) -> None:  # type: ignore[no-untyped-def]
    db_session.execute(FxRate.__table__.delete())

    view = fx_service.current_rate(db_session)

    assert (view.rate, view.source, view.stored, view.is_live) == (
        Decimal("129.00"),
        "static",
        False,
        False,
    )
    assert rates(db_session) == []


def test_a_live_rate_goes_stale_after_six_hours_but_a_static_one_never_does() -> None:
    now = datetime.now(UTC)
    fresh = fx_service.RateView(Decimal("129"), "open_er_api", now - timedelta(hours=5))
    old = fx_service.RateView(Decimal("129"), "open_er_api", now - timedelta(hours=7))
    static = fx_service.RateView(Decimal("129"), "static", now - timedelta(days=30))

    assert (fresh.is_stale(now), old.is_stale(now), static.is_stale(now)) == (False, True, False)
