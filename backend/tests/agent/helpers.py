"""A scripted stand-in for the language model, and builders for the money the agent looks at."""

import uuid
from types import SimpleNamespace
from typing import Any

from app.ai import llm


def say(text: str) -> SimpleNamespace:
    """A model turn that only talks: the loop ends."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn"
    )


def use(name: str, arguments: dict[str, Any] | None = None, *, call_id: str | None = None):  # type: ignore[no-untyped-def]
    """A model turn that asks for one tool."""
    block = SimpleNamespace(
        type="tool_use",
        id=call_id or f"toolu_{uuid.uuid4().hex[:8]}",
        name=name,
        input=arguments or {},
    )
    return SimpleNamespace(content=[block], stop_reason="tool_use")


def use_many(*calls: tuple[str, dict[str, Any]]) -> SimpleNamespace:
    """A model turn that asks for several tools at once."""
    blocks = [
        SimpleNamespace(type="tool_use", id=f"toolu_{i}", name=name, input=arguments)
        for i, (name, arguments) in enumerate(calls)
    ]
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


class ScriptedModel:
    """Answers each call with the next scripted turn and keeps every request it was sent."""

    def __init__(self, *turns: Any) -> None:
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        # Copy the conversation now: the runner keeps appending to the same list.
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        if not self.turns:
            return say("(the script ran out)")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def last_tool_results(self) -> list[dict[str, Any]]:
        """The tool results the model was shown in its most recent request."""
        message = self.requests[-1]["messages"][-1]
        return message["content"] if isinstance(message["content"], list) else []


def configure(app: Any, monkeypatch: Any, *turns: Any) -> ScriptedModel:
    """Switch the agent on with a configured model that follows ``turns``."""
    monkeypatch.setitem(app.config, "AGENT_ENABLED", True)
    monkeypatch.setitem(app.config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setitem(app.config, "LLM_API_KEY", "test-key-not-real")
    monkeypatch.setitem(app.config, "LLM_MODEL", "test-model")
    model = ScriptedModel(*turns)
    monkeypatch.setattr(llm, "client_factory", lambda: model)
    return model
