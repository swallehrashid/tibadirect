"""Gemini behind the Anthropic-shaped ``messages.create`` call, without the network."""

import json
from typing import Any

import pytest
from flask import Flask

from app.ai import gemini, llm
from app.ai.gemini import GeminiClient, GeminiError


class FakePost:
    def __init__(self, *answers: dict[str, Any], status: int = 200) -> None:
        self.answers = list(answers)
        self.status = status
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, *, headers: dict, json: dict, timeout: float) -> Any:  # noqa: A002
        assert url.endswith("/chat/completions")
        assert headers["Authorization"] == "Bearer key-for-tests"
        self.bodies.append(json)
        answer = self.answers.pop(0) if self.answers else {}
        status = self.status

        class Reply:
            ok = status < 400
            status_code = status

            def json(self) -> dict[str, Any]:
                return answer

        return Reply()


def _choice(content: str | None = None, calls: list[dict[str, Any]] | None = None) -> dict:
    return {"choices": [{"message": {"content": content, "tool_calls": calls}}]}


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
        "extra_content": {"google": {"thought_signature": "sig-" + call_id}},
    }


@pytest.fixture
def post(monkeypatch: pytest.MonkeyPatch) -> FakePost:
    fake = FakePost()
    monkeypatch.setattr(gemini.requests, "post", fake)
    return fake


def test_a_forced_tool_call_comes_back_as_a_tool_use_block(post: FakePost) -> None:
    post.answers.append(_choice(calls=[_call("c1", "record_bill", {"total_kes": 15500})]))
    client = GeminiClient("key-for-tests", timeout=5)
    response = client.messages.create(
        model="gemini-test",
        max_tokens=100,
        temperature=0,
        system="Extract.",
        tools=[llm._tool()],
        tool_choice={"type": "tool", "name": "record_bill"},
        messages=[{"role": "user", "content": "<document>TOTAL 15,500</document>"}],
    )
    [block] = response.content
    assert (block.type, block.name) == ("tool_use", "record_bill")
    assert block.input == {"total_kes": 15500}
    assert response.stop_reason == "tool_use"
    body = post.bodies[0]
    assert body["messages"][0] == {"role": "system", "content": "Extract."}
    assert body["tool_choice"] == {"type": "function", "function": {"name": "record_bill"}}
    assert body["temperature"] == 0
    parameters = json.dumps(body["tools"][0]["function"]["parameters"])
    assert "$ref" not in parameters  # Gemini does not follow refs
    assert "$defs" not in parameters
    assert "amount_kes" in parameters  # the line item definition was inlined


def test_a_tool_round_trip_sends_the_raw_call_and_the_result_back(post: FakePost) -> None:
    post.answers += [
        _choice(content="Looking.", calls=[_call("c1", "case_summary", {"case_id": "x"})]),
        _choice(content="All settled."),
    ]
    client = GeminiClient("key-for-tests", timeout=5)
    messages: list[dict[str, Any]] = [{"role": "user", "content": "How is case x?"}]
    first = client.messages.create(model="m", max_tokens=50, messages=messages)
    messages.append(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking."},
                {"type": "tool_use", "id": "c1", "name": "case_summary", "input": {}},
            ],
        }
    )
    messages.append(
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]}
    )
    second = client.messages.create(model="m", max_tokens=50, messages=messages)

    assert [b.type for b in first.content] == ["text", "tool_use"]
    assert [(b.type, b.text) for b in second.content] == [("text", "All settled.")]
    assert second.stop_reason == "end_turn"
    sent = post.bodies[1]["messages"]
    assert sent[1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig-c1"
    assert sent[2] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


def test_a_refusal_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini.requests, "post", FakePost(status=503))
    with pytest.raises(GeminiError, match="503"):
        GeminiClient("key-for-tests", timeout=5).messages.create(
            model="m", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
        )


def test_gemini_is_a_supported_provider(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (
        ("LLM_PROVIDER", "gemini"),
        ("LLM_API_KEY", "key-for-tests"),
        ("LLM_MODEL", "gemini-test"),
    ):
        monkeypatch.setitem(app.config, name, value)
    with app.app_context():
        assert llm.available()
        assert isinstance(llm._real_client(), GeminiClient)
