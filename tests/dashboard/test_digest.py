import sqlite3

import pytest

from src.db.connection import init_db
from src.dashboard.digest import build_digest_markdown
from src.dashboard.store import ensure_application_rows, set_application_status


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


def _tailor_success(conn, posting_id, pdf_path=None, summary="Reordered bullet 1\nUpdated Technical Skills"):
    conn.execute(
        """INSERT INTO tailored (posting_id, status, resume_pdf_path, resume_tex_path, outreach_draft,
           tailoring_summary, attempts) VALUES (?, 'tailored', ?, ?, 'Hi,', ?, 1)""",
        (posting_id, pdf_path, pdf_path.replace(".pdf", ".tex") if pdf_path else None, summary),
    )
    conn.commit()


def _insert_alert(conn, company, status="unreviewed"):
    conn.execute(
        """INSERT INTO company_monitor_alerts (company, careers_url, diff_snippet, snippet_hash, status)
           VALUES (?, ?, 'New role added: Senior SWE', 'h1', ?)""",
        (company, f"https://{company}.com/careers", status),
    )
    conn.commit()


def test_all_three_section_headers_present(conn, tmp_path):
    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "## Ready to review" in md
    assert "## For manual review" in md
    assert "## ⚠ Company-monitor alerts" in md


def test_empty_state_messages_when_no_data(conn, tmp_path):
    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "Nothing here right now." in md
    assert "Nothing in this range right now." in md
    assert "Nothing flagged right now." in md


def test_section_1_includes_pdf_link_and_reasoning(conn, tmp_path):
    src_dir = tmp_path / "data" / "tailored"
    src_dir.mkdir(parents=True)
    pdf = src_dir / "Acme_SWE_1.pdf"
    pdf.write_text("pdf-bytes")
    reviewed_output_dir = tmp_path / "reviewed_output"
    pid = _insert_posting(conn, "a", company="Acme", title="SWE")
    _score(conn, pid, 9, reasoning="Great match on backend experience")
    _tailor_success(conn, pid, pdf_path=str(pdf))

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=reviewed_output_dir)

    assert "Acme" in md
    assert "SWE" in md
    assert "[Acme_SWE_1.pdf](../" in md
    assert "Great match on backend experience" in md


def test_section_1_does_not_render_tailoring_summary(conn, tmp_path):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid, summary="Reordered bullet 1\nUpdated Technical Skills")

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "Tailoring summary" not in md
    assert "Reordered bullet 1" not in md
    assert "Updated Technical Skills" not in md


def test_section_1_groups_multiple_roles_at_same_company_under_one_heading(conn, tmp_path):
    a = _insert_posting(conn, "a", company="Point72", title="Software Engineer")
    b = _insert_posting(conn, "b", company="Point72", title="Machine Learning Engineer")
    c = _insert_posting(conn, "c", company="Point72", title="Quant Developer")
    _score(conn, a, 9)
    _score(conn, b, 8)
    _score(conn, c, 8)
    _tailor_success(conn, a)
    _tailor_success(conn, b)
    _tailor_success(conn, c)

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    # Only one H3 heading for Point72 -- the highest-scoring role leads it.
    assert md.count("### ") == 1
    assert "Point72 — Software Engineer" in md
    assert "Other tailored roles at Point72:" in md
    assert "Machine Learning Engineer" in md
    assert "Quant Developer" in md


def test_section_1_other_roles_include_posting_id_and_resume_link(conn, tmp_path):
    src_dir = tmp_path / "data" / "tailored"
    src_dir.mkdir(parents=True)
    reviewed_output_dir = tmp_path / "reviewed_output"
    pdf_a = src_dir / "Jane_Street_SWE_1.pdf"
    pdf_b = src_dir / "Jane_Street_MLE_2.pdf"
    pdf_a.write_text("a")
    pdf_b.write_text("b")
    a = _insert_posting(conn, "a", company="Jane Street", title="SWE")
    b = _insert_posting(conn, "b", company="Jane Street", title="MLE")
    _score(conn, a, 9)
    _score(conn, b, 8)
    _tailor_success(conn, a, pdf_path=str(pdf_a))
    _tailor_success(conn, b, pdf_path=str(pdf_b))

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=reviewed_output_dir)

    other_roles_block = md.split("Other tailored roles at Jane Street:")[1].split("###")[0]
    assert f"posting_id: {b}" in other_roles_block
    assert "[Jane_Street_MLE_2.pdf](../" in other_roles_block


def test_section_1_single_role_company_has_no_other_roles_line(conn, tmp_path):
    pid = _insert_posting(conn, "a", company="SoloCo")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "Other tailored roles at SoloCo" not in md


def test_missing_pdf_file_shows_placeholder_not_broken_link(conn, tmp_path):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid, pdf_path=str(tmp_path / "gone.pdf"))

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "PDF not found" in md
    assert "[gone.pdf](" not in md  # no markdown link emitted for a file that isn't actually there


def test_pending_status_shows_unchecked_box(conn, tmp_path):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "[ ] pending_review" in md


def test_accepted_status_shows_checked_box(conn, tmp_path):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)
    ensure_application_rows(conn)
    set_application_status(conn, pid, "accepted")

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "[x] accepted" in md


def test_accept_command_included_with_posting_id(conn, tmp_path):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert f"set_application_status {pid} accepted" in md


def test_section_2_shows_score_below_threshold_no_pdf_needed(conn, tmp_path):
    pid = _insert_posting(conn, "a", company="LowFitCo", title="Backend Eng")
    _score(conn, pid, 5, reasoning="Some overlap but junior-heavy team")

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "LowFitCo" in md
    assert "Backend Eng" in md
    assert "Some overlap but junior-heavy team" in md


def test_section_2_groups_multiple_roles_at_same_company(conn, tmp_path):
    a = _insert_posting(conn, "a", company="HRT", title="Research Engineer")
    b = _insert_posting(conn, "b", company="HRT", title="AI Research Engineer, Inference")
    _score(conn, a, 7, reasoning="Solid but early-career for the role")
    _score(conn, b, 4, reasoning="Missing kernel-level ML systems experience")

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    manual_section = md.split("## For manual review")[1].split("## ⚠")[0]
    # Only one top-level bullet naming HRT -- the higher-scoring role leads it.
    assert manual_section.count("HRT — ") == 1
    assert "HRT — Research Engineer" in manual_section
    assert "Other scored roles at HRT:" in manual_section
    assert "AI Research Engineer, Inference" in manual_section


def test_section_2_single_role_company_has_no_other_roles_line(conn, tmp_path):
    pid = _insert_posting(conn, "a", company="SoloReviewCo")
    _score(conn, pid, 5)

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "Other scored roles at SoloReviewCo" not in md


def test_section_2_excludes_at_or_above_threshold(conn, tmp_path):
    pid = _insert_posting(conn, "a", company="HighFitCo")
    _score(conn, pid, 8)
    _tailor_success(conn, pid)

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    manual_section = md.split("## For manual review")[1].split("## ⚠")[0]
    assert "HighFitCo" not in manual_section


def test_section_3_shows_unreviewed_alerts_and_hides_dismissed(conn, tmp_path):
    _insert_alert(conn, "GoldmanSachs", status="unreviewed")
    _insert_alert(conn, "DismissedCo", status="dismissed")

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    alerts_section = md.split("## ⚠")[1]
    assert "GoldmanSachs" in alerts_section
    assert "DismissedCo" not in alerts_section


def test_section_3_labeled_lower_confidence(conn, tmp_path):
    _insert_alert(conn, "GoldmanSachs")

    md = build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    assert "not a confirmed job posting" in md.lower()


def test_generating_digest_creates_application_rows(conn, tmp_path):
    pid = _insert_posting(conn, "a")
    _score(conn, pid, 9)
    _tailor_success(conn, pid)

    build_digest_markdown(conn, threshold=8, reviewed_output_dir=tmp_path)

    row = conn.execute("SELECT status FROM applications WHERE posting_id = ?", (pid,)).fetchone()
    assert row["status"] == "pending_review"
