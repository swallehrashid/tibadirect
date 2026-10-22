"""Schemas for the treasurer agent endpoints."""

import marshmallow as ma
from sqlalchemy.orm import object_session

from app.models.enums import AgentActionStatus
from app.models.identity import User


def _decider(action) -> str | None:  # type: ignore[no-untyped-def]
    session = object_session(action)
    if action.decided_by_id is None or session is None:
        return None
    user = session.get(User, action.decided_by_id)
    return user.display_name if user else None


class AskSchema(ma.Schema):
    question = ma.fields.String(
        required=True,
        validate=ma.validate.Length(min=3, max=1000),
        metadata={"description": "What the administrator wants to know or have looked at"},
    )
    case_id = ma.fields.UUID(
        load_default=None,
        metadata={"description": "The case the question is about, if there is one"},
    )


class ToolCallSchema(ma.Schema):
    name = ma.fields.String()
    input = ma.fields.Dict()
    ok = ma.fields.Boolean()
    summary = ma.fields.String(metadata={"description": "How much came back, or why it failed"})


class AgentActionSchema(ma.Schema):
    id = ma.fields.UUID()
    action_type = ma.fields.Function(lambda action: action.action_type.value)
    case_id = ma.fields.UUID(allow_none=True)
    case_alias = ma.fields.Function(lambda a: a.case.alias if a.case else None)
    payload = ma.fields.Dict()
    rationale = ma.fields.String()
    status = ma.fields.Function(lambda action: action.status.value)
    decided_by = ma.fields.Function(_decider)
    decided_at = ma.fields.DateTime(allow_none=True)
    executed_at = ma.fields.DateTime(allow_none=True)
    result = ma.fields.Dict(allow_none=True)
    error = ma.fields.String(allow_none=True)
    created_at = ma.fields.DateTime()


class AskResultSchema(ma.Schema):
    answer = ma.fields.String()
    tool_calls = ma.fields.List(ma.fields.Nested(ToolCallSchema))
    proposed_actions = ma.fields.List(ma.fields.Nested(AgentActionSchema))
    steps = ma.fields.Integer()
    stopped_early = ma.fields.Boolean(
        metadata={"description": "True if it ran out of steps before finishing its answer"}
    )


class ActionsQuerySchema(ma.Schema):
    class Meta:
        unknown = ma.EXCLUDE

    status = ma.fields.Enum(AgentActionStatus, by_value=True, load_default=None)


class RejectSchema(ma.Schema):
    note = ma.fields.String(
        required=True, metadata={"description": "Why, in 10 to 500 characters. Required."}
    )
