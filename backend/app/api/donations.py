"""Donation checkout and receipts. Public: donors need no account."""

from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.extensions import db, limiter
from app.money import format_amount
from app.schemas.donations import (
    CaptureResultSchema,
    CreateOrderSchema,
    OrderCreatedSchema,
    ReceiptSchema,
)
from app.services import donation_service

blp = TibaBlueprint(
    "donations", __name__, url_prefix="/api/donations", description="Donate to a case"
)

# Orders and captures reach PayPal, so each address gets a modest allowance.
ORDER_LIMIT = "20 per hour"
CAPTURE_LIMIT = "40 per hour"
# Receipt links are secrets; the limit makes guessing one pointless.
RECEIPT_LIMIT = "60 per minute"


@blp.route("/orders")
class Orders(MethodView):
    decorators = [limiter.limit(ORDER_LIMIT, scope="donation_order")]

    @blp.arguments(CreateOrderSchema)
    @blp.response(201, OrderCreatedSchema)
    @blp.alt_response(404, description="No such public case")
    @blp.alt_response(409, description="The case is not taking donations (case_not_accepting)")
    @blp.alt_response(422, description="Amount, name or choice not valid")
    @blp.alt_response(502, description="PayPal could not be reached")
    def post(self, payload: dict) -> dict:
        """Start a donation: creates the PayPal order the donor then approves in PayPal."""
        donation, _ = donation_service.create_order(
            db.session,
            case_slug=payload["case_slug"],
            amount_usd=payload["amount_usd"],
            excess_choice=payload["excess_choice"],
            donor_display_name=payload["donor_display_name"],
            is_anonymous=payload["is_anonymous"],
        )
        db.session.commit()
        return {
            "order_id": donation.paypal_order_id,
            "amount_usd_minor": donation.amount_usd_minor,
            "amount_usd": format_amount(donation.amount_usd_minor),
        }


@blp.route("/orders/<string:order_id>/capture")
class Capture(MethodView):
    decorators = [limiter.limit(CAPTURE_LIMIT, scope="donation_capture")]

    @blp.response(200, CaptureResultSchema)
    @blp.alt_response(404, description="No such order")
    @blp.alt_response(
        409, description="Case fully funded (nothing was captured), or payment pending"
    )
    @blp.alt_response(422, description="PayPal declined the payment")
    @blp.alt_response(502, description="PayPal could not be reached")
    def post(self, order_id: str) -> dict:
        """Take the money once the donor has approved the order. Repeating the call returns the
        same donation. If the case stopped needing money since the order was made, nothing is
        captured and the donor is not charged."""
        result = donation_service.capture_order(db.session, order_id)
        view = donation_service.receipt_view(db.session, result.donation)
        return {"receipt_token": view["receipt_token"], "donation": view}


@blp.route("/receipts/<string:receipt_token>")
class Receipt(MethodView):
    decorators = [limiter.limit(RECEIPT_LIMIT, scope="donation_receipt")]

    @blp.response(200, ReceiptSchema)
    @blp.alt_response(404, description="No such receipt")
    def get(self, receipt_token: str) -> dict:
        """A donation receipt: amount, fee, KES estimate, excess choice, case and ledger entry."""
        donation = donation_service.get_receipt_or_404(db.session, receipt_token)
        return donation_service.receipt_view(db.session, donation)
