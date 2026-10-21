"""Schemas for the authentication endpoints."""

import marshmallow as ma


class CsrfTokenSchema(ma.Schema):
    csrf_token = ma.fields.String(
        metadata={
            "description": "Send this in the X-CSRFToken header of every POST, PATCH and DELETE"
        }
    )
