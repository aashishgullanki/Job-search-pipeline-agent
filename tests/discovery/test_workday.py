"""Pagination/retry tests for the Workday runner, entirely mocked -- no network.

Covers the two live quirks the fetch loop is built around (see
src/discovery/workday.py's module docstring): the unreliable `total` field
and offsets wrapping around past the end instead of returning empty --
plus the transient-page-failure retry-then-partial-credit behavior, added
after Nvidia's Workday board 500'd mid-pagination on two separate real runs.
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

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 25
    assert error is None
    assert fake_post.calls == [0, 10, 20]


def test_pagination_stops_at_known_total_without_extra_request(monkeypatch):
    # total=5 on page 1, page_size=10 -> the first page is already short (5<10),
    # so pagination must stop after exactly one request.
    pages = {0: [{"total": 5, "jobPostings": [_job(i) for i in range(5)]}]}
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 5
    assert error is None
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

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 20
    assert error is None
    assert fake_post.calls == [0, 10, 10]  # offset 10 hit twice: blip + retry


def test_genuinely_empty_board_returns_no_jobs_after_retries(monkeypatch):
    # A company with zero open postings: total=0, empty on every attempt.
    pages = {0: [{"total": 0, "jobPostings": []}]}
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10, empty_retries=2,
    )

    assert jobs == []
    assert error is None
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

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert len(jobs) == 20
    assert error is None
    assert fake_post.calls == [0, 10]


def test_locale_prefix_in_careers_url_is_stripped_from_job_urls(monkeypatch):
    # careers_url carries a locale segment (en-US) that must NOT leak into
    # the cxs endpoint or the stored job URL -- only the host + site matter.
    pages = {0: [{"total": 1, "jobPostings": [_job(1)]}]}
    fake_post = make_fake_post(pages)
    _patch(monkeypatch, fake_post)

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="arrowstreetcapital", site="Arrowstreet", company="Arrowstreet Capital",
        careers_url="https://arrowstreetcapital.wd5.myworkdayjobs.com/en-US/Arrowstreet",
    )

    assert error is None
    assert jobs[0]["url"] == "https://arrowstreetcapital.wd5.myworkdayjobs.com/Arrowstreet/job/City/Job-1_R1"


# --- transient page-fetch failure: retry, then partial credit ---
# Added live: Nvidia's Workday board 500'd mid-pagination on two separate
# real runs. A page-level failure must not throw away postings already
# fetched from earlier pages, and a genuinely transient blip should just
# recover silently via retry.


def test_transient_500_recovers_after_one_retry(monkeypatch):
    call_count = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        offset = json["offset"]
        if offset == 10:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeResponse({}, status_code=500)  # first attempt at offset 10 fails
        pages = {
            0: {"total": 20, "jobPostings": [_job(i) for i in range(10)]},
            10: {"jobPostings": [_job(i) for i in range(10, 20)]},
        }
        return FakeResponse(pages[offset])

    _patch(monkeypatch, fake_post)

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
    )

    assert error is None
    assert len(jobs) == 20  # the retry recovered offset 10's data too
    assert call_count["n"] == 2  # failed once, succeeded on retry


def test_transient_error_exhausting_retries_returns_partial_jobs_not_none(monkeypatch):
    # offset=10 fails every attempt (retries + initial = 3 total). Page 0's
    # 10 already-fetched jobs must NOT be thrown away just because a later
    # page never recovered.
    def fake_post(url, json=None, headers=None, timeout=None):
        offset = json["offset"]
        if offset == 0:
            return FakeResponse({"total": 20, "jobPostings": [_job(i) for i in range(10)]})
        return FakeResponse({}, status_code=500)

    _patch(monkeypatch, fake_post)

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
        transient_error_retries=2,
    )

    assert len(jobs) == 10  # page 0's postings preserved, not discarded
    assert error is not None
    assert "500" in error
    assert "offset=10" in error


def test_transient_error_retries_the_exact_configured_number_of_times(monkeypatch):
    calls_at_offset_10 = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        offset = json["offset"]
        if offset == 0:
            return FakeResponse({"total": 20, "jobPostings": [_job(i) for i in range(10)]})
        calls_at_offset_10["n"] += 1
        return FakeResponse({}, status_code=500)

    _patch(monkeypatch, fake_post)

    workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", page_size=10,
        transient_error_retries=2,
    )

    assert calls_at_offset_10["n"] == 3  # initial attempt + 2 retries


def test_first_page_failing_returns_empty_jobs_and_error_not_none(monkeypatch):
    # No pages ever succeeded -- jobs is empty, but the caller still gets a
    # normal (jobs, error) tuple, not an exception.
    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse({}, status_code=500)

    _patch(monkeypatch, fake_post)

    jobs, error = workday_mod.fetch_workday_jobs(
        tenant="t", site="s", company="X",
        careers_url="https://t.wd5.myworkdayjobs.com/s", transient_error_retries=1,
    )

    assert jobs == []
    assert error is not None
