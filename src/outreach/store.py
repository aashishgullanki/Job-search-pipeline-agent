"""Dedup layer for the Outreach Draft stage: only process Track B
(LinkedIn-sourced) postings that already have a successfully tailored
resume (`tailored.status = 'tailored'`) and haven't gone through this
stage yet (`outreach_status IS NULL`). Mirrors the shape of
src/filter/store.py and src/score/store.py.
"""

import json
import sqlite3


def get_track_b_postings_needing_outreach(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """`limit` caps how many postings are processed this run, for capped
    dev/test runs against a small sample -- each is a paid Apify call, and
    a contact found also means a paid Haiku call.
    """
    query = """
        SELECT p.id, p.company, p.title, p.url, p.location
        FROM postings p
        JOIN tailored t ON t.posting_id = p.id
        WHERE p.source = 'linkedin' AND t.status = 'tailored' AND t.outreach_status IS NULL
        ORDER BY p.id
        """
    if limit is not None:
        query += " LIMIT ?"
        return conn.execute(query, (limit,)).fetchall()
    return conn.execute(query).fetchall()


def record_outreach_drafted(
    conn: sqlite3.Connection,
    posting_id: int,
    message: str,
    contact: dict,
    candidate_contacts: list[dict],
) -> None:
    """A contact was found and a message drafted -- overwrites the generic
    outreach_draft the Tailor stage's own skill already wrote (that draft
    isn't contact-specific; this one is, and is strictly more useful for a
    Track B posting).
    """
    conn.execute(
        """UPDATE tailored SET
               outreach_draft = ?,
               outreach_status = 'drafted',
               outreach_contact_name = ?,
               outreach_contact_profile_url = ?,
               outreach_source_post_url = ?,
               outreach_candidate_contacts = ?,
               outreach_drafted_at = datetime('now')
           WHERE posting_id = ?""",
        (
            message,
            contact.get("name"),
            contact.get("profile_url"),
            contact.get("post_url"),
            json.dumps(candidate_contacts),
            posting_id,
        ),
    )
    conn.commit()


def record_no_contact_found(conn: sqlite3.Connection, posting_id: int, attempts_log: list[dict]) -> None:
    """No contact found even after the widened retry -- outreach_draft is
    deliberately left untouched (the Tailor stage's generic draft is still
    better than nothing), only the dedup/status fields are set so this
    posting isn't retried on every future run.
    """
    conn.execute(
        """UPDATE tailored SET
               outreach_status = 'no_contact_found',
               outreach_candidate_contacts = ?,
               outreach_drafted_at = datetime('now')
           WHERE posting_id = ?""",
        (json.dumps(attempts_log), posting_id),
    )
    conn.commit()
