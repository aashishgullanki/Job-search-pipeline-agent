"""Pagination/retry tests for the Workday runner, entirely mocked -- no network.

Covers the two live quirks the fetch loop is built around (see
src/discovery/workday.py's module docstring): the unreliable `total` field
and offsets wrapping around past the end instead of returning empty.
"""

import src.discovery.workday as workday_mod


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json


def _job(n: int) -> dict:
    return {
        "title": f"Job {n}",
        "externalPath": f"/job/City/Job-{n}_R{n}",
        "locationsText": "New York",
        "postedOn": "Posted Yesterday",
    }


def make_fake_post(pages_by_offset):
    """pages_by_offset: {offset: [response_dict, ...]} -- responses for repeat
    calls at the same offset are consumed in order, last one repeats after that.
    """
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        offset = json["offset"]
        calls.append(offset)
        queue = pages_by_offset[offset]
        resp = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse(resp)

    fake_post.calls = calls
    return fake_post


def _patch(monkeypatch, fake_post):
    monkeypatch.setattr(workday_mod.requests, "post", fake_post)
    monkeypatch.setattr(workday_mod.time, "sleep", lambda *_: None)


def test_pagination_stops_on_partial_page(monkeypatch):
    # total=25, page_size=10 -> pages of 10, 10, 5 (partial = terminal)
    pages = {
        0: [{"total": 25, "jobPostings": [_job(i) for i in range(10)]}],
        10: [{"jobPostings": [_job(i) for i in range(10, 20)]}],
        20: [{"jobPostings": [_job(i) for i in range(20, 25)]}],
    }
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 25
    assert fake_post.calls == [0, 10, 20]


def test_pagination_stops_at_known_total_without_extra_request(monkeypatch):
    # total=5 on page 1, page_size=10 -> the first page is already short (5<10),
    # so pagination must stop after exactly one request.
    pages = {0: [{"total": 5, "jobPostings": [_job(i) for i in range(5)]}]}
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 5
    assert fake_post.calls == [0]


def test_empty_page_is_retried_before_being_treated_as_terminal(monkeypatch):
    # offset=10 comes back empty once (simulated blip), then recovers with real data.
    pages = {
        0: [{"total": 20, "jobPostings": [_job(i) for i in range(10)]}],
        10: [
            {"jobPostings": []},
            {"jobPostings": [_job(i) for i in range(10, 20)]},
        ],
    }
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 20
    assert fake_post.calls == [0, 10, 10]  # offset 10 hit twice: blip + retry


def test_genuinely_empty_board_returns_no_jobs_after_retries(monkeypatch):
    # A company with zero open postings: total=0, empty on every attempt.
    pages = {0: [{"total": 0, "jobPostings": []}]}
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10, empty_retries=2,
    )

    assert jobs == []
    assert fake_post.calls == [0, 0, 0]  # initial + 2 retries, all at the same offset


def test_wraparound_past_total_is_never_requested(monkeypatch):
    # Regression guard for the "offset past total silently re-serves page 1"
    # quirk: once offset+page_size reaches the known total we must stop,
    # never issuing the out-of-range request that would trigger it.
    pages = {
        0: [{"total": 20, "jobPostings": [_job(i) for i in range(10)]}],
        10: [{"jobPostings": [_job(i) for i in range(10, 20)]}],
        # deliberately no entry for offset=20 -- a KeyError here would mean
        # the loop over-paginated instead of stopping at the known total
    }
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 20
    assert fake_post.calls == [0, 10]


def test_http_error_raises_runtime_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse({}, status_code=500)

    monkeypatch.setattr(workday_mod.requests, "post", fake_post)
    monkeypatch.setattr(workday_mod.time, "sleep", lambda *_: None)

    try:
        workday_mod.fetch_workday_jobs(
            tenant="t", site="s", company="X",
            careers_url="https://t.wd5.myworkdayjobs.com/s",
        )
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "500" in str(e)


def test_locale_prefix_in_careers_url_is_stripped_from_job_urls(monkeypatch):
    # careers_url carries a locale segment (en-US) that must NOT leak into
    # the cxs endpoint or the stored job URL -- only the host + site matter.
    pages = {0: [{"total": 1, "jobPostings": [_job(1)]}]}
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs = workday_mod.fetch_workday_jobs(
        tenant="arrowstreetcapital", site="Arrowstreet", company="Arrowstreet Capital",
        careers_url="https://arrowstreetcapital.wd5.myworkdayjobs.com/en-US/Arrowstreet",
    )

    assert jobs[0]["url"] == "https://arrowstreetcapital.wd5.myworkdayjobs.com/Arrowstreet/job/City/Job-1_R1"
