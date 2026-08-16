"""Review-list generation: a plain markdown report of every filter-passed,
scored posting that's below the Tailor stage's auto-tailor threshold --
these don't get a resume generated automatically, but the score and
reasoning still need to be visible somewhere so a human can decide
manually whether any are worth applying to anyway. No LLM calls -- this
is a straight query + markdown render of scores already computed.
"""

import sqlite3
from datetime import datetime, timezone

from src.tailor.store import TAILOR_SCORE_THRESHOLD, get_review_list_postings


def build_review_list_markdown(conn: sqlite3.Connection, threshold: int = TAILOR_SCORE_THRESHOLD) -> str:
    rows = get_review_list_postings(conn, threshold)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Review list -- filtered postings below the auto-tailor threshold",
        "",
        f"Generated {generated_at}. {len(rows)} posting(s) passed the Filter stage and were scored, "
        f"but scored below {threshold} so no resume was generated automatically. Sorted by score, "
        "highest first -- these are still worth a manual look.",
        "",
    ]

    if not rows:
        lines.append("Nothing in this range right now.")
        return "\n".join(lines)

    for row in rows:
        lines.append(f"## [{row['score']}/10] {row['company']} — {row['title']}")
        lines.append("")
        lines.append(f"- **Location:** {row['location'] or 'n/a'}")
        lines.append(f"- **Link:** {row['url']}")
        lines.append(f"- **Reasoning:** {row['reasoning']}")
        lines.append("")

    return "\n".join(lines)
