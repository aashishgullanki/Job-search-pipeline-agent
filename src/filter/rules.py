"""Filter stage: cheap, deterministic rules, no LLM calls (architecture doc
section 3). Runs against everything in `postings` (Track A + B; Track C
doesn't produce postings rows, only company_monitor_alerts).

Four rules, checked in this order -- the first one a posting fails is what
gets recorded as `excluded_by`, so a posting failing multiple rules is
still reported under one clear category rather than double-counted:

  1. location     -- must have a genuine NYC location entry
  2. employment_type -- must be full-time (or reads as full-time when no
                        structured field exists)
  3. title_keyword -- title must match the SWE/AI-Engineer keyword set
  4. title_seniority -- title must NOT indicate Senior/Staff/Principal/Manager

Every rule here was checked against live data pulled by the discovery
runners before being finalized -- see the comments below for what that
data showed and why each rule looks the way it does.
"""

import re

# --- location -----------------------------------------------------------
#
# Location text format varies by source, checked against ~6,900 real
# postings before picking a matching strategy:
#   - LinkedIn:          "New York, NY"
#   - Greenhouse:         "New York, New York, United States"  (spelled-out
#                          state, not the "NY" abbreviation)
#   - Ashby/Workday:      "New York"  (bare city, no state at all -- this
#                          was actually the MOST common NYC format, not an
#                          edge case)
#   - Multi-location:     semicolon-joined, e.g. "Chicago, Illinois, United
#                          States; New York, New York, United States"
#
# A strict "contains New York, NY" substring match only caught 582 of the
# ~1,357 genuinely-NYC postings in that check -- most were missed purely
# on formatting, not because they weren't NYC. So: split on ";" for
# multi-location strings, then treat the FIRST comma-segment of each entry
# as the city. If that segment is exactly "new york", it's a real NYC
# office. This also naturally rejects state-reference false positives like
# "Albany, New York" (first segment there is "Albany", not "New York") and
# purely-remote entries like "United States - Remote" (first segment
# doesn't match at all).
#
# This also makes a separate "remote-only" exclusion unnecessary: 29 real
# postings pair "New York, NY" with a distinct "United States - Remote"
# entry (flexible roles with an NYC office as one option) -- those
# genuinely have an NYC option and should pass, and a naive "exclude if
# the string contains 'remote' anywhere" rule would incorrectly drop all
# of them. A location string that's remote-only (no NYC entry at all)
# already fails this check on its own, with no extra rule needed.


def matches_nyc_location(location: str | None) -> bool:
    if not location:
        return False
    for entry in location.split(";"):
        city = entry.split(",")[0].strip().lower()
        if city == "new york":
            return True
    return False


# --- employment type ------------------------------------------------------
#
# Checked live: only Ashby postings carry a structured `employmentType`
# field (982/982 do, values seen: FullTime/Intern/Temporary/Contract) and
# LinkedIn (50/50, values: "Full-time"). Greenhouse and Workday's list
# endpoints expose no per-posting employment-type field at all (0/5833 and
# 0/23 respectively) -- it isn't hidden under a different key, it's just
# not part of what those APIs return for a job list. Defaulting those to
# "excluded" would silently gut the majority of Track A, which isn't what
# "employment type = full-time" is meant to accomplish (it's meant to
# filter out interns/contractors, not punish an API for omitting a field).
# So: use the structured field when present, and fall back to a title
# keyword heuristic (catches "... Intern", "... Contract", etc.) when it's
# absent, defaulting to "assume full-time" only when neither signal fires.

_NON_FULLTIME_TITLE_KEYWORDS = [
    "intern",
    "internship",
    "part time",
    "contract",
    "contractor",
    "temporary",
    "co op",
]


def _get_structured_employment_type(raw_json: dict) -> str | None:
    return raw_json.get("employmentType") or raw_json.get("employment_type") or raw_json.get("timeType")


def _normalize_employment_type_value(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def passes_employment_type(raw_json: dict, title: str) -> bool:
    employment_type = _get_structured_employment_type(raw_json)
    if employment_type:
        return _normalize_employment_type_value(employment_type) == "fulltime"
    return not _contains_any(_normalize_title(title), _NON_FULLTIME_TITLE_KEYWORDS)


# --- title -----------------------------------------------------------------

TITLE_KEYWORDS = [
    "software engineer",
    "swe",
    "software developer",
    "backend engineer",
    "back end engineer",
    "full stack engineer",
    "fullstack engineer",
    "frontend engineer",
    "front end engineer",
    "ai engineer",
    "artificial intelligence engineer",
    "machine learning engineer",
    "ml engineer",
]

# Senior/Sr./Staff/Principal/Manager were the four levels originally named;
# Lead was added after reviewing real results -- ~18% of what passed the
# filter was "Lead Software Engineer"-type titles (mostly Capital One),
# which reads as senior-tier in practice and is out of scope at ~22 months
# of experience. Director/VP/Head of remain unrequested -- add them here
# too if they should also be excluded.
SENIORITY_EXCLUDE_KEYWORDS = ["senior", "sr", "staff", "principal", "manager", "lead"]


def _normalize_title(title: str) -> str:
    # Strip periods ("Sr." -> "sr") and turn hyphens into spaces
    # ("full-stack" -> "full stack") so keyword lists don't need every
    # punctuation variant spelled out separately.
    t = (title or "").lower().replace(".", "").replace("-", " ")
    return re.sub(r"\s+", " ", t).strip()


def _contains_any(normalized_text: str, keywords: list[str]) -> bool:
    # Word-boundary matching, not bare substring -- "swe" as a plain
    # substring matches inside "answered", and this list has other short
    # tokens ("sr") with the same risk.
    return any(re.search(r"\b" + re.escape(kw) + r"\b", normalized_text) for kw in keywords)


def matches_title_keywords(title: str) -> bool:
    return _contains_any(_normalize_title(title), TITLE_KEYWORDS)


def matches_seniority_exclusion(title: str) -> bool:
    return _contains_any(_normalize_title(title), SENIORITY_EXCLUDE_KEYWORDS)


# --- combined ----------------------------------------------------------


def evaluate_posting(title: str, location: str | None, raw_json: dict) -> dict:
    """Run all four rules in order; return {"passed": bool, "excluded_by": str | None}.

    Stops at the first failing rule -- a posting failing multiple rules is
    reported under whichever one is checked first, not double-counted.
    """
    if not matches_nyc_location(location):
        return {"passed": False, "excluded_by": "location"}
    if not passes_employment_type(raw_json, title):
        return {"passed": False, "excluded_by": "employment_type"}
    if not matches_title_keywords(title):
        return {"passed": False, "excluded_by": "title_keyword"}
    if matches_seniority_exclusion(title):
        return {"passed": False, "excluded_by": "title_seniority"}
    return {"passed": True, "excluded_by": None}
