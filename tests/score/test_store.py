import sqlite3

import pytest

from src.db.connection import init_db
from src.score.store import get_unscored_passing_postings, record_score


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _insert_posting(conn, url_hash, passed=True, excluded_by=None):
    conn.execute(
        """INSERT INTO postings (source, company, title, url, url_hash, location, raw_json)
           VALUES ('linkedin', 'X', 'Software Engineer', ?, ?, 'New York, NY', '{}')""",
        (f"https://example.com/{url_hash}", url_hash),
    )
    conn.commit()
    posting_id = conn.execute("SELECT id FROM postings WHERE url_hash = ?", (url_hash,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO filter_results (posting_id, passed, excluded_by) VALUES (?, ?, ?)",
        (posting_id, int(passed), excluded_by),
    )
    conn.commit()
    return posting_id


def test_only_returns_postings_that_passed_the_filter(conn):
    _insert_posting(conn, "a", passed=True)
    _insert_posting(conn, "b", passed=False, excluded_by="location")

    unscored = get_unscored_passing_postings(conn)

    assert len(unscored) == 1
    assert unscored[0]["url_hash"] == "a"


def test_excludes_postings_that_are_already_scored(conn):
    id_a = _insert_posting(conn, "a", passed=True)
    _insert_posting(conn, "b", passed=True)
    record_score(conn, id_a, score=7, reasoning="Good fit.")

    unscored = get_unscored_passing_postings(conn)

    assert len(unscored) == 1
    assert unscored[0]["url_hash"] == "b"


def test_record_score_stores_score_and_reasoning(conn):
    id_a = _insert_posting(conn, "a", passed=True)

    record_score(conn, id_a, score=9, reasoning="Excellent match on stack and seniority.")

    row = conn.execute("SELECT * FROM scores WHERE posting_id = ?", (id_a,)).fetchone()
    assert row["score"] == 9
    assert row["reasoning"] == "Excellent match on stack and seniority."


def test_never_rescores_a_posting_on_rerun(conn):
    id_a = _insert_posting(conn, "a", passed=True)
    record_score(conn, id_a, score=5, reasoning="First pass.")

    unscored = get_unscored_passing_postings(conn)

    assert len(unscored) == 0
