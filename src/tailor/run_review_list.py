"""CLI entry point: generate the review-list markdown report for
filter-passed, scored postings below the Tailor stage's threshold.
No LLM calls, no API key needed -- pure query + render.

Usage:
    python3 -m src.tailor.run_review_list [--db PATH] [--out PATH]
"""

import argparse
import sys
from pathlib import Path

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.tailor.review import build_review_list_markdown

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "review_list.md"


def run(db_path: Path = DEFAULT_DB_PATH, output_path: Path = DEFAULT_OUTPUT_PATH) -> int:
    conn = get_connection(db_path)
    init_db(conn)

    markdown = build_review_list_markdown(conn)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown)

    posting_count = markdown.count("\n## [")
    print(f"Wrote review list ({posting_count} postings) to {output_path}")

    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output markdown path")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db, output_path=args.out))
