"""Workday cxs/jobs polling (Track A, paginated POST).

Two quirks found by hand-testing live Workday tenants (not documented
anywhere, so noting them here):

1. The `total` field in the response is only reliable on the *first* page.
   Deep into a board it can drop to 0 mid-pagination even though more
   postings genuinely remain, so it's used only as a first-page estimate
   and a stop-before-going-too-far safety net -- never as the sole
   "are we done" signal.
2. Requesting an offset past the real end of the list does not return an
   empty page -- several tenants silently wrap around and re-serve page 1.
   That means "empty response" is NOT how these boards signal completion,
   and blindly looping on "keep going until empty" can spin forever. The
   real termination signal is a page shorter than the requested page size.
"""

import time
from urllib.parse import urlparse

import requests

from src.discovery.normalize import normalize_workday_job

PAGE_SIZE = 20
PAGE_DELAY_SECONDS = 0.25
EMPTY_PAGE_RETRIES = 2
EMPTY_PAGE_RETRY_DELAY_SECONDS = 1.0
MAX_PAGES = 300  # safety cap (6000 postings at PAGE_SIZE=20) against the wraparound quirk above


def _workday_urls(careers_url: str, tenant: str, site: str) -> tuple[str, str]:
    """Derive the POST endpoint and the job-page base URL from careers_url's host.

    Using only the host (not the full careers_url path) matters because some
    configs carry a locale segment in careers_url (e.g. .../en-US/Arrowstreet)
    that the cxs endpoint and job permalinks don't use -- the canonical path
    is just /{site}.
    """
    parsed = urlparse(careers_url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    return f"{host}/wday/cxs/{tenant}/{site}/jobs", f"{host}/{site}"


def _fetch_page(cxs_url: str, offset: int, limit: int, timeout: int) -> dict:
    try:
        resp = requests.post(
            cxs_url,
            json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"workday fetch failed at offset={offset}: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"workday fetch failed at offset={offset}: HTTP {resp.status_code}")

    return resp.json()


def fetch_workday_jobs(
    tenant: str,
    site: str,
    company: str,
    careers_url: str,
    page_size: int = PAGE_SIZE,
    page_delay: float = PAGE_DELAY_SECONDS,
    empty_retries: int = EMPTY_PAGE_RETRIES,
    empty_retry_delay: float = EMPTY_PAGE_RETRY_DELAY_SECONDS,
    timeout: int = 20,
) -> list[dict]:
    """Paginate a Workday cxs/jobs board in steps of `page_size` and normalize the results.

    Terminal conditions, checked in this order, first one wins:
      1. a page comes back shorter than `page_size` -- the reliable "last page" signal
      2. offset + page_size reaches the `total` reported by page 1 (safety net,
         stops us before ever requesting an offset that could wrap around)
      3. MAX_PAGES reached (should never fire on real data; last-resort guard)

    A page with zero postings is retried (with a short backoff) up to
    `empty_retries` times before being accepted as terminal -- covers both a
    genuinely job-free board (retries confirm it, we return an empty list)
    and a transient blip (retry recovers real data and pagination continues).
    """
    cxs_url, base_url = _workday_urls(careers_url, tenant, site)
    jobs: list[dict] = []
    offset = 0
    total_expected: int | None = None

    for page_num in range(MAX_PAGES):
        data = _fetch_page(cxs_url, offset, page_size, timeout)
        postings = data.get("jobPostings", [])

        if page_num == 0 and data.get("total"):
            total_expected = data["total"]

        if not postings:
            for _ in range(empty_retries):
                time.sleep(empty_retry_delay)
                data = _fetch_page(cxs_url, offset, page_size, timeout)
                postings = data.get("jobPostings", [])
                if postings:
                    break
            if not postings:
                break  # confirmed empty after retries -- terminal (or genuinely 0 postings)

        jobs.extend(normalize_workday_job(company, job, base_url) for job in postings)

        if len(postings) < page_size:
            break  # partial page = last page
        if total_expected is not None and offset + page_size >= total_expected:
            break  # reached the known total -- stop before risking a wraparound page

        offset += page_size
        time.sleep(page_delay)

    return jobs
