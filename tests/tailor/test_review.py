import sqlite3

import pytest

from src.db.connection import init_db
from src.tailor.review import build_review_list_markdown


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


def test_empty_review_list_says_so_explicitly(conn):
    markdown = build_review_list_markdown(conn)
    assert "Nothing in this range right now." in markdown


def test_review_list_includes_company_title_score_reasoning_link(conn):
    _insert_scored_posting(conn, "a", score=6, title="Backend Engineer", company="Acme", reasoning="Solid match")

    markdown = build_review_list_markdown(conn)

    assert "Acme" in markdown
    assert "Backend Engineer" in markdown
    assert "6/10" in markdown
    assert "Solid match" in markdown
    assert "https://example.com/a" in markdown


def test_review_list_excludes_postings_at_or_above_threshold(conn):
    _insert_scored_posting(conn, "a", score=8, title="ShouldBeTailoredNotReviewed")
    _insert_scored_posting(conn, "b", score=5, title="ShouldAppearInReview")

    markdown = build_review_list_markdown(conn)

    assert "ShouldBeTailoredNotReviewed" not in markdown
    assert "ShouldAppearInReview" in markdown


def test_review_list_sorted_score_descending():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _insert_scored_posting(conn, "a", score=3, title="Low")
    _insert_scored_posting(conn, "b", score=7, title="High")
    _insert_scored_posting(conn, "c", score=5, title="Mid")

    markdown = build_review_list_markdown(conn)

    assert markdown.index("High") < markdown.index("Mid") < markdown.index("Low")


def test_review_list_respects_custom_threshold(conn):
    _insert_scored_posting(conn, "a", score=6, title="IncludedAtLowerThreshold")

    markdown = build_review_list_markdown(conn, threshold=5)

    assert "IncludedAtLowerThreshold" not in markdown  # 6 >= 5, goes to tailor instead
