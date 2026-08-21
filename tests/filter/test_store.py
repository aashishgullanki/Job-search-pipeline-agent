import json
import sqlite3

import pytest

from src.db.connection import init_db
from src.filter.store import get_unfiltered_postings, record_filter_result


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _insert_posting(conn, url_hash, title="Software Engineer", location="New York, NY"):
    conn.execute(
        """INSERT INTO postings (source, company, title, url, url_hash, location, raw_json)
           VALUES ('linkedin', 'X', ?, ?, ?, ?, '{}')""",
        (title, f"https://example.com/{url_hash}", url_hash, location),
    )
    conn.commit()
    return conn.execute("SELECT id FROM postings WHERE url_hash = ?", (url_hash,)).fetchone()["id"]


def test_get_unfiltered_postings_returns_all_postings_with_no_filter_result(conn):
    _insert_posting(conn, "a")
    _insert_posting(conn, "b")

    unfiltered = get_unfiltered_postings(conn)

    assert len(unfiltered) == 2


def test_get_unfiltered_postings_excludes_already_filtered_postings(conn):
    id_a = _insert_posting(conn, "a")
    _insert_posting(conn, "b")
    record_filter_result(conn, id_a, passed=True, excluded_by=None)

    unfiltered = get_unfiltered_postings(conn)

    assert len(unfiltered) == 1
    assert unfiltered[0]["url_hash"] == "b"


def test_excluded_postings_are_also_skipped_on_rerun_not_just_passed_ones(conn):
    # "never re-process a posting that's already been filtered" applies
    # regardless of the outcome -- an excluded posting must not come back
    # up for re-evaluation either.
    id_a = _insert_posting(conn, "a")
    record_filter_result(conn, id_a, passed=False, excluded_by="location")

    unfiltered = get_unfiltered_postings(conn)

    assert len(unfiltered) == 0


def test_record_filter_result_stores_passed_and_reason(conn):
    id_a = _insert_posting(conn, "a")

    record_filter_result(conn, id_a, passed=False, excluded_by="title_seniority")

    row = conn.execute("SELECT * FROM filter_results WHERE posting_id = ?", (id_a,)).fetchone()
    assert row["passed"] == 0
    assert row["excluded_by"] == "title_seniority"
    assert row["low_confidence_age"] == 0  # default when not specified


def test_record_filter_result_stores_low_confidence_age_flag(conn):
    id_a = _insert_posting(conn, "a")

    record_filter_result(conn, id_a, passed=True, excluded_by=None, low_confidence_age=True)

    row = conn.execute("SELECT * FROM filter_results WHERE posting_id = ?", (id_a,)).fetchone()
    assert row["passed"] == 1
    assert row["low_confidence_age"] == 1
