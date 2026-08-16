"""CLI entry point: the Score stage (architecture doc section 4).

Scores every filter-passed posting that hasn't been scored yet against the
candidate profile (built from resumes/Baseline Resume.tex), using Claude
Haiku 4.5 as an LLM judge. Requires ANTHROPIC_API_KEY. Re-running is a
no-op for postings that already have a scores row -- see src/score/store.py.

Usage:
    python3 -m src.score.run_score [--db PATH] [--delay SECONDS]
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.score.profile import build_profile_summary
from src.score.scorer import score_posting
from src.score.store import get_unscored_passing_postings, record_score

DEFAULT_DELAY_SECONDS = 0.3


def run(db_path: Path = DEFAULT_DB_PATH, delay: float = DEFAULT_DELAY_SECONDS) -> int:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "[error] ANTHROPIC_API_KEY not set (checked environment and .env). "
            "Add ANTHROPIC_API_KEY=... to a .env file in the repo root or export it."
        )
        return 1

    client = anthropic.Anthropic(api_key=api_key)
    profile = build_profile_summary()

    conn = get_connection(db_path)
    init_db(conn)

    to_score = get_unscored_passing_postings(conn)
    already_scored = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]

    score_counts: Counter[int] = Counter()
    errors: list[tuple[str, str]] = []

    for i, row in enumerate(to_score):
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        try:
            result = score_posting(client, profile, row["title"], row["company"], row["location"], raw)
        except (RuntimeError, ValueError) as e:
            errors.append((f"{row['company']} — {row['title']}", str(e)))
            print(f"[error] {row['company']} — {row['title']}: {e}")
            continue

        record_score(conn, row["id"], result["score"], result["reasoning"])
        score_counts[result["score"]] += 1
        print(f"{result['score']}/10  {row['company']} — {row['title']}")

        if i < len(to_score) - 1:
            time.sleep(delay)

    print(f"\n{len(to_score)} newly scored | {already_scored} already scored (skipped) | {len(errors)} errors")
    if score_counts:
        print("\nScore distribution:")
        for score in range(10, 0, -1):
            if score_counts.get(score):
                print(f"  {score:2d}: {'#' * score_counts[score]} ({score_counts[score]})")
    if errors:
        print("\nFailures:")
        for label, err in errors:
            print(f"  - {label}: {err}")

    conn.close()
    return 1 if errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Delay between API calls")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db, delay=args.delay))
