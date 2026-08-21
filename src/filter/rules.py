"""Filter stage: cheap, deterministic rules, no LLM calls (architecture doc
section 3). Runs against everything in `postings` (Track A + B; Track C
doesn't produce postings rows, only company_monitor_alerts).

Five rules, checked in this order -- the first one a posting fails is what
gets recorded as `excluded_by`, so a posting failing multiple rules is
still reported under one clear category rather than double-counted:

  1. staleness     -- must be recently posted (cutoff varies by source)
  2. location     -- must have a genuine NYC location entry
  3. employment_type -- must be full-time (or reads as full-time when no
                        structured field exists)
  4. title_keyword -- title must match the SWE/AI-Engineer keyword set
  5. title_seniority -- title must NOT indicate Senior/Staff/Principal/Manager

Every rule here was checked against live data pulled by the discovery
runners before being finalized -- see the comments below for what that
data showed and why each rule looks the way it does.
"""

import re
from datetime import datetime, timezone

# --- staleness -------------------------------------------------------------
#
# No rule here checked posting age at all until a live investigation found
# real evergreen listings sitting unfiltered in the discovery pool -- some
# over 1,000 days old (OpenAI, Palantir), and every one of the first 10
# postings actually tailored turned out to be stale once traced back to
# its true first-posted date, one over 7 years old. Two cutoffs, by
# explicit direction: LinkedIn (a broad market scrape where "worth
# applying to" tracks closely with "just posted") gets a tight 24-hour
# window; ATS-sourced postings (Greenhouse/Ashby/Workday -- a small
# curated company list that won't necessarily post daily) get 14 days.
#
# Source format varies:
#   - Greenhouse: `first_published`, a real ISO timestamp (normalize.py
#     used to capture `updated_at` instead, which can lag the true post
#     date by years on a listing a company periodically re-saves without
#     actually reposting -- fixed there, see its docstring)
#   - Ashby:      `publishedAt`, a real ISO timestamp
#   - LinkedIn:   `postedAt`, a real ISO date
#   - Workday:    `postedOn`, a relative string ("Posted Today" / "Posted
#     N Days Ago") with day-by-day precision only up to 30 days, after
#     which it buckets everything as "Posted 30+ Days Ago". Some Workday
#     boards (e.g. Blackstone) don't return this field at all.
#
# These are two genuinely different kinds of uncertainty, by explicit
# direction, and are NOT treated the same:
#   - "30+ Days Ago" IS confirmed information -- we know the posting is at
#     least 30 days old, which already exceeds both cutoffs here (1 and 14
#     days), so it's excluded outright same as any other confirmed-stale
#     posting.
#   - A posted_at that's missing entirely (or unparseable) is zero
#     information -- it isn't "confirmed old" the way the 30+ bucket is,
#     it's simply unknown, and could be brand new. Excluding it outright
#     would be no different from the data-availability trap already
#     avoided for employment_type (where missing data is common and
#     defaulting to "excluded" would gut the funnel for reasons unrelated
#     to the actual rule). So this does NOT fail the staleness check --
#     it still passes (and flows to every later rule normally) but is
#     flagged `low_confidence_age` so it's visibly different downstream
#     from a posting whose freshness is actually confirmed.

LINKEDIN_STALENESS_CUTOFF_DAYS = 1
ATS_STALENESS_CUTOFF_DAYS = 14

_WORKDAY_RELATIVE_PATTERN = re.compile(r"^posted\s+(today|yesterday|(\d+)(\+)?\s+days?\s+ago)$", re.IGNORECASE)


def _posting_age_days(posted_at: str | None, now: datetime | None = None) -> tuple[int | None, bool]:
    """Returns (age_days, is_known).

    is_known is False only when posted_at is missing or fully unparseable
    -- zero information about age. It's True for every other case,
    including Workday's "N+ Days Ago" bucket: that's an imprecise age but
    still a confirmed lower bound (N), which is genuine information, not
    an unknown.
    """
    if not posted_at:
        return None, False
    now = now or datetime.now(timezone.utc)

    m = _WORKDAY_RELATIVE_PATTERN.match(posted_at.strip())
    if m:
        word = m.group(1).lower()
        if word == "today":
            return 0, True
        if word == "yesterday":
            return 1, True
        return int(m.group(2)), True  # "N Days Ago" or "N+ Days Ago" -- either way N is a known (lower-bound) age

    try:
        parsed = datetime.fromisoformat(posted_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).days, True


def evaluate_staleness(source: str, posted_at: str | None, now: datetime | None = None) -> dict:
    """Returns {"excluded": bool, "low_confidence_age": bool}. See the
    module comment above for why missing/unparseable dates pass (flagged)
    instead of being excluded like a confirmed-stale posting.
    """
    age_days, is_known = _posting_age_days(posted_at, now=now)
    if not is_known:
        return {"excluded": False, "low_confidence_age": True}
    cutoff = LINKEDIN_STALENESS_CUTOFF_DAYS if source == "linkedin" else ATS_STALENESS_CUTOFF_DAYS
    return {"excluded": age_days > cutoff, "low_confidence_age": False}

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
    "deep learning engineer",
    "deep learning research engineer",
    # Bare "research engineer" was added after reviewing 25 excluded Data/
    # Research Engineer postings: HRT- and Jump Trading-style "Research
    # Engineer" / "AI Research Engineer" roles skew quant/ML even without
    # the literal word "AI", and this phrase already covers "AI Research
    # Engineer" and "Machine Learning Research Engineer" too (both contain
    # "research engineer" as a substring). Deliberately NOT adding bare
    # "data engineer" -- that pulled in more data-infra/pipeline noise
    # (Point72's Data Engineer cluster, Stripe's "Data Analyst") than real
    # matches when the samples were reviewed.
    "research engineer",
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


def evaluate_posting(
    title: str,
    location: str | None,
    raw_json: dict,
    source: str,
    posted_at: str | None,
    now: datetime | None = None,
) -> dict:
    """Run all five rules in order; return {"passed": bool, "excluded_by":
    str | None, "low_confidence_age": bool}.

    Stops at the first failing rule -- a posting failing multiple rules is
    reported under whichever one is checked first, not double-counted.
    low_confidence_age is carried through regardless of outcome (it's only
    ever True when the posting's age is unknown, not when it's confirmed
    stale -- see evaluate_staleness), but it's mainly meaningful when the
    posting passes: a real posting with an unverified age, not excluded,
    just flagged. `now` is only for tests -- defaults to the real current
    time.
    """
    staleness = evaluate_staleness(source, posted_at, now=now)
    if staleness["excluded"]:
        return {"passed": False, "excluded_by": "staleness", "low_confidence_age": False}
    low_confidence_age = staleness["low_confidence_age"]
    if not matches_nyc_location(location):
        return {"passed": False, "excluded_by": "location", "low_confidence_age": low_confidence_age}
    if not passes_employment_type(raw_json, title):
        return {"passed": False, "excluded_by": "employment_type", "low_confidence_age": low_confidence_age}
    if not matches_title_keywords(title):
        return {"passed": False, "excluded_by": "title_keyword", "low_confidence_age": low_confidence_age}
    if matches_seniority_exclusion(title):
        return {"passed": False, "excluded_by": "title_seniority", "low_confidence_age": low_confidence_age}
    return {"passed": True, "excluded_by": None, "low_confidence_age": low_confidence_age}
