import json
from pathlib import Path

from src.discovery.normalize import (
    normalize_ashby_job,
    normalize_greenhouse_job,
    normalize_linkedin_job,
    normalize_workday_job,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_normalize_greenhouse_job():
    data = json.loads((FIXTURES / "greenhouse_sample.json").read_text())
    job = data["jobs"][0]

    result = normalize_greenhouse_job("ExampleCo", job)

    assert result["source"] == "ats:ExampleCo"
    assert result["company"] == "ExampleCo"
    assert result["title"] == "Software Engineer, Backend"
    assert result["url"] == "https://job-boards.greenhouse.io/examplecowork/jobs/1000001"
    assert result["location"] == "New York, NY"
    assert result["posted_at"] == "2026-07-15T09:00:00-04:00"  # first_published, not updated_at
    assert len(result["url_hash"]) == 64  # sha256 hex digest
    assert json.loads(result["raw_json"]) == job


def test_normalize_greenhouse_job_falls_back_to_updated_at_if_first_published_missing():
    job = {
        "absolute_url": "https://job-boards.greenhouse.io/examplecowork/jobs/1000003",
        "id": 1000003,
        "title": "Software Engineer, Frontend",
        "updated_at": "2026-08-02T09:30:00-04:00",
        "location": {"name": "New York, NY"},
    }

    result = normalize_greenhouse_job("ExampleCo", job)

    assert result["posted_at"] == "2026-08-02T09:30:00-04:00"


def test_normalize_greenhouse_job_missing_location():
    job = {"absolute_url": "https://job-boards.greenhouse.io/x/jobs/1", "title": "Foo"}
    result = normalize_greenhouse_job("X", job)
    assert result["location"] is None


def test_normalize_ashby_job():
    data = json.loads((FIXTURES / "ashby_sample.json").read_text())
    job = data["jobs"][0]

    result = normalize_ashby_job("ExampleCo", job)

    assert result["source"] == "ats:ExampleCo"
    assert result["title"] == "Software Engineer, Infra"
    assert result["url"] == "https://jobs.ashbyhq.com/examplecowork/aaaa-1111"
    assert result["location"] == "New York, New York"
    assert result["posted_at"] == "2026-08-01T00:00:00.000+00:00"
    assert len(result["url_hash"]) == 64


def test_normalize_ashby_job_falls_back_to_apply_url():
    job = {"title": "Foo", "applyUrl": "https://jobs.ashbyhq.com/x/y/application"}
    result = normalize_ashby_job("X", job)
    assert result["url"] == "https://jobs.ashbyhq.com/x/y/application"


def test_same_url_produces_same_hash():
    job = {"absolute_url": "https://job-boards.greenhouse.io/x/jobs/1", "title": "Foo"}
    a = normalize_greenhouse_job("X", job)
    b = normalize_greenhouse_job("X", job)
    assert a["url_hash"] == b["url_hash"]


def test_normalize_workday_job():
    data = json.loads((FIXTURES / "workday_sample.json").read_text())
    job = data["jobPostings"][0]
    base_url = "https://example.wd5.myworkdayjobs.com/External"

    result = normalize_workday_job("ExampleCo", job, base_url)

    assert result["source"] == "ats:ExampleCo"
    assert result["title"] == "Software Engineer, Backend"
    assert (
        result["url"]
        == "https://example.wd5.myworkdayjobs.com/External/job/New-York/Software-Engineer--Backend_R100001"
    )
    assert result["location"] == "New York"
    assert result["posted_at"] == "Posted Yesterday"
    assert len(result["url_hash"]) == 64


def test_normalize_workday_job_missing_external_path_falls_back_to_base_url():
    job = {"title": "Foo"}
    result = normalize_workday_job("X", job, "https://x.wd1.myworkdayjobs.com/External")
    assert result["url"] == "https://x.wd1.myworkdayjobs.com/External"


def test_normalize_linkedin_job():
    data = json.loads((FIXTURES / "linkedin_sample.json").read_text())
    job = data[0]

    result = normalize_linkedin_job(job)

    assert result["source"] == "linkedin"
    assert result["company"] == "Example Startup Inc"
    assert result["title"] == "Software Engineer, Backend"
    assert result["url"] == "https://www.linkedin.com/jobs/view/4012345678"
    assert result["location"] == "New York, NY"
    assert result["posted_at"] == "2026-08-10"
    assert len(result["url_hash"]) == 64
    assert json.loads(result["raw_json"]) == job


def test_normalize_linkedin_job_ignores_per_search_tracking_params_in_link():
    # Live-observed: the same job id comes back with a completely different
    # `link` (different refId/trackingId/position) on every fresh search of
    # the same query. Dedup must key off the stable id, not the raw link,
    # or every re-fetch of an already-seen posting looks "new".
    job_run_1 = {
        "id": "4449057383",
        "title": "Software Developer",
        "link": "https://www.linkedin.com/jobs/view/software-developer-at-x-4449057383"
        "?position=10&pageNum=0&refId=AAA&trackingId=BBB",
    }
    job_run_2 = {
        "id": "4449057383",
        "title": "Software Developer",
        "link": "https://www.linkedin.com/jobs/view/software-developer-at-x-4449057383"
        "?position=3&pageNum=0&refId=ZZZ&trackingId=YYY",
    }

    result_1 = normalize_linkedin_job(job_run_1)
    result_2 = normalize_linkedin_job(job_run_2)

    assert result_1["url_hash"] == result_2["url_hash"]
    assert result_1["url"] == "https://www.linkedin.com/jobs/view/4449057383"


def test_normalize_linkedin_job_falls_back_to_stripped_link_when_id_missing():
    job = {
        "title": "Foo",
        "link": "https://www.linkedin.com/jobs/view/foo-at-x-123?position=1&refId=AAA",
    }
    result = normalize_linkedin_job(job)
    assert result["url"] == "https://www.linkedin.com/jobs/view/foo-at-x-123"


def test_normalize_linkedin_job_missing_fields_defaults_gracefully():
    result = normalize_linkedin_job({})
    assert result["source"] == "linkedin"
    assert result["company"] == ""
    assert result["title"] == ""
    assert result["url"] == ""
    assert result["location"] is None
