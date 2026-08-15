"""Apify call shape + adaptive widening tests for Track B, entirely mocked -- no network."""

import json
from pathlib import Path

import pytest

import src.discovery.linkedin as linkedin_mod

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FakeResponse:
    def __init__(self, json_data, status_code=200, text=""):
        self._json = json_data
        self.status_code = status_code
        self.text = text or json.dumps(json_data)[:300]

    def json(self):
        return self._json


def _job(n: int) -> dict:
    return {
        "id": str(n),
        "title": f"Software Engineer {n}",
        "companyName": f"Company {n}",
        "location": "New York, NY",
        "link": f"https://www.linkedin.com/jobs/view/{n}",
        "postedAt": "2026-08-10",
    }


# --- run_apify_actor ---


def test_run_apify_actor_posts_token_as_query_param_and_input_as_body(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return FakeResponse([_job(1)])

    monkeypatch.setattr(linkedin_mod.requests, "post", fake_post)

    result = linkedin_mod.run_apify_actor("some~actor", "tok123", {"keywords": "Engineer"})

    assert result == [_job(1)]
    assert captured["params"] == {"token": "tok123"}
    assert captured["json"] == {"keywords": "Engineer"}
    assert "some~actor" in captured["url"]


def test_run_apify_actor_raises_on_non_200(monkeypatch):
    monkeypatch.setattr(
        linkedin_mod.requests,
        "post",
        lambda *a, **kw: FakeResponse({}, status_code=401, text="unauthorized"),
    )

    with pytest.raises(RuntimeError, match="401"):
        linkedin_mod.run_apify_actor("some~actor", "bad-token", {})


def test_run_apify_actor_raises_on_network_error(monkeypatch):
    import requests

    def raise_conn_error(*args, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(linkedin_mod.requests, "post", raise_conn_error)

    with pytest.raises(RuntimeError, match="boom"):
        linkedin_mod.run_apify_actor("some~actor", "tok", {})


# --- fetch_linkedin_jobs_with_widening ---


def make_fake_search(results_per_call):
    """results_per_call: list of raw-job-lists returned in order across calls."""
    calls = []
    it = iter(results_per_call)

    def fake_search(token, keywords, location, distance, limit, timeout=180):
        calls.append({"keywords": keywords, "distance": distance})
        return next(it)

    fake_search.calls = calls
    return fake_search


def test_no_widening_needed_when_first_attempt_clears_threshold(monkeypatch):
    fake_search = make_fake_search([[_job(i) for i in range(10)]])
    monkeypatch.setattr(linkedin_mod, "search_linkedin_jobs", fake_search)

    jobs, attempts = linkedin_mod.fetch_linkedin_jobs_with_widening("tok", min_results=5)

    assert len(jobs) == 10
    assert len(attempts) == 1
    assert len(fake_search.calls) == 1


def test_widens_keywords_then_stops_once_threshold_cleared(monkeypatch):
    fake_search = make_fake_search(
        [
            [_job(1), _job(2)],  # attempt 1: too few (2 < 5)
            [_job(i) for i in range(8)],  # attempt 2 (widened keywords): enough
        ]
    )
    monkeypatch.setattr(linkedin_mod, "search_linkedin_jobs", fake_search)

    jobs, attempts = linkedin_mod.fetch_linkedin_jobs_with_widening("tok", min_results=5)

    assert len(jobs) == 8
    assert len(attempts) == 2
    assert attempts[0]["results"] == 2
    assert attempts[1]["results"] == 8
    # second attempt's keywords must be broader than the first
    assert fake_search.calls[1]["keywords"] != fake_search.calls[0]["keywords"]


def test_widens_radius_on_third_attempt(monkeypatch):
    fake_search = make_fake_search(
        [
            [_job(1)],  # attempt 1: too few
            [_job(1), _job(2)],  # attempt 2 (wider keywords): still too few
            [_job(i) for i in range(6)],  # attempt 3 (wider radius): enough
        ]
    )
    monkeypatch.setattr(linkedin_mod, "search_linkedin_jobs", fake_search)

    jobs, attempts = linkedin_mod.fetch_linkedin_jobs_with_widening("tok", min_results=5)

    assert len(jobs) == 6
    assert len(attempts) == 3
    assert fake_search.calls[2]["distance"] > fake_search.calls[0]["distance"]


def test_gives_up_after_exhausting_all_widening_steps_but_returns_best_partial(monkeypatch):
    # Every attempt stays under threshold; the ladder has 4 steps.
    fake_search = make_fake_search(
        [
            [_job(1)],  # 1 result
            [_job(1), _job(2), _job(3)],  # 3 results -- the best of the lot
            [_job(1), _job(2)],  # 2 results
            [],  # 0 results
        ]
    )
    monkeypatch.setattr(linkedin_mod, "search_linkedin_jobs", fake_search)

    jobs, attempts = linkedin_mod.fetch_linkedin_jobs_with_widening("tok", min_results=5)

    assert len(attempts) == 4  # exhausted the whole ladder, never gave up early
    assert len(jobs) == 3  # returns the best attempt found (3), not the last (0) or nothing
    assert all(a["results"] < 5 for a in attempts)  # honestly never cleared the threshold


def test_widening_ladder_has_no_duplicate_steps():
    # Sanity check on the module-level constant: each step should differ
    # from the previous in keywords and/or distance, or "widening" is a no-op.
    steps = linkedin_mod.WIDENING_STEPS
    for prev, cur in zip(steps, steps[1:]):
        assert prev != cur
