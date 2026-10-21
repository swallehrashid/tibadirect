"""Schemas for the authentication endpoints."""

import marshmallow as ma


class CsrfTokenSchema(ma.Schema):
    csrf_token = ma.fields.String(
        metadata={
            "description": "Send this in the X-CSRFToken header of every POST, PATCH and DELETE"
        }
    )


class RequestCodeSchema(ma.Schema):
    email = ma.fields.Email(required=True)


class AcceptedSchema(ma.Schema):
    message = ma.fields.String()


class VerifyCodeSchema(ma.Schema):
    email = ma.fields.Email(required=True)
    code = ma.fields.String(required=True, validate=ma.validate.Regexp(r"^\s*\d{6}\s*$"))


class HospitalSummarySchema(ma.Schema):
    id = ma.fields.UUID()
    display_name = ma.fields.String()
    county = ma.fields.String()
    staff_role = ma.fields.String()


class UserSchema(ma.Schema):
    id = ma.fields.UUID()
    email = ma.fields.String()
    display_name = ma.fields.String()
    role = ma.fields.String()
    preferred_language = ma.fields.String()
    hospital = ma.fields.Nested(HospitalSummarySchema, allow_none=True)


class SessionSchema(ma.Schema):
    user = ma.fields.Nested(UserSchema)


class UpdateMeSchema(ma.Schema):
    display_name = ma.fields.String(validate=ma.validate.Length(min=1, max=80))
    preferred_language = ma.fields.String(validate=ma.validate.OneOf(["en", "sw"]))

    @ma.pre_load
    def _strip(self, data: dict, **kwargs: object) -> dict:
        if isinstance(data, dict) and isinstance(data.get("display_name"), str):
            data = {**data, "display_name": data["display_name"].strip()}
        return data
