"""Money helpers. Amounts are integer minor units (cents); floats are rejected everywhere.

USD and KES both have two decimal places, so one minor unit is 1/100 of the major unit.
Conversions use ``Decimal`` and ``ROUND_HALF_UP`` unless a caller asks for another mode.
"""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

MINOR_PER_MAJOR: Final = 100
BPS_DENOMINATOR: Final = 10_000
_CENT = Decimal("0.01")


class MoneyError(ValueError):
    """An amount or rate that cannot be used."""


def as_decimal(value: Decimal | int | str) -> Decimal:
    """Convert to a finite ``Decimal``. Floats are refused because they cannot hold money."""
    if isinstance(value, float):
        raise MoneyError("Use Decimal, int or str for money; floats are not allowed")
    if isinstance(value, bool):
        raise MoneyError("Booleans are not amounts")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise MoneyError(f"Not a valid amount: {value!r}") from exc
    if not result.is_finite():
        raise MoneyError("Amount must be finite")
    return result


def to_minor(amount: Decimal | int | str, *, rounding: str = ROUND_HALF_UP) -> int:
    """Major units to minor units: ``Decimal("25.005")`` becomes 2501."""
    return int((as_decimal(amount) * MINOR_PER_MAJOR).to_integral_value(rounding=rounding))


def from_minor(minor: int) -> Decimal:
    """Minor units to a two-place ``Decimal`` in major units."""
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise MoneyError("Minor units must be an integer")
    return (Decimal(minor) / MINOR_PER_MAJOR).quantize(_CENT)


def format_amount(minor: int) -> str:
    """Plain decimal string for APIs and PayPal: ``2500`` becomes ``"25.00"``."""
    return f"{from_minor(minor):.2f}"


def format_money(minor: int, currency: str) -> str:
    """Display string with thousands separators: ``(125000, "KES")`` becomes ``"KES 1,250.00"``."""
    return f"{currency} {from_minor(minor):,.2f}"


def apply_bps(minor: int, bps: int, *, rounding: str = ROUND_HALF_UP) -> int:
    """``minor`` multiplied by ``bps`` basis points (2500 bps is 25 percent)."""
    scaled = Decimal(minor) * Decimal(bps) / BPS_DENOMINATOR
    return int(scaled.to_integral_value(rounding=rounding))


def _positive_rate(rate: Decimal | int | str) -> Decimal:
    value = as_decimal(rate)
    if value <= 0:
        raise MoneyError("Exchange rate must be positive")
    return value


def usd_minor_to_kes_minor(
    usd_minor: int, rate: Decimal | int | str, *, rounding: str = ROUND_HALF_UP
) -> int:
    """Convert US cents to KES cents at ``rate`` (KES per 1 USD)."""
    converted = Decimal(usd_minor) * _positive_rate(rate)
    return int(converted.to_integral_value(rounding=rounding))


def kes_minor_to_usd_minor(
    kes_minor: int, rate: Decimal | int | str, *, rounding: str = ROUND_HALF_UP
) -> int:
    """Convert KES cents to US cents at ``rate`` (KES per 1 USD)."""
    converted = Decimal(kes_minor) / _positive_rate(rate)
    return int(converted.to_integral_value(rounding=rounding))


def usd_goal_minor(target_kes_minor: int, buffer_bps: int, rate: Decimal | int | str) -> int:
    """USD cents to ask for so the KES target is covered after the FX and fee buffer.

    ``ceil(target x (1 + buffer_bps / 10000) / rate)``, rounded up so the goal never falls short.
    """
    padded = Decimal(target_kes_minor) * (BPS_DENOMINATOR + buffer_bps) / BPS_DENOMINATOR
    return int((padded / _positive_rate(rate)).to_integral_value(rounding=ROUND_CEILING))
