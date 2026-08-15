"""Track C: hash-diff monitoring for the 32 custom/workday_protected companies
that don't have a clean structured ATS API (see config/companies.yaml).

Design (see commit message / README for the full reasoning, tested against
live pages before committing to this approach):

- Hash *normalized visible text*, not raw HTML and not embedded JS state
  blobs. Raw HTML is dominated by script/style noise and cache-busting query
  strings. Embedded SPA state (e.g. Next.js `__NEXT_DATA__`) looked promising
  but tested out badly on a real site (Goldman Sachs): the job data isn't in
  there at all (it's fetched client-side after load), while a `buildId` field
  that changes on every deploy IS in there -- hashing it would false-positive
  on every redeploy regardless of job content. So: strip script/style/
  noscript/comments, extract visible text, scrub obvious volatile patterns
  (relative timestamps), collapse whitespace, hash that.

- Known limitation, tracked explicitly rather than papered over: several of
  these 32 companies are heavy client-rendered SPAs where the *initial* HTML
  has little real content -- job listings load via a client-side API call
  the plain fetch never sees. Pages that look like this (low visible word
  count + script tags present) are flagged `low_confidence` in the stored
  state so it's visible which companies this mechanism is weak for, instead
  of silently pretending hash-diffing works uniformly well everywhere.

- On a hash change: diff the stored normalized text against the new one
  (difflib), keyword-gate the added lines for job-title-shaped language, and
  only then write an alert. This is the "scrape of the diffed section"
  option from the architecture doc, chosen over the "Claude web-search call"
  option because it's deterministic and fixture-testable with no added
  per-run API cost; the web-search option remains a documented upgrade path
  for the interactive company-monitor subagent.

- Alerts go to a separate `company_monitor_alerts` table, not `postings` --
  Track C can't reliably extract a structured title/URL from arbitrary
  bespoke markup, so its output shouldn't masquerade as a confirmed posting
  in the same review surface as Track A/B's API-confirmed rows. First run
  per company is baseline-only (nothing to diff against yet, so no alert);
  a hash change whose diff doesn't look job-shaped is silently absorbed;
  repeat alerts on the same diff content are deduped by a hash of the
  flagged snippet.
"""

import difflib
import hashlib
import re
import sqlite3

import requests
from bs4 import BeautifulSoup, Comment

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

LOW_CONTENT_WORD_THRESHOLD = 200  # below this + script tags present -> flag low_confidence

# Regex-scrubbed before hashing: patterns that change on their own schedule
# independent of whether any job content changed.
_VOLATILE_PATTERNS = [
    re.compile(r"\bposted\s+(today|yesterday)\b", re.IGNORECASE),
    re.compile(r"\b\d+\+?\s*(day|hour|minute|week)s?\s+ago\b", re.IGNORECASE),
    re.compile(r"\b(19|20)\d{2}\b"),  # bare years (copyright footers etc.)
    # Per-request tracking/session tokens rendered inline as visible text --
    # found live on the Phenom People platform (Snowflake, eBay both regenerate
    # a fresh 32-char hex token on every single fetch, seconds apart, with no
    # content change). A 16+ char hex/uuid-shaped run is never a real job title
    # or location, so scrubbing it is safe.
    re.compile(r"\b[a-f0-9]{16,}\b", re.IGNORECASE),
]

_JOB_TITLE_KEYWORDS = [
    "software engineer",
    "swe",
    "ai engineer",
    "ml engineer",
    "machine learning engineer",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "full-stack engineer",
    "data engineer",
    "data scientist",
    "site reliability engineer",
    "infrastructure engineer",
    "platform engineer",
    "research engineer",
    "applied scientist",
]
_NYC_KEYWORDS = ["new york", "nyc", "ny,", "manhattan", "brooklyn"]


def fetch_page(url: str, timeout: int = 20) -> str:
    """GET the careers page with a browser-like UA, following redirects.

    Several of these sites block plain/non-browser requests (403s observed
    live for e.g. Tesla, Citadel Securities) -- this doesn't work around
    real bot-detection, it just avoids failing on the trivially-blockable
    default User-Agent.
    """
    try:
        resp = requests.get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True
        )
    except requests.RequestException as e:
        raise RuntimeError(f"fetch failed for {url}: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"fetch failed for {url}: HTTP {resp.status_code}")

    return resp.text


def normalize_html_to_text(
    html: str, low_content_word_threshold: int = LOW_CONTENT_WORD_THRESHOLD
) -> tuple[str, bool]:
    """Strip script/style/noscript/comments, extract visible text, scrub
    volatile patterns, collapse whitespace.

    Returns (normalized_text, looks_low_content) -- the second flag is True
    when the page reads as a client-rendered shell (few real words despite
    carrying script tags), a signal the hash-diff won't reliably catch new
    postings here. `low_content_word_threshold` is exposed mainly for tests
    against small fixtures; production callers should use the default.
    """
    soup = BeautifulSoup(html, "html.parser")
    had_scripts = bool(soup.find_all("script"))

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    text = soup.get_text(separator=" ")
    for pattern in _VOLATILE_PATTERNS:
        text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()

    word_count = len(text.split())
    looks_low_content = had_scripts and word_count < low_content_word_threshold
    return text, looks_low_content


def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def diff_added_lines(old_text: str, new_text: str) -> list[str]:
    """Return the lines/segments present in new_text but not old_text.

    Both inputs are single long normalized strings (not naturally
    line-broken), so they're chunked into pseudo-lines by sentence-ish
    boundaries before diffing -- difflib works line-by-line, and diffing
    two giant one-line strings word-by-word is too noisy to be useful.
    """

    def chunk(text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+|\s{2,}", text)
        return [p.strip() for p in parts if p.strip()]

    old_chunks = chunk(old_text)
    new_chunks = chunk(new_text)
    sm = difflib.SequenceMatcher(a=old_chunks, b=new_chunks, autojunk=False)
    added = []
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            added.extend(new_chunks[j1:j2])
    return added


def find_job_signal(diff_lines: list[str]) -> dict | None:
    """Keyword-gate the diff. Returns a match summary dict, or None if the
    diff doesn't look job-related.

    A job-title keyword is required; an NYC keyword is a confidence boost,
    not a requirement, since the diff window is small and may not include
    location text even for a genuinely NYC-relevant posting.
    """
    joined = " ".join(diff_lines).lower()
    title_hits = [kw for kw in _JOB_TITLE_KEYWORDS if kw in joined]
    if not title_hits:
        return None
    location_hits = [kw for kw in _NYC_KEYWORDS if kw in joined]
    return {
        "title_keywords": title_hits,
        "nyc_keywords": location_hits,
        "confidence": "high" if location_hits else "medium",
    }


def _get_stored_state(conn: sqlite3.Connection, company: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM company_page_hashes WHERE company = ?", (company,)
    ).fetchone()


def _record_fetch_failure(conn: sqlite3.Connection, company: str) -> None:
    row = _get_stored_state(conn, company)
    if row is None:
        # No prior state and the very first fetch failed -- nothing to update yet.
        conn.execute(
            """INSERT INTO company_page_hashes (company, page_hash, last_content, consecutive_fetch_failures)
               VALUES (?, '', NULL, 1)""",
            (company,),
        )
    else:
        conn.execute(
            """UPDATE company_page_hashes
               SET consecutive_fetch_failures = consecutive_fetch_failures + 1, checked_at = datetime('now')
               WHERE company = ?""",
            (company,),
        )
    conn.commit()


def _store_state(
    conn: sqlite3.Connection, company: str, page_hash: str, content: str, low_confidence: bool
) -> None:
    conn.execute(
        """INSERT INTO company_page_hashes (company, page_hash, last_content, low_confidence, consecutive_fetch_failures)
           VALUES (?, ?, ?, ?, 0)
           ON CONFLICT(company) DO UPDATE SET
               page_hash = excluded.page_hash,
               last_content = excluded.last_content,
               low_confidence = excluded.low_confidence,
               consecutive_fetch_failures = 0,
               checked_at = datetime('now')""",
        (company, page_hash, content, int(low_confidence)),
    )
    conn.commit()


def _insert_alert_if_new(
    conn: sqlite3.Connection, company: str, careers_url: str, diff_lines: list[str]
) -> bool:
    snippet = " | ".join(diff_lines)[:2000]
    snippet_hash = hash_content(snippet)
    cur = conn.execute(
        """INSERT OR IGNORE INTO company_monitor_alerts (company, careers_url, diff_snippet, snippet_hash)
           VALUES (?, ?, ?, ?)""",
        (company, careers_url, snippet, snippet_hash),
    )
    conn.commit()
    return cur.rowcount > 0  # False means this exact diff signal already alerted before (deduped)


def check_company(conn: sqlite3.Connection, company: str, careers_url: str) -> dict:
    """Fetch, hash, and (on a real content change) alert for one company.

    Returns a status dict for the caller to report/summarize. Never raises
    on fetch failure -- that's tracked in company_page_hashes and reported
    back as status='fetch_failed', so one blocked company doesn't stop the run.
    """
    try:
        html = fetch_page(careers_url)
    except RuntimeError as e:
        _record_fetch_failure(conn, company)
        row = _get_stored_state(conn, company)
        failures = row["consecutive_fetch_failures"] if row else 1
        return {"status": "fetch_failed", "error": str(e), "consecutive_fetch_failures": failures}

    text, low_confidence = normalize_html_to_text(html)
    new_hash = hash_content(text)
    prior = _get_stored_state(conn, company)

    if prior is None or not prior["page_hash"]:
        _store_state(conn, company, new_hash, text, low_confidence)
        return {"status": "baseline_seeded", "low_confidence": low_confidence}

    if prior["page_hash"] == new_hash:
        _store_state(conn, company, new_hash, text, low_confidence)
        return {"status": "unchanged", "low_confidence": low_confidence}

    old_text = prior["last_content"] or ""
    diff_lines = diff_added_lines(old_text, text)
    signal = find_job_signal(diff_lines)

    _store_state(conn, company, new_hash, text, low_confidence)

    if signal is None:
        return {"status": "changed_no_signal", "low_confidence": low_confidence}

    is_new_alert = _insert_alert_if_new(conn, company, careers_url, diff_lines)
    return {
        "status": "alert" if is_new_alert else "changed_signal_already_alerted",
        "signal": signal,
        "low_confidence": low_confidence,
    }
