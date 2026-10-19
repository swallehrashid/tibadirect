"""Schemas that document the error envelope in the OpenAPI document."""

import marshmallow as ma


class ErrorBodySchema(ma.Schema):
    code = ma.fields.String(metadata={"description": "Stable machine-readable error code"})
    message = ma.fields.String(metadata={"description": "Human-readable summary"})
    details = ma.fields.Dict(metadata={"description": "Extra structured information"})
    request_id = ma.fields.String(
        allow_none=True, metadata={"description": "Echo of X-Request-ID for support"}
    )


class ErrorEnvelopeSchema(ma.Schema):
    """Every error response has this shape: ``{"error": {...}}``."""

    error = ma.fields.Nested(ErrorBodySchema)
