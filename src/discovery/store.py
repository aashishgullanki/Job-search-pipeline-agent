"""Dedup-and-insert normalized postings into the `postings` table.

Dedup key is `url_hash` (sha256 of the job URL) -- a posting already in the
DB is never re-inserted or re-scored, matching the "never re-process a seen
posting" rule from the architecture doc's Filter stage. This is idempotent:
running the same fetch twice inserts the postings once and skips the second
time.
"""

import sqlite3

INSERT_SQL = """
INSERT INTO postings (source, company, title, url, url_hash, location, posted_at, raw_json)
VALUES (:source, :company, :title, :url, :url_hash, :location, :posted_at, :raw_json)
"""


def insert_new_postings(conn: sqlite3.Connection, postings: list[dict]) -> tuple[int, int]:
    """Insert postings not already present by url_hash.

    Returns (new_count, skipped_count).
    """
    new_count = 0
    skipped_count = 0
    cur = conn.cursor()
    for posting in postings:
        cur.execute("SELECT 1 FROM postings WHERE url_hash = ?", (posting["url_hash"],))
        if cur.fetchone() is not None:
            skipped_count += 1
            continue
        cur.execute(INSERT_SQL, posting)
        new_count += 1
    conn.commit()
    return new_count, skipped_count
