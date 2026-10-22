"""Builders for donation tests: a live public case at a known rate, and a donation through it."""

from decimal import Decimal

from app.models.case import Case
from app.models.enums import CaseStatus, ConsentScope, ExcessChoice
from app.models.money import Donation
from app.payments.paypal_mock import MockPayPal
from app.services import donation_service
from tests.factories import CaseFactory, ConsentFactory, FxRateFactory

RATE = Decimal("129.00000000")


def live_case(target_kes_minor: int = 10_000_000, buffer_bps: int = 500, **fields: object) -> Case:
    """A public, live case with a confirmed target of KES 100,000 at 129 KES per dollar."""
    FxRateFactory(rate=RATE)
    case = CaseFactory(
        status=CaseStatus.LIVE,
        target_kes_minor=target_kes_minor,
        buffer_bps=buffer_bps,
        is_estimate=False,
        **fields,
    )
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    return case


def order_for(
    session,  # type: ignore[no-untyped-def]
    case: Case,
    amount: str = "25.00",
    *,
    excess: ExcessChoice = ExcessChoice.REFUND,
    name: str | None = None,
    anonymous: bool = True,
) -> Donation:
    donation, _ = donation_service.create_order(
        session,
        case_slug=case.public_slug,
        amount_usd=Decimal(amount),
        excess_choice=excess,
        donor_display_name=name,
        is_anonymous=anonymous,
    )
    return donation


def donate(
    session,  # type: ignore[no-untyped-def]
    case: Case,
    amount: str = "25.00",
    **order_options: object,
) -> Donation:
    """An order made and captured: the donation as it stands once the money is in."""
    donation = order_for(session, case, amount, **order_options)  # type: ignore[arg-type]
    return donation_service.capture_order(session, donation.paypal_order_id).donation


def calls(paypal: MockPayPal, operation: str, monkeypatch) -> list[tuple]:  # type: ignore[no-untyped-def]
    """Record calls to one of the fake's methods without changing what it does."""
    seen: list[tuple] = []
    original = getattr(paypal, operation)

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(paypal, operation, spy)
    return seen
