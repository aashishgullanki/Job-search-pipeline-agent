"""Exit-code tests for the Track A Greenhouse/Ashby CLI runner -- entirely
mocked (no network). Same reasoning as the Workday and Track C runners:
one company hitting a fetch problem is expected steady-state, not a run
worth failing the whole pipeline over -- only something systemic
(majority failed, all failed, or no companies loaded) should exit
non-zero. Mirrors tests/discovery/test_run_track_a_workday.py's shape.
"""

from pathlib import Path

import src.discovery.run_track_a_simple as run_mod


def _company(name: str, ats_type: str = "greenhouse") -> dict:
    return {"name": name, "ats_type": ats_type, "slug": name.lower()}


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


def _run_with(monkeypatch, companies: list[dict], results: dict[str, list[dict] | Exception]):
    """`results` maps company name -> a list of jobs to "fetch" successfully,
    or an Exception instance to raise for that company.
    """
    monkeypatch.setattr(run_mod, "track_a_simple_companies", lambda: companies)

    def fake_fetch_greenhouse(slug, name):
        result = results[name]
        if isinstance(result, Exception):
            raise result
        return result

    def fake_fetch_ashby(slug, name):
        result = results[name]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(run_mod, "fetch_greenhouse_jobs", fake_fetch_greenhouse)
    monkeypatch.setattr(run_mod, "fetch_ashby_jobs", fake_fetch_ashby)
    return run_mod.run(db_path=Path(":memory:"))


def test_all_succeed_exits_zero(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(5)]
    results = {c["name"]: [] for c in companies}

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 0


def test_one_company_failure_still_exits_zero(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(5)]
    results = {c["name"]: [] for c in companies}
    results["Co0"] = RuntimeError("greenhouse fetch failed: HTTP 503")

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 0


def test_majority_failing_exits_one(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(5)]
    results = {c["name"]: [] for c in companies}
    for failed in ["Co0", "Co1", "Co2"]:
        results[failed] = RuntimeError("HTTP 503")

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 1


def test_every_company_failing_exits_one(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(3)]
    results = {c["name"]: RuntimeError("HTTP 503") for c in companies}

    exit_code = _run_with(monkeypatch, companies, results)

    assert exit_code == 1


def test_no_companies_loaded_exits_one(monkeypatch):
    exit_code = _run_with(monkeypatch, [], {})

    assert exit_code == 1
