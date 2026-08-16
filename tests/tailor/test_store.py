import sqlite3

import pytest

from src.db.connection import init_db
from src.tailor.store import (
    get_review_list_postings,
    get_untailored_high_scoring_postings,
    record_tailored_failure,
    record_tailored_success,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _insert_scored_posting(conn, url_hash, score, title="Software Engineer", company="X", reasoning="Good fit"):
    conn.execute(
        """INSERT INTO postings (source, company, title, url, url_hash, location, raw_json)
           VALUES ('linkedin', ?, ?, ?, ?, 'New York, NY', '{}')""",
        (company, title, f"https://example.com/{url_hash}", url_hash),
    )
    conn.commit()
    posting_id = conn.execute("SELECT id FROM postings WHERE url_hash = ?", (url_hash,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO scores (posting_id, score, reasoning) VALUES (?, ?, ?)", (posting_id, score, reasoning)
    )
    conn.commit()
    return posting_id


# --- get_untailored_high_scoring_postings ---


def test_only_returns_postings_at_or_above_threshold(conn):
    _insert_scored_posting(conn, "a", score=8)
    _insert_scored_posting(conn, "b", score=7)

    result = get_untailored_high_scoring_postings(conn, threshold=8)

    assert len(result) == 1
    assert result[0]["url_hash"] == "a"


def test_excludes_postings_already_in_tailored_table(conn):
    id_a = _insert_scored_posting(conn, "a", score=9)
    _insert_scored_posting(conn, "b", score=9)
    record_tailored_success(conn, id_a, "/path/a.pdf", "/path/a.tex", "outreach", ["change 1"], attempts=1)

    result = get_untailored_high_scoring_postings(conn, threshold=8)

    assert len(result) == 1
    assert result[0]["url_hash"] == "b"


def test_a_failed_tailoring_attempt_is_also_not_retried_on_rerun(conn):
    id_a = _insert_scored_posting(conn, "a", score=9)
    record_tailored_failure(conn, id_a, reason="overflow after 3 attempts", attempts=3)

    result = get_untailored_high_scoring_postings(conn, threshold=8)

    assert len(result) == 0


def test_custom_threshold_is_respected(conn):
    _insert_scored_posting(conn, "a", score=6)
    _insert_scored_posting(conn, "b", score=5)

    result = get_untailored_high_scoring_postings(conn, threshold=6)

    assert len(result) == 1
    assert result[0]["url_hash"] == "a"


# --- record_tailored_success / record_tailored_failure ---


def test_record_tailored_success_stores_all_fields(conn):
    id_a = _insert_scored_posting(conn, "a", score=9)

    record_tailored_success(
        conn, id_a, "/out/a.pdf", "/out/a.tex", "Hi there,", ["Reordered bullet X", "Added skill Y"], attempts=2
    )

    row = conn.execute("SELECT * FROM tailored WHERE posting_id = ?", (id_a,)).fetchone()
    assert row["status"] == "tailored"
    assert row["resume_pdf_path"] == "/out/a.pdf"
    assert row["resume_tex_path"] == "/out/a.tex"
    assert row["outreach_draft"] == "Hi there,"
    assert row["tailoring_summary"] == "Reordered bullet X\nAdded skill Y"
    assert row["attempts"] == 2
    assert row["failure_reason"] is None


def test_record_tailored_failure_stores_reason_and_no_pdf(conn):
    id_a = _insert_scored_posting(conn, "a", score=9)

    record_tailored_failure(conn, id_a, "still 2 pages after 3 attempts", attempts=3)

    row = conn.execute("SELECT * FROM tailored WHERE posting_id = ?", (id_a,)).fetchone()
    assert row["status"] == "failed"
    assert row["resume_pdf_path"] is None
    assert row["failure_reason"] == "still 2 pages after 3 attempts"
    assert row["attempts"] == 3


# --- get_review_list_postings ---


def test_review_list_only_includes_postings_below_threshold(conn):
    _insert_scored_posting(conn, "a", score=8, title="AtThreshold")
    _insert_scored_posting(conn, "b", score=7, title="BelowThreshold")
    _insert_scored_posting(conn, "c", score=2, title="WayBelowThreshold")

    result = get_review_list_postings(conn, threshold=8)

    titles = [r["title"] for r in result]
    assert "AtThreshold" not in titles  # >= threshold -> excluded, goes through Tailor instead
    assert "BelowThreshold" in titles
    assert "WayBelowThreshold" in titles
    assert len(result) == 2


def test_review_list_sorted_by_score_descending(conn):
    _insert_scored_posting(conn, "a", score=3, title="Low")
    _insert_scored_posting(conn, "b", score=7, title="High")
    _insert_scored_posting(conn, "c", score=5, title="Mid")

    result = get_review_list_postings(conn, threshold=8)

    assert [r["title"] for r in result] == ["High", "Mid", "Low"]


def test_review_list_includes_expected_fields(conn):
    _insert_scored_posting(conn, "a", score=6, title="SWE", company="Acme", reasoning="Decent match")

    result = get_review_list_postings(conn, threshold=8)

    row = result[0]
    assert row["company"] == "Acme"
    assert row["title"] == "SWE"
    assert row["score"] == 6
    assert row["reasoning"] == "Decent match"
    assert row["url"] == "https://example.com/a"
