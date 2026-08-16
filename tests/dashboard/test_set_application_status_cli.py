from src.dashboard.run_digest import run as run_digest
from src.dashboard.set_application_status import run as set_status
from src.db.connection import get_connection, init_db


def _seed(db_path):
    conn = get_connection(db_path)
    init_db(conn)
    conn.execute(
        """INSERT INTO postings (source, company, title, url, url_hash, location, raw_json)
           VALUES ('linkedin', 'Acme', 'SWE', 'https://example.com/a', 'a', 'New York, NY', '{}')"""
    )
    conn.commit()
    pid = conn.execute("SELECT id FROM postings").fetchone()[0]
    conn.execute("INSERT INTO scores (posting_id, score, reasoning) VALUES (?, 9, 'Great fit')", (pid,))
    conn.execute(
        """INSERT INTO tailored (posting_id, status, resume_pdf_path, resume_tex_path, outreach_draft,
           tailoring_summary, attempts) VALUES (?, 'tailored', NULL, NULL, 'Hi,', 'change', 1)""",
        (pid,),
    )
    conn.commit()
    conn.close()
    return pid


def test_cli_updates_status_after_digest_creates_row(tmp_path):
    db_path = tmp_path / "test.db"
    out_path = tmp_path / "dashboard.md"
    pid = _seed(db_path)

    run_digest(db_path=db_path, output_path=out_path)
    exit_code = set_status(pid, "accepted", db_path=db_path)

    assert exit_code == 0
    conn = get_connection(db_path)
    row = conn.execute("SELECT status FROM applications WHERE posting_id = ?", (pid,)).fetchone()
    assert row["status"] == "accepted"
    conn.close()


def test_cli_returns_error_code_for_unknown_posting(tmp_path):
    db_path = tmp_path / "test.db"
    exit_code = set_status(9999, "accepted", db_path=db_path)

    assert exit_code == 1
