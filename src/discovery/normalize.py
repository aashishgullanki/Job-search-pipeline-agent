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
    url = job.get("absolute_url", "")
    return {
        "source": f"ats:{company}",
        "company": company,
        "title": job.get("title", ""),
        "url": url,
        "url_hash": _url_hash(url),
        "location": (job.get("location") or {}).get("name"),
        "posted_at": job.get("updated_at"),
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
