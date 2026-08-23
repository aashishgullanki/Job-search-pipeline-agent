"""Exit-code tests for the Track C CLI runner -- entirely mocked (no
network, no real fetches). A few companies being bot-blocked is expected
steady-state (confirmed live: 29/32 succeeded, 3 consistently blocked by
anti-bot layers), so the run must not fail the whole pipeline over that;
only something systemic (majority failed, all failed, or no companies
loaded at all) should exit non-zero.
"""

from pathlib import Path

import src.discovery.run_track_c_monitor as run_mod


def _company(name: str) -> dict:
    return {"name": name, "careers_url": f"https://{name.lower()}.example.com/careers"}


def _run_with(monkeypatch, companies: list[dict], statuses: dict[str, dict]):
    """`statuses` maps company name -> the dict check_company() should
    return for it.
    """
    monkeypatch.setattr(run_mod, "track_c_companies", lambda: companies)
    monkeypatch.setattr(run_mod, "check_company", lambda conn, name, url: statuses[name])
    monkeypatch.setattr(run_mod.time, "sleep", lambda seconds: None)  # skip the real inter-company delay
    return run_mod.run(db_path=Path(":memory:"))


def test_all_succeed_exits_zero(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(5)]
    statuses = {c["name"]: {"status": "unchanged", "low_confidence": False} for c in companies}

    exit_code = _run_with(monkeypatch, companies, statuses)

    assert exit_code == 0


def test_a_few_bot_blocked_failures_still_exits_zero(monkeypatch):
    # Mirrors the real 29/32-succeeded case: a small minority failing is
    # expected anti-bot noise, not a broken run.
    companies = [_company(f"Co{i}") for i in range(32)]
    statuses = {c["name"]: {"status": "unchanged", "low_confidence": False} for c in companies}
    for blocked in ["Co0", "Co1", "Co2"]:
        statuses[blocked] = {"status": "fetch_failed", "error": "HTTP 403", "consecutive_fetch_failures": 1}

    exit_code = _run_with(monkeypatch, companies, statuses)

    assert exit_code == 0


def test_exactly_half_failing_still_exits_zero(monkeypatch):
    # Not a majority -- majority means MORE than half.
    companies = [_company(f"Co{i}") for i in range(4)]
    statuses = {
        "Co0": {"status": "fetch_failed", "error": "err", "consecutive_fetch_failures": 1},
        "Co1": {"status": "fetch_failed", "error": "err", "consecutive_fetch_failures": 1},
        "Co2": {"status": "unchanged", "low_confidence": False},
        "Co3": {"status": "unchanged", "low_confidence": False},
    }

    exit_code = _run_with(monkeypatch, companies, statuses)

    assert exit_code == 0


def test_majority_failing_exits_one(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(5)]
    statuses = {c["name"]: {"status": "unchanged", "low_confidence": False} for c in companies}
    for blocked in ["Co0", "Co1", "Co2"]:  # 3 of 5 -- a real majority
        statuses[blocked] = {"status": "fetch_failed", "error": "HTTP 500", "consecutive_fetch_failures": 1}

    exit_code = _run_with(monkeypatch, companies, statuses)

    assert exit_code == 1


def test_every_company_failing_exits_one(monkeypatch):
    companies = [_company(f"Co{i}") for i in range(3)]
    statuses = {c["name"]: {"status": "fetch_failed", "error": "err", "consecutive_fetch_failures": 1} for c in companies}

    exit_code = _run_with(monkeypatch, companies, statuses)

    assert exit_code == 1


def test_no_companies_loaded_exits_one(monkeypatch):
    # Empty config is a real config/loading problem, not a clean "nothing
    # to do" run -- must not silently report success.
    exit_code = _run_with(monkeypatch, [], {})

    assert exit_code == 1
