from src.filter.rules import (
    evaluate_posting,
    matches_nyc_location,
    matches_seniority_exclusion,
    matches_title_keywords,
    passes_employment_type,
)

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


def test_seniority_exclusion_does_not_match_entry_level_title():
    assert matches_seniority_exclusion("Software Engineer") is False
    assert matches_seniority_exclusion("Software Engineer II") is False


# --- evaluate_posting (combined, rule ordering) ---


def _job(title="Software Engineer", location="New York, NY", raw=None):
    return dict(title=title, location=location, raw_json=raw or {})


def test_evaluate_posting_passes_a_clean_match():
    j = _job()
    result = evaluate_posting(j["title"], j["location"], j["raw_json"])
    assert result == {"passed": True, "excluded_by": None}


def test_evaluate_posting_excludes_by_location_first():
    j = _job(location="Chicago, IL")
    result = evaluate_posting(j["title"], j["location"], j["raw_json"])
    assert result["excluded_by"] == "location"


def test_evaluate_posting_excludes_by_employment_type():
    j = _job(raw={"employmentType": "Intern"})
    result = evaluate_posting(j["title"], j["location"], j["raw_json"])
    assert result["excluded_by"] == "employment_type"


def test_evaluate_posting_excludes_by_title_keyword():
    j = _job(title="Product Manager")
    result = evaluate_posting(j["title"], j["location"], j["raw_json"])
    assert result["excluded_by"] == "title_keyword"


def test_evaluate_posting_excludes_by_title_seniority():
    j = _job(title="Senior Software Engineer")
    result = evaluate_posting(j["title"], j["location"], j["raw_json"])
    assert result["excluded_by"] == "title_seniority"


def test_evaluate_posting_reports_first_failing_rule_when_several_fail():
    # Fails location, employment type, AND title keyword all at once --
    # must report "location" (checked first), not any of the others.
    j = _job(title="Marketing Intern", location="Chicago, IL", raw={"employmentType": "Intern"})
    result = evaluate_posting(j["title"], j["location"], j["raw_json"])
    assert result["excluded_by"] == "location"


def test_evaluate_posting_title_keyword_checked_before_seniority():
    # "Product Manager" contains "manager" (a seniority keyword) but has no
    # SWE/AI keyword match at all -- must be bucketed as title_keyword, not
    # mislabeled as a seniority exclusion.
    j = _job(title="Product Manager")
    result = evaluate_posting(j["title"], j["location"], j["raw_json"])
    assert result["excluded_by"] == "title_keyword"
