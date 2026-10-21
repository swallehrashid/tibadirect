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
