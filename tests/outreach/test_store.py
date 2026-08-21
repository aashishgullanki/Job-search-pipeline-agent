import json
import sqlite3

import pytest

from src.db.connection import init_db
from src.outreach.store import (
    get_track_b_postings_needing_outreach,
    record_no_contact_found,
    record_outreach_drafted,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _insert_posting(conn, url_hash, source="linkedin", company="Acme", title="Software Engineer"):
    conn.execute(
        """INSERT INTO postings (source, company, title, url, url_hash, location, raw_json)
           VALUES (?, ?, ?, ?, ?, 'New York, NY', '{}')""",
        (source, company, title, f"https://example.com/{url_hash}", url_hash),
    )
    conn.commit()
    return conn.execute("SELECT id FROM postings WHERE url_hash = ?", (url_hash,)).fetchone()["id"]


def _tailor_success(conn, posting_id, outreach_draft="Generic draft from Tailor stage."):
    conn.execute(
        """INSERT INTO tailored (posting_id, status, resume_pdf_path, resume_tex_path, outreach_draft,
           tailoring_summary, attempts) VALUES (?, 'tailored', '/out/a.pdf', '/out/a.tex', ?, 'change', 1)""",
        (posting_id, outreach_draft),
    )
    conn.commit()


def _tailor_failure(conn, posting_id):
    conn.execute(
        "INSERT INTO tailored (posting_id, status, failure_reason, attempts) VALUES (?, 'failed', 'overflow', 3)",
        (posting_id,),
    )
    conn.commit()


_CONTACT = {
    "name": "Jordan Lee",
    "profile_url": "https://www.linkedin.com/in/jordan-lee",
    "post_url": "https://www.linkedin.com/posts/activity-1",
}


# --- get_track_b_postings_needing_outreach ---


def test_only_returns_linkedin_sourced_postings(conn):
    a = _insert_posting(conn, "a", source="linkedin")
    b = _insert_posting(conn, "b", source="ats:Acme")
    _tailor_success(conn, a)
    _tailor_success(conn, b)

    result = get_track_b_postings_needing_outreach(conn)

    assert len(result) == 1
    assert result[0]["id"] == a


def test_excludes_postings_not_successfully_tailored(conn):
    a = _insert_posting(conn, "a")
    _tailor_failure(conn, a)

    result = get_track_b_postings_needing_outreach(conn)

    assert len(result) == 0


def test_excludes_untailored_postings(conn):
    _insert_posting(conn, "a")  # never tailored at all

    result = get_track_b_postings_needing_outreach(conn)

    assert len(result) == 0


def test_excludes_postings_already_processed(conn):
    a = _insert_posting(conn, "a")
    _tailor_success(conn, a)
    record_outreach_drafted(conn, a, "message", _CONTACT, [_CONTACT])

    result = get_track_b_postings_needing_outreach(conn)

    assert len(result) == 0


def test_excludes_postings_marked_no_contact_found(conn):
    a = _insert_posting(conn, "a")
    _tailor_success(conn, a)
    record_no_contact_found(conn, a, [{"attempt": 1, "query": "x", "results": 0}])

    result = get_track_b_postings_needing_outreach(conn)

    assert len(result) == 0


def test_limit_caps_results(conn):
    for h in ["a", "b", "c"]:
        pid = _insert_posting(conn, h)
        _tailor_success(conn, pid)

    result = get_track_b_postings_needing_outreach(conn, limit=2)

    assert len(result) == 2


# --- record_outreach_drafted ---


def test_record_outreach_drafted_overwrites_generic_draft_and_sets_contact_fields(conn):
    a = _insert_posting(conn, "a")
    _tailor_success(conn, a, outreach_draft="Generic draft from Tailor stage.")

    record_outreach_drafted(conn, a, "Hi Jordan, ...", _CONTACT, [_CONTACT])

    row = conn.execute("SELECT * FROM tailored WHERE posting_id = ?", (a,)).fetchone()
    assert row["outreach_draft"] == "Hi Jordan, ..."
    assert row["outreach_status"] == "drafted"
    assert row["outreach_contact_name"] == "Jordan Lee"
    assert row["outreach_contact_profile_url"] == "https://www.linkedin.com/in/jordan-lee"
    assert row["outreach_source_post_url"] == "https://www.linkedin.com/posts/activity-1"
    assert json.loads(row["outreach_candidate_contacts"]) == [_CONTACT]
    assert row["outreach_drafted_at"] is not None


# --- record_no_contact_found ---


def test_record_no_contact_found_leaves_generic_draft_untouched(conn):
    a = _insert_posting(conn, "a", )
    _tailor_success(conn, a, outreach_draft="Generic draft from Tailor stage.")

    record_no_contact_found(conn, a, [{"attempt": 1, "query": "x", "results": 0}, {"attempt": 2, "query": "y", "results": 0}])

    row = conn.execute("SELECT * FROM tailored WHERE posting_id = ?", (a,)).fetchone()
    assert row["outreach_draft"] == "Generic draft from Tailor stage."  # untouched
    assert row["outreach_status"] == "no_contact_found"
    assert row["outreach_contact_name"] is None
    assert json.loads(row["outreach_candidate_contacts"])[0]["attempt"] == 1
