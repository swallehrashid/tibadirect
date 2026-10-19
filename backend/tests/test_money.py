from decimal import ROUND_CEILING, Decimal

import pytest

from app import money
from app.money import MoneyError


@pytest.mark.parametrize(
    ("amount", "minor"),
    [
        ("25", 2500),
        ("25.00", 2500),
        (Decimal("0.01"), 1),
        (5, 500),
        ("25.005", 2501),  # half rounds up
        ("25.004", 2500),
        ("0.005", 1),
        ("-0.005", -1),  # half rounds away from zero
        ("1234567.89", 123456789),
    ],
)
def test_to_minor(amount, minor: int) -> None:
    assert money.to_minor(amount) == minor


def test_to_minor_honours_an_explicit_rounding_mode() -> None:
    assert money.to_minor("25.001", rounding=ROUND_CEILING) == 2501


@pytest.mark.parametrize("bad", [25.0, True, "abc", "NaN", "Infinity", Decimal("NaN"), ""])
def test_to_minor_rejects_unusable_input(bad) -> None:
    with pytest.raises(MoneyError):
        money.to_minor(bad)


@pytest.mark.parametrize(
    ("minor", "text"), [(2500, "25.00"), (1, "0.01"), (0, "0.00"), (-150, "-1.50")]
)
def test_format_amount(minor: int, text: str) -> None:
    assert money.format_amount(minor) == text


def test_format_money_groups_thousands() -> None:
    assert money.format_money(12_500_000, "KES") == "KES 125,000.00"
    assert money.format_money(2500, "USD") == "USD 25.00"


def test_from_minor_round_trips_with_to_minor() -> None:
    for minor in (0, 1, 99, 100, 123456789):
        assert money.to_minor(money.from_minor(minor)) == minor


@pytest.mark.parametrize("bad", [1.5, "100", True])
def test_from_minor_requires_an_int(bad) -> None:
    with pytest.raises(MoneyError):
        money.from_minor(bad)


def test_apply_bps() -> None:
    assert money.apply_bps(10_000_000, 2500) == 2_500_000
    assert money.apply_bps(10_000_000, 8000) == 8_000_000
    assert money.apply_bps(333, 500) == 17  # 16.65 rounds half up
    assert money.apply_bps(333, 500, rounding=ROUND_CEILING) == 17
    assert money.apply_bps(10, 5, rounding=ROUND_CEILING) == 1


def test_usd_to_kes_uses_the_rate_in_minor_units() -> None:
    # $25.00 at 129.00 KES per USD is KES 3,225.00
    assert money.usd_minor_to_kes_minor(2500, Decimal("129.00")) == 322_500
    # $10.01 at 128.4567 is KES 1,285.851567, which rounds to the cent
    assert money.usd_minor_to_kes_minor(1001, Decimal("128.4567")) == 128_585
    # exactly half a cent rounds up: 1 US cent at 100.5 is 100.5 KES cents
    assert money.usd_minor_to_kes_minor(1, Decimal("100.5")) == 101


def test_kes_to_usd_rounds_half_up_by_default_and_can_round_up() -> None:
    assert money.kes_minor_to_usd_minor(322_500, Decimal("129.00")) == 2500
    assert money.kes_minor_to_usd_minor(100_000, Decimal("129.00")) == 775  # 775.19..
    assert money.kes_minor_to_usd_minor(100_000, Decimal("129.00"), rounding=ROUND_CEILING) == 776


@pytest.mark.parametrize("rate", [0, "-1", "abc", 129.0])
def test_rate_must_be_positive_and_not_a_float(rate) -> None:
    with pytest.raises(MoneyError):
        money.usd_minor_to_kes_minor(100, rate)


def test_conversion_never_uses_floats() -> None:
    # 0.1 + 0.2 style drift cannot occur: 3 x $0.10 at rate 1.1 stays exact.
    total = sum(money.usd_minor_to_kes_minor(10, Decimal("1.1")) for _ in range(3))
    assert total == 33
