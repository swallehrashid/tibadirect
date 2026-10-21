"""Schemas for consent wording and a case's consents."""

import marshmallow as ma

from app.models.enums import ConsentActor, ConsentScope

LANGUAGES = ["en", "sw"]


class ConsentTextsQuerySchema(ma.Schema):
    class Meta:
        unknown = ma.EXCLUDE

    lang = ma.fields.String(load_default="en", validate=ma.validate.OneOf(LANGUAGES))


class ConsentTextSchema(ma.Schema):
    scope = ma.fields.String()
    language = ma.fields.String()
    version = ma.fields.String(metadata={"description": "Send this back when granting"})
    title = ma.fields.String()
    body = ma.fields.String()


class ConsentTextsSchema(ma.Schema):
    language = ma.fields.String()
    items = ma.fields.List(ma.fields.Nested(ConsentTextSchema))


class GrantConsentSchema(ma.Schema):
    scope = ma.fields.Enum(ConsentScope, by_value=True, required=True)
    actor = ma.fields.Enum(ConsentActor, by_value=True, required=True)
    wording_version = ma.fields.String(required=True, validate=ma.validate.Length(min=1, max=20))
    language = ma.fields.String(required=True, validate=ma.validate.OneOf(LANGUAGES))


class ConsentSchema(ma.Schema):
    id = ma.fields.UUID()
    scope = ma.fields.String()
    actor = ma.fields.String()
    wording_version = ma.fields.String()
    language = ma.fields.String()
    granted_at = ma.fields.DateTime()
    withdrawn_at = ma.fields.DateTime(allow_none=True)
    active = ma.fields.Boolean()


class ConsentListSchema(ma.Schema):
    items = ma.fields.List(ma.fields.Nested(ConsentSchema))
