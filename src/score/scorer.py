"""Score stage: LLM-as-judge fit scoring against the candidate profile
(architecture doc section 4). Model: Claude Haiku 4.5 -- cheap and fast
enough to run against every filter-passed posting.

Agentic feature per the doc: this replaces brittle keyword exclusion with
judgment. The Filter stage already caught the hard, deterministic
signals (location/employment-type/title); this stage is for the softer
call a keyword list can't make -- e.g. a title that superficially matches
"Software Engineer" but the actual role (per its description) is really a
sales-engineering or IT-support position, or a role that's a stronger or
weaker match than its title alone suggests given the candidate's specific
background (fintech/tariff systems experience, AI/ML project work, current
MS in AI). Score + reasoning are stored as-is, without a second layer of
keyword rules trying to double-check the LLM's judgment -- that would just
reintroduce the brittleness this stage exists to avoid.
"""

import html
import json
import re

import anthropic

from src.score.profile import build_profile_summary

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 512

SCORE_TOOL = {
    "name": "submit_fit_score",
    "description": "Submit the fit score and reasoning for this job posting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Fit score from 1 (poor fit) to 10 (excellent fit)",
            },
            "reasoning": {
                "type": "string",
                "description": "2-3 sentence explanation for the score",
            },
        },
        "required": ["score", "reasoning"],
    },
}

SYSTEM_PROMPT = """You are screening job postings for fit against a specific candidate's \
background. Judge fit holistically -- a title that superficially matches "Software \
Engineer" doesn't guarantee a good score if the actual responsibilities described are a \
poor match (e.g. a sales-engineering or pure IT-support role dressed up with an \
engineering title), and a role can score well even if its title alone looks generic, if \
the description shows real alignment with the candidate's experience and interests. \
Weigh seniority expectations implied by the description (not just the title) against the \
candidate's actual years of experience. Call the submit_fit_score tool with your score \
(1-10) and a 2-3 sentence reasoning."""


def _extract_description(raw_json: dict) -> str | None:
    """Descriptions aren't uniformly available: Greenhouse/Ashby/LinkedIn
    carry one, Workday's cxs/jobs list endpoint doesn't expose one at all.
    Returns plain text, or None if nothing usable was found.
    """
    desc = raw_json.get("content") or raw_json.get("descriptionText") or raw_json.get("descriptionHtml")
    if not desc:
        return None
    plain = html.unescape(re.sub(r"<[^<]+?>", " ", desc))
    return re.sub(r"\s+", " ", plain).strip()


def build_scoring_prompt(profile: str, title: str, company: str, location: str, raw_json: dict) -> str:
    description = _extract_description(raw_json)
    description_block = description[:3000] if description else "(no description available for this posting)"
    return f"""## Candidate profile

{profile}

## Job posting

Company: {company}
Title: {title}
Location: {location}

Description:
{description_block}"""


def parse_tool_response(response) -> dict:
    """Extract {"score": int, "reasoning": str} from a Messages API response
    that used the submit_fit_score tool. Raises ValueError on anything
    malformed (no tool call, missing fields, out-of-range score) so the
    caller can treat it as a per-posting failure rather than store garbage.
    """
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_fit_score":
            data = block.input
            score = data.get("score")
            reasoning = data.get("reasoning")
            if not isinstance(score, int) or not (1 <= score <= 10):
                raise ValueError(f"invalid score in tool response: {score!r}")
            if not reasoning or not isinstance(reasoning, str):
                raise ValueError(f"missing/invalid reasoning in tool response: {reasoning!r}")
            return {"score": score, "reasoning": reasoning.strip()}
    raise ValueError("no submit_fit_score tool call found in response")


def score_posting(
    client: anthropic.Anthropic, profile: str, title: str, company: str, location: str, raw_json: dict
) -> dict:
    prompt = build_scoring_prompt(profile, title, company, location, raw_json)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[SCORE_TOOL],
            tool_choice={"type": "tool", "name": "submit_fit_score"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Haiku scoring call failed: {e}") from e

    return parse_tool_response(response)
