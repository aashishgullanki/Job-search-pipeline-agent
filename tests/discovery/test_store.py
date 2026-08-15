import sqlite3

import pytest

from src.db.connection import init_db
from src.discovery.store import insert_new_postings


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _posting(url_hash: str, title: str = "Software Engineer") -> dict:
    return {
        "source": "ats:ExampleCo",
        "company": "ExampleCo",
        "title": title,
        "url": f"https://example.com/jobs/{url_hash}",
        "url_hash": url_hash,
        "location": "New York, NY",
        "posted_at": "2026-08-01T00:00:00Z",
        "raw_json": "{}",
    }


def test_insert_new_postings_inserts_all_on_first_run(conn):
    postings = [_posting("hash1"), _posting("hash2")]

    new_count, skipped_count = insert_new_postings(conn, postings)

    assert new_count == 2
    assert skipped_count == 0
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 2


def test_insert_new_postings_is_idempotent(conn):
    postings = [_posting("hash1"), _posting("hash2")]
    insert_new_postings(conn, postings)

    new_count, skipped_count = insert_new_postings(conn, postings)

    assert new_count == 0
    assert skipped_count == 2
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 2


def test_insert_new_postings_only_inserts_the_new_subset(conn):
    insert_new_postings(conn, [_posting("hash1")])

    new_count, skipped_count = insert_new_postings(conn, [_posting("hash1"), _posting("hash2")])

    assert new_count == 1
    assert skipped_count == 1
    assert conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 2
