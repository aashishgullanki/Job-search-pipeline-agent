"""Review Dashboard digest (architecture doc section 7): one markdown file
surfacing three things --

1. Score>=threshold, successfully-tailored postings, each with its resume
   PDF (linked into reviewed_output/), fit reasoning, and current
   accept/reject status. One entry per company (its highest-scoring
   tailored role) -- other tailored roles at the same company are linked
   in a compact list underneath rather than repeating the full block.
2. Score<threshold postings from the review list, for manual assessment --
   same one-entry-per-company treatment.
3. Track C company-monitor alerts, called out as a clearly separate,
   lower-confidence feed -- these are page-diff signals, not confirmed
   postings.

No LLM calls -- pure query + render of work already done by earlier
stages. "Start simple" per the doc: a generated markdown digest, not a
live web UI yet.
"""

import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from src.common.features import is_outreach_enabled
from src.dashboard.store import (
    REVIEWED_OUTPUT_DIR,
    ensure_application_rows,
    ensure_reviewed_output_copies,
    get_tailored_postings,
    get_unreviewed_company_monitor_alerts,
)
from src.tailor.store import TAILOR_SCORE_THRESHOLD, get_review_list_postings

ACCEPT_COMMAND_TEMPLATE = "python3 -m src.dashboard.set_application_status {posting_id} accepted   # or: rejected"


def _group_by_company(rows: list[sqlite3.Row]) -> "OrderedDict[str, list[sqlite3.Row]]":
    """Both source queries already sort by score desc, so this both groups
    and preserves a sensible group order (a company's best-scoring role
    decides where its group falls) and a sensible within-group order (each
    company's own rows stay score-desc) for free -- no extra query/sort.
    """
    groups: "OrderedDict[str, list[sqlite3.Row]]" = OrderedDict()
    for row in rows:
        groups.setdefault(row["company"], []).append(row)
    return groups


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


def _status_checkbox(status: str | None) -> str:
    """Compact form of _status_marker for inline use in "other roles" lists."""
    status = status or "pending_review"
    return {"accepted": "[x]", "submitted": "[x]", "rejected": "[rej]"}.get(status, "[ ]")


def _low_confidence_age_badge(row: sqlite3.Row) -> str:
    """Compact inline caveat for a posting whose freshness couldn't be
    confirmed by the Filter stage (missing/unparseable posted_at) --
    passed rather than excluded, same "flag it, don't hide it" principle
    as Track C's low_confidence marker, so it needs the same kind of
    visible caveat here rather than looking like any other confirmed-
    fresh posting. `row["low_confidence_age"]` comes from a LEFT JOIN so
    it may be a genuine 0/1 or SQLite's NULL (falsy either way) depending
    on whether a filter_results row exists at all.
    """
    return " ⚠ _unverified posting date_" if row["low_confidence_age"] else ""


def _outreach_line(row: sqlite3.Row) -> str | None:
    """None if there's nothing worth a line -- not yet processed by the
    Outreach Draft stage (Track A/C postings, or a Track B one it hasn't
    reached yet). Caller only calls this when outreach is enabled.
    """
    status = row["outreach_status"]
    if status == "drafted":
        return (
            f"- **Outreach contact:** {row['outreach_contact_name'] or 'Unknown'} "
            f"([profile]({row['outreach_contact_profile_url']})) -- found via "
            f"[this post]({row['outreach_source_post_url']})"
        )
    if status == "no_contact_found":
        return "- **Outreach contact:** _none found (Apify LinkedIn post search, widened once, still nothing)_"
    return None


def build_digest_markdown(
    conn: sqlite3.Connection,
    threshold: int = TAILOR_SCORE_THRESHOLD,
    reviewed_output_dir: Path = REVIEWED_OUTPUT_DIR,
    outreach_enabled: bool | None = None,
) -> str:
    # outreach_enabled=None means "check the real config" -- tests inject
    # True/False directly so they don't depend on config/features.yaml's
    # actual on-disk contents.
    outreach_on = is_outreach_enabled() if outreach_enabled is None else outreach_enabled

    ensure_application_rows(conn)
    ensure_reviewed_output_copies(conn, dest_dir=reviewed_output_dir)

    tailored = get_tailored_postings(conn, threshold)
    review_list = get_review_list_postings(conn, threshold)
    alerts = get_unreviewed_company_monitor_alerts(conn)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = ["# Review Dashboard", "", f"Generated {generated_at}.", ""]

    # --- Section 1: ready to review ---
    tailored_groups = _group_by_company(tailored)
    lines += [
        f"## Ready to review -- score >= {threshold} ({len(tailored)} posting(s), {len(tailored_groups)} compan"
        f"{'y' if len(tailored_groups) == 1 else 'ies'})",
        "",
        "One entry per company, its highest-scoring tailored role -- other tailored roles at the same "
        "company are listed underneath it. Accept or reject any of them with:",
        "",
        "```",
        "python3 -m src.dashboard.set_application_status <posting_id> <accepted|rejected>",
        "```",
        "",
    ]
    if not tailored_groups:
        lines.append("Nothing here right now.")
        lines.append("")
    for company, postings in tailored_groups.items():
        lead, others = postings[0], postings[1:]
        lines.append(
            f"### {_status_marker(lead['application_status'])} -- [{lead['score']}/10] "
            f"{lead['company']} — {lead['title']} (posting_id: {lead['posting_id']})"
            f"{_low_confidence_age_badge(lead)}"
        )
        lines.append("")
        lines.append(f"- **Location:** {lead['location'] or 'n/a'}")
        lines.append(f"- **Posting:** {lead['url']}")
        lines.append(f"- **Resume:** {_resume_link(lead['resume_pdf_path'], reviewed_output_dir)}")
        lines.append(f"- **Fit reasoning:** {lead['reasoning']}")
        if outreach_on:
            outreach_line = _outreach_line(lead)
            if outreach_line:
                lines.append(outreach_line)
        lines.append(f"- **Accept/reject:** `{ACCEPT_COMMAND_TEMPLATE.format(posting_id=lead['posting_id'])}`")
        if others:
            lines.append(f"- **Other tailored roles at {company}:**")
            for o in others:
                lines.append(
                    f"  - {_status_checkbox(o['application_status'])} [{o['score']}/10] {o['title']} "
                    f"(posting_id: {o['posting_id']}) — [posting]({o['url']}) · "
                    f"{_resume_link(o['resume_pdf_path'], reviewed_output_dir)}"
                    f"{_low_confidence_age_badge(o)}"
                )
        lines.append("")

    # --- Section 2: manual review ---
    review_groups = _group_by_company(review_list)
    lines += [
        f"## For manual review -- score < {threshold} ({len(review_list)} posting(s), {len(review_groups)} compan"
        f"{'y' if len(review_groups) == 1 else 'ies'})",
        "",
        "Passed the Filter stage and were scored, but below the auto-tailor threshold -- no resume "
        "generated. One entry per company, its highest-scoring role -- other scored roles at the same "
        "company are listed underneath it. Nothing here is accepted/rejected automatically.",
        "",
    ]
    if not review_groups:
        lines.append("Nothing in this range right now.")
        lines.append("")
    for company, postings in review_groups.items():
        lead, others = postings[0], postings[1:]
        lines.append(
            f"- **[{lead['score']}/10] {lead['company']} — {lead['title']}** "
            f"({lead['location'] or 'n/a'}) -- {lead['reasoning']} ([link]({lead['url']}))"
            f"{_low_confidence_age_badge(lead)}"
        )
        if others:
            lines.append(f"  - Other scored roles at {company}:")
            for o in others:
                lines.append(f"    - [{o['score']}/10] {o['title']} ([link]({o['url']})){_low_confidence_age_badge(o)}")
    if review_groups:
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
