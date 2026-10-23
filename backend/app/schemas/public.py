"""Schemas for the public endpoints."""

import marshmallow as ma


class FeatureFlagsSchema(ma.Schema):
    agent = ma.fields.Boolean()
    appeal_checker = ma.fields.Boolean()


class PublicConfigSchema(ma.Schema):
    paypal_client_id = ma.fields.String(
        metadata={"description": "Public PayPal client ID for the JS SDK; empty in mock mode"}
    )
    paypal_mode = ma.fields.String(metadata={"description": "sandbox or mock"})
    currency = ma.fields.String(metadata={"description": "Donations are always in USD"})
    demo_banner = ma.fields.Boolean(metadata={"description": "Show the sandbox money banner"})
    features = ma.fields.Nested(FeatureFlagsSchema)


class PublicStatsSchema(ma.Schema):
    public_cases = ma.fields.Integer(metadata={"description": "Cases with a public page"})
    open_cases = ma.fields.Integer(metadata={"description": "Of those, still taking donations"})
    pledged_kes_minor = ma.fields.Integer()
    settled_kes_minor = ma.fields.Integer(metadata={"description": "Already paid to hospitals"})
    donations = ma.fields.Integer()
    hospitals = ma.fields.Integer()


class PublicFxSchema(ma.Schema):
    base = ma.fields.String(metadata={"example": "USD"})
    quote = ma.fields.String(metadata={"example": "KES"})
    rate = ma.fields.String(
        metadata={"description": "KES for 1 USD, as a decimal string", "example": "129.00000000"}
    )
    source = ma.fields.String(metadata={"description": "open_er_api, or static for the fallback"})
    fetched_at = ma.fields.DateTime()
    is_live = ma.fields.Boolean(metadata={"description": "False when the rate is the static one"})
    is_stale = ma.fields.Boolean(
        metadata={"description": "A live rate that has not been refreshed for six hours"}
    )
