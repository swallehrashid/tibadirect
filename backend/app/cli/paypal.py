"""``flask paypal order``, ``capture``, ``invoice`` and ``invoice-status``: PayPal by hand.

For checking the sandbox without the website: ``order`` creates a real PayPal order for a public
case and prints the link where the sandbox donor approves it; ``capture`` then takes the money.
``invoice`` issues the hospital's invoice for a confirmed bill, and records any tranches already
paid on it; ``invoice-status`` reads the invoice's status back from PayPal.
"""

import uuid
from decimal import Decimal, InvalidOperation

import click
from flask.cli import with_appcontext

from app.errors import AppError
from app.extensions import db
from app.models.enums import ExcessChoice, SettlementStatus
from app.models.verification import Confirmation
from app.money import format_amount
from app.services import donation_service, invoicing_service


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


def _confirmation(confirmation_id: str) -> Confirmation:
    try:
        found = db.session.get(Confirmation, uuid.UUID(confirmation_id))
    except ValueError as exc:
        raise click.ClickException("That is not a confirmation ID.") from exc
    if found is None:
        raise click.ClickException("No such confirmation.")
    return found


@paypal_group.command("invoice")
@click.argument("confirmation_id")
@click.option("--retry", is_flag=True, help="Try again after an invoice was marked failed.")
@with_appcontext
def invoice_command(confirmation_id: str, retry: bool) -> None:
    """Issue the PayPal invoice for the confirmed bill CONFIRMATION_ID, as its hospital."""
    confirmation = _confirmation(confirmation_id)
    if retry and confirmation.invoice_status == invoicing_service.FAILED:
        confirmation.invoice_status = "DRAFT" if confirmation.paypal_invoice_id else None
        db.session.commit()
    try:
        outcome = invoicing_service.issue(db.session, confirmation.id)
        recorded = 0
        for settlement in sorted(confirmation.settlements, key=lambda s: s.tranche_no):
            if (
                settlement.status == SettlementStatus.SUCCEEDED
                and not settlement.invoice_payment_id
            ):
                result = invoicing_service.record_payment(db.session, settlement.id)
                recorded += result == "recorded"
    except AppError as exc:
        db.session.rollback()
        raise click.ClickException(f"{exc.code}: {exc.message}") from exc
    db.session.refresh(confirmation)
    click.echo(f"Invoice: {outcome}. Status: {confirmation.invoice_status or 'none'}.")
    if confirmation.paypal_invoice_id:
        click.echo(f"PayPal invoice ID: {confirmation.paypal_invoice_id}")
    if recorded:
        click.echo(f"Recorded {recorded} tranche payment(s) on it.")


@paypal_group.command("invoice-status")
@click.argument("confirmation_id")
@with_appcontext
def invoice_status_command(confirmation_id: str) -> None:
    """Read the status of the invoice for CONFIRMATION_ID back from PayPal."""
    confirmation = _confirmation(confirmation_id)
    try:
        status = invoicing_service.refresh_status(db.session, confirmation.id)
    except AppError as exc:
        db.session.rollback()
        raise click.ClickException(f"{exc.code}: {exc.message}") from exc
    click.echo(f"Status: {status or 'no invoice'}")
