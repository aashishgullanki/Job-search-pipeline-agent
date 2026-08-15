"""Dedup layer for the filter stage: never re-evaluate a posting that
already has a filter_results row, whether it passed or was excluded.
Mirrors the shape of src/discovery/store.py (dedup by a stable key already
in the schema -- there, url_hash; here, postings.id via the FK).
"""

import sqlite3


def get_unfiltered_postings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Postings with no filter_results row yet."""
    return conn.execute(
        """
        SELECT p.* FROM postings p
        LEFT JOIN filter_results f ON f.posting_id = p.id
        WHERE f.posting_id IS NULL
        """
    ).fetchall()


def record_filter_result(
    conn: sqlite3.Connection, posting_id: int, passed: bool, excluded_by: str | None
) -> None:
    conn.execute(
        "INSERT INTO filter_results (posting_id, passed, excluded_by) VALUES (?, ?, ?)",
        (posting_id, int(passed), excluded_by),
    )
    conn.commit()
