"""Schemas for donation checkout and receipts."""

import marshmallow as ma

from app.models.enums import ExcessChoice
from app.money import format_amount as _usd


class CreateOrderSchema(ma.Schema):
    case_slug = ma.fields.String(required=True, validate=ma.validate.Length(min=1, max=40))
    amount_usd = ma.fields.Decimal(
        required=True,
        as_string=True,
        metadata={
            "description": "US dollars, $1 to $10,000, at most two decimals",
            "example": "25.00",
        },
    )
    excess_choice = ma.fields.Enum(
        ExcessChoice,
        by_value=True,
        load_default=ExcessChoice.REFUND,
        metadata={"description": "What happens to money the case does not need"},
    )
    donor_display_name = ma.fields.String(
        load_default=None,
        allow_none=True,
        metadata={"description": "Shown beside the donation in the public ledger"},
    )
    is_anonymous = ma.fields.Boolean(load_default=True)


class OrderCreatedSchema(ma.Schema):
    order_id = ma.fields.String(metadata={"description": "Give this to the PayPal button"})
    amount_usd_minor = ma.fields.Integer()
    amount_usd = ma.fields.String()


class ReceiptSchema(ma.Schema):
    """What a donor sees. No email address, no internal IDs, no full ledger hash."""

    receipt_token = ma.fields.String()
    status = ma.fields.String()
    amount_usd_minor = ma.fields.Integer()
    amount_usd = ma.fields.Function(lambda r: _usd(r["amount_usd_minor"]))
    paypal_fee_usd_minor = ma.fields.Integer()
    paypal_fee_usd = ma.fields.Function(lambda r: _usd(r["paypal_fee_usd_minor"]))
    net_usd_minor = ma.fields.Integer(metadata={"description": "What reaches the case"})
    net_usd = ma.fields.Function(lambda r: _usd(r["net_usd_minor"]))
    refunded_usd_minor = ma.fields.Integer()
    kes_estimate_minor = ma.fields.Integer(
        metadata={"description": "KES value of the net amount at the rate when it was paid"}
    )
    kes_estimate = ma.fields.Function(lambda r: _usd(r["kes_estimate_minor"]))
    fx_rate = ma.fields.String(allow_none=True)
    excess_choice = ma.fields.String()
    donor_display_name = ma.fields.String(allow_none=True)
    captured_at = ma.fields.DateTime(allow_none=True)
    case_slug = ma.fields.String()
    case_alias = ma.fields.String()
    hospital_name = ma.fields.String()
    ledger_reference = ma.fields.String(
        allow_none=True, metadata={"description": "The PayPal capture, shortened"}
    )
    ledger_hash_short = ma.fields.String(
        allow_none=True, metadata={"description": "First 12 characters of the ledger entry hash"}
    )


class CaptureResultSchema(ma.Schema):
    receipt_token = ma.fields.String()
    donation = ma.fields.Nested(ReceiptSchema)
