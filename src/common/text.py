"""Shared helper for pulling a plain-text job description out of a
posting's raw_json -- used by both the Score stage (fit judgment) and the
Tailor stage (bullet rewriting), since neither cares which ATS a posting
came from, just whether a description exists.
"""

import html
import re


def extract_description(raw_json: dict) -> str | None:
    """Descriptions aren't uniformly available: Greenhouse/Ashby/LinkedIn
    carry one, Workday's cxs/jobs list endpoint doesn't expose one at all.
    Returns plain text, or None if nothing usable was found.
    """
    desc = raw_json.get("content") or raw_json.get("descriptionText") or raw_json.get("descriptionHtml")
    if not desc:
        return None
    plain = html.unescape(re.sub(r"<[^<]+?>", " ", desc))
    return re.sub(r"\s+", " ", plain).strip()
