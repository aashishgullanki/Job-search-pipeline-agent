"""render_digest_html tests -- the whole point of this module is that
real markdown constructs become real HTML tags, not that the source text
just gets wrapped in <html> unchanged.
"""

from src.notify.render import render_digest_html


def test_h1_becomes_real_heading_tag():
    html = render_digest_html("# Review Dashboard")
    assert "<h1>Review Dashboard</h1>" in html
    assert "# Review Dashboard" not in html  # raw markdown syntax must not leak through


def test_h2_becomes_real_heading_tag():
    html = render_digest_html("## Ready to review -- score >= 8 (3)")
    assert "<h2>" in html
    assert "## Ready to review" not in html


def test_h3_becomes_real_heading_tag():
    html = render_digest_html("### [ ] pending_review -- [8/10] Acme — SWE (posting_id: 1)")
    assert "<h3>" in html
    # literal "[ ]" text (not a task-list checkbox) must survive untouched
    assert "[ ] pending_review" in html


def test_bold_becomes_strong_tag():
    html = render_digest_html("- **Location:** New York, NY")
    assert "<strong>Location:</strong>" in html
    assert "**Location**" not in html


def test_bullet_list_becomes_ul_li():
    html = render_digest_html("- First item\n- Second item")
    assert "<ul>" in html
    assert "<li>First item</li>" in html
    assert "<li>Second item</li>" in html


def test_nested_bullet_list_becomes_nested_ul():
    # 4-space indent, not 2 -- Python-Markdown needs a full 4 spaces to
    # recognize this as nested rather than flattening it into a sibling
    # list item (verified live before digest.py was written this way).
    md = "- **Other roles:**\n    - Role A\n    - Role B"
    html = render_digest_html(md)
    assert html.count("<ul>") == 2  # outer + nested
    assert "<li>Role A</li>" in html


def test_two_space_indent_does_not_nest_confirming_why_four_is_used():
    # Documents the real failure mode that drove digest.py's 4-space
    # sub-bullet indentation -- 2 spaces silently flattens the list
    # instead of nesting it.
    md = "- **Other roles:**\n  - Role A\n  - Role B"
    html = render_digest_html(md)
    assert html.count("<ul>") == 1


def test_link_becomes_real_anchor_tag():
    html = render_digest_html("[HRT_resume.pdf](../reviewed_output/HRT_resume.pdf)")
    assert '<a href="../reviewed_output/HRT_resume.pdf">HRT_resume.pdf</a>' in html


def test_fenced_code_block_becomes_pre_code():
    md = "```\npython3 -m src.dashboard.set_application_status 1 accepted\n```"
    html = render_digest_html(md)
    assert "<pre>" in html
    assert "<code>" in html
    assert "python3 -m src.dashboard.set_application_status" in html


def test_horizontal_rule_becomes_hr_tag():
    html = render_digest_html("Section A\n\n---\n\nSection B")
    assert "<hr" in html


def test_blockquote_becomes_blockquote_tag():
    html = render_digest_html("> Some diff snippet excerpt")
    assert "<blockquote>" in html


def test_output_is_a_full_html_document_with_email_safe_styling():
    html = render_digest_html("# Hello")
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "<meta charset=\"utf-8\">" in html


def test_full_realistic_digest_snippet_renders_without_raising():
    md = """# Review Dashboard

Generated 2026-08-23 09:00 UTC.

## Ready to review -- score >= 8 (1 posting(s), 1 companies)

```
python3 -m src.dashboard.set_application_status <posting_id> <accepted|rejected>
```

### [ ] pending_review -- [8/10] Acme — Software Engineer (posting_id: 12) ⚠ _unverified posting date_

- **Location:** New York, NY
- **Posting:** https://example.com/jobs/1
- **Resume:** [Acme_SWE_12.pdf](../reviewed_output/Acme_SWE_12.pdf)
- **Fit reasoning:** Strong match.
- **Accept/reject:** `python3 -m src.dashboard.set_application_status 12 accepted   # or: rejected`

---

## ⚠ Company-monitor alerts -- lower confidence, unverified (1)

- **Blackstone** (detected 2026-08-23) -- https://blackstone.com/careers
  > New role added: Senior SWE
"""
    html = render_digest_html(md)
    assert "<h1>" in html and "<h2>" in html and "<h3>" in html
    assert "<strong>Location:</strong>" in html
    assert "<a href=\"https://example.com/jobs/1\">" not in html  # bare URL, not a markdown link -- no anchor expected
