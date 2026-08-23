import sqlite3

import pytest

from src.db.connection import init_db
from src.dashboard.store import (
    ensure_application_rows,
    ensure_reviewed_output_copies,
    get_tailored_postings,
    get_unreviewed_company_monitor_alerts,
    set_application_status,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _insert_posting(conn, url_hash, company="Acme", title="Software Engineer", location="New York, NY"):
    conn.execute(
        """INSERT INTO postings (source, company, title, url, url_hash, location, raw_json)
           VALUES ('linkedin', ?, ?, ?, ?, ?, '{}')""",
        (company, title, f"https://example.com/{url_hash}", url_hash, location),
    )
    conn.commit()
    return conn.execute("SELECT id FROM postings WHERE url_hash = ?", (url_hash,)).fetchone()["id"]


def _score(conn, posting_id, score, reasoning="Strong fit"):
    conn.execute("INSERT INTO scores (posting_id, score, reasoning) VALUES (?, ?, ?)", (posting_id, score, reasoning))
    conn.commit()


def _tailor_success(conn, posting_id, pdf_path="/out/a.pdf", tex_path="/out/a.tex", summary="Reordered bullet 1"):
    conn.execute(
        """INSERT INTO tailored (posting_id, status, resume_pdf_path, resume_tex_path, outreach_draft,
           tailoring_summary, attempts) VALUES (?, 'tailored', ?, ?, 'Hi,', ?, 1)""",
        (posting_id, pdf_path, tex_path, summary),
    )
    conn.commit()


def _tailor_failure(conn, posting_id):
    conn.execute(
        "INSERT INTO tailored (posting_id, status, failure_reason, attempts) VALUES (?, 'failed', 'overflow', 3)",
        (posting_id,),
    )
    conn.commit()


# --- ensure_application_rows ---


def test_creates_pending_review_row_for_tailored_posting(conn):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)

    created = ensure_application_rows(conn)

    assert created == 1
    row = conn.execute("SELECT status FROM applications WHERE posting_id = ?", (pid,)).fetchone()
    assert row["status"] == "pending_review"


def test_does_not_duplicate_existing_row(conn):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)
    ensure_application_rows(conn)

    created_second_call = ensure_application_rows(conn)

    assert created_second_call == 0
    count = conn.execute("SELECT COUNT(*) c FROM applications WHERE posting_id = ?", (pid,)).fetchone()["c"]
    assert count == 1


def test_does_not_touch_already_accepted_row(conn):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)
    ensure_application_rows(conn)
    set_application_status(conn, pid, "accepted")

    ensure_application_rows(conn)

    row = conn.execute("SELECT status FROM applications WHERE posting_id = ?", (pid,)).fetchone()
    assert row["status"] == "accepted"


def test_skips_failed_tailoring_no_row_created(conn):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_failure(conn, pid)

    created = ensure_application_rows(conn)

    assert created == 0
    assert conn.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"] == 0


# --- ensure_reviewed_output_copies ---


def test_copies_pdf_and_tex_not_already_in_dest(conn, tmp_path):
    src_dir = tmp_path / "data" / "tailored"
    src_dir.mkdir(parents=True)
    pdf = src_dir / "Acme_SWE_1.pdf"
    tex = src_dir / "Acme_SWE_1.tex"
    pdf.write_text("pdf-bytes")
    tex.write_text("tex-source")
    dest_dir = tmp_path / "reviewed_output"

    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid, pdf_path=str(pdf), tex_path=str(tex))

    copied = ensure_reviewed_output_copies(conn, dest_dir=dest_dir)

    assert copied == 2
    assert (dest_dir / "Acme_SWE_1.pdf").read_text() == "pdf-bytes"
    assert (dest_dir / "Acme_SWE_1.tex").read_text() == "tex-source"


def test_overwrites_stale_file_already_present_in_dest(conn, tmp_path):
    # A coincidentally-repeated posting_id (e.g. after a DB reset + fresh
    # repopulation) can produce the same filename for a *different*
    # tailoring run -- the fresh source must win, not the stale copy.
    src_dir = tmp_path / "data" / "tailored"
    src_dir.mkdir(parents=True)
    pdf = src_dir / "Acme_SWE_1.pdf"
    pdf.write_text("fresh-content")
    dest_dir = tmp_path / "reviewed_output"
    dest_dir.mkdir(parents=True)
    (dest_dir / "Acme_SWE_1.pdf").write_text("stale-content-from-a-prior-db")

    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid, pdf_path=str(pdf), tex_path=None)

    copied = ensure_reviewed_output_copies(conn, dest_dir=dest_dir)

    assert copied == 1
    assert (dest_dir / "Acme_SWE_1.pdf").read_text() == "fresh-content"


def test_recopying_identical_content_is_a_safe_no_op(conn, tmp_path):
    src_dir = tmp_path / "data" / "tailored"
    src_dir.mkdir(parents=True)
    pdf = src_dir / "Acme_SWE_1.pdf"
    pdf.write_text("same-content")
    dest_dir = tmp_path / "reviewed_output"

    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid, pdf_path=str(pdf), tex_path=None)

    ensure_reviewed_output_copies(conn, dest_dir=dest_dir)
    ensure_reviewed_output_copies(conn, dest_dir=dest_dir)  # second call, same DB/source

    assert (dest_dir / "Acme_SWE_1.pdf").read_text() == "same-content"


def test_handles_missing_source_gracefully(conn, tmp_path):
    dest_dir = tmp_path / "reviewed_output"
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid, pdf_path="/nonexistent/gone.pdf", tex_path=None)

    copied = ensure_reviewed_output_copies(conn, dest_dir=dest_dir)

    assert copied == 0
    assert not (dest_dir / "gone.pdf").exists()


def test_ignores_failed_tailoring_rows(conn, tmp_path):
    dest_dir = tmp_path / "reviewed_output"
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_failure(conn, pid)

    copied = ensure_reviewed_output_copies(conn, dest_dir=dest_dir)

    assert copied == 0


# --- get_tailored_postings ---


def test_only_returns_score_at_or_above_threshold(conn):
    a = _insert_posting(conn, "a")
    b = _insert_posting(conn, "b")
    _score(conn, a, 8)
    _score(conn, b, 7)
    _tailor_success(conn, a)
    _tailor_success(conn, b)

    result = get_tailored_postings(conn, threshold=8)

    assert len(result) == 1
    assert result[0]["posting_id"] == a


def test_excludes_failed_tailoring_even_if_score_qualifies(conn):
    a = _insert_posting(conn, "a")
    _score(conn, a, 9)
    _tailor_failure(conn, a)

    result = get_tailored_postings(conn, threshold=8)

    assert len(result) == 0


def test_includes_application_status_when_present(conn):
    a = _insert_posting(conn, "a")
    _score(conn, a, 9)
    _tailor_success(conn, a)
    ensure_application_rows(conn)
    set_application_status(conn, a, "accepted")

    result = get_tailored_postings(conn, threshold=8)

    assert result[0]["application_status"] == "accepted"


def test_application_status_null_when_no_row_yet(conn):
    a = _insert_posting(conn, "a")
    _score(conn, a, 9)
    _tailor_success(conn, a)

    result = get_tailored_postings(conn, threshold=8)

    assert result[0]["application_status"] is None


def test_outreach_fields_null_when_not_yet_processed(conn):
    a = _insert_posting(conn, "a")
    _score(conn, a, 9)
    _tailor_success(conn, a)

    result = get_tailored_postings(conn, threshold=8)

    assert result[0]["outreach_status"] is None
    assert result[0]["outreach_contact_name"] is None
    assert result[0]["outreach_contact_profile_url"] is None
    assert result[0]["outreach_source_post_url"] is None


def test_outreach_fields_populated_after_outreach_drafted(conn):
    a = _insert_posting(conn, "a")
    _score(conn, a, 9)
    _tailor_success(conn, a)
    conn.execute(
        """UPDATE tailored SET outreach_status = 'drafted', outreach_contact_name = 'Jordan Lee',
           outreach_contact_profile_url = 'https://www.linkedin.com/in/jordan-lee',
           outreach_source_post_url = 'https://www.linkedin.com/posts/activity-1'
           WHERE posting_id = ?""",
        (a,),
    )
    conn.commit()

    result = get_tailored_postings(conn, threshold=8)

    assert result[0]["outreach_status"] == "drafted"
    assert result[0]["outreach_contact_name"] == "Jordan Lee"
    assert result[0]["outreach_contact_profile_url"] == "https://www.linkedin.com/in/jordan-lee"
    assert result[0]["outreach_source_post_url"] == "https://www.linkedin.com/posts/activity-1"


def test_sorted_by_score_desc_then_company_title(conn):
    a = _insert_posting(conn, "a", company="Zeta")
    b = _insert_posting(conn, "b", company="Alpha")
    c = _insert_posting(conn, "c", company="Beta")
    _score(conn, a, 8)
    _score(conn, b, 9)
    _score(conn, c, 9)
    _tailor_success(conn, a)
    _tailor_success(conn, b)
    _tailor_success(conn, c)

    result = get_tailored_postings(conn, threshold=8)

    assert [r["company"] for r in result] == ["Alpha", "Beta", "Zeta"]


# --- get_unreviewed_company_monitor_alerts ---


def _insert_alert(conn, company, status="unreviewed", snippet_hash="h1"):
    conn.execute(
        """INSERT INTO company_monitor_alerts (company, careers_url, diff_snippet, snippet_hash, status)
           VALUES (?, ?, 'New role added: Senior SWE', ?, ?)""",
        (company, f"https://{company}.com/careers", snippet_hash, status),
    )
    conn.commit()


def test_only_unreviewed_alerts_returned(conn):
    _insert_alert(conn, "GoldmanSachs", status="unreviewed", snippet_hash="h1")
    _insert_alert(conn, "OtherCo", status="dismissed", snippet_hash="h2")
    _insert_alert(conn, "ThirdCo", status="confirmed", snippet_hash="h3")

    result = get_unreviewed_company_monitor_alerts(conn)

    assert len(result) == 1
    assert result[0]["company"] == "GoldmanSachs"


# --- set_application_status ---


def test_set_application_status_updates_existing_row(conn):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)
    ensure_application_rows(conn)

    updated = set_application_status(conn, pid, "accepted")

    assert updated is True
    row = conn.execute("SELECT status, reviewed_at FROM applications WHERE posting_id = ?", (pid,)).fetchone()
    assert row["status"] == "accepted"
    assert row["reviewed_at"] is not None


def test_set_application_status_returns_false_when_no_row(conn):
    updated = set_application_status(conn, 9999, "accepted")

    assert updated is False


def test_set_application_status_rejects_invalid_status(conn):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)
    ensure_application_rows(conn)

    with pytest.raises(ValueError):
        set_application_status(conn, pid, "maybe")
