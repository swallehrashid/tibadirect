"""The tool-use loop: ask the model, run the tools it names, give the results back, repeat.

At most ``MAX_STEPS`` model calls per question. The model can read and propose and nothing else:
the only tools it is given are the ones in ``tools.py``, every call is checked against that list
and its schema, and every call is written to the audit log with actor ``agent`` and the
administrator who asked. What it proposes is saved as ``proposed`` actions and waits.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from flask import current_app
from sqlalchemy.orm import Session

from app.ai import llm
from app.ai.agent import tools
from app.ai.agent.prompts import MAX_STEPS, SYSTEM_PROMPT
from app.errors import AppError
from app.models.enums import ActorType
from app.models.identity import User
from app.models.platform import AgentAction
from app.services import audit_service

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 1500
MAX_PROPOSALS = 3
MAX_TRACE_VALUE = 120
SUMMARY_LIMIT = 200


class AgentUnavailable(AppError):
    status_code = 503
    code = "agent_unavailable"
    message = "The assistant is not available. A language model has not been set up."


class AgentFailed(AppError):
    status_code = 502
    code = "agent_failed"
    message = "The assistant could not get an answer from the language model. Try again shortly."


@dataclass
class AgentRun:
    answer: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    proposed_action_ids: list[uuid.UUID] = field(default_factory=list)
    steps: int = 0
    stopped_early: bool = False


def _block_to_dict(block: Any) -> dict[str, Any]:
    """A response block as the plain dictionary the next request sends back."""
    kind = getattr(block, "type", None)
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input if isinstance(block.input, dict) else {},
        }
    return {"type": "text", "text": str(getattr(block, "text", ""))}


def _trace_input(arguments: Any) -> dict[str, Any]:
    """What is kept of a call's input: short values only, so a long reason is not copied around."""
    if not isinstance(arguments, dict):
        return {}
    return {
        str(key): (str(value)[:MAX_TRACE_VALUE] if not isinstance(value, bool) else value)
        for key, value in list(arguments.items())[:10]
    }


def _summary(result: dict[str, Any], ok: bool) -> str:
    if not ok:
        return str(result.get("error", "failed"))[:SUMMARY_LIMIT]
    for key in ("donations", "entries", "settlements", "transactions"):
        if key in result:
            return f"{len(result[key])} {key}"
    if "action_id" in result:
        return "proposal written, waiting for approval"
    return "ok"


def ask(
    session: Session,
    admin: User,
    question: str,
    *,
    case_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> AgentRun:
    """Answer an administrator's question. Commits what it audited and proposed."""
    if not llm.available():
        raise AgentUnavailable()
    run_id = uuid.uuid4()
    target = audit_service.Target("agent_run", run_id)
    audit_service.record(
        "agent.asked",
        target,
        actor=ActorType.AGENT,
        approver=admin,
        request_id=request_id,
        details={"run_id": str(run_id), "case_id": case_id, "question_chars": len(question)},
    )
    client = llm.client_factory()
    first = question if case_id is None else f"{question}\n\n(The case in question: {case_id}.)"
    messages: list[dict[str, Any]] = [{"role": "user", "content": first}]
    run = AgentRun()

    for _ in range(MAX_STEPS):
        run.steps += 1
        try:
            response = client.messages.create(
                model=current_app.config["LLM_MODEL"],
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0,
                system=SYSTEM_PROMPT,
                tools=tools.specs(),
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 - any provider failure is the same to the caller
            logger.warning("treasurer agent model call failed (%s)", type(exc).__name__)
            session.commit()  # keep the audit trail and any proposals already written
            raise AgentFailed() from exc
        blocks = list(getattr(response, "content", None) or [])
        messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in blocks]})
        calls = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
        if not calls:
            run.answer = "\n".join(
                str(b.text).strip() for b in blocks if getattr(b, "type", None) == "text"
            ).strip()
            break
        results = []
        for block in calls:
            result, ok = _run_one(session, admin, block, run, run_id, request_id)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tools.render(result),
                    **({} if ok else {"is_error": True}),
                }
            )
        messages.append({"role": "user", "content": results})
    else:
        run.stopped_early = True
        run.answer = (
            "I stopped after "
            f"{MAX_STEPS} steps without finishing. What I found so far is in the tool calls."
        )

    session.commit()
    return run


def _run_one(
    session: Session,
    admin: User,
    block: Any,
    run: AgentRun,
    run_id: uuid.UUID,
    request_id: str | None,
) -> tuple[dict[str, Any], bool]:
    name = str(block.name)
    arguments = block.input
    spec = tools.BY_NAME.get(name)
    if spec is not None and spec.proposes and len(run.proposed_action_ids) >= MAX_PROPOSALS:
        result, ok = {"error": f"At most {MAX_PROPOSALS} proposals in one answer."}, False
    else:
        result, ok = tools.call(session, admin, name, arguments)
    if ok and spec is not None and spec.proposes and "action_id" in result:
        action_id = uuid.UUID(result["action_id"])
        if action_id not in run.proposed_action_ids:
            run.proposed_action_ids.append(action_id)
    run.tool_calls.append(
        {
            "name": name,
            "input": _trace_input(arguments),
            "ok": ok,
            "summary": _summary(result, ok),
        }
    )
    audit_service.record(
        "agent.tool_called",
        audit_service.Target("agent_run", run_id),
        actor=ActorType.AGENT,
        approver=admin,
        request_id=request_id,
        details={
            "run_id": str(run_id),
            "tool": name[:60],
            "input": _trace_input(arguments),
            "ok": ok,
        },
    )
    return result, ok


def proposed_actions(session: Session, run: AgentRun) -> list[AgentAction]:
    """The actions the run proposed, in the order it proposed them."""
    found = [session.get(AgentAction, action_id) for action_id in run.proposed_action_ids]
    return [action for action in found if action is not None]
