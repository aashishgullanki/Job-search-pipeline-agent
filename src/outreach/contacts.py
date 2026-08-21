"""Outreach Draft stage, contact-discovery half (architecture doc section
6): finds a real person to reach out to for a Track B (LinkedIn-sourced)
posting via Apify's LinkedIn post-search actor, reusing the exact same
`run_apify_actor()` helper already built for Track B discovery (that
function is actor-agnostic; only the input shape and the field names read
back out are specific to this actor).

Actor: curious_coder/linkedin-post-search-scraper (same publisher as the
jobs-scraper actor already in use). Unlike the jobs actor, its input is a
`urls` array -- LinkedIn post-search URLs, profile URLs, or post URLs --
not a bare keywords string, so a search here means building a LinkedIn
content-search URL ourselves. Output per post includes the author's name,
profile URL, headline, and type (`Person` vs a company page), plus the
post's own URL and text -- checked against the actor's own published
input/output schema, not guessed.
"""

import urllib.parse

from src.discovery.linkedin import run_apify_actor

POST_SEARCH_ACTOR_ID = "curious_coder~linkedin-post-search-scraper"
LINKEDIN_POST_SEARCH_BASE_URL = "https://www.linkedin.com/search/results/content/"

MAX_CONTACTS_PER_COMPANY = 3


def _default_query(company: str) -> str:
    return f'"hiring" AND "software engineering" AND "new york" AND "{company}"'


def _widened_query(company: str) -> str:
    # Drop the location phrase first, on a zero-result retry -- a post can
    # be genuinely about hiring in NYC without literally containing "new
    # york" (e.g. "NYC", or nothing locational at all if it's implied by
    # the poster's or company's own profile), so this is the single
    # highest-value thing to relax before giving up.
    return f'"hiring" AND "software engineering" AND "{company}"'


def build_post_search_url(query: str) -> str:
    return f"{LINKEDIN_POST_SEARCH_BASE_URL}?keywords={urllib.parse.quote(query)}"


def search_linkedin_posts(token: str, urls: list[str], limit: int, timeout: int = 180) -> list[dict]:
    run_input = {"urls": urls, "limitPerSource": limit}
    return run_apify_actor(POST_SEARCH_ACTOR_ID, token, run_input, timeout=timeout)


def _extract_contacts(posts: list[dict], limit: int = MAX_CONTACTS_PER_COMPANY) -> list[dict]:
    """Dedup by author profile URL (one contact per person, even if they
    posted more than once), skip company-page-authored posts (authorType
    != "Person" -- a company's own post isn't a person to reach out to),
    cap at `limit`.
    """
    contacts = []
    seen_profile_urls = set()
    for post in posts:
        if post.get("authorType") != "Person":
            continue
        profile_url = post.get("authorProfileUrl")
        if not profile_url or profile_url in seen_profile_urls:
            continue
        seen_profile_urls.add(profile_url)
        contacts.append(
            {
                "name": post.get("authorFullName"),
                "profile_url": profile_url,
                "headline": post.get("authorHeadline"),
                "post_url": post.get("url"),
                "post_text": post.get("text"),
                "posted_at": post.get("postedAtISO"),
            }
        )
        if len(contacts) >= limit:
            break
    return contacts


def find_contacts_for_company(
    token: str, company: str, limit: int = MAX_CONTACTS_PER_COMPANY, timeout: int = 180
) -> tuple[list[dict], list[dict]]:
    """Search LinkedIn posts for a hiring signal at `company`, widening
    once on a zero-*raw*-result search before giving up -- same "retry,
    don't just report nothing found" principle as Track A/B/C's existing
    widening logic, but a single step here per explicit direction (not a
    whole ladder). The retry triggers on the raw post count being zero,
    not on zero *valid* contacts after Person/dedup filtering -- a search
    that found real posts but no extractable contact isn't a "the query
    was too narrow" situation the same way an empty search is.

    Returns (contacts, attempts_log) -- contacts is empty if both the
    original and widened queries came back with zero posts.
    """
    attempts_log: list[dict] = []

    for attempt_num, query in enumerate([_default_query(company), _widened_query(company)], start=1):
        url = build_post_search_url(query)
        posts = search_linkedin_posts(token, [url], limit, timeout=timeout)
        attempts_log.append({"attempt": attempt_num, "query": query, "results": len(posts)})
        if posts:
            return _extract_contacts(posts, limit=limit), attempts_log

    return [], attempts_log
