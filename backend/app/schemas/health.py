"""Schemas for the health endpoint."""

import marshmallow as ma


class HealthSchema(ma.Schema):
    status = ma.fields.String(metadata={"description": "ok, degraded or error"})
    version = ma.fields.String()
    db = ma.fields.String(metadata={"description": "up or down"})
    redis = ma.fields.String(metadata={"description": "up or down"})
