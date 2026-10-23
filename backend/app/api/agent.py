"""The treasurer agent: ask a question, and approve or reject what it proposes (admin only)."""

import uuid

from flask import current_app
from flask.views import MethodView

from app.ai.agent import runner
from app.api.base import TibaBlueprint
from app.errors import NotFound
from app.extensions import db, limiter
from app.models.enums import UserRole
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.schemas.agent import (
    ActionsQuerySchema,
    AgentActionSchema,
    AskResultSchema,
    AskSchema,
    RejectSchema,
)
from app.security.permissions import current_account, get_case_for_admin_or_404
from app.services import agent_service

blp = TibaBlueprint(
    "agent", __name__, url_prefix="/api/admin/agent", description="Treasurer agent (admin)"
)

ASK_LIMIT = "30 per hour"


@blp.before_request
def _only_when_switched_on() -> None:
    """With the flag off these routes do not exist, whoever asks and whatever they send."""
    if not current_app.config["AGENT_ENABLED"]:
        raise NotFound("The treasurer agent is not switched on.", code="feature_disabled")


@blp.route("/ask")
class Ask(MethodView):
    decorators = [limiter.limit(ASK_LIMIT, scope="agent_ask")]

    @blp.secured(UserRole.ADMIN)
    @blp.arguments(AskSchema)
    @blp.response(200, AskResultSchema)
    @blp.alt_response(404, description="The agent is switched off, or there is no such case")
    @blp.alt_response(429, description="More than 30 questions an hour")
    @blp.alt_response(502, description="The language model could not be reached")
    @blp.alt_response(503, description="No language model is configured")
    def post(self, payload: dict) -> dict:
        """Ask about the money. It reads the books and may propose a refund, a payout or a freeze;
        a proposal waits in the actions list until an administrator approves it."""
        if payload["case_id"] is not None:
            get_case_for_admin_or_404(payload["case_id"])
        run = runner.ask(
            db.session, current_account(), payload["question"], case_id=payload["case_id"]
        )
        return {
            "answer": run.answer,
            "tool_calls": run.tool_calls,
            "proposed_actions": runner.proposed_actions(db.session, run),
            "steps": run.steps,
            "stopped_early": run.stopped_early,
        }


@blp.route("/actions")
class Actions(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.arguments(ActionsQuerySchema, location="query")
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(AgentActionSchema))
    def get(self, filters: dict, paging: dict) -> dict:
        """Proposals, the ones waiting first. Filter by ``status``."""
        return paginate(db.session, agent_service.actions(filters["status"]), paging)


@blp.route("/actions/<uuid:action_id>/approve")
class ApproveAction(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.response(200, AgentActionSchema)
    @blp.alt_response(404, description="No such action")
    @blp.alt_response(409, description="Already decided")
    def post(self, action_id: uuid.UUID) -> object:
        """Approve a proposal and carry it out through the normal service. If it can no longer be
        done the answer is the action with status ``failed`` and the reason in ``error``."""
        return agent_service.approve(db.session, action_id, current_account())


@blp.route("/actions/<uuid:action_id>/reject")
class RejectAction(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.arguments(RejectSchema)
    @blp.response(200, AgentActionSchema)
    @blp.alt_response(404, description="No such action")
    @blp.alt_response(409, description="Already decided")
    def post(self, payload: dict, action_id: uuid.UUID) -> object:
        """Turn a proposal down. A reason is required."""
        action = agent_service.reject(db.session, action_id, current_account(), payload["note"])
        db.session.commit()
        return action
