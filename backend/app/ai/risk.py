"""From signals to a score, a band and a route.

``risk_score`` is the sum of the weights of the signals raised, capped at 100. The bands are
low (under 30), medium (30 to 69) and high (70 or more). A bill goes to manual review if its band
is high or any signal that forces review is present (a duplicate or a patient already in an open
case); otherwise it goes to the hospital. Nothing here ever rejects a bill: a person decides what
manual review means, and the hospital's confirmation is the gate before any money moves.
"""

from dataclasses import dataclass

from app.ai.signals import Signal
from app.models.enums import RiskBand, TriageRoute

MEDIUM_FROM = 30
HIGH_FROM = 70
MAX_SCORE = 100


@dataclass(frozen=True)
class Risk:
    score: int
    band: RiskBand
    route: TriageRoute


def band_for(score: int) -> RiskBand:
    if score >= HIGH_FROM:
        return RiskBand.HIGH
    if score >= MEDIUM_FROM:
        return RiskBand.MEDIUM
    return RiskBand.LOW


def assess(signals: list[Signal]) -> Risk:
    score = min(MAX_SCORE, sum(signal.weight for signal in signals))
    band = band_for(score)
    forced = any(signal.forces_manual_review for signal in signals)
    route = (
        TriageRoute.MANUAL_REVIEW
        if band == RiskBand.HIGH or forced
        else TriageRoute.HOSPITAL_CONFIRMATION
    )
    return Risk(score, band, route)
