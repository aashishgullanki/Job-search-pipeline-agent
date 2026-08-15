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


def normalize_linkedin_job(job: dict) -> dict:
    """Field names match the curious_coder/linkedin-jobs-scraper Apify actor's
    documented output schema: title, companyName, location, link, postedAt.

    Unlike the ATS normalizers, `source` is the fixed string "linkedin" (not
    "ats:{company}") -- Track B isn't tied to one company's board, it's a
    blanket market scrape, so `company` (from the job payload) is where the
    per-posting company lives instead.
    """
    url = job.get("link") or job.get("url") or ""
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
