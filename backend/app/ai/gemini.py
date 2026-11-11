"""Google Gemini behind the same ``messages.create`` call the code makes to Anthropic.

``LLM_PROVIDER=gemini`` sends requests to Gemini's OpenAI-compatible endpoint, which has a free
tier. Requests and answers are translated so the bill reader, the appeal checker and the treasurer
agent run unchanged: a system prompt, messages whose content is text or a list of ``text``,
``tool_use`` and ``tool_result`` blocks, tools with an ``input_schema``, and an optional forced
tool. Answers come back as ``text`` and ``tool_use`` blocks with an ``input`` dictionary.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

import requests

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# The free tier answers "busy" (429, 503) now and then; a short wait usually clears it.
RETRY_STATUSES = frozenset({429, 500, 503})
RETRY_WAITS_SECONDS = (2, 5)


class GeminiError(RuntimeError):
    """Gemini refused the request or answered with something that is not a chat completion."""


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class Response:
    content: list[Block]
    stop_reason: str


def _inline_refs(schema: Any, defs: dict[str, Any]) -> Any:
    """The schema with every ``$ref`` replaced by its definition (Gemini does not follow them)."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            return _inline_refs(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline_refs(v, defs) for k, v in schema.items() if k != "$defs"}
    if isinstance(schema, list):
        return [_inline_refs(item, defs) for item in schema]
    return schema


def _text(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content)


def _tool(spec: dict[str, Any]) -> dict[str, Any]:
    schema = spec.get("input_schema") or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "parameters": _inline_refs(schema, schema.get("$defs", {})),
        },
    }


class _Messages:
    def __init__(self, client: "GeminiClient") -> None:
        self._client = client

    def create(self, **kwargs: Any) -> Response:
        return self._client.create(**kwargs)


class GeminiClient:
    """One per conversation: it remembers the raw tool calls, which Gemini wants sent back as is."""

    def __init__(self, api_key: str, timeout: float, base_url: str = BASE_URL) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.messages = _Messages(self)
        self._raw_calls: dict[str, dict[str, Any]] = {}

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Response:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [m for message in messages for m in self._to_openai(message)],
        }
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = [_tool(spec) for spec in tools]
        if tool_choice and tool_choice.get("type") == "tool":
            body["tool_choice"] = {"type": "function", "function": {"name": tool_choice["name"]}}
        for wait in (*RETRY_WAITS_SECONDS, None):
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=self.timeout,
            )
            if response.status_code not in RETRY_STATUSES or wait is None:
                break
            time.sleep(wait)
        if not response.ok:
            raise GeminiError(f"HTTP {response.status_code}")
        try:
            choice = response.json()["choices"][0]
        except (ValueError, KeyError, IndexError) as exc:
            raise GeminiError("no choices in the answer") from exc
        return self._from_openai(choice)

    def _to_openai(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        content = message["content"]
        if isinstance(content, str):
            return [{"role": message["role"], "content": content}]
        texts = [str(b.get("text", "")) for b in content if b.get("type") == "text"]
        if message["role"] == "assistant":
            calls = [
                self._raw_calls.get(b["id"])
                or {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))},
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            out: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                out["tool_calls"] = calls
            return [out]
        results = [
            {"role": "tool", "tool_call_id": b["tool_use_id"], "content": _text(b["content"])}
            for b in content
            if b.get("type") == "tool_result"
        ]
        return results + ([{"role": "user", "content": "\n".join(texts)}] if texts else [])

    def _from_openai(self, choice: dict[str, Any]) -> Response:
        message = choice.get("message") or {}
        blocks: list[Block] = []
        if message.get("content"):
            blocks.append(Block(type="text", text=str(message["content"])))
        for call in message.get("tool_calls") or []:
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                arguments = {}
            self._raw_calls[call["id"]] = call
            blocks.append(
                Block(
                    type="tool_use",
                    id=call["id"],
                    name=call["function"]["name"],
                    input=arguments if isinstance(arguments, dict) else {},
                )
            )
        stop = "tool_use" if message.get("tool_calls") else "end_turn"
        return Response(content=blocks, stop_reason=stop)
