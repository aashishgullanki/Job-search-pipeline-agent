"""Review Dashboard digest (architecture doc section 7): one markdown file
surfacing three things --

1. Score>=threshold, successfully-tailored postings, each with its resume
   PDF (linked into reviewed_output/), fit reasoning, Tailoring Summary,
   and current accept/reject status.
2. Score<threshold postings from the review list, for manual assessment.
3. Track C company-monitor alerts, called out as a clearly separate,
   lower-confidence feed -- these are page-diff signals, not confirmed
   postings.

No LLM calls -- pure query + render of work already done by earlier
stages. "Start simple" per the doc: a generated markdown digest, not a
live web UI yet.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.dashboard.store import (
    REVIEWED_OUTPUT_DIR,
    ensure_application_rows,
    ensure_reviewed_output_copies,
    get_tailored_postings,
    get_unreviewed_company_monitor_alerts,
)
from src.tailor.store import TAILOR_SCORE_THRESHOLD, get_review_list_postings

ACCEPT_COMMAND_TEMPLATE = "python3 -m src.dashboard.set_application_status {posting_id} accepted   # or: rejected"


def _resume_link(pdf_path: str | None, reviewed_output_dir: Path) -> str:
    if not pdf_path:
        return "_no PDF (tailoring failed for this posting)_"
    filename = Path(pdf_path).name
    if not (reviewed_output_dir / filename).exists():
        return f"_PDF not found in reviewed_output/ ({filename})_"
    rel = Path("..") / reviewed_output_dir.name / filename
    return f"[{filename}]({rel.as_posix()})"


def _status_marker(status: str | None) -> str:
    status = status or "pending_review"
    return {
        "accepted": "[x] accepted",
        "submitted": "[x] submitted",
        "rejected": "[rejected]",
    }.get(status, "[ ] pending_review")


def build_digest_markdown(
    conn: sqlite3.Connection,
    threshold: int = TAILOR_SCORE_THRESHOLD,
    reviewed_output_dir: Path = REVIEWED_OUTPUT_DIR,
) -> str:
    ensure_application_rows(conn)
    ensure_reviewed_output_copies(conn, dest_dir=reviewed_output_dir)

    tailored = get_tailored_postings(conn, threshold)
    review_list = get_review_list_postings(conn, threshold)
    alerts = get_unreviewed_company_monitor_alerts(conn)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = ["# Review Dashboard", "", f"Generated {generated_at}.", ""]

    # --- Section 1: ready to review ---
    lines += [
        f"## Ready to review -- score >= {threshold} ({len(tailored)})",
        "",
        "Tailored resume, fit reasoning, and what was changed for each. Accept or reject with:",
        "",
        "```",
        "python3 -m src.dashboard.set_application_status <posting_id> <accepted|rejected>",
        "```",
        "",
    ]
    if not tailored:
        lines.append("Nothing here right now.")
        lines.append("")
    for row in tailored:
        lines.append(
            f"### {_status_marker(row['application_status'])} -- [{row['score']}/10] "
            f"{row['company']} — {row['title']} (posting_id: {row['posting_id']})"
        )
        lines.append("")
        lines.append(f"- **Location:** {row['location'] or 'n/a'}")
        lines.append(f"- **Posting:** {row['url']}")
        lines.append(f"- **Resume:** {_resume_link(row['resume_pdf_path'], reviewed_output_dir)}")
        lines.append(f"- **Fit reasoning:** {row['reasoning']}")
        lines.append(f"- **Accept/reject:** `{ACCEPT_COMMAND_TEMPLATE.format(posting_id=row['posting_id'])}`")
        lines.append("- **Tailoring summary:**")
        for change in (row["tailoring_summary"] or "").split("\n"):
            change = change.strip()
            if change:
                lines.append(f"  - {change}")
        lines.append("")

    # --- Section 2: manual review ---
    lines += [
        f"## For manual review -- score < {threshold} ({len(review_list)})",
        "",
        "Passed the Filter stage and were scored, but below the auto-tailor threshold -- no resume "
        "generated. Worth a manual look; nothing here is accepted/rejected automatically.",
        "",
    ]
    if not review_list:
        lines.append("Nothing in this range right now.")
        lines.append("")
    for row in review_list:
        lines.append(
            f"- **[{row['score']}/10] {row['company']} — {row['title']}** "
            f"({row['location'] or 'n/a'}) -- {row['reasoning']} ([link]({row['url']}))"
        )
    if review_list:
        lines.append("")

    # --- Section 3: Track C alerts, clearly separate ---
    lines += [
        "---",
        "",
        f"## ⚠ Company-monitor alerts -- lower confidence, unverified ({len(alerts)})",
        "",
        "Track C hash-diffing flagged a change on a careers page with no structured ATS to query. "
        "This is a page-diff signal, **not a confirmed job posting** -- no title, URL, or score exists "
        "for these. Worth a manual look at the careers page itself, not something to accept/reject here.",
        "",
    ]
    if not alerts:
        lines.append("Nothing flagged right now.")
        lines.append("")
    for alert in alerts:
        lines.append(f"- **{alert['company']}** (detected {alert['detected_at']}) -- {alert['careers_url']}")
        snippet = alert["diff_snippet"].strip().replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:300] + "…"
        lines.append(f"  > {snippet}")

    return "\n".join(lines).rstrip() + "\n"
