"""``flask paypal order`` and ``flask paypal capture``: a donation without the website.

For checking the sandbox by hand: ``order`` creates a real PayPal order for a public case and
prints the link where the sandbox donor approves it; ``capture`` then takes the money.
"""

from decimal import Decimal, InvalidOperation

import click
from flask.cli import with_appcontext

from app.errors import AppError
from app.extensions import db
from app.models.enums import ExcessChoice
from app.money import format_amount
from app.services import donation_service


@click.group("paypal")
def paypal_group() -> None:
    """PayPal checkout commands."""


@paypal_group.command("order")
@click.argument("case_slug")
@click.argument("amount_usd")
@click.option(
    "--excess",
    type=click.Choice([choice.value for choice in ExcessChoice]),
    default=ExcessChoice.REFUND.value,
    show_default=True,
)
@with_appcontext
def order_command(case_slug: str, amount_usd: str, excess: str) -> None:
    """Create an order of AMOUNT_USD dollars for the public case CASE_SLUG."""
    try:
        amount = Decimal(amount_usd)
        donation, order = donation_service.create_order(
            db.session,
            case_slug=case_slug,
            amount_usd=amount,
            excess_choice=ExcessChoice(excess),
            donor_display_name=None,
            is_anonymous=True,
        )
    except (AppError, InvalidOperation) as exc:
        db.session.rollback()
        raise click.ClickException(getattr(exc, "message", str(exc))) from exc
    db.session.commit()
    approve = next(
        (link["href"] for link in order.get("links", []) if link["rel"] == "approve"), ""
    )
    click.echo(f"Order {donation.paypal_order_id} for ${format_amount(donation.amount_usd_minor)}.")
    click.echo(f"Approve it as the sandbox donor: {approve}")
    click.echo(f"Then run: flask paypal capture {donation.paypal_order_id}")


@paypal_group.command("capture")
@click.argument("order_id")
@with_appcontext
def capture_command(order_id: str) -> None:
    """Capture the approved order ORDER_ID and print the receipt link token."""
    try:
        result = donation_service.capture_order(db.session, order_id)
    except AppError as exc:
        db.session.rollback()
        raise click.ClickException(f"{exc.code}: {exc.message}") from exc
    donation = result.donation
    click.echo(
        f"Captured {donation.paypal_capture_id}: ${format_amount(donation.amount_usd_minor)} "
        f"gross, ${format_amount(donation.paypal_fee_usd_minor)} fee, "
        f"${format_amount(donation.net_usd_minor)} net."
    )
    click.echo(f"Receipt token: {donation.receipt_token}")
