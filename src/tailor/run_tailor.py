"""CLI entry point: the Tailor stage (architecture doc section 5).

Generates a tailored one-page resume PDF + outreach draft for every posting
that scored >= TAILOR_SCORE_THRESHOLD (8) and doesn't already have a
`tailored` row. Requires ANTHROPIC_API_KEY and a working `pdflatex` on
PATH. Re-running is a no-op for postings already tailored -- successes
and exhausted-retry failures alike, see src/tailor/store.py.

Usage:
    python3 -m src.tailor.run_tailor [--db PATH] [--threshold N]
"""

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.tailor.resume_tailor import tailor_and_compile
from src.tailor.store import (
    TAILOR_SCORE_THRESHOLD,
    get_untailored_high_scoring_postings,
    record_tailored_failure,
    record_tailored_success,
)


def run(db_path: Path = DEFAULT_DB_PATH, threshold: int = TAILOR_SCORE_THRESHOLD) -> int:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "[error] ANTHROPIC_API_KEY not set (checked environment and .env). "
            "Add it to a .env file in the repo root or export it."
        )
        return 1

    client = anthropic.Anthropic(api_key=api_key)

    conn = get_connection(db_path)
    init_db(conn)

    to_tailor = get_untailored_high_scoring_postings(conn, threshold)
    already_tailored = conn.execute("SELECT COUNT(*) FROM tailored").fetchone()[0]

    successes = 0
    failures: list[tuple[str, str]] = []

    for row in to_tailor:
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        result = tailor_and_compile(client, row["id"], row["company"], row["title"], row["location"], raw)

        label = f"[{row['score']}/10] {row['company']} — {row['title']}"
        if result["status"] == "tailored":
            record_tailored_success(
                conn, row["id"], result["pdf_path"], result["tex_path"], result["outreach_draft"], result["attempts"]
            )
            successes += 1
            print(f"{label}: tailored in {result['attempts']} attempt(s) -> {result['pdf_path']}")
        else:
            record_tailored_failure(conn, row["id"], result["reason"], result["attempts"])
            failures.append((label, result["reason"]))
            print(f"{label}: FAILED after {result['attempts']} attempt(s) -- {result['reason']}")

    print(
        f"\n{len(to_tailor)} newly processed ({successes} tailored, {len(failures)} failed) | "
        f"{already_tailored} already processed (skipped)"
    )
    if failures:
        print("\nFailures (flagged for manual review, not retried automatically on rerun):")
        for label, reason in failures:
            print(f"  - {label}: {reason}")

    conn.close()
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--threshold", type=int, default=TAILOR_SCORE_THRESHOLD, help="Minimum score to auto-tailor")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db, threshold=args.threshold))
