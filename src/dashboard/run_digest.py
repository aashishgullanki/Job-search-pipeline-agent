"""CLI entry point: generate the Review Dashboard digest (architecture doc
section 7). No LLM calls, no API key needed -- pure query + render of work
already done by Filter/Score/Tailor/Track C, plus ensuring tailored PDFs
are actually copied into reviewed_output/ before linking to them.

Usage:
    python3 -m src.dashboard.run_digest [--db PATH] [--out PATH]
"""

import argparse
import sys
from pathlib import Path

from src.dashboard.digest import build_digest_markdown
from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "dashboard.md"


def run(db_path: Path = DEFAULT_DB_PATH, output_path: Path = DEFAULT_OUTPUT_PATH) -> int:
    conn = get_connection(db_path)
    init_db(conn)

    markdown = build_digest_markdown(conn)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown)

    ready_companies = markdown.count("\n### [")
    print(f"Wrote dashboard to {output_path} ({ready_companies} compan{'y' if ready_companies == 1 else 'ies'} ready to review, plus review-list + alerts)")

    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output markdown path")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db, output_path=args.out))
