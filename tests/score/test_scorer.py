"""Scorer tests -- entirely mocked, no real Anthropic API calls."""

from types import SimpleNamespace

import pytest

from src.score.scorer import (
    SCORE_TOOL,
    _extract_description,
    build_scoring_prompt,
    parse_tool_response,
    score_posting,
)

# --- _extract_description ---


def test_extract_description_prefers_content_field():
    assert _extract_description({"content": "<p>Hello</p>"}) == "Hello"


def test_extract_description_falls_back_to_description_text():
    assert _extract_description({"descriptionText": "Plain text here"}) == "Plain text here"


def test_extract_description_falls_back_to_description_html():
    assert _extract_description({"descriptionHtml": "<p>HTML &amp; stuff</p>"}) == "HTML & stuff"


def test_extract_description_returns_none_when_missing():
    # Workday's cxs/jobs list endpoint never includes a description at all.
    assert _extract_description({"title": "Foo", "externalPath": "/job/x"}) is None


def test_extract_description_strips_tags_and_collapses_whitespace():
    result = _extract_description({"content": "<p>Line one</p>\n\n<p>Line   two</p>"})
    assert result == "Line one Line two"


# --- build_scoring_prompt ---


def test_prompt_includes_profile_and_job_fields():
    prompt = build_scoring_prompt(
        "## Education\nSome University",
        "Software Engineer",
        "ExampleCo",
        "New York, NY",
        {"content": "<p>Do some engineering</p>"},
    )
    assert "Some University" in prompt
    assert "Software Engineer" in prompt
    assert "ExampleCo" in prompt
    assert "New York, NY" in prompt
    assert "Do some engineering" in prompt


def test_prompt_notes_missing_description_explicitly():
    prompt = build_scoring_prompt("profile", "Title", "Co", "NYC", {})
    assert "no description available" in prompt


def test_prompt_truncates_very_long_descriptions():
    long_desc = "x" * 10000
    prompt = build_scoring_prompt("profile", "Title", "Co", "NYC", {"descriptionText": long_desc})
    # description block capped at 3000 chars so a pathological posting
    # doesn't blow out the prompt/token budget
    assert prompt.count("x") <= 3000


# --- parse_tool_response ---


def _tool_use_response(input_data: dict):
    block = SimpleNamespace(type="tool_use", name="submit_fit_score", input=input_data)
    return SimpleNamespace(content=[block])


def test_parse_tool_response_extracts_score_and_reasoning():
    response = _tool_use_response({"score": 8, "reasoning": "Strong match on stack and location."})
    result = parse_tool_response(response)
    assert result == {"score": 8, "reasoning": "Strong match on stack and location."}


def test_parse_tool_response_rejects_out_of_range_score():
    response = _tool_use_response({"score": 15, "reasoning": "Whatever"})
    with pytest.raises(ValueError, match="invalid score"):
        parse_tool_response(response)


def test_parse_tool_response_rejects_zero_score():
    response = _tool_use_response({"score": 0, "reasoning": "Whatever"})
    with pytest.raises(ValueError, match="invalid score"):
        parse_tool_response(response)


def test_parse_tool_response_rejects_missing_reasoning():
    response = _tool_use_response({"score": 5, "reasoning": ""})
    with pytest.raises(ValueError, match="reasoning"):
        parse_tool_response(response)


def test_parse_tool_response_rejects_non_integer_score():
    response = _tool_use_response({"score": "eight", "reasoning": "Whatever"})
    with pytest.raises(ValueError, match="invalid score"):
        parse_tool_response(response)


def test_parse_tool_response_raises_when_no_tool_call_present():
    text_block = SimpleNamespace(type="text", text="I refuse to use the tool.")
    response = SimpleNamespace(content=[text_block])
    with pytest.raises(ValueError, match="no submit_fit_score"):
        parse_tool_response(response)


# --- score_posting (mocked client) ---


class FakeMessages:
    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exception:
            raise self._exception
        return self._response


class FakeClient:
    def __init__(self, response=None, exception=None):
        self.messages = FakeMessages(response, exception)


def test_score_posting_calls_the_tool_with_forced_tool_choice():
    response = _tool_use_response({"score": 7, "reasoning": "Good fit."})
    client = FakeClient(response=response)

    result = score_posting(client, "profile text", "Software Engineer", "Co", "NYC", {})

    assert result == {"score": 7, "reasoning": "Good fit."}
    call = client.messages.calls[0]
    assert call["tools"] == [SCORE_TOOL]
    assert call["tool_choice"] == {"type": "tool", "name": "submit_fit_score"}
    assert "profile text" in call["messages"][0]["content"]


def test_score_posting_wraps_api_errors():
    import anthropic

    fake_request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    api_error = anthropic.APIError("boom", request=fake_request, body=None)
    client = FakeClient(exception=api_error)

    with pytest.raises(RuntimeError, match="Haiku scoring call failed"):
        score_posting(client, "profile", "Title", "Co", "NYC", {})
