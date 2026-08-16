"""Dedup layer for the Tailor stage: only tailor postings that scored at or
above TAILOR_SCORE_THRESHOLD and don't already have a `tailored` row
(success or failure -- a posting that already failed 3 compile attempts
isn't retried forever on every rerun either). Mirrors filter/store.py and
score/store.py's shape.
"""

import sqlite3

TAILOR_SCORE_THRESHOLD = 8


def get_untailored_high_scoring_postings(
    conn: sqlite3.Connection, threshold: int = TAILOR_SCORE_THRESHOLD
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*, s.score, s.reasoning FROM postings p
        JOIN scores s ON s.posting_id = p.id
        LEFT JOIN tailored t ON t.posting_id = p.id
        WHERE s.score >= ? AND t.posting_id IS NULL
        """,
        (threshold,),
    ).fetchall()


def record_tailored_success(
    conn: sqlite3.Connection,
    posting_id: int,
    pdf_path: str,
    tex_path: str,
    outreach_draft: str,
    tailoring_summary: list[str],
    attempts: int,
) -> None:
    conn.execute(
        """INSERT INTO tailored
           (posting_id, status, resume_pdf_path, resume_tex_path, outreach_draft, tailoring_summary, attempts)
           VALUES (?, 'tailored', ?, ?, ?, ?, ?)""",
        (posting_id, pdf_path, tex_path, outreach_draft, "\n".join(tailoring_summary), attempts),
    )
    conn.commit()


def record_tailored_failure(conn: sqlite3.Connection, posting_id: int, reason: str, attempts: int) -> None:
    conn.execute(
        """INSERT INTO tailored (posting_id, status, failure_reason, attempts)
           VALUES (?, 'failed', ?, ?)""",
        (posting_id, reason, attempts),
    )
    conn.commit()


def get_review_list_postings(conn: sqlite3.Connection, threshold: int = TAILOR_SCORE_THRESHOLD) -> list[sqlite3.Row]:
    """Filter-passed, scored, but below the tailor threshold -- these never
    get an automatic resume, but the score/reasoning still needs to be
    reviewable so a human can decide manually.
    """
    return conn.execute(
        """
        SELECT p.company, p.title, p.url, p.location, s.score, s.reasoning
        FROM postings p
        JOIN scores s ON s.posting_id = p.id
        WHERE s.score < ?
        ORDER BY s.score DESC, p.company, p.title
        """,
        (threshold,),
    ).fetchall()
