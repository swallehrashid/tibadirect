"""Money policy settings, read once from the configuration so the rules share one source.

Amounts keep the unit of the money they describe: ``*_kes_minor`` is KES cents, ``*_usd_minor``
is US cents, ``*_bps`` is basis points (100 bps is one percent).
"""

from dataclasses import dataclass

from flask import current_app

from app.money import MINOR_PER_MAJOR


@dataclass(frozen=True)
class Policy:
    """How much to pay the hospital and when, and how small a refund can be."""

    tranche_min_kes_minor: int
    tranche_fraction_bps: int
    estimate_cap_bps: int
    refund_min_usd_minor: int
    follow_up_hold_days: int


def policy() -> Policy:
    config = current_app.config
    return Policy(
        tranche_min_kes_minor=config["TRANCHE_MIN_KES"] * MINOR_PER_MAJOR,
        tranche_fraction_bps=config["TRANCHE_FRACTION_BPS"],
        estimate_cap_bps=config["ESTIMATE_SETTLEMENT_CAP_BPS"],
        refund_min_usd_minor=config["REFUND_MIN_USD_MINOR"],
        follow_up_hold_days=config["FOLLOW_UP_HOLD_DAYS"],
    )
