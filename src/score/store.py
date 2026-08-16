"""Dedup layer for the Score stage: only score postings that (a) passed the
Filter stage and (b) don't already have a scores row. Mirrors
src/filter/store.py's shape.
"""

import sqlite3


def get_unscored_passing_postings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.* FROM postings p
        JOIN filter_results f ON f.posting_id = p.id
        LEFT JOIN scores s ON s.posting_id = p.id
        WHERE f.passed = 1 AND s.posting_id IS NULL
        """
    ).fetchall()


def record_score(conn: sqlite3.Connection, posting_id: int, score: int, reasoning: str) -> None:
    conn.execute(
        "INSERT INTO scores (posting_id, score, reasoning) VALUES (?, ?, ?)",
        (posting_id, score, reasoning),
    )
    conn.commit()
