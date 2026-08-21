"""Outreach Draft stage, message-drafting half (architecture doc section
6). Model: Claude Haiku 4.5 -- deliberately switched from Sonnet (used by
the Tailor stage) to control cost, since a short outreach note doesn't
need Sonnet-level writing quality the way a full resume tailoring pass
does. Same forced-tool-use pattern as the Score stage's scorer.py:
structured output via a required tool call, not free text + parsing.
"""

import anthropic

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 400

DRAFT_TOOL = {
    "name": "submit_outreach_message",
    "description": "Submit the drafted outreach message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The short outreach message, ready to send as a LinkedIn connection note or DM.",
            },
        },
        "required": ["message"],
    },
}

SYSTEM_PROMPT = """You write short, genuine LinkedIn outreach messages from a job candidate to \
a specific person at a company who recently posted about hiring. The message should:
- Open by referencing the specific hiring post naturally, not a generic "I saw your post about..."
- Connect the candidate's actual background to the role in one sentence -- specific, not a resume dump
- Ask for a short chat, or to be considered for the role
- Stay under 100 words, professional but warm, no corporate boilerplate ("synergy," "circle back," etc.)
- Never fabricate a shared connection, mutual acquaintance, or any detail not given below
Call submit_outreach_message with the finished message."""


def build_outreach_prompt(profile: str, company: str, title: str, job_url: str, contact: dict) -> str:
    post_snippet = (contact.get("post_text") or "")[:500]
    return f"""## Candidate profile

{profile}

## Job posting

Company: {company}
Title: {title}
Posting: {job_url}

## Contact found via a recent LinkedIn hiring post

Name: {contact.get("name") or "Unknown"}
Headline: {contact.get("headline") or "n/a"}
Their post ({contact.get("post_url") or "n/a"}): "{post_snippet}"

Draft a short outreach message from the candidate to this contact."""


def parse_tool_response(response) -> str:
    """Extract the message string from a Messages API response that used
    the submit_outreach_message tool. Raises ValueError on anything
    malformed so the caller treats it as a per-posting failure, not
    silently-stored garbage.
    """
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_outreach_message":
            message = block.input.get("message")
            if not message or not isinstance(message, str):
                raise ValueError(f"missing/invalid message in tool response: {message!r}")
            return message.strip()
    raise ValueError("no submit_outreach_message tool call found in response")


def draft_outreach_message(
    client: anthropic.Anthropic, profile: str, company: str, title: str, job_url: str, contact: dict
) -> str:
    prompt = build_outreach_prompt(profile, company, title, job_url, contact)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[DRAFT_TOOL],
            tool_choice={"type": "tool", "name": "submit_outreach_message"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Haiku outreach draft call failed: {e}") from e

    return parse_tool_response(response)
