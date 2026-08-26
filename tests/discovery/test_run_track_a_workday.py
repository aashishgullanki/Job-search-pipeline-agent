"""Exit-code tests for the Track A Workday CLI runner -- entirely mocked
(no network). One company hitting a transient/unrecoverable fetch problem
is expected steady-state (live-observed: Nvidia's Workday board 500'd
mid-pagination on two separate real runs while 16/17 other companies
succeeded cleanly), so the run must not fail the whole pipeline over
that; only something systemic (majority failed, all failed, or no
companies loaded) should exit non-zero. Mirrors
tests/discovery/test_run_track_c_monitor.py's shape.
"""

from pathlib import Path

import src.discovery.run_track_a_workday as run_mod


def _company(name: str) -> dict:
    return {"name": name, "tenant": "t", "site": "s", "careers_url": f"https://{name.lower()}.example.com"}


def _job(company: str, n: int) -> dict:
    return {
        "source": f"ats:{company}",
        "company": company,
        "title": f"Job {n}",
        "url": f"https://{company.lower()}.example.com/job/{n}",
        "url_hash": f"{company}-{n}",
        "location": "New York",
        "posted_at": None,
        "raw_json": "{}",
    }


def _run_with(
    monkeypatch, companies: list[dict], results: dict[str, tuple[list[dict], str | None]], db_path: Path | None = None
):
    """`results` maps company name -> (jobs, error) that fetch_workday_jobs should return for it."""
    monkeypatch.setattr(run_mod, "track_a_workday_companies", lambda: companies)
    monkeypatch.setattr(run_mod, "fetch_workday_jobs", lambda **kwargs: results[kwargs["company"]])
    return run_mod.run(db_path=db_path or Path(":memory:"))


def test_all_succeed_exits_zero(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(5)]
    results = {c["name"]: ([], None) for c in companies}

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 0


def test_one_company_partial_failure_still_exits_zero(monkeypatch):
    # Mirrors the real Nvidia case: 16/17 clean, 1 partial (jobs fetched
    # before the failure point, plus an error message).
    companies = [_company(f"Co{i}") for i in range(17)]
    results = {c["name"]: ([], None) for c in companies}
    results["Co0"] = ([_job("Co0", 1)], "workday fetch failed at offset=480: HTTP 500")

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 0


def test_majority_failing_exits_one(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(5)]
    results = {c["name"]: ([], None) for c in companies}
    for failed in ["Co0", "Co1", "Co2"]:  # 3 of 5 -- a real majority
        results[failed] = ([], "HTTP 500")

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 1


def test_every_company_failing_exits_one(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(3)]
    results = {c["name"]: ([], "HTTP 500") for c in companies}

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 1


def test_no_companies_loaded_exits_one(monkeypatch):
    exit_code = _run_with(monkeypatch, [], {})

    assert exit_code == 1


def test_partial_results_are_still_inserted_into_the_db(monkeypatch, tmp_path):
    # The whole point of returning partial credit instead of raising --
    # confirm the postings fetched before a failure actually get saved.
    # A second, clean company keeps this at 1/2 failed (not a majority),
    # so the exit-code path doesn't obscure what this test actually checks.
    companies = [_company("Nvidia"), _company("PayPal")]
    results = {
        "Nvidia": ([_job("Nvidia", 1)], "workday fetch failed at offset=480: HTTP 500"),
        "PayPal": ([_job("PayPal", 1)], None),
    }

    db_path = tmp_path / "test.db"
    exit_code = _run_with(monkeypatch, companies, results, db_path=db_path)

    import sqlite3

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM postings WHERE company = 'Nvidia'").fetchone()[0]
    conn.close()

    assert count == 1  # the one job fetched before the failure was saved, not discarded
    assert exit_code == 0
