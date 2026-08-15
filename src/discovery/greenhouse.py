"""Greenhouse boards-api polling (Track A, simple GET)."""

import requests

from src.discovery.normalize import normalize_greenhouse_job

API_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def fetch_greenhouse_jobs(slug: str, company: str, timeout: int = 20) -> list[dict]:
    """Fetch all open jobs for a Greenhouse board and normalize them.

    Raises RuntimeError on a non-200 response or request failure -- the
    caller decides whether that's fatal for the run or just one company's
    failure to report.
    """
    url = API_URL.format(slug=slug)
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"greenhouse fetch failed for slug={slug}: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(
            f"greenhouse fetch failed for slug={slug}: HTTP {resp.status_code}"
        )

    jobs = resp.json().get("jobs", [])
    return [normalize_greenhouse_job(company, job) for job in jobs]
