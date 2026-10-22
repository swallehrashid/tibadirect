"""Schemas for the appeal checker."""

import marshmallow as ma


class AppealTextSchema(ma.Schema):
    text = ma.fields.String(
        required=True,
        validate=ma.validate.Length(min=20, max=5000),
        metadata={"description": "The message exactly as received. It is not stored."},
    )


class MatchedCaseSchema(ma.Schema):
    slug = ma.fields.String()
    alias = ma.fields.String()
    hospital_name = ma.fields.String()
    url = ma.fields.String()
    note = ma.fields.String(
        metadata={"description": "This appeal matches a hospital-confirmed TibaDirect case"}
    )


class AppealResultSchema(ma.Schema):
    score = ma.fields.Integer(metadata={"description": "0 (no concern) to 100"})
    band = ma.fields.String(metadata={"description": "low, medium or high"})
    flagged = ma.fields.Boolean(metadata={"description": "True if it reads as likely a scam"})
    reasons_en = ma.fields.List(ma.fields.String())
    reasons_sw = ma.fields.List(ma.fields.String())
    matched_case = ma.fields.Nested(MatchedCaseSchema, allow_none=True)
    safety_tip_en = ma.fields.String()
    safety_tip_sw = ma.fields.String()
    explanation = ma.fields.String(
        allow_none=True, metadata={"description": "Only when the optional language model is on"}
    )
    used_llm = ma.fields.Boolean()
