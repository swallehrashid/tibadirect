"""USD to KES rates and the donation goal they set.

``refresh_rates`` runs every hour: it asks the configured provider for today's rate and stores it,
and keeps the USD goal of open cases in step with it. If the provider cannot be reached, or
answers with something that makes no sense, the newest live rate is kept while it is less than a
day old; after that the configured static rate is stored, so a rate is always available.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from flask import current_app
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.enums import CaseStatus
from app.models.money import FxRate
from app.money import usd_goal_minor

logger = logging.getLogger(__name__)

BASE, QUOTE = "USD", "KES"
STATIC_SOURCE = "static"
PROVIDER_OPEN_ER_API = "open_er_api"
OPEN_ER_API_URL = "https://open.er-api.com/v6/latest/USD"
PROVIDER_TIMEOUT_SECONDS = 10
RATE_SCALE = Decimal("0.00000001")
# A live rate older than this is replaced by the static one when refreshing fails.
LIVE_RATE_MAX_AGE = timedelta(hours=24)
STALE_AFTER = timedelta(hours=6)
# A fetched rate must lie within this factor of the configured static rate, which catches a feed
# that returns zero, a rate for another currency or a unit slip (cents for shillings).
SANE_RATE_FACTOR = Decimal(2)
GOAL_STATUSES = (CaseStatus.LIVE, CaseStatus.FUNDED)


class RateUnavailable(Exception):
    """The provider could not give a usable rate."""


@dataclass(frozen=True)
class RateView:
    """A rate as the public API shows it. ``stored`` is false for the unsaved static fallback."""

    rate: Decimal
    source: str
    fetched_at: datetime
    stored: bool = True

    @property
    def is_live(self) -> bool:
        return self.source != STATIC_SOURCE

    def is_stale(self, now: datetime | None = None) -> bool:
        """A live rate that has not been refreshed for six hours; a static rate is never stale."""
        return self.is_live and (now or datetime.now(UTC)) - self.fetched_at > STALE_AFTER


def latest(session: Session) -> FxRate:
    """The newest stored USD to KES rate, storing the configured static one if there is none."""
    rate = session.execute(
        select(FxRate)
        .where(FxRate.base == BASE, FxRate.quote == QUOTE)
        .order_by(FxRate.fetched_at.desc(), FxRate.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if rate is not None:
        return rate
    rate = FxRate(
        base=BASE,
        quote=QUOTE,
        rate=Decimal(current_app.config["FX_STATIC_USD_KES"]),
        source=STATIC_SOURCE,
        fetched_at=datetime.now(UTC),
    )
    session.add(rate)
    session.flush()
    return rate


def usd_goal_for(session: Session, case: Case) -> int | None:
    """USD cents to ask for: the confirmed KES target plus its buffer, at the latest rate."""
    if case.target_kes_minor is None:
        return None
    return usd_goal_minor(case.target_kes_minor, case.buffer_bps, latest(session).rate)


def current_rate(session: Session) -> RateView:
    """The newest stored rate, or the static one if none is stored. Never writes."""
    stored = session.execute(
        select(FxRate)
        .where(FxRate.base == BASE, FxRate.quote == QUOTE)
        .order_by(FxRate.fetched_at.desc(), FxRate.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if stored is not None:
        return RateView(stored.rate, stored.source, stored.fetched_at)
    return RateView(
        Decimal(current_app.config["FX_STATIC_USD_KES"]),
        STATIC_SOURCE,
        datetime.now(UTC),
        stored=False,
    )


# --------------------------------------------------------------------------- refreshing


def _static_rate() -> Decimal:
    return Decimal(current_app.config["FX_STATIC_USD_KES"])


def _sane(rate: Decimal) -> bool:
    static = _static_rate()
    return rate.is_finite() and static / SANE_RATE_FACTOR <= rate <= static * SANE_RATE_FACTOR


def fetch_open_er_api() -> Decimal:
    """KES per 1 USD from open.er-api.com. Raises ``RateUnavailable`` for anything unusable."""
    import json

    try:
        response = requests.get(OPEN_ER_API_URL, timeout=PROVIDER_TIMEOUT_SECONDS)
        response.raise_for_status()
        body = json.loads(response.text, parse_float=Decimal)
        value = body["rates"]["KES"]
        rate = Decimal(str(value)).quantize(RATE_SCALE)
    except (requests.RequestException, ValueError, KeyError, TypeError, InvalidOperation) as exc:
        raise RateUnavailable(f"{type(exc).__name__}") from exc
    if body.get("result") not in (None, "success") or not _sane(rate):
        raise RateUnavailable("rate outside the expected range")
    return rate


def _store(session: Session, rate: Decimal, source: str) -> FxRate:
    row = FxRate(base=BASE, quote=QUOTE, rate=rate, source=source, fetched_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


def refresh_rates(session: Session) -> FxRate:
    """Fetch and store today's rate; fall back as described in the module docstring.

    The caller commits. Returns the row that is now the latest rate.
    """
    config = current_app.config
    if config["FX_PROVIDER"] == PROVIDER_OPEN_ER_API:
        try:
            fetched = fetch_open_er_api()
        except RateUnavailable as exc:
            logger.warning("fx refresh failed (%s); keeping or falling back", exc)
        else:
            row = _store(session, fetched, PROVIDER_OPEN_ER_API)
            update_open_case_goals(session, row.rate)
            return row
        newest = session.execute(
            select(FxRate)
            .where(FxRate.base == BASE, FxRate.quote == QUOTE, FxRate.source != STATIC_SOURCE)
            .order_by(FxRate.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if newest is not None and datetime.now(UTC) - newest.fetched_at < LIVE_RATE_MAX_AGE:
            return newest
    row = _store(session, _static_rate(), STATIC_SOURCE)
    update_open_case_goals(session, row.rate)
    return row


def update_open_case_goals(session: Session, rate: Decimal) -> int:
    """Re-set the USD goal of live and funded cases at ``rate``. Returns how many changed.

    A column-only UPDATE: it does not bump the case's version number, so a donation being
    recorded at the same moment is not turned away with a conflict.
    """
    cases = session.execute(
        select(Case.id, Case.target_kes_minor, Case.buffer_bps, Case.usd_goal_minor).where(
            Case.status.in_(GOAL_STATUSES), Case.target_kes_minor.is_not(None)
        )
    ).all()
    changed = 0
    for case_id, target, buffer_bps, current in cases:
        goal = usd_goal_minor(target, buffer_bps, rate)
        if goal != current:
            session.execute(
                update(Case.__table__).where(Case.id == case_id).values(usd_goal_minor=goal)
            )
            changed += 1
    return changed
