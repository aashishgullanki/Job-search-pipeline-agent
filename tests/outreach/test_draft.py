"""Outreach message-drafting tests -- entirely mocked, no real Anthropic
API calls. Mirrors tests/score/test_scorer.py's shape.
"""

from types import SimpleNamespace

import pytest

from src.outreach.draft import DRAFT_TOOL, build_outreach_prompt, draft_outreach_message, parse_tool_response

_CONTACT = {
    "name": "Jordan Lee",
    "headline": "Engineering Manager at Acme",
    "profile_url": "https://www.linkedin.com/in/jordan-lee",
    "post_url": "https://www.linkedin.com/posts/activity-1",
    "post_text": "We're hiring software engineers to join our NYC team!",
    "posted_at": "2026-08-10T12:00:00Z",
}

# --- build_outreach_prompt ---


def test_prompt_includes_profile_job_and_contact_fields():
    prompt = build_outreach_prompt("## Education\nSome University", "Acme", "Software Engineer", "https://acme.com/jobs/1", _CONTACT)
    assert "Some University" in prompt
    assert "Acme" in prompt
    assert "Software Engineer" in prompt
    assert "https://acme.com/jobs/1" in prompt
    assert "Jordan Lee" in prompt
    assert "Engineering Manager at Acme" in prompt
    assert "https://www.linkedin.com/posts/activity-1" in prompt
    assert "We're hiring software engineers" in prompt


def test_prompt_handles_missing_contact_fields_gracefully():
    sparse_contact = {"profile_url": "https://www.linkedin.com/in/x"}
    prompt = build_outreach_prompt("profile", "Acme", "SWE", "https://acme.com/jobs/1", sparse_contact)
    assert "Unknown" in prompt  # missing name
    assert "n/a" in prompt  # missing headline/post_url


def test_prompt_truncates_very_long_post_text():
    contact = {**_CONTACT, "post_text": "x" * 2000}
    prompt = build_outreach_prompt("profile", "Acme", "SWE", "https://acme.com/jobs/1", contact)
    assert prompt.count("x") <= 500


# --- parse_tool_response ---


def _tool_use_response(input_data: dict):
    block = SimpleNamespace(type="tool_use", name="submit_outreach_message", input=input_data)
    return SimpleNamespace(content=[block])


def test_parse_tool_response_extracts_message():
    response = _tool_use_response({"message": "Hi Jordan, I saw your post..."})
    assert parse_tool_response(response) == "Hi Jordan, I saw your post..."


def test_parse_tool_response_strips_whitespace():
    response = _tool_use_response({"message": "  Hi there.  "})
    assert parse_tool_response(response) == "Hi there."


def test_parse_tool_response_rejects_empty_message():
    response = _tool_use_response({"message": ""})
    with pytest.raises(ValueError, match="missing/invalid message"):
        parse_tool_response(response)


def test_parse_tool_response_rejects_missing_message_key():
    response = _tool_use_response({})
    with pytest.raises(ValueError, match="missing/invalid message"):
        parse_tool_response(response)


def test_parse_tool_response_raises_when_no_tool_call_present():
    text_block = SimpleNamespace(type="text", text="I refuse to use the tool.")
    response = SimpleNamespace(content=[text_block])
    with pytest.raises(ValueError, match="no submit_outreach_message"):
        parse_tool_response(response)


# --- draft_outreach_message (mocked client) ---


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


def test_draft_outreach_message_calls_the_tool_with_forced_tool_choice():
    response = _tool_use_response({"message": "Hi Jordan, ..."})
    client = FakeClient(response=response)

    result = draft_outreach_message(client, "profile text", "Acme", "SWE", "https://acme.com/jobs/1", _CONTACT)

    assert result == "Hi Jordan, ..."
    call = client.messages.calls[0]
    assert call["tools"] == [DRAFT_TOOL]
    assert call["tool_choice"] == {"type": "tool", "name": "submit_outreach_message"}
    assert call["model"] == "claude-haiku-4-5-20251001"  # cost-control: Haiku, not Sonnet
    assert "profile text" in call["messages"][0]["content"]


def test_draft_outreach_message_wraps_api_errors():
    import anthropic

    fake_request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    api_error = anthropic.APIError("boom", request=fake_request, body=None)
    client = FakeClient(exception=api_error)

    with pytest.raises(RuntimeError, match="Haiku outreach draft call failed"):
        draft_outreach_message(client, "profile", "Acme", "SWE", "https://acme.com/jobs/1", _CONTACT)
