"""Normalize raw ATS job payloads into the shared `postings` row shape.

Every discovery source (greenhouse, ashby, workday, ...) converges on the
same dict shape here so store.insert_new_postings() only needs to know one
format. Keeping this separate from the HTTP-fetching code also makes it
trivial to unit test without hitting the network.
"""

import hashlib
import json


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def normalize_greenhouse_job(company: str, job: dict) -> dict:
    """`first_published` is the actual original-posting date; `updated_at`
    only reflects the last metadata edit and can lag it by years on a
    listing a company periodically bumps or re-saves without actually
    reposting -- checked live against 20 real postings and found gaps up
    to 1,627 days between the two on the same job. Falls back to
    `updated_at` only if `first_published` is ever absent.
    """
    url = job.get("absolute_url", "")
    return {
        "source": f"ats:{company}",
        "company": company,
        "title": job.get("title", ""),
        "url": url,
        "url_hash": _url_hash(url),
        "location": (job.get("location") or {}).get("name"),
        "posted_at": job.get("first_published") or job.get("updated_at"),
        "raw_json": json.dumps(job),
    }


def normalize_ashby_job(company: str, job: dict) -> dict:
    url = job.get("jobUrl") or job.get("applyUrl") or ""
    return {
        "source": f"ats:{company}",
        "company": company,
        "title": job.get("title", ""),
        "url": url,
        "url_hash": _url_hash(url),
        "location": job.get("location"),
        "posted_at": job.get("publishedAt"),
        "raw_json": json.dumps(job),
    }


def normalize_workday_job(company: str, job: dict, base_url: str) -> dict:
    """`base_url` is the job board root, e.g. https://adobe.wd5.myworkdayjobs.com/external_experienced --
    Workday's cxs/jobs response only gives a relative `externalPath` per posting, not an absolute URL.
    """
    external_path = job.get("externalPath", "")
    url = f"{base_url}{external_path}" if external_path else base_url
    return {
        "source": f"ats:{company}",
        "company": company,
        "title": job.get("title", ""),
        "url": url,
        "url_hash": _url_hash(url),
        "location": job.get("locationsText"),
        "posted_at": job.get("postedOn"),
        "raw_json": json.dumps(job),
    }


def _canonical_linkedin_url(job: dict) -> str:
    """LinkedIn job `link` URLs carry per-search tracking params (refId,
    trackingId, position, pageNum) that regenerate on every single search --
    found live, running the same search twice: the same job id came back
    with two completely different `link` values. Hashing the raw link would
    treat every re-fetch of the same posting as "new" and defeat dedup
    entirely, so the canonical URL is built from the stable `id` field
    instead. Falls back to stripping the query string off `link` if `id`
    is ever missing (LinkedIn job URLs are stable up to the query string).
    """
    job_id = job.get("id")
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    link = job.get("link") or job.get("url") or ""
    return link.split("?", 1)[0]


def normalize_linkedin_job(job: dict) -> dict:
    """Field names match the curious_coder/linkedin-jobs-scraper Apify actor's
    documented output schema: id, title, companyName, location, link, postedAt.

    Unlike the ATS normalizers, `source` is the fixed string "linkedin" (not
    "ats:{company}") -- Track B isn't tied to one company's board, it's a
    blanket market scrape, so `company` (from the job payload) is where the
    per-posting company lives instead.
    """
    url = _canonical_linkedin_url(job)
    return {
        "source": "linkedin",
        "company": job.get("companyName", ""),
        "title": job.get("title", ""),
        "url": url,
        "url_hash": _url_hash(url),
        "location": job.get("location"),
        "posted_at": job.get("postedAt"),
        "raw_json": json.dumps(job),
    }
