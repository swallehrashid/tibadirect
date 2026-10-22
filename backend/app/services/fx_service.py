"""USD to KES rates and the donation goal they set.

This is the part the hospital confirmation needs: the latest stored rate and the USD goal for a
confirmed bill. The hourly refresh job and the live rate provider arrive with the payments work
(P3-F02); until then, and whenever no rate is stored, the configured static rate is used.
"""

from datetime import UTC, datetime
from decimal import Decimal

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.money import FxRate
from app.money import usd_goal_minor

BASE, QUOTE = "USD", "KES"
STATIC_SOURCE = "static"


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
