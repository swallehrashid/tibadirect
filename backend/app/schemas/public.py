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
