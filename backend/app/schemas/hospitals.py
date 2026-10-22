"""Schemas for the hospital directory."""

import marshmallow as ma


class HospitalQuerySchema(ma.Schema):
    class Meta:
        unknown = ma.EXCLUDE

    query = ma.fields.String(load_default=None, validate=ma.validate.Length(max=80))


class HospitalListItemSchema(ma.Schema):
    id = ma.fields.UUID()
    display_name = ma.fields.String()
    county = ma.fields.String()
