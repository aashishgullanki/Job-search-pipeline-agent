"""Dashboard stage queries (architecture doc section 7). No new schema --
this stage is pure read/aggregate over what Filter/Score/Tailor/Track C
already computed, plus two small pieces of bookkeeping:

- `applications` already exists with a `status` column (pending_review |
  accepted | submitted | rejected) -- that's the accept mechanism
  Form-Fill will eventually read from. `ensure_application_rows` just makes
  sure every successfully-tailored posting has a row to update.
- The digest links resumes into reviewed_output/, the durable
  (gitignored, never-swept) copy directory established after the earlier
  data/tailored/ cleanup incident. `ensure_reviewed_output_copies` makes
  that link target actually exist instead of depending on a manual copy
  step that's already bitten this project once.
"""

import shutil
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEWED_OUTPUT_DIR = REPO_ROOT / "reviewed_output"

VALID_APPLICATION_STATUSES = {"pending_review", "accepted", "submitted", "rejected"}


def ensure_application_rows(conn: sqlite3.Connection) -> int:
    """Give every successfully-tailored posting an `applications` row if it
    doesn't have one yet, defaulting to 'pending_review'. Never touches a
    row that already exists -- a posting already accepted/rejected/
    submitted stays exactly as the human left it. Returns count inserted.
    """
    cur = conn.execute(
        """
        INSERT INTO applications (posting_id, status)
        SELECT t.posting_id, 'pending_review'
        FROM tailored t
        LEFT JOIN applications a ON a.posting_id = t.posting_id
        WHERE t.status = 'tailored' AND a.posting_id IS NULL
        """
    )
    conn.commit()
    return cur.rowcount


def ensure_reviewed_output_copies(conn: sqlite3.Connection, dest_dir: Path = REVIEWED_OUTPUT_DIR) -> int:
    """Copy every tailored PDF/tex from its data/tailored/ source into
    reviewed_output/, the durable directory the digest actually links to.
    Always overwrites rather than skipping when the destination filename
    already exists -- filenames are `{company}_{title}_{posting_id}.pdf`,
    and posting_id is just a per-DB autoincrement, not a stable content
    key. After a DB reset + fresh repopulation, a coincidentally-repeated
    posting_id can produce the exact same filename for a *different*
    tailoring run; skip-if-exists would silently keep the old file under
    a fresh posting's link instead of syncing it to what the `tailored`
    table (and the Tailoring Summary text next to it) actually says now.
    Within one DB's lifetime this is a safe no-op re-copy, since a given
    posting_id is only ever tailored once (dedup in tailor/store.py).
    Best-effort: if the source was already swept from data/tailored/
    before this ran, it's simply not copyable; the digest handles a
    missing file gracefully rather than linking to nothing.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT resume_pdf_path, resume_tex_path FROM tailored WHERE status = 'tailored'"
    ).fetchall()
    copied = 0
    for row in rows:
        for src in (row["resume_pdf_path"], row["resume_tex_path"]):
            if not src:
                continue
            src_path = Path(src)
            if not src_path.exists():
                continue
            dest_path = dest_dir / src_path.name
            if src_path.resolve() == dest_path.resolve():
                continue  # source IS the destination (e.g. tests pointing both at the same dir)
            shutil.copy(src_path, dest_path)
            copied += 1
    return copied


def get_tailored_postings(conn: sqlite3.Connection, threshold: int) -> list[sqlite3.Row]:
    """Score>=threshold postings with a successful tailoring -- everything
    the "ready to review" section needs in one row: score, reasoning, the
    tailored resume paths, the tailoring summary, and current accept
    status (NULL if ensure_application_rows hasn't run yet this call).
    """
    return conn.execute(
        """
        SELECT p.id AS posting_id, p.company, p.title, p.url, p.location,
               s.score, s.reasoning,
               t.resume_pdf_path, t.resume_tex_path, t.outreach_draft, t.tailoring_summary,
               a.status AS application_status
        FROM postings p
        JOIN scores s ON s.posting_id = p.id
        JOIN tailored t ON t.posting_id = p.id AND t.status = 'tailored'
        LEFT JOIN applications a ON a.posting_id = p.id
        WHERE s.score >= ?
        ORDER BY s.score DESC, p.company, p.title
        """,
        (threshold,),
    ).fetchall()


def get_unreviewed_company_monitor_alerts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Track C alerts still needing a human look. Deliberately narrower
    than "all alerts ever" -- once dismissed/confirmed, an alert has
    already been reviewed and doesn't need to keep showing up in a fresh
    digest.
    """
    return conn.execute(
        """
        SELECT company, careers_url, diff_snippet, status, detected_at
        FROM company_monitor_alerts
        WHERE status = 'unreviewed'
        ORDER BY detected_at DESC
        """
    ).fetchall()


def set_application_status(conn: sqlite3.Connection, posting_id: int, status: str) -> bool:
    """The accept mechanism: flip a posting's applications.status by hand.
    Returns False (no-op) if there's no applications row yet for this
    posting -- generate the digest first, which creates one for every
    tailored posting.
    """
    if status not in VALID_APPLICATION_STATUSES:
        raise ValueError(f"invalid status {status!r}, must be one of {sorted(VALID_APPLICATION_STATUSES)}")
    cur = conn.execute(
        "UPDATE applications SET status = ?, reviewed_at = datetime('now') WHERE posting_id = ?",
        (status, posting_id),
    )
    conn.commit()
    return cur.rowcount > 0
