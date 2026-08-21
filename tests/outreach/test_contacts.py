"""Contact-discovery tests for the Outreach Draft stage -- entirely
mocked, no network. Mirrors tests/discovery/test_linkedin.py's shape for
the widening-retry tests.
"""

import urllib.parse

import src.outreach.contacts as contacts_mod


def _post(n: int, author_type="Person", profile_url=None) -> dict:
    return {
        "urn": f"urn:{n}",
        "text": f"We're hiring software engineers in NYC! Post #{n}",
        "url": f"https://www.linkedin.com/posts/activity-{n}",
        "postedAtISO": "2026-08-10T12:00:00Z",
        "authorFullName": f"Person {n}",
        "authorProfileUrl": profile_url or f"https://www.linkedin.com/in/person-{n}",
        "authorHeadline": f"Engineering Manager at Company {n}",
        "authorType": author_type,
    }


# --- query builders ---


def test_default_query_includes_all_three_required_terms_and_company():
    query = contacts_mod._default_query("Acme")
    assert '"hiring"' in query
    assert '"software engineering"' in query
    assert '"new york"' in query
    assert '"Acme"' in query


def test_widened_query_drops_location_but_keeps_the_rest():
    query = contacts_mod._widened_query("Acme")
    assert '"new york"' not in query
    assert '"hiring"' in query
    assert '"software engineering"' in query
    assert '"Acme"' in query


def test_build_post_search_url_url_encodes_the_query():
    url = contacts_mod.build_post_search_url('"hiring" AND "Acme"')
    assert url.startswith(contacts_mod.LINKEDIN_POST_SEARCH_BASE_URL)
    decoded = urllib.parse.unquote(url.split("keywords=", 1)[1])
    assert decoded == '"hiring" AND "Acme"'


# --- search_linkedin_posts ---


def test_search_linkedin_posts_passes_urls_and_limit_to_apify(monkeypatch):
    captured = {}

    def fake_run_apify_actor(actor_id, token, run_input, timeout=180):
        captured["actor_id"] = actor_id
        captured["token"] = token
        captured["run_input"] = run_input
        return [_post(1)]

    monkeypatch.setattr(contacts_mod, "run_apify_actor", fake_run_apify_actor)

    result = contacts_mod.search_linkedin_posts("tok", ["https://example.com/search"], limit=3)

    assert result == [_post(1)]
    assert captured["actor_id"] == contacts_mod.POST_SEARCH_ACTOR_ID
    assert captured["token"] == "tok"
    assert captured["run_input"] == {"urls": ["https://example.com/search"], "limitPerSource": 3}


# --- _extract_contacts ---


def test_extract_contacts_skips_company_authored_posts():
    posts = [_post(1, author_type="Organization"), _post(2, author_type="Person")]
    contacts = contacts_mod._extract_contacts(posts)
    assert len(contacts) == 1
    assert contacts[0]["name"] == "Person 2"


def test_extract_contacts_dedups_by_profile_url():
    same_url = "https://www.linkedin.com/in/same-person"
    posts = [_post(1, profile_url=same_url), _post(2, profile_url=same_url)]
    contacts = contacts_mod._extract_contacts(posts)
    assert len(contacts) == 1


def test_extract_contacts_respects_limit():
    posts = [_post(i) for i in range(10)]
    contacts = contacts_mod._extract_contacts(posts, limit=3)
    assert len(contacts) == 3


def test_extract_contacts_includes_post_and_profile_links():
    contacts = contacts_mod._extract_contacts([_post(1)])
    assert contacts[0]["profile_url"] == "https://www.linkedin.com/in/person-1"
    assert contacts[0]["post_url"] == "https://www.linkedin.com/posts/activity-1"


# --- find_contacts_for_company (widening) ---


def test_no_widening_needed_when_first_attempt_finds_posts(monkeypatch):
    calls = []

    def fake_search(token, urls, limit, timeout=180):
        calls.append(urls)
        return [_post(1)]

    monkeypatch.setattr(contacts_mod, "search_linkedin_posts", fake_search)

    contacts, attempts = contacts_mod.find_contacts_for_company("tok", "Acme")

    assert len(contacts) == 1
    assert len(attempts) == 1
    assert len(calls) == 1


def test_widens_once_on_zero_results_then_succeeds(monkeypatch):
    results = iter([[], [_post(1)]])

    def fake_search(token, urls, limit, timeout=180):
        return next(results)

    monkeypatch.setattr(contacts_mod, "search_linkedin_posts", fake_search)

    contacts, attempts = contacts_mod.find_contacts_for_company("tok", "Acme")

    assert len(contacts) == 1
    assert len(attempts) == 2
    assert attempts[0]["results"] == 0
    assert attempts[1]["results"] == 1
    # widened query must actually differ from the first
    assert attempts[0]["query"] != attempts[1]["query"]


def test_gives_up_after_one_widen_if_still_zero_results(monkeypatch):
    def fake_search(token, urls, limit, timeout=180):
        return []

    monkeypatch.setattr(contacts_mod, "search_linkedin_posts", fake_search)

    contacts, attempts = contacts_mod.find_contacts_for_company("tok", "Acme")

    assert contacts == []
    assert len(attempts) == 2  # tried once, widened once, then stopped -- not an unbounded retry


def test_does_not_widen_when_posts_found_but_no_valid_contacts(monkeypatch):
    # Posts came back (non-zero raw results) but none pass the Person/dedup
    # filter -- this is NOT the same as a zero-result search, so no retry.
    calls = []

    def fake_search(token, urls, limit, timeout=180):
        calls.append(urls)
        return [_post(1, author_type="Organization")]

    monkeypatch.setattr(contacts_mod, "search_linkedin_posts", fake_search)

    contacts, attempts = contacts_mod.find_contacts_for_company("tok", "Acme")

    assert contacts == []
    assert len(attempts) == 1
    assert len(calls) == 1
