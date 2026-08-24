"""The Ollama chat-history serialization bug bit in production: string
tool-call arguments and a missing tool_name made qwen return empty
responses, so the agent retried identical calls until max_iterations."""
from app.agents.providers.base import ChatMessage, ToolCall
from app.agents.providers.ollama import _coerce_arguments, _serialize_messages


def test_string_arguments_become_objects():
    assert _coerce_arguments('{"query": "SV", "limit": 5}') == {"query": "SV", "limit": 5}


def test_dict_arguments_pass_through():
    assert _coerce_arguments({"query": "SV"}) == {"query": "SV"}


def test_invalid_arguments_degrade_to_empty_object():
    assert _coerce_arguments("not json") == {}
    assert _coerce_arguments(None) == {}
    assert _coerce_arguments('["list"]') == {}


def test_assistant_tool_call_serializes_object_arguments():
    msg = ChatMessage(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id="c1", name="search", arguments='{"query": "SV"}')],
    )
    [out] = _serialize_messages([msg])
    assert out["tool_calls"][0]["function"]["arguments"] == {"query": "SV"}


def test_tool_result_carries_tool_name():
    msg = ChatMessage(role="tool", content='{"ok": true}', tool_call_id="c1", name="search")
    [out] = _serialize_messages([msg])
    assert out["tool_name"] == "search"
    assert out["name"] == "search"
    assert out["content"] == '{"ok": true}'
