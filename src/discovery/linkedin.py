"""Track B: broad NYC market scrape via the Apify LinkedIn jobs scraper actor.

Unlike Track A/C, this isn't scoped to a fixed company list -- it casts a
wider net for NYC SWE/AI Engineer roles at companies not on the tier list
(see architecture doc section 2, Track B). Actor: curious_coder/linkedin-
jobs-scraper (input: keywords/location/distance/limitPerSource; output per
job: id/title/companyName/location/link/postedAt/employmentType -- taken
from the actor's public documentation, not invented).

Agentic feature: if a search returns too few results, widen before giving
up rather than just reporting "nothing found" -- same principle as the
empty-page retry in workday.py and the hash-diff-then-check escalation in
company_monitor.py. Widening here escalates in stages: broaden the keyword
set first (cheaper, more targeted), then widen the search radius, ending on
the broadest combination -- capped at a fixed number of attempts so a
chronically thin market doesn't retry forever.
"""

import requests

from src.discovery.normalize import normalize_linkedin_job

APIFY_ACTOR_ID = "curious_coder~linkedin-jobs-scraper"
APIFY_RUN_SYNC_URL = "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"

DEFAULT_LOCATION = "New York, NY"
DEFAULT_LIMIT = 50
MIN_RESULTS_THRESHOLD = 5

# Escalating widening ladder: widen keywords before radius, since a broader
# radius on an already-broad keyword set returns mostly-irrelevant NYC-metro
# noise, whereas broader keywords on a tight radius stays NYC-relevant.
WIDENING_STEPS = [
    {"keywords": "Software Engineer", "distance": 25},
    {"keywords": "Software Engineer OR AI Engineer OR Machine Learning Engineer", "distance": 25},
    {"keywords": "Software Engineer OR AI Engineer OR Machine Learning Engineer", "distance": 50},
    {"keywords": "Engineer", "distance": 75},
]


def run_apify_actor(actor_id: str, token: str, run_input: dict, timeout: int = 180) -> list[dict]:
    """POST to Apify's run-sync-get-dataset-items endpoint and return the raw dataset items.

    This is the generic, actor-agnostic Apify pattern (works for any actor
    given its own input schema) -- only the input shape and output field
    names in normalize_linkedin_job() are specific to this actor.
    """
    url = APIFY_RUN_SYNC_URL.format(actor_id=actor_id)
    try:
        resp = requests.post(url, params={"token": token}, json=run_input, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"apify actor run failed: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(
            f"apify actor run failed: HTTP {resp.status_code} - {resp.text[:300]}"
        )

    return resp.json()


def search_linkedin_jobs(
    token: str, keywords: str, location: str, distance: int, limit: int, timeout: int = 180
) -> list[dict]:
    run_input = {
        "keywords": keywords,
        "location": location,
        "distance": distance,
        "limitPerSource": limit,
    }
    return run_apify_actor(APIFY_ACTOR_ID, token, run_input, timeout=timeout)


def fetch_linkedin_jobs_with_widening(
    token: str,
    location: str = DEFAULT_LOCATION,
    limit: int = DEFAULT_LIMIT,
    min_results: int = MIN_RESULTS_THRESHOLD,
    steps: list[dict] | None = None,
    timeout: int = 180,
) -> tuple[list[dict], list[dict]]:
    """Run the widening ladder until a step clears `min_results`, or give up
    after exhausting `steps`.

    Returns (normalized_jobs, attempts_log). `normalized_jobs` is the result
    of whichever step returned the *most* postings (not necessarily the
    last one tried) -- if every step stays under threshold, the caller still
    gets the best partial results found rather than nothing, but the
    attempts_log makes it clear the threshold was never actually met so the
    caller can report that honestly instead of implying a clean success.
    """
    if steps is None:
        steps = WIDENING_STEPS

    attempts_log: list[dict] = []
    best_raw: list[dict] = []

    for i, step in enumerate(steps):
        raw = search_linkedin_jobs(
            token, step["keywords"], location, step["distance"], limit, timeout=timeout
        )
        attempts_log.append(
            {
                "attempt": i + 1,
                "keywords": step["keywords"],
                "distance": step["distance"],
                "results": len(raw),
            }
        )
        if len(raw) > len(best_raw):
            best_raw = raw
        if len(raw) >= min_results:
            break

    normalized = [normalize_linkedin_job(job) for job in best_raw]
    return normalized, attempts_log
