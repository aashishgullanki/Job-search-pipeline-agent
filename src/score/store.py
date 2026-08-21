"""Dedup layer for the Score stage: only score postings that (a) passed the
Filter stage and (b) don't already have a scores row. Mirrors
src/filter/store.py's shape.
"""

import sqlite3


def get_unscored_passing_postings(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """`limit` caps how many unscored postings are returned, for capped
    dev/test runs against a small sample instead of the full backlog (each
    call is a paid LLM request downstream). Unlimited by default.
    """
    query = """
        SELECT p.* FROM postings p
        JOIN filter_results f ON f.posting_id = p.id
        LEFT JOIN scores s ON s.posting_id = p.id
        WHERE f.passed = 1 AND s.posting_id IS NULL
        ORDER BY p.id
        """
    if limit is not None:
        query += " LIMIT ?"
        return conn.execute(query, (limit,)).fetchall()
    return conn.execute(query).fetchall()


def record_score(conn: sqlite3.Connection, posting_id: int, score: int, reasoning: str) -> None:
    conn.execute(
        "INSERT INTO scores (posting_id, score, reasoning) VALUES (?, ?, ?)",
        (posting_id, score, reasoning),
    )
    conn.commit()
