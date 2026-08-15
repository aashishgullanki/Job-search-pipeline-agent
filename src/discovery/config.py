"""Load config/companies.yaml and slice it by track/ats_type."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "companies.yaml"


def load_companies(config_path: Path = DEFAULT_CONFIG_PATH) -> list[dict]:
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return data["companies"]


def track_a_simple_companies(companies: list[dict] | None = None) -> list[dict]:
    """Track A companies polled via simple GET-based APIs (Greenhouse + Ashby).

    Excludes Workday, which needs paginated POST requests and its own runner.
    """
    if companies is None:
        companies = load_companies()
    return [
        c
        for c in companies
        if c.get("track") == "A" and c.get("ats_type") in ("greenhouse", "ashby")
    ]


def track_a_workday_companies(companies: list[dict] | None = None) -> list[dict]:
    """Track A companies polled via Workday's paginated cxs/jobs POST endpoint."""
    if companies is None:
        companies = load_companies()
    return [c for c in companies if c.get("track") == "A" and c.get("ats_type") == "workday"]


def track_c_companies(companies: list[dict] | None = None) -> list[dict]:
    """Track C companies monitored via careers-page hash-diffing (no clean ATS API)."""
    if companies is None:
        companies = load_companies()
    return [c for c in companies if c.get("track") == "C"]
