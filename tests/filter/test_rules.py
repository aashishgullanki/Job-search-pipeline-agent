from datetime import datetime, timedelta, timezone

from src.filter.rules import (
    ATS_STALENESS_CUTOFF_DAYS,
    LINKEDIN_STALENESS_CUTOFF_DAYS,
    evaluate_posting,
    evaluate_staleness,
    matches_nyc_location,
    matches_seniority_exclusion,
    matches_title_keywords,
    passes_employment_type,
)

_NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _iso_days_ago(days: int) -> str:
    return (_NOW - timedelta(days=days)).isoformat()

# --- matches_nyc_location ---
# Cases mirror the real formats found live: LinkedIn's "New York, NY",
# Greenhouse's "New York, New York, United States", and Ashby/Workday's
# bare "New York" -- plus the multi-location and false-positive cases that
# shaped the split-on-";"/first-comma-segment algorithm.


def test_matches_linkedin_format():
    assert matches_nyc_location("New York, NY") is True


def test_matches_greenhouse_format_with_spelled_out_state():
    assert matches_nyc_location("New York, New York, United States") is True


def test_matches_bare_city_format():
    assert matches_nyc_location("New York") is True


def test_matches_case_insensitively():
    assert matches_nyc_location("NEW YORK, ny") is True


def test_matches_nyc_entry_within_a_multi_location_list():
    assert (
        matches_nyc_location("Chicago, Illinois, United States; New York, New York, United States")
        is True
    )


def test_matches_nyc_entry_alongside_a_remote_entry():
    # 29 real postings looked like this -- NYC is a genuine office choice,
    # "Remote" is a separate additional option, not evidence the NYC entry
    # is fake.
    assert matches_nyc_location("New York, NY; San Francisco, CA; United States - Remote") is True


def test_rejects_non_nyc_single_location():
    assert matches_nyc_location("Chicago, Illinois, United States") is False


def test_rejects_multi_location_list_without_nyc():
    assert matches_nyc_location("Chicago, Illinois, United States; Boston, Massachusetts") is False


def test_rejects_state_only_reference_not_the_city():
    # "New York" here is the STATE (second segment), not the city -- must
    # not match on a bare substring search.
    assert matches_nyc_location("Albany, New York") is False


def test_rejects_pure_remote_location():
    assert matches_nyc_location("United States - Remote") is False
    assert matches_nyc_location("Remote") is False


def test_rejects_locations_count_placeholder():
    # Known Workday data-quality gap: a multi-location posting sometimes
    # only exposes a count ("2 Locations"), not the actual cities. Can't be
    # resolved from this field; correctly excluded rather than guessed at.
    assert matches_nyc_location("2 Locations") is False


def test_rejects_missing_location():
    assert matches_nyc_location(None) is False
    assert matches_nyc_location("") is False


def test_rejects_company_name_with_no_comma():
    assert matches_nyc_location("Radix Trading Amsterdam") is False


# --- passes_employment_type ---


def test_structured_fulltime_ashby_style_passes():
    assert passes_employment_type({"employmentType": "FullTime"}, "Software Engineer") is True


def test_structured_fulltime_linkedin_style_passes():
    assert passes_employment_type({"employmentType": "Full-time"}, "Software Engineer") is True


def test_structured_intern_fails():
    assert passes_employment_type({"employmentType": "Intern"}, "Software Engineer") is False


def test_structured_contract_fails():
    assert passes_employment_type({"employmentType": "Contract"}, "Software Engineer") is False


def test_structured_temporary_fails():
    assert passes_employment_type({"employmentType": "Temporary"}, "Software Engineer") is False


def test_missing_field_falls_back_to_title_heuristic_and_defaults_to_pass():
    # Greenhouse/Workday: 0/5833 and 0/23 postings carry a structured field
    # at all -- must not default to excluding these, only unusual titles.
    assert passes_employment_type({}, "Software Engineer") is True


def test_missing_field_title_heuristic_catches_intern():
    assert passes_employment_type({}, "Software Engineer Intern") is False


def test_missing_field_title_heuristic_catches_contract():
    assert passes_employment_type({}, "Software Engineer (Contract)") is False


# --- matches_title_keywords / matches_seniority_exclusion ---


def test_title_matches_software_engineer():
    assert matches_title_keywords("Software Engineer") is True


def test_title_matches_swe_abbreviation():
    assert matches_title_keywords("SWE - Backend") is True


def test_title_matches_hyphenated_full_stack():
    assert matches_title_keywords("Full-Stack Engineer") is True


def test_title_matches_ai_engineer():
    assert matches_title_keywords("AI Engineer") is True


def test_title_matches_machine_learning_engineer():
    assert matches_title_keywords("Machine Learning Engineer") is True


def test_title_rejects_unrelated_role():
    assert matches_title_keywords("Product Manager") is False
    assert matches_title_keywords("Recruiter") is False


def test_swe_keyword_does_not_false_positive_inside_other_words():
    # "swe" is a substring of "answered" -- word-boundary matching must
    # reject this, not just do a plain substring search.
    assert matches_title_keywords("Answered support tickets") is False


def test_title_matches_bare_research_engineer():
    # Added after reviewing real excluded postings: HRT/Jump Trading-style
    # "Research Engineer" roles skew quant/ML even without "AI" literally
    # in the title.
    assert matches_title_keywords("Research Engineer") is True


def test_title_matches_ai_research_engineer_via_bare_research_engineer():
    # No separate "ai research engineer" keyword needed -- "research
    # engineer" alone matches as a substring of this title too.
    assert matches_title_keywords("AI Research Engineer, Pre-Training") is True


def test_title_matches_deep_learning_engineer():
    assert matches_title_keywords("Deep Learning Engineer") is True
    assert matches_title_keywords("Campus AI Research Engineer – Deep Learning (Full-Time)") is True


def test_title_still_rejects_bare_data_engineer():
    # Explicitly not added -- reviewed samples showed this pulls in more
    # data-infra/pipeline noise (Point72's Data Engineer cluster, Stripe's
    # "Data Analyst") than real ML/AI matches.
    assert matches_title_keywords("Data Engineer") is False
    assert matches_title_keywords("Data Engineer, Knowledge Graph") is False
    assert matches_title_keywords("Low-Latency Market Data Engineer") is False


def test_title_still_rejects_data_analyst():
    assert matches_title_keywords("Data Analyst, Financial Data Engineering") is False


def test_seniority_exclusion_matches_senior():
    assert matches_seniority_exclusion("Senior Software Engineer") is True


def test_seniority_exclusion_matches_sr_abbreviation_with_period():
    assert matches_seniority_exclusion("Sr. Software Engineer") is True


def test_seniority_exclusion_matches_staff():
    assert matches_seniority_exclusion("Staff AI Engineer") is True


def test_seniority_exclusion_matches_principal():
    assert matches_seniority_exclusion("Principal Machine Learning Engineer") is True


def test_seniority_exclusion_matches_manager():
    assert matches_seniority_exclusion("Engineering Manager") is True


def test_seniority_exclusion_matches_lead():
    # Added after reviewing real results: "Lead Software Engineer" reads as
    # senior-tier in practice (mostly seen at Capital One), added to the
    # exclusion list alongside Senior/Staff/Principal/Manager.
    assert matches_seniority_exclusion("Lead Software Engineer") is True
    assert matches_seniority_exclusion("Lead Machine Learning Engineer") is True


def test_seniority_exclusion_does_not_match_entry_level_title():
    assert matches_seniority_exclusion("Software Engineer") is False
    assert matches_seniority_exclusion("Software Engineer II") is False


# --- evaluate_staleness ---
# Cutoffs are per explicit direction: LinkedIn 24h, ATS sources 14 days.
# Source format varies: Greenhouse/Ashby give real ISO timestamps (via
# first_published/publishedAt), LinkedIn gives a real ISO date (postedAt),
# Workday gives a relative string with day precision only up to 30 days
# ("Posted N Days Ago"), after which it's bucketed as "Posted 30+ Days
# Ago" -- a lower bound, not a real age, checked live against real data.
#
# Two genuinely different kinds of uncertainty, per explicit correction:
# the 30+ bucket IS confirmed information (at least 30 days, past both
# cutoffs) and gets excluded outright; a missing/unparseable posted_at is
# zero information and must NOT be excluded the same way -- it passes,
# flagged low_confidence_age, same principle already used for
# employment_type (missing data isn't the same as a confirmed violation).


def test_linkedin_within_24h_passes():
    result = evaluate_staleness("linkedin", _iso_days_ago(0), now=_NOW)
    assert result == {"excluded": False, "low_confidence_age": False}


def test_linkedin_older_than_24h_excluded():
    result = evaluate_staleness("linkedin", _iso_days_ago(2), now=_NOW)
    assert result == {"excluded": True, "low_confidence_age": False}


def test_linkedin_exactly_at_cutoff_passes():
    result = evaluate_staleness("linkedin", _iso_days_ago(LINKEDIN_STALENESS_CUTOFF_DAYS), now=_NOW)
    assert result["excluded"] is False


def test_ats_source_within_14_days_passes():
    result = evaluate_staleness("ats:ExampleCo", _iso_days_ago(10), now=_NOW)
    assert result == {"excluded": False, "low_confidence_age": False}


def test_ats_source_older_than_14_days_excluded():
    result = evaluate_staleness("ats:ExampleCo", _iso_days_ago(45), now=_NOW)
    assert result == {"excluded": True, "low_confidence_age": False}


def test_ats_source_exactly_at_cutoff_passes():
    result = evaluate_staleness("ats:ExampleCo", _iso_days_ago(ATS_STALENESS_CUTOFF_DAYS), now=_NOW)
    assert result["excluded"] is False


def test_workday_posted_today_passes():
    assert evaluate_staleness("ats:ExampleCo", "Posted Today", now=_NOW) == {
        "excluded": False,
        "low_confidence_age": False,
    }


def test_workday_posted_yesterday_passes():
    assert evaluate_staleness("ats:ExampleCo", "Posted Yesterday", now=_NOW) == {
        "excluded": False,
        "low_confidence_age": False,
    }


def test_workday_posted_n_days_ago_within_cutoff_passes():
    result = evaluate_staleness("ats:ExampleCo", "Posted 10 Days Ago", now=_NOW)
    assert result == {"excluded": False, "low_confidence_age": False}


def test_workday_posted_n_days_ago_past_cutoff_excluded():
    result = evaluate_staleness("ats:ExampleCo", "Posted 20 Days Ago", now=_NOW)
    assert result == {"excluded": True, "low_confidence_age": False}


def test_workday_30_plus_bucket_is_confirmed_stale_not_low_confidence():
    # "30+ Days Ago" is a known lower bound (>=30, past both cutoffs) --
    # confirmed information, excluded outright, NOT flagged low_confidence.
    result = evaluate_staleness("ats:ExampleCo", "Posted 30+ Days Ago", now=_NOW)
    assert result == {"excluded": True, "low_confidence_age": False}


def test_missing_posted_at_passes_but_flagged_low_confidence():
    # Zero information about age -- must NOT be treated like a confirmed-
    # stale posting (the 30+ bucket). Passes staleness, flagged instead.
    assert evaluate_staleness("ats:ExampleCo", None, now=_NOW) == {
        "excluded": False,
        "low_confidence_age": True,
    }
    assert evaluate_staleness("linkedin", None, now=_NOW) == {
        "excluded": False,
        "low_confidence_age": True,
    }


def test_unparseable_posted_at_passes_but_flagged_low_confidence():
    result = evaluate_staleness("ats:ExampleCo", "not a real date", now=_NOW)
    assert result == {"excluded": False, "low_confidence_age": True}


# --- evaluate_posting (combined, rule ordering) ---


_UNSET = object()


def _job(title="Software Engineer", location="New York, NY", raw=None, source="ats:ExampleCo", posted_at=_UNSET):
    # Default posted_at is "right now" so every test not specifically about
    # staleness passes that rule regardless of when the suite actually runs.
    # A sentinel (not None) distinguishes "caller didn't specify" from a
    # caller deliberately testing a missing posted_at.
    if posted_at is _UNSET:
        posted_at = datetime.now(timezone.utc).isoformat()
    return dict(title=title, location=location, raw_json=raw or {}, source=source, posted_at=posted_at)


def _evaluate(j):
    return evaluate_posting(j["title"], j["location"], j["raw_json"], j["source"], j["posted_at"])


def test_evaluate_posting_passes_a_clean_match():
    j = _job()
    result = _evaluate(j)
    assert result == {"passed": True, "excluded_by": None, "low_confidence_age": False}


def test_evaluate_posting_excludes_by_staleness_first():
    # Confirmed-stale (30+ bucket) AND wrong location -- must report
    # "staleness" (checked first), not "location".
    j = _job(location="Chicago, IL", posted_at="Posted 30+ Days Ago")
    result = _evaluate(j)
    assert result["excluded_by"] == "staleness"


def test_evaluate_posting_passes_with_unverified_age_flagged_not_excluded():
    # Missing posted_at is NOT the same as confirmed-stale -- the posting
    # still passes every other rule, just flagged.
    j = _job(posted_at=None)
    result = _evaluate(j)
    assert result == {"passed": True, "excluded_by": None, "low_confidence_age": True}


def test_evaluate_posting_low_confidence_age_carried_through_a_later_exclusion():
    # Unverified age doesn't get excluded outright, but the flag still
    # rides along even when a later rule (location, here) fails it.
    j = _job(location="Chicago, IL", posted_at=None)
    result = _evaluate(j)
    assert result == {"passed": False, "excluded_by": "location", "low_confidence_age": True}


def test_evaluate_posting_excludes_by_location():
    j = _job(location="Chicago, IL")
    result = _evaluate(j)
    assert result["excluded_by"] == "location"


def test_evaluate_posting_excludes_by_employment_type():
    j = _job(raw={"employmentType": "Intern"})
    result = _evaluate(j)
    assert result["excluded_by"] == "employment_type"


def test_evaluate_posting_excludes_by_title_keyword():
    j = _job(title="Product Manager")
    result = _evaluate(j)
    assert result["excluded_by"] == "title_keyword"


def test_evaluate_posting_excludes_by_title_seniority():
    j = _job(title="Senior Software Engineer")
    result = _evaluate(j)
    assert result["excluded_by"] == "title_seniority"


def test_evaluate_posting_excludes_lead_titles():
    j = _job(title="Lead Software Engineer, Full Stack")
    result = _evaluate(j)
    assert result["excluded_by"] == "title_seniority"


def test_evaluate_posting_reports_first_failing_rule_when_several_fail():
    # Fails location, employment type, AND title keyword all at once --
    # must report "location" (checked first among these, after staleness),
    # not any of the others.
    j = _job(title="Marketing Intern", location="Chicago, IL", raw={"employmentType": "Intern"})
    result = _evaluate(j)
    assert result["excluded_by"] == "location"


def test_evaluate_posting_title_keyword_checked_before_seniority():
    # "Product Manager" contains "manager" (a seniority keyword) but has no
    # SWE/AI keyword match at all -- must be bucketed as title_keyword, not
    # mislabeled as a seniority exclusion.
    j = _job(title="Product Manager")
    result = _evaluate(j)
    assert result["excluded_by"] == "title_keyword"
