"""Ashby posting-api polling (Track A, simple GET)."""

import requests

from src.discovery.normalize import normalize_ashby_job

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def fetch_ashby_jobs(slug: str, company: str, timeout: int = 20) -> list[dict]:
    """Fetch all open jobs for an Ashby job board and normalize them."""
    url = API_URL.format(slug=slug)
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"ashby fetch failed for slug={slug}: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(
            f"ashby fetch failed for slug={slug}: HTTP {resp.status_code}"
        )

    jobs = resp.json().get("jobs", [])
    return [normalize_ashby_job(company, job) for job in jobs]
